"""Strict transport invariants for the Phase 6B weekly review."""

from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest
from pydantic import ValidationError

from job_hunt_agent.weekly_review_schemas import (
    ApplicationActionReviewCreate,
    ApplicationActionReviewMutationResponse,
    ApplicationActionReviewResponse,
    FunnelSegmentMetric,
    FunnelStageMetric,
    OutreachObservedMetric,
    OutreachRescueMetric,
    WeeklyReviewFunnel,
    WeeklyReviewOutreach,
    WeeklyReviewPolicy,
    WeeklyReviewResponse,
    WeeklyReviewStaleApplication,
    WeeklyReviewWindow,
)


RECORDED_AT = "2026-07-15T08:30:00Z"
CREATED_AT = "2026-07-15T08:30:01Z"


def _action(*, action_id: str = "action-a", version: int = 2) -> dict[str, object]:
    return {
        "id": action_id,
        "version": version,
        "application_id": "application-a",
        "interview_round_id": None,
        "kind": "review_and_prepare_application",
        "status": "open",
        "title": "Review and prepare the application",
        "due_on": "2026-07-22",
        "completed_at": None,
        "cancelled_at": None,
        "created_at": "2026-07-10T08:30:00Z",
        "updated_at": RECORDED_AT,
    }


def _posting(*, posting_id: str = "posting-a") -> dict[str, object]:
    return {
        "id": posting_id,
        "company": "Acme",
        "title": "Senior Backend Engineer",
        "canonical_url": "https://careers.example.com/jobs/123",
        "first_party": True,
        "state": "open",
    }


def _application(
    *,
    application_id: str = "application-a",
    version: int = 2,
    posting_id: str = "posting-a",
    action_id: str = "action-a",
) -> dict[str, object]:
    action = _action(action_id=action_id, version=version)
    action["application_id"] = application_id
    return {
        "id": application_id,
        "version": version,
        "opportunity_id": "opportunity-a",
        "pursued_posting_version_id": "posting-version-a",
        "stage": "pursuing",
        "posting": _posting(posting_id=posting_id),
        "current_action": action,
        "outcome": None,
        "created_at": "2026-07-10T08:30:00Z",
        "updated_at": RECORDED_AT,
    }


def _review(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": "review-a",
        "application_id": "application-a",
        "prior_action_item_id": "action-a",
        "action_item_id": "action-a",
        "decision": "continue",
        "prior_due_on": "2026-07-15",
        "new_due_on": "2026-07-22",
        "prior_action_version": 1,
        "action_version": 2,
        "prior_application_version": 1,
        "application_version": 2,
        "recording_method": "manual",
        "recorded_at": RECORDED_AT,
        "created_at": CREATED_AT,
    }
    values.update(overrides)
    return values


def _stage(stage: str, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "stage": stage,
        "cohort_total": 4,
        "mature": 3,
        "evaluable": 2,
        "immature": 1,
        "censored_open": 1,
        "converted": 1,
        "missing": 1,
        "rate": 0.5,
    }
    values.update(overrides)
    return values


def _stages(**overrides: object) -> list[dict[str, object]]:
    return [_stage(stage, **overrides) for stage in ("screen", "interview", "offer")]


def _observed(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "key": "employee",
        "label": "Employee",
        "reached": 4,
        "mature": 2,
        "evaluable": 2,
        "successes": 1,
        "censored_open": 1,
        "immature": 1,
        "ambiguity_excluded": 1,
        "observed_rate": 0.5,
    }
    values.update(overrides)
    return values


def _rescue(position: int, **overrides: object) -> dict[str, object]:
    values = _observed(**overrides)
    values.pop("key")
    values.pop("label")
    values["position"] = position
    return values


def test_action_review_create_requires_exact_manual_confirmation() -> None:
    payload = ApplicationActionReviewCreate(
        decision="waiting",
        new_due_on=date(2026, 7, 22),
        confirm_current_action=True,
    )
    assert payload.decision.value == "waiting"
    assert payload.confirm_current_action is True

    for invalid in (False, 1, "true", None):
        with pytest.raises(ValidationError, match="boolean true"):
            ApplicationActionReviewCreate(
                decision="continue",
                new_due_on=date(2026, 7, 22),
                confirm_current_action=invalid,  # type: ignore[arg-type]
            )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ApplicationActionReviewCreate.model_validate(
            {
                "decision": "continue",
                "new_due_on": "2026-07-22",
                "confirm_current_action": True,
                "auto_reject": False,
            }
        )


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"action_item_id": "action-b"}, "exact same action"),
        ({"new_due_on": "2026-07-15"}, "strictly after"),
        ({"action_version": 3}, "increment exactly once"),
        ({"application_version": 3}, "increment exactly once"),
        ({"created_at": "2026-07-15T08:29:59Z"}, "cannot precede"),
    ],
)
def test_action_review_response_is_an_exact_single_version_reschedule(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        ApplicationActionReviewResponse.model_validate(_review(**overrides))


def test_action_review_mutation_resources_share_identity_and_versions() -> None:
    response = ApplicationActionReviewMutationResponse.model_validate(
        {
            "application": _application(),
            "action": _action(),
            "review": _review(),
            "mutation_created": True,
        }
    )
    assert response.application.id == response.review.application_id
    assert response.action.id == response.review.action_item_id

    invalid_cases = [
        {"application": _application(application_id="application-b")},
        {"action": _action(action_id="action-b")},
        {"action": _action(version=1)},
        {"application": _application(version=1)},
    ]
    base = {
        "application": _application(),
        "action": _action(),
        "review": _review(),
        "mutation_created": False,
    }
    for override in invalid_cases:
        candidate = deepcopy(base)
        candidate.update(override)
        with pytest.raises(ValidationError):
            ApplicationActionReviewMutationResponse.model_validate(candidate)

    for mismatch in (
        {**base, "action": _action(version=3), "mutation_created": True},
        {
            **base,
            "application": _application(version=3),
            "mutation_created": True,
        },
    ):
        with pytest.raises(ValidationError, match="current action"):
            ApplicationActionReviewMutationResponse.model_validate(mismatch)

    replay = ApplicationActionReviewMutationResponse.model_validate(
        {
            "application": _application(version=3, action_id="action-b"),
            "action": _action(version=2),
            "review": _review(),
            "mutation_created": False,
        }
    )
    assert replay.application.version == 3
    assert replay.action.id == "action-a"
    assert replay.application.current_action is not None
    assert replay.application.current_action.id == "action-b"


def test_stale_application_requires_one_exact_application_posting_action_graph() -> None:
    stale = WeeklyReviewStaleApplication.model_validate(
        {
            "application": _application(),
            "posting": _posting(),
            "current_action": _action(),
            "days_overdue": 3,
        }
    )
    assert stale.application.id == "application-a"

    with pytest.raises(ValidationError, match="current action"):
        WeeklyReviewStaleApplication.model_validate(
            {
                "application": _application(),
                "posting": _posting(),
                "current_action": {
                    **_action(),
                    "application_id": "application-b",
                },
                "days_overdue": 3,
            }
        )
    with pytest.raises(ValidationError, match="posting"):
        WeeklyReviewStaleApplication.model_validate(
            {
                "application": _application(),
                "posting": _posting(posting_id="posting-b"),
                "current_action": _action(),
                "days_overdue": 3,
            }
        )


def test_weekly_window_and_locked_policy_are_exact() -> None:
    window = WeeklyReviewWindow(starts_on=date(2026, 4, 23), ends_on=date(2026, 7, 15))
    policy = WeeklyReviewPolicy()
    assert (window.ends_on - window.starts_on).days == 83
    assert policy.observation_window_days == 84
    assert policy.application_maturity_days == 14
    assert policy.outreach_maturity_days == 14
    with pytest.raises(ValidationError, match="cannot follow"):
        WeeklyReviewWindow(
            starts_on=date(2026, 7, 16),
            ends_on=date(2026, 7, 15),
        )


def test_funnel_stage_partitions_mature_immature_missing_and_rate() -> None:
    metric = FunnelStageMetric.model_validate(_stage("screen"))
    assert metric.cohort_total == metric.mature + metric.immature
    assert metric.mature == metric.evaluable + metric.missing
    assert metric.rate == 0.5
    assert metric.late_converted == 0

    late = FunnelStageMetric.model_validate(
        _stage("screen", converted=0, late_converted=1, rate=0)
    )
    assert late.late_converted == 1
    assert late.rate == 0

    null_rate = FunnelStageMetric.model_validate(
        _stage(
            "screen",
            cohort_total=2,
            mature=1,
            evaluable=0,
            immature=1,
            censored_open=1,
            converted=0,
            missing=1,
            rate=None,
        )
    )
    assert null_rate.rate is None

    invalid = [
        {"cohort_total": 5},
        {"mature": 4},
        {"censored_open": 2},
        {"converted": 3},
        {"late_converted": 2},
        {"rate": 0.4},
        {
            "cohort_total": 2,
            "mature": 1,
            "evaluable": 0,
            "immature": 1,
            "censored_open": 1,
            "converted": 0,
            "missing": 1,
            "rate": 0,
        },
    ]
    for override in invalid:
        with pytest.raises(ValidationError):
            FunnelStageMetric.model_validate(_stage("screen", **override))


def test_funnel_segments_have_complete_ordered_stages_and_unique_dimensions() -> None:
    segment = FunnelSegmentMetric.model_validate(
        {
            "key": "referral",
            "label": "Referral",
            "cohort_total": 4,
            "missing": 0,
            "stages": _stages(),
        }
    )
    funnel = WeeklyReviewFunnel(
        overall=_stages(),
        by_acquisition_source=[segment],
        attribution_missing=1,
        assessment_missing=2,
    )
    assert funnel.by_acquisition_source[0].key == "referral"

    reversed_stages = list(reversed(_stages()))
    with pytest.raises(ValidationError, match="screen, interview, then offer"):
        FunnelSegmentMetric.model_validate(
            {
                "key": "referral",
                "label": "Referral",
                "cohort_total": 4,
                "missing": 0,
                "stages": reversed_stages,
            }
        )
    with pytest.raises(ValidationError, match="unique"):
        WeeklyReviewFunnel(
            overall=_stages(),
            by_acquisition_source=[segment, segment],
            attribution_missing=0,
            assessment_missing=0,
        )


def test_outreach_metric_partitions_ambiguity_and_uses_only_mature_denominator() -> None:
    metric = OutreachObservedMetric.model_validate(_observed())
    assert metric.reached == (
        metric.evaluable + metric.immature + metric.ambiguity_excluded
    )
    assert metric.observed_rate == metric.successes / metric.evaluable

    null_rate = OutreachObservedMetric.model_validate(
        _observed(
            reached=2,
            mature=0,
            evaluable=0,
            successes=0,
            censored_open=1,
            immature=1,
            ambiguity_excluded=1,
            observed_rate=None,
        )
    )
    assert null_rate.observed_rate is None

    invalid = [
        {"mature": 3},
        {"censored_open": 0},
        {"reached": 5},
        {"successes": 3},
        {"observed_rate": 0.25},
    ]
    for override in invalid:
        with pytest.raises(ValidationError):
            OutreachObservedMetric.model_validate(_observed(**override))


def test_contacts_two_through_five_are_exact_rescue_positions_and_noncausal() -> None:
    rescue = [OutreachRescueMetric.model_validate(_rescue(position)) for position in range(2, 6)]
    outreach = WeeklyReviewOutreach(
        contacts_two_through_five=rescue,
        unattributed_legacy_successes=2,
    )
    assert [item.position for item in outreach.contacts_two_through_five] == [2, 3, 4, 5]
    assert "not causal" in outreach.noncausal_label
    assert outreach.unattributed_legacy_successes == 2

    with pytest.raises(ValidationError, match="positions two through five"):
        WeeklyReviewOutreach(
            contacts_two_through_five=[
                _rescue(2),
                _rescue(3),
                _rescue(5),
                _rescue(4),
            ],
            unattributed_legacy_successes=0,
        )


def test_weekly_response_exposes_bounded_stale_page_with_complete_total() -> None:
    stale = {
        "application": _application(),
        "posting": _posting(),
        "current_action": _action(),
        "days_overdue": 3,
    }
    values = {
        "as_of": RECORDED_AT,
        "owner_timezone": "Asia/Kolkata",
        "owner_local_date": "2026-07-15",
        "window": {"starts_on": "2026-04-23", "ends_on": "2026-07-15"},
        "policy": {},
        "stale_application_total": 2,
        "stale_applications": [stale],
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
    response = WeeklyReviewResponse.model_validate(values)
    assert response.stale_application_total == 2
    assert len(response.stale_applications) == 1

    with pytest.raises(ValidationError, match="smaller than the page"):
        WeeklyReviewResponse.model_validate(
            {**values, "stale_application_total": 0}
        )
