"""Transport tests for deterministic application-artifact routes."""

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

from job_hunt_agent.application_artifact_schemas import (
    ApplicationArtifactEventCreate,
    ApplicationArtifactRevisionCreate,
    ApplicationArtifactsResponse,
)
from job_hunt_agent.database import Database
from job_hunt_agent.routers.applications import create_application_router
from job_hunt_agent.routers.session import create_session_router
from job_hunt_agent.routers.workspace import install_workspace_error_handler
from job_hunt_agent.resume_docx import ApprovedResumeDocx, DOCX_MEDIA_TYPE
from job_hunt_agent.security import hash_access_token


OWNER_TOKEN = "application-artifact-owner-token-more-than-thirty-two-characters"
ORIGIN = "http://localhost:3000"
NOW = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)


def _response(version: int) -> ApplicationArtifactsResponse:
    return ApplicationArtifactsResponse(
        application_id="application1",
        status="not_started",
        pack={
            "id": "pack1",
            "version": version,
            "application_id": "application1",
            "posting_version_id": "postingversion1",
            "base_resume_version_id": "resume1",
            "created_at": NOW,
            "updated_at": NOW,
        },
        blockers=["grounding_review_required"],
    )


@dataclass
class FakeArtifactStore:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def get_application_artifacts(self, *, owner_id: str, application_id: str):
        self.calls.append(("get", {"owner_id": owner_id, "application_id": application_id}))
        return _response(3) if application_id == "application1" else None

    def get_approved_tailored_resume_docx(
        self,
        *,
        owner_id: str,
        application_id: str,
    ):
        self.calls.append(
            ("download", {"owner_id": owner_id, "application_id": application_id})
        )
        if application_id != "application1":
            return None
        return ApprovedResumeDocx(
            content=b"exact-docx-bytes",
            filename="example-backend-engineer-resume-r2.docx",
            artifact_revision_id="artifact2",
            content_hash="a" * 64,
        )

    def create_application_artifact_revision(
        self,
        *,
        owner_id: str,
        application_id: str,
        pack_id: str,
        payload: ApplicationArtifactRevisionCreate,
        expected_pack_version: int,
        idempotency_key: str,
    ):
        self.calls.append(("revision", locals() | {"self": None}))
        return _response(4) if application_id == "application1" and pack_id == "pack1" else None

    def record_application_artifact_event(
        self,
        *,
        owner_id: str,
        application_id: str,
        pack_id: str,
        payload: ApplicationArtifactEventCreate,
        expected_pack_version: int,
        idempotency_key: str,
    ):
        self.calls.append(("event", locals() | {"self": None}))
        return _response(5) if application_id == "application1" and pack_id == "pack1" else None


@pytest.fixture
def artifact_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakeArtifactStore]]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'artifact-router.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("JOB_HUNT_OWNER_ID", "owner")
    monkeypatch.setenv("JOB_HUNT_OWNER_TOKEN_HASH", hash_access_token(OWNER_TOKEN))
    monkeypatch.setenv("JOB_HUNT_SESSION_TTL_DAYS", "30")
    command.upgrade(Config("alembic.ini"), "head")
    database = Database(database_url)
    store = FakeArtifactStore()
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


def test_artifact_routes_are_authenticated_versioned_and_forward_exact_questions(
    artifact_client: tuple[TestClient, FakeArtifactStore],
) -> None:
    client, store = artifact_client
    path = "/api/applications/application1/application-artifacts"
    assert client.get(path).status_code == 401
    _login(client)
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["etag"] == '"3"'
    assert response.headers["cache-control"] == "no-store, max-age=0"

    mutation_path = (
        "/api/applications/application1/application-packs/pack1/artifact-revisions"
    )
    assert client.post(
        mutation_path,
        headers={"Origin": ORIGIN},
        json={"grounding_revision_id": "grounding1"},
    ).status_code == 428
    created = client.post(
        mutation_path,
        headers={
            "Origin": ORIGIN,
            "If-Match": '"3"',
            "Idempotency-Key": "artifact-revision-1",
        },
        json={
            "grounding_revision_id": "grounding1",
            "questions": [{"id": "question1", "text": "  Exact question?  "}],
        },
    )
    assert created.status_code == 201, created.text
    assert created.headers["etag"] == '"4"'
    call = store.calls[-1]
    assert call[0] == "revision"
    assert call[1]["expected_pack_version"] == 3
    assert call[1]["idempotency_key"] == "artifact-revision-1"
    assert call[1]["payload"].questions[0].text == "  Exact question?  "

    event = client.post(
        "/api/applications/application1/application-packs/pack1/artifact-events",
        headers={
            "Origin": ORIGIN,
            "If-Match": '"4"',
            "Idempotency-Key": "artifact-reject-1",
        },
        json={"event_type": "rejected", "artifact_revision_id": "artifact1"},
    )
    assert event.status_code == 200, event.text
    assert event.headers["etag"] == '"5"'


def test_approved_resume_download_is_authenticated_private_and_owner_scoped(
    artifact_client: tuple[TestClient, FakeArtifactStore],
) -> None:
    client, store = artifact_client
    path = (
        "/api/applications/application1/application-artifacts/approved-resume.docx"
    )
    assert client.get(path).status_code == 401

    _login(client)
    response = client.get(path)

    assert response.status_code == 200
    assert response.content == b"exact-docx-bytes"
    assert response.headers["content-type"] == DOCX_MEDIA_TYPE
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == (
        'attachment; filename="example-backend-engineer-resume-r2.docx"'
    )
    assert store.calls[-1] == (
        "download",
        {"owner_id": "owner", "application_id": "application1"},
    )
    assert client.get(
        "/api/applications/missing/application-artifacts/approved-resume.docx"
    ).status_code == 404


def test_artifact_payloads_reject_models_and_false_approval_confirmation(
    artifact_client: tuple[TestClient, FakeArtifactStore],
) -> None:
    client, store = artifact_client
    _login(client)
    headers = {
        "Origin": ORIGIN,
        "If-Match": '"3"',
        "Idempotency-Key": "artifact-invalid",
    }
    response = client.post(
        "/api/applications/application1/application-packs/pack1/artifact-revisions",
        headers=headers,
        json={"grounding_revision_id": "grounding1", "generation_mode": "model"},
    )
    assert response.status_code == 422
    assert store.calls == []
    response = client.post(
        "/api/applications/application1/application-packs/pack1/artifact-events",
        headers=headers,
        json={
            "event_type": "approved",
            "artifact_revision_id": "artifact1",
            "confirm_artifacts_reviewed": False,
        },
    )
    assert response.status_code == 422
    assert "artifact1" not in response.text
