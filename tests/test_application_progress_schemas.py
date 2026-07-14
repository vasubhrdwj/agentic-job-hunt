"""Focused contracts for post-application progress and terminal outcomes."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from job_hunt_agent.application_schemas import (
    ActionItemResponse,
    ApplicationOutcome,
    ApplicationOutcomeResponse,
    ApplicationPostingSummary,
    ApplicationSummary,
)
from job_hunt_agent.application_submission_schemas import (
    ApplicationTransitionCreate,
    ClosedTransitionCreate,
    InterviewingTransitionCreate,
    OfferTransitionCreate,
    ScreeningTransitionCreate,
)


NOW = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)


def _action(kind: str, **updates: object) -> ActionItemResponse:
    values: dict[str, object] = {
        "id": "action1",
        "version": 1,
        "application_id": "application1",
        "kind": kind,
        "status": "open",
        "title": kind.replace("_", " "),
        "due_on": date(2026, 7, 20),
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(updates)
    return ActionItemResponse.model_validate(values)


def _application(
    stage: str,
    current_action: ActionItemResponse | None,
    outcome: ApplicationOutcomeResponse | None = None,
) -> ApplicationSummary:
    return ApplicationSummary.model_validate(
        {
            "id": "application1",
            "version": 7,
            "opportunity_id": "opportunity1",
            "pursued_posting_version_id": "postingversion1",
            "stage": stage,
            "posting": ApplicationPostingSummary(
                id="posting1",
                company="Example",
                title="Senior Backend Engineer",
                canonical_url="https://careers.example.com/jobs/123",
                first_party=True,
                state="open",
            ),
            "current_action": current_action,
            "outcome": outcome,
            "created_at": NOW - timedelta(days=14),
            "updated_at": NOW,
        }
    )


def _outcome(**updates: object) -> ApplicationOutcomeResponse:
    values: dict[str, object] = {
        "id": "outcome1",
        "application_id": "application1",
        "application_submission_id": "submission1",
        "stage_at_outcome": "interviewing",
        "outcome": "rejected",
        "outcome_on": date(2026, 7, 14),
        "recording_method": "manual",
        "recorded_at": NOW,
        "created_at": NOW,
    }
    values.update(updates)
    return ApplicationOutcomeResponse.model_validate(values)


@pytest.mark.parametrize(
    ("stage", "action_kind"),
    [
        ("pursuing", "review_and_prepare_application"),
        ("ready_to_apply", "submit_application"),
        ("applied", "follow_up_application"),
        ("screening", "prepare_recruiter_screen"),
        ("interviewing", "prepare_interview"),
        ("offer", "review_offer"),
    ],
)
def test_every_active_stage_requires_its_exact_open_action(
    stage: str,
    action_kind: str,
) -> None:
    application = _application(stage, _action(action_kind))

    assert application.current_action is not None
    assert application.current_action.kind.value == action_kind
    with pytest.raises(ValidationError, match="current_action"):
        _application(stage, None)
    with pytest.raises(ValidationError, match="current_action"):
        _application(
            stage,
            _action(
                "review_offer" if stage != "offer" else "prepare_interview"
            ),
        )
    with pytest.raises(ValidationError, match="outcome"):
        _application(stage, _action(action_kind), _outcome())


def test_only_closed_has_no_current_action() -> None:
    outcome = _outcome()
    closed = _application("closed", None, outcome)

    assert closed.current_action is None
    assert closed.outcome == outcome
    with pytest.raises(ValidationError, match="outcome"):
        _application("closed", None)
    with pytest.raises(ValidationError, match="exact outcome"):
        _application(
            "closed",
            None,
            _outcome(application_id="anotherapplication"),
        )
    with pytest.raises(ValidationError, match="closed"):
        _application("closed", _action("review_offer"), outcome)


def test_outcome_enum_is_small_stable_and_metric_safe() -> None:
    assert {item.value for item in ApplicationOutcome} == {
        "rejected",
        "withdrawn",
        "offer_accepted",
        "offer_declined",
        "no_response",
        "posting_closed",
    }


@pytest.mark.parametrize(
    ("outcome", "stage", "submission_id"),
    [
        ("withdrawn", "pursuing", None),
        ("posting_closed", "ready_to_apply", None),
        ("no_response", "applied", "submission1"),
        ("rejected", "screening", "submission1"),
        ("rejected", "interviewing", "submission1"),
        ("offer_accepted", "offer", "submission1"),
        ("offer_declined", "offer", "submission1"),
    ],
)
def test_outcomes_preserve_the_stage_and_exact_submission_boundary(
    outcome: str,
    stage: str,
    submission_id: str | None,
) -> None:
    response = _outcome(
        outcome=outcome,
        stage_at_outcome=stage,
        application_submission_id=submission_id,
    )

    assert response.outcome.value == outcome
    assert response.stage_at_outcome.value == stage
    assert response.application_submission_id == submission_id


@pytest.mark.parametrize(
    "updates",
    [
        {
            "outcome": "offer_accepted",
            "stage_at_outcome": "interviewing",
        },
        {
            "outcome": "offer_declined",
            "stage_at_outcome": "screening",
        },
        {
            "outcome": "rejected",
            "stage_at_outcome": "pursuing",
            "application_submission_id": None,
        },
        {
            "outcome": "no_response",
            "stage_at_outcome": "ready_to_apply",
            "application_submission_id": None,
        },
        {
            "outcome": "withdrawn",
            "stage_at_outcome": "screening",
            "application_submission_id": None,
        },
        {
            "outcome": "posting_closed",
            "stage_at_outcome": "pursuing",
            "application_submission_id": "submission1",
        },
        {
            "stage_at_outcome": "closed",
        },
    ],
)
def test_outcome_contract_rejects_impossible_funnel_history(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _outcome(**updates)


def test_outcome_is_manual_timestamped_and_extra_fields_fail_closed() -> None:
    with pytest.raises(ValidationError):
        _outcome(recording_method="automatic")
    with pytest.raises(ValidationError, match="created_at"):
        _outcome(created_at=NOW - timedelta(seconds=1))
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _outcome(provider_payload={"private": "must not leak"})


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        (
            {
                "to_stage": "screening",
                "reached_on": "2026-07-14",
                "next_action_due_on": "2026-07-16",
                "confirm_progress": True,
            },
            ScreeningTransitionCreate,
        ),
        (
            {
                "to_stage": "interviewing",
                "reached_on": "2026-07-14",
                "next_action_due_on": "2026-07-18",
                "confirm_progress": True,
            },
            InterviewingTransitionCreate,
        ),
        (
            {
                "to_stage": "offer",
                "received_on": "2026-07-15",
                "next_action_due_on": "2026-07-20",
                "confirm_offer": True,
            },
            OfferTransitionCreate,
        ),
        (
            {
                "to_stage": "closed",
                "outcome": "rejected",
                "outcome_on": "2026-07-15",
                "confirm_close": True,
            },
            ClosedTransitionCreate,
        ),
    ],
)
def test_progress_transition_union_is_discriminated_and_exact(
    payload: dict[str, object],
    expected_type: type,
) -> None:
    transition = TypeAdapter(ApplicationTransitionCreate).validate_python(payload)

    assert isinstance(transition, expected_type)
    assert transition.model_dump(mode="json") == payload


@pytest.mark.parametrize(
    "payload",
    [
        {
            "to_stage": "screening",
            "reached_on": "2026-07-14",
            "next_action_due_on": "2026-07-16",
            "confirm_progress": False,
        },
        {
            "to_stage": "interviewing",
            "reached_on": "2026-07-14",
            "next_action_due_on": "2026-07-18",
            "confirm_progress": 1,
        },
        {
            "to_stage": "offer",
            "received_on": "2026-07-15",
            "next_action_due_on": "2026-07-20",
            "confirm_offer": False,
        },
        {
            "to_stage": "closed",
            "outcome": "accepted",
            "outcome_on": "2026-07-15",
            "confirm_close": True,
        },
        {
            "to_stage": "closed",
            "outcome": "rejected",
            "outcome_on": "2026-07-15",
            "next_action_due_on": "2026-07-20",
            "confirm_close": True,
        },
        {
            "to_stage": "hired",
            "reached_on": "2026-07-15",
            "next_action_due_on": "2026-07-20",
            "confirm_progress": True,
        },
    ],
)
def test_progress_payloads_reject_coercion_unknowns_and_terminal_actions(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ApplicationTransitionCreate).validate_python(payload)
