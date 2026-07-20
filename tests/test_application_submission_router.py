"""Transport tests for manual application transitions and submission reloads."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient

from job_hunt_agent.application_schemas import (
    ActionItemResponse,
    ApplicationActivityEventResponse,
    ApplicationPostingSummary,
    ApplicationSummary,
)
from job_hunt_agent.application_submission_schemas import (
    ApplicationSubmissionProjection,
    ApplicationTransitionCreate,
    ApplicationTransitionResponse,
)
from job_hunt_agent.database import Database
from job_hunt_agent.routers.applications import create_application_router
from job_hunt_agent.routers.session import create_session_router
from job_hunt_agent.routers.workspace import install_workspace_error_handler
from tests.auth_helpers import login_test_account, seed_test_account


ORIGIN = "http://localhost:3000"
NOW = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)
EXACT_IDS = {
    "application_pack_id": "pack1",
    "application_pack_revision_id": "grounding1",
    "application_pack_review_event_id": "groundingreview1",
    "application_artifact_revision_id": "artifact1",
    "application_artifact_approval_event_id": "artifactapproval1",
    "tailored_resume_version_id": "resume2",
}


def _ready_transition() -> ApplicationTransitionResponse:
    action = ActionItemResponse(
        id="submitaction1",
        version=1,
        application_id="application1",
        kind="submit_application",
        status="open",
        title="Submit application",
        due_on=date(2026, 7, 15),
        created_at=NOW,
        updated_at=NOW,
    )
    application = ApplicationSummary(
        id="application1",
        version=2,
        opportunity_id="opportunity1",
        pursued_posting_version_id="postingversion1",
        stage="ready_to_apply",
        posting=ApplicationPostingSummary(
            id="posting1",
            company="Example",
            title="Senior Backend Engineer",
            canonical_url="https://careers.example.com/jobs/123",
            first_party=True,
            state="open",
        ),
        current_action=action,
        created_at=NOW,
        updated_at=NOW,
    )
    event = ApplicationActivityEventResponse(
        id="readyevent1",
        application_id="application1",
        sequence_number=2,
        event_type="application_ready_to_apply",
        from_stage="pursuing",
        to_stage="ready_to_apply",
        action_item_id=action.id,
        previous_action_item_id="reviewaction1",
        occurred_at=NOW,
    )
    return ApplicationTransitionResponse(
        application=application,
        activity_event=event,
        transition_created=True,
    )


@dataclass
class FakeApplicationSubmissionStore:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def get_application_submission(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationSubmissionProjection | None:
        self.calls.append(
            ("get", {"owner_id": owner_id, "application_id": application_id})
        )
        if application_id != "application1":
            return None
        return ApplicationSubmissionProjection(
            application_id=application_id,
            stage="pursuing",
            available_destinations=["https://careers.example.com/jobs/123/apply"],
            first_party_verified=True,
        )

    def transition_application(
        self,
        *,
        owner_id: str,
        application_id: str,
        payload: ApplicationTransitionCreate,
        expected_application_version: int,
        idempotency_key: str,
    ) -> ApplicationTransitionResponse | None:
        self.calls.append(
            (
                "transition",
                {
                    "owner_id": owner_id,
                    "application_id": application_id,
                    "payload": payload,
                    "expected_application_version": expected_application_version,
                    "idempotency_key": idempotency_key,
                },
            )
        )
        return _ready_transition() if application_id == "application1" else None


@pytest.fixture
def submission_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakeApplicationSubmissionStore]]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'submission-router.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("JOB_HUNT_SESSION_TTL_DAYS", "30")
    command.upgrade(Config("alembic.ini"), "head")
    database = Database(database_url)
    seed_test_account(database)
    store = FakeApplicationSubmissionStore()
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
    response = login_test_account(client, origin=ORIGIN)
    assert response.status_code == 200, response.text


def _ready_payload() -> dict[str, object]:
    return {
        "to_stage": "ready_to_apply",
        **EXACT_IDS,
        "next_action_due_on": "2026-07-15",
        "confirm_ready": True,
    }


def test_submission_reload_is_authenticated_owner_scoped_and_database_only(
    submission_client: tuple[TestClient, FakeApplicationSubmissionStore],
) -> None:
    client, store = submission_client
    path = "/api/applications/application1/submission"

    assert client.get(path).status_code == 401
    _login(client)
    response = client.get(path)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.json() == {
        "data_source": "database",
        "application_id": "application1",
        "stage": "pursuing",
        "available_destinations": [
            "https://careers.example.com/jobs/123/apply"
        ],
        "first_party_verified": True,
        "submission": None,
    }
    assert store.calls[-1] == (
        "get",
        {"owner_id": "owner", "application_id": "application1"},
    )
    assert client.get("/api/applications/foreignapp/submission").status_code == 404


def test_transition_requires_concurrency_headers_and_forwards_exact_materials(
    submission_client: tuple[TestClient, FakeApplicationSubmissionStore],
) -> None:
    client, store = submission_client
    path = "/api/applications/application1/transitions"
    payload = _ready_payload()

    assert (
        client.post(path, headers={"Origin": ORIGIN}, json=payload).status_code
        == 401
    )
    _login(client)
    assert (
        client.post(path, headers={"Origin": ORIGIN}, json=payload).status_code
        == 428
    )
    assert (
        client.post(
            path,
            headers={"Origin": ORIGIN, "If-Match": '"1"'},
            json=payload,
        ).status_code
        == 400
    )
    response = client.post(
        path,
        headers={
            "Origin": ORIGIN,
            "If-Match": '"1"',
            "Idempotency-Key": "ready-application1-v1",
        },
        json=payload,
    )

    assert response.status_code == 200, response.text
    assert response.headers["etag"] == '"2"'
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.json()["application"]["stage"] == "ready_to_apply"
    assert response.json()["transition_created"] is True
    call_name, call = store.calls[-1]
    assert call_name == "transition"
    assert call["owner_id"] == "owner"
    assert call["expected_application_version"] == 1
    assert call["idempotency_key"] == "ready-application1-v1"
    assert call["payload"].model_dump(mode="json") == payload


def test_transition_rejects_false_confirmation_before_calling_store(
    submission_client: tuple[TestClient, FakeApplicationSubmissionStore],
) -> None:
    client, store = submission_client
    _login(client)
    payload = _ready_payload()
    payload["confirm_ready"] = False

    response = client.post(
        "/api/applications/application1/transitions",
        headers={
            "Origin": ORIGIN,
            "If-Match": '"1"',
            "Idempotency-Key": "invalid-ready",
        },
        json=payload,
    )

    assert response.status_code == 422
    assert store.calls == []
