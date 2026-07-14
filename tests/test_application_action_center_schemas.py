"""Focused contracts for the owner-local Today application action center."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from job_hunt_agent.application_schemas import (
    ActionItemResponse,
    ApplicationPostingSummary,
    TodayApplicationActionApplication,
    TodayApplicationActionGroup,
    TodayApplicationActionItem,
    TodayApplicationActionsResponse,
)


NOW = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)
LOCAL_TODAY = date(2026, 7, 15)


def _item(
    suffix: str,
    *,
    due_on: date,
    stage: str = "pursuing",
    kind: str = "review_and_prepare_application",
    action_application_id: str | None = None,
    posting_id: str | None = None,
    status: str = "open",
    created_at: datetime = NOW,
) -> TodayApplicationActionItem:
    application_id = f"application{suffix}"
    job_posting_id = f"posting{suffix}"
    lifecycle: dict[str, object] = {}
    if status == "completed":
        lifecycle["completed_at"] = NOW + timedelta(hours=1)
    elif status == "cancelled":
        lifecycle["cancelled_at"] = NOW + timedelta(hours=1)
    return TodayApplicationActionItem(
        application=TodayApplicationActionApplication(
            id=application_id,
            version=2,
            opportunity_id=f"opportunity{suffix}",
            job_posting_id=job_posting_id,
            pursued_posting_version_id=f"postingversion{suffix}",
            stage=stage,
        ),
        posting=ApplicationPostingSummary(
            id=posting_id or job_posting_id,
            company="Example",
            title=f"Backend Engineer {suffix}",
            canonical_url=f"https://careers.example.com/jobs/{suffix}",
            first_party=True,
            state="open",
        ),
        action=ActionItemResponse(
            id=f"action{suffix}",
            version=1,
            application_id=action_application_id or application_id,
            kind=kind,
            status=status,
            title="Take the next application step",
            due_on=due_on,
            completed_at=lifecycle.get("completed_at"),
            cancelled_at=lifecycle.get("cancelled_at"),
            created_at=created_at,
            updated_at=created_at,
        ),
    )


def _group(items: list[TodayApplicationActionItem]) -> TodayApplicationActionGroup:
    return TodayApplicationActionGroup(total=len(items), items=items)


def _response(
    *,
    overdue: list[TodayApplicationActionItem] | None = None,
    today: list[TodayApplicationActionItem] | None = None,
    next_7_days: list[TodayApplicationActionItem] | None = None,
    window_ends_on: date = LOCAL_TODAY + timedelta(days=7),
) -> TodayApplicationActionsResponse:
    return TodayApplicationActionsResponse(
        as_of=NOW,
        owner_timezone="Asia/Kolkata",
        owner_local_date=LOCAL_TODAY,
        window_ends_on=window_ends_on,
        overdue=_group(overdue or []),
        today=_group(today or []),
        next_7_days=_group(next_7_days or []),
    )


def test_action_center_accepts_exact_owner_local_bucket_boundaries() -> None:
    response = _response(
        overdue=[_item("overdue", due_on=LOCAL_TODAY - timedelta(days=1))],
        today=[_item("today", due_on=LOCAL_TODAY)],
        next_7_days=[
            _item("tomorrow", due_on=LOCAL_TODAY + timedelta(days=1)),
            _item("dayseven", due_on=LOCAL_TODAY + timedelta(days=7)),
        ],
    )

    assert response.data_source == "database"
    assert response.window_ends_on == date(2026, 7, 22)
    assert response.overdue.total == 1
    assert response.today.items[0].action.due_on == LOCAL_TODAY
    assert [item.action.due_on for item in response.next_7_days.items] == [
        date(2026, 7, 16),
        date(2026, 7, 22),
    ]


@pytest.mark.parametrize(
    ("bucket", "due_on"),
    [
        ("overdue", LOCAL_TODAY),
        ("today", LOCAL_TODAY - timedelta(days=1)),
        ("today", LOCAL_TODAY + timedelta(days=1)),
        ("next_7_days", LOCAL_TODAY),
        ("next_7_days", LOCAL_TODAY + timedelta(days=8)),
    ],
)
def test_action_center_rejects_items_outside_their_exact_bucket(
    bucket: str,
    due_on: date,
) -> None:
    with pytest.raises(ValidationError, match=bucket):
        _response(**{bucket: [_item(f"bad{bucket}", due_on=due_on)]})


def test_action_center_requires_an_exact_seven_day_window() -> None:
    with pytest.raises(ValidationError, match="window_ends_on"):
        _response(window_ends_on=LOCAL_TODAY + timedelta(days=8))


@pytest.mark.parametrize(
    ("stage", "kind"),
    [
        ("pursuing", "review_and_prepare_application"),
        ("ready_to_apply", "submit_application"),
        ("applied", "follow_up_application"),
        ("screening", "prepare_recruiter_screen"),
        ("interviewing", "prepare_interview"),
        ("offer", "review_offer"),
    ],
)
def test_action_center_accepts_every_active_stage_with_its_exact_action_kind(
    stage: str,
    kind: str,
) -> None:
    item = _item(stage, due_on=LOCAL_TODAY, stage=stage, kind=kind)

    assert item.application.stage.value == stage
    assert item.action.kind.value == kind


def test_action_item_rejects_cross_resource_closed_or_non_open_links() -> None:
    with pytest.raises(ValidationError, match="application"):
        _item(
            "crossapplication",
            due_on=LOCAL_TODAY,
            action_application_id="anotherapplication",
        )
    with pytest.raises(ValidationError, match="posting"):
        _item("crossposting", due_on=LOCAL_TODAY, posting_id="anotherposting")
    with pytest.raises(ValidationError, match="open"):
        _item("completed", due_on=LOCAL_TODAY, status="completed")
    with pytest.raises(ValidationError, match="action kind"):
        _item(
            "wrongkind",
            due_on=LOCAL_TODAY,
            stage="offer",
            kind="prepare_interview",
        )
    with pytest.raises(ValidationError, match="closed"):
        TodayApplicationActionApplication(
            id="closedapplication",
            version=1,
            opportunity_id="closedopportunity",
            job_posting_id="closedposting",
            pursued_posting_version_id="closedversion",
            stage="closed",
        )


def test_groups_preserve_full_totals_but_reject_impossible_or_duplicate_rows() -> None:
    first = _item("first", due_on=LOCAL_TODAY)
    truncated = TodayApplicationActionGroup(total=4, items=[first])
    assert truncated.total == 4
    assert len(truncated.items) == 1

    with pytest.raises(ValidationError, match="total"):
        TodayApplicationActionGroup(total=0, items=[first])
    with pytest.raises(ValidationError, match="duplicate"):
        TodayApplicationActionGroup(total=2, items=[first, first])

    too_many = [
        _item(str(index), due_on=LOCAL_TODAY)
        for index in range(51)
    ]
    with pytest.raises(ValidationError):
        TodayApplicationActionGroup(total=51, items=too_many)


def test_response_rejects_global_duplicate_application_or_action_ids() -> None:
    overdue = _item("duplicate", due_on=LOCAL_TODAY - timedelta(days=1))
    today = _item("duplicate", due_on=LOCAL_TODAY)

    with pytest.raises(ValidationError, match="duplicate"):
        _response(overdue=[overdue], today=[today])


def test_each_bucket_requires_stable_due_created_and_id_order() -> None:
    earliest_due = _item(
        "laterid",
        due_on=LOCAL_TODAY + timedelta(days=1),
        created_at=NOW,
    )
    earlier_created = _item(
        "z",
        due_on=LOCAL_TODAY + timedelta(days=2),
        created_at=NOW,
    )
    later_created = _item(
        "a",
        due_on=LOCAL_TODAY + timedelta(days=2),
        created_at=NOW + timedelta(minutes=1),
    )
    sorted_items = [earliest_due, earlier_created, later_created]
    response = _response(next_7_days=sorted_items)
    assert response.next_7_days.items == sorted_items

    with pytest.raises(ValidationError, match="sorted"):
        _response(next_7_days=[later_created, earliest_due, earlier_created])

    id_first = _item(
        "a1",
        due_on=LOCAL_TODAY + timedelta(days=3),
        created_at=NOW,
    )
    id_second = _item(
        "b1",
        due_on=LOCAL_TODAY + timedelta(days=3),
        created_at=NOW,
    )
    with pytest.raises(ValidationError, match="sorted"):
        _response(next_7_days=[id_second, id_first])
