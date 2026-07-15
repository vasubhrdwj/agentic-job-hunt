"""Trustworthy observed outreach and contacts-two-through-five metrics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from job_hunt_agent.database import Database
from job_hunt_agent.models import OutreachReply
from job_hunt_agent.weekly_review_repository import load_weekly_review
from tests.test_contact_models import (
    _application_contact,
    _contact,
    _plan,
    contact_db,
)
from tests.test_outreach_models import _event, _message, _sequence
from tests.test_outreach_reply_models import _reply


@pytest.fixture
def weekly_outreach_db(contact_db: Database) -> Database:
    with contact_db.session() as session:
        session.add(
            _plan(
                discovered_count=5,
                verified_count=5,
                selected_count=5,
                coverage_status="met",
                exhausted=True,
                shortfall_reasons=[],
            )
        )
        for position in range(1, 6):
            contact_id = f"contact-{position}"
            session.add(
                _contact(
                    contact_id=contact_id,
                    identity_hash=str(position) * 64,
                )
            )
        session.flush()
        for position in range(1, 6):
            row = _application_contact(
                row_id=f"application-contact-{position}",
                contact_id=f"contact-{position}",
                pool_rank=position,
                verification_status="verified",
                confidence=0.9,
                bench_rank=position,
                wave=position,
                bench_state="reserve",
            )
            if position == 2:
                row.category = "recruiter"
            session.add(row)
        session.flush()
        session.add(_sequence())
    return contact_db


def _add_send(
    database: Database,
    *,
    position: int,
    sent_at: datetime,
    sequence_number: int,
    kind: str = "initial",
) -> tuple[str, str]:
    contact_id = f"application-contact-{position}"
    message_id = f"message-{position}-{kind}"
    event_id = f"sent-{position}-{kind}"
    with database.session() as session:
        message = _message(
            message_id=message_id,
            kind=kind,
            version_number=1,
        )
        message.application_contact_id = contact_id
        message.created_at = sent_at
        session.add(message)
        session.flush()
        event = _event(
            event_id=event_id,
            sequence_number=sequence_number,
            event_type="marked_sent",
            hash_character=str((sequence_number % 9) + 1),
            application_contact_id=contact_id,
            message_version_id=message_id,
            kind=kind,
            channel="linkedin",
            follow_up_due_at=(
                sent_at + timedelta(days=5) if kind == "initial" else None
            ),
        )
        event.occurred_at = sent_at
        event.created_at = sent_at
        session.add(event)
    return event_id, message_id


def _add_reply(
    database: Database,
    *,
    reply_id: str,
    position: int,
    sent_event_id: str,
    message_id: str,
    kind: str,
    reply_kind: str,
    received_on,
) -> None:
    with database.session() as session:
        session.add(
            _reply(
                reply_id=reply_id,
                hash_character=reply_id[-1],
                application_contact_id=f"application-contact-{position}",
                marked_sent_event_id=sent_event_id,
                message_version_id=message_id,
                message_kind=kind,
                reply_kind=reply_kind,
                received_on=received_on,
                recorded_at=datetime.combine(
                    received_on,
                    datetime.min.time(),
                    tzinfo=timezone.utc,
                ),
            )
        )


def _add_outcome(
    database: Database,
    *,
    event_id: str,
    position: int,
    outcome: str,
    occurred_at: datetime,
    sequence_number: int,
) -> None:
    with database.session() as session:
        event = _event(
            event_id=event_id,
            sequence_number=sequence_number,
            event_type="outcome_recorded",
            hash_character=str((sequence_number % 9) + 1),
            application_contact_id=f"application-contact-{position}",
            outcome=outcome,
        )
        event.occurred_at = occurred_at
        event.created_at = occurred_at
        session.add(event)


def _rescue(review, position: int):
    return next(
        item
        for item in review.outreach.contacts_two_through_five
        if item.position == position
    )


def test_follow_up_attributed_reply_counts_for_contact_but_legacy_success_does_not(
    weekly_outreach_db: Database,
) -> None:
    first_sent, first_message = _add_send(
        weekly_outreach_db,
        position=1,
        sent_at=datetime(2026, 7, 1, 8, tzinfo=timezone.utc),
        sequence_number=1,
    )
    del first_sent, first_message
    follow_sent, follow_message = _add_send(
        weekly_outreach_db,
        position=1,
        sent_at=datetime(2026, 7, 6, 8, tzinfo=timezone.utc),
        sequence_number=2,
        kind="follow_up",
    )
    _add_reply(
        weekly_outreach_db,
        reply_id="reply1",
        position=1,
        sent_event_id=follow_sent,
        message_id=follow_message,
        kind="follow_up",
        reply_kind="useful_reply",
        received_on=datetime(2026, 7, 7, tzinfo=timezone.utc).date(),
    )

    _add_send(
        weekly_outreach_db,
        position=2,
        sent_at=datetime(2026, 7, 2, 8, tzinfo=timezone.utc),
        sequence_number=3,
    )
    _add_outcome(
        weekly_outreach_db,
        event_id="legacy-useful",
        position=2,
        outcome="useful_reply",
        occurred_at=datetime(2026, 7, 3, 8, tzinfo=timezone.utc),
        sequence_number=4,
    )

    with weekly_outreach_db.session() as session:
        review = load_weekly_review(
            session,
            owner_id="owner-a",
            now=datetime(2026, 8, 1, 8, tzinfo=timezone.utc),
        )
        foreign = load_weekly_review(
            session,
            owner_id="owner-b",
            now=datetime(2026, 8, 1, 8, tzinfo=timezone.utc),
        )

    categories = {item.key: item for item in review.outreach.by_contact_category}
    assert (
        categories["team_peer"].reached,
        categories["team_peer"].mature,
        categories["team_peer"].successes,
        categories["team_peer"].observed_rate,
    ) == (1, 1, 1, 1.0)
    assert (
        categories["recruiter"].reached,
        categories["recruiter"].mature,
        categories["recruiter"].successes,
        categories["recruiter"].observed_rate,
    ) == (1, 1, 0, 0.0)
    assert review.outreach.unattributed_legacy_successes == 1
    assert foreign.outreach.by_contact_category == []
    assert foreign.outreach.unattributed_legacy_successes == 0


def test_second_contact_rescue_denominator_is_prior_unsuccessful_and_exact_success(
    weekly_outreach_db: Database,
) -> None:
    _add_send(
        weekly_outreach_db,
        position=1,
        sent_at=datetime(2026, 7, 1, 8, tzinfo=timezone.utc),
        sequence_number=1,
    )
    _add_outcome(
        weekly_outreach_db,
        event_id="first-no-reply",
        position=1,
        outcome="no_reply",
        occurred_at=datetime(2026, 7, 2, 8, tzinfo=timezone.utc),
        sequence_number=2,
    )
    second_sent, second_message = _add_send(
        weekly_outreach_db,
        position=2,
        sent_at=datetime(2026, 7, 3, 8, tzinfo=timezone.utc),
        sequence_number=3,
    )
    _add_reply(
        weekly_outreach_db,
        reply_id="reply2",
        position=2,
        sent_event_id=second_sent,
        message_id=second_message,
        kind="initial",
        reply_kind="introduced",
        received_on=datetime(2026, 7, 4, tzinfo=timezone.utc).date(),
    )

    with weekly_outreach_db.session() as session:
        review = load_weekly_review(
            session,
            owner_id="owner-a",
            now=datetime(2026, 8, 1, 8, tzinfo=timezone.utc),
        )

    second = _rescue(review, 2)
    assert (
        second.reached,
        second.mature,
        second.evaluable,
        second.successes,
        second.ambiguity_excluded,
        second.observed_rate,
    ) == (1, 1, 1, 1, 0, 1.0)


def test_rescue_numerator_uses_the_earliest_success_after_target_send(
    weekly_outreach_db: Database,
) -> None:
    first_sent, first_message = _add_send(
        weekly_outreach_db,
        position=1,
        sent_at=datetime(2026, 7, 1, 8, tzinfo=timezone.utc),
        sequence_number=1,
    )
    second_sent, second_message = _add_send(
        weekly_outreach_db,
        position=2,
        sent_at=datetime(2026, 7, 2, 8, tzinfo=timezone.utc),
        sequence_number=2,
    )
    _add_reply(
        weekly_outreach_db,
        reply_id="reply3",
        position=1,
        sent_event_id=first_sent,
        message_id=first_message,
        kind="initial",
        reply_kind="useful_reply",
        received_on=datetime(2026, 7, 3, tzinfo=timezone.utc).date(),
    )
    _add_reply(
        weekly_outreach_db,
        reply_id="reply4",
        position=2,
        sent_event_id=second_sent,
        message_id=second_message,
        kind="initial",
        reply_kind="referred",
        received_on=datetime(2026, 7, 4, tzinfo=timezone.utc).date(),
    )

    with weekly_outreach_db.session() as session:
        review = load_weekly_review(
            session,
            owner_id="owner-a",
            now=datetime(2026, 8, 1, 8, tzinfo=timezone.utc),
        )

    second = _rescue(review, 2)
    assert (
        second.reached,
        second.mature,
        second.successes,
        second.observed_rate,
    ) == (1, 1, 0, 0.0)


def test_same_day_prior_success_is_excluded_as_ambiguous_not_assigned_to_contact_two(
    weekly_outreach_db: Database,
) -> None:
    first_sent, first_message = _add_send(
        weekly_outreach_db,
        position=1,
        sent_at=datetime(2026, 7, 1, 8, tzinfo=timezone.utc),
        sequence_number=1,
    )
    _add_send(
        weekly_outreach_db,
        position=2,
        sent_at=datetime(2026, 7, 2, 8, tzinfo=timezone.utc),
        sequence_number=2,
    )
    _add_reply(
        weekly_outreach_db,
        reply_id="reply5",
        position=1,
        sent_event_id=first_sent,
        message_id=first_message,
        kind="initial",
        reply_kind="useful_reply",
        received_on=datetime(2026, 7, 2, tzinfo=timezone.utc).date(),
    )

    with weekly_outreach_db.session() as session:
        review = load_weekly_review(
            session,
            owner_id="owner-a",
            now=datetime(2026, 8, 1, 8, tzinfo=timezone.utc),
        )

    second = _rescue(review, 2)
    assert (
        second.reached,
        second.mature,
        second.evaluable,
        second.successes,
        second.ambiguity_excluded,
        second.observed_rate,
    ) == (1, 0, 0, 0, 1, None)


def test_rescue_maturity_and_censoring_use_the_target_contact_send(
    weekly_outreach_db: Database,
) -> None:
    _add_send(
        weekly_outreach_db,
        position=1,
        sent_at=datetime(2026, 7, 15, 8, tzinfo=timezone.utc),
        sequence_number=1,
    )
    _add_send(
        weekly_outreach_db,
        position=2,
        sent_at=datetime(2026, 7, 20, 8, tzinfo=timezone.utc),
        sequence_number=2,
    )

    with weekly_outreach_db.session() as session:
        immature = load_weekly_review(
            session,
            owner_id="owner-a",
            now=datetime(2026, 8, 1, 8, tzinfo=timezone.utc),
        )
        mature = load_weekly_review(
            session,
            owner_id="owner-a",
            now=datetime(2026, 8, 4, 8, tzinfo=timezone.utc),
        )

    second_immature = _rescue(immature, 2)
    assert (
        second_immature.reached,
        second_immature.mature,
        second_immature.evaluable,
        second_immature.immature,
        second_immature.censored_open,
        second_immature.observed_rate,
    ) == (1, 0, 0, 1, 1, None)
    second_mature = _rescue(mature, 2)
    assert (
        second_mature.reached,
        second_mature.mature,
        second_mature.evaluable,
        second_mature.immature,
        second_mature.successes,
        second_mature.observed_rate,
    ) == (1, 1, 1, 0, 0, 0.0)


def test_rescue_uses_prior_attempt_outside_window_when_target_is_inside(
    weekly_outreach_db: Database,
) -> None:
    _add_send(
        weekly_outreach_db,
        position=1,
        sent_at=datetime(2026, 7, 9, 8, tzinfo=timezone.utc),
        sequence_number=1,
    )
    second_sent, second_message = _add_send(
        weekly_outreach_db,
        position=2,
        sent_at=datetime(2026, 7, 11, 8, tzinfo=timezone.utc),
        sequence_number=2,
    )
    _add_reply(
        weekly_outreach_db,
        reply_id="reply6",
        position=2,
        sent_event_id=second_sent,
        message_id=second_message,
        kind="initial",
        reply_kind="referred",
        received_on=datetime(2026, 7, 12, tzinfo=timezone.utc).date(),
    )

    with weekly_outreach_db.session() as session:
        review = load_weekly_review(
            session,
            owner_id="owner-a",
            now=datetime(2026, 10, 1, 8, tzinfo=timezone.utc),
        )

    # July 9 is outside the 84-day reporting window, but it is still the first
    # attempt needed to define the July 11 second-contact rescue denominator.
    assert [item.key for item in review.outreach.by_sequence_position] == ["2"]
    second = _rescue(review, 2)
    assert (second.reached, second.evaluable, second.successes, second.observed_rate) == (
        1,
        1,
        1,
        1.0,
    )
