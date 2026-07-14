"""Authenticated transport tests for application interview-round endpoints."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient

from job_hunt_agent.database import Database
from job_hunt_agent.interview_round_schemas import (
    ApplicationInterviewRoundsResponse,
    InterviewRoundCreate,
    InterviewRoundEventCreate,
    InterviewRoundMutationResponse,
)
from job_hunt_agent.owner_workspace import (
    WorkspaceConflict,
    WorkspaceInputError,
    WorkspaceUnavailable,
)
from job_hunt_agent.routers.applications import create_application_router
from job_hunt_agent.routers.session import create_session_router
from job_hunt_agent.routers.workspace import install_workspace_error_handler
from job_hunt_agent.security import hash_access_token
from tests.test_interview_round_schemas import _application, _round


OWNER_TOKEN = "interview-round-owner-token-more-than-thirty-two-characters"
ORIGIN = "http://localhost:3000"


def _projection() -> ApplicationInterviewRoundsResponse:
    return ApplicationInterviewRoundsResponse.model_validate(
        {"application": _application(), "rounds": [_round()]}
    )


def _schedule_mutation() -> InterviewRoundMutationResponse:
    round_payload = _round()
    return InterviewRoundMutationResponse.model_validate(
        {
            "application": _application(),
            "round": round_payload,
            "event": round_payload["events"][-1],
            "mutation_created": True,
        }
    )


def _event_mutation() -> InterviewRoundMutationResponse:
    round_payload = _round(rescheduled=True)
    return InterviewRoundMutationResponse.model_validate(
        {
            "application": _application(
                action_id="round1action2",
                action_round_id="round1",
            ),
            "round": round_payload,
            "event": round_payload["events"][-1],
            "mutation_created": True,
        }
    )


@dataclass
class FakeInterviewRoundStore:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    failure: Exception | None = None

    def _raise_failure(self) -> None:
        if self.failure is not None:
            raise self.failure

    def get_application_interview_rounds(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationInterviewRoundsResponse | None:
        self.calls.append(
            ("get", {"owner_id": owner_id, "application_id": application_id})
        )
        self._raise_failure()
        return _projection() if application_id == "application1" else None

    def schedule_interview_round(
        self,
        *,
        owner_id: str,
        application_id: str,
        payload: InterviewRoundCreate,
        expected_application_version: int,
        idempotency_key: str,
    ) -> InterviewRoundMutationResponse | None:
        self.calls.append(
            (
                "schedule",
                {
                    "owner_id": owner_id,
                    "application_id": application_id,
                    "payload": payload,
                    "expected_application_version": expected_application_version,
                    "idempotency_key": idempotency_key,
                },
            )
        )
        self._raise_failure()
        return _schedule_mutation() if application_id == "application1" else None

    def record_interview_round_event(
        self,
        *,
        owner_id: str,
        application_id: str,
        interview_round_id: str,
        payload: InterviewRoundEventCreate,
        expected_round_version: int,
        idempotency_key: str,
    ) -> InterviewRoundMutationResponse | None:
        self.calls.append(
            (
                "event",
                {
                    "owner_id": owner_id,
                    "application_id": application_id,
                    "interview_round_id": interview_round_id,
                    "payload": payload,
                    "expected_round_version": expected_round_version,
                    "idempotency_key": idempotency_key,
                },
            )
        )
        self._raise_failure()
        if application_id != "application1" or interview_round_id != "round1":
            return None
        return _event_mutation()


@pytest.fixture
def interview_round_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakeInterviewRoundStore]]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'interview-round-router.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("JOB_HUNT_OWNER_ID", "owner")
    monkeypatch.setenv("JOB_HUNT_OWNER_TOKEN_HASH", hash_access_token(OWNER_TOKEN))
    monkeypatch.setenv("JOB_HUNT_SESSION_TTL_DAYS", "30")
    command.upgrade(Config("alembic.ini"), "head")
    database = Database(database_url)
    store = FakeInterviewRoundStore()
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


def _schedule_json() -> dict[str, object]:
    return {
        "kind": "technical",
        "title": "Technical interview",
        "scheduled_local": "2026-07-18T14:00:00",
        "scheduled_timezone": "Asia/Kolkata",
        "duration_minutes": 60,
        "meeting_format": "video",
        "next_action_due_on": "2026-07-18",
        "confirm_schedule": True,
    }


def _mutation_headers(*, version: int, key: str) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "If-Match": f'"{version}"',
        "Idempotency-Key": key,
    }


def _assert_problem(response, *, status_code: int, code: str) -> dict[str, object]:
    assert response.status_code == status_code, response.text
    body = response.json()
    assert body["code"] == code
    assert body["request_id"]
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    return body


def test_round_read_requires_auth_forwards_owner_sets_etag_and_handles_not_found(
    interview_round_client: tuple[TestClient, FakeInterviewRoundStore],
) -> None:
    client, store = interview_round_client
    path = "/api/applications/application1/interview-rounds"
    _assert_problem(client.get(path), status_code=401, code="owner_session_required")
    assert store.calls == []

    _login(client)
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.headers["etag"] == '"7"'
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.json()["data_source"] == "database"
    assert response.json()["rounds"][0]["status"] == "scheduled"
    assert store.calls[-1] == (
        "get",
        {"owner_id": "owner", "application_id": "application1"},
    )

    missing = client.get("/api/applications/missingapp/interview-rounds")
    _assert_problem(missing, status_code=404, code="resource_not_found")


def test_schedule_route_requires_origin_preconditions_and_forwards_exact_payload(
    interview_round_client: tuple[TestClient, FakeInterviewRoundStore],
) -> None:
    client, store = interview_round_client
    path = "/api/applications/application1/interview-rounds"
    payload = _schedule_json()
    assert client.post(path, json=payload).status_code == 401
    _login(client)

    forbidden = client.post(
        path,
        headers={
            "Origin": "https://untrusted.example",
            "If-Match": '"7"',
            "Idempotency-Key": "schedule-v7",
        },
        json=payload,
    )
    _assert_problem(forbidden, status_code=403, code="origin_forbidden")
    _assert_problem(
        client.post(path, headers={"Origin": ORIGIN}, json=payload),
        status_code=428,
        code="precondition_required",
    )
    _assert_problem(
        client.post(
            path,
            headers={"Origin": ORIGIN, "If-Match": '"7"'},
            json=payload,
        ),
        status_code=400,
        code="idempotency_key_required",
    )
    _assert_problem(
        client.post(
            path,
            headers={
                "Origin": ORIGIN,
                "If-Match": "W/7",
                "Idempotency-Key": "schedule-v7",
            },
            json=payload,
        ),
        status_code=400,
        code="invalid_if_match",
    )
    assert store.calls == []

    response = client.post(
        path,
        headers=_mutation_headers(version=7, key=" schedule-v7 "),
        json=payload,
    )
    assert response.status_code == 201, response.text
    assert response.headers["etag"] == '"1"'
    assert response.json()["mutation_created"] is True
    assert response.json()["round"]["status"] == "scheduled"
    name, call = store.calls[-1]
    assert name == "schedule"
    assert call["owner_id"] == "owner"
    assert call["application_id"] == "application1"
    assert call["expected_application_version"] == 7
    assert call["idempotency_key"] == "schedule-v7"
    assert call["payload"].model_dump(mode="json") == payload


def test_schedule_route_rejects_unconfirmed_extra_or_offset_payload_before_store(
    interview_round_client: tuple[TestClient, FakeInterviewRoundStore],
) -> None:
    client, store = interview_round_client
    _login(client)
    path = "/api/applications/application1/interview-rounds"
    headers = _mutation_headers(version=7, key="invalid-schedule")
    invalid_payloads = []
    unconfirmed = _schedule_json()
    unconfirmed["confirm_schedule"] = False
    invalid_payloads.append(unconfirmed)
    extra = _schedule_json()
    extra["provider_event_id"] = "PRIVATE-PROVIDER-ID"
    invalid_payloads.append(extra)
    offset = _schedule_json()
    offset["scheduled_local"] = "2026-07-18T14:00:00+05:30"
    invalid_payloads.append(offset)

    for payload in invalid_payloads:
        response = client.post(path, headers=headers, json=payload)
        body = _assert_problem(response, status_code=422, code="invalid_request")
        assert "input" not in body
        assert "PRIVATE-PROVIDER-ID" not in json.dumps(body)
    assert store.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {
            "event_type": "rescheduled",
            "scheduled_local": "2026-07-19T14:00:00",
            "scheduled_timezone": "Asia/Kolkata",
            "duration_minutes": 75,
            "meeting_format": "onsite",
            "next_action_due_on": "2026-07-19",
            "confirm_reschedule": True,
        },
        {
            "event_type": "completed",
            "completed_on": "2026-07-18",
            "next_action_due_on": "2026-07-19",
            "confirm_complete": True,
        },
        {
            "event_type": "cancelled",
            "cancelled_on": "2026-07-17",
            "cancelled_by": "mutual",
            "next_action_due_on": "2026-07-18",
            "confirm_cancel": True,
        },
    ],
)
def test_round_event_route_forwards_each_discriminated_event_and_round_version(
    interview_round_client: tuple[TestClient, FakeInterviewRoundStore],
    payload: dict[str, object],
) -> None:
    client, store = interview_round_client
    _login(client)
    path = "/api/applications/application1/interview-rounds/round1/events"
    response = client.post(
        path,
        headers=_mutation_headers(version=1, key=f"{payload['event_type']}-v1"),
        json=payload,
    )
    assert response.status_code == 200, response.text
    assert response.headers["etag"] == '"2"'
    assert response.json()["round"]["version"] == 2
    name, call = store.calls[-1]
    assert name == "event"
    assert call["owner_id"] == "owner"
    assert call["application_id"] == "application1"
    assert call["interview_round_id"] == "round1"
    assert call["expected_round_version"] == 1
    assert call["idempotency_key"] == f"{payload['event_type']}-v1"
    assert call["payload"].model_dump(mode="json") == payload


def test_round_event_route_requires_headers_confirmation_and_exact_round(
    interview_round_client: tuple[TestClient, FakeInterviewRoundStore],
) -> None:
    client, store = interview_round_client
    _login(client)
    path = "/api/applications/application1/interview-rounds/round1/events"
    payload = {
        "event_type": "completed",
        "completed_on": "2026-07-18",
        "next_action_due_on": "2026-07-19",
        "confirm_complete": True,
    }
    assert client.post(path, headers={"Origin": ORIGIN}, json=payload).status_code == 428
    invalid = dict(payload)
    invalid["confirm_complete"] = False
    response = client.post(
        path,
        headers=_mutation_headers(version=1, key="invalid-complete"),
        json=invalid,
    )
    _assert_problem(response, status_code=422, code="invalid_request")
    assert store.calls == []

    missing = client.post(
        "/api/applications/application1/interview-rounds/missinground/events",
        headers=_mutation_headers(version=1, key="missing-round"),
        json=payload,
    )
    _assert_problem(missing, status_code=404, code="resource_not_found")


@pytest.mark.parametrize(
    ("failure", "status_code", "code"),
    [
        (WorkspaceConflict("round changed", code="version_conflict"), 409, "version_conflict"),
        (WorkspaceInputError("scheduled_local is invalid"), 422, "invalid_request"),
        (WorkspaceUnavailable("PRIVATE_DATABASE_HOST"), 503, "workspace_unavailable"),
    ],
)
def test_round_routes_map_workspace_errors_to_problem_responses(
    interview_round_client: tuple[TestClient, FakeInterviewRoundStore],
    failure: Exception,
    status_code: int,
    code: str,
) -> None:
    client, store = interview_round_client
    _login(client)
    store.failure = failure
    response = client.get("/api/applications/application1/interview-rounds")
    body = _assert_problem(response, status_code=status_code, code=code)
    if status_code == 503:
        assert body["retryable"] is True
        assert "PRIVATE_DATABASE_HOST" not in json.dumps(body)


def test_interview_round_openapi_has_stable_cookie_authenticated_contracts(
    interview_round_client: tuple[TestClient, FakeInterviewRoundStore],
) -> None:
    client, _store = interview_round_client
    schema = client.get("/openapi.json").json()
    operations = {
        ("/api/applications/{application_id}/interview-rounds", "get"),
        ("/api/applications/{application_id}/interview-rounds", "post"),
        (
            "/api/applications/{application_id}/interview-rounds/"
            "{interview_round_id}/events",
            "post",
        ),
    }
    for path, method in operations:
        operation = schema["paths"][path][method]
        assert operation["security"] == [{"OwnerSessionCookie": []}]
        assert operation["responses"]["422"]["content"]["application/json"][
            "schema"
        ]["$ref"].endswith("/ProblemResponse")
    assert schema["paths"][
        "/api/applications/{application_id}/interview-rounds"
    ]["post"]["responses"]["201"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/InterviewRoundMutationResponse")
