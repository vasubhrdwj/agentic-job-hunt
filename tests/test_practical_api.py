"""Phase-0 owner-session and readiness API tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from job_hunt_agent.auth import create_owner_session
from job_hunt_agent.api import create_app
from job_hunt_agent.database import Database
from job_hunt_agent.hunt_repository import store_hunt_success
from job_hunt_agent.job_queue import (
    claim_next_job,
    enqueue_job,
    fail_job_attempt,
    record_worker_heartbeat,
)
from job_hunt_agent.models import (
    AchievementEvidence,
    BackgroundJob,
    CandidateProfile,
    HuntOutcome,
    HuntRun,
    Owner,
    OwnerMutationReceipt,
    OwnerSession,
    ResumeVersion,
)
from job_hunt_agent.schemas import HuntResult, OutreachDraft, Person, Role
from job_hunt_agent.security import hash_access_token, load_data_keyring
from job_hunt_agent.sqlalchemy_application_workspace import (
    SqlAlchemyApplicationWorkspaceStore,
)


OWNER_TOKEN = "phase-zero-owner-token-with-more-than-thirty-two-characters"
ALLOWED_ORIGIN = "http://localhost:3000"


def _hunt_payload(resume_text: str = "Built reliable identity systems.") -> dict:
    return {
        "resume_text": resume_text,
        "criteria": {
            "role_keywords": ["identity"],
            "seniority": "senior",
            "location": ["Remote-India"],
        },
        "pack": "backend_india",
        "provider_consent": True,
    }


def _fake_hunt_result(run_id: str) -> HuntResult:
    role = Role(
        company="Okta",
        title="Senior Engineer, Identity",
        url="https://example.com/jobs/identity",
        location="Remote-India",
        summary="Build reliable identity systems.",
        match_reason="The role needs identity platform experience.",
    )
    person = Person(
        name="Priya Rao",
        title="Staff Engineer",
        company="Okta",
        profile_url="https://example.com/people/priya",
        source="company_page",
        why_relevant="Works on the adjacent identity team.",
        verified_current_employer=True,
        confidence=0.9,
    )
    return HuntResult(
        run_id=run_id,
        roles=[role],
        outreach=[
            OutreachDraft(
                draft_id="draft-1",
                role=role,
                person=person,
                message="Hello Priya",
            )
        ],
    )


@pytest.fixture
def practical_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, Database]]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'practical.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("JOB_HUNT_DB_PATH", str(tmp_path / "legacy.db"))
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("USE_MOCKS", "1")
    monkeypatch.setenv("ENABLE_PRACTICAL_MODE", "1")
    monkeypatch.setenv("ENABLE_TRACING", "0")
    monkeypatch.setenv("ALLOWED_ORIGINS", ALLOWED_ORIGIN)
    monkeypatch.setenv("JOB_HUNT_OWNER_ID", "owner")
    monkeypatch.setenv("JOB_HUNT_OWNER_TOKEN_HASH", hash_access_token(OWNER_TOKEN))
    monkeypatch.setenv("JOB_HUNT_SESSION_TTL_DAYS", "30")
    monkeypatch.setenv("JOB_HUNT_WORKER_HEARTBEAT_MAX_AGE_SECONDS", "90")

    command.upgrade(Config("alembic.ini"), "head")
    app = create_app()
    database = app.state.practical_database
    assert isinstance(database, Database)
    with TestClient(app) as client:
        yield client, database
    database.dispose()


def _login(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/session",
        headers={"Origin": ALLOWED_ORIGIN},
        json={"owner_token": OWNER_TOKEN},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_practical_mode_mounts_the_database_application_workspace(
    practical_client: tuple[TestClient, Database],
) -> None:
    client, database = practical_client

    store = client.app.state.application_workspace_store
    assert isinstance(store, SqlAlchemyApplicationWorkspaceStore)
    assert store.database is database
    assert client.app.state.contact_workspace_store is store
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/applications" in paths
    assert "/api/applications/{application_id}/contacts" in paths
    assert "/api/applications/{application_id}/contact-searches" in paths


def test_owner_session_survives_requests_and_is_stored_only_as_a_hash(
    practical_client: tuple[TestClient, Database],
) -> None:
    client, database = practical_client
    body = _login(client)

    assert body["owner_id"] == "owner"
    assert body["display_name"] == "Owner"
    assert body["timezone"] == "UTC"
    assert isinstance(body["local_date"], str)
    assert OWNER_TOKEN not in str(body)
    set_cookie = client.cookies.get("job_hunt_session")
    assert set_cookie is not None
    header = client.post(
        "/api/session",
        headers={"Origin": ALLOWED_ORIGIN},
        json={"owner_token": OWNER_TOKEN},
    ).headers["set-cookie"]
    assert "HttpOnly" in header
    assert "SameSite=strict" in header
    assert OWNER_TOKEN not in header

    current = client.get("/api/session")
    assert current.status_code == 200
    assert current.json()["owner_id"] == "owner"

    with database.session() as session:
        stored = list(session.scalars(select(OwnerSession)))
        assert stored
        assert all(row.token_hash != set_cookie for row in stored)
        assert all(len(row.token_hash) == 64 for row in stored)


def test_session_status_reports_only_sanitized_setup_readiness(
    practical_client: tuple[TestClient, Database],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _database = practical_client

    ready = client.get("/api/session/status")
    assert ready.status_code == 200
    assert ready.headers["cache-control"] == "no-store, max-age=0"
    assert ready.json() == {"state": "ready"}
    assert OWNER_TOKEN not in ready.text

    monkeypatch.delenv("JOB_HUNT_OWNER_TOKEN_HASH")
    setup_required = client.get("/api/session/status")
    assert setup_required.status_code == 200
    assert setup_required.json() == {"state": "setup_required"}
    assert "database" not in setup_required.text
    assert "token" not in setup_required.text


def test_invalid_token_and_wrong_origin_fail_closed(
    practical_client: tuple[TestClient, Database],
) -> None:
    client, _database = practical_client
    wrong_origin = client.post(
        "/api/session",
        headers={"Origin": "https://attacker.invalid"},
        json={"owner_token": OWNER_TOKEN},
    )
    assert wrong_origin.status_code == 403

    invalid = client.post(
        "/api/session",
        headers={"Origin": ALLOWED_ORIGIN},
        json={"owner_token": "wrong-owner-token-with-more-than-thirty-two-characters"},
    )
    assert invalid.status_code == 401
    assert client.get("/api/session").status_code == 401


def test_logout_revokes_session_and_clears_cookie(
    practical_client: tuple[TestClient, Database],
) -> None:
    client, _database = practical_client
    _login(client)
    response = client.delete("/api/session", headers={"Origin": ALLOWED_ORIGIN})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert client.get("/api/session").status_code == 401


def test_practical_hunt_requires_owner_session_and_allowed_origin(
    practical_client: tuple[TestClient, Database],
) -> None:
    client, _database = practical_client
    payload = _hunt_payload()

    logged_out = client.post(
        "/api/hunt",
        headers={"Origin": ALLOWED_ORIGIN},
        json=payload,
    )
    assert logged_out.status_code == 401

    _login(client)
    wrong_origin = client.post(
        "/api/hunt",
        headers={"Origin": "https://attacker.invalid"},
        json=payload,
    )
    assert wrong_origin.status_code == 403

    accepted = client.post(
        "/api/hunt",
        headers={"Origin": ALLOWED_ORIGIN},
        json=payload,
    )
    assert accepted.status_code == 202
    assert accepted.json()["status"] == "queued"


def test_practical_hunt_uses_only_encrypted_postgres_state_and_cookie_auth(
    practical_client: tuple[TestClient, Database],
    tmp_path: Path,
) -> None:
    client, database = practical_client
    _login(client)
    marker = "DISTINCTIVE-PRIVATE-RESUME-MARKER"
    created = client.post(
        "/api/hunt",
        headers={"Origin": ALLOWED_ORIGIN},
        json=_hunt_payload(marker),
    )
    assert created.status_code == 202, created.text
    body = created.json()

    # Owner-cookie authorization works in a new tab without sessionStorage or
    # an Authorization header. The returned capability is legacy compatibility.
    detail = client.get(f"/api/runs/{body['run_id']}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "queued"

    with database.session() as session:
        hunt = session.get(HuntRun, body["run_id"])
        assert hunt is not None
        job = session.get(BackgroundJob, hunt.background_job_id)
        assert job is not None
        assert hunt.owner_id == "owner"
        assert hunt.access_hash == hash_access_token(body["access_token"])
        assert marker not in (hunt.encrypted_request or "")
        assert job.kind == "legacy_hunt"
        assert job.payload == {"hunt_run_id": body["run_id"]}

    assert not (tmp_path / "legacy.db").exists()

    client.cookies.clear()
    bearer_only = client.get(
        f"/api/runs/{body['run_id']}",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert bearer_only.status_code == 401


def test_practical_hunt_idempotency_is_owner_scoped_and_body_safe(
    practical_client: tuple[TestClient, Database],
) -> None:
    client, database = practical_client
    _login(client)
    headers = {"Origin": ALLOWED_ORIGIN, "Idempotency-Key": "repeat-submit"}

    first = client.post("/api/hunt", headers=headers, json=_hunt_payload())
    second = client.post("/api/hunt", headers=headers, json=_hunt_payload())
    conflict = client.post(
        "/api/hunt",
        headers=headers,
        json=_hunt_payload("A different resume body"),
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["run_id"] == first.json()["run_id"]
    assert second.json()["reused"] is True
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == (
        "idempotency key was already used for a different request"
    )

    with database.session() as session:
        assert len(list(session.scalars(select(HuntRun)))) == 1
        assert len(
            list(
                session.scalars(
                    select(BackgroundJob).where(BackgroundJob.kind == "legacy_hunt")
                )
            )
        ) == 1


def test_practical_runs_are_hidden_from_another_owner_even_with_bearer(
    practical_client: tuple[TestClient, Database],
) -> None:
    client, database = practical_client
    _login(client)
    owner_cookie = client.cookies.get("job_hunt_session")
    created = client.post(
        "/api/hunt",
        headers={"Origin": ALLOWED_ORIGIN},
        json=_hunt_payload(),
    ).json()

    with database.session() as session:
        session.add(Owner(id="other-owner", display_name="Other", timezone="UTC"))
        session.flush()
        other = create_owner_session(session, "other-owner")

    client.cookies.clear()
    client.cookies.set("job_hunt_session", other.token)
    bearer = {"Authorization": f"Bearer {created['access_token']}"}
    assert client.get(f"/api/runs/{created['run_id']}", headers=bearer).status_code == 404
    assert client.post(
        f"/api/runs/{created['run_id']}/cancel",
        headers={**bearer, "Origin": ALLOWED_ORIGIN},
    ).status_code == 404
    assert client.post(
        f"/api/runs/{created['run_id']}/outcomes",
        headers={**bearer, "Origin": ALLOWED_ORIGIN},
        json={"outcomes": [{"draft_id": "draft-1", "outcome": "replied"}]},
    ).status_code == 404
    assert client.post(
        f"/api/runs/{created['run_id']}/requeue",
        headers={**bearer, "Origin": ALLOWED_ORIGIN},
        json={"reason": "must remain owner-scoped"},
    ).status_code == 404
    assert client.delete(
        f"/api/runs/{created['run_id']}",
        headers={**bearer, "Origin": ALLOWED_ORIGIN},
    ).status_code == 404

    client.cookies.clear()
    assert owner_cookie is not None
    client.cookies.set("job_hunt_session", owner_cookie)
    owner_detail = client.get(
        f"/api/runs/{created['run_id']}",
        headers={"Authorization": "Bearer deliberately-wrong-token"},
    )
    assert owner_detail.status_code == 200


def test_practical_success_outcomes_cancel_and_delete_are_postgres_atomic(
    practical_client: tuple[TestClient, Database],
) -> None:
    client, database = practical_client
    _login(client)
    created_response = client.post(
        "/api/hunt",
        headers={"Origin": ALLOWED_ORIGIN},
        json=_hunt_payload(),
    )
    run_id = created_response.json()["run_id"]
    lease_token = "practical-api-live-lease"
    keyring = load_data_keyring(production=False)

    with database.session() as session:
        claimed = claim_next_job(
            session,
            worker_id="api-test-worker",
            lease_token=lease_token,
            kinds={"legacy_hunt"},
        )
        assert claimed is not None
        completed = store_hunt_success(
            session,
            hunt_result=_fake_hunt_result(run_id),
            worker_id="api-test-worker",
            lease_token=lease_token,
            keyring=keyring,
        )
        assert completed is not None
        assert completed.status == "succeeded"

    detail = client.get(f"/api/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["hunt_result"]["run_id"] == run_id

    inserted = client.post(
        f"/api/runs/{run_id}/outcomes",
        headers={"Origin": ALLOWED_ORIGIN},
        json={
            "outcomes": [
                {
                    "draft_id": "draft-1",
                    "outcome": "replied",
                    "logged_at": "1999-01-01T00:00:00Z",
                }
            ]
        },
    )
    assert inserted.status_code == 200, inserted.text
    assert inserted.json()["inserted"] == 1
    assert inserted.json()["outcomes"][0]["logged_at"] != "1999-01-01T00:00:00Z"

    rejected = client.post(
        f"/api/runs/{run_id}/outcomes",
        headers={"Origin": ALLOWED_ORIGIN},
        json={"outcomes": [{"draft_id": "unknown", "outcome": "replied"}]},
    )
    assert rejected.status_code == 400
    with database.session() as session:
        assert len(list(session.scalars(select(HuntOutcome)))) == 1

    deleted = client.delete(
        f"/api/runs/{run_id}",
        headers={"Origin": ALLOWED_ORIGIN},
    )
    assert deleted.status_code == 200
    assert client.get(f"/api/runs/{run_id}").status_code == 404
    with database.session() as session:
        assert session.get(HuntRun, run_id) is None
        assert list(session.scalars(select(HuntOutcome))) == []


def test_practical_api_to_real_worker_to_outcome_flow_is_durable_and_private(
    practical_client: tuple[TestClient, Database],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    client, database = practical_client
    _login(client)
    resume_marker = "E2E-DISTINCTIVE-PRIVATE-RESUME"
    outcome_marker = "E2E-DISTINCTIVE-PRIVATE-OUTCOME-NOTE"
    created = client.post(
        "/api/hunt",
        headers={"Origin": ALLOWED_ORIGIN},
        json=_hunt_payload(resume_marker),
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["run_id"]

    captured: dict[str, object] = {}

    def fake_run_hunt(**kwargs):
        captured.update(kwargs)
        return _fake_hunt_result(kwargs["run_id"])

    monkeypatch.setattr(worker, "run_hunt", fake_run_hunt)
    worked = worker.run_worker_once(
        worker_id="integrated-practical-worker",
        lease_seconds=60,
        durable_database=database,
        practical_mode=True,
        use_mocks=True,
        enable_tracing=False,
    )
    assert worked == worker.WorkerResult(
        claimed=True,
        run_id=run_id,
        status="succeeded",
        stage="succeeded",
    )
    assert captured["resume_text"] == resume_marker

    # Cookie-only retrieval proves a reload/new-tab path no longer depends on
    # the legacy capability stored in browser sessionStorage.
    detail = client.get(f"/api/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["hunt_result"]["run_id"] == run_id
    outcome = client.post(
        f"/api/runs/{run_id}/outcomes",
        headers={"Origin": ALLOWED_ORIGIN},
        json={
            "outcomes": [
                {
                    "draft_id": "draft-1",
                    "outcome": "introduced",
                    "notes": outcome_marker,
                }
            ]
        },
    )
    assert outcome.status_code == 200, outcome.text

    with database.session() as session:
        hunt = session.get(HuntRun, run_id)
        assert hunt is not None
        job = session.get(BackgroundJob, hunt.background_job_id)
        stored_outcome = session.scalar(
            select(HuntOutcome).where(HuntOutcome.hunt_run_id == run_id)
        )
        assert job is not None
        assert stored_outcome is not None
        mapped_storage = json.dumps(
            {
                "request": hunt.encrypted_request,
                "result": hunt.encrypted_result,
                "job_payload": job.payload,
                "job_error": job.last_error,
                "outcome": stored_outcome.encrypted_payload,
            },
            sort_keys=True,
        )
        assert resume_marker not in mapped_storage
        assert outcome_marker not in mapped_storage

    assert not (tmp_path / "legacy.db").exists()


def test_practical_owner_can_cancel_and_requeue_without_operator_bearer(
    practical_client: tuple[TestClient, Database],
) -> None:
    client, database = practical_client
    _login(client)
    cancelled_run = client.post(
        "/api/hunt",
        headers={"Origin": ALLOWED_ORIGIN},
        json=_hunt_payload("Cancel this private hunt"),
    ).json()["run_id"]

    wrong_origin = client.post(
        f"/api/runs/{cancelled_run}/cancel",
        headers={"Origin": "https://attacker.invalid"},
    )
    assert wrong_origin.status_code == 403
    cancelled = client.post(
        f"/api/runs/{cancelled_run}/cancel",
        headers={"Origin": ALLOWED_ORIGIN},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    with database.session() as session:
        hunt = session.get(HuntRun, cancelled_run)
        assert hunt is not None
        assert hunt.encrypted_request is None
        assert session.get(BackgroundJob, hunt.background_job_id).status == "cancelled"

    dead_run = client.post(
        "/api/hunt",
        headers={"Origin": ALLOWED_ORIGIN},
        json=_hunt_payload("Retry this private hunt"),
    ).json()["run_id"]
    with database.session() as session:
        claimed = claim_next_job(
            session,
            worker_id="dead-letter-worker",
            lease_token="dead-letter-lease",
            kinds={"legacy_hunt"},
        )
        assert claimed is not None
        assert claimed.payload == {"hunt_run_id": dead_run}
        dead = fail_job_attempt(
            session,
            claimed.id,
            worker_id="dead-letter-worker",
            lease_token="dead-letter-lease",
            error_code="ProviderError",
            terminal=True,
        )
        assert dead is not None
        assert dead.status == "dead_letter"

    requeued = client.post(
        f"/api/runs/{dead_run}/requeue",
        headers={"Origin": ALLOWED_ORIGIN},
        json={"reason": "provider recovered"},
    )
    assert requeued.status_code == 200, requeued.text
    assert requeued.json()["status"] == "queued"
    assert requeued.json()["attempt_count"] == 0


def test_owner_onboarding_to_saved_search_to_queued_hunt_is_immediately_usable(
    practical_client: tuple[TestClient, Database],
) -> None:
    client, database = practical_client
    _login(client)

    profile = client.put(
        "/api/me/profile",
        headers={"Origin": ALLOWED_ORIGIN, "If-Match": '"0"'},
        json={
            "career_thesis": "Build secure, reliable identity platforms.",
            "current_title": "Senior Backend Engineer",
            "current_location": "Bengaluru",
            "work_authorizations": [{"country_code": "in", "status": "citizen"}],
            "work_modes": ["remote", "hybrid"],
            "employment_types": ["full_time"],
            "notice_period_days": 30,
            "onboarding_step": "resume",
        },
    )
    assert profile.status_code == 200, profile.text
    assert profile.headers["etag"] == '"1"'

    resume_text = (
        "Built SCIM provisioning services.\n"
        "Reduced identity synchronization failures by 40%."
    )
    resume = client.post(
        "/api/me/resume-versions",
        headers={"Origin": ALLOWED_ORIGIN, "Idempotency-Key": "base-resume-v1"},
        json={
            "label": "Identity platform resume",
            "content": resume_text,
            "source": "pasted",
            "set_as_base": True,
        },
    )
    assert resume.status_code == 201, resume.text
    resume_body = resume.json()
    assert resume_body["is_base"] is True

    profile_with_resume = client.get("/api/me/profile")
    assert profile_with_resume.status_code == 200
    assert profile_with_resume.json()["base_resume"]["id"] == resume_body["id"]

    track = client.post(
        "/api/career-tracks",
        headers={"Origin": ALLOWED_ORIGIN, "Idempotency-Key": "identity-track-v1"},
        json={
            "name": "Identity platform",
            "role_families": ["Backend", "Platform", "Identity"],
            "seniority_levels": ["senior", "staff"],
            "target_locations": ["Remote-India", "Bengaluru"],
            "priorities": {
                "compensation": 4,
                "scope": 5,
                "learning": 5,
                "company_quality": 4,
                "flexibility": 5,
            },
            "active": True,
        },
    )
    assert track.status_code == 201, track.text

    evidence = client.post(
        "/api/me/evidence",
        headers={"Origin": ALLOWED_ORIGIN, "Idempotency-Key": "evidence-v1"},
        json={
            "statement": "Reduced identity synchronization failures by 40%.",
            "source_resume_version_id": resume_body["id"],
            "source_excerpt": "Reduced identity synchronization failures by 40%.",
            "skills": ["SCIM", "Reliability"],
            "origin": "resume_suggestion",
        },
    )
    assert evidence.status_code == 201, evidence.text
    assert evidence.json()["approval_state"] == "pending"
    approved = client.patch(
        f"/api/me/evidence/{evidence.json()['id']}",
        headers={"Origin": ALLOWED_ORIGIN, "If-Match": '"1"'},
        json={"approval_state": "approved"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval_state"] == "approved"
    assert approved.json()["approved_at"] is not None

    search = client.post(
        "/api/saved-searches",
        headers={"Origin": ALLOWED_ORIGIN, "Idempotency-Key": "identity-search-v1"},
        json={
            "name": "Senior identity roles",
            "career_track_id": track.json()["id"],
            "criteria": {
                "role_keywords": ["identity", "SCIM", "IAM"],
                "seniority": "senior",
                "location": ["Remote-India", "Bengaluru"],
                "comp_min_lpa": 35,
                "comp_max_lpa": 60,
                "employment_types": ["full_time"],
                "max_age_days": 30,
                "country": "in",
            },
            "schedule": {
                "cadence": "weekdays",
                "timezone": "Asia/Kolkata",
                "local_time": "08:30",
            },
            "pack": "backend_india",
            "active": True,
        },
    )
    assert search.status_code == 201, search.text
    assert search.json()["resume_version_id"] == resume_body["id"]
    assert search.json()["next_scan_at"] is not None

    projection = client.get(
        f"/api/saved-searches/{search.json()['id']}/hunt-input"
    )
    assert projection.status_code == 200, projection.text
    projection_body = projection.json()
    assert projection_body["ready"] is True
    assert projection_body["blockers"] == []
    assert projection_body["input"]["resume_text"] == resume_text
    assert projection_body["input"]["criteria"] == search.json()["criteria"]

    with database.session() as session:
        stored_profile = session.scalar(select(CandidateProfile))
        stored_resume = session.scalar(select(ResumeVersion))
        stored_evidence = session.scalar(select(AchievementEvidence))
        receipts = list(session.scalars(select(OwnerMutationReceipt)))
        assert stored_profile is not None
        assert stored_resume is not None
        assert stored_evidence is not None
        private_storage = " ".join(
            [
                stored_profile.encrypted_payload,
                stored_resume.encrypted_content,
                stored_evidence.encrypted_payload,
            ]
        )
        assert "Build secure, reliable identity platforms" not in private_storage
        assert resume_text not in private_storage
        assert "Reduced identity synchronization failures" not in private_storage
        assert receipts
        assert all(receipt.idempotency_key_hash != "base-resume-v1" for receipt in receipts)

    hunt_input = projection_body["input"]
    queued = client.post(
        "/api/hunt",
        headers={"Origin": ALLOWED_ORIGIN, "Idempotency-Key": "run-saved-search-v1"},
        json={
            "resume_text": hunt_input["resume_text"],
            "criteria": hunt_input["criteria"],
            "pack": hunt_input["pack"],
            "use_self_rag": hunt_input["use_self_rag"],
            "provider_consent": True,
        },
    )
    assert queued.status_code == 202, queued.text
    assert queued.json()["status"] == "queued"


def test_openapi_marks_hunt_as_cookie_authenticated(
    practical_client: tuple[TestClient, Database],
) -> None:
    client, _database = practical_client
    schema = client.get("/openapi.json").json()
    scheme = schema["components"]["securitySchemes"]["OwnerSessionCookie"]
    assert scheme == {
        "type": "apiKey",
        "description": "Opaque HttpOnly session issued by POST /api/session.",
        "in": "cookie",
        "name": "job_hunt_session",
    }
    assert schema["paths"]["/api/hunt"]["post"]["security"] == [
        {"OwnerSessionCookie": []}
    ]
    assert schema["paths"]["/api/session"]["get"]["security"] == [
        {"OwnerSessionCookie": []}
    ]
    assert schema["paths"]["/api/session"]["delete"]["security"] == [
        {"OwnerSessionCookie": []}
    ]
    assert "security" not in schema["paths"]["/api/session/status"]["get"]
    assert schema["paths"]["/api/health"]["get"]["security"] == [
        {"OwnerSessionCookie": []}
    ]
    practical_run_operations = (
        ("/api/runs/{run_id}", "get"),
        ("/api/runs/{run_id}", "delete"),
        ("/api/runs/{run_id}/cancel", "post"),
        ("/api/runs/{run_id}/requeue", "post"),
        ("/api/runs/{run_id}/outcomes", "post"),
    )
    for path, method in practical_run_operations:
        assert schema["paths"][path][method]["security"] == [
            {"OwnerSessionCookie": []}
        ]
    for path in (
        "/api/me/profile",
        "/api/me/resume-versions",
        "/api/me/evidence",
        "/api/career-tracks",
        "/api/saved-searches",
        "/api/saved-searches/{saved_search_id}/hunt-input",
    ):
        assert path in schema["paths"]
        for operation in schema["paths"][path].values():
            if isinstance(operation, dict) and "responses" in operation:
                assert operation["security"] == [{"OwnerSessionCookie": []}]


def test_workspace_validation_problem_is_field_aware_and_hides_private_input(
    practical_client: tuple[TestClient, Database],
) -> None:
    client, _database = practical_client
    _login(client)
    marker = "DISTINCTIVE-PRIVATE-PROFILE-MARKER"
    response = client.put(
        "/api/me/profile",
        headers={"Origin": ALLOWED_ORIGIN, "If-Match": '"0"'},
        json={"current_title": marker + ("x" * 200)},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "invalid_request"
    assert body["message"] == "Request validation failed"
    assert any(error["field"] == "current_title" for error in body["field_errors"])
    assert marker not in response.text
    assert '"input"' not in response.text
    assert response.headers["cache-control"] == "no-store, max-age=0"


def test_readiness_requires_current_migration_and_fresh_worker(
    practical_client: tuple[TestClient, Database],
) -> None:
    client, database = practical_client
    web_ready = client.get("/web-ready")
    assert web_ready.status_code == 200
    assert web_ready.json()["ok"] is True
    assert "worker" not in web_ready.json()

    before = client.get("/ready")
    assert before.status_code == 503
    assert before.json()["migrations"]["current"] is True
    assert before.json()["worker"]["fresh"] is False

    now = datetime.now(timezone.utc)
    with database.session() as session:
        record_worker_heartbeat(
            session,
            worker_id="phase-zero-worker",
            supported_kinds={"legacy_hunt"},
            build_version="test",
            now=now,
        )

    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["ok"] is True
    assert ready.json()["worker"]["worker_id"] == "phase-zero-worker"


def test_readiness_requires_fresh_capability_for_every_active_job_kind(
    practical_client: tuple[TestClient, Database],
) -> None:
    client, database = practical_client
    now = datetime.now(timezone.utc)
    with database.session() as session:
        record_worker_heartbeat(
            session,
            worker_id="legacy-worker",
            supported_kinds={"legacy_hunt"},
            now=now,
        )
        record_worker_heartbeat(
            session,
            worker_id="stale-scan-worker",
            supported_kinds={"scan_company"},
            now=now - timedelta(seconds=120),
        )
        enqueue_job(
            session,
            kind="scan_company",
            dedupe_key="readiness:scan:acme",
            payload={"company_id": "acme"},
        )

    unsupported = client.get("/ready")
    assert unsupported.status_code == 503
    assert unsupported.headers["cache-control"] == "no-store, max-age=0"
    assert unsupported.json()["worker"]["supported_kinds"] == ["legacy_hunt"]
    assert unsupported.json()["worker"]["active_job_kinds"] == ["scan_company"]
    assert unsupported.json()["worker"]["unsupported_active_kinds"] == ["scan_company"]

    with database.session() as session:
        record_worker_heartbeat(
            session,
            worker_id="scan-worker",
            supported_kinds={"scan_company"},
            now=datetime.now(timezone.utc),
        )

    ready = client.get("/ready")
    worker = ready.json()["worker"]
    assert ready.status_code == 200
    assert set(worker["worker_ids"]) == {"legacy-worker", "scan-worker"}
    assert worker["fresh_worker_count"] == 2
    assert worker["supported_kinds"] == ["legacy_hunt", "scan_company"]
    assert worker["unsupported_active_kinds"] == []


def test_owner_health_is_private_and_reports_queue_counts(
    practical_client: tuple[TestClient, Database],
) -> None:
    client, database = practical_client
    assert client.get("/api/health").status_code == 401
    _login(client)
    with database.session() as session:
        enqueue_job(
            session,
            kind="scan_company",
            dedupe_key="owner-health:owner",
            owner_id="owner",
            payload={"company_id": "owner-company"},
        )
        enqueue_job(
            session,
            kind="scan_company",
            dedupe_key="owner-health:system",
            payload={"company_id": "system-company"},
        )
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["owner_id"] == "owner"
    assert response.json()["queue"]["counts"] == {"queued": 1}
    assert response.json()["queue"]["dead_letter"] == 0


def test_legacy_liveness_remains_available_without_durable_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("JOB_HUNT_DB_PATH", str(tmp_path / "legacy-only.db"))
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("USE_MOCKS", "1")
    monkeypatch.setenv("ENABLE_PRACTICAL_MODE", "0")
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.head("/health").status_code == 200
        assert client.get("/ready").status_code == 503
        assert client.get("/web-ready").status_code == 503
        assert client.get("/api/me/profile").status_code == 404
        assert client.get("/api/career-tracks").status_code == 404
        assert client.get("/api/saved-searches").status_code == 404
        assert client.get("/api/session/status").json() == {
            "state": "setup_required"
        }
        login = client.post(
            "/api/session",
            json={"owner_token": OWNER_TOKEN},
        )
        assert login.status_code == 503


def test_health_openapi_operation_id_is_deterministic(
    practical_client: tuple[TestClient, Database],
) -> None:
    client, _database = practical_client
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/health"]["get"]["operationId"] == "health_liveness"
    assert "head" not in schema["paths"]["/health"]
