"""Transport tests for owner-scoped application-pack routes."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient

from job_hunt_agent.application_pack_schemas import (
    ApplicationPackCreate,
    ApplicationPackEventCreate,
    ApplicationPackResponse,
    ApplicationPackRevisionCreate,
)
from job_hunt_agent.database import Database
from job_hunt_agent.routers.applications import create_application_router
from job_hunt_agent.routers.session import create_session_router
from job_hunt_agent.routers.workspace import install_workspace_error_handler
from job_hunt_agent.security import hash_access_token


OWNER_TOKEN = "application-pack-owner-token-with-more-than-thirty-two-characters"
ORIGIN = "http://localhost:3000"
NOW = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)
DESCRIPTION = "Requirements:\n- Python experience required."
TEXT = "Python experience required."
START = DESCRIPTION.index(TEXT)


def _response(*, version: int, reviewed: bool = False) -> ApplicationPackResponse:
    coverage = "unsupported" if reviewed else "needs_review"
    revision = {
        "id": "revision1",
        "application_pack_id": "pack1",
        "parent_revision_id": None,
        "revision_number": 1,
        "source": "extracted",
        "extraction_version": "requirements-v1",
        "job_description_source": "persisted_description",
        "job_description": DESCRIPTION,
        "requirements": [
            {
                "id": "requirement_01",
                "ordinal": 1,
                "importance": "required",
                "text": TEXT,
                "source_start": START,
                "source_end": START + len(TEXT),
                "coverage": coverage,
                "evidence": [],
            }
        ],
        "created_at": NOW,
    }
    event = {
        "id": "event1",
        "application_pack_id": "pack1",
        "revision_id": "revision1",
        "sequence_number": 1,
        "event_type": "reviewed",
        "occurred_at": NOW,
    }
    return ApplicationPackResponse(
        application_id="application1",
        status="reviewed" if reviewed else "draft",
        pack={
            "id": "pack1",
            "version": version,
            "application_id": "application1",
            "posting_version_id": "postingversion1",
            "base_resume_version_id": "resume1",
            "created_at": NOW,
            "updated_at": NOW,
        },
        current_revision=revision,
        reviewed_revision=revision if reviewed else None,
        review_event=event if reviewed else None,
        blockers=[] if reviewed else ["requirements_need_review"],
    )


@dataclass
class FakeApplicationPackStore:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def get_application_pack(
        self, *, owner_id: str, application_id: str
    ) -> ApplicationPackResponse | None:
        self.calls.append(("get", {"owner_id": owner_id, "application_id": application_id}))
        return _response(version=3) if application_id == "application1" else None

    def create_application_pack(
        self,
        *,
        owner_id: str,
        application_id: str,
        payload: ApplicationPackCreate,
        expected_application_version: int,
        idempotency_key: str,
    ) -> ApplicationPackResponse | None:
        self.calls.append(
            (
                "create",
                {
                    "owner_id": owner_id,
                    "application_id": application_id,
                    "payload": payload,
                    "expected_application_version": expected_application_version,
                    "idempotency_key": idempotency_key,
                },
            )
        )
        return _response(version=1) if application_id == "application1" else None

    def create_application_pack_revision(
        self,
        *,
        owner_id: str,
        application_id: str,
        pack_id: str,
        payload: ApplicationPackRevisionCreate,
        expected_pack_version: int,
        idempotency_key: str,
    ) -> ApplicationPackResponse | None:
        self.calls.append(
            (
                "revision",
                {
                    "owner_id": owner_id,
                    "application_id": application_id,
                    "pack_id": pack_id,
                    "payload": payload,
                    "expected_pack_version": expected_pack_version,
                    "idempotency_key": idempotency_key,
                },
            )
        )
        if application_id != "application1" or pack_id != "pack1":
            return None
        return _response(
            version=4,
            reviewed=payload.confirm_requirements_reviewed is True,
        )

    def record_application_pack_event(
        self,
        *,
        owner_id: str,
        application_id: str,
        pack_id: str,
        payload: ApplicationPackEventCreate,
        expected_pack_version: int,
        idempotency_key: str,
    ) -> ApplicationPackResponse | None:
        self.calls.append(
            (
                "event",
                {
                    "owner_id": owner_id,
                    "application_id": application_id,
                    "pack_id": pack_id,
                    "payload": payload,
                    "expected_pack_version": expected_pack_version,
                    "idempotency_key": idempotency_key,
                },
            )
        )
        if application_id != "application1" or pack_id != "pack1":
            return None
        return _response(version=5, reviewed=True)


@pytest.fixture
def pack_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakeApplicationPackStore]]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'application-pack-router.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("JOB_HUNT_OWNER_ID", "owner")
    monkeypatch.setenv("JOB_HUNT_OWNER_TOKEN_HASH", hash_access_token(OWNER_TOKEN))
    monkeypatch.setenv("JOB_HUNT_SESSION_TTL_DAYS", "30")
    command.upgrade(Config("alembic.ini"), "head")
    database = Database(database_url)
    store = FakeApplicationPackStore()
    app = FastAPI()
    app.include_router(
        create_session_router(database, allowed_origins=[ORIGIN], production=False)
    )
    app.include_router(
        create_application_router(
            database,
            store,  # type: ignore[arg-type]
            allowed_origins=[ORIGIN],
            production=False,
        )
    )
    install_workspace_error_handler(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, store
    database.dispose()


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/session",
        headers={"Origin": ORIGIN},
        json={"owner_token": OWNER_TOKEN},
    )
    assert response.status_code == 200, response.text


def test_pack_get_is_authenticated_owner_scoped_database_read(
    pack_client: tuple[TestClient, FakeApplicationPackStore],
) -> None:
    client, store = pack_client
    path = "/api/applications/application1/application-pack"
    assert client.get(path).status_code == 401
    assert store.calls == []
    _login(client)

    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "draft"
    assert response.headers["etag"] == '"3"'
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert store.calls[-1] == (
        "get",
        {"owner_id": "owner", "application_id": "application1"},
    )


def test_pack_mutations_forward_versions_keys_and_strict_payloads(
    pack_client: tuple[TestClient, FakeApplicationPackStore],
) -> None:
    client, store = pack_client
    _login(client)
    headers = {
        "Origin": ORIGIN,
        "If-Match": '"1"',
        "Idempotency-Key": "pack-create",
    }
    created = client.post(
        "/api/applications/application1/application-packs",
        headers=headers,
        json={"base_resume_version_id": "resume1"},
    )
    assert created.status_code == 201, created.text
    assert created.headers["etag"] == '"1"'
    assert store.calls[-1][1]["expected_application_version"] == 1
    assert store.calls[-1][1]["idempotency_key"] == "pack-create"

    requirement = {
        "id": "requirement_01",
        "ordinal": 1,
        "importance": "required",
        "text": TEXT,
        "source_start": START,
        "source_end": START + len(TEXT),
        "coverage": "unsupported",
        "evidence_refs": [],
    }
    revision = client.post(
        "/api/applications/application1/application-packs/pack1/revisions",
        headers={**headers, "If-Match": '"3"', "Idempotency-Key": "revision"},
        json={
            "parent_revision_id": "revision1",
            "requirements": [requirement],
            "confirm_requirements_reviewed": True,
        },
    )
    assert revision.status_code == 201, revision.text
    assert revision.json()["status"] == "reviewed"
    assert revision.headers["etag"] == '"4"'
    assert store.calls[-1][1]["expected_pack_version"] == 3
    assert store.calls[-1][1]["payload"].confirm_requirements_reviewed is True

    reviewed = client.post(
        "/api/applications/application1/application-packs/pack1/events",
        headers={**headers, "If-Match": '"4"', "Idempotency-Key": "review"},
        json={
            "event_type": "reviewed",
            "revision_id": "revision1",
            "confirm_requirements_reviewed": True,
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "reviewed"
    assert reviewed.headers["etag"] == '"5"'


def test_pack_routes_require_preconditions_and_hide_invalid_private_input(
    pack_client: tuple[TestClient, FakeApplicationPackStore],
) -> None:
    client, store = pack_client
    _login(client)
    path = "/api/applications/application1/application-packs"
    missing_headers = client.post(
        path,
        headers={"Origin": ORIGIN},
        json={"base_resume_version_id": "resume1"},
    )
    assert missing_headers.status_code == 428

    marker = "PRIVATE-JD-MUST-NOT-ECHO"
    invalid = client.post(
        path,
        headers={
            "Origin": ORIGIN,
            "If-Match": '"1"',
            "Idempotency-Key": "invalid",
        },
        json={
            "base_resume_version_id": "resume1",
            "owner_job_description": marker,
            "private_extra": marker,
        },
    )
    assert invalid.status_code == 422
    assert marker not in invalid.text

    false_confirmation = client.post(
        "/api/applications/application1/application-packs/pack1/events",
        headers={
            "Origin": ORIGIN,
            "If-Match": '"1"',
            "Idempotency-Key": "false-confirmation",
        },
        json={
            "event_type": "reviewed",
            "revision_id": "revision1",
            "confirm_requirements_reviewed": False,
        },
    )
    assert false_confirmation.status_code == 422
    assert all(call[0] not in {"create", "event"} for call in store.calls)


def test_application_pack_openapi_uses_stable_paths_cookie_auth_and_problem_contracts(
    pack_client: tuple[TestClient, FakeApplicationPackStore],
) -> None:
    client, _store = pack_client
    schema = client.get("/openapi.json").json()
    operations = {
        ("/api/applications/{application_id}/application-pack", "get"),
        ("/api/applications/{application_id}/application-packs", "post"),
        (
            "/api/applications/{application_id}/application-packs/{pack_id}/revisions",
            "post",
        ),
        (
            "/api/applications/{application_id}/application-packs/{pack_id}/events",
            "post",
        ),
    }
    for path, method in operations:
        operation = schema["paths"][path][method]
        assert operation["security"] == [{"OwnerSessionCookie": []}]
        validation = operation["responses"]["422"]["content"]["application/json"][
            "schema"
        ]
        assert validation["$ref"].endswith("/ProblemResponse")
