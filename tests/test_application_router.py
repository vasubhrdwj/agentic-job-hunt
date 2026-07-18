"""Transport tests for the authenticated, owner-scoped application workspace."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient

from job_hunt_agent.application_schemas import (
    ActionItemResponse,
    ApplicationActivityEventResponse,
    ApplicationActivityListResponse,
    ApplicationDetailResponse,
    ApplicationListResponse,
    ApplicationPostingSummary,
    ApplicationSummary,
)
from job_hunt_agent.contact_schemas import ApplicationContactBenchResponse
from job_hunt_agent.database import Database
from job_hunt_agent.owner_workspace import WorkspaceConflict, WorkspaceUnavailable
from job_hunt_agent.opportunity_schemas import (
    OpportunityDecisionEvent,
    OpportunityDecisionResponse,
)
from job_hunt_agent.routers.applications import create_application_router
from job_hunt_agent.routers.session import create_session_router
from job_hunt_agent.routers.workspace import install_workspace_error_handler
from job_hunt_agent.security import hash_access_token


OWNER_TOKEN = "application-owner-token-with-more-than-thirty-two-characters"
ORIGIN = "http://localhost:3000"
NOW = datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)


def _action() -> ActionItemResponse:
    return ActionItemResponse(
        id="action1",
        version=1,
        application_id="application1",
        kind="review_and_prepare_application",
        status="open",
        title="Review role and prepare application",
        due_on=date(2026, 7, 14),
        created_at=NOW,
        updated_at=NOW,
    )


def _application() -> ApplicationSummary:
    return ApplicationSummary(
        id="application1",
        version=1,
        opportunity_id="opportunity1",
        pursued_posting_version_id="postingversion1",
        stage="pursuing",
        posting=ApplicationPostingSummary(
            id="posting1",
            company="Example",
            title="Senior Backend Engineer",
            canonical_url="https://careers.example.com/jobs/123",
            first_party=True,
            state="open",
        ),
        current_action=_action(),
        created_at=NOW,
        updated_at=NOW,
    )


def _activity() -> ApplicationActivityEventResponse:
    return ApplicationActivityEventResponse(
        id="activity1",
        application_id="application1",
        sequence_number=1,
        event_type="application_created",
        from_stage=None,
        to_stage="pursuing",
        action_item_id="action1",
        occurred_at=NOW,
    )


@dataclass
class FakeApplicationStore:
    calls: list[tuple[str, str]] = field(default_factory=list)
    last_list_query: tuple[int, str | None] | None = None
    last_contact_search: tuple[str, int, str] | None = None
    last_undo: tuple[str, int, str] | None = None
    unavailable: bool = False
    conflict: WorkspaceConflict | None = None

    def list_applications(
        self,
        *,
        owner_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ApplicationListResponse:
        self.calls.append(("list_applications", owner_id))
        self.last_list_query = (limit, cursor)
        if self.unavailable:
            raise WorkspaceUnavailable("PRIVATE_APPLICATION_DATABASE_HOST")
        return ApplicationListResponse(items=[_application()], total=1)

    def get_application(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationDetailResponse | None:
        self.calls.append(("get_application", owner_id))
        if application_id != "application1":
            return None
        return ApplicationDetailResponse(
            application=_application(),
            activity=[_activity()],
        )

    def undo_application_pursuit(
        self,
        *,
        owner_id: str,
        application_id: str,
        expected_application_version: int,
        idempotency_key: str,
    ) -> OpportunityDecisionResponse | None:
        self.calls.append(("undo_application_pursuit", owner_id))
        self.last_undo = (
            application_id,
            expected_application_version,
            idempotency_key,
        )
        if self.unavailable:
            raise WorkspaceUnavailable("PRIVATE_APPLICATION_DATABASE_HOST")
        if self.conflict is not None:
            raise self.conflict
        if application_id != "application1":
            return None
        return OpportunityDecisionResponse(
            opportunity_id="opportunity1",
            opportunity_version=3,
            state="inbox",
            event=OpportunityDecisionEvent(
                id="undoevent1",
                opportunity_id="opportunity1",
                action="restore_to_inbox",
                previous_state="pursued",
                state="inbox",
                restores_event_id="pursuedevent1",
                created_at=NOW,
            ),
        )

    def list_activity(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationActivityListResponse | None:
        self.calls.append(("list_activity", owner_id))
        if application_id != "application1":
            return None
        return ApplicationActivityListResponse(items=[_activity()])

    def get_application_contacts(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationContactBenchResponse | None:
        self.calls.append(("get_application_contacts", owner_id))
        if self.unavailable:
            raise WorkspaceUnavailable("PRIVATE_CONTACT_DATABASE_HOST")
        if application_id != "application1":
            return None
        return ApplicationContactBenchResponse(
            application_id=application_id,
            status="not_started",
            verified_count=0,
            coverage_status="not_started",
        )

    def create_application_contact_search(
        self,
        *,
        owner_id: str,
        application_id: str,
        expected_application_version: int,
        idempotency_key: str,
    ) -> ApplicationContactBenchResponse | None:
        self.calls.append(("create_application_contact_search", owner_id))
        self.last_contact_search = (
            application_id,
            expected_application_version,
            idempotency_key,
        )
        if self.unavailable:
            raise WorkspaceUnavailable("PRIVATE_CONTACT_DATABASE_HOST")
        if self.conflict is not None:
            raise self.conflict
        if application_id != "application1":
            return None
        return ApplicationContactBenchResponse(
            application_id=application_id,
            status="queued",
            verified_count=0,
            coverage_status="pending",
            current_search={
                "id": "contactplan1",
                "version": 1,
                "plan_number": 1,
                "status": "queued",
                "job_stage": "queued",
                "target_count": 5,
                "candidate_limit": 12,
                "confidence_floor": 0.75,
                "discovered_count": 0,
                "evidence_verified_count": 0,
                "selected_count": 0,
                "coverage_status": "pending",
                "exhausted": False,
                "retryable": False,
                "shortfall_reasons": [],
                "created_at": NOW,
                "updated_at": NOW,
            },
        )


@pytest.fixture
def application_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakeApplicationStore]]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'application-router.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("JOB_HUNT_OWNER_ID", "owner")
    monkeypatch.setenv("JOB_HUNT_OWNER_TOKEN_HASH", hash_access_token(OWNER_TOKEN))
    monkeypatch.setenv("JOB_HUNT_SESSION_TTL_DAYS", "30")
    command.upgrade(Config("alembic.ini"), "head")
    database = Database(database_url)
    store = FakeApplicationStore()
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
            store,
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
    assert body["retryable"] in {True, False}
    assert body["request_id"]
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    return body


def test_application_reads_require_auth_and_forward_only_the_session_owner(
    application_client: tuple[TestClient, FakeApplicationStore],
) -> None:
    client, store = application_client
    _assert_problem(
        client.get("/api/applications"),
        status_code=401,
        code="owner_session_required",
    )
    _assert_problem(
        client.get("/api/applications/application1/contacts"),
        status_code=401,
        code="owner_session_required",
    )

    _login(client)
    listed = client.get(
        "/api/applications",
        params={"limit": 7, "cursor": "abc_123"},
    )
    detail = client.get("/api/applications/application1")
    activity = client.get("/api/applications/application1/activity")
    contacts = client.get("/api/applications/application1/contacts")

    assert listed.status_code == 200, listed.text
    assert listed.json()["data_source"] == "database"
    assert detail.status_code == 200, detail.text
    assert detail.headers["etag"] == '"1"'
    assert activity.status_code == 200, activity.text
    assert contacts.status_code == 200, contacts.text
    assert contacts.json()["status"] == "not_started"
    assert contacts.json()["verified_count"] == 0
    for response in (listed, detail, activity, contacts):
        assert response.headers["cache-control"] == "no-store, max-age=0"
        assert response.headers["pragma"] == "no-cache"
    assert store.calls == [
        ("list_applications", "owner"),
        ("get_application", "owner"),
        ("list_activity", "owner"),
        ("get_application_contacts", "owner"),
    ]
    assert store.last_list_query == (7, "abc_123")


def test_application_reads_mask_missing_or_foreign_ids(
    application_client: tuple[TestClient, FakeApplicationStore],
) -> None:
    client, _store = application_client
    _login(client)

    for path in (
        "/api/applications/foreignapplication",
        "/api/applications/foreignapplication/activity",
        "/api/applications/foreignapplication/contacts",
    ):
        body = _assert_problem(
            client.get(path),
            status_code=404,
            code="resource_not_found",
        )
        serialized = json.dumps(body)
        assert "foreignapplication" not in serialized
        assert "owner" not in serialized


def test_application_validation_and_storage_failures_use_safe_problem_responses(
    application_client: tuple[TestClient, FakeApplicationStore],
) -> None:
    client, store = application_client
    _login(client)

    invalid = client.get("/api/applications", params={"limit": 51})
    _assert_problem(invalid, status_code=422, code="invalid_request")
    invalid_cursor = client.get(
        "/api/applications",
        params={"cursor": "not a cursor"},
    )
    _assert_problem(invalid_cursor, status_code=422, code="invalid_request")

    store.unavailable = True
    unavailable = client.get("/api/applications")
    body = _assert_problem(
        unavailable,
        status_code=503,
        code="workspace_unavailable",
    )
    assert body["retryable"] is True
    assert "PRIVATE_APPLICATION_DATABASE_HOST" not in json.dumps(body)

    unavailable_contacts = client.get("/api/applications/application1/contacts")
    contact_body = _assert_problem(
        unavailable_contacts,
        status_code=503,
        code="workspace_unavailable",
    )
    assert contact_body["retryable"] is True
    assert "PRIVATE_CONTACT_DATABASE_HOST" not in json.dumps(contact_body)


def test_undo_pursuit_requires_mutation_security_and_restores_the_opportunity(
    application_client: tuple[TestClient, FakeApplicationStore],
) -> None:
    client, store = application_client
    path = "/api/applications/application1/undo-pursuit"
    valid_headers = {
        "Origin": ORIGIN,
        "If-Match": '"1"',
        "Idempotency-Key": "undo-application1-v1",
    }

    _assert_problem(
        client.post(path, headers=valid_headers),
        status_code=401,
        code="owner_session_required",
    )
    _login(client)
    _assert_problem(
        client.post(
            path,
            headers={**valid_headers, "Origin": "https://attacker.invalid"},
        ),
        status_code=403,
        code="origin_forbidden",
    )
    _assert_problem(
        client.post(
            path,
            headers={
                "Origin": ORIGIN,
                "Idempotency-Key": "undo-application1-v1",
            },
        ),
        status_code=428,
        code="precondition_required",
    )
    _assert_problem(
        client.post(path, headers={"Origin": ORIGIN, "If-Match": '"1"'}),
        status_code=400,
        code="idempotency_key_required",
    )

    restored = client.post(path, headers=valid_headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()["state"] == "inbox"
    assert restored.json()["event"]["previous_state"] == "pursued"
    assert restored.json()["event"]["restores_event_id"] == "pursuedevent1"
    assert restored.headers["etag"] == '"3"'
    assert restored.headers["cache-control"] == "no-store, max-age=0"
    assert store.last_undo == ("application1", 1, "undo-application1-v1")


@pytest.mark.parametrize(
    ("conflict", "code", "retryable"),
    [
        (
            WorkspaceConflict("application changed", code="version_conflict"),
            "version_conflict",
            False,
        ),
        (WorkspaceConflict("outreach was sent"), "resource_conflict", False),
        (
            WorkspaceConflict("key reused", code="idempotency_conflict"),
            "idempotency_conflict",
            False,
        ),
        (
            WorkspaceConflict("application is busy", code="mutation_pending"),
            "mutation_pending",
            True,
        ),
    ],
)
def test_undo_pursuit_masks_missing_applications_and_preserves_conflict_codes(
    application_client: tuple[TestClient, FakeApplicationStore],
    conflict: WorkspaceConflict,
    code: str,
    retryable: bool,
) -> None:
    client, store = application_client
    _login(client)
    headers = {
        "Origin": ORIGIN,
        "If-Match": '"1"',
        "Idempotency-Key": f"undo-{code}",
    }
    missing = client.post(
        "/api/applications/foreignapplication/undo-pursuit",
        headers=headers,
    )
    missing_body = _assert_problem(
        missing,
        status_code=404,
        code="resource_not_found",
    )
    assert "foreignapplication" not in json.dumps(missing_body)

    store.conflict = conflict
    response = client.post(
        "/api/applications/application1/undo-pursuit",
        headers=headers,
    )
    body = _assert_problem(response, status_code=409, code=code)
    assert body["retryable"] is retryable


def test_contact_search_requires_mutation_security_and_returns_queued_bench(
    application_client: tuple[TestClient, FakeApplicationStore],
) -> None:
    client, store = application_client
    path = "/api/applications/application1/contact-searches"
    valid_headers = {
        "Origin": ORIGIN,
        "If-Match": '"1"',
        "Idempotency-Key": "contacts-application1-v1",
    }

    _assert_problem(
        client.post(path, headers=valid_headers),
        status_code=401,
        code="owner_session_required",
    )
    _login(client)
    _assert_problem(
        client.post(
            path,
            headers={**valid_headers, "Origin": "https://attacker.invalid"},
        ),
        status_code=403,
        code="origin_forbidden",
    )
    _assert_problem(
        client.post(
            path,
            headers={
                "Origin": ORIGIN,
                "Idempotency-Key": "contacts-application1-v1",
            },
        ),
        status_code=428,
        code="precondition_required",
    )
    _assert_problem(
        client.post(
            path,
            headers={"Origin": ORIGIN, "If-Match": '"1"'},
        ),
        status_code=400,
        code="idempotency_key_required",
    )

    queued = client.post(path, headers=valid_headers)
    assert queued.status_code == 202, queued.text
    assert queued.json()["status"] == "queued"
    assert queued.json()["current_search"]["job_stage"] == "queued"
    assert queued.json()["target_count"] == 5
    assert queued.headers["cache-control"] == "no-store, max-age=0"
    assert store.last_contact_search == (
        "application1",
        1,
        "contacts-application1-v1",
    )


@pytest.mark.parametrize(
    ("conflict", "code"),
    [
        (
            WorkspaceConflict("application changed", code="version_conflict"),
            "version_conflict",
        ),
        (WorkspaceConflict("posting closed"), "resource_conflict"),
        (
            WorkspaceConflict("key reused", code="idempotency_conflict"),
            "idempotency_conflict",
        ),
    ],
)
def test_contact_search_masks_missing_applications_and_preserves_conflict_codes(
    application_client: tuple[TestClient, FakeApplicationStore],
    conflict: WorkspaceConflict,
    code: str,
) -> None:
    client, store = application_client
    _login(client)
    headers = {
        "Origin": ORIGIN,
        "If-Match": '"1"',
        "Idempotency-Key": f"contacts-{code}",
    }
    missing = client.post(
        "/api/applications/foreignapplication/contact-searches",
        headers=headers,
    )
    missing_body = _assert_problem(
        missing,
        status_code=404,
        code="resource_not_found",
    )
    assert "foreignapplication" not in json.dumps(missing_body)

    store.conflict = conflict
    response = client.post(
        "/api/applications/application1/contact-searches",
        headers=headers,
    )
    _assert_problem(response, status_code=409, code=code)


def test_application_openapi_declares_cookie_auth_and_problem_contracts(
    application_client: tuple[TestClient, FakeApplicationStore],
) -> None:
    client, _store = application_client
    schema = client.get("/openapi.json").json()
    expected = {
        (
            "/api/applications",
            "get",
        ): "list_owner_applications_api_applications_get",
        (
            "/api/applications/{application_id}",
            "get",
        ): "get_owner_application_api_applications__application_id__get",
        (
            "/api/applications/{application_id}/activity",
            "get",
        ): (
            "list_owner_application_activity_api_applications__application_id__"
            "activity_get"
        ),
        (
            "/api/applications/{application_id}/undo-pursuit",
            "post",
        ): (
            "undo_owner_application_pursuit_api_applications__application_id__"
            "undo_pursuit_post"
        ),
        (
            "/api/applications/{application_id}/contacts",
            "get",
        ): (
            "get_owner_application_contacts_api_applications__application_id__"
            "contacts_get"
        ),
        (
            "/api/applications/{application_id}/contact-searches",
            "post",
        ): (
            "create_owner_application_contact_search_api_applications__"
            "application_id__contact_searches_post"
        ),
    }
    for (path, method), operation_id in expected.items():
        operation = schema["paths"][path][method]
        assert operation["operationId"] == operation_id
        assert operation["security"] == [{"OwnerSessionCookie": []}]
        validation_schema = operation["responses"]["422"]["content"][
            "application/json"
        ]["schema"]
        assert validation_schema["$ref"].endswith("/ProblemResponse")

    contact_schema = schema["paths"][
        "/api/applications/{application_id}/contacts"
    ]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert contact_schema["$ref"].endswith("/ApplicationContactBenchResponse")

    create_operation = schema["paths"][
        "/api/applications/{application_id}/contact-searches"
    ]["post"]
    assert "requestBody" not in create_operation
    assert create_operation["responses"]["202"]["content"]["application/json"][
        "schema"
    ]["$ref"].endswith("/ApplicationContactBenchResponse")
