"""Transport tests for owner profile and saved-search routes."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient

from job_hunt_agent.database import Database
from job_hunt_agent.owner_workspace import WorkspaceConflict
from job_hunt_agent.profile_schemas import (
    CandidateProfileResponse,
    CandidateProfileWrite,
    CareerTrackCreate,
    CareerTrackList,
    CareerTrackResponse,
    HuntInput,
    ResumeVersionSummary,
    SavedSearchCreate,
    SavedSearchCriteria,
    SavedSearchHuntInputResponse,
    SavedSearchList,
    SavedSearchResponse,
)
from job_hunt_agent.routers.session import create_session_router
from job_hunt_agent.routers.workspace import (
    create_workspace_router,
    install_workspace_error_handler,
)
from tests.auth_helpers import login_test_account, seed_test_account


ORIGIN = "http://localhost:3000"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _resume() -> ResumeVersionSummary:
    now = _now()
    return ResumeVersionSummary(
        id="resume1",
        label="Base resume",
        source="pasted",
        parent_resume_version_id=None,
        is_base=True,
        character_count=32,
        version=1,
        created_at=now,
        updated_at=now,
    )


def _track_payload(name: str = "Identity platform") -> dict[str, object]:
    return {
        "name": name,
        "role_families": ["Backend", "Platform"],
        "seniority_levels": ["senior", "staff"],
        "target_locations": ["Remote-India"],
        "priorities": {
            "compensation": 4,
            "scope": 5,
            "learning": 4,
            "company_quality": 3,
            "flexibility": 5,
        },
        "active": True,
    }


def _search_payload() -> dict[str, object]:
    return {
        "name": "Senior identity roles",
        "career_track_id": "track1",
        "criteria": {
            "role_keywords": ["identity", "SCIM"],
            "seniority": "senior",
            "location": ["Remote-India"],
            "comp_min_lpa": 35,
            "comp_max_lpa": 60,
            "employment_types": ["full_time"],
            "max_age_days": 30,
            "country": "in",
        },
        "schedule": {
            "cadence": "daily",
            "timezone": "Asia/Kolkata",
            "local_time": "08:30",
        },
        "pack": "backend_india",
        "active": True,
    }


@dataclass
class FakeWorkspaceStore:
    profile: CandidateProfileResponse | None = None
    tracks: dict[str, CareerTrackResponse] = field(default_factory=dict)
    searches: dict[str, SavedSearchResponse] = field(default_factory=dict)
    track_receipts: dict[str, tuple[CareerTrackCreate, CareerTrackResponse]] = field(
        default_factory=dict
    )

    def get_profile(self, *, owner_id: str) -> CandidateProfileResponse | None:
        assert owner_id == "owner"
        return self.profile

    def put_profile(
        self,
        *,
        owner_id: str,
        payload: CandidateProfileWrite,
        expected_version: int,
    ) -> CandidateProfileResponse:
        assert owner_id == "owner"
        current = self.profile.version if self.profile is not None else 0
        if expected_version != current:
            raise WorkspaceConflict("profile version changed", code="version_conflict")
        now = _now()
        self.profile = CandidateProfileResponse(
            **payload.model_dump(),
            id="profile1",
            base_resume=_resume(),
            version=current + 1,
            created_at=self.profile.created_at if self.profile is not None else now,
            updated_at=now,
        )
        return self.profile

    def list_career_tracks(self, *, owner_id: str) -> CareerTrackList:
        assert owner_id == "owner"
        return CareerTrackList(items=list(self.tracks.values()))

    def create_career_track(
        self,
        *,
        owner_id: str,
        payload: CareerTrackCreate,
        idempotency_key: str,
    ) -> CareerTrackResponse:
        assert owner_id == "owner"
        prior = self.track_receipts.get(idempotency_key)
        if prior is not None:
            if prior[0] != payload:
                raise WorkspaceConflict(
                    "idempotency key was already used for a different request",
                    code="idempotency_conflict",
                )
            return prior[1]
        now = _now()
        response = CareerTrackResponse(
            **payload.model_dump(),
            id="track1",
            version=1,
            created_at=now,
            updated_at=now,
        )
        self.tracks[response.id] = response
        self.track_receipts[idempotency_key] = (payload, response)
        return response

    def list_saved_searches(self, *, owner_id: str) -> SavedSearchList:
        assert owner_id == "owner"
        return SavedSearchList(items=list(self.searches.values()))

    def create_saved_search(
        self,
        *,
        owner_id: str,
        payload: SavedSearchCreate,
        idempotency_key: str,
    ) -> SavedSearchResponse:
        assert owner_id == "owner"
        assert idempotency_key
        now = _now()
        response = SavedSearchResponse(
            **payload.model_dump(),
            id="search1",
            last_scan_at=None,
            next_scan_at=now + timedelta(days=1),
            version=1,
            created_at=now,
            updated_at=now,
        )
        self.searches[response.id] = response
        return response

    def build_hunt_input(
        self,
        *,
        owner_id: str,
        saved_search_id: str,
    ) -> SavedSearchHuntInputResponse | None:
        assert owner_id == "owner"
        saved = self.searches.get(saved_search_id)
        track = self.tracks.get("track1")
        if saved is None or track is None:
            return None
        resume = _resume()
        return SavedSearchHuntInputResponse(
            saved_search_id=saved.id,
            saved_search_version=saved.version,
            career_track_id=track.id,
            career_track_version=track.version,
            resume=resume,
            ready=True,
            blockers=[],
            warnings=[],
            input=HuntInput(
                resume_text="Built reliable identity systems.",
                criteria=SavedSearchCriteria.model_validate(saved.criteria.model_dump()),
                pack=saved.pack,
                use_self_rag=saved.use_self_rag,
            ),
        )


@pytest.fixture
def workspace_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakeWorkspaceStore]]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'workspace.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("JOB_HUNT_SESSION_TTL_DAYS", "30")
    command.upgrade(Config("alembic.ini"), "head")
    database = Database(database_url)
    seed_test_account(database)
    store = FakeWorkspaceStore()
    app = FastAPI()
    app.include_router(
        create_session_router(
            database,
            allowed_origins=[ORIGIN],
            production=False,
        )
    )
    app.include_router(
        create_workspace_router(
            database,
            store,
            allowed_origins=[ORIGIN],
            production=False,
        )
    )
    install_workspace_error_handler(app)
    with TestClient(app) as client:
        yield client, store
    database.dispose()


def _login(client: TestClient) -> None:
    response = login_test_account(client, origin=ORIGIN)
    assert response.status_code == 200, response.text


def test_profile_requires_owner_origin_and_if_match_with_problem_errors(
    workspace_client: tuple[TestClient, FakeWorkspaceStore],
) -> None:
    client, _store = workspace_client
    logged_out = client.get("/api/me/profile")
    assert logged_out.status_code == 401
    assert logged_out.json()["code"] == "owner_session_required"
    assert logged_out.json()["request_id"]

    _login(client)
    missing = client.get("/api/me/profile")
    assert missing.status_code == 404
    assert missing.json()["code"] == "resource_not_found"

    wrong_origin = client.put(
        "/api/me/profile",
        headers={"Origin": "https://attacker.invalid", "If-Match": '"0"'},
        json={"career_thesis": "Build secure identity platforms."},
    )
    assert wrong_origin.status_code == 403
    assert wrong_origin.json()["code"] == "origin_forbidden"

    precondition = client.put(
        "/api/me/profile",
        headers={"Origin": ORIGIN},
        json={"career_thesis": "Build secure identity platforms."},
    )
    assert precondition.status_code == 428
    assert precondition.json()["code"] == "precondition_required"

    blank = client.put(
        "/api/me/profile",
        headers={"Origin": ORIGIN, "If-Match": '"0"'},
        json={},
    )
    assert blank.status_code == 422
    assert blank.json()["code"] == "invalid_request"
    assert "meaningful personal detail" in json.dumps(blank.json())

    created = client.put(
        "/api/me/profile",
        headers={"Origin": ORIGIN, "If-Match": '"0"'},
        json={"career_thesis": "Build secure identity platforms."},
    )
    assert created.status_code == 200, created.text
    assert created.headers["etag"] == '"1"'
    assert created.headers["cache-control"] == "no-store, max-age=0"

    stale = client.put(
        "/api/me/profile",
        headers={"Origin": ORIGIN, "If-Match": '"0"'},
        json={"career_thesis": "Changed concurrently."},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "version_conflict"


def test_create_track_requires_idempotency_and_replays_exactly(
    workspace_client: tuple[TestClient, FakeWorkspaceStore],
) -> None:
    client, _store = workspace_client
    _login(client)
    missing = client.post(
        "/api/career-tracks",
        headers={"Origin": ORIGIN},
        json=_track_payload(),
    )
    assert missing.status_code == 400
    assert missing.json()["code"] == "idempotency_key_required"

    headers = {"Origin": ORIGIN, "Idempotency-Key": "create-track-1"}
    first = client.post("/api/career-tracks", headers=headers, json=_track_payload())
    replay = client.post("/api/career-tracks", headers=headers, json=_track_payload())
    conflict = client.post(
        "/api/career-tracks",
        headers=headers,
        json=_track_payload("Different track"),
    )

    assert first.status_code == 201
    assert first.headers["etag"] == '"1"'
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"


def test_saved_search_is_exactly_hunt_compatible_and_projects_without_work(
    workspace_client: tuple[TestClient, FakeWorkspaceStore],
) -> None:
    client, _store = workspace_client
    _login(client)
    client.post(
        "/api/career-tracks",
        headers={"Origin": ORIGIN, "Idempotency-Key": "track-for-search"},
        json=_track_payload(),
    )

    contradictory = _search_payload()
    contradictory["criteria"] = {
        **contradictory["criteria"],  # type: ignore[arg-type]
        "comp_min_lpa": 70,
        "comp_max_lpa": 60,
    }
    invalid = client.post(
        "/api/saved-searches",
        headers={"Origin": ORIGIN, "Idempotency-Key": "bad-search"},
        json=contradictory,
    )
    assert invalid.status_code == 422
    invalid_body = invalid.json()
    assert invalid_body["code"] == "invalid_request"
    assert invalid_body["message"] == "Request validation failed"
    assert invalid_body["field_errors"]
    safe_problem = json.dumps(
        {key: value for key, value in invalid_body.items() if key != "request_id"}
    )
    assert '"input"' not in safe_problem
    assert "70" not in safe_problem
    assert invalid.headers["cache-control"] == "no-store, max-age=0"

    created = client.post(
        "/api/saved-searches",
        headers={"Origin": ORIGIN, "Idempotency-Key": "search-1"},
        json=_search_payload(),
    )
    assert created.status_code == 201, created.text
    assert created.json()["criteria"]["seniority"] == "senior"

    projection = client.get("/api/saved-searches/search1/hunt-input")
    assert projection.status_code == 200
    body = projection.json()
    assert body["ready"] is True
    assert body["input"]["resume_text"] == "Built reliable identity systems."
    assert body["input"]["criteria"] == created.json()["criteria"]
    assert body["input"]["provider_consent_required"] is True
    assert projection.headers["cache-control"] == "no-store, max-age=0"


def test_workspace_openapi_uses_owner_cookie_on_every_operation(
    workspace_client: tuple[TestClient, FakeWorkspaceStore],
) -> None:
    client, _store = workspace_client
    schema = client.get("/openapi.json").json()
    workspace_prefixes = (
        "/api/me/profile",
        "/api/me/resume-versions",
        "/api/me/evidence",
        "/api/career-tracks",
        "/api/saved-searches",
    )
    operations = 0
    for path, methods in schema["paths"].items():
        if not path.startswith(workspace_prefixes):
            continue
        for operation in methods.values():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            assert operation["security"] == [{"OwnerSessionCookie": []}]
            operations += 1
    assert operations == 20
    validation_schema = schema["paths"]["/api/saved-searches"]["post"]["responses"][
        "422"
    ]["content"]["application/json"]["schema"]
    assert validation_schema["$ref"].endswith("/ProblemResponse")
