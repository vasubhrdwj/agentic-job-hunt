"""Contract coverage for repeatable application interview rounds."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from job_hunt_agent.interview_round_schemas import (
    ApplicationInterviewRoundsResponse,
    InterviewCancellationParty,
    InterviewMeetingFormat,
    InterviewRoundCreate,
    InterviewRoundEventCreate,
    InterviewRoundEventResponse,
    InterviewRoundEventType,
    InterviewRoundKind,
    InterviewRoundMutationResponse,
    InterviewRoundResponse,
    InterviewRoundStatus,
)


NOW = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)
SCHEDULED_AT = NOW + timedelta(days=2)


def _event(
    event_type: str = "scheduled",
    *,
    sequence: int | None = None,
    application_id: str = "application1",
    round_id: str = "round1",
    event_id: str | None = None,
    scheduled_at: datetime = SCHEDULED_AT,
    previous_action_id: str | None = None,
    action_id: str | None = None,
) -> dict[str, object]:
    sequence = sequence or (1 if event_type == "scheduled" else 2)
    terminal = event_type in {"completed", "cancelled"}
    return {
        "id": event_id or f"event{sequence}",
        "application_id": application_id,
        "interview_round_id": round_id,
        "sequence_number": sequence,
        "event_type": event_type,
        "from_status": None if event_type == "scheduled" else "scheduled",
        "to_status": (
            event_type if terminal else "scheduled"
        ),
        "scheduled_start_at": scheduled_at,
        "scheduled_timezone": "Asia/Kolkata",
        "duration_minutes": 60,
        "meeting_format": "video",
        "effective_on": date(2026, 7, 18) if terminal else None,
        "cancelled_by": "employer" if event_type == "cancelled" else None,
        "previous_action_item_id": previous_action_id or f"action{sequence - 1}",
        "action_item_id": action_id or f"action{sequence}",
        "recording_method": "manual",
        "occurred_at": NOW + timedelta(hours=sequence - 1),
        "created_at": NOW + timedelta(hours=sequence - 1),
    }


def _round(
    status: str = "scheduled",
    *,
    round_id: str = "round1",
    application_id: str = "application1",
    round_number: int = 1,
    rescheduled: bool = False,
) -> dict[str, object]:
    first = _event(
        round_id=round_id,
        application_id=application_id,
        event_id=f"{round_id}event1",
        action_id=f"{round_id}action1",
    )
    events = [first]
    if rescheduled:
        scheduled_at = SCHEDULED_AT + timedelta(days=1)
        events.append(
            _event(
                "rescheduled",
                round_id=round_id,
                application_id=application_id,
                event_id=f"{round_id}event2",
                scheduled_at=scheduled_at,
                previous_action_id=f"{round_id}action1",
                action_id=f"{round_id}action2",
            )
        )
    elif status in {"completed", "cancelled"}:
        events.append(
            _event(
                status,
                round_id=round_id,
                application_id=application_id,
                event_id=f"{round_id}event2",
                previous_action_id=f"{round_id}action1",
                action_id=f"{round_id}action2",
            )
        )
    latest = events[-1]
    return {
        "id": round_id,
        "version": len(events),
        "application_id": application_id,
        "application_submission_id": "submission1",
        "round_number": round_number,
        "kind": "technical",
        "title": "Technical interview",
        "status": status,
        "scheduled_start_at": latest["scheduled_start_at"],
        "scheduled_timezone": latest["scheduled_timezone"],
        "duration_minutes": latest["duration_minutes"],
        "meeting_format": latest["meeting_format"],
        "completed_on": date(2026, 7, 18) if status == "completed" else None,
        "cancelled_on": date(2026, 7, 18) if status == "cancelled" else None,
        "cancelled_by": "employer" if status == "cancelled" else None,
        "created_at": NOW,
        "updated_at": NOW + timedelta(hours=len(events) - 1),
        "events": events,
    }


def _application(
    *,
    stage: str = "interviewing",
    action_id: str = "round1action1",
    action_round_id: str | None = "round1",
) -> dict[str, object]:
    kind_by_stage = {
        "applied": "follow_up_application",
        "screening": "prepare_recruiter_screen",
        "interviewing": "prepare_interview",
        "offer": "review_offer",
    }
    return {
        "id": "application1",
        "version": 7,
        "opportunity_id": "opportunity1",
        "pursued_posting_version_id": "postingversion1",
        "stage": stage,
        "posting": {
            "id": "posting1",
            "company": "Example",
            "title": "Engineer",
            "canonical_url": "https://careers.example.com/jobs/1",
            "first_party": True,
            "state": "open",
        },
        "current_action": {
            "id": action_id,
            "version": 1,
            "application_id": "application1",
            "interview_round_id": action_round_id,
            "kind": (
                "prepare_interview"
                if action_round_id is not None
                else kind_by_stage[stage]
            ),
            "status": "open",
            "title": "Prepare for interview",
            "due_on": date(2026, 7, 18),
            "completed_at": None,
            "cancelled_at": None,
            "created_at": NOW,
            "updated_at": NOW,
        },
        "outcome": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def test_interview_round_enums_are_closed_and_explicit() -> None:
    assert {value.value for value in InterviewRoundKind} == {
        "hiring_manager",
        "technical",
        "system_design",
        "behavioral",
        "case_study",
        "panel",
        "final",
        "other",
    }
    assert {value.value for value in InterviewMeetingFormat} == {
        "video",
        "phone",
        "onsite",
        "unspecified",
    }
    assert {value.value for value in InterviewRoundStatus} == {
        "scheduled",
        "completed",
        "cancelled",
    }
    assert {value.value for value in InterviewRoundEventType} == {
        "scheduled",
        "rescheduled",
        "completed",
        "cancelled",
    }
    assert {value.value for value in InterviewCancellationParty} == {
        "employer",
        "candidate",
        "mutual",
        "unknown",
    }


def _schedule_payload() -> dict[str, object]:
    return {
        "kind": "technical",
        "title": "Technical interview",
        "scheduled_local": datetime(2026, 7, 18, 14, 0),
        "scheduled_timezone": "Asia/Kolkata",
        "duration_minutes": 60,
        "meeting_format": "video",
        "next_action_due_on": date(2026, 7, 18),
        "confirm_schedule": True,
    }


def test_schedule_payload_accepts_only_explicit_bounded_local_appointments() -> None:
    parsed = InterviewRoundCreate.model_validate(_schedule_payload())
    assert parsed.scheduled_local.tzinfo is None
    assert parsed.confirm_schedule is True

    invalid: list[dict[str, object]] = []
    for field, value in (
        ("scheduled_local", datetime(2026, 7, 18, 8, 30, tzinfo=timezone.utc)),
        ("scheduled_timezone", "Mars/Olympus_Mons"),
        ("duration_minutes", 14),
        ("duration_minutes", 481),
        ("confirm_schedule", False),
        ("confirm_schedule", 1),
        ("confirm_schedule", "true"),
    ):
        candidate = _schedule_payload()
        candidate[field] = value
        invalid.append(candidate)
    extra = _schedule_payload()
    extra["provider_event_id"] = "fabricated"
    invalid.append(extra)

    for candidate in invalid:
        with pytest.raises(ValidationError):
            InterviewRoundCreate.model_validate(candidate)


def test_event_write_union_is_discriminated_and_requires_exact_confirmation() -> None:
    adapter = TypeAdapter(InterviewRoundEventCreate)
    payloads = [
        {
            "event_type": "rescheduled",
            "scheduled_local": datetime(2026, 7, 19, 14, 0),
            "scheduled_timezone": "Asia/Kolkata",
            "duration_minutes": 75,
            "meeting_format": "onsite",
            "next_action_due_on": date(2026, 7, 19),
            "confirm_reschedule": True,
        },
        {
            "event_type": "completed",
            "completed_on": date(2026, 7, 18),
            "next_action_due_on": date(2026, 7, 19),
            "confirm_complete": True,
        },
        {
            "event_type": "cancelled",
            "cancelled_on": date(2026, 7, 17),
            "cancelled_by": "mutual",
            "next_action_due_on": date(2026, 7, 18),
            "confirm_cancel": True,
        },
    ]
    assert [adapter.validate_python(payload).event_type for payload in payloads] == [
        "rescheduled",
        "completed",
        "cancelled",
    ]

    for payload, confirmation in zip(
        payloads,
        ("confirm_reschedule", "confirm_complete", "confirm_cancel"),
        strict=True,
    ):
        for value in (False, 1, "true"):
            invalid = dict(payload)
            invalid[confirmation] = value
            with pytest.raises(ValidationError):
                adapter.validate_python(invalid)
    with pytest.raises(ValidationError):
        adapter.validate_python({"event_type": "scheduled"})
    incompatible = dict(payloads[1])
    incompatible["cancelled_by"] = "employer"
    with pytest.raises(ValidationError):
        adapter.validate_python(incompatible)


@pytest.mark.parametrize(
    "event_type",
    ["scheduled", "rescheduled", "completed", "cancelled"],
)
def test_event_response_accepts_each_exact_lifecycle_shape(event_type: str) -> None:
    event = InterviewRoundEventResponse.model_validate(_event(event_type))
    assert event.event_type.value == event_type


@pytest.mark.parametrize(
    ("event_type", "field", "value"),
    [
        ("scheduled", "sequence_number", 2),
        ("scheduled", "from_status", "scheduled"),
        ("rescheduled", "to_status", "completed"),
        ("completed", "effective_on", None),
        ("completed", "cancelled_by", "employer"),
        ("cancelled", "cancelled_by", None),
        ("cancelled", "to_status", "completed"),
    ],
)
def test_event_response_rejects_inconsistent_lifecycle_fields(
    event_type: str,
    field: str,
    value: object,
) -> None:
    payload = _event(event_type)
    payload[field] = value
    with pytest.raises(ValidationError):
        InterviewRoundEventResponse.model_validate(payload)


def test_event_response_rejects_action_reuse_and_backdated_creation() -> None:
    payload = _event()
    payload["action_item_id"] = payload["previous_action_item_id"]
    with pytest.raises(ValidationError, match="replace"):
        InterviewRoundEventResponse.model_validate(payload)

    payload = _event()
    payload["created_at"] = NOW - timedelta(seconds=1)
    with pytest.raises(ValidationError, match="precede"):
        InterviewRoundEventResponse.model_validate(payload)


@pytest.mark.parametrize(
    ("status", "rescheduled"),
    [("scheduled", False), ("scheduled", True), ("completed", False), ("cancelled", False)],
)
def test_round_response_accepts_full_current_projection(
    status: str,
    rescheduled: bool,
) -> None:
    parsed = InterviewRoundResponse.model_validate(
        _round(status, rescheduled=rescheduled)
    )
    assert parsed.version == len(parsed.events)
    assert parsed.events[-1].to_status.value == status


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(version=2), "version"),
        (
            lambda value: (
                value["events"].append(
                    _event("rescheduled", sequence=3, event_id="gappedevent")
                ),
                value.update(version=2),
            ),
            "sequence",
        ),
        (
            lambda value: value["events"][0].update(application_id="otherapp"),
            "belong",
        ),
        (
            lambda value: value.update(duration_minutes=90),
            "latest event",
        ),
        (
            lambda value: value.update(completed_on=date(2026, 7, 18)),
            "timestamps",
        ),
    ],
)
def test_round_response_rejects_projection_or_graph_drift(mutation, message: str) -> None:
    payload = _round()
    mutation(payload)
    with pytest.raises(ValidationError, match=message):
        InterviewRoundResponse.model_validate(payload)


def test_application_rounds_require_order_owner_and_exact_scheduled_action() -> None:
    scheduled = _round()
    valid = ApplicationInterviewRoundsResponse.model_validate(
        {"application": _application(), "rounds": [scheduled]}
    )
    assert valid.data_source == "database"

    second = _round(round_id="round2", round_number=2)
    application = _application(action_id="round2action1", action_round_id="round2")
    with pytest.raises(ValidationError, match="only one scheduled"):
        ApplicationInterviewRoundsResponse.model_validate(
            {"application": application, "rounds": [scheduled, second]}
        )

    completed = _round("completed", round_id="round2", round_number=2)
    with pytest.raises(ValidationError, match="ascending"):
        ApplicationInterviewRoundsResponse.model_validate(
            {
                "application": _application(),
                "rounds": [completed, scheduled],
            }
        )
    wrong_owner = _round(application_id="otherapp")
    with pytest.raises(ValidationError, match="belong"):
        ApplicationInterviewRoundsResponse.model_validate(
            {"application": _application(), "rounds": [wrong_owner]}
        )
    with pytest.raises(ValidationError, match="current action"):
        ApplicationInterviewRoundsResponse.model_validate(
            {
                "application": _application(action_round_id="round2"),
                "rounds": [scheduled],
            }
        )
    with pytest.raises(ValidationError, match="post-application"):
        ApplicationInterviewRoundsResponse.model_validate(
            {
                "application": _application(stage="offer", action_round_id=None),
                "rounds": [scheduled],
            }
        )

    terminal_only = ApplicationInterviewRoundsResponse.model_validate(
        {
            "application": _application(stage="offer", action_round_id=None),
            "rounds": [completed],
        }
    )
    assert terminal_only.rounds[0].status.value == "completed"


def test_mutation_response_requires_one_latest_application_round_event_graph() -> None:
    round_payload = _round()
    valid = InterviewRoundMutationResponse.model_validate(
        {
            "application": _application(),
            "round": round_payload,
            "event": round_payload["events"][-1],
            "mutation_created": True,
        }
    )
    assert valid.mutation_created is True

    stale_event = _event(
        round_id="round1",
        application_id="application1",
        event_id="staleevent",
    )
    with pytest.raises(ValidationError, match="do not match"):
        InterviewRoundMutationResponse.model_validate(
            {
                "application": _application(),
                "round": round_payload,
                "event": stale_event,
                "mutation_created": False,
            }
        )
