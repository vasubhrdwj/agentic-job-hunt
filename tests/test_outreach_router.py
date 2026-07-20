"""Transport tests for owner-scoped, manual application outreach routes."""

from __future__ import annotations

import json
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

from job_hunt_agent.database import Database
from job_hunt_agent.outreach_schemas import (
    ApplicationOutreachResponse,
    OutreachEventCreate,
    OutreachMessageCreate,
    OutreachReplyCreate,
)
from job_hunt_agent.routers.applications import create_application_router
from job_hunt_agent.routers.session import create_session_router
from job_hunt_agent.routers.workspace import install_workspace_error_handler
from tests.auth_helpers import login_test_account, seed_test_account


ORIGIN = "http://localhost:3000"
NOW = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)


def _outreach(*, version: int) -> ApplicationOutreachResponse:
    return ApplicationOutreachResponse(
        application_id="application1",
        status="active",
        sequence={
            "id": "sequence1",
            "version": version,
            "application_id": "application1",
            "contact_plan_id": "contactplan1",
            "status": "active",
            "active_wave": 1,
            "reason": None,
            "manual_only": True,
            "started_at": NOW,
            "created_at": NOW,
            "updated_at": NOW,
        },
        recipients=[
            {
                "sequence_id": "sequence1",
                "application_contact_id": "applicationcontact1",
                "contact_id": "contact1",
                "public_name": "Asha Example",
                "profile_url": "https://www.linkedin.com/in/asha-example",
                "lifecycle": "active",
                "current_title": "Senior Backend Engineer",
                "current_company": "Example",
                "category": "team_peer",
                "why_relevant": "Works on backend systems near this hiring team.",
                "employer_evidence": {
                    "excerpt": "Public profile lists a Senior Backend Engineer role at Example.",
                    "url": "https://www.linkedin.com/in/asha-example",
                    "source": "linkedin",
                    "observed_at": NOW,
                },
                "bench_rank": 1,
                "wave": 1,
                "bench_state": "ready",
                "no_reply_eligible_at": None,
            }
        ],
        timeline=[],
    )


@dataclass
class FakeOutreachStore:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def get_application_outreach(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationOutreachResponse | None:
        self.calls.append(
            (
                "get_application_outreach",
                {"owner_id": owner_id, "application_id": application_id},
            )
        )
        if application_id != "application1":
            return None
        return _outreach(version=7)

    def start_application_outreach(
        self,
        *,
        owner_id: str,
        application_id: str,
        expected_application_version: int,
        idempotency_key: str,
    ) -> ApplicationOutreachResponse | None:
        self.calls.append(
            (
                "start_application_outreach",
                {
                    "owner_id": owner_id,
                    "application_id": application_id,
                    "expected_application_version": expected_application_version,
                    "idempotency_key": idempotency_key,
                },
            )
        )
        if application_id != "application1":
            return None
        return _outreach(version=11)

    def save_outreach_message(
        self,
        *,
        owner_id: str,
        application_id: str,
        sequence_id: str,
        payload: OutreachMessageCreate,
        expected_sequence_version: int,
        idempotency_key: str,
    ) -> ApplicationOutreachResponse | None:
        self.calls.append(
            (
                "save_outreach_message",
                {
                    "owner_id": owner_id,
                    "application_id": application_id,
                    "sequence_id": sequence_id,
                    "payload": payload,
                    "expected_sequence_version": expected_sequence_version,
                    "idempotency_key": idempotency_key,
                },
            )
        )
        if application_id != "application1" or sequence_id != "sequence1":
            return None
        return _outreach(version=12)

    def record_outreach_event(
        self,
        *,
        owner_id: str,
        application_id: str,
        sequence_id: str,
        payload: OutreachEventCreate,
        expected_sequence_version: int,
        idempotency_key: str,
    ) -> ApplicationOutreachResponse | None:
        self.calls.append(
            (
                "record_outreach_event",
                {
                    "owner_id": owner_id,
                    "application_id": application_id,
                    "sequence_id": sequence_id,
                    "payload": payload,
                    "expected_sequence_version": expected_sequence_version,
                    "idempotency_key": idempotency_key,
                },
            )
        )
        if application_id != "application1" or sequence_id != "sequence1":
            return None
        return _outreach(version=13)

    def record_outreach_reply(
        self,
        *,
        owner_id: str,
        application_id: str,
        sequence_id: str,
        payload: OutreachReplyCreate,
        expected_sequence_version: int,
        idempotency_key: str,
    ) -> ApplicationOutreachResponse | None:
        self.calls.append(
            (
                "record_outreach_reply",
                {
                    "owner_id": owner_id,
                    "application_id": application_id,
                    "sequence_id": sequence_id,
                    "payload": payload,
                    "expected_sequence_version": expected_sequence_version,
                    "idempotency_key": idempotency_key,
                },
            )
        )
        if application_id != "application1" or sequence_id != "sequence1":
            return None
        return _outreach(version=14)


@pytest.fixture
def outreach_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakeOutreachStore]]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'outreach-router.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("JOB_HUNT_SESSION_TTL_DAYS", "30")
    command.upgrade(Config("alembic.ini"), "head")

    database = Database(database_url)
    seed_test_account(database)
    store = FakeOutreachStore()
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
    response = login_test_account(client, origin=ORIGIN)
    assert response.status_code == 200, response.text


def _assert_problem(response: Any, *, status_code: int, code: str) -> dict[str, Any]:
    assert response.status_code == status_code, response.text
    body = response.json()
    assert body["code"] == code
    assert isinstance(body["message"], str) and body["message"]
    assert body["retryable"] in {True, False}
    assert body["request_id"]
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    return body


def _post(
    client: TestClient,
    path: str,
    *,
    headers: dict[str, str],
    payload: dict[str, object] | None,
):
    if payload is None:
        return client.post(path, headers=headers)
    return client.post(path, headers=headers, json=payload)


def test_outreach_get_requires_owner_and_returns_owner_scoped_etag(
    outreach_client: tuple[TestClient, FakeOutreachStore],
) -> None:
    client, store = outreach_client
    path = "/api/applications/application1/outreach"

    _assert_problem(
        client.get(path),
        status_code=401,
        code="owner_session_required",
    )
    assert store.calls == []

    _login(client)
    response = client.get(path)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"
    assert response.json()["sequence"]["manual_only"] is True
    assert response.headers["etag"] == '"7"'
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert store.calls == [
        (
            "get_application_outreach",
            {"owner_id": "owner", "application_id": "application1"},
        )
    ]


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/applications/application1/outreach-sequences", None),
        (
            "/api/applications/application1/outreach-sequences/sequence1/messages",
            {
                "application_contact_id": "applicationcontact1",
                "kind": "initial",
                "body": "Hello Asha",
            },
        ),
        (
            "/api/applications/application1/outreach-sequences/sequence1/events",
            {"event_type": "copied", "message_version_id": "message1"},
        ),
        (
            "/api/applications/application1/outreach-sequences/sequence1/replies",
            {
                "marked_sent_event_id": "markedsentevent1",
                "reply_kind": "reply_received",
                "received_on": "2026-07-14",
                "confirm_exact_sent_attempt": True,
            },
        ),
    ],
)
def test_outreach_mutations_require_auth_origin_version_and_idempotency(
    outreach_client: tuple[TestClient, FakeOutreachStore],
    path: str,
    payload: dict[str, object] | None,
) -> None:
    client, store = outreach_client
    valid_headers = {
        "Origin": ORIGIN,
        "If-Match": '"4"',
        "Idempotency-Key": "manual-outreach-once",
    }

    _assert_problem(
        _post(client, path, headers=valid_headers, payload=payload),
        status_code=401,
        code="owner_session_required",
    )
    _login(client)
    _assert_problem(
        _post(
            client,
            path,
            headers={**valid_headers, "Origin": "https://attacker.invalid"},
            payload=payload,
        ),
        status_code=403,
        code="origin_forbidden",
    )
    _assert_problem(
        _post(
            client,
            path,
            headers={
                "Origin": ORIGIN,
                "Idempotency-Key": "manual-outreach-once",
            },
            payload=payload,
        ),
        status_code=428,
        code="precondition_required",
    )
    _assert_problem(
        _post(
            client,
            path,
            headers={"Origin": ORIGIN, "If-Match": '"4"'},
            payload=payload,
        ),
        status_code=400,
        code="idempotency_key_required",
    )
    assert store.calls == []


def test_outreach_routes_forward_validated_inputs_and_return_sequence_etags(
    outreach_client: tuple[TestClient, FakeOutreachStore],
) -> None:
    client, store = outreach_client
    _login(client)
    headers = {
        "Origin": ORIGIN,
        "If-Match": '"4"',
        "Idempotency-Key": "outreach-mutation-v1",
    }
    exact_body = "  Hi Asha, would you be open to a quick question?\n"

    started = client.post(
        "/api/applications/application1/outreach-sequences",
        headers=headers,
    )
    saved = client.post(
        "/api/applications/application1/outreach-sequences/sequence1/messages",
        headers={**headers, "Idempotency-Key": "message-v1"},
        json={
            "application_contact_id": "applicationcontact1",
            "kind": "initial",
            "body": exact_body,
        },
    )
    recorded = client.post(
        "/api/applications/application1/outreach-sequences/sequence1/events",
        headers={**headers, "Idempotency-Key": "sent-v1"},
        json={
            "event_type": "marked_sent",
            "message_version_id": "message1",
            "channel": "email",
            "confirm_exact_version": True,
        },
    )

    assert started.status_code == 201, started.text
    assert saved.status_code == 200, saved.text
    assert recorded.status_code == 200, recorded.text
    assert started.headers["etag"] == '"11"'
    assert saved.headers["etag"] == '"12"'
    assert recorded.headers["etag"] == '"13"'
    assert store.calls[0] == (
        "start_application_outreach",
        {
            "owner_id": "owner",
            "application_id": "application1",
            "expected_application_version": 4,
            "idempotency_key": "outreach-mutation-v1",
        },
    )
    assert store.calls[1][0] == "save_outreach_message"
    saved_args = store.calls[1][1]
    assert {
        key: saved_args[key]
        for key in (
            "owner_id",
            "application_id",
            "sequence_id",
            "expected_sequence_version",
            "idempotency_key",
        )
    } == {
        "owner_id": "owner",
        "application_id": "application1",
        "sequence_id": "sequence1",
        "expected_sequence_version": 4,
        "idempotency_key": "message-v1",
    }
    assert isinstance(saved_args["payload"], OutreachMessageCreate)
    assert saved_args["payload"].body == exact_body
    assert saved_args["payload"].model_dump(mode="json") == {
        "application_contact_id": "applicationcontact1",
        "kind": "initial",
        "body": exact_body,
    }
    assert store.calls[2][0] == "record_outreach_event"
    event_args = store.calls[2][1]
    assert {
        key: event_args[key]
        for key in (
            "owner_id",
            "application_id",
            "sequence_id",
            "expected_sequence_version",
            "idempotency_key",
        )
    } == {
        "owner_id": "owner",
        "application_id": "application1",
        "sequence_id": "sequence1",
        "expected_sequence_version": 4,
        "idempotency_key": "sent-v1",
    }
    assert event_args["payload"].model_dump(mode="json") == {
        "event_type": "marked_sent",
        "message_version_id": "message1",
        "channel": "email",
        "confirm_exact_version": True,
    }


def test_reply_route_returns_201_and_forwards_only_the_exact_attempt_contract(
    outreach_client: tuple[TestClient, FakeOutreachStore],
) -> None:
    client, store = outreach_client
    _login(client)
    private_note = "  PRIVATE exact reply note  "
    response = client.post(
        "/api/applications/application1/outreach-sequences/sequence1/replies",
        headers={
            "Origin": ORIGIN,
            "If-Match": '"8"',
            "Idempotency-Key": "reply-once",
        },
        json={
            "marked_sent_event_id": "markedsentevent1",
            "reply_kind": "referred",
            "received_on": "2026-07-14",
            "note": private_note,
            "confirm_exact_sent_attempt": True,
        },
    )

    assert response.status_code == 201, response.text
    assert response.headers["etag"] == '"14"'
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert response.json()["sequence"]["version"] == 14
    assert store.calls[-1][0] == "record_outreach_reply"
    args = store.calls[-1][1]
    assert {
        key: args[key]
        for key in (
            "owner_id",
            "application_id",
            "sequence_id",
            "expected_sequence_version",
            "idempotency_key",
        )
    } == {
        "owner_id": "owner",
        "application_id": "application1",
        "sequence_id": "sequence1",
        "expected_sequence_version": 8,
        "idempotency_key": "reply-once",
    }
    assert isinstance(args["payload"], OutreachReplyCreate)
    assert args["payload"].model_dump(mode="json") == {
        "marked_sent_event_id": "markedsentevent1",
        "reply_kind": "referred",
        "received_on": "2026-07-14",
        "note": private_note,
        "confirm_exact_sent_attempt": True,
    }


@pytest.mark.parametrize(
    ("path", "payload", "missing_marker"),
    [
        (
            "/api/applications/foreignapplication/outreach",
            None,
            "foreignapplication",
        ),
        (
            "/api/applications/foreignapplication/outreach-sequences",
            None,
            "foreignapplication",
        ),
        (
            "/api/applications/application1/outreach-sequences/foreignsequence/messages",
            {
                "application_contact_id": "applicationcontact1",
                "kind": "initial",
                "body": "Hello",
            },
            "foreignsequence",
        ),
        (
            "/api/applications/application1/outreach-sequences/foreignsequence/events",
            {"event_type": "copied", "message_version_id": "message1"},
            "foreignsequence",
        ),
        (
            "/api/applications/application1/outreach-sequences/foreignsequence/replies",
            {
                "marked_sent_event_id": "markedsentevent1",
                "reply_kind": "reply_received",
                "received_on": "2026-07-14",
                "confirm_exact_sent_attempt": True,
            },
            "foreignsequence",
        ),
    ],
)
def test_outreach_routes_mask_missing_and_foreign_resources(
    outreach_client: tuple[TestClient, FakeOutreachStore],
    path: str,
    payload: dict[str, object] | None,
    missing_marker: str,
) -> None:
    client, _store = outreach_client
    _login(client)
    headers = {
        "Origin": ORIGIN,
        "If-Match": '"4"',
        "Idempotency-Key": "missing-outreach",
    }
    response = (
        client.get(path)
        if path.endswith("/outreach")
        else _post(client, path, headers=headers, payload=payload)
    )

    body = _assert_problem(
        response,
        status_code=404,
        code="resource_not_found",
    )
    serialized = json.dumps(body)
    assert missing_marker not in serialized
    assert "owner" not in serialized


def test_outreach_validation_is_standardized_and_never_reaches_storage(
    outreach_client: tuple[TestClient, FakeOutreachStore],
) -> None:
    client, store = outreach_client
    _login(client)
    headers = {
        "Origin": ORIGIN,
        "If-Match": '"4"',
        "Idempotency-Key": "invalid-outreach",
    }
    private_marker = "PRIVATE-INVALID-MESSAGE-MARKER"

    invalid_message = client.post(
        "/api/applications/application1/outreach-sequences/sequence1/messages",
        headers=headers,
        json={
            "application_contact_id": "applicationcontact1",
            "kind": "initial",
            "body": " \n\t ",
            "private_extra": private_marker,
        },
    )
    invalid_discriminator = client.post(
        "/api/applications/application1/outreach-sequences/sequence1/events",
        headers=headers,
        json={"event_type": "auto_send", "message_version_id": "message1"},
    )
    false_confirmation = client.post(
        "/api/applications/application1/outreach-sequences/sequence1/events",
        headers=headers,
        json={
            "event_type": "marked_sent",
            "message_version_id": "message1",
            "channel": "linkedin",
            "confirm_exact_version": False,
        },
    )
    numeric_confirmation = client.post(
        "/api/applications/application1/outreach-sequences/sequence1/events",
        headers=headers,
        json={
            "event_type": "marked_sent",
            "message_version_id": "message1",
            "channel": "linkedin",
            "confirm_exact_version": 1,
        },
    )
    false_reply_confirmation = client.post(
        "/api/applications/application1/outreach-sequences/sequence1/replies",
        headers=headers,
        json={
            "marked_sent_event_id": "markedsentevent1",
            "reply_kind": "reply_received",
            "received_on": "2026-07-14",
            "confirm_exact_sent_attempt": False,
        },
    )
    numeric_reply_confirmation = client.post(
        "/api/applications/application1/outreach-sequences/sequence1/replies",
        headers=headers,
        json={
            "marked_sent_event_id": "markedsentevent1",
            "reply_kind": "reply_received",
            "received_on": "2026-07-14",
            "confirm_exact_sent_attempt": 1,
        },
    )
    client_supplied_reply_binding = client.post(
        "/api/applications/application1/outreach-sequences/sequence1/replies",
        headers=headers,
        json={
            "marked_sent_event_id": "markedsentevent1",
            "message_version_id": "message1",
            "reply_kind": "reply_received",
            "received_on": "2026-07-14",
            "confirm_exact_sent_attempt": True,
        },
    )

    for response in (
        invalid_message,
        invalid_discriminator,
        false_confirmation,
        numeric_confirmation,
        false_reply_confirmation,
        numeric_reply_confirmation,
        client_supplied_reply_binding,
    ):
        body = _assert_problem(
            response,
            status_code=422,
            code="invalid_request",
        )
        assert body["retryable"] is False
        assert body["field_errors"]
        assert '"input":' not in json.dumps(body)
    assert private_marker not in json.dumps(invalid_message.json())
    assert store.calls == []
