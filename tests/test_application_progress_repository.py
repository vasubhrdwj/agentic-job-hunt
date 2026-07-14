"""Repository coverage for post-application progress and terminal outcomes."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

import job_hunt_agent.application_submission_repository as repository
from job_hunt_agent.application_submission_schemas import (
    ClosedTransitionCreate,
    InterviewingTransitionCreate,
    OfferTransitionCreate,
    ScreeningTransitionCreate,
)
from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    ActionItem,
    Application,
    ApplicationActivityEvent,
    ApplicationOutcome,
    ApplicationSubmission,
    OwnerMutationReceipt,
)
from job_hunt_agent.mutation_receipts import MutationIdempotencyConflict
from job_hunt_agent.repository_errors import ResourceConflict, VersionConflict
from job_hunt_agent.security import load_data_keyring
from tests.test_application_submission_models import NOW, submission_db
from tests.test_application_submission_repository import _reset_to_pursuing


SCREENING_ON = date(2026, 7, 15)
INTERVIEWING_ON = date(2026, 7, 16)
OFFER_ON = date(2026, 7, 17)


def _screening(
    *,
    reached_on: date = SCREENING_ON,
    due_on: date = INTERVIEWING_ON,
) -> ScreeningTransitionCreate:
    return ScreeningTransitionCreate(
        to_stage="screening",
        reached_on=reached_on,
        next_action_due_on=due_on,
        confirm_progress=True,
    )


def _interviewing(
    *,
    reached_on: date = INTERVIEWING_ON,
    due_on: date = OFFER_ON,
) -> InterviewingTransitionCreate:
    return InterviewingTransitionCreate(
        to_stage="interviewing",
        reached_on=reached_on,
        next_action_due_on=due_on,
        confirm_progress=True,
    )


def _offer(
    *,
    received_on: date = OFFER_ON,
    due_on: date = date(2026, 7, 18),
) -> OfferTransitionCreate:
    return OfferTransitionCreate(
        to_stage="offer",
        received_on=received_on,
        next_action_due_on=due_on,
        confirm_offer=True,
    )


def _closed(
    *,
    outcome: str = "offer_accepted",
    outcome_on: date = OFFER_ON,
) -> ClosedTransitionCreate:
    return ClosedTransitionCreate(
        to_stage="closed",
        outcome=outcome,
        outcome_on=outcome_on,
        confirm_close=True,
    )


def _transition(
    database: Database,
    *,
    payload,
    expected_version: int,
    idempotency_key: str,
    now: datetime,
    owner_id: str = "owner1",
):
    with database.session() as session:
        return repository.transition_application(
            session,
            owner_id=owner_id,
            application_id="application1",
            payload=payload,
            expected_application_version=expected_version,
            idempotency_key=idempotency_key,
            keyring=load_data_keyring(production=False),
            now=now,
        )


def _assert_active_action(
    database: Database,
    *,
    stage: str,
    version: int,
    kind: str,
    due_on: date,
) -> str:
    with database.session() as session:
        application = session.get(Application, "application1")
        assert application is not None
        assert (application.stage, application.version, application.outcome_id) == (
            stage,
            version,
            None,
        )
        actions = list(
            session.scalars(
                select(ActionItem).where(
                    ActionItem.owner_id == "owner1",
                    ActionItem.application_id == "application1",
                    ActionItem.status == "open",
                )
            )
        )
        assert [(action.kind, action.due_on) for action in actions] == [
            (kind, due_on)
        ]
        return actions[0].id


def _assert_initial_applied_graph(database: Database) -> None:
    with database.session() as session:
        application = session.get(Application, "application1")
        assert application is not None
        assert (application.stage, application.version, application.outcome_id) == (
            "applied",
            3,
            None,
        )
        assert session.scalar(select(func.count(ApplicationActivityEvent.id))) == 3
        assert session.scalar(select(func.count(ApplicationSubmission.id))) == 1
        assert session.scalar(select(func.count(ApplicationOutcome.id))) == 0
        actions = list(
            session.scalars(
                select(ActionItem).where(
                    ActionItem.application_id == "application1",
                    ActionItem.status == "open",
                )
            )
        )
        assert [(action.id, action.kind) for action in actions] == [
            ("action3", "follow_up_application")
        ]


def test_full_progress_chain_closes_with_exact_immutable_graph_and_replays(
    submission_db: Database,
) -> None:
    screening_now = NOW + timedelta(days=1)
    interviewing_now = NOW + timedelta(days=2)
    offer_now = NOW + timedelta(days=3)
    closed_now = offer_now + timedelta(hours=1)

    screening = _transition(
        submission_db,
        payload=_screening(),
        expected_version=3,
        idempotency_key="progress-screening-once",
        now=screening_now,
    )
    assert screening is not None
    assert screening.transition_created is True
    assert (
        screening.application.stage.value,
        screening.application.version,
        screening.activity_event.sequence_number,
        screening.activity_event.event_type.value,
        screening.activity_event.effective_on,
    ) == ("screening", 4, 4, "application_screening", SCREENING_ON)
    assert screening.submission is not None
    assert screening.submission.id == "submission1"
    screening_action_id = _assert_active_action(
        submission_db,
        stage="screening",
        version=4,
        kind="prepare_recruiter_screen",
        due_on=INTERVIEWING_ON,
    )
    assert screening.activity_event.action_item_id == screening_action_id
    assert screening.activity_event.previous_action_item_id == "action3"

    screening_replay = _transition(
        submission_db,
        payload=_screening(),
        expected_version=3,
        idempotency_key="progress-screening-once",
        now=screening_now + timedelta(minutes=5),
    )
    assert screening_replay is not None
    assert screening_replay.transition_created is False
    assert screening_replay.model_copy(
        update={"transition_created": True}
    ) == screening

    interviewing = _transition(
        submission_db,
        payload=_interviewing(),
        expected_version=4,
        idempotency_key="progress-interviewing-once",
        now=interviewing_now,
    )
    assert interviewing is not None
    assert (
        interviewing.application.stage.value,
        interviewing.application.version,
        interviewing.activity_event.sequence_number,
        interviewing.activity_event.event_type.value,
        interviewing.activity_event.from_stage.value,
        interviewing.activity_event.effective_on,
    ) == (
        "interviewing",
        5,
        5,
        "application_interviewing",
        "screening",
        INTERVIEWING_ON,
    )
    interviewing_action_id = _assert_active_action(
        submission_db,
        stage="interviewing",
        version=5,
        kind="prepare_interview",
        due_on=OFFER_ON,
    )
    assert interviewing.activity_event.action_item_id == interviewing_action_id
    assert interviewing.activity_event.previous_action_item_id == screening_action_id

    with pytest.raises(ResourceConflict, match="progressed"):
        _transition(
            submission_db,
            payload=_screening(),
            expected_version=3,
            idempotency_key="progress-screening-once",
            now=interviewing_now + timedelta(minutes=5),
        )

    offer = _transition(
        submission_db,
        payload=_offer(),
        expected_version=5,
        idempotency_key="progress-offer-once",
        now=offer_now,
    )
    assert offer is not None
    assert (
        offer.application.stage.value,
        offer.application.version,
        offer.activity_event.sequence_number,
        offer.activity_event.event_type.value,
        offer.activity_event.from_stage.value,
        offer.activity_event.effective_on,
    ) == ("offer", 6, 6, "application_offer", "interviewing", OFFER_ON)
    offer_action_id = _assert_active_action(
        submission_db,
        stage="offer",
        version=6,
        kind="review_offer",
        due_on=date(2026, 7, 18),
    )
    assert offer.activity_event.action_item_id == offer_action_id
    assert offer.activity_event.previous_action_item_id == interviewing_action_id

    closed = _transition(
        submission_db,
        payload=_closed(),
        expected_version=6,
        idempotency_key="progress-close-once",
        now=closed_now,
    )
    assert closed is not None
    assert closed.transition_created is True
    assert (closed.application.stage.value, closed.application.version) == ("closed", 7)
    assert closed.application.current_action is None
    assert closed.outcome is not None
    assert closed.application.outcome == closed.outcome
    assert closed.submission is not None
    assert closed.submission.id == "submission1"
    assert (
        closed.outcome.application_id,
        closed.outcome.application_submission_id,
        closed.outcome.stage_at_outcome.value,
        closed.outcome.outcome.value,
        closed.outcome.outcome_on,
        closed.outcome.recording_method,
        closed.outcome.recorded_at,
    ) == (
        "application1",
        "submission1",
        "offer",
        "offer_accepted",
        OFFER_ON,
        "manual",
        closed_now,
    )
    assert (
        closed.activity_event.sequence_number,
        closed.activity_event.event_type.value,
        closed.activity_event.from_stage.value,
        closed.activity_event.to_stage.value,
        closed.activity_event.action_item_id,
        closed.activity_event.previous_action_item_id,
        closed.activity_event.submission_id,
        closed.activity_event.effective_on,
        closed.activity_event.outcome_id,
    ) == (
        7,
        "application_closed",
        "offer",
        "closed",
        None,
        offer_action_id,
        None,
        OFFER_ON,
        closed.outcome.id,
    )

    closed_replay = _transition(
        submission_db,
        payload=_closed(),
        expected_version=6,
        idempotency_key="progress-close-once",
        now=closed_now + timedelta(minutes=5),
    )
    assert closed_replay is not None
    assert closed_replay.transition_created is False
    assert closed_replay.model_copy(update={"transition_created": True}) == closed

    with submission_db.session() as session:
        application = session.get(Application, "application1")
        assert application is not None
        assert (application.stage, application.version, application.outcome_id) == (
            "closed",
            7,
            closed.outcome.id,
        )

        submission = session.get(ApplicationSubmission, "submission1")
        assert submission is not None
        assert (
            submission.owner_id,
            submission.application_id,
            submission.application_pack_id,
            submission.application_pack_revision_id,
            submission.application_pack_review_event_id,
            submission.application_artifact_revision_id,
            submission.application_artifact_approval_event_id,
            submission.tailored_resume_version_id,
            submission.destination_url,
            submission.applied_on,
            submission.submission_method,
        ) == (
            "owner1",
            "application1",
            "pack1",
            "grounding1",
            "groundingreview1",
            "artifact1",
            "artifactapproval1",
            "resume2",
            "https://careers.example.com/jobs/1/apply",
            date(2026, 7, 14),
            "manual",
        )
        assert session.scalar(select(func.count(ApplicationSubmission.id))) == 1

        outcomes = list(session.scalars(select(ApplicationOutcome)))
        assert len(outcomes) == 1
        outcome = outcomes[0]
        assert (
            outcome.id,
            outcome.owner_id,
            outcome.application_id,
            outcome.application_submission_id,
            outcome.stage_at_outcome,
            outcome.outcome,
            outcome.outcome_on,
            outcome.recording_method,
        ) == (
            closed.outcome.id,
            "owner1",
            "application1",
            "submission1",
            "offer",
            "offer_accepted",
            OFFER_ON,
            "manual",
        )

        events = list(
            session.scalars(
                select(ApplicationActivityEvent).order_by(
                    ApplicationActivityEvent.sequence_number
                )
            )
        )
        assert [event.sequence_number for event in events] == list(range(1, 8))
        assert [event.event_type for event in events] == [
            "application_created",
            "application_ready_to_apply",
            "application_applied",
            "application_screening",
            "application_interviewing",
            "application_offer",
            "application_closed",
        ]
        assert [event.id for event in events[3:]] == [
            screening.activity_event.id,
            interviewing.activity_event.id,
            offer.activity_event.id,
            closed.activity_event.id,
        ]
        assert [event.effective_on for event in events[3:]] == [
            SCREENING_ON,
            INTERVIEWING_ON,
            OFFER_ON,
            OFFER_ON,
        ]
        assert [event.action_item_id for event in events[3:]] == [
            screening_action_id,
            interviewing_action_id,
            offer_action_id,
            None,
        ]
        assert [event.previous_action_item_id for event in events[3:]] == [
            "action3",
            screening_action_id,
            interviewing_action_id,
            offer_action_id,
        ]
        assert [event.outcome_id for event in events[3:]] == [
            None,
            None,
            None,
            closed.outcome.id,
        ]

        actions = list(
            session.scalars(
                select(ActionItem).order_by(ActionItem.created_at, ActionItem.id)
            )
        )
        assert sum(action.status == "open" for action in actions) == 0
        assert {
            action.kind: action.status
            for action in actions
            if action.kind
            in {
                "follow_up_application",
                "prepare_recruiter_screen",
                "prepare_interview",
                "review_offer",
            }
        } == {
            "follow_up_application": "completed",
            "prepare_recruiter_screen": "completed",
            "prepare_interview": "completed",
            "review_offer": "cancelled",
        }
        final_action = session.get(ActionItem, offer_action_id)
        assert final_action is not None
        assert final_action.completed_at is None
        assert final_action.cancelled_at is not None


@pytest.mark.parametrize(
    ("payload", "expected_stage", "expected_event", "expected_action", "due_on"),
    [
        (
            _interviewing(reached_on=SCREENING_ON, due_on=INTERVIEWING_ON),
            "interviewing",
            "application_interviewing",
            "prepare_interview",
            INTERVIEWING_ON,
        ),
        (
            _offer(received_on=SCREENING_ON, due_on=INTERVIEWING_ON),
            "offer",
            "application_offer",
            "review_offer",
            INTERVIEWING_ON,
        ),
    ],
)
def test_applied_can_skip_directly_to_interviewing_or_offer(
    submission_db: Database,
    payload,
    expected_stage: str,
    expected_event: str,
    expected_action: str,
    due_on: date,
) -> None:
    result = _transition(
        submission_db,
        payload=payload,
        expected_version=3,
        idempotency_key=f"skip-applied-to-{expected_stage}",
        now=NOW + timedelta(days=1),
    )
    assert result is not None
    assert (result.application.stage.value, result.application.version) == (
        expected_stage,
        4,
    )
    assert (
        result.activity_event.sequence_number,
        result.activity_event.event_type.value,
        result.activity_event.from_stage.value,
        result.activity_event.effective_on,
        result.activity_event.previous_action_item_id,
    ) == (4, expected_event, "applied", SCREENING_ON, "action3")
    assert result.submission is not None
    assert result.submission.id == "submission1"
    _assert_active_action(
        submission_db,
        stage=expected_stage,
        version=4,
        kind=expected_action,
        due_on=due_on,
    )
    with submission_db.session() as session:
        assert session.scalar(select(func.count(ApplicationSubmission.id))) == 1
        assert session.scalar(select(func.count(ApplicationOutcome.id))) == 0
        assert session.scalar(select(func.count(ApplicationActivityEvent.id))) == 4


@pytest.mark.parametrize(
    ("case", "payload", "expected_version", "error"),
    [
        ("stale-version", _screening(), 2, VersionConflict),
        (
            "milestone-before-application",
            _screening(reached_on=date(2026, 7, 13)),
            3,
            ValueError,
        ),
        (
            "future-milestone",
            _screening(reached_on=INTERVIEWING_ON),
            3,
            ValueError,
        ),
        (
            "past-next-action",
            _screening(due_on=date(2026, 7, 14)),
            3,
            ValueError,
        ),
        (
            "accepted-without-offer",
            _closed(outcome_on=SCREENING_ON),
            3,
            ResourceConflict,
        ),
    ],
)
def test_invalid_version_stage_outcome_or_dates_leave_applied_graph_unchanged(
    submission_db: Database,
    case: str,
    payload,
    expected_version: int,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        _transition(
            submission_db,
            payload=payload,
            expected_version=expected_version,
            idempotency_key=f"invalid-{case}",
            now=NOW + timedelta(days=1),
        )
    _assert_initial_applied_graph(submission_db)


@pytest.mark.parametrize("illegal_payload", [_screening(), _interviewing()])
def test_backward_or_duplicate_progress_stage_is_rejected_without_partial_writes(
    submission_db: Database,
    illegal_payload,
) -> None:
    first = _transition(
        submission_db,
        payload=_interviewing(reached_on=SCREENING_ON, due_on=INTERVIEWING_ON),
        expected_version=3,
        idempotency_key="interview-before-invalid-stage",
        now=NOW + timedelta(days=1),
    )
    assert first is not None

    with pytest.raises(ResourceConflict, match="cannot move"):
        _transition(
            submission_db,
            payload=illegal_payload,
            expected_version=4,
            idempotency_key=f"invalid-from-interview-{illegal_payload.to_stage}",
            now=NOW + timedelta(days=2),
        )

    _assert_active_action(
        submission_db,
        stage="interviewing",
        version=4,
        kind="prepare_interview",
        due_on=INTERVIEWING_ON,
    )
    with submission_db.session() as session:
        assert session.scalar(select(func.count(ApplicationActivityEvent.id))) == 4
        assert session.scalar(select(func.count(ApplicationOutcome.id))) == 0


def test_outcome_date_cannot_precede_latest_recorded_milestone(
    submission_db: Database,
) -> None:
    screening = _transition(
        submission_db,
        payload=_screening(),
        expected_version=3,
        idempotency_key="screen-before-invalid-outcome-date",
        now=NOW + timedelta(days=1),
    )
    assert screening is not None

    with pytest.raises(ValueError, match="prior milestone"):
        _transition(
            submission_db,
            payload=_closed(outcome="rejected", outcome_on=date(2026, 7, 14)),
            expected_version=4,
            idempotency_key="invalid-outcome-date",
            now=NOW + timedelta(days=1, hours=1),
        )

    _assert_active_action(
        submission_db,
        stage="screening",
        version=4,
        kind="prepare_recruiter_screen",
        due_on=INTERVIEWING_ON,
    )
    with submission_db.session() as session:
        assert session.scalar(select(func.count(ApplicationActivityEvent.id))) == 4
        assert session.scalar(select(func.count(ApplicationOutcome.id))) == 0


def test_changed_request_cannot_reuse_progress_idempotency_key(
    submission_db: Database,
) -> None:
    result = _transition(
        submission_db,
        payload=_screening(),
        expected_version=3,
        idempotency_key="screen-request-identity",
        now=NOW + timedelta(days=1),
    )
    assert result is not None

    with pytest.raises(MutationIdempotencyConflict):
        _transition(
            submission_db,
            payload=_screening(due_on=date(2026, 7, 17)),
            expected_version=3,
            idempotency_key="screen-request-identity",
            now=NOW + timedelta(days=1, minutes=1),
        )

    _assert_active_action(
        submission_db,
        stage="screening",
        version=4,
        kind="prepare_recruiter_screen",
        due_on=INTERVIEWING_ON,
    )
    with submission_db.session() as session:
        assert session.scalar(select(func.count(ApplicationActivityEvent.id))) == 4
        assert session.scalar(select(func.count(OwnerMutationReceipt.id))) == 1


def test_progress_lookup_is_owner_isolated_and_creates_no_receipt(
    submission_db: Database,
) -> None:
    result = _transition(
        submission_db,
        owner_id="owner2",
        payload=_screening(),
        expected_version=3,
        idempotency_key="cross-owner-progress",
        now=NOW + timedelta(days=1),
    )
    assert result is None

    _assert_initial_applied_graph(submission_db)
    with submission_db.session() as session:
        assert session.scalar(select(func.count(OwnerMutationReceipt.id))) == 0


@pytest.mark.parametrize("outcome", ["withdrawn", "posting_closed"])
def test_pre_submission_closure_has_no_fabricated_submission_or_next_action(
    submission_db: Database,
    outcome: str,
) -> None:
    _reset_to_pursuing(submission_db)

    result = _transition(
        submission_db,
        payload=_closed(outcome=outcome, outcome_on=SCREENING_ON),
        expected_version=1,
        idempotency_key=f"close-before-submission-{outcome}",
        now=NOW + timedelta(days=1),
    )

    assert result is not None
    assert result.application.stage.value == "closed"
    assert result.application.current_action is None
    assert result.submission is None
    assert result.outcome is not None
    assert result.outcome.application_submission_id is None
    assert result.outcome.stage_at_outcome.value == "pursuing"
    assert result.outcome.outcome.value == outcome
    assert result.activity_event.sequence_number == 2
    assert result.activity_event.previous_action_item_id == "action1"
    with submission_db.session() as session:
        assert session.scalar(select(func.count(ApplicationSubmission.id))) == 0
        assert session.scalar(select(func.count(ApplicationOutcome.id))) == 1
        assert session.scalar(
            select(func.count(ActionItem.id)).where(ActionItem.status == "open")
        ) == 0
