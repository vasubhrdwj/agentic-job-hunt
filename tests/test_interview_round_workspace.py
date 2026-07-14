"""Adapter tests for interview-round repository operations and safe errors."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime

import pytest
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

import job_hunt_agent.sqlalchemy_application_workspace as workspace_module
from job_hunt_agent.interview_round_repository import InterviewRoundRepositoryError
from job_hunt_agent.interview_round_schemas import (
    ApplicationInterviewRoundsResponse,
    InterviewRoundCompletedCreate,
    InterviewRoundCreate,
    InterviewRoundMutationResponse,
)
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
from tests.test_interview_round_schemas import _application, _round


class FakeDatabase:
    @contextmanager
    def session(self):
        yield object()


def _store() -> SqlAlchemyApplicationWorkspaceStore:
    return SqlAlchemyApplicationWorkspaceStore(
        FakeDatabase(),  # type: ignore[arg-type]
        load_data_keyring(production=False),
    )


def _schedule_payload() -> InterviewRoundCreate:
    return InterviewRoundCreate(
        kind="technical",
        title="Technical interview",
        scheduled_local=datetime(2026, 7, 18, 14, 0),
        scheduled_timezone="Asia/Kolkata",
        duration_minutes=60,
        meeting_format="video",
        next_action_due_on=date(2026, 7, 18),
        confirm_schedule=True,
    )


def _complete_payload() -> InterviewRoundCompletedCreate:
    return InterviewRoundCompletedCreate(
        event_type="completed",
        completed_on=date(2026, 7, 18),
        next_action_due_on=date(2026, 7, 19),
        confirm_complete=True,
    )


def _projection() -> ApplicationInterviewRoundsResponse:
    return ApplicationInterviewRoundsResponse.model_validate(
        {"application": _application(), "rounds": [_round()]}
    )


def _mutation() -> InterviewRoundMutationResponse:
    round_payload = _round()
    return InterviewRoundMutationResponse.model_validate(
        {
            "application": _application(),
            "round": round_payload,
            "event": round_payload["events"][-1],
            "mutation_created": True,
        }
    )


def test_interview_round_store_forwards_all_three_repository_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    projection = _projection()
    mutation = _mutation()
    calls: list[tuple[str, object, dict[str, object]]] = []

    def fake_load(session, **kwargs):
        calls.append(("load", session, kwargs))
        return projection

    def fake_schedule(session, **kwargs):
        calls.append(("schedule", session, kwargs))
        return mutation

    def fake_event(session, **kwargs):
        calls.append(("event", session, kwargs))
        return mutation

    monkeypatch.setattr(workspace_module, "load_application_interview_rounds", fake_load)
    monkeypatch.setattr(workspace_module, "schedule_interview_round", fake_schedule)
    monkeypatch.setattr(workspace_module, "record_interview_round_event", fake_event)

    loaded = store.get_application_interview_rounds(
        owner_id="owner-a",
        application_id="application1",
    )
    scheduled = store.schedule_interview_round(
        owner_id="owner-a",
        application_id="application1",
        payload=_schedule_payload(),
        expected_application_version=7,
        idempotency_key="schedule-v7",
    )
    recorded = store.record_interview_round_event(
        owner_id="owner-a",
        application_id="application1",
        interview_round_id="round1",
        payload=_complete_payload(),
        expected_round_version=1,
        idempotency_key="complete-v1",
    )

    assert loaded is projection
    assert scheduled is recorded is mutation
    assert calls[0][0] == "load"
    assert calls[0][2] == {
        "owner_id": "owner-a",
        "application_id": "application1",
    }
    assert calls[1][0] == "schedule"
    assert calls[1][2] == {
        "owner_id": "owner-a",
        "application_id": "application1",
        "payload": _schedule_payload(),
        "expected_application_version": 7,
        "idempotency_key": "schedule-v7",
    }
    assert calls[2][0] == "event"
    assert calls[2][2] == {
        "owner_id": "owner-a",
        "application_id": "application1",
        "interview_round_id": "round1",
        "payload": _complete_payload(),
        "expected_round_version": 1,
        "idempotency_key": "complete-v1",
    }


@pytest.mark.parametrize(
    ("repository_error", "code"),
    [
        (VersionConflict("interview round", "round1", 1, 2), "version_conflict"),
        (ResourceConflict("round resolved"), "resource_conflict"),
        (MutationIdempotencyConflict("key reused"), "idempotency_conflict"),
        (MutationPending("request pending"), "mutation_pending"),
    ],
)
def test_interview_round_store_maps_expected_conflicts(
    monkeypatch: pytest.MonkeyPatch,
    repository_error: Exception,
    code: str,
) -> None:
    store = _store()

    def fail(*_args, **_kwargs):
        raise repository_error

    monkeypatch.setattr(workspace_module, "record_interview_round_event", fail)
    with pytest.raises(WorkspaceConflict) as caught:
        store.record_interview_round_event(
            owner_id="owner-a",
            application_id="application1",
            interview_round_id="round1",
            payload=_complete_payload(),
            expected_round_version=1,
            idempotency_key="complete-v1",
        )
    assert caught.value.code == code


def test_interview_round_store_maps_input_and_sanitizes_corruption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()

    def invalid(*_args, **_kwargs):
        raise ValueError("scheduled_local falls in a daylight-saving time gap")

    monkeypatch.setattr(workspace_module, "schedule_interview_round", invalid)
    with pytest.raises(WorkspaceInputError, match="daylight-saving"):
        store.schedule_interview_round(
            owner_id="owner-a",
            application_id="application1",
            payload=_schedule_payload(),
            expected_application_version=7,
            idempotency_key="invalid-time",
        )

    def corrupt(*_args, **_kwargs):
        raise InterviewRoundRepositoryError("PRIVATE_INTERVIEW_GRAPH")

    monkeypatch.setattr(workspace_module, "load_application_interview_rounds", corrupt)
    with pytest.raises(WorkspaceUnavailable) as caught:
        store.get_application_interview_rounds(
            owner_id="owner-a",
            application_id="application1",
        )
    assert str(caught.value) == "interview-round data is inconsistent"
    assert "PRIVATE_INTERVIEW_GRAPH" not in str(caught.value)


@pytest.mark.parametrize(
    ("repository_error", "expected_type", "expected_message"),
    [
        (
            IntegrityError("INSERT private", {}, RuntimeError("PRIVATE_ROW")),
            WorkspaceConflict,
            "interview round conflicts with existing state",
        ),
        (
            SQLAlchemyError("PRIVATE_DATABASE_HOST"),
            WorkspaceUnavailable,
            "interview-round database is unavailable",
        ),
    ],
)
def test_interview_round_store_maps_database_failures_without_leaking_details(
    monkeypatch: pytest.MonkeyPatch,
    repository_error: Exception,
    expected_type: type[Exception],
    expected_message: str,
) -> None:
    store = _store()

    def fail(*_args, **_kwargs):
        raise repository_error

    monkeypatch.setattr(workspace_module, "schedule_interview_round", fail)
    with pytest.raises(expected_type) as caught:
        store.schedule_interview_round(
            owner_id="owner-a",
            application_id="application1",
            payload=_schedule_payload(),
            expected_application_version=7,
            idempotency_key="database-failure",
        )
    assert str(caught.value) == expected_message
    assert "PRIVATE" not in str(caught.value)
