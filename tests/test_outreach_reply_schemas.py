"""Strict contracts for exact manual outreach-reply attribution."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from job_hunt_agent.outreach_schemas import (
    OutreachReplyCreate,
    OutreachReplyRecordedTimelineEvent,
    OutreachReplyResponse,
    OutreachSentAttemptResponse,
)


NOW = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)
SENT_AT = NOW - timedelta(days=1)
REPLY_KINDS = {
    "reply_received",
    "useful_reply",
    "introduced",
    "referred",
    "declined",
    "do_not_contact",
}


def _reply(
    *,
    reply_id: str = "reply1",
    sent_event_id: str = "sent1",
    message_id: str = "message1",
    version_number: int = 2,
    message_kind: str = "initial",
    reply_kind: str = "reply_received",
    received_on: date = date(2026, 7, 21),
    recorded_at: datetime = NOW,
    note: str | None = None,
) -> OutreachReplyResponse:
    return OutreachReplyResponse(
        id=reply_id,
        marked_sent_event_id=sent_event_id,
        message_version_id=message_id,
        message_version_number=version_number,
        message_kind=message_kind,
        reply_kind=reply_kind,
        received_on=received_on,
        note=note,
        recorded_at=recorded_at,
    )


def test_reply_create_is_exact_confirmed_and_fail_closed() -> None:
    for kind in sorted(REPLY_KINDS):
        payload = OutreachReplyCreate.model_validate(
            {
                "marked_sent_event_id": "sent1",
                "reply_kind": kind,
                "received_on": "2026-07-21",
                "note": "  They asked for the job link.  ",
                "confirm_exact_sent_attempt": True,
            }
        )
        assert payload.reply_kind.value == kind
        assert payload.received_on == date(2026, 7, 21)

    for invalid in (False, 1, "true"):
        with pytest.raises(ValidationError):
            OutreachReplyCreate.model_validate(
                {
                    "marked_sent_event_id": "sent1",
                    "reply_kind": "reply_received",
                    "received_on": "2026-07-21",
                    "confirm_exact_sent_attempt": invalid,
                }
            )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        OutreachReplyCreate.model_validate(
            {
                "marked_sent_event_id": "sent1",
                "reply_kind": "reply_received",
                "received_on": "2026-07-21",
                "confirm_exact_sent_attempt": True,
                "message_version_id": "client-must-not-choose-the-derived-version",
            }
        )
    with pytest.raises(ValidationError):
        OutreachReplyCreate.model_validate(
            {
                "marked_sent_event_id": "sent1",
                "reply_kind": "no_reply",
                "received_on": "2026-07-21",
                "confirm_exact_sent_attempt": True,
            }
        )


def test_sent_attempt_exposes_exact_body_and_multiple_replies() -> None:
    first = _reply(note="They replied.")
    second = _reply(
        reply_id="reply2",
        reply_kind="useful_reply",
        recorded_at=NOW + timedelta(minutes=1),
    )

    attempt = OutreachSentAttemptResponse(
        marked_sent_event_id="sent1",
        message_version_id="message1",
        version_number=2,
        kind="initial",
        body="Exact immutable message body",
        channel="linkedin",
        sent_at=SENT_AT,
        sent_local_on=date(2026, 7, 20),
        replies=[first, second],
    )

    assert attempt.body == "Exact immutable message body"
    assert [item.id for item in attempt.replies] == ["reply1", "reply2"]
    assert all(
        item.marked_sent_event_id == attempt.marked_sent_event_id
        for item in attempt.replies
    )
    assert all(
        item.message_version_id == attempt.message_version_id
        for item in attempt.replies
    )


@pytest.mark.parametrize(
    "update",
    [
        {"marked_sent_event_id": "other-sent"},
        {"message_version_id": "other-message"},
        {"message_version_number": 3},
        {"message_kind": "follow_up"},
    ],
)
def test_sent_attempt_rejects_mismatched_reply_attribution(
    update: dict[str, object],
) -> None:
    reply = _reply().model_copy(update=update)
    with pytest.raises(ValidationError):
        OutreachSentAttemptResponse(
            marked_sent_event_id="sent1",
            message_version_id="message1",
            version_number=2,
            kind="initial",
            body="Exact immutable message body",
            channel="email",
            sent_at=SENT_AT,
            sent_local_on=date(2026, 7, 20),
            replies=[reply],
        )


def test_sent_attempt_rejects_duplicate_or_reverse_ordered_replies() -> None:
    first = _reply(recorded_at=NOW)
    with pytest.raises(ValidationError):
        OutreachSentAttemptResponse(
            marked_sent_event_id="sent1",
            message_version_id="message1",
            version_number=2,
            kind="initial",
            body="Exact immutable message body",
            channel="linkedin",
            sent_at=SENT_AT,
            sent_local_on=date(2026, 7, 20),
            replies=[first, first],
        )
    with pytest.raises(ValidationError):
        OutreachSentAttemptResponse(
            marked_sent_event_id="sent1",
            message_version_id="message1",
            version_number=2,
            kind="initial",
            body="Exact immutable message body",
            channel="linkedin",
            sent_at=SENT_AT,
            sent_local_on=date(2026, 7, 20),
            replies=[
                first,
                _reply(reply_id="reply2", recorded_at=NOW - timedelta(seconds=1)),
            ],
        )


def test_reply_recorded_timeline_carries_exact_flattened_attribution() -> None:
    event = OutreachReplyRecordedTimelineEvent(
        id="reply1",
        sequence_id="sequence1",
        event_type="reply_recorded",
        application_contact_id="applicationcontact1",
        marked_sent_event_id="sent1",
        message_version_id="message1",
        message_version_number=2,
        message_kind="follow_up",
        reply_kind="referred",
        received_on=date(2026, 7, 21),
        note="They connected me to the hiring manager.",
        occurred_at=NOW,
    )

    assert event.event_type == "reply_recorded"
    assert event.message_kind.value == "follow_up"
    assert event.reply_kind.value == "referred"
    assert event.marked_sent_event_id == "sent1"
