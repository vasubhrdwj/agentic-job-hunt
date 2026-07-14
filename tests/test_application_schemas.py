"""Focused validation tests for application and next-action contracts."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from job_hunt_agent.application_schemas import (
    ActionItemResponse,
    ApplicationActivityEventResponse,
    ApplicationActivityListResponse,
    ApplicationDetailResponse,
    ApplicationListResponse,
    ApplicationPostingSummary,
    ApplicationSummary,
    PursuitBundle,
)


NOW = datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)


def _posting(**updates: object) -> ApplicationPostingSummary:
    values: dict[str, object] = {
        "id": "posting1",
        "company": "Example",
        "title": "Senior Backend Engineer",
        "canonical_url": "https://careers.example.com/jobs/123",
        "first_party": True,
        "state": "open",
    }
    values.update(updates)
    return ApplicationPostingSummary.model_validate(values)


def _action(**updates: object) -> ActionItemResponse:
    values: dict[str, object] = {
        "id": "action1",
        "version": 1,
        "application_id": "application1",
        "kind": "review_and_prepare_application",
        "status": "open",
        "title": "Review the role and prepare the application",
        "due_on": date(2026, 7, 15),
        "completed_at": None,
        "cancelled_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(updates)
    return ActionItemResponse.model_validate(values)


def _application(**updates: object) -> ApplicationSummary:
    values: dict[str, object] = {
        "id": "application1",
        "version": 1,
        "opportunity_id": "opportunity1",
        "pursued_posting_version_id": "postingversion1",
        "stage": "pursuing",
        "posting": _posting(),
        "current_action": _action(),
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(updates)
    return ApplicationSummary.model_validate(values)


def _activity(**updates: object) -> ApplicationActivityEventResponse:
    values: dict[str, object] = {
        "id": "activity1",
        "application_id": "application1",
        "sequence_number": 1,
        "event_type": "application_created",
        "from_stage": None,
        "to_stage": "pursuing",
        "action_item_id": "action1",
        "occurred_at": NOW,
    }
    values.update(updates)
    return ApplicationActivityEventResponse.model_validate(values)


def _bundle(**updates: object) -> PursuitBundle:
    values: dict[str, object] = {
        "application": _application(),
        "activity": _activity(),
        "application_created": True,
    }
    values.update(updates)
    return PursuitBundle.model_validate(values)


def test_pursuit_bundle_is_one_practical_atomic_result() -> None:
    bundle = _bundle()

    assert bundle.application.stage.value == "pursuing"
    assert bundle.application.current_action.status.value == "open"
    assert bundle.application.current_action.due_on == date(2026, 7, 15)
    assert bundle.activity.event_type.value == "application_created"
    assert bundle.application_created is True


def test_action_lifecycle_is_exact_and_timestamped_in_utc() -> None:
    completed_at = NOW + timedelta(hours=1)
    completed = _action(
        status="completed",
        completed_at=completed_at.astimezone(
            timezone(timedelta(hours=5, minutes=30))
        ),
        updated_at=completed_at,
    )
    assert completed.completed_at == completed_at
    assert completed.completed_at is not None
    assert completed.completed_at.tzinfo is timezone.utc

    with pytest.raises(ValidationError, match="open actions"):
        _action(completed_at=completed_at)
    with pytest.raises(ValidationError, match="require completed_at"):
        _action(status="completed")
    with pytest.raises(ValidationError, match="require cancelled_at"):
        _action(status="cancelled")
    with pytest.raises(ValidationError, match="UTC offset"):
        _action(created_at=NOW.replace(tzinfo=None))


def test_pursuing_application_requires_its_own_open_current_action() -> None:
    with pytest.raises(ValidationError, match="belong"):
        _application(
            current_action=_action(application_id="differentapplication")
        )

    completed_at = NOW + timedelta(hours=1)
    with pytest.raises(ValidationError, match="open current_action"):
        _application(
            current_action=_action(
                status="completed",
                completed_at=completed_at,
                updated_at=completed_at,
            )
        )


@pytest.mark.parametrize(
    ("stage", "kind"),
    [
        ("pursuing", "review_and_prepare_application"),
        ("ready_to_apply", "submit_application"),
        ("applied", "follow_up_application"),
    ],
)
def test_application_stage_requires_its_matching_current_action_kind(
    stage: str,
    kind: str,
) -> None:
    application = _application(stage=stage, current_action=_action(kind=kind))

    assert application.current_action.kind.value == kind
    with pytest.raises(ValidationError, match="current_action"):
        _application(
            stage=stage,
            current_action=_action(
                kind=(
                    "submit_application"
                    if kind != "submit_application"
                    else "follow_up_application"
                )
            ),
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"sequence_number": 2},
        {"from_stage": "pursuing"},
        {"to_stage": None},
        {"action_item_id": None},
    ],
)
def test_creation_activity_has_one_immutable_shape(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="application_created"):
        _activity(**updates)


def test_pursuit_bundle_rejects_cross_resource_or_non_atomic_data() -> None:
    with pytest.raises(ValidationError, match="belong"):
        _bundle(activity=_activity(application_id="application2"))
    with pytest.raises(ValidationError, match="current action"):
        _bundle(activity=_activity(action_item_id="action2"))
    with pytest.raises(ValidationError, match="when the application is created"):
        _bundle(activity=_activity(occurred_at=NOW + timedelta(seconds=1)))


def test_application_responses_are_database_only_and_consistent() -> None:
    application = _application()
    activity = _activity()

    listing = ApplicationListResponse(
        items=[application],
        total=1,
        next_cursor="abc_123",
    )
    detail = ApplicationDetailResponse(
        application=application,
        activity=[activity],
    )
    timeline = ApplicationActivityListResponse(items=[activity])

    assert listing.data_source == "database"
    assert listing.next_cursor == "abc_123"
    assert detail.activity[0].id == "activity1"
    assert timeline.items[0].application_id == application.id

    with pytest.raises(ValidationError, match="literal_error"):
        ApplicationListResponse(
            data_source="provider",  # type: ignore[arg-type]
            items=[],
            total=0,
        )
    with pytest.raises(ValidationError, match="total"):
        ApplicationListResponse(items=[application], total=0)
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        ApplicationListResponse(items=[application], total=1, next_cursor="bad cursor")
    with pytest.raises(ValidationError, match="belong"):
        ApplicationDetailResponse(
            application=application,
            activity=[_activity(application_id="application2")],
        )


def test_contracts_reject_unsafe_urls_extra_fields_and_unbounded_titles() -> None:
    with pytest.raises(ValidationError, match="HTTPS URL"):
        _posting(canonical_url="javascript:alert(1)")
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ApplicationPostingSummary.model_validate(
            {
                **_posting().model_dump(),
                "provider_payload": {"secret": "must not leak"},
            }
        )
    with pytest.raises(ValidationError):
        _action(title="x" * 241)
