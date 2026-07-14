"""Repository coverage for practical interview scheduling and lifecycle history."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

import job_hunt_agent.application_submission_repository as submission_repository
import job_hunt_agent.interview_round_repository as repository
from job_hunt_agent.application_repository import list_today_application_actions
from job_hunt_agent.contact_search_repository import create_contact_search
from job_hunt_agent.application_submission_schemas import (
    ClosedTransitionCreate,
    OfferTransitionCreate,
)
from job_hunt_agent.database import Database
from job_hunt_agent.interview_round_repository import InterviewRoundRepositoryError
from job_hunt_agent.interview_round_schemas import (
    InterviewRoundCancelledCreate,
    InterviewRoundCompletedCreate,
    InterviewRoundCreate,
    InterviewRoundRescheduledCreate,
)
from job_hunt_agent.models import (
    ActionItem,
    Application,
    ApplicationActivityEvent,
    ApplicationInterviewRound,
    ApplicationInterviewRoundEvent,
    ApplicationSubmission,
    OwnerMutationReceipt,
)
from job_hunt_agent.mutation_receipts import MutationIdempotencyConflict
from job_hunt_agent.outreach_repository import start_outreach_sequence
from job_hunt_agent.repository_errors import ResourceConflict, VersionConflict
from job_hunt_agent.security import load_data_keyring
from tests.test_application_submission_models import NOW, submission_db


FIRST_LOCAL = datetime(2026, 7, 15, 15, 0)
RESCHEDULED_LOCAL = datetime(2026, 7, 16, 15, 0)
FIRST_START_UTC = datetime(2026, 7, 15, 9, 30, tzinfo=timezone.utc)
RESCHEDULED_START_UTC = datetime(2026, 7, 16, 9, 30, tzinfo=timezone.utc)
COMPLETE_NOW = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
CANCEL_NOW = datetime(2026, 7, 17, 8, 0, tzinfo=timezone.utc)


def _schedule_payload(
    *,
    scheduled_local: datetime = FIRST_LOCAL,
    scheduled_timezone: str = "Asia/Kolkata",
    due_on: date = date(2026, 7, 15),
    title: str = "Technical interview",
) -> InterviewRoundCreate:
    return InterviewRoundCreate(
        kind="technical",
        title=title,
        scheduled_local=scheduled_local,
        scheduled_timezone=scheduled_timezone,
        duration_minutes=60,
        meeting_format="video",
        next_action_due_on=due_on,
        confirm_schedule=True,
    )


def _reschedule_payload(
    *,
    scheduled_local: datetime = RESCHEDULED_LOCAL,
    scheduled_timezone: str = "Asia/Kolkata",
    due_on: date = date(2026, 7, 16),
) -> InterviewRoundRescheduledCreate:
    return InterviewRoundRescheduledCreate(
        event_type="rescheduled",
        scheduled_local=scheduled_local,
        scheduled_timezone=scheduled_timezone,
        duration_minutes=75,
        meeting_format="onsite",
        next_action_due_on=due_on,
        confirm_reschedule=True,
    )


def _complete_payload(
    *,
    completed_on: date = date(2026, 7, 16),
    due_on: date = date(2026, 7, 17),
) -> InterviewRoundCompletedCreate:
    return InterviewRoundCompletedCreate(
        event_type="completed",
        completed_on=completed_on,
        next_action_due_on=due_on,
        confirm_complete=True,
    )


def _cancel_payload(
    *,
    cancelled_on: date = date(2026, 7, 17),
    due_on: date = date(2026, 7, 18),
) -> InterviewRoundCancelledCreate:
    return InterviewRoundCancelledCreate(
        event_type="cancelled",
        cancelled_on=cancelled_on,
        cancelled_by="employer",
        next_action_due_on=due_on,
        confirm_cancel=True,
    )


def _schedule(
    database: Database,
    *,
    payload: InterviewRoundCreate | None = None,
    expected_version: int = 3,
    key: str = "schedule-round-1",
    now: datetime = NOW,
    owner_id: str = "owner1",
    application_id: str = "application1",
):
    with database.session() as session:
        _begin_sqlite_transaction(session)
        return repository.schedule_interview_round(
            session,
            owner_id=owner_id,
            application_id=application_id,
            payload=payload or _schedule_payload(),
            expected_application_version=expected_version,
            idempotency_key=key,
            now=now,
        )


def _record(
    database: Database,
    round_id: str,
    *,
    payload,
    expected_version: int,
    key: str,
    now: datetime,
    owner_id: str = "owner1",
    application_id: str = "application1",
):
    with database.session() as session:
        _begin_sqlite_transaction(session)
        return repository.record_interview_round_event(
            session,
            owner_id=owner_id,
            application_id=application_id,
            interview_round_id=round_id,
            payload=payload,
            expected_round_version=expected_version,
            idempotency_key=key,
            now=now,
        )


def _begin_sqlite_transaction(session) -> None:
    """Make savepoint rollback semantics match PostgreSQL in SQLite tests."""

    if session.get_bind().dialect.name == "sqlite":
        session.connection().exec_driver_sql("BEGIN")


def _counts(database: Database) -> tuple[int, int, int, int, int]:
    with database.session() as session:
        return (
            int(session.scalar(select(func.count(ApplicationInterviewRound.id))) or 0),
            int(
                session.scalar(select(func.count(ApplicationInterviewRoundEvent.id)))
                or 0
            ),
            int(session.scalar(select(func.count(ActionItem.id))) or 0),
            int(session.scalar(select(func.count(OwnerMutationReceipt.id))) or 0),
            int(
                session.scalar(select(func.count(ApplicationActivityEvent.id))) or 0
            ),
        )


def _open_action(database: Database) -> ActionItem:
    with database.session() as session:
        action = session.scalar(
            select(ActionItem).where(
                ActionItem.owner_id == "owner1",
                ActionItem.application_id == "application1",
                ActionItem.status == "open",
            )
        )
        assert action is not None
        session.expunge(action)
        return action


def test_full_schedule_reschedule_complete_then_second_cancelled_round_chain(
    submission_db: Database,
) -> None:
    scheduled = _schedule(submission_db)
    assert scheduled is not None
    round1_id = scheduled.round.id
    schedule_action_id = scheduled.event.action_item_id
    assert (
        scheduled.mutation_created,
        scheduled.application.stage.value,
        scheduled.application.version,
        scheduled.round.version,
        scheduled.round.round_number,
        scheduled.round.status.value,
        scheduled.round.scheduled_start_at,
        scheduled.event.sequence_number,
        scheduled.event.event_type.value,
        scheduled.event.previous_action_item_id,
    ) == (
        True,
        "applied",
        4,
        1,
        1,
        "scheduled",
        FIRST_START_UTC,
        1,
        "scheduled",
        "action3",
    )
    assert scheduled.application.current_action is not None
    assert scheduled.application.current_action.id == schedule_action_id
    assert scheduled.application.current_action.interview_round_id == round1_id

    rescheduled = _record(
        submission_db,
        round1_id,
        payload=_reschedule_payload(),
        expected_version=1,
        key="reschedule-round-1",
        now=NOW + timedelta(hours=1),
    )
    assert rescheduled is not None
    reschedule_action_id = rescheduled.event.action_item_id
    assert (
        rescheduled.round.version,
        rescheduled.round.scheduled_start_at,
        rescheduled.round.duration_minutes,
        rescheduled.round.meeting_format.value,
        rescheduled.event.sequence_number,
        rescheduled.event.previous_action_item_id,
    ) == (
        2,
        RESCHEDULED_START_UTC,
        75,
        "onsite",
        2,
        schedule_action_id,
    )

    completed = _record(
        submission_db,
        round1_id,
        payload=_complete_payload(),
        expected_version=2,
        key="complete-round-1",
        now=COMPLETE_NOW,
    )
    assert completed is not None
    completion_action_id = completed.event.action_item_id
    assert (
        completed.application.stage.value,
        completed.application.version,
        completed.round.version,
        completed.round.status.value,
        completed.round.completed_on,
        completed.event.previous_action_item_id,
    ) == (
        "interviewing",
        6,
        3,
        "completed",
        date(2026, 7, 16),
        reschedule_action_id,
    )
    assert completed.application.current_action is not None
    assert completed.application.current_action.id == completion_action_id
    assert completed.application.current_action.interview_round_id is None

    second_scheduled = _schedule(
        submission_db,
        payload=_schedule_payload(
            scheduled_local=datetime(2026, 7, 18, 15, 0),
            due_on=date(2026, 7, 18),
            title="System design interview",
        ),
        expected_version=6,
        key="schedule-round-2",
        now=COMPLETE_NOW + timedelta(minutes=5),
    )
    assert second_scheduled is not None
    round2_id = second_scheduled.round.id
    second_schedule_action_id = second_scheduled.event.action_item_id
    assert (
        second_scheduled.round.round_number,
        second_scheduled.round.version,
        second_scheduled.application.version,
        second_scheduled.event.previous_action_item_id,
    ) == (2, 1, 7, completion_action_id)

    cancelled = _record(
        submission_db,
        round2_id,
        payload=_cancel_payload(),
        expected_version=1,
        key="cancel-round-2",
        now=CANCEL_NOW,
    )
    assert cancelled is not None
    final_action_id = cancelled.event.action_item_id
    assert (
        cancelled.application.stage.value,
        cancelled.application.version,
        cancelled.round.version,
        cancelled.round.status.value,
        cancelled.round.cancelled_on,
        cancelled.round.cancelled_by.value,
        cancelled.event.previous_action_item_id,
    ) == (
        "interviewing",
        8,
        2,
        "cancelled",
        date(2026, 7, 17),
        "employer",
        second_schedule_action_id,
    )
    assert cancelled.application.current_action is not None
    assert cancelled.application.current_action.id == final_action_id
    assert cancelled.application.current_action.interview_round_id is None

    with submission_db.session() as session:
        actions = {
            action.id: action
            for action in session.scalars(
                select(ActionItem).where(ActionItem.application_id == "application1")
            )
        }
        assert actions["action3"].status == "completed"
        assert actions[schedule_action_id].status == "cancelled"
        assert actions[reschedule_action_id].status == "completed"
        assert actions[completion_action_id].status == "completed"
        assert actions[second_schedule_action_id].status == "cancelled"
        assert actions[final_action_id].status == "open"
        assert actions[final_action_id].interview_round_id is None
        assert actions[final_action_id].kind == "prepare_interview"

        activity = session.scalar(
            select(ApplicationActivityEvent).where(
                ApplicationActivityEvent.event_type == "application_interviewing"
            )
        )
        assert activity is not None
        assert (
            activity.sequence_number,
            activity.from_stage,
            activity.to_stage,
            activity.previous_action_item_id,
            activity.action_item_id,
            activity.interview_round_id,
            activity.effective_on,
        ) == (
            4,
            "applied",
            "interviewing",
            reschedule_action_id,
            completion_action_id,
            round1_id,
            date(2026, 7, 16),
        )

        projection = repository.load_application_interview_rounds(
            session,
            owner_id="owner1",
            application_id="application1",
        )
        assert projection is not None
        assert [round_.round_number for round_ in projection.rounds] == [1, 2]
        assert [round_.status.value for round_ in projection.rounds] == [
            "completed",
            "cancelled",
        ]
        assert [
            [event.event_type.value for event in round_.events]
            for round_ in projection.rounds
        ] == [["scheduled", "rescheduled", "completed"], ["scheduled", "cancelled"]]

        today = list_today_application_actions(session, "owner1", now=CANCEL_NOW)
        assert today.overdue.total == today.today.total == 0
        assert today.next_7_days.total == 1
        assert today.next_7_days.items[0].action.id == final_action_id
        assert today.next_7_days.items[0].action.interview_round_id is None


def test_schedule_and_event_replays_are_exact_and_changed_payloads_conflict(
    submission_db: Database,
) -> None:
    payload = _schedule_payload()
    scheduled = _schedule(submission_db, payload=payload, key="schedule-replay")
    assert scheduled is not None
    replay = _schedule(
        submission_db,
        payload=payload,
        expected_version=3,
        key="schedule-replay",
        now=NOW + timedelta(minutes=1),
    )
    assert replay is not None
    assert replay.mutation_created is False
    assert replay.model_copy(update={"mutation_created": True}) == scheduled

    with pytest.raises(MutationIdempotencyConflict, match="different"):
        _schedule(
            submission_db,
            payload=_schedule_payload(title="Changed title"),
            expected_version=3,
            key="schedule-replay",
            now=NOW + timedelta(minutes=2),
        )

    round_id = scheduled.round.id
    event_payload = _reschedule_payload()
    rescheduled = _record(
        submission_db,
        round_id,
        payload=event_payload,
        expected_version=1,
        key="event-replay",
        now=NOW + timedelta(hours=1),
    )
    assert rescheduled is not None
    event_replay = _record(
        submission_db,
        round_id,
        payload=event_payload,
        expected_version=1,
        key="event-replay",
        now=NOW + timedelta(hours=1, minutes=1),
    )
    assert event_replay is not None
    assert event_replay.mutation_created is False
    assert event_replay.model_copy(update={"mutation_created": True}) == rescheduled

    with pytest.raises(MutationIdempotencyConflict, match="different"):
        _record(
            submission_db,
            round_id,
            payload=_reschedule_payload(due_on=date(2026, 7, 15)),
            expected_version=1,
            key="event-replay",
            now=NOW + timedelta(hours=1, minutes=2),
        )

    completed = _record(
        submission_db,
        round_id,
        payload=_complete_payload(),
        expected_version=2,
        key="complete-after-replay",
        now=COMPLETE_NOW,
    )
    assert completed is not None
    with pytest.raises(ResourceConflict, match="changed"):
        _record(
            submission_db,
            round_id,
            payload=event_payload,
            expected_version=1,
            key="event-replay",
            now=COMPLETE_NOW + timedelta(minutes=1),
        )
    assert _counts(submission_db) == (1, 3, 6, 3, 4)


def test_stale_versions_and_a_second_scheduled_round_roll_back_cleanly(
    submission_db: Database,
) -> None:
    assert _counts(submission_db) == (0, 0, 3, 0, 3)
    with pytest.raises(VersionConflict):
        _schedule(submission_db, expected_version=2, key="stale-schedule")
    assert _counts(submission_db) == (0, 0, 3, 0, 3)

    scheduled = _schedule(submission_db)
    assert scheduled is not None
    stable_counts = _counts(submission_db)
    with pytest.raises(VersionConflict):
        _record(
            submission_db,
            scheduled.round.id,
            payload=_reschedule_payload(),
            expected_version=2,
            key="stale-event",
            now=NOW + timedelta(hours=1),
        )
    assert _counts(submission_db) == stable_counts

    with pytest.raises(ResourceConflict, match="complete or cancel"):
        _schedule(
            submission_db,
            expected_version=4,
            key="second-active-round",
            now=NOW + timedelta(hours=1),
        )
    assert _counts(submission_db) == stable_counts


def test_reads_and_mutations_are_owner_and_resource_isolated(
    submission_db: Database,
) -> None:
    with submission_db.session() as session:
        initial = repository.load_application_interview_rounds(
            session,
            owner_id="owner1",
            application_id="application1",
        )
        missing = repository.load_application_interview_rounds(
            session,
            owner_id="otherowner",
            application_id="application1",
        )
    assert initial is not None and initial.rounds == []
    assert missing is None
    assert _schedule(submission_db, owner_id="otherowner") is None

    scheduled = _schedule(submission_db)
    assert scheduled is not None
    assert (
        _record(
            submission_db,
            scheduled.round.id,
            payload=_reschedule_payload(),
            expected_version=1,
            key="wrong-owner",
            now=NOW + timedelta(hours=1),
            owner_id="otherowner",
        )
        is None
    )
    assert (
        _record(
            submission_db,
            "missinground",
            payload=_reschedule_payload(),
            expected_version=1,
            key="wrong-round",
            now=NOW + timedelta(hours=1),
        )
        is None
    )
    assert _counts(submission_db) == (1, 1, 4, 1, 3)


def test_invalid_stage_missing_submission_and_missing_current_action_are_rejected(
    submission_db: Database,
) -> None:
    with submission_db.session() as session:
        application = session.get(Application, "application1")
        assert application is not None
        application.stage = "offer"
    with pytest.raises(ResourceConflict, match="applied, screening, or interviewing"):
        _schedule(submission_db, key="invalid-stage")
    assert _counts(submission_db) == (0, 0, 3, 0, 3)


def test_missing_submission_is_an_inconsistent_graph_without_partial_writes(
    submission_db: Database,
) -> None:
    with submission_db.session() as session:
        applied_event = session.get(ApplicationActivityEvent, "activity3")
        submission = session.get(ApplicationSubmission, "submission1")
        assert applied_event is not None and submission is not None
        session.delete(applied_event)
        session.flush()
        session.delete(submission)
    before = _counts(submission_db)
    with pytest.raises(InterviewRoundRepositoryError, match="exact application submission"):
        _schedule(submission_db, key="missing-submission")
    assert _counts(submission_db) == before


def test_missing_or_wrong_current_action_is_rejected_without_partial_writes(
    submission_db: Database,
) -> None:
    with submission_db.session() as session:
        action = session.get(ActionItem, "action3")
        assert action is not None
        action.status = "completed"
        action.completed_at = NOW + timedelta(minutes=1)
        action.version += 1
        action.updated_at = NOW + timedelta(minutes=1)
    before = _counts(submission_db)
    with pytest.raises(InterviewRoundRepositoryError, match="exactly one current action"):
        _schedule(submission_db, key="missing-action")
    assert _counts(submission_db) == before


def test_wrong_default_current_action_is_not_silently_replaced(
    submission_db: Database,
) -> None:
    with submission_db.session() as session:
        action = session.get(ActionItem, "action3")
        assert action is not None
        action.kind = "prepare_interview"
        action.title = "Corrupted action"
    before = _counts(submission_db)
    with pytest.raises(InterviewRoundRepositoryError, match="current action"):
        _schedule(submission_db, key="wrong-action-kind")
    assert _counts(submission_db) == before


def test_round_event_rejects_a_scheduled_round_that_lost_its_action_link(
    submission_db: Database,
) -> None:
    scheduled = _schedule(submission_db)
    assert scheduled is not None
    with submission_db.session() as session:
        action = session.get(ActionItem, scheduled.event.action_item_id)
        assert action is not None
        action.interview_round_id = None
    before = _counts(submission_db)
    with pytest.raises(InterviewRoundRepositoryError, match="does not own"):
        _record(
            submission_db,
            scheduled.round.id,
            payload=_reschedule_payload(),
            expected_version=1,
            key="orphaned-round-action",
            now=NOW + timedelta(hours=1),
        )
    assert _counts(submission_db) == before


@pytest.mark.parametrize(
    ("scheduled_local", "timezone_name", "due_on", "message"),
    [
        (datetime(2026, 7, 14, 10, 0), "Asia/Kolkata", date(2026, 7, 14), "future"),
        (
            datetime(2026, 3, 8, 2, 30),
            "America/New_York",
            date(2026, 7, 14),
            "gap",
        ),
        (
            datetime(2026, 11, 1, 1, 30),
            "America/New_York",
            date(2026, 7, 14),
            "ambiguous",
        ),
        (
            datetime(2027, 7, 15, 15, 0),
            "Asia/Kolkata",
            date(2027, 7, 14),
            "365",
        ),
        (FIRST_LOCAL, "Asia/Kolkata", date(2026, 7, 13), "today"),
        (FIRST_LOCAL, "Asia/Kolkata", date(2026, 7, 16), "appointment date"),
    ],
)
def test_invalid_appointments_and_due_dates_leave_no_partial_graph(
    submission_db: Database,
    scheduled_local: datetime,
    timezone_name: str,
    due_on: date,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _schedule(
            submission_db,
            payload=_schedule_payload(
                scheduled_local=scheduled_local,
                scheduled_timezone=timezone_name,
                due_on=due_on,
            ),
            key=f"invalid-appointment-{message}",
        )
    assert _counts(submission_db) == (0, 0, 3, 0, 3)
    assert _open_action(submission_db).id == "action3"


def test_exact_365_day_owner_local_appointment_boundary_is_allowed(
    submission_db: Database,
) -> None:
    scheduled = _schedule(
        submission_db,
        payload=_schedule_payload(
            scheduled_local=datetime(2027, 7, 14, 15, 0),
            due_on=date(2027, 7, 14),
        ),
        key="appointment-day-365",
    )
    assert scheduled is not None
    assert scheduled.round.scheduled_start_at == datetime(
        2027, 7, 14, 9, 30, tzinfo=timezone.utc
    )


@pytest.mark.parametrize(
    ("recorded_at", "completed_on", "due_on", "message"),
    [
        (
            datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc),
            date(2026, 7, 15),
            date(2026, 7, 15),
            "before it starts",
        ),
        (
            datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc),
            date(2026, 7, 14),
            date(2026, 7, 15),
            "scheduled record",
        ),
        (
            datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc),
            date(2026, 7, 16),
            date(2026, 7, 15),
            "future",
        ),
        (
            datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc),
            date(2026, 7, 15),
            date(2026, 7, 14),
            "next_action_due_on",
        ),
        (
            datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc),
            date(2026, 7, 15),
            date(2027, 7, 16),
            "next_action_due_on",
        ),
    ],
)
def test_invalid_completion_time_dates_and_due_dates_roll_back(
    submission_db: Database,
    recorded_at: datetime,
    completed_on: date,
    due_on: date,
    message: str,
) -> None:
    scheduled = _schedule(submission_db)
    assert scheduled is not None
    before = _counts(submission_db)
    with pytest.raises(ValueError, match=message):
        _record(
            submission_db,
            scheduled.round.id,
            payload=_complete_payload(completed_on=completed_on, due_on=due_on),
            expected_version=1,
            key=f"invalid-completion-{message}",
            now=recorded_at,
        )
    assert _counts(submission_db) == before
    assert _open_action(submission_db).id == scheduled.event.action_item_id


@pytest.mark.parametrize(
    ("cancelled_on", "due_on", "message"),
    [
        (date(2026, 7, 13), date(2026, 7, 14), "scheduled record"),
        (date(2026, 7, 15), date(2026, 7, 14), "future"),
        (date(2026, 7, 14), date(2026, 7, 13), "next_action_due_on"),
        (date(2026, 7, 14), date(2027, 7, 15), "next_action_due_on"),
    ],
)
def test_invalid_cancellation_and_due_dates_roll_back(
    submission_db: Database,
    cancelled_on: date,
    due_on: date,
    message: str,
) -> None:
    scheduled = _schedule(submission_db)
    assert scheduled is not None
    before = _counts(submission_db)
    with pytest.raises(ValueError, match=message):
        _record(
            submission_db,
            scheduled.round.id,
            payload=_cancel_payload(cancelled_on=cancelled_on, due_on=due_on),
            expected_version=1,
            key=f"invalid-cancellation-{message}",
            now=NOW + timedelta(hours=1),
        )
    assert _counts(submission_db) == before


def test_same_raw_event_key_can_be_reused_for_a_later_round_namespace(
    submission_db: Database,
) -> None:
    first = _schedule(submission_db)
    assert first is not None
    first_cancelled = _record(
        submission_db,
        first.round.id,
        payload=_cancel_payload(
            cancelled_on=date(2026, 7, 14),
            due_on=date(2026, 7, 15),
        ),
        expected_version=1,
        key="shared-event-key",
        now=NOW + timedelta(hours=1),
    )
    assert first_cancelled is not None
    second = _schedule(
        submission_db,
        payload=_schedule_payload(
            scheduled_local=datetime(2026, 7, 16, 15, 0),
            due_on=date(2026, 7, 16),
        ),
        expected_version=5,
        key="schedule-round-2",
        now=NOW + timedelta(hours=2),
    )
    assert second is not None
    second_cancelled = _record(
        submission_db,
        second.round.id,
        payload=_cancel_payload(
            cancelled_on=date(2026, 7, 14),
            due_on=date(2026, 7, 15),
        ),
        expected_version=1,
        key="shared-event-key",
        now=NOW + timedelta(hours=3),
    )
    assert second_cancelled is not None
    assert second_cancelled.round.status.value == "cancelled"


def test_same_raw_key_can_be_reused_across_schedule_and_event_namespaces(
    submission_db: Database,
) -> None:
    shared_key = "shared-cross-endpoint-key"
    scheduled = _schedule(submission_db, key=shared_key)
    assert scheduled is not None

    completed = _record(
        submission_db,
        scheduled.round.id,
        payload=_complete_payload(),
        expected_version=1,
        key=shared_key,
        now=COMPLETE_NOW,
    )

    assert completed is not None
    assert completed.round.status.value == "completed"


def test_cancellation_cannot_predate_the_latest_reschedule_event(
    submission_db: Database,
) -> None:
    scheduled = _schedule(submission_db)
    assert scheduled is not None
    rescheduled = _record(
        submission_db,
        scheduled.round.id,
        payload=_reschedule_payload(),
        expected_version=1,
        key="reschedule-before-cancel",
        now=NOW + timedelta(days=1),
    )
    assert rescheduled is not None
    before = _counts(submission_db)

    with pytest.raises(ValueError, match="scheduled record"):
        _record(
            submission_db,
            scheduled.round.id,
            payload=_cancel_payload(
                cancelled_on=date(2026, 7, 14),
                due_on=date(2026, 7, 15),
            ),
            expected_version=2,
            key="backdated-cancel-after-reschedule",
            now=NOW + timedelta(days=1, hours=1),
        )

    assert _counts(submission_db) == before


def test_offer_and_close_transitions_wait_for_the_scheduled_round_resolution(
    submission_db: Database,
) -> None:
    scheduled = _schedule(submission_db)
    assert scheduled is not None
    keyring = load_data_keyring(production=False)
    transitions = [
        OfferTransitionCreate(
            to_stage="offer",
            received_on=date(2026, 7, 14),
            next_action_due_on=date(2026, 7, 15),
            confirm_offer=True,
        ),
        ClosedTransitionCreate(
            to_stage="closed",
            outcome="withdrawn",
            outcome_on=date(2026, 7, 14),
            confirm_close=True,
        ),
    ]
    for index, payload in enumerate(transitions):
        with pytest.raises(ResourceConflict, match="scheduled interview round"):
            with submission_db.session() as session:
                _begin_sqlite_transaction(session)
                submission_repository.transition_application(
                    session,
                    owner_id="owner1",
                    application_id="application1",
                    payload=payload,
                    expected_application_version=4,
                    idempotency_key=f"blocked-stage-{index}",
                    keyring=keyring,
                    now=NOW + timedelta(hours=index + 1),
                )

    with submission_db.session() as session:
        application = session.get(Application, "application1")
        assert application is not None
        assert (application.stage, application.version) == ("applied", 4)
        current = session.get(ActionItem, scheduled.event.action_item_id)
        assert current is not None
        assert (current.status, current.interview_round_id) == (
            "open",
            scheduled.round.id,
        )
    assert _counts(submission_db) == (1, 1, 4, 1, 3)


def test_recorded_round_stops_new_contact_discovery_and_outreach(
    submission_db: Database,
) -> None:
    scheduled = _schedule(submission_db)
    assert scheduled is not None

    with pytest.raises(ResourceConflict, match="contact discovery stops"):
        with submission_db.session() as session:
            _begin_sqlite_transaction(session)
            create_contact_search(
                session,
                owner_id="owner1",
                application_id="application1",
                expected_application_version=4,
                idempotency_key="search-after-interview-round",
                now=NOW + timedelta(minutes=1),
            )

    with pytest.raises(ResourceConflict, match="outreach stops"):
        with submission_db.session() as session:
            _begin_sqlite_transaction(session)
            start_outreach_sequence(
                session,
                owner_id="owner1",
                application_id="application1",
                expected_application_version=4,
                idempotency_key="outreach-after-interview-round",
                keyring=load_data_keyring(production=False),
                now=NOW + timedelta(minutes=2),
            )

    assert _counts(submission_db) == (1, 1, 4, 1, 3)
