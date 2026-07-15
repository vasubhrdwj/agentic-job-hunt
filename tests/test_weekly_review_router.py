"""Authenticated HTTP contract for weekly review and action decisions."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from job_hunt_agent.owner_workspace import WorkspaceConflict, WorkspaceUnavailable
from job_hunt_agent.weekly_review_schemas import (
    ApplicationActionReviewMutationResponse,
    WeeklyReviewResponse,
)
from tests.test_application_router import (
    ORIGIN,
    FakeApplicationStore,
    _assert_problem,
    _login,
    application_client,
)
from tests.test_weekly_review_schemas import (
    CREATED_AT,
    RECORDED_AT,
    _action,
    _application,
    _rescue,
    _review,
    _stages,
)


def _weekly_response() -> WeeklyReviewResponse:
    return WeeklyReviewResponse.model_validate(
        {
            "as_of": RECORDED_AT,
            "owner_timezone": "Asia/Kolkata",
            "owner_local_date": "2026-07-15",
            "window": {"starts_on": "2026-04-23", "ends_on": "2026-07-15"},
            "policy": {},
            "stale_application_total": 0,
            "stale_applications": [],
            "funnel": {
                "overall": _stages(),
                "attribution_missing": 1,
                "assessment_missing": 1,
            },
            "outreach": {
                "contacts_two_through_five": [
                    _rescue(position) for position in range(2, 6)
                ],
                "unattributed_legacy_successes": 0,
            },
        }
    )


def _mutation_response(
    *, mutation_created: bool = True
) -> ApplicationActionReviewMutationResponse:
    return ApplicationActionReviewMutationResponse.model_validate(
        {
            "application": _application(),
            "action": _action(),
            "review": _review(
                recorded_at=RECORDED_AT,
                created_at=CREATED_AT,
            ),
            "mutation_created": mutation_created,
        }
    )


def test_weekly_read_requires_session_and_forwards_only_authenticated_owner(
    application_client: tuple[TestClient, FakeApplicationStore],
) -> None:
    client, store = application_client
    path = "/api/review/weekly"
    _assert_problem(
        client.get(path),
        status_code=401,
        code="owner_session_required",
    )
    calls: list[str] = []

    def get_weekly_review(*, owner_id: str):
        calls.append(owner_id)
        return _weekly_response()

    store.get_weekly_review = get_weekly_review  # type: ignore[attr-defined]
    _login(client)
    response = client.get(path)

    assert response.status_code == 200, response.text
    assert response.json()["data_source"] == "database"
    assert response.json()["owner_timezone"] == "Asia/Kolkata"
    assert response.json()["funnel"]["overall"][0]["stage"] == "screen"
    assert "not causal" in response.json()["outreach"]["noncausal_label"]
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert calls == ["owner"]


def test_action_review_requires_origin_if_match_idempotency_and_exact_confirmation(
    application_client: tuple[TestClient, FakeApplicationStore],
) -> None:
    client, store = application_client
    path = "/api/applications/application1/actions/action1/reviews"
    body = {
        "decision": "continue",
        "new_due_on": "2026-07-22",
        "confirm_current_action": True,
    }
    valid_headers = {
        "Origin": ORIGIN,
        "If-Match": '"1"',
        "Idempotency-Key": "review-application1-v1",
    }
    _assert_problem(
        client.post(path, headers=valid_headers, json=body),
        status_code=401,
        code="owner_session_required",
    )
    _login(client)
    _assert_problem(
        client.post(
            path,
            headers={**valid_headers, "Origin": "https://attacker.invalid"},
            json=body,
        ),
        status_code=403,
        code="origin_forbidden",
    )
    _assert_problem(
        client.post(
            path,
            headers={
                "Origin": ORIGIN,
                "Idempotency-Key": "review-application1-v1",
            },
            json=body,
        ),
        status_code=428,
        code="precondition_required",
    )
    _assert_problem(
        client.post(
            path,
            headers={"Origin": ORIGIN, "If-Match": '"1"'},
            json=body,
        ),
        status_code=400,
        code="idempotency_key_required",
    )
    _assert_problem(
        client.post(
            path,
            headers=valid_headers,
            json={**body, "confirm_current_action": 1},
        ),
        status_code=422,
        code="invalid_request",
    )

    captured: dict[str, object] = {}

    def record_application_action_review(**kwargs):
        captured.update(kwargs)
        return _mutation_response()

    store.record_application_action_review = (  # type: ignore[attr-defined]
        record_application_action_review
    )
    created = client.post(path, headers=valid_headers, json=body)

    assert created.status_code == 201, created.text
    assert created.headers["etag"] == '"2"'
    assert created.headers["cache-control"] == "no-store, max-age=0"
    assert created.json()["mutation_created"] is True
    assert captured["owner_id"] == "owner"
    assert captured["application_id"] == "application1"
    assert captured["action_id"] == "action1"
    assert captured["expected_application_version"] == 1
    assert captured["idempotency_key"] == "review-application1-v1"
    assert captured["payload"].decision.value == "continue"  # type: ignore[union-attr]


def test_weekly_routes_mask_missing_and_preserve_safe_workspace_errors(
    application_client: tuple[TestClient, FakeApplicationStore],
) -> None:
    client, store = application_client
    _login(client)

    def unavailable(*, owner_id: str):
        del owner_id
        raise WorkspaceUnavailable("PRIVATE_WEEKLY_DATABASE")

    store.get_weekly_review = unavailable  # type: ignore[attr-defined]
    body = _assert_problem(
        client.get("/api/review/weekly"),
        status_code=503,
        code="workspace_unavailable",
    )
    assert "PRIVATE_WEEKLY_DATABASE" not in json.dumps(body)

    path = "/api/applications/foreignapplication/actions/foreignaction/reviews"
    headers = {
        "Origin": ORIGIN,
        "If-Match": '"1"',
        "Idempotency-Key": "missing-review",
    }
    request_body = {
        "decision": "waiting",
        "new_due_on": "2026-07-22",
        "confirm_current_action": True,
    }

    def missing(**_kwargs):
        return None

    store.record_application_action_review = missing  # type: ignore[attr-defined]
    missing_response = client.post(path, headers=headers, json=request_body)
    missing_body = _assert_problem(
        missing_response,
        status_code=404,
        code="resource_not_found",
    )
    serialized = json.dumps(missing_body)
    assert "foreignapplication" not in serialized
    assert "foreignaction" not in serialized

    def conflict(**_kwargs):
        raise WorkspaceConflict("review changed", code="version_conflict")

    store.record_application_action_review = conflict  # type: ignore[attr-defined]
    conflict_response = client.post(
        "/api/applications/application1/actions/action1/reviews",
        headers=headers,
        json=request_body,
    )
    _assert_problem(
        conflict_response,
        status_code=409,
        code="version_conflict",
    )


def test_weekly_openapi_declares_cookie_auth_headers_body_and_problem_contracts(
    application_client: tuple[TestClient, FakeApplicationStore],
) -> None:
    client, _store = application_client
    schema = client.get("/openapi.json").json()

    read = schema["paths"]["/api/review/weekly"]["get"]
    assert read["operationId"] == "get_owner_weekly_review_api_review_weekly_get"
    assert read["security"] == [{"OwnerSessionCookie": []}]
    assert read["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/WeeklyReviewResponse")

    mutation = schema["paths"][
        "/api/applications/{application_id}/actions/{action_id}/reviews"
    ]["post"]
    assert mutation["operationId"] == (
        "review_owner_application_action_api_applications__application_id__"
        "actions__action_id__reviews_post"
    )
    assert mutation["security"] == [{"OwnerSessionCookie": []}]
    parameters = {item["name"]: item for item in mutation["parameters"]}
    assert {"application_id", "action_id", "If-Match", "Idempotency-Key"} <= (
        parameters.keys()
    )
    assert mutation["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/ApplicationActionReviewCreate")
    assert mutation["responses"]["201"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/ApplicationActionReviewMutationResponse")
    for code in ("400", "401", "403", "404", "409", "422", "428", "503"):
        assert mutation["responses"][code]["content"]["application/json"][
            "schema"
        ]["$ref"].endswith("/ProblemResponse")
