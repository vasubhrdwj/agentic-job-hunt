"""Focused relational invariants for durable manual outreach."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    OutreachEvent,
    OutreachMessageVersion,
    OutreachSequence,
)
from tests.test_contact_models import (
    NOW,
    _application_contact,
    _contact,
    _plan,
    contact_db,
)


@pytest.fixture
def outreach_db(contact_db: Database) -> Database:
    with contact_db.session() as session:
        session.add(
            _plan(
                discovered_count=1,
                verified_count=1,
                selected_count=1,
                coverage_status="partial",
                exhausted=True,
            )
        )
        session.add(_contact(contact_id="contact-a", identity_hash="a" * 64))
        session.flush()
        session.add(
            _application_contact(
                row_id="application-contact-a",
                contact_id="contact-a",
                pool_rank=1,
                verification_status="verified",
                confidence=0.9,
                bench_rank=1,
                wave=1,
                bench_state="reserve",
            )
        )
    return contact_db


def _sequence(**overrides: object) -> OutreachSequence:
    values: dict[str, object] = {
        "id": "outreach-sequence-a",
        "owner_id": "owner-a",
        "application_id": "application-a",
        "contact_plan_id": "plan-a",
        "status": "active",
        "active_wave": 1,
        "reason_code": None,
        "version": 1,
        "started_at": NOW,
    }
    values.update(overrides)
    return OutreachSequence(**values)


def _message(
    *,
    message_id: str = "message-a",
    kind: str = "initial",
    version_number: int = 1,
) -> OutreachMessageVersion:
    return OutreachMessageVersion(
        id=message_id,
        owner_id="owner-a",
        application_id="application-a",
        outreach_sequence_id="outreach-sequence-a",
        application_contact_id="application-contact-a",
        kind=kind,
        version_number=version_number,
        encrypted_body=f"ciphertext-{message_id}",
        encryption_key_id="key-1",
        content_hash=(message_id[-1] if message_id[-1].isalnum() else "a") * 64,
        created_at=NOW,
    )


def _event(
    *,
    event_id: str,
    sequence_number: int,
    event_type: str,
    hash_character: str,
    application_contact_id: str | None = None,
    message_version_id: str | None = None,
    kind: str | None = None,
    channel: str | None = None,
    outcome: str | None = None,
    reason_code: str | None = None,
    wave: int | None = None,
    follow_up_due_at=None,
) -> OutreachEvent:
    return OutreachEvent(
        id=event_id,
        owner_id="owner-a",
        application_id="application-a",
        outreach_sequence_id="outreach-sequence-a",
        application_contact_id=application_contact_id,
        message_version_id=message_version_id,
        sequence_number=sequence_number,
        event_type=event_type,
        kind=kind,
        channel=channel,
        outcome=outcome,
        reason_code=reason_code,
        wave=wave,
        follow_up_due_at=follow_up_due_at,
        occurred_at=NOW + timedelta(minutes=sequence_number),
        idempotency_key_hash=hash_character * 64,
        created_at=NOW + timedelta(minutes=sequence_number),
    )


def _seed_sequence_and_message(database: Database) -> None:
    with database.session() as session:
        session.add(_sequence())
        session.flush()
        session.add(_message())


def test_exact_message_and_manual_event_history_persists(
    outreach_db: Database,
) -> None:
    _seed_sequence_and_message(outreach_db)
    due_at = NOW + timedelta(days=5)
    with outreach_db.session() as session:
        session.add_all(
            [
                _event(
                    event_id="event-started",
                    sequence_number=1,
                    event_type="sequence_started",
                    hash_character="1",
                    wave=1,
                ),
                _event(
                    event_id="event-saved",
                    sequence_number=2,
                    event_type="message_saved",
                    hash_character="2",
                    application_contact_id="application-contact-a",
                    message_version_id="message-a",
                    kind="initial",
                ),
                _event(
                    event_id="event-copied",
                    sequence_number=3,
                    event_type="copied",
                    hash_character="3",
                    application_contact_id="application-contact-a",
                    message_version_id="message-a",
                    kind="initial",
                ),
                _event(
                    event_id="event-sent",
                    sequence_number=4,
                    event_type="marked_sent",
                    hash_character="4",
                    application_contact_id="application-contact-a",
                    message_version_id="message-a",
                    kind="initial",
                    channel="linkedin",
                    follow_up_due_at=due_at,
                ),
                _event(
                    event_id="event-outcome",
                    sequence_number=5,
                    event_type="outcome_recorded",
                    hash_character="5",
                    application_contact_id="application-contact-a",
                    outcome="useful_reply",
                ),
            ]
        )

    with outreach_db.session() as session:
        events = list(
            session.scalars(
                select(OutreachEvent).order_by(OutreachEvent.sequence_number)
            )
        )
        assert [event.event_type for event in events] == [
            "sequence_started",
            "message_saved",
            "copied",
            "marked_sent",
            "outcome_recorded",
        ]
        assert events[3].message_version_id == "message-a"
        assert events[3].follow_up_due_at == due_at.replace(tzinfo=None)


def test_one_sequence_revision_number_and_send_per_kind_are_enforced(
    outreach_db: Database,
) -> None:
    _seed_sequence_and_message(outreach_db)

    with pytest.raises(IntegrityError):
        with outreach_db.session() as session:
            session.add(_sequence(id="outreach-sequence-duplicate"))

    with pytest.raises(IntegrityError):
        with outreach_db.session() as session:
            session.add(_message(message_id="message-duplicate-revision"))

    due_at = NOW + timedelta(days=5)
    with outreach_db.session() as session:
        session.add(
            _event(
                event_id="event-first-send",
                sequence_number=1,
                event_type="marked_sent",
                hash_character="a",
                application_contact_id="application-contact-a",
                message_version_id="message-a",
                kind="initial",
                channel="email",
                follow_up_due_at=due_at,
            )
        )
    with outreach_db.session() as session:
        session.add(_message(message_id="message-b", version_number=2))
    with pytest.raises(IntegrityError):
        with outreach_db.session() as session:
            session.add(
                _event(
                    event_id="event-second-send",
                    sequence_number=2,
                    event_type="marked_sent",
                    hash_character="b",
                    application_contact_id="application-contact-a",
                    message_version_id="message-b",
                    kind="initial",
                    channel="linkedin",
                    follow_up_due_at=due_at,
                )
            )


def test_cross_owner_and_invalid_event_shapes_fail_closed(
    outreach_db: Database,
) -> None:
    with pytest.raises(IntegrityError):
        with outreach_db.session() as session:
            session.add(_sequence(id="foreign-sequence", owner_id="owner-b"))

    _seed_sequence_and_message(outreach_db)
    with pytest.raises(IntegrityError):
        with outreach_db.session() as session:
            session.add(
                _event(
                    event_id="sent-without-due",
                    sequence_number=1,
                    event_type="marked_sent",
                    hash_character="c",
                    application_contact_id="application-contact-a",
                    message_version_id="message-a",
                    kind="initial",
                    channel="linkedin",
                )
            )

    with pytest.raises(IntegrityError):
        with outreach_db.session() as session:
            session.add(
                _event(
                    event_id="copied-with-channel",
                    sequence_number=1,
                    event_type="copied",
                    hash_character="d",
                    application_contact_id="application-contact-a",
                    message_version_id="message-a",
                    kind="initial",
                    channel="email",
                )
            )
