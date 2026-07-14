"""Transport tests for authenticated Today application actions."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient

from job_hunt_agent.application_schemas import (
    TodayApplicationActionGroup,
    TodayApplicationActionsResponse,
)
from job_hunt_agent.database import Database
from job_hunt_agent.owner_workspace import WorkspaceUnavailable
from job_hunt_agent.routers.applications import create_application_router
from job_hunt_agent.routers.session import create_session_router
from job_hunt_agent.routers.workspace import install_workspace_error_handler
from job_hunt_agent.security import hash_access_token


OWNER_TOKEN = "today-action-owner-token-with-more-than-thirty-two-characters"
ORIGIN = "http://localhost:3000"
NOW = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)


def _response() -> TodayApplicationActionsResponse:
    empty = TodayApplicationActionGroup(total=0, items=[])
    return TodayApplicationActionsResponse(
        as_of=NOW,
        owner_timezone="UTC",
        owner_local_date=date(2026, 7, 15),
        window_ends_on=date(2026, 7, 22),
        overdue=empty,
        today=empty,
        next_7_days=empty,
    )


@dataclass
class FakeTodayApplicationActionStore:
    last_call: tuple[str, int] | None = None
    unavailable: bool = False

    def list_today_application_actions(
        self,
        *,
        owner_id: str,
        limit: int = 20,
    ) -> TodayApplicationActionsResponse:
        self.last_call = (owner_id, limit)
        if self.unavailable:
            raise WorkspaceUnavailable("PRIVATE_ACTION_DATABASE_HOST")
        return _response()


@pytest.fixture
def action_center_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakeTodayApplicationActionStore]]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'action-router.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("JOB_HUNT_OWNER_ID", "owner")
    monkeypatch.setenv("JOB_HUNT_OWNER_TOKEN_HASH", hash_access_token(OWNER_TOKEN))
    monkeypatch.setenv("JOB_HUNT_SESSION_TTL_DAYS", "30")
    command.upgrade(Config("alembic.ini"), "head")
    database = Database(database_url)
    store = FakeTodayApplicationActionStore()
    app = FastAPI()
    app.include_router(
        create_session_router(
            database,
            allowed_origins=[ORIGIN],
            production=False,
        )
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


def _assert_problem(response, *, status_code: int, code: str) -> dict[str, object]:
    assert response.status_code == status_code, response.text
    body = response.json()
    assert body["code"] == code
    assert isinstance(body["message"], str) and body["message"]
    assert body["request_id"]
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    return body


def test_today_application_actions_require_auth_and_forward_owner_and_limit(
    action_center_client: tuple[TestClient, FakeTodayApplicationActionStore],
) -> None:
    client, store = action_center_client
    path = "/api/today/application-actions"
    _assert_problem(
        client.get(path),
        status_code=401,
        code="owner_session_required",
    )

    _login(client)
    response = client.get(path, params={"limit": 7})
    assert response.status_code == 200, response.text
    assert response.json()["data_source"] == "database"
    assert response.json()["owner_timezone"] == "UTC"
    assert response.json()["window_ends_on"] == "2026-07-22"
    assert store.last_call == ("owner", 7)
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"


@pytest.mark.parametrize("limit", [0, 51])
def test_today_application_action_limit_is_safely_bounded(
    action_center_client: tuple[TestClient, FakeTodayApplicationActionStore],
    limit: int,
) -> None:
    client, store = action_center_client
    _login(client)

    response = client.get(
        "/api/today/application-actions",
        params={"limit": limit},
    )
    body = _assert_problem(
        response,
        status_code=422,
        code="invalid_request",
    )
    assert store.last_call is None
    assert "input" not in body


def test_today_application_action_storage_errors_are_sanitized(
    action_center_client: tuple[TestClient, FakeTodayApplicationActionStore],
) -> None:
    client, store = action_center_client
    _login(client)
    store.unavailable = True

    response = client.get("/api/today/application-actions")
    body = _assert_problem(
        response,
        status_code=503,
        code="workspace_unavailable",
    )
    assert body["retryable"] is True
    assert "PRIVATE_ACTION_DATABASE_HOST" not in json.dumps(body)
    assert store.last_call == ("owner", 20)


def test_today_application_action_openapi_is_stable_and_cookie_authenticated(
    action_center_client: tuple[TestClient, FakeTodayApplicationActionStore],
) -> None:
    client, _store = action_center_client
    operation = client.get("/openapi.json").json()["paths"][
        "/api/today/application-actions"
    ]["get"]

    assert operation["operationId"] == (
        "list_today_application_action_items_api_today_application_actions_get"
    )
    assert operation["security"] == [{"OwnerSessionCookie": []}]
    assert operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"].endswith("/TodayApplicationActionsResponse")
    assert operation["responses"]["422"]["content"]["application/json"][
        "schema"
    ]["$ref"].endswith("/ProblemResponse")
