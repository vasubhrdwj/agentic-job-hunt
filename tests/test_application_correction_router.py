"""HTTP contract coverage for owner-scoped milestone corrections."""

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
    ApplicationMilestoneCorrectionCreate,
    ApplicationMilestoneCorrectionMutationResponse,
    ApplicationMilestoneCorrectionResponse,
    ApplicationPostingSummary,
    ApplicationSummary,
)
from job_hunt_agent.database import Database
from job_hunt_agent.routers.applications import create_application_router
from job_hunt_agent.routers.session import create_session_router
from job_hunt_agent.routers.workspace import install_workspace_error_handler
from tests.auth_helpers import login_test_account, seed_test_account


ORIGIN = "http://localhost:3000"
NOW = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)


def _mutation() -> ApplicationMilestoneCorrectionMutationResponse:
    action = ActionItemResponse(
        id="screeningaction1",
        version=1,
        application_id="application1",
        kind="prepare_recruiter_screen",
        status="open",
        title="Follow up after recruiter screen",
        due_on=date(2026, 7, 17),
        created_at=NOW,
        updated_at=NOW,
    )
    application = ApplicationSummary(
        id="application1",
        version=5,
        opportunity_id="opportunity1",
        pursued_posting_version_id="postingversion1",
        stage="screening",
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
    correction = ApplicationMilestoneCorrectionResponse(
        id="correction1",
        application_id="application1",
        activity_event_id="screeningevent1",
        correction_number=1,
        previous_effective_on=date(2026, 7, 15),
        corrected_effective_on=date(2026, 7, 14),
        recording_method="manual",
        recorded_at=NOW,
        created_at=NOW,
    )
    event = ApplicationActivityEventResponse(
        id="screeningevent1",
        application_id="application1",
        sequence_number=4,
        event_type="application_screening",
        from_stage="applied",
        to_stage="screening",
        action_item_id=action.id,
        previous_action_item_id="action3",
        effective_on=date(2026, 7, 15),
        resolved_effective_on=date(2026, 7, 14),
        occurred_at=NOW,
        corrections=[correction],
    )
    return ApplicationMilestoneCorrectionMutationResponse(
        application=application,
        activity_event=event,
        correction=correction,
        correction_created=True,
    )


@dataclass
class FakeCorrectionStore:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def record_application_milestone_correction(
        self,
        *,
        owner_id: str,
        application_id: str,
        activity_event_id: str,
        payload: ApplicationMilestoneCorrectionCreate,
        expected_application_version: int,
        idempotency_key: str,
    ) -> ApplicationMilestoneCorrectionMutationResponse | None:
        self.calls.append(
            {
                "owner_id": owner_id,
                "application_id": application_id,
                "activity_event_id": activity_event_id,
                "payload": payload,
                "expected_application_version": expected_application_version,
                "idempotency_key": idempotency_key,
            }
        )
        if (
            application_id != "application1"
            or activity_event_id != "screeningevent1"
        ):
            return None
        return _mutation()


@pytest.fixture
def correction_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakeCorrectionStore]]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'correction-router.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("JOB_HUNT_SESSION_TTL_DAYS", "30")
    command.upgrade(Config("alembic.ini"), "head")
    database = Database(database_url)
    seed_test_account(database)
    store = FakeCorrectionStore()
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


def _post(
    client: TestClient,
    *,
    application_id: str = "application1",
    event_id: str = "screeningevent1",
    headers: dict[str, str] | None = None,
    body: dict[str, object] | None = None,
):
    return client.post(
        f"/api/applications/{application_id}/activity/{event_id}/corrections",
        headers=(
            {
                "Origin": ORIGIN,
                "If-Match": '"4"',
                "Idempotency-Key": "correct-screen-v4",
                **(headers or {}),
            }
        ),
        json=(
            {
                "corrected_effective_on": "2026-07-14",
                "confirm_correction": True,
            }
            if body is None
            else body
        ),
    )


def test_correction_route_requires_owner_headers_and_returns_new_application_etag(
    correction_client: tuple[TestClient, FakeCorrectionStore],
) -> None:
    client, store = correction_client
    _login(client)

    response = _post(client)

    assert response.status_code == 201, response.text
    assert response.headers["etag"] == '"5"'
    assert response.headers["cache-control"] == "no-store, max-age=0"
    payload = response.json()
    assert payload["application"]["version"] == 5
    assert payload["application"]["stage"] == "screening"
    assert payload["activity_event"]["effective_on"] == "2026-07-15"
    assert payload["activity_event"]["resolved_effective_on"] == "2026-07-14"
    assert payload["correction"]["correction_number"] == 1
    assert payload["correction_created"] is True
    assert len(store.calls) == 1
    assert store.calls[0] == {
        "owner_id": "owner",
        "application_id": "application1",
        "activity_event_id": "screeningevent1",
        "payload": ApplicationMilestoneCorrectionCreate(
            corrected_effective_on=date(2026, 7, 14),
            confirm_correction=True,
        ),
        "expected_application_version": 4,
        "idempotency_key": "correct-screen-v4",
    }


@pytest.mark.parametrize(
    ("headers", "body", "status_code", "code"),
    [
        ({"If-Match": ""}, None, 400, "invalid_if_match"),
        ({"Idempotency-Key": ""}, None, 400, "idempotency_key_required"),
        (
            None,
            {
                "corrected_effective_on": "2026-07-14",
                "confirm_correction": False,
            },
            422,
            "invalid_request",
        ),
        (
            None,
            {
                "corrected_effective_on": "2026-07-14",
                "confirm_correction": True,
                "outcome": "rejected",
            },
            422,
            "invalid_request",
        ),
    ],
)
def test_correction_route_rejects_missing_or_unsafe_write_contracts(
    correction_client: tuple[TestClient, FakeCorrectionStore],
    headers: dict[str, str] | None,
    body: dict[str, object] | None,
    status_code: int,
    code: str,
) -> None:
    client, store = correction_client
    _login(client)

    response = _post(client, headers=headers, body=body)

    assert response.status_code == status_code, response.text
    assert response.json()["code"] == code
    assert store.calls == []


def test_correction_route_hides_foreign_or_missing_resources(
    correction_client: tuple[TestClient, FakeCorrectionStore],
) -> None:
    client, store = correction_client
    _login(client)

    response = _post(client, event_id="missingevent")

    assert response.status_code == 404
    assert response.json()["code"] == "resource_not_found"
    assert len(store.calls) == 1


def test_correction_route_requires_authentication_and_allowed_origin(
    correction_client: tuple[TestClient, FakeCorrectionStore],
) -> None:
    client, store = correction_client

    unauthenticated = _post(client)
    assert unauthenticated.status_code == 401

    _login(client)
    forbidden = _post(client, headers={"Origin": "https://evil.example"})
    assert forbidden.status_code == 403
    assert store.calls == []
