"""Database invariants for owner-scoped interview rounds and events."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    ActionItem,
    Application,
    ApplicationInterviewRound,
    ApplicationInterviewRoundEvent,
)
from tests.test_application_submission_models import NOW, submission_db


SCHEDULED_AT = NOW + timedelta(days=2)


def _round(
    round_id: str = "round1",
    *,
    owner_id: str = "owner1",
    application_id: str = "application1",
    submission_id: str = "submission1",
    round_number: int = 1,
    status: str = "scheduled",
    completed_on: date | None = None,
    cancelled_on: date | None = None,
    cancelled_by: str | None = None,
) -> ApplicationInterviewRound:
    return ApplicationInterviewRound(
        id=round_id,
        owner_id=owner_id,
        application_id=application_id,
        application_submission_id=submission_id,
        round_number=round_number,
        kind="technical",
        title="Technical interview",
        status=status,
        scheduled_start_at=SCHEDULED_AT,
        scheduled_timezone="Asia/Kolkata",
        duration_minutes=60,
        meeting_format="video",
        completed_on=completed_on,
        cancelled_on=cancelled_on,
        cancelled_by=cancelled_by,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _event(
    event_id: str = "roundevent1",
    *,
    round_id: str = "round1",
    owner_id: str = "owner1",
    application_id: str = "application1",
    sequence: int = 1,
    event_type: str = "scheduled",
    previous_action_id: str = "action1",
    action_id: str = "action3",
    effective_on: date | None = None,
    cancelled_by: str | None = None,
    mutation_hash: str = "a" * 64,
) -> ApplicationInterviewRoundEvent:
    terminal = event_type in {"completed", "cancelled"}
    return ApplicationInterviewRoundEvent(
        id=event_id,
        owner_id=owner_id,
        application_id=application_id,
        interview_round_id=round_id,
        sequence_number=sequence,
        event_type=event_type,
        from_status=None if event_type == "scheduled" else "scheduled",
        to_status=event_type if terminal else "scheduled",
        scheduled_start_at=SCHEDULED_AT,
        scheduled_timezone="Asia/Kolkata",
        duration_minutes=60,
        meeting_format="video",
        effective_on=effective_on,
        cancelled_by=cancelled_by,
        previous_action_item_id=previous_action_id,
        action_item_id=action_id,
        recording_method="manual",
        idempotency_key_hash=mutation_hash,
        occurred_at=NOW + timedelta(hours=sequence),
        created_at=NOW + timedelta(hours=sequence),
    )


def test_round_and_event_persist_one_exact_owner_scoped_submission_graph(
    submission_db: Database,
) -> None:
    with submission_db.session() as session:
        session.add(_round())
        session.flush()
        session.add(_event())

    with submission_db.session() as session:
        round_ = session.get(ApplicationInterviewRound, "round1")
        event = session.get(ApplicationInterviewRoundEvent, "roundevent1")
        assert round_ is not None and event is not None
        assert round_.application_submission_id == "submission1"
        assert (event.interview_round_id, event.sequence_number) == ("round1", 1)
        assert event.previous_action_item_id == "action1"
        assert event.action_item_id == "action3"
        assert "updated_at" not in ApplicationInterviewRoundEvent.__table__.columns


@pytest.mark.parametrize(
    "values",
    [
        {"owner_id": "missingowner"},
        {"application_id": "missingapplication"},
        {"submission_id": "missingsubmission"},
    ],
)
def test_round_requires_exact_owner_application_and_submission_foreign_keys(
    submission_db: Database,
    values: dict[str, str],
) -> None:
    with pytest.raises(IntegrityError):
        with submission_db.session() as session:
            session.add(_round(**values))


def test_only_one_scheduled_round_and_one_round_number_are_allowed(
    submission_db: Database,
) -> None:
    with submission_db.session() as session:
        session.add(_round())

    with pytest.raises(IntegrityError):
        with submission_db.session() as session:
            session.add(_round("round2", round_number=2))

    with submission_db.session() as session:
        first = session.get(ApplicationInterviewRound, "round1")
        assert first is not None
        first.status = "completed"
        first.completed_on = date(2026, 7, 18)
        first.version = 2
        first.updated_at = NOW + timedelta(days=2)
        session.add(_round("round2", round_number=2))

    with pytest.raises(IntegrityError):
        with submission_db.session() as session:
            session.add(
                _round(
                    "round3",
                    round_number=2,
                    status="cancelled",
                    cancelled_on=date(2026, 7, 18),
                    cancelled_by="employer",
                )
            )


@pytest.mark.parametrize(
    "values",
    [
        {"status": "completed"},
        {"status": "cancelled", "cancelled_on": date(2026, 7, 18)},
        {"status": "scheduled", "completed_on": date(2026, 7, 18)},
        {
            "status": "cancelled",
            "completed_on": date(2026, 7, 18),
            "cancelled_on": date(2026, 7, 18),
            "cancelled_by": "employer",
        },
    ],
)
def test_round_status_shape_is_enforced(
    submission_db: Database,
    values: dict[str, object],
) -> None:
    with pytest.raises(IntegrityError):
        with submission_db.session() as session:
            session.add(_round(**values))


@pytest.mark.parametrize(
    "values",
    [
        {"event_type": "scheduled", "sequence": 2},
        {"event_type": "rescheduled", "sequence": 1},
        {"event_type": "completed", "sequence": 2},
        {
            "event_type": "completed",
            "sequence": 2,
            "effective_on": date(2026, 7, 18),
            "cancelled_by": "employer",
        },
        {
            "event_type": "cancelled",
            "sequence": 2,
            "effective_on": date(2026, 7, 18),
        },
        {"previous_action_id": "action3", "action_id": "action3"},
        {"mutation_hash": "short"},
    ],
)
def test_event_lifecycle_action_replacement_and_hash_shape_are_enforced(
    submission_db: Database,
    values: dict[str, object],
) -> None:
    with submission_db.session() as session:
        session.add(_round())
    with pytest.raises(IntegrityError):
        with submission_db.session() as session:
            session.add(_event(**values))


@pytest.mark.parametrize(
    "values",
    [
        {"round_id": "missinground"},
        {"previous_action_id": "missingaction"},
        {"action_id": "missingaction"},
        {"owner_id": "missingowner"},
        {"application_id": "missingapplication"},
    ],
)
def test_event_requires_exact_round_owner_application_and_actions(
    submission_db: Database,
    values: dict[str, str],
) -> None:
    with submission_db.session() as session:
        session.add(_round())
    with pytest.raises(IntegrityError):
        with submission_db.session() as session:
            session.add(_event(**values))


def test_event_sequence_application_hash_and_terminal_event_are_unique(
    submission_db: Database,
) -> None:
    with submission_db.session() as session:
        session.add(_round())
        session.flush()
        session.add(_event())

    with pytest.raises(IntegrityError):
        with submission_db.session() as session:
            session.add(_event("duplicatesequence", mutation_hash="b" * 64))

    with pytest.raises(IntegrityError):
        with submission_db.session() as session:
            session.add(_event("duplicatehash", sequence=2))

    with submission_db.session() as session:
        session.add(
            _event(
                "completedevent",
                sequence=2,
                event_type="completed",
                previous_action_id="action3",
                action_id="action1",
                effective_on=date(2026, 7, 18),
                mutation_hash="c" * 64,
            )
        )

    with pytest.raises(IntegrityError):
        with submission_db.session() as session:
            session.add(
                _event(
                    "cancelledevent",
                    sequence=3,
                    event_type="cancelled",
                    previous_action_id="action1",
                    action_id="action3",
                    effective_on=date(2026, 7, 18),
                    cancelled_by="employer",
                    mutation_hash="d" * 64,
                )
            )


def test_round_linked_actions_must_prepare_for_the_exact_round(
    submission_db: Database,
) -> None:
    with submission_db.session() as session:
        session.add(_round())

    with pytest.raises(IntegrityError):
        with submission_db.session() as session:
            action = session.get(ActionItem, "action3")
            assert action is not None
            action.interview_round_id = "round1"

    with submission_db.session() as session:
        action = session.get(ActionItem, "action3")
        assert action is not None
        action.kind = "prepare_interview"
        action.interview_round_id = "round1"

    with submission_db.session() as session:
        action = session.get(ActionItem, "action3")
        assert action is not None
        assert (action.kind, action.interview_round_id) == (
            "prepare_interview",
            "round1",
        )

    with pytest.raises(IntegrityError):
        with submission_db.session() as session:
            action = session.get(ActionItem, "action3")
            assert action is not None
            action.interview_round_id = "missinground"


def test_application_delete_cascades_rounds_and_events(
    submission_db: Database,
) -> None:
    with submission_db.session() as session:
        session.add(_round())
        session.flush()
        session.add(_event())

    with submission_db.session() as session:
        application = session.get(Application, "application1")
        assert application is not None
        session.delete(application)

    with submission_db.session() as session:
        assert session.scalar(select(func.count(ApplicationInterviewRound.id))) == 0
        assert (
            session.scalar(select(func.count(ApplicationInterviewRoundEvent.id))) == 0
        )
