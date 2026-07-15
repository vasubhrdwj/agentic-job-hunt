"""Repository safety coverage for append-only milestone corrections."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

import job_hunt_agent.application_correction_repository as repository
import job_hunt_agent.interview_round_repository as interview_repository
from job_hunt_agent.application_schemas import ApplicationMilestoneCorrectionCreate
from job_hunt_agent.database import Database
from job_hunt_agent.interview_round_schemas import (
    InterviewRoundCompletedCreate,
    InterviewRoundCreate,
)
from job_hunt_agent.models import (
    ActionItem,
    Application,
    ApplicationActivityEvent,
    ApplicationInterviewRound,
    ApplicationMilestoneCorrection,
    ApplicationOutcome,
    ApplicationSubmission,
    OwnerMutationReceipt,
)
from job_hunt_agent.mutation_receipts import MutationIdempotencyConflict
from job_hunt_agent.repository_errors import ResourceConflict, VersionConflict
from tests.test_application_progress_repository import (
    _closed,
    _interviewing,
    _screening,
    _transition,
)
from tests.test_application_submission_models import NOW, submission_db


def _event_id(database: Database, event_type: str) -> str:
    with database.session() as session:
        value = session.scalar(
            select(ApplicationActivityEvent.id).where(
                ApplicationActivityEvent.owner_id == "owner1",
                ApplicationActivityEvent.application_id == "application1",
                ApplicationActivityEvent.event_type == event_type,
            )
        )
        assert value is not None
        return value


def _correct(
    database: Database,
    *,
    event_id: str,
    corrected_on: date,
    expected_version: int,
    key: str,
    now: datetime,
    owner_id: str = "owner1",
):
    with database.session() as session:
        return repository.record_application_milestone_correction(
            session,
            owner_id=owner_id,
            application_id="application1",
            activity_event_id=event_id,
            payload=ApplicationMilestoneCorrectionCreate(
                corrected_effective_on=corrected_on,
                confirm_correction=True,
            ),
            expected_application_version=expected_version,
            idempotency_key=key,
            now=now,
        )


def test_first_and_repeated_corrections_preserve_the_application_graph(
    submission_db: Database,
) -> None:
    screening = _transition(
        submission_db,
        payload=_screening(),
        expected_version=3,
        idempotency_key="screen-before-corrections",
        now=NOW + timedelta(days=1),
    )
    assert screening is not None
    event_id = screening.activity_event.id
    action_id = screening.application.current_action.id  # type: ignore[union-attr]

    first = _correct(
        submission_db,
        event_id=event_id,
        corrected_on=date(2026, 7, 14),
        expected_version=4,
        key="correct-screen-first",
        now=NOW + timedelta(days=1, hours=1),
    )
    assert first is not None
    second = _correct(
        submission_db,
        event_id=event_id,
        corrected_on=date(2026, 7, 15),
        expected_version=5,
        key="correct-screen-second",
        now=NOW + timedelta(days=1, hours=2),
    )
    assert second is not None

    assert first.correction_created is True
    assert first.activity_event.effective_on == date(2026, 7, 15)
    assert first.activity_event.resolved_effective_on == date(2026, 7, 14)
    assert second.activity_event.effective_on == date(2026, 7, 15)
    assert second.activity_event.resolved_effective_on == date(2026, 7, 15)
    assert [item.correction_number for item in second.activity_event.corrections] == [
        1,
        2,
    ]
    assert second.activity_event.corrections[1].supersedes_correction_id == (
        first.correction.id
    )

    with submission_db.session() as session:
        application = session.get(Application, "application1")
        activity = session.get(ApplicationActivityEvent, event_id)
        action = session.get(ActionItem, action_id)
        submission = session.get(ApplicationSubmission, "submission1")
        assert application is not None
        assert activity is not None
        assert action is not None
        assert submission is not None
        assert (application.stage, application.version, application.outcome_id) == (
            "screening",
            6,
            None,
        )
        assert (action.status, action.kind) == ("open", "prepare_recruiter_screen")
        assert activity.effective_on == date(2026, 7, 15)
        assert submission.applied_on == date(2026, 7, 14)
        assert session.scalar(select(func.count(ApplicationOutcome.id))) == 0
        assert session.scalar(
            select(func.count(ApplicationMilestoneCorrection.id))
        ) == 2


def test_correction_replay_changed_request_and_stale_version_are_safe(
    submission_db: Database,
) -> None:
    screening = _transition(
        submission_db,
        payload=_screening(),
        expected_version=3,
        idempotency_key="screen-before-replay",
        now=NOW + timedelta(days=1),
    )
    assert screening is not None
    event_id = screening.activity_event.id
    current = NOW + timedelta(days=1, hours=1)
    created = _correct(
        submission_db,
        event_id=event_id,
        corrected_on=date(2026, 7, 14),
        expected_version=4,
        key="stable-correction-key",
        now=current,
    )
    replay = _correct(
        submission_db,
        event_id=event_id,
        corrected_on=date(2026, 7, 14),
        expected_version=4,
        key="stable-correction-key",
        now=current + timedelta(minutes=1),
    )

    assert created is not None and replay is not None
    assert replay.correction_created is False
    assert replay.correction == created.correction
    assert replay.application.version == 5

    with pytest.raises(MutationIdempotencyConflict):
        _correct(
            submission_db,
            event_id=event_id,
            corrected_on=date(2026, 7, 15),
            expected_version=4,
            key="stable-correction-key",
            now=current + timedelta(minutes=2),
        )
    with pytest.raises(VersionConflict):
        _correct(
            submission_db,
            event_id=event_id,
            corrected_on=date(2026, 7, 15),
            expected_version=4,
            key="stale-correction-version",
            now=current + timedelta(minutes=3),
        )

    with submission_db.session() as session:
        assert session.scalar(
            select(func.count(ApplicationMilestoneCorrection.id))
        ) == 1


def test_cross_owner_correction_is_not_found_and_creates_no_receipt(
    submission_db: Database,
) -> None:
    screening = _transition(
        submission_db,
        payload=_screening(),
        expected_version=3,
        idempotency_key="screen-before-owner-check",
        now=NOW + timedelta(days=1),
    )
    assert screening is not None
    with submission_db.session() as session:
        before = int(session.scalar(select(func.count(OwnerMutationReceipt.id))) or 0)

    result = _correct(
        submission_db,
        owner_id="owner2",
        event_id=screening.activity_event.id,
        corrected_on=date(2026, 7, 14),
        expected_version=4,
        key="cross-owner-correction",
        now=NOW + timedelta(days=1, hours=1),
    )

    assert result is None
    with submission_db.session() as session:
        assert session.scalar(
            select(func.count(ApplicationMilestoneCorrection.id))
        ) == 0
        assert session.scalar(select(func.count(OwnerMutationReceipt.id))) == before


def test_invalid_dates_and_non_milestone_target_leave_history_unchanged(
    submission_db: Database,
) -> None:
    screening = _transition(
        submission_db,
        payload=_screening(),
        expected_version=3,
        idempotency_key="screen-before-invalid-corrections",
        now=NOW + timedelta(days=1),
    )
    assert screening is not None
    event_id = screening.activity_event.id

    for corrected_on in (date(2026, 7, 13), date(2026, 7, 16)):
        with pytest.raises(ValueError, match="between"):
            _correct(
                submission_db,
                event_id=event_id,
                corrected_on=corrected_on,
                expected_version=4,
                key=f"invalid-bound-{corrected_on.isoformat()}",
                now=NOW + timedelta(days=1, hours=1),
            )
    applied_event_id = _event_id(submission_db, "application_applied")
    with pytest.raises(ResourceConflict, match="manually recorded"):
        _correct(
            submission_db,
            event_id=applied_event_id,
            corrected_on=date(2026, 7, 14),
            expected_version=4,
            key="invalid-applied-target",
            now=NOW + timedelta(days=1, hours=1),
        )

    with submission_db.session() as session:
        application = session.get(Application, "application1")
        assert application is not None and application.version == 4
        assert session.scalar(
            select(func.count(ApplicationMilestoneCorrection.id))
        ) == 0


def test_correction_respects_a_later_milestone_upper_bound(
    submission_db: Database,
) -> None:
    screening = _transition(
        submission_db,
        payload=_screening(),
        expected_version=3,
        idempotency_key="screen-before-successor",
        now=NOW + timedelta(days=1),
    )
    assert screening is not None
    interviewing = _transition(
        submission_db,
        payload=_interviewing(),
        expected_version=4,
        idempotency_key="interview-before-invalid-screen-date",
        now=NOW + timedelta(days=2),
    )
    assert interviewing is not None

    with pytest.raises(ValueError, match="between"):
        _correct(
            submission_db,
            event_id=screening.activity_event.id,
            corrected_on=date(2026, 7, 17),
            expected_version=5,
            key="screen-after-interview",
            now=NOW + timedelta(days=3),
        )


def test_closed_application_allows_nonterminal_correction_without_reopening(
    submission_db: Database,
) -> None:
    screening = _transition(
        submission_db,
        payload=_screening(),
        expected_version=3,
        idempotency_key="screen-before-close-correction",
        now=NOW + timedelta(days=1),
    )
    assert screening is not None
    closed = _transition(
        submission_db,
        payload=_closed(outcome="rejected", outcome_on=date(2026, 7, 16)),
        expected_version=4,
        idempotency_key="close-before-correction",
        now=NOW + timedelta(days=2),
    )
    assert closed is not None and closed.outcome is not None
    outcome_before = closed.outcome

    corrected = _correct(
        submission_db,
        event_id=screening.activity_event.id,
        corrected_on=date(2026, 7, 14),
        expected_version=5,
        key="correct-closed-screen",
        now=NOW + timedelta(days=3),
    )

    assert corrected is not None
    assert corrected.application.stage.value == "closed"
    assert corrected.application.current_action is None
    assert corrected.application.outcome == outcome_before
    assert corrected.application.version == 6
    with submission_db.session() as session:
        outcome = session.get(ApplicationOutcome, outcome_before.id)
        application = session.get(Application, "application1")
        assert outcome is not None and application is not None
        assert (outcome.outcome, outcome.outcome_on) == ("rejected", date(2026, 7, 16))
        assert application.outcome_id == outcome.id


def test_round_linked_interviewing_milestone_cannot_be_corrected_independently(
    submission_db: Database,
) -> None:
    with submission_db.session() as session:
        scheduled = interview_repository.schedule_interview_round(
            session,
            owner_id="owner1",
            application_id="application1",
            payload=InterviewRoundCreate(
                kind="technical",
                title="Technical interview",
                scheduled_local=datetime(2026, 7, 16, 10, 0),
                scheduled_timezone="Asia/Kolkata",
                duration_minutes=60,
                meeting_format="video",
                next_action_due_on=date(2026, 7, 15),
                confirm_schedule=True,
            ),
            expected_application_version=3,
            idempotency_key="schedule-before-linked-correction",
            now=NOW,
        )
    assert scheduled is not None
    with submission_db.session() as session:
        completed = interview_repository.record_interview_round_event(
            session,
            owner_id="owner1",
            application_id="application1",
            interview_round_id=scheduled.round.id,
            payload=InterviewRoundCompletedCreate(
                event_type="completed",
                completed_on=date(2026, 7, 16),
                next_action_due_on=date(2026, 7, 17),
                confirm_complete=True,
            ),
            expected_round_version=1,
            idempotency_key="complete-before-linked-correction",
            now=datetime(2026, 7, 16, 6, 0, tzinfo=timezone.utc),
        )
    assert completed is not None
    linked_event_id = _event_id(submission_db, "application_interviewing")

    with pytest.raises(ResourceConflict, match="manually recorded"):
        _correct(
            submission_db,
            event_id=linked_event_id,
            corrected_on=date(2026, 7, 15),
            expected_version=5,
            key="reject-linked-round-correction",
            now=NOW + timedelta(days=3),
        )
    with submission_db.session() as session:
        round_ = session.get(ApplicationInterviewRound, scheduled.round.id)
        assert round_ is not None
        assert round_.completed_on == date(2026, 7, 16)
        assert session.scalar(
            select(func.count(ApplicationMilestoneCorrection.id))
        ) == 0


def test_corrected_screening_date_is_used_by_the_next_interview_transition(
    submission_db: Database,
) -> None:
    # This is the critical regression: using max(raw activity.effective_on) would
    # incorrectly retain July 15 and reject the truthful July 14 interview.
    with submission_db.session() as session:
        submission = session.get(ApplicationSubmission, "submission1")
        assert submission is not None
        submission.applied_on = date(2026, 7, 12)

    screening = _transition(
        submission_db,
        payload=_screening(reached_on=date(2026, 7, 15)),
        expected_version=3,
        idempotency_key="screen-july-15",
        now=NOW + timedelta(days=1),
    )
    assert screening is not None
    corrected = _correct(
        submission_db,
        event_id=screening.activity_event.id,
        corrected_on=date(2026, 7, 13),
        expected_version=4,
        key="screen-july-13-correction",
        now=NOW + timedelta(days=1, hours=1),
    )
    assert corrected is not None

    interviewing = _transition(
        submission_db,
        payload=_interviewing(
            reached_on=date(2026, 7, 14),
            due_on=date(2026, 7, 16),
        ),
        expected_version=5,
        idempotency_key="interview-july-14-after-correction",
        now=NOW + timedelta(days=2),
    )

    assert interviewing is not None
    assert interviewing.application.stage.value == "interviewing"
    assert interviewing.application.version == 6
    assert interviewing.activity_event.effective_on == date(2026, 7, 14)
    with submission_db.session() as session:
        original = session.get(
            ApplicationActivityEvent,
            screening.activity_event.id,
        )
        assert original is not None
        assert original.effective_on == date(2026, 7, 15)
        correction = session.scalar(select(ApplicationMilestoneCorrection))
        assert correction is not None
        assert correction.corrected_effective_on == date(2026, 7, 13)
