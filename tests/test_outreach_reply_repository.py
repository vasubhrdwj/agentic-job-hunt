"""Focused repository policy tests for exact-attempt outreach replies."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    ApplicationContact,
    Contact,
    JobPosting,
    OutreachEvent,
    OutreachReply,
    OutreachSequence,
    Owner,
)
from job_hunt_agent.mutation_receipts import MutationIdempotencyConflict
from job_hunt_agent.outreach_repository import (
    load_application_outreach,
    record_outreach_reply,
)
from job_hunt_agent.outreach_schemas import (
    ApplicationOutreachResponse,
    OutreachCopiedEventCreate,
    OutreachMarkedSentEventCreate,
    OutreachOutcomeEventCreate,
    OutreachReplyCreate,
)
from job_hunt_agent.repository_errors import ResourceConflict, VersionConflict
from job_hunt_agent.security import DataKeyring
from tests.test_outreach_repository import (
    APPLICATION_ID,
    NOW,
    OWNER_ID,
    _record,
    _recipient,
    _save,
    _save_copy_send_initial,
    _sequence,
    _start,
    keyring,
    outreach_db,
)


def _payload(
    response: ApplicationOutreachResponse,
    *,
    recipient_id: str = "application-contact-1",
    attempt_kind: str = "initial",
    reply_kind: str = "reply_received",
    received_on: date | None = None,
    note: str | None = None,
    marked_sent_event_id: str | None = None,
) -> OutreachReplyCreate:
    attempt = next(
        item
        for item in _recipient(response, recipient_id).sent_attempts
        if item.kind.value == attempt_kind
    )
    return OutreachReplyCreate(
        marked_sent_event_id=(
            attempt.marked_sent_event_id
            if marked_sent_event_id is None
            else marked_sent_event_id
        ),
        reply_kind=reply_kind,
        received_on=attempt.sent_local_on if received_on is None else received_on,
        note=note,
        confirm_exact_sent_attempt=True,
    )


def _record_reply(
    database: Database,
    keyring: DataKeyring,
    response: ApplicationOutreachResponse,
    *,
    payload: OutreachReplyCreate,
    key: str,
    now: datetime,
    owner_id: str = OWNER_ID,
    application_id: str = APPLICATION_ID,
    expected_version: int | None = None,
) -> ApplicationOutreachResponse | None:
    sequence = _sequence(response)
    with database.session() as session:
        return record_outreach_reply(
            session,
            owner_id=owner_id,
            application_id=application_id,
            sequence_id=sequence.id,
            payload=payload,
            expected_sequence_version=(
                sequence.version if expected_version is None else expected_version
            ),
            idempotency_key=key,
            keyring=keyring,
            now=now,
        )


def _send_follow_up(
    database: Database,
    keyring: DataKeyring,
    response: ApplicationOutreachResponse,
) -> tuple[ApplicationOutreachResponse, datetime]:
    due = _recipient(response, "application-contact-1").follow_up_due_at
    assert due is not None
    response = _save(
        database,
        keyring,
        response,
        recipient_id="application-contact-1",
        kind="follow_up",
        body="Exact immutable follow-up body",
        key="reply-test-follow-up-save",
        now=due - timedelta(minutes=2),
    )
    message = _recipient(response, "application-contact-1").follow_up_message
    assert message is not None
    response = _record(
        database,
        keyring,
        response,
        payload=OutreachCopiedEventCreate(
            event_type="copied",
            message_version_id=message.id,
        ),
        key="reply-test-follow-up-copy",
        now=due - timedelta(minutes=1),
    )
    response = _record(
        database,
        keyring,
        response,
        payload=OutreachMarkedSentEventCreate(
            event_type="marked_sent",
            message_version_id=message.id,
            channel="email",
            confirm_exact_version=True,
        ),
        key="reply-test-follow-up-send",
        now=due,
    )
    return response, due


def test_exact_initial_and_follow_up_attribution_multiple_replies_and_note_privacy(
    outreach_db: Database,
    keyring: DataKeyring,
) -> None:
    response = _save_copy_send_initial(
        outreach_db,
        keyring,
        _start(outreach_db, keyring),
    )
    response, due = _send_follow_up(outreach_db, keyring, response)
    private_note = "  PRIVATE reply context\nwith exact spacing  "

    for payload, key, recorded_at in (
        (
            _payload(response, note=private_note),
            "initial-reply-one",
            due + timedelta(hours=1),
        ),
        (
            _payload(response, reply_kind="useful_reply"),
            "initial-reply-two",
            due + timedelta(hours=2),
        ),
        (
            _payload(
                response,
                attempt_kind="follow_up",
                reply_kind="declined",
            ),
            "follow-up-reply",
            due + timedelta(hours=3),
        ),
    ):
        recorded = _record_reply(
            outreach_db,
            keyring,
            response,
            payload=payload,
            key=key,
            now=recorded_at,
        )
        assert recorded is not None
        response = recorded

    attempts = _recipient(response, "application-contact-1").sent_attempts
    assert [(item.kind.value, item.body) for item in attempts] == [
        ("initial", "Exact initial message"),
        ("follow_up", "Exact immutable follow-up body"),
    ]
    assert [reply.reply_kind.value for reply in attempts[0].replies] == [
        "reply_received",
        "useful_reply",
    ]
    assert [reply.reply_kind.value for reply in attempts[1].replies] == ["declined"]
    assert attempts[0].replies[0].note == private_note
    assert {
        event.message_kind.value
        for event in response.timeline
        if event.event_type == "reply_recorded"
    } == {"initial", "follow_up"}

    with outreach_db.session() as session:
        rows = list(session.scalars(select(OutreachReply)))
        fresh = load_application_outreach(
            session,
            owner_id=OWNER_ID,
            application_id=APPLICATION_ID,
            keyring=keyring,
        )
    assert len(rows) == 3
    noted = next(row for row in rows if row.encrypted_note is not None)
    assert noted.note_key_id == "test-v1"
    assert private_note not in noted.encrypted_note
    assert fresh is not None
    assert (
        _recipient(fresh, "application-contact-1")
        .sent_attempts[0]
        .replies[0]
        .note
        == private_note
    )


def test_owner_local_date_bounds_wrong_attempt_and_graph_isolation(
    outreach_db: Database,
    keyring: DataKeyring,
) -> None:
    sent_at = datetime(2026, 7, 13, 18, 45, tzinfo=timezone.utc)
    response = _save_copy_send_initial(
        outreach_db,
        keyring,
        _start(outreach_db, keyring),
        sent_at=sent_at,
    )
    attempt = _recipient(response, "application-contact-1").sent_attempts[0]
    assert attempt.sent_local_on == date(2026, 7, 14)
    now = datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc)

    with pytest.raises(ResourceConflict, match="cannot precede"):
        _record_reply(
            outreach_db,
            keyring,
            response,
            payload=_payload(response, received_on=date(2026, 7, 13)),
            key="reply-before-send",
            now=now,
        )
    with pytest.raises(ResourceConflict, match="owner's future"):
        _record_reply(
            outreach_db,
            keyring,
            response,
            payload=_payload(response, received_on=date(2026, 7, 15)),
            key="reply-in-future",
            now=now,
        )

    with outreach_db.session() as session:
        copied_id = session.scalar(
            select(OutreachEvent.id).where(OutreachEvent.event_type == "copied")
        )
    assert copied_id is not None
    with pytest.raises(ResourceConflict, match="sent attempt"):
        _record_reply(
            outreach_db,
            keyring,
            response,
            payload=_payload(response, marked_sent_event_id=copied_id),
            key="reply-to-copy",
            now=now,
        )

    assert _record_reply(
        outreach_db,
        keyring,
        response,
        payload=_payload(response),
        key="foreign-owner-reply",
        now=now,
        owner_id="owner-b",
    ) is None
    assert _record_reply(
        outreach_db,
        keyring,
        response,
        payload=_payload(response),
        key="foreign-application-reply",
        now=now,
        application_id="application-missing",
    ) is None

    accepted = _record_reply(
        outreach_db,
        keyring,
        response,
        payload=_payload(response, received_on=date(2026, 7, 14)),
        key="reply-local-today",
        now=now,
    )
    assert accepted is not None


def test_owner_timezone_change_does_not_invalidate_historical_reply_date(
    outreach_db: Database,
    keyring: DataKeyring,
) -> None:
    sent_at = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    response = _save_copy_send_initial(
        outreach_db,
        keyring,
        _start(outreach_db, keyring),
        sent_at=sent_at,
    )
    original_attempt = _recipient(
        response, "application-contact-1"
    ).sent_attempts[0]
    assert original_attempt.sent_local_on == date(2026, 7, 13)
    recorded = _record_reply(
        outreach_db,
        keyring,
        response,
        payload=_payload(response, received_on=date(2026, 7, 13)),
        key="reply-before-timezone-change",
        now=sent_at + timedelta(hours=1),
    )
    assert recorded is not None

    with outreach_db.session() as session:
        owner = session.get(Owner, OWNER_ID)
        assert owner is not None
        owner.timezone = "Pacific/Kiritimati"
    with outreach_db.session() as session:
        fresh = load_application_outreach(
            session,
            owner_id=OWNER_ID,
            application_id=APPLICATION_ID,
            keyring=keyring,
        )

    assert fresh is not None
    projected = _recipient(fresh, "application-contact-1").sent_attempts[0]
    assert projected.sent_local_on == date(2026, 7, 14)
    assert projected.replies[0].received_on == date(2026, 7, 13)


def test_reply_idempotency_exact_replay_payload_conflict_and_stale_version(
    outreach_db: Database,
    keyring: DataKeyring,
) -> None:
    response = _save_copy_send_initial(
        outreach_db,
        keyring,
        _start(outreach_db, keyring),
    )
    original_version = _sequence(response).version
    payload = _payload(response, note="One exact note")
    first = _record_reply(
        outreach_db,
        keyring,
        response,
        payload=payload,
        key="reply-replay",
        now=NOW + timedelta(hours=1),
    )
    assert first is not None

    replay = _record_reply(
        outreach_db,
        keyring,
        response,
        payload=payload,
        key="reply-replay",
        now=NOW + timedelta(hours=2),
        expected_version=original_version,
    )
    assert replay is not None
    assert _sequence(replay).version == _sequence(first).version

    with pytest.raises(MutationIdempotencyConflict):
        _record_reply(
            outreach_db,
            keyring,
            response,
            payload=_payload(response, reply_kind="declined"),
            key="reply-replay",
            now=NOW + timedelta(hours=3),
            expected_version=original_version,
        )
    with pytest.raises(VersionConflict):
        _record_reply(
            outreach_db,
            keyring,
            response,
            payload=payload,
            key="reply-stale-version",
            now=NOW + timedelta(hours=3),
            expected_version=original_version,
        )
    with outreach_db.session() as session:
        assert session.scalar(select(func.count(OutreachReply.id))) == 1


def test_late_reply_after_no_reply_and_dnc_remains_factual_and_terminal_safe(
    outreach_db: Database,
    keyring: DataKeyring,
) -> None:
    response = _save_copy_send_initial(
        outreach_db,
        keyring,
        _start(outreach_db, keyring),
    )
    eligible_at = _recipient(
        response, "application-contact-1"
    ).no_reply_eligible_at
    assert eligible_at is not None
    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachOutcomeEventCreate(
            event_type="outcome",
            application_contact_id="application-contact-1",
            outcome="no_reply",
        ),
        key="late-reply-no-reply",
        now=eligible_at,
    )

    reply = _record_reply(
        outreach_db,
        keyring,
        response,
        payload=_payload(response, reply_kind="reply_received"),
        key="late-reply-arrived",
        now=eligible_at + timedelta(days=1),
    )
    assert reply is not None
    assert _recipient(reply, "application-contact-1").outcome.value == "no_reply"
    assert len(_recipient(reply, "application-contact-1").sent_attempts[0].replies) == 1

    stopped = _record_reply(
        outreach_db,
        keyring,
        reply,
        payload=_payload(reply, reply_kind="do_not_contact"),
        key="late-reply-dnc",
        now=eligible_at + timedelta(days=2),
    )
    assert stopped is not None
    assert stopped.status.value == "stopped"
    stopped_version = _sequence(stopped).version
    later = _record_reply(
        outreach_db,
        keyring,
        stopped,
        payload=_payload(stopped, reply_kind="reply_received"),
        key="reply-after-stopped",
        now=eligible_at + timedelta(days=3),
    )
    assert later is not None
    assert later.status.value == "stopped"
    assert _sequence(later).version == stopped_version + 1
    with outreach_db.session() as session:
        contact = session.get(Contact, "contact-1")
        assert contact is not None
        assert contact.lifecycle == "do_not_contact"
        assert session.scalar(select(func.count(OutreachReply.id))) == 3


def test_reply_on_completed_or_newly_closed_sequence_never_reopens_outreach(
    outreach_db: Database,
    keyring: DataKeyring,
) -> None:
    response = _save_copy_send_initial(
        outreach_db,
        keyring,
        _start(outreach_db, keyring),
    )
    completed_at = NOW + timedelta(hours=1)
    with outreach_db.session() as session:
        sequence = session.get(OutreachSequence, _sequence(response).id)
        assert sequence is not None
        sequence.status = "completed"
        sequence.active_wave = None
        sequence.reason_code = None
        sequence.completed_at = completed_at
        sequence.version += 1
        sequence.updated_at = completed_at
        for recipient in session.scalars(select(ApplicationContact)):
            recipient.bench_state = "stopped"
            recipient.updated_at = completed_at
    with outreach_db.session() as session:
        completed = load_application_outreach(
            session,
            owner_id=OWNER_ID,
            application_id=APPLICATION_ID,
            keyring=keyring,
        )
    assert completed is not None and completed.status.value == "completed"
    late = _record_reply(
        outreach_db,
        keyring,
        completed,
        payload=_payload(completed),
        key="reply-after-completed",
        now=completed_at + timedelta(hours=1),
    )
    assert late is not None and late.status.value == "completed"

    # Reopen only the fixture state to exercise the automatic posting-closure
    # guard on a separate reply; the reply itself must stop, never reactivate.
    with outreach_db.session() as session:
        sequence = session.get(OutreachSequence, _sequence(late).id)
        posting = session.get(JobPosting, "posting-a")
        assert sequence is not None and posting is not None
        sequence.status = "active"
        sequence.active_wave = 1
        sequence.completed_at = None
        sequence.version += 1
        sequence.updated_at = completed_at + timedelta(hours=2)
        posting.lifecycle_state = "closed"
        posting.closure_reason = "explicit"
        posting.closed_at = completed_at + timedelta(hours=2)
        posting.version += 1
        posting.updated_at = completed_at + timedelta(hours=2)
    with outreach_db.session() as session:
        closed = load_application_outreach(
            session,
            owner_id=OWNER_ID,
            application_id=APPLICATION_ID,
            keyring=keyring,
        )
    assert closed is not None
    closed_reply = _record_reply(
        outreach_db,
        keyring,
        closed,
        payload=_payload(closed, reply_kind="useful_reply"),
        key="reply-after-posting-close",
        now=completed_at + timedelta(hours=3),
    )
    assert closed_reply is not None
    assert closed_reply.status.value == "stopped"
    assert _sequence(closed_reply).reason == "posting_closed"
