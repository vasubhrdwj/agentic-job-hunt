"""Focused contract tests for the manual staged outreach projection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from job_hunt_agent.outreach_schemas import (
    ApplicationOutreachResponse,
    MAX_OUTREACH_MESSAGE_CHARS,
    OutreachEventCreate,
    OutreachMessageCreate,
    OutreachMessageVersionResponse,
    OutreachRecipientResponse,
    OutreachSequenceResponse,
)


NOW = datetime(2026, 7, 14, 9, 0, tzinfo=timezone.utc)


def _message(
    *,
    version_id: str = "messageversion1",
    kind: str = "initial",
    sent: bool = False,
) -> OutreachMessageVersionResponse:
    return OutreachMessageVersionResponse(
        id=version_id,
        version_number=1,
        kind=kind,
        body="Hello — your work on the platform team stood out.",
        copied_at=NOW + timedelta(minutes=1) if sent else None,
        sent_at=NOW + timedelta(minutes=2) if sent else None,
        sent_channel="linkedin" if sent else None,
        created_at=NOW,
    )


def _sequence(**overrides: object) -> OutreachSequenceResponse:
    values: dict[str, object] = {
        "id": "sequence1",
        "version": 1,
        "application_id": "application1",
        "contact_plan_id": "contactplan1",
        "status": "active",
        "active_wave": 1,
        "reason": None,
        "manual_only": True,
        "started_at": NOW,
        "paused_at": None,
        "stopped_at": None,
        "completed_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return OutreachSequenceResponse(**values)


def _recipient(
    *,
    rank: int = 1,
    wave: int | None = None,
    initial: OutreachMessageVersionResponse | None = None,
    follow_up: OutreachMessageVersionResponse | None = None,
    no_reply_eligible_at: datetime | None = None,
) -> OutreachRecipientResponse:
    latest_sent_at = (
        follow_up.sent_at
        if follow_up is not None and follow_up.sent_at is not None
        else initial.sent_at
        if initial is not None
        else None
    )
    sent_attempts = [
        {
            "marked_sent_event_id": f"markedsentevent{rank}{message.kind.value}",
            "message_version_id": message.id,
            "version_number": message.version_number,
            "kind": message.kind,
            "body": message.body,
            "channel": message.sent_channel,
            "sent_at": message.sent_at,
            "sent_local_on": message.sent_at.date(),
        }
        for message in (initial, follow_up)
        if message is not None and message.sent_at is not None
    ]
    return OutreachRecipientResponse(
        sequence_id="sequence1",
        application_contact_id=f"applicationcontact{rank}",
        contact_id=f"contact{rank}",
        public_name=f"Person {rank}",
        profile_url=f"https://www.linkedin.com/in/person-{rank}",
        lifecycle="active",
        current_title="Staff Engineer",
        current_company="Example",
        category="team_peer",
        bench_rank=rank,
        wave=rank if wave is None else wave,
        bench_state="ready" if rank == 1 else "reserve",
        initial_message=initial,
        follow_up_message=follow_up,
        sent_attempts=sent_attempts,
        follow_up_due_at=(
            initial.sent_at + timedelta(days=4)
            if initial is not None and initial.sent_at is not None
            else None
        ),
        no_reply_eligible_at=(
            no_reply_eligible_at
            if no_reply_eligible_at is not None
            else latest_sent_at + timedelta(days=7)
            if latest_sent_at is not None
            else None
        ),
    )


def test_not_started_response_is_explicitly_database_only() -> None:
    response = ApplicationOutreachResponse(
        application_id="application1",
        status="not_started",
    )

    assert response.data_source == "database"
    assert response.sequence is None
    assert response.recipients == []
    assert response.timeline == []

    with pytest.raises(ValidationError):
        ApplicationOutreachResponse(
            application_id="application1",
            status="not_started",
            sequence=_sequence(),
            recipients=[_recipient()],
        )


def test_message_create_preserves_exact_whitespace_but_rejects_blank_text() -> None:
    body = "  Hello there.\n\nThank you.  "
    request = OutreachMessageCreate(
        application_contact_id="applicationcontact1",
        kind="initial",
        body=body,
    )

    assert request.body == body

    for invalid in ("", "  \n\t  ", "x" * (MAX_OUTREACH_MESSAGE_CHARS + 1)):
        with pytest.raises(ValidationError):
            OutreachMessageCreate(
                application_contact_id="applicationcontact1",
                kind="initial",
                body=invalid,
            )


def test_message_create_rejects_unknown_kinds_and_fields() -> None:
    with pytest.raises(ValidationError):
        OutreachMessageCreate(
            application_contact_id="applicationcontact1",
            kind="second_follow_up",
            body="Hello",
        )
    with pytest.raises(ValidationError):
        OutreachMessageCreate(
            application_contact_id="applicationcontact1",
            kind="follow_up",
            body="Following up once.",
            auto_send=True,
        )


def test_event_union_is_discriminated_and_send_confirmation_is_literal_true() -> None:
    adapter = TypeAdapter(OutreachEventCreate)

    copied = adapter.validate_python(
        {"event_type": "copied", "message_version_id": "messageversion1"}
    )
    assert copied.event_type == "copied"

    sent = adapter.validate_python(
        {
            "event_type": "marked_sent",
            "message_version_id": "messageversion1",
            "channel": "linkedin",
            "confirm_exact_version": True,
        }
    )
    assert sent.confirm_exact_version is True

    for confirmation in (False, 1, "true", None):
        with pytest.raises(ValidationError):
            adapter.validate_python(
                {
                    "event_type": "marked_sent",
                    "message_version_id": "messageversion1",
                    "channel": "linkedin",
                    "confirm_exact_version": confirmation,
                }
            )


@pytest.mark.parametrize(
    "outcome",
    [
        "no_reply",
        "unreachable",
    ],
)
def test_non_reply_manual_outcomes_validate(outcome: str) -> None:
    event = TypeAdapter(OutreachEventCreate).validate_python(
        {
            "event_type": "outcome",
            "application_contact_id": "applicationcontact1",
            "outcome": outcome,
        }
    )
    assert event.outcome.value == outcome


@pytest.mark.parametrize(
    "reply_outcome",
    ["declined", "useful_reply", "introduced", "referred", "do_not_contact"],
)
def test_reply_outcomes_require_the_exact_reply_contract(reply_outcome: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(OutreachEventCreate).validate_python(
            {
                "event_type": "outcome",
                "application_contact_id": "applicationcontact1",
                "outcome": reply_outcome,
            }
        )


def test_pause_resume_and_stop_require_a_bounded_visible_reason() -> None:
    adapter = TypeAdapter(OutreachEventCreate)

    for event_type in ("pause", "resume", "stop"):
        event = adapter.validate_python(
            {"event_type": event_type, "reason": "  Owner chose to wait.  "}
        )
        assert event.reason == "Owner chose to wait."
        for invalid_reason in ("", " \n ", "x" * 101):
            with pytest.raises(ValidationError):
                adapter.validate_python(
                    {"event_type": event_type, "reason": invalid_reason}
                )


def test_message_projection_requires_coherent_manual_timestamps_and_channel() -> None:
    with pytest.raises(ValidationError):
        OutreachMessageVersionResponse(
            id="messageversion1",
            version_number=1,
            kind="initial",
            body="Hello",
            sent_at=NOW,
            created_at=NOW,
        )
    with pytest.raises(ValidationError):
        OutreachMessageVersionResponse(
            id="messageversion1",
            version_number=1,
            kind="initial",
            body="Hello",
            copied_at=NOW + timedelta(minutes=2),
            sent_at=NOW + timedelta(minutes=1),
            sent_channel="email",
            created_at=NOW,
        )


def test_recipient_allows_only_one_follow_up_after_a_sent_initial() -> None:
    sent_initial = _message(sent=True)
    follow_up = _message(
        version_id="messageversion2",
        kind="follow_up",
    )
    recipient = _recipient(initial=sent_initial, follow_up=follow_up)

    assert recipient.initial_message is sent_initial
    assert recipient.follow_up_message is follow_up

    with pytest.raises(ValidationError):
        _recipient(initial=_message(sent=False), follow_up=follow_up)
    with pytest.raises(ValidationError):
        _recipient(initial=sent_initial, follow_up=_message(kind="initial"))


def test_recipient_no_reply_deadline_requires_and_follows_a_send() -> None:
    sent_initial = _message(sent=True)

    with pytest.raises(ValidationError):
        _recipient(no_reply_eligible_at=NOW + timedelta(days=7))
    with pytest.raises(ValidationError):
        _recipient(
            initial=sent_initial,
            no_reply_eligible_at=sent_initial.sent_at - timedelta(seconds=1),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "active", "active_wave": None},
        {"status": "active", "reason": "Unexpected reason"},
        {"status": "paused", "reason": None, "paused_at": NOW},
        {"status": "paused", "reason": "Waiting", "paused_at": None},
        {
            "status": "stopped",
            "active_wave": 1,
            "reason": "Referral received",
            "stopped_at": NOW,
        },
        {"status": "completed", "active_wave": None, "completed_at": None},
    ],
)
def test_sequence_status_requires_matching_wave_reason_and_timestamp(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _sequence(**overrides)


def test_sequence_requires_the_exact_manual_only_boolean() -> None:
    assert _sequence(manual_only=True).manual_only is True
    for invalid in (False, 1, "true", None):
        with pytest.raises(ValidationError):
            _sequence(manual_only=invalid)


def test_started_response_requires_one_ordered_owner_scoped_sequence() -> None:
    response = ApplicationOutreachResponse(
        application_id="application1",
        status="active",
        sequence=_sequence(),
        recipients=[_recipient(rank=1), _recipient(rank=2)],
        timeline=[
            {
                "id": "event1",
                "sequence_id": "sequence1",
                "event_type": "outcome_recorded",
                "application_contact_id": "applicationcontact1",
                "outcome": "useful_reply",
                "occurred_at": NOW,
            }
        ],
    )

    assert response.sequence is not None
    assert response.sequence.manual_only is True
    assert [recipient.wave for recipient in response.recipients] == [1, 2]

    with pytest.raises(ValidationError):
        ApplicationOutreachResponse(
            application_id="anotherapplication",
            status="active",
            sequence=_sequence(),
            recipients=[_recipient()],
        )
    with pytest.raises(ValidationError):
        ApplicationOutreachResponse(
            application_id="application1",
            status="active",
            sequence=_sequence(),
            recipients=[_recipient(rank=2)],
        )


def test_wave_one_can_hold_two_purposes_while_later_waves_are_single() -> None:
    response = ApplicationOutreachResponse(
        application_id="application1",
        status="active",
        sequence=_sequence(),
        recipients=[
            _recipient(rank=1, wave=1),
            _recipient(rank=4, wave=1),
            _recipient(rank=2, wave=2),
            _recipient(rank=3, wave=3),
        ],
    )

    assert [(item.wave, item.bench_rank) for item in response.recipients] == [
        (1, 1),
        (1, 4),
        (2, 2),
        (3, 3),
    ]

    with pytest.raises(ValidationError):
        ApplicationOutreachResponse(
            application_id="application1",
            status="active",
            sequence=_sequence(),
            recipients=[
                _recipient(rank=1, wave=1),
                _recipient(rank=2, wave=2),
                _recipient(rank=3, wave=2),
            ],
        )
    with pytest.raises(ValidationError):
        ApplicationOutreachResponse(
            application_id="application1",
            status="active",
            sequence=_sequence(),
            recipients=[
                _recipient(rank=1, wave=1),
                _recipient(rank=2, wave=3),
            ],
        )


def test_timeline_must_be_unique_ordered_and_reference_a_returned_recipient() -> None:
    base = {
        "application_id": "application1",
        "status": "active",
        "sequence": _sequence(updated_at=NOW + timedelta(minutes=3)),
        "recipients": [_recipient()],
    }
    with pytest.raises(ValidationError):
        ApplicationOutreachResponse(
            **base,
            timeline=[
                {
                    "id": "event1",
                    "sequence_id": "sequence1",
                    "event_type": "paused",
                    "reason": "Wait",
                    "occurred_at": NOW + timedelta(minutes=2),
                },
                {
                    "id": "event2",
                    "sequence_id": "sequence1",
                    "event_type": "resumed",
                    "reason": "Continue",
                    "occurred_at": NOW + timedelta(minutes=1),
                },
            ],
        )
    with pytest.raises(ValidationError):
        ApplicationOutreachResponse(
            **base,
            timeline=[
                {
                    "id": "event1",
                    "sequence_id": "sequence1",
                    "event_type": "outcome_recorded",
                    "application_contact_id": "missingcontact",
                    "outcome": "no_reply",
                    "occurred_at": NOW,
                }
            ],
        )


def test_contracts_reject_naive_timestamps_and_extra_response_fields() -> None:
    values = _sequence().model_dump()
    values["started_at"] = NOW.replace(tzinfo=None)
    with pytest.raises(ValidationError):
        OutreachSequenceResponse(**values)

    with pytest.raises(ValidationError):
        ApplicationOutreachResponse(
            application_id="application1",
            status="not_started",
            provider_status="ready",
        )
