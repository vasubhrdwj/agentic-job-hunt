"""Transaction adapter and safe error mapping for Phase 6B."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

import job_hunt_agent.sqlalchemy_application_workspace as workspace_module
from job_hunt_agent.mutation_receipts import (
    MutationIdempotencyConflict,
    MutationPending,
)
from job_hunt_agent.owner_workspace import (
    WorkspaceConflict,
    WorkspaceInputError,
    WorkspaceUnavailable,
)
from job_hunt_agent.repository_errors import ResourceConflict, VersionConflict
from job_hunt_agent.security import load_data_keyring
from job_hunt_agent.sqlalchemy_application_workspace import (
    SqlAlchemyApplicationWorkspaceStore,
)
from job_hunt_agent.weekly_review_repository import WeeklyReviewRepositoryError
from job_hunt_agent.weekly_review_schemas import ApplicationActionReviewCreate


class FakeDatabase:
    @contextmanager
    def session(self):
        yield object()


def _store() -> SqlAlchemyApplicationWorkspaceStore:
    return SqlAlchemyApplicationWorkspaceStore(
        FakeDatabase(),  # type: ignore[arg-type]
        load_data_keyring(production=False),
    )


def _payload() -> ApplicationActionReviewCreate:
    return ApplicationActionReviewCreate(
        decision="continue",
        new_due_on=date(2026, 7, 22),
        confirm_current_action=True,
    )


def test_weekly_store_owns_sessions_and_forwards_exact_read_and_mutation_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    projection = object()
    mutation = object()
    calls: list[tuple[str, object, dict[str, object]]] = []

    def fake_load(session, **kwargs):
        calls.append(("load", session, kwargs))
        return projection

    def fake_record(session, **kwargs):
        calls.append(("record", session, kwargs))
        return mutation

    monkeypatch.setattr(workspace_module, "load_weekly_review", fake_load)
    monkeypatch.setattr(
        workspace_module,
        "record_application_action_review",
        fake_record,
    )
    payload = _payload()

    loaded = store.get_weekly_review(owner_id="owner-a")
    recorded = store.record_application_action_review(
        owner_id="owner-a",
        application_id="application-a",
        action_id="action-a",
        payload=payload,
        expected_application_version=7,
        idempotency_key="review-v7",
    )

    assert loaded is projection
    assert recorded is mutation
    assert calls[0][0] == "load"
    assert calls[0][1] is not calls[1][1]
    assert calls[0][2] == {"owner_id": "owner-a"}
    assert calls[1][2] == {
        "owner_id": "owner-a",
        "application_id": "application-a",
        "action_id": "action-a",
        "payload": payload,
        "expected_application_version": 7,
        "idempotency_key": "review-v7",
    }


@pytest.mark.parametrize(
    "repository_error, workspace_type, code",
    [
        (
            VersionConflict("application", "application-a", 1, 2),
            WorkspaceConflict,
            "version_conflict",
        ),
        (
            MutationIdempotencyConflict("same key, changed review"),
            WorkspaceConflict,
            "idempotency_conflict",
        ),
        (MutationPending("review pending"), WorkspaceConflict, "mutation_pending"),
        (ResourceConflict("action changed"), WorkspaceConflict, "resource_conflict"),
        (ValueError("new_due_on is outside the window"), WorkspaceInputError, None),
        (
            IntegrityError("INSERT private", {}, RuntimeError("PRIVATE_ROW")),
            WorkspaceConflict,
            "resource_conflict",
        ),
        (
            OperationalError("SELECT private", {}, RuntimeError("PRIVATE_DB")),
            WorkspaceUnavailable,
            None,
        ),
    ],
)
def test_weekly_store_maps_expected_repository_failures(
    monkeypatch: pytest.MonkeyPatch,
    repository_error: Exception,
    workspace_type: type[Exception],
    code: str | None,
) -> None:
    store = _store()

    def fail(*_args, **_kwargs):
        raise repository_error

    monkeypatch.setattr(
        workspace_module,
        "record_application_action_review",
        fail,
    )
    with pytest.raises(workspace_type) as caught:
        store.record_application_action_review(
            owner_id="owner-a",
            application_id="application-a",
            action_id="action-a",
            payload=_payload(),
            expected_application_version=1,
            idempotency_key="review-once",
        )

    if code is not None:
        assert isinstance(caught.value, WorkspaceConflict)
        assert caught.value.code == code
    assert "PRIVATE" not in str(caught.value)


def test_weekly_store_sanitizes_inconsistent_saved_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()

    def corrupt(*_args, **_kwargs):
        raise WeeklyReviewRepositoryError("PRIVATE_WEEKLY_GRAPH")

    monkeypatch.setattr(workspace_module, "load_weekly_review", corrupt)
    with pytest.raises(WorkspaceUnavailable) as caught:
        store.get_weekly_review(owner_id="owner-a")

    assert str(caught.value) == "weekly-review data is inconsistent"
    assert "PRIVATE_WEEKLY_GRAPH" not in str(caught.value)
