"""End-to-end tests for the queued FastAPI surface."""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from job_hunt_agent import persistence
from job_hunt_agent.schemas import (
    HuntResult,
    OutreachDraft,
    Person,
    Role,
)
from job_hunt_agent.security import hash_access_token


def _fake_role() -> Role:
    return Role(
        company="Okta",
        title="Senior Engineer, Identity",
        url="https://www.linkedin.com/jobs/view/123",
        location="Remote-India",
        summary="Build SCIM provisioning.",
        match_reason="Listing mentions SCIM 2.0.",
    )


def _fake_person() -> Person:
    return Person(
        name="Priya Rao",
        title="Staff Engineer",
        company="Okta",
        profile_url="https://linkedin.com/in/priya",
        source="linkedin",
        why_relevant="Adjacent team.",
    )


def _fake_run_hunt(*, run_id: str, draft_ids: tuple[str, ...] = ("d-0", "d-1", "d-2")) -> HuntResult:
    role = _fake_role()
    person = _fake_person()
    drafts = [
        OutreachDraft(draft_id=did, role=role, person=person, message=f"hello {did}")
        for did in draft_ids
    ]
    return HuntResult(run_id=run_id, roles=[role], outreach=drafts)


@pytest.fixture
def api_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Reload the api module with a temp SQLite path."""
    monkeypatch.setenv("JOB_HUNT_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("USE_MOCKS", "1")
    monkeypatch.setenv("RETENTION_CLEANUP_INTERVAL_SECONDS", "3600")
    monkeypatch.setenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173",
    )
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("JOB_HUNT_OPERATOR_TOKEN_HASH", raising=False)
    from job_hunt_agent import api as api_mod

    api_mod = importlib.reload(api_mod)
    api_mod.app = api_mod.create_app()
    return api_mod


@pytest.fixture
def client(api_module) -> Iterator[TestClient]:
    with TestClient(api_module.app) as test_client:
        yield test_client


def _hunt_payload(resume_text: str = "Built SCIM systems.") -> dict:
    return {
        "resume_text": resume_text,
        "criteria": {
            "role_keywords": ["SCIM"],
            "seniority": "senior",
            "location": ["Remote-India"],
        },
        "pack": "backend_india",
        "provider_consent": True,
    }


def _post_hunt(client: TestClient, *, idempotency_key: str | None = None) -> dict:
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
    response = client.post("/api/hunt", json=_hunt_payload(), headers=headers)
    assert response.status_code == 202, response.text
    return response.json()


def _auth(hunt: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {hunt['access_token']}"}


def _finish_hunt(hunt: dict) -> None:
    run_id = hunt["run_id"]
    lease_token = f"lease-{run_id}"
    state = persistence.claim_next_run(
        worker_id="test-worker",
        lease_token=lease_token,
        lease_seconds=60,
    )
    assert state is not None
    assert state.run_id == run_id
    final = persistence.complete_run_with_result(
        _fake_run_hunt(run_id=run_id),
        worker_id="test-worker",
        lease_token=lease_token,
    )
    assert final is not None
    assert final.status == "succeeded"


def test_health_endpoint_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_health_endpoint_allows_head(client: TestClient) -> None:
    """Uptime monitors (UptimeRobot) probe /health with HEAD requests."""
    response = client.head("/health")
    assert response.status_code == 200


def test_post_hunt_enqueues_request_and_returns_capability(client: TestClient) -> None:
    response = client.post("/api/hunt", json=_hunt_payload())
    body = response.json()

    assert response.status_code == 202
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert body["run_id"]
    assert body["status"] == "queued"
    assert body["stage"] == "queued"
    assert body["attempt_count"] == 0
    assert len(body["access_token"]) >= 40
    assert body["reused"] is False
    assert persistence.load_run(body["run_id"]) is None
    assert persistence.load_encrypted_request(body["run_id"]) is not None


def test_get_run_returns_queued_status_before_result(client: TestClient) -> None:
    hunt = _post_hunt(client)

    response = client.get(f"/api/runs/{hunt['run_id']}", headers=_auth(hunt))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["hunt_result"] is None
    assert body["outcomes"] == []


def test_post_hunt_idempotency_reuses_same_run(client: TestClient) -> None:
    first = client.post(
        "/api/hunt",
        json=_hunt_payload(),
        headers={"Idempotency-Key": "repeat-submit"},
    )
    second = client.post(
        "/api/hunt",
        json=_hunt_payload(),
        headers={"Idempotency-Key": "repeat-submit"},
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["run_id"] == second.json()["run_id"]
    assert second.json()["reused"] is True
    assert len(persistence.list_queue_states(status="queued")) == 1


def test_post_hunt_idempotency_rejects_different_body(client: TestClient) -> None:
    first = client.post(
        "/api/hunt",
        json=_hunt_payload("Built SCIM systems."),
        headers={"Idempotency-Key": "same-key"},
    )
    second = client.post(
        "/api/hunt",
        json=_hunt_payload("Built unrelated mobile apps."),
        headers={"Idempotency-Key": "same-key"},
    )

    assert first.status_code == 202
    assert second.status_code == 409


def test_post_outcomes_accepts_known_draft_ids_after_success(client: TestClient) -> None:
    hunt = _post_hunt(client)
    _finish_hunt(hunt)
    run_id = hunt["run_id"]

    response = client.post(
        f"/api/runs/{run_id}/outcomes",
        json={
            "outcomes": [
                {"draft_id": "d-0", "outcome": "replied"},
                {"draft_id": "d-1", "outcome": "no_reply", "notes": "ghosted"},
            ]
        },
        headers=_auth(hunt),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["inserted"] == 2
    for entry in body["outcomes"]:
        assert entry["logged_at"] is not None


def test_post_outcomes_rejects_active_run(client: TestClient) -> None:
    hunt = _post_hunt(client)

    response = client.post(
        f"/api/runs/{hunt['run_id']}/outcomes",
        json={"outcomes": [{"draft_id": "d-0", "outcome": "replied"}]},
        headers=_auth(hunt),
    )

    assert response.status_code == 409


def test_post_outcomes_rejects_unknown_draft_ids(client: TestClient) -> None:
    hunt = _post_hunt(client)
    _finish_hunt(hunt)
    run_id = hunt["run_id"]

    response = client.post(
        f"/api/runs/{run_id}/outcomes",
        json={
            "outcomes": [
                {"draft_id": "d-0", "outcome": "replied"},
                {"draft_id": "bogus", "outcome": "replied"},
            ]
        },
        headers=_auth(hunt),
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "bogus" in detail["unknown_draft_ids"]


def test_post_outcomes_rejects_unknown_run(client: TestClient) -> None:
    response = client.post(
        "/api/runs/missing/outcomes",
        json={"outcomes": [{"draft_id": "d-0", "outcome": "replied"}]},
        headers={"Authorization": "Bearer unknown-token"},
    )
    assert response.status_code == 404


def test_get_run_returns_hunt_result_and_outcomes_after_success(client: TestClient) -> None:
    hunt = _post_hunt(client)
    _finish_hunt(hunt)
    run_id = hunt["run_id"]

    client.post(
        f"/api/runs/{run_id}/outcomes",
        json={"outcomes": [{"draft_id": "d-0", "outcome": "introduced"}]},
        headers=_auth(hunt),
    )

    response = client.get(f"/api/runs/{run_id}", headers=_auth(hunt))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["hunt_result"]["run_id"] == run_id
    assert len(body["outcomes"]) == 1
    assert body["outcomes"][0]["draft_id"] == "d-0"
    assert body["outcomes"][0]["outcome"] == "introduced"


def test_get_run_returns_404_when_missing(client: TestClient) -> None:
    response = client.get(
        "/api/runs/missing",
        headers={"Authorization": "Bearer unknown-token"},
    )
    assert response.status_code == 404


def test_run_access_token_is_required_and_wrong_token_is_hidden(
    client: TestClient,
) -> None:
    hunt = _post_hunt(client)
    run_id = hunt["run_id"]

    missing = client.get(f"/api/runs/{run_id}")
    wrong = client.get(
        f"/api/runs/{run_id}",
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert missing.status_code == 401
    assert wrong.status_code == 404


def test_cancel_queued_run_prevents_worker_claim(client: TestClient) -> None:
    hunt = _post_hunt(client)

    cancelled = client.post(f"/api/runs/{hunt['run_id']}/cancel", headers=_auth(hunt))
    claim = persistence.claim_next_run(worker_id="worker", lease_token="lease")
    detail = client.get(f"/api/runs/{hunt['run_id']}", headers=_auth(hunt))

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert claim is None
    assert detail.json()["status"] == "cancelled"
    assert persistence.load_encrypted_request(hunt["run_id"]) is None


def test_delete_run_removes_result_and_revokes_access(client: TestClient) -> None:
    hunt = _post_hunt(client)
    _finish_hunt(hunt)
    run_id = hunt["run_id"]

    deleted = client.delete(f"/api/runs/{run_id}", headers=_auth(hunt))
    after = client.get(f"/api/runs/{run_id}", headers=_auth(hunt))

    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}
    assert after.status_code == 404


def test_operator_requeue_requires_operator_token(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator_token = "operator-secret-token"
    monkeypatch.setenv("JOB_HUNT_OPERATOR_TOKEN_HASH", hash_access_token(operator_token))
    now = datetime.now(timezone.utc)
    persistence.create_run_security(
        "poison",
        access_hash=hash_access_token("run-token"),
        encrypted_request="ciphertext",
        encryption_key_id="v1",
        request_expires_at=now + timedelta(hours=1),
        access_expires_at=now + timedelta(days=1),
        max_attempts=1,
    )
    state = persistence.claim_next_run(
        worker_id="worker",
        lease_token="lease-poison",
    )
    assert state is not None
    dead = persistence.mark_run_attempt_failed(
        "poison",
        worker_id="worker",
        lease_token="lease-poison",
        error="PoisonError",
    )
    assert dead is not None
    assert dead.status == "dead_letter"

    forbidden = client.post(
        "/api/runs/poison/requeue",
        json={"reason": "retry after fix"},
        headers={"Authorization": "Bearer run-token"},
    )
    allowed = client.post(
        "/api/runs/poison/requeue",
        json={"reason": "retry after fix"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )

    assert forbidden.status_code == 403
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["status"] == "queued"
    events = persistence.load_run_events("poison")
    assert events[-1]["from_status"] == "dead_letter"
    assert events[-1]["to_status"] == "queued"
    assert events[-1]["actor"] == "operator"


def test_persistence_survives_app_restart(api_module) -> None:
    with TestClient(api_module.app) as first:
        hunt = _post_hunt(first)
        _finish_hunt(hunt)
        run_id = hunt["run_id"]
        first.post(
            f"/api/runs/{run_id}/outcomes",
            json={"outcomes": [{"draft_id": "d-0", "outcome": "replied"}]},
            headers=_auth(hunt),
        )

    # Build a fresh app instance pointing at the same DB.
    api_module.app = api_module.create_app()
    with TestClient(api_module.app) as second:
        response = second.get(f"/api/runs/{run_id}", headers=_auth(hunt))
        assert response.status_code == 200
        body = response.json()
        assert body["hunt_result"]["run_id"] == run_id
        assert body["outcomes"][0]["outcome"] == "replied"


def test_logged_at_is_overwritten_by_server(client: TestClient) -> None:
    hunt = _post_hunt(client)
    _finish_hunt(hunt)
    run_id = hunt["run_id"]

    spoofed = "1999-01-01T00:00:00+00:00"
    response = client.post(
        f"/api/runs/{run_id}/outcomes",
        json={
            "outcomes": [
                {"draft_id": "d-0", "outcome": "replied", "logged_at": spoofed},
            ]
        },
        headers=_auth(hunt),
    )
    assert response.status_code == 200
    stored = response.json()["outcomes"][0]
    assert stored["logged_at"] != spoofed


def test_db_path_env_var_is_honored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "scoped.db"
    monkeypatch.setenv("JOB_HUNT_DB_PATH", str(db))
    monkeypatch.setenv("USE_MOCKS", "1")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000")

    from job_hunt_agent import api as api_mod

    api_mod = importlib.reload(api_mod)
    api_mod.app = api_mod.create_app()

    with TestClient(api_mod.app) as client:
        _post_hunt(client)

    assert db.exists()
    assert persistence.load_run is not None  # sanity import check


def test_post_hunt_rejects_invalid_pack_name(client: TestClient) -> None:
    response = client.post(
        "/api/hunt",
        json={
            "resume_text": "Built backend systems.",
            "criteria": {
                "role_keywords": ["backend"],
                "seniority": "junior",
                "location": ["India"],
            },
            "pack": "../secret",
            "provider_consent": True,
        },
    )

    assert response.status_code == 422


def test_post_hunt_requires_provider_consent(client: TestClient) -> None:
    response = client.post(
        "/api/hunt",
        json={
            "resume_text": "Built backend systems.",
            "criteria": {
                "role_keywords": ["backend"],
                "seniority": "junior",
                "location": ["India"],
            },
            "pack": "backend_india",
        },
    )

    assert response.status_code == 422


def test_post_hunt_rejects_oversized_resume(client: TestClient) -> None:
    from job_hunt_agent.security import MAX_RESUME_CHARS

    marker = "PRIVATE-OVERSIZED-RESUME-MARKER"
    response = client.post(
        "/api/hunt",
        json={
            "resume_text": marker + ("x" * MAX_RESUME_CHARS),
            "criteria": {
                "role_keywords": ["backend"],
                "seniority": "junior",
                "location": ["India"],
            },
            "pack": "backend_india",
            "provider_consent": True,
        },
    )

    assert response.status_code == 422
    assert marker not in response.text
    assert len(response.content) < 1_000
    assert response.headers["cache-control"] == "no-store, max-age=0"


def test_completed_hunt_erases_encrypted_resume_request(client: TestClient) -> None:
    hunt = _post_hunt(client)
    assert persistence.load_encrypted_request(hunt["run_id"]) is not None

    _finish_hunt(hunt)

    assert persistence.load_encrypted_request(hunt["run_id"]) is None


def test_retention_cleanup_runs_on_health_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JOB_HUNT_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("RETENTION_CLEANUP_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("USE_MOCKS", "1")
    from job_hunt_agent import api as api_mod

    api_mod = importlib.reload(api_mod)
    api_mod.app = api_mod.create_app()
    now = api_mod.datetime.now(api_mod.timezone.utc)
    persistence.create_run_security(
        "expired-run",
        access_hash="hash",
        encrypted_request="ciphertext",
        encryption_key_id="v1",
        request_expires_at=now - api_mod.timedelta(days=2),
        access_expires_at=now - api_mod.timedelta(seconds=1),
    )

    with TestClient(api_mod.app) as test_client:
        response = test_client.get("/health")

    assert response.status_code == 200
    assert not persistence.authorize_run("expired-run", "hash")


def test_production_config_fails_loudly_when_required_env_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import api as api_mod

    monkeypatch.setenv("ENVIRONMENT", "production")
    for name in (
        "GOOGLE_API_KEY",
        "SERPAPI_API_KEY",
        "SERPAPI_KEY",
        "PHOENIX_API_KEY",
        "PHOENIX_COLLECTOR_ENDPOINT",
        "JOB_HUNT_DB_PATH",
        "JOB_HUNT_DATA_KEYS",
        "JOB_HUNT_OPERATOR_TOKEN_HASH",
        "ALLOWED_ORIGINS",
        "GEMINI_PAID_SERVICE_ACK",
        "DATABASE_URL",
        "JOB_HUNT_OWNER_ID",
        "JOB_HUNT_OWNER_TOKEN_HASH",
        "JOB_HUNT_PRIVACY_RECEIPT_SECRET",
        "ENABLE_PRACTICAL_MODE",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="Invalid production config"):
        importlib.reload(api_mod)

    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("JOB_HUNT_DB_PATH", str(tmp_path / "recovered.db"))
    monkeypatch.setenv("USE_MOCKS", "1")
    importlib.reload(api_mod)


def test_production_config_accepts_required_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import api as api_mod

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ENABLE_TRACING", "0")
    monkeypatch.setenv("USE_MOCKS", "0")
    for name in (
        "GOOGLE_API_KEY",
        "SERPAPI_API_KEY",
        "SERPAPI_KEY",
        "PHOENIX_API_KEY",
        "PHOENIX_COLLECTOR_ENDPOINT",
        "GEMINI_PAID_SERVICE_ACK",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JOB_HUNT_DB_PATH", str(tmp_path / "data" / "outcomes.db"))
    from cryptography.fernet import Fernet

    monkeypatch.setenv(
        "JOB_HUNT_DATA_KEYS",
        f"v1:{Fernet.generate_key().decode('ascii')}",
    )
    monkeypatch.setenv("JOB_HUNT_OPERATOR_TOKEN_HASH", hash_access_token("operator"))
    monkeypatch.setenv("ENABLE_TRACE_DRAFT_CONTENT", "0")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://job-hunt-agent.vercel.app")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://job_hunt:secret@postgres.invalid/job_hunt?sslmode=require",
    )
    with pytest.raises(
        RuntimeError,
        match="JOB_HUNT_PRIVACY_RECEIPT_SECRET must be a stable 32\\+ character",
    ):
        importlib.reload(api_mod)
    monkeypatch.setenv(
        "JOB_HUNT_PRIVACY_RECEIPT_SECRET",
        "stable-production-receipt-secret-with-more-than-32-characters",
    )

    api_mod = importlib.reload(api_mod)

    with TestClient(api_mod.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True}

    monkeypatch.delenv("ENVIRONMENT", raising=False)
    importlib.reload(api_mod)


@pytest.mark.parametrize(
    "origin",
    [
        "http://job-hunt-agent.vercel.app",
        "https://job-hunt-agent.vercel.app/path",
        "https://user:pass@job-hunt-agent.vercel.app",
        "https://job-hunt-agent.vercel.app?preview=1",
    ],
)
def test_production_origin_validation_requires_exact_https_origin(origin: str) -> None:
    from job_hunt_agent.api import _production_origin_error

    assert _production_origin_error(origin) is not None
    assert _production_origin_error("https://job-hunt-agent.vercel.app") is None


def test_production_rejects_public_legacy_hunt_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import api as api_mod

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ENABLE_PRACTICAL_MODE", "0")
    monkeypatch.setenv("ENABLE_TRACING", "1")
    monkeypatch.setenv("USE_MOCKS", "0")
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-google")
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-serpapi")
    monkeypatch.setenv("PHOENIX_API_KEY", "fake-phoenix")
    monkeypatch.setenv(
        "PHOENIX_COLLECTOR_ENDPOINT",
        "https://app.phoenix.arize.com/s/demo",
    )
    monkeypatch.setenv("JOB_HUNT_DB_PATH", str(tmp_path / "outcomes.db"))
    from cryptography.fernet import Fernet

    monkeypatch.setenv("JOB_HUNT_DATA_KEYS", f"v1:{Fernet.generate_key().decode('ascii')}")
    monkeypatch.setenv("JOB_HUNT_OPERATOR_TOKEN_HASH", hash_access_token("operator"))
    monkeypatch.setenv("GEMINI_PAID_SERVICE_ACK", "1")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://job-hunt-agent.vercel.app")

    with pytest.raises(
        RuntimeError,
        match="ENABLE_PRACTICAL_MODE must be true in production",
    ):
        importlib.reload(api_mod)

    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("ENABLE_PRACTICAL_MODE", "0")
    monkeypatch.setenv("USE_MOCKS", "1")
    importlib.reload(api_mod)
