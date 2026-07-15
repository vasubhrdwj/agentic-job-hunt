"""Relational invariants for replies attributed to exact outreach sends."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from job_hunt_agent.database import Database
from job_hunt_agent.models import OutreachReply
from tests.test_contact_models import NOW, contact_db
from tests.test_outreach_models import (
    _event,
    _seed_sequence_and_message,
    outreach_db,
)


def _reply(
    *,
    reply_id: str = "reply-a",
    hash_character: str = "a",
    **overrides: object,
) -> OutreachReply:
    values: dict[str, object] = {
        "id": reply_id,
        "owner_id": "owner-a",
        "application_id": "application-a",
        "outreach_sequence_id": "outreach-sequence-a",
        "application_contact_id": "application-contact-a",
        "marked_sent_event_id": "event-sent",
        "marked_sent_event_type": "marked_sent",
        "message_version_id": "message-a",
        "message_kind": "initial",
        "reply_kind": "reply_received",
        "received_on": NOW.date(),
        "encrypted_note": None,
        "note_key_id": None,
        "recording_method": "manual",
        "recorded_at": NOW + timedelta(minutes=5),
        "idempotency_key_hash": hash_character * 64,
    }
    values.update(overrides)
    return OutreachReply(**values)


def _seed_exact_send(database: Database) -> None:
    _seed_sequence_and_message(database)
    with database.session() as session:
        session.add(
            _event(
                event_id="event-sent",
                sequence_number=1,
                event_type="marked_sent",
                hash_character="1",
                application_contact_id="application-contact-a",
                message_version_id="message-a",
                kind="initial",
                channel="linkedin",
                follow_up_due_at=NOW + timedelta(days=5),
            )
        )


def test_multiple_replies_can_identify_one_exact_sent_attempt(
    outreach_db: Database,
) -> None:
    _seed_exact_send(outreach_db)

    with outreach_db.session() as session:
        session.add_all(
            [
                _reply(
                    encrypted_note="ciphertext-private-note",
                    note_key_id="key-1",
                ),
                _reply(
                    reply_id="reply-b",
                    hash_character="b",
                    reply_kind="referred",
                    received_on=(NOW + timedelta(days=1)).date(),
                    recorded_at=NOW + timedelta(days=6),
                ),
            ]
        )

    with outreach_db.session() as session:
        rows = list(
            session.scalars(
                select(OutreachReply).order_by(OutreachReply.recorded_at)
            )
        )

    assert [row.reply_kind for row in rows] == ["reply_received", "referred"]
    assert {row.marked_sent_event_id for row in rows} == {"event-sent"}
    assert rows[0].encrypted_note == "ciphertext-private-note"
    assert rows[0].note_key_id == "key-1"


@pytest.mark.parametrize(
    "overrides",
    [
        {"owner_id": "owner-b"},
        {"application_id": "application-missing"},
        {"outreach_sequence_id": "sequence-missing"},
        {"application_contact_id": "contact-missing"},
        {"marked_sent_event_id": "event-missing"},
        {"marked_sent_event_type": "copied"},
        {"message_version_id": "message-missing"},
        {"message_kind": "follow_up"},
    ],
)
def test_reply_graph_and_exact_sent_target_fail_closed(
    outreach_db: Database,
    overrides: dict[str, object],
) -> None:
    _seed_exact_send(outreach_db)

    with pytest.raises(IntegrityError):
        with outreach_db.session() as session:
            session.add(_reply(**overrides))


def test_non_sent_event_cannot_be_used_as_reply_target(
    outreach_db: Database,
) -> None:
    _seed_exact_send(outreach_db)
    with outreach_db.session() as session:
        session.add(
            _event(
                event_id="event-copied",
                sequence_number=2,
                event_type="copied",
                hash_character="2",
                application_contact_id="application-contact-a",
                message_version_id="message-a",
                kind="initial",
            )
        )

    with pytest.raises(IntegrityError):
        with outreach_db.session() as session:
            session.add(_reply(marked_sent_event_id="event-copied"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"reply_kind": "no_reply"},
        {"encrypted_note": "ciphertext", "note_key_id": None},
        {"encrypted_note": None, "note_key_id": "key-1"},
        {"recording_method": "automatic"},
        {"idempotency_key_hash": "short"},
    ],
)
def test_reply_enums_privacy_envelope_and_manual_audit_fields_are_enforced(
    outreach_db: Database,
    overrides: dict[str, object],
) -> None:
    _seed_exact_send(outreach_db)

    with pytest.raises(IntegrityError):
        with outreach_db.session() as session:
            session.add(_reply(**overrides))


def test_reply_idempotency_hash_is_unique_within_a_sequence(
    outreach_db: Database,
) -> None:
    _seed_exact_send(outreach_db)
    with outreach_db.session() as session:
        session.add(_reply())

    with pytest.raises(IntegrityError):
        with outreach_db.session() as session:
            session.add(_reply(reply_id="reply-b"))

    with outreach_db.session() as session:
        assert session.scalar(select(func.count(OutreachReply.id))) == 1
