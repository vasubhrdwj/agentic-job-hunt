"""Behavior tests for the owner-local weekly operating review."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    ActionItem,
    Application,
    ApplicationActionReview,
    ApplicationActivityEvent,
    ApplicationMetricSnapshot,
    OwnerMutationReceipt,
)
from job_hunt_agent.mutation_receipts import MutationIdempotencyConflict
from job_hunt_agent.repository_errors import ResourceConflict, VersionConflict
from job_hunt_agent.weekly_review_repository import (
    load_weekly_review,
    record_application_action_review,
)
from job_hunt_agent.weekly_review_schemas import ApplicationActionReviewCreate
from tests.test_application_action_center_repository import (
    NOW as ACTION_NOW,
    _add_application,
    action_center_db,
)
from tests.test_application_correction_repository import _correct
from tests.test_application_progress_repository import (
    _interviewing,
    _screening,
    _transition,
)
from tests.test_interview_round_repository import _schedule
from tests.test_application_submission_models import (
    NOW as SUBMISSION_NOW,
    submission_db,
)


def _metric_by_stage(review, stage: str):
    return next(item for item in review.funnel.overall if item.stage.value == stage)


def _record_review(
    database: Database,
    *,
    decision: str = "continue",
    new_due_on: date = date(2026, 7, 22),
    expected_version: int = 3,
    action_id: str = "action3",
    key: str = "review-once",
    now: datetime = SUBMISSION_NOW,
):
    with database.session() as session:
        return record_application_action_review(
            session,
            owner_id="owner1",
            application_id="application1",
            action_id=action_id,
            payload=ApplicationActionReviewCreate(
                decision=decision,
                new_due_on=new_due_on,
                confirm_current_action=True,
            ),
            expected_application_version=expected_version,
            idempotency_key=key,
            now=now,
        )


def test_stale_projection_uses_owner_timezone_is_ordered_and_never_auto_rejects(
    action_center_db: Database,
) -> None:
    # At this instant Kolkata is July 16 while UTC is still July 15.
    with action_center_db.session() as session:
        _add_application(
            session,
            owner_id="owner-a",
            suffix="oldest",
            due_on=date(2026, 7, 13),
        )
        _add_application(
            session,
            owner_id="owner-a",
            suffix="z",
            due_on=date(2026, 7, 14),
        )
        _add_application(
            session,
            owner_id="owner-a",
            suffix="a",
            due_on=date(2026, 7, 14),
        )
        _add_application(
            session,
            owner_id="owner-a",
            suffix="boundary-kolkata",
            due_on=date(2026, 7, 15),
        )
        _add_application(
            session,
            owner_id="owner-b",
            suffix="boundary-utc",
            due_on=date(2026, 7, 15),
        )
        before = {
            row.id: (row.stage, row.version, row.outcome_id)
            for row in session.scalars(select(Application))
        }

        kolkata = load_weekly_review(
            session,
            owner_id="owner-a",
            now=ACTION_NOW,
        )
        utc = load_weekly_review(
            session,
            owner_id="owner-b",
            now=ACTION_NOW,
        )
        after = {
            row.id: (row.stage, row.version, row.outcome_id)
            for row in session.scalars(select(Application))
        }

    assert (kolkata.owner_timezone, kolkata.owner_local_date) == (
        "Asia/Kolkata",
        date(2026, 7, 16),
    )
    assert [item.application.id for item in kolkata.stale_applications] == [
        "app-oldest",
        "app-a",
        "app-z",
        "app-boundary-kolkata",
    ]
    assert [item.days_overdue for item in kolkata.stale_applications] == [3, 2, 2, 1]
    assert kolkata.stale_application_total == 4
    assert (utc.owner_timezone, utc.owner_local_date) == (
        "UTC",
        date(2026, 7, 15),
    )
    assert utc.stale_application_total == 0
    assert before == after
    assert kolkata.as_of == utc.as_of == ACTION_NOW


def test_funnel_uses_one_fixed_fourteen_day_mature_cohort(
    submission_db: Database,
) -> None:
    with submission_db.session() as session:
        mature = load_weekly_review(
            session,
            owner_id="owner1",
            now=datetime(2026, 7, 28, 8, tzinfo=timezone.utc),
        )

    for stage in ("screen", "interview", "offer"):
        metric = _metric_by_stage(mature, stage)
        assert (
            metric.cohort_total,
            metric.mature,
            metric.evaluable,
            metric.immature,
            metric.censored_open,
            metric.converted,
            metric.late_converted,
            metric.missing,
            metric.rate,
        ) == (1, 1, 1, 0, 0, 0, 0, 0, 0.0)
    assert mature.funnel.attribution_missing == 1
    assert mature.funnel.assessment_missing == 1
    assert mature.funnel.by_acquisition_source == []
    assert mature.funnel.by_career_track == []
    assert mature.funnel.by_assessment_band == []


def test_younger_conversion_stays_immature_and_is_not_resolved_case_bias(
    submission_db: Database,
) -> None:
    result = _transition(
        submission_db,
        payload=_screening(
            reached_on=date(2026, 7, 15),
            due_on=date(2026, 7, 16),
        ),
        expected_version=3,
        idempotency_key="young-screen",
        now=SUBMISSION_NOW + timedelta(days=1),
    )
    assert result is not None

    with submission_db.session() as session:
        review = load_weekly_review(
            session,
            owner_id="owner1",
            now=datetime(2026, 7, 20, 8, tzinfo=timezone.utc),
        )

    screen = _metric_by_stage(review, "screen")
    interview = _metric_by_stage(review, "interview")
    assert (
        screen.mature,
        screen.evaluable,
        screen.immature,
        screen.censored_open,
        screen.converted,
        screen.rate,
    ) == (0, 0, 1, 0, 0, None)
    assert (
        interview.mature,
        interview.evaluable,
        interview.immature,
        interview.censored_open,
        interview.rate,
    ) == (0, 0, 1, 1, None)


def test_direct_interview_skip_does_not_fabricate_a_screen_conversion(
    submission_db: Database,
) -> None:
    result = _transition(
        submission_db,
        payload=_interviewing(
            reached_on=date(2026, 7, 15),
            due_on=date(2026, 7, 16),
        ),
        expected_version=3,
        idempotency_key="direct-interview",
        now=SUBMISSION_NOW + timedelta(days=1),
    )
    assert result is not None

    with submission_db.session() as session:
        review = load_weekly_review(
            session,
            owner_id="owner1",
            now=datetime(2026, 7, 28, 8, tzinfo=timezone.utc),
        )

    screen = _metric_by_stage(review, "screen")
    interview = _metric_by_stage(review, "interview")
    offer = _metric_by_stage(review, "offer")
    assert (screen.converted, screen.rate) == (0, 0.0)
    assert (interview.converted, interview.rate) == (1, 1.0)
    assert (offer.converted, offer.rate) == (0, 0.0)


def test_latest_corrected_milestone_controls_on_time_vs_late_conversion(
    submission_db: Database,
) -> None:
    screening = _transition(
        submission_db,
        payload=_screening(
            reached_on=date(2026, 7, 15),
            due_on=date(2026, 7, 16),
        ),
        expected_version=3,
        idempotency_key="screen-before-weekly-correction",
        now=SUBMISSION_NOW + timedelta(days=1),
    )
    assert screening is not None
    corrected = _correct(
        submission_db,
        event_id=screening.activity_event.id,
        corrected_on=date(2026, 7, 29),
        expected_version=4,
        key="weekly-late-correction",
        now=datetime(2026, 7, 29, 8, tzinfo=timezone.utc),
    )
    assert corrected is not None

    with submission_db.session() as session:
        review = load_weekly_review(
            session,
            owner_id="owner1",
            now=datetime(2026, 7, 30, 8, tzinfo=timezone.utc),
        )

    screen = _metric_by_stage(review, "screen")
    assert (
        screen.mature,
        screen.evaluable,
        screen.converted,
        screen.late_converted,
        screen.rate,
    ) == (1, 1, 0, 1, 0.0)


def test_incomplete_application_graph_is_explicit_missing_not_silent_denominator(
    submission_db: Database,
) -> None:
    with submission_db.session() as session:
        applied = session.scalar(
            select(ApplicationActivityEvent).where(
                ApplicationActivityEvent.event_type == "application_applied"
            )
        )
        assert applied is not None
        session.delete(applied)

    with submission_db.session() as session:
        review = load_weekly_review(
            session,
            owner_id="owner1",
            now=datetime(2026, 7, 28, 8, tzinfo=timezone.utc),
        )

    for stage in ("screen", "interview", "offer"):
        metric = _metric_by_stage(review, stage)
        assert (
            metric.cohort_total,
            metric.mature,
            metric.evaluable,
            metric.missing,
            metric.rate,
        ) == (1, 1, 0, 1, None)


def test_captured_source_and_assessment_are_segmented_from_frozen_snapshot(
    submission_db: Database,
) -> None:
    with submission_db.session() as session:
        session.add(
            ApplicationMetricSnapshot(
                id="snapshot1",
                owner_id="owner1",
                application_id="application1",
                job_posting_id="posting1",
                pursued_posting_version_id="postingversion1",
                acquisition_source="referral",
                attribution_status="captured",
                assessment_state="assessed",
                assessment_band="core",
                assessment_algorithm_version="fit-v1",
                assessment_reason=None,
                recorded_at=SUBMISSION_NOW,
                created_at=SUBMISSION_NOW,
            )
        )

    with submission_db.session() as session:
        review = load_weekly_review(
            session,
            owner_id="owner1",
            now=datetime(2026, 7, 28, 8, tzinfo=timezone.utc),
        )

    assert review.funnel.attribution_missing == 0
    assert review.funnel.assessment_missing == 0
    assert [item.key for item in review.funnel.by_acquisition_source] == [
        "referral"
    ]
    assert [item.key for item in review.funnel.by_assessment_band] == ["core"]
    assert review.funnel.by_career_track == []


@pytest.mark.parametrize("decision", ["continue", "waiting"])
def test_action_review_reschedules_exact_action_and_replays_once(
    submission_db: Database,
    decision: str,
) -> None:
    created = _record_review(
        submission_db,
        decision=decision,
        key=f"review-{decision}",
    )
    replay = _record_review(
        submission_db,
        decision=decision,
        key=f"review-{decision}",
    )

    assert created is not None and replay is not None
    assert created.mutation_created is True
    assert replay.mutation_created is False
    assert created.review.id == replay.review.id
    assert created.review.decision.value == decision
    assert (
        created.review.prior_due_on,
        created.review.new_due_on,
        created.review.prior_action_version,
        created.review.action_version,
        created.review.prior_application_version,
        created.review.application_version,
    ) == (
        date(2026, 7, 21),
        date(2026, 7, 22),
        1,
        2,
        3,
        4,
    )
    assert created.application.version == 4
    assert created.action.id == "action3"
    assert created.action.version == 2
    assert created.action.due_on == date(2026, 7, 22)

    with submission_db.session() as session:
        assert session.scalar(select(func.count(ApplicationActionReview.id))) == 1
        assert session.scalar(select(func.count(OwnerMutationReceipt.id))) == 1


def test_action_review_changed_same_key_stale_version_and_wrong_action_fail_closed(
    submission_db: Database,
) -> None:
    created = _record_review(submission_db, key="stable-review-key")
    assert created is not None

    with pytest.raises(MutationIdempotencyConflict):
        _record_review(
            submission_db,
            decision="waiting",
            key="stable-review-key",
        )
    with pytest.raises(VersionConflict):
        _record_review(
            submission_db,
            new_due_on=date(2026, 7, 23),
            expected_version=3,
            key="stale-review-version",
        )
    with pytest.raises(ResourceConflict, match="exact current open action"):
        _record_review(
            submission_db,
            action_id="action1",
            expected_version=4,
            key="historical-action",
        )
    missing = _record_review(
        submission_db,
        action_id="missing-action",
        expected_version=4,
        key="missing-action",
    )
    assert missing is None

    with submission_db.session() as session:
        assert session.scalar(select(func.count(ApplicationActionReview.id))) == 1


@pytest.mark.parametrize(
    "new_due_on, message",
    [
        (date(2026, 7, 13), "owner-local today"),
        (date(2026, 10, 13), "owner-local today"),
        (date(2026, 7, 21), "strictly after"),
    ],
)
def test_action_review_date_bounds_are_owner_local_and_strictly_forward(
    submission_db: Database,
    new_due_on: date,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _record_review(
            submission_db,
            new_due_on=new_due_on,
            key=f"invalid-date-{new_due_on.isoformat()}",
        )

    with submission_db.session() as session:
        application = session.get(Application, "application1")
        action = session.get(ActionItem, "action3")
        assert application is not None and action is not None
        assert (application.version, action.version, action.due_on) == (
            3,
            1,
            date(2026, 7, 21),
        )
        assert session.scalar(select(func.count(ApplicationActionReview.id))) == 0


def test_round_linked_action_is_listed_stale_but_refuses_generic_review(
    submission_db: Database,
) -> None:
    scheduled = _schedule(submission_db)
    assert scheduled is not None
    round_action_id = scheduled.event.action_item_id
    assert round_action_id is not None

    with submission_db.session() as session:
        review = load_weekly_review(
            session,
            owner_id="owner1",
            now=datetime(2026, 7, 16, 8, tzinfo=timezone.utc),
        )
    assert [item.current_action.id for item in review.stale_applications] == [
        round_action_id
    ]

    with pytest.raises(ResourceConflict, match="interview-round actions"):
        with submission_db.session() as session:
            record_application_action_review(
                session,
                owner_id="owner1",
                application_id="application1",
                action_id=round_action_id,
                payload=ApplicationActionReviewCreate(
                    decision="waiting",
                    new_due_on=date(2026, 7, 17),
                    confirm_current_action=True,
                ),
                expected_application_version=4,
                idempotency_key="reject-round-action",
                now=datetime(2026, 7, 16, 8, tzinfo=timezone.utc),
            )

    with submission_db.session() as session:
        assert session.scalar(select(func.count(ApplicationActionReview.id))) == 0


def test_action_review_replay_survives_a_later_stage_and_action_transition(
    submission_db: Database,
) -> None:
    created = _record_review(
        submission_db,
        new_due_on=date(2026, 7, 22),
        key="review-before-progress",
    )
    assert created is not None
    progressed = _transition(
        submission_db,
        payload=_screening(
            reached_on=date(2026, 7, 15),
            due_on=date(2026, 7, 23),
        ),
        expected_version=4,
        idempotency_key="progress-after-review",
        now=SUBMISSION_NOW + timedelta(days=1),
    )
    assert progressed is not None
    assert progressed.application.current_action is not None
    assert progressed.application.current_action.id != "action3"

    replay = _record_review(
        submission_db,
        new_due_on=date(2026, 7, 22),
        expected_version=3,
        key="review-before-progress",
        now=SUBMISSION_NOW + timedelta(days=2),
    )
    assert replay is not None
    assert replay.mutation_created is False
    assert replay.review.id == created.review.id
    assert replay.action.id == "action3"
    assert replay.action.status.value == "completed"
    assert replay.application.version == 5
    assert replay.application.current_action is not None
    assert replay.application.current_action.id != replay.action.id
