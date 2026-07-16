"""Fake-store transport tests for the owner-scoped opportunity radar API."""

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
from job_hunt_agent.opportunity_schemas import (
    CompensationEvidenceFact,
    DateEvidenceFact,
    EmploymentTypeEvidenceFact,
    OpportunityDecisionEvent,
    OpportunityDecisionRequest,
    OpportunityDecisionResponse,
    OpportunityDetailResponse,
    OpportunityFacts,
    OpportunityPosting,
    OpportunityUnknown,
    PostingVersionSummary,
    SavedSearchProvenance,
    ScanCreateRequest,
    ScanCreateResponse,
    ScanStatusResponse,
    TextEvidenceFact,
    TodayListResponse,
    TodayOpportunityItem,
    TodayQuery,
    TodayScanHealth,
    TodaySummary,
    TransparentMatchSummary,
)
from job_hunt_agent.owner_workspace import (
    WorkspaceCapabilityUnavailable,
    WorkspaceConflict,
    WorkspaceNotFound,
    WorkspaceUnavailable,
)
from job_hunt_agent.routers.opportunities import create_opportunity_router
from job_hunt_agent.routers.session import create_session_router
from job_hunt_agent.routers.workspace import install_workspace_error_handler
from job_hunt_agent.security import hash_access_token


OWNER_TOKEN = "opportunity-owner-token-with-more-than-thirty-two-characters"
ORIGIN = "http://localhost:3000"
NOW = datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)


def _scan() -> ScanCreateResponse:
    return ScanCreateResponse(
        id="scan1",
        version=4,
        saved_search_id="search1",
        saved_search_version=3,
        status="queued",
        stage="queued",
        queued_at=NOW,
    )


def _facts() -> OpportunityFacts:
    return OpportunityFacts(
        location=TextEvidenceFact(
            state="verified",
            value="Bengaluru",
            source_label="Workday",
            observed_at=NOW,
        ),
        employment_type=EmploymentTypeEvidenceFact(
            state="inferred",
            value="full_time",
            source_label="Job description",
            observed_at=NOW,
        ),
        posted_date=DateEvidenceFact(state="unknown"),
        compensation=CompensationEvidenceFact(state="unknown"),
    )


def _posting() -> OpportunityPosting:
    return OpportunityPosting(
        id="posting1",
        company="Example",
        company_slug="example",
        title="Senior Backend Engineer",
        summary="Build reliable identity services.",
        canonical_url="https://careers.example.com/jobs/123",
        source="workday",
        source_job_id="REQ-123",
        first_party=True,
        state="open",
        change_kind="new",
        first_seen_at=NOW - timedelta(hours=1),
        last_confirmed_at=NOW,
    )


def _opportunity_item() -> TodayOpportunityItem:
    return TodayOpportunityItem(
        id="opportunity1",
        version=2,
        state="inbox",
        lane="core",
        posting=_posting(),
        facts=_facts(),
        unknowns=[
            OpportunityUnknown(
                field="posted_date",
                reason_code="not_reported_by_source",
                message="The source did not report an original posting date.",
            ),
            OpportunityUnknown(
                field="compensation",
                reason_code="not_reported_by_source",
                message="The source did not report compensation.",
            ),
        ],
        discovered_by=[
            SavedSearchProvenance(
                saved_search_id="search1",
                saved_search_name="Senior backend India",
                first_matched_at=NOW - timedelta(hours=1),
                last_matched_at=NOW,
            )
        ],
        match=TransparentMatchSummary(
            state="not_assessed",
            not_assessed_reason="assessment_pending",
        ),
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW,
    )


def _opportunity_detail() -> OpportunityDetailResponse:
    item = _opportunity_item()
    return OpportunityDetailResponse(
        **item.model_dump(),
        description="Build and operate reliable identity services.",
        apply_urls=["https://careers.example.com/jobs/123/apply"],
        posting_versions=[
            PostingVersionSummary(
                version=1,
                observed_at=NOW,
                change_kind="new",
            )
        ],
    )


def _today() -> TodayListResponse:
    return TodayListResponse(
        as_of=NOW,
        summary=TodaySummary(needs_decision=1, watching=0, dismissed=0),
        scan_health=TodayScanHealth(
            state="healthy",
            active_searches=1,
            last_attempt_at=NOW,
            last_success_at=NOW,
        ),
        items=[_opportunity_item()],
    )


@dataclass
class FakeOpportunityStore:
    calls: list[tuple[str, str]] = field(default_factory=list)
    last_scan_create: dict[str, object] | None = None
    last_today_query: TodayQuery | None = None
    last_decision: dict[str, object] | None = None
    unavailable_today: bool = False

    def create_scan(
        self,
        *,
        owner_id: str,
        saved_search_id: str,
        expected_saved_search_version: int,
        idempotency_key: str,
        payload: ScanCreateRequest,
    ) -> ScanCreateResponse:
        self.calls.append(("create_scan", owner_id))
        if saved_search_id == "foreignsearch":
            raise WorkspaceNotFound("saved search not found")
        if saved_search_id == "workerless":
            raise WorkspaceCapabilityUnavailable(
                "role_scan",
                reason="no_fresh_worker",
            )
        self.last_scan_create = {
            "saved_search_id": saved_search_id,
            "expected_version": expected_saved_search_version,
            "idempotency_key": idempotency_key,
            "payload": payload,
        }
        return _scan()

    def get_scan(self, *, owner_id: str, scan_id: str) -> ScanStatusResponse | None:
        self.calls.append(("get_scan", owner_id))
        return _scan() if scan_id == "scan1" else None

    def list_today(self, *, owner_id: str, query: TodayQuery) -> TodayListResponse:
        self.calls.append(("list_today", owner_id))
        if self.unavailable_today:
            raise WorkspaceUnavailable("PRIVATE_DATABASE_HOST")
        self.last_today_query = query
        return _today()

    def get_opportunity(
        self,
        *,
        owner_id: str,
        opportunity_id: str,
    ) -> OpportunityDetailResponse | None:
        self.calls.append(("get_opportunity", owner_id))
        return _opportunity_detail() if opportunity_id == "opportunity1" else None

    def decide_opportunity(
        self,
        *,
        owner_id: str,
        opportunity_id: str,
        expected_version: int,
        idempotency_key: str,
        payload: OpportunityDecisionRequest,
    ) -> OpportunityDecisionResponse:
        self.calls.append(("decide_opportunity", owner_id))
        if opportunity_id != "opportunity1":
            raise WorkspaceNotFound("opportunity not found")
        if expected_version != 2:
            raise WorkspaceConflict("opportunity version changed", code="version_conflict")
        self.last_decision = {
            "expected_version": expected_version,
            "idempotency_key": idempotency_key,
            "payload": payload,
        }
        if payload.action.value == "watch":
            previous_state = "inbox"
            state = "watch"
            restores_event_id = None
        elif payload.action.value == "dismiss":
            previous_state = "inbox"
            state = "dismiss"
            restores_event_id = None
        else:
            previous_state = "watch"
            state = "inbox"
            restores_event_id = payload.restore_decision_event_id
        event = OpportunityDecisionEvent(
            id="decision1",
            opportunity_id=opportunity_id,
            action=payload.action,
            previous_state=previous_state,
            state=state,
            dismiss_reason=payload.dismiss_reason,
            note=payload.note,
            restores_event_id=restores_event_id,
            created_at=NOW,
        )
        return OpportunityDecisionResponse(
            opportunity_id=opportunity_id,
            opportunity_version=3,
            state=state,
            event=event,
        )


@pytest.fixture
def opportunity_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakeOpportunityStore]]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'opportunity-router.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("JOB_HUNT_OWNER_ID", "owner")
    monkeypatch.setenv("JOB_HUNT_OWNER_TOKEN_HASH", hash_access_token(OWNER_TOKEN))
    monkeypatch.setenv("JOB_HUNT_SESSION_TTL_DAYS", "30")
    command.upgrade(Config("alembic.ini"), "head")
    database = Database(database_url)
    store = FakeOpportunityStore()
    app = FastAPI()
    app.include_router(
        create_session_router(
            database,
            allowed_origins=[ORIGIN],
            production=False,
        )
    )
    app.include_router(
        create_opportunity_router(
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
    return body


def test_radar_reads_require_owner_and_forward_only_authenticated_owner_scope(
    opportunity_client: tuple[TestClient, FakeOpportunityStore],
) -> None:
    client, store = opportunity_client
    _assert_problem(
        client.get("/api/today"),
        status_code=401,
        code="owner_session_required",
    )

    _login(client)
    scan = client.get("/api/scans/scan1")
    today = client.get("/api/today")
    opportunity = client.get("/api/opportunities/opportunity1")

    assert scan.status_code == 200
    assert scan.headers["etag"] == '"4"'
    assert today.status_code == 200
    assert today.json()["data_source"] == "database"
    assert opportunity.status_code == 200
    assert opportunity.headers["etag"] == '"2"'
    assert all(
        response.headers["cache-control"] == "no-store, max-age=0"
        for response in (scan, today, opportunity)
    )
    assert store.calls == [
        ("get_scan", "owner"),
        ("list_today", "owner"),
        ("get_opportunity", "owner"),
    ]


def test_scan_create_enforces_origin_preconditions_idempotency_202_and_etag(
    opportunity_client: tuple[TestClient, FakeOpportunityStore],
) -> None:
    client, store = opportunity_client
    _login(client)
    path = "/api/saved-searches/search1/scans"

    _assert_problem(
        client.post(
            path,
            headers={
                "Origin": "https://attacker.invalid",
                "If-Match": '"3"',
                "Idempotency-Key": "scan-1",
            },
            json={},
        ),
        status_code=403,
        code="origin_forbidden",
    )
    _assert_problem(
        client.post(
            path,
            headers={"Origin": ORIGIN, "Idempotency-Key": "scan-1"},
            json={},
        ),
        status_code=428,
        code="precondition_required",
    )
    _assert_problem(
        client.post(
            path,
            headers={"Origin": ORIGIN, "If-Match": '"3"'},
            json={},
        ),
        status_code=400,
        code="idempotency_key_required",
    )

    created = client.post(
        path,
        headers={
            "Origin": ORIGIN,
            "If-Match": '"3"',
            "Idempotency-Key": "scan-1",
        },
        json={},
    )
    assert created.status_code == 202, created.text
    assert created.headers["etag"] == '"4"'
    assert created.headers["cache-control"] == "no-store, max-age=0"
    assert created.json()["status"] == "queued"
    assert store.last_scan_create is not None
    assert store.last_scan_create["expected_version"] == 3
    assert store.last_scan_create["idempotency_key"] == "scan-1"


def test_scan_and_opportunity_reads_mask_foreign_or_missing_resources(
    opportunity_client: tuple[TestClient, FakeOpportunityStore],
) -> None:
    client, _store = opportunity_client
    _login(client)

    scan = client.get("/api/scans/foreignscan")
    opportunity = client.get("/api/opportunities/foreignopportunity")
    for response in (scan, opportunity):
        body = _assert_problem(
            response,
            status_code=404,
            code="resource_not_found",
        )
        serialized = json.dumps(body)
        assert "foreign" not in serialized
        assert "owner" not in serialized


def test_scan_create_reports_worker_unavailability_as_actionable_retryable_problem(
    opportunity_client: tuple[TestClient, FakeOpportunityStore],
) -> None:
    client, _store = opportunity_client
    _login(client)
    response = client.post(
        "/api/saved-searches/workerless/scans",
        headers={
            "Origin": ORIGIN,
            "If-Match": '"3"',
            "Idempotency-Key": "workerless-scan",
        },
        json={},
    )
    body = _assert_problem(
        response,
        status_code=503,
        code="scan_worker_unavailable",
    )
    assert body["retryable"] is True
    assert "scan service" in body["message"]
    assert "no_fresh_worker" not in json.dumps(body)


def test_today_query_validation_is_safe_and_valid_filters_reach_store(
    opportunity_client: tuple[TestClient, FakeOpportunityStore],
) -> None:
    client, store = opportunity_client
    _login(client)

    valid = client.get(
        "/api/today",
        params={
            "view": "watching",
            "saved_search_id": "search1",
            "lane": "core",
            "cursor": "cursor_123-Z",
            "limit": "7",
        },
    )
    assert valid.status_code == 200, valid.text
    assert store.last_today_query == TodayQuery(
        view="watching",
        saved_search_id="search1",
        lane="core",
        cursor="cursor_123-Z",
        limit=7,
    )

    invalid_limit = client.get("/api/today", params={"limit": "51"})
    _assert_problem(invalid_limit, status_code=422, code="invalid_request")

    invalid_cursor = client.get("/api/today", params={"cursor": "unsafe cursor"})
    body = _assert_problem(
        invalid_cursor,
        status_code=422,
        code="invalid_request",
    )
    assert "unsafe cursor" not in json.dumps(body)


def test_decision_enforces_origin_preconditions_etag_and_safe_validation(
    opportunity_client: tuple[TestClient, FakeOpportunityStore],
) -> None:
    client, store = opportunity_client
    _login(client)
    path = "/api/opportunities/opportunity1/decision"

    _assert_problem(
        client.post(
            path,
            headers={"Origin": ORIGIN, "Idempotency-Key": "decision-1"},
            json={"action": "watch"},
        ),
        status_code=428,
        code="precondition_required",
    )
    _assert_problem(
        client.post(
            path,
            headers={"Origin": ORIGIN, "If-Match": '"2"'},
            json={"action": "watch"},
        ),
        status_code=400,
        code="idempotency_key_required",
    )

    sensitive_note = "PRIVATE_RESUME_TEXT_" + "x" * 500
    invalid = client.post(
        path,
        headers={
            "Origin": ORIGIN,
            "If-Match": '"2"',
            "Idempotency-Key": "bad-decision",
        },
        json={"action": "dismiss", "note": sensitive_note},
    )
    body = _assert_problem(invalid, status_code=422, code="invalid_request")
    assert "PRIVATE_RESUME_TEXT" not in json.dumps(body)

    decided = client.post(
        path,
        headers={
            "Origin": ORIGIN,
            "If-Match": '"2"',
            "Idempotency-Key": "decision-1",
        },
        json={"action": "watch", "note": "Review after earnings."},
    )
    assert decided.status_code == 200, decided.text
    assert decided.headers["etag"] == '"3"'
    assert decided.headers["cache-control"] == "no-store, max-age=0"
    assert decided.json()["state"] == "watch"
    assert store.last_decision is not None
    assert store.last_decision["expected_version"] == 2
    assert store.last_decision["idempotency_key"] == "decision-1"

    stale = client.post(
        path,
        headers={
            "Origin": ORIGIN,
            "If-Match": '"1"',
            "Idempotency-Key": "decision-stale",
        },
        json={"action": "watch"},
    )
    _assert_problem(stale, status_code=409, code="version_conflict")


def test_decision_masks_foreign_opportunity_and_requires_allowed_origin(
    opportunity_client: tuple[TestClient, FakeOpportunityStore],
) -> None:
    client, _store = opportunity_client
    _login(client)
    headers = {
        "Origin": ORIGIN,
        "If-Match": '"2"',
        "Idempotency-Key": "decision-foreign",
    }
    response = client.post(
        "/api/opportunities/foreignopportunity/decision",
        headers=headers,
        json={"action": "watch"},
    )
    body = _assert_problem(response, status_code=404, code="resource_not_found")
    assert "foreign" not in json.dumps(body)

    wrong_origin = client.post(
        "/api/opportunities/opportunity1/decision",
        headers={**headers, "Origin": "https://attacker.invalid"},
        json={"action": "watch"},
    )
    _assert_problem(wrong_origin, status_code=403, code="origin_forbidden")


def test_store_failures_return_a_safe_retryable_problem(
    opportunity_client: tuple[TestClient, FakeOpportunityStore],
) -> None:
    client, store = opportunity_client
    _login(client)
    store.unavailable_today = True

    response = client.get("/api/today")
    body = _assert_problem(
        response,
        status_code=503,
        code="workspace_unavailable",
    )
    assert body["retryable"] is True
    assert "PRIVATE_DATABASE_HOST" not in json.dumps(body)


def test_opportunity_openapi_has_cookie_security_problem_responses_and_stable_ids(
    opportunity_client: tuple[TestClient, FakeOpportunityStore],
) -> None:
    client, _store = opportunity_client
    schema = client.get("/openapi.json").json()
    expected = {
        (
            "/api/saved-searches/{saved_search_id}/scans",
            "post",
        ): "create_scan_api_saved_searches__saved_search_id__scans_post",
        ("/api/scans/{scan_id}", "get"): "get_scan_api_scans__scan_id__get",
        ("/api/today", "get"): "list_today_api_today_get",
        (
            "/api/opportunities/{opportunity_id}",
            "get",
        ): "get_opportunity_api_opportunities__opportunity_id__get",
        (
            "/api/opportunities/{opportunity_id}/decision",
            "post",
        ): "decide_opportunity_api_opportunities__opportunity_id__decision_post",
    }
    for (path, method), operation_id in expected.items():
        operation = schema["paths"][path][method]
        assert operation["operationId"] == operation_id
        assert operation["security"] == [{"OwnerSessionCookie": []}]
        validation_schema = operation["responses"]["422"]["content"][
            "application/json"
        ]["schema"]
        assert validation_schema["$ref"].endswith("/ProblemResponse")
