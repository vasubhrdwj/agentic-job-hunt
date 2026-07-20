"""Owner-scoped manual outreach policy, persistence, and projections.

This module never invokes a provider, model, clipboard, email API, or social
network.  A ``marked_sent`` event is only the owner's explicit assertion that
they manually sent one exact immutable message version.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, cast
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .application_schemas import CONTACTABLE_APPLICATION_STAGE_VALUES
from .job_queue import utcnow
from .models import (
    Application,
    ApplicationContact,
    ApplicationInterviewRound,
    Contact,
    ContactPlan,
    JobPosting,
    OutreachEvent,
    OutreachMessageVersion,
    OutreachReply,
    OutreachSequence,
    Owner,
)
from .mutation_receipts import claim_owner_mutation, complete_owner_mutation
from .outreach_schemas import (
    ApplicationOutreachResponse,
    MAX_OUTREACH_RECIPIENTS,
    OutreachCopiedEventCreate,
    OutreachCopiedTimelineEvent,
    OutreachEventCreate,
    OutreachMarkedSentEventCreate,
    OutreachMarkedSentTimelineEvent,
    OutreachMessageCreate,
    OutreachMessageKind,
    OutreachMessageVersionResponse,
    OutreachOutcomeEventCreate,
    OutreachOutcomeTimelineEvent,
    OutreachPauseEventCreate,
    OutreachPausedTimelineEvent,
    OutreachRecipientResponse,
    OutreachReplyCreate,
    OutreachReplyRecordedTimelineEvent,
    OutreachReplyResponse,
    OutreachResumeEventCreate,
    OutreachResumedTimelineEvent,
    OutreachMessageSavedTimelineEvent,
    OutreachSequenceResponse,
    OutreachSequenceStartedTimelineEvent,
    OutreachSentAttemptResponse,
    OutreachStopEventCreate,
    OutreachStoppedTimelineEvent,
    OutreachWaveAdvancedTimelineEvent,
)
from .private_payloads import decrypt_private_payload, encrypt_private_payload
from .repository_errors import ResourceConflict, require_version
from .security import DataKeyring


OUTREACH_POLICY_VERSION = "manual-outreach-v1"
PERSON_COOLDOWN_DAYS = 30
COMPANY_COLD_LIMIT = 3
COMPANY_COLD_WINDOW_DAYS = 7
FOLLOW_UP_BUSINESS_DAYS = 5
NO_REPLY_WITHOUT_FOLLOW_UP_BUSINESS_DAYS = 7
MAX_TIMELINE_EVENTS = 200
TERMINAL_RECIPIENT_OUTCOMES = {"no_reply", "declined", "unreachable"}
STOP_OUTCOMES = {"introduced", "referred", "do_not_contact"}
STOP_REPLY_KINDS = {"introduced", "referred", "do_not_contact"}


class OutreachRepositoryError(RuntimeError):
    """A stored outreach aggregate is incomplete or internally inconsistent."""


def load_application_outreach(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    keyring: DataKeyring,
) -> ApplicationOutreachResponse | None:
    """Project one application sequence entirely from persisted rows."""

    application_exists = session.scalar(
        select(Application.id).where(
            Application.owner_id == owner_id,
            Application.id == application_id,
        )
    )
    if application_exists is None:
        return None

    sequence = session.scalar(
        select(OutreachSequence).where(
            OutreachSequence.owner_id == owner_id,
            OutreachSequence.application_id == application_id,
        )
    )
    if sequence is None:
        return ApplicationOutreachResponse(
            application_id=application_id,
            status="not_started",
        )
    owner_timezone = session.scalar(
        select(Owner.timezone).where(Owner.id == sequence.owner_id)
    )
    if owner_timezone is None:
        raise OutreachRepositoryError("outreach owner timezone is missing")

    recipient_rows = list(
        session.execute(
            select(ApplicationContact, Contact)
            .join(
                Contact,
                (Contact.owner_id == ApplicationContact.owner_id)
                & (Contact.id == ApplicationContact.contact_id),
            )
            .where(
                ApplicationContact.owner_id == owner_id,
                ApplicationContact.application_id == application_id,
                ApplicationContact.contact_plan_id == sequence.contact_plan_id,
                ApplicationContact.bench_rank.is_not(None),
            )
            .order_by(
                ApplicationContact.wave.asc(),
                ApplicationContact.bench_rank.asc(),
                ApplicationContact.id.asc(),
            )
        ).all()
    )
    if not recipient_rows:
        raise OutreachRepositoryError("outreach sequence has no pinned recipients")

    latest_messages: dict[tuple[str, str], OutreachMessageVersion] = {}
    for application_contact, _contact in recipient_rows:
        for kind in ("initial", "follow_up"):
            message = _latest_message(
                session,
                sequence=sequence,
                application_contact_id=application_contact.id,
                kind=kind,
            )
            if message is not None:
                latest_messages[(application_contact.id, kind)] = message

    state_events = list(
        session.scalars(
            select(OutreachEvent)
            .where(
                OutreachEvent.owner_id == owner_id,
                OutreachEvent.application_id == application_id,
                OutreachEvent.outreach_sequence_id == sequence.id,
                OutreachEvent.event_type.in_(("marked_sent", "outcome_recorded")),
            )
            .order_by(
                OutreachEvent.sequence_number.asc(),
                OutreachEvent.id.asc(),
            )
        )
    )
    copied_by_message: dict[str, OutreachEvent] = {}
    for message in latest_messages.values():
        copied = session.scalar(
            select(OutreachEvent)
            .where(
                OutreachEvent.owner_id == owner_id,
                OutreachEvent.outreach_sequence_id == sequence.id,
                OutreachEvent.event_type == "copied",
                OutreachEvent.message_version_id == message.id,
            )
            .order_by(
                OutreachEvent.sequence_number.desc(),
                OutreachEvent.id.desc(),
            )
            .limit(1)
        )
        if copied is not None:
            copied_by_message[message.id] = copied

    sent_by_message: dict[str, OutreachEvent] = {}
    latest_outcomes: dict[str, OutreachEvent] = {}
    sent_by_contact_and_kind: dict[tuple[str, str], OutreachEvent] = {}
    for event in state_events:
        if event.event_type == "marked_sent" and event.message_version_id is not None:
            sent_by_message[event.message_version_id] = event
            if event.kind is not None and event.application_contact_id is not None:
                sent_by_contact_and_kind[(event.application_contact_id, event.kind)] = event
        elif event.event_type == "outcome_recorded" and event.application_contact_id:
            latest_outcomes[event.application_contact_id] = event

    sent_message_ids = set(sent_by_message)
    sent_messages = (
        {
            message.id: message
            for message in session.scalars(
                select(OutreachMessageVersion).where(
                    OutreachMessageVersion.owner_id == owner_id,
                    OutreachMessageVersion.application_id == application_id,
                    OutreachMessageVersion.outreach_sequence_id == sequence.id,
                    OutreachMessageVersion.id.in_(sent_message_ids),
                )
            )
        }
        if sent_message_ids
        else {}
    )
    if set(sent_messages) != sent_message_ids:
        raise OutreachRepositoryError("a sent outreach message version is missing")

    replies = list(
        session.scalars(
            select(OutreachReply)
            .where(
                OutreachReply.owner_id == owner_id,
                OutreachReply.application_id == application_id,
                OutreachReply.outreach_sequence_id == sequence.id,
            )
            .order_by(OutreachReply.recorded_at.asc(), OutreachReply.id.asc())
        )
    )
    sent_event_ids = {
        event.id for event in state_events if event.event_type == "marked_sent"
    }
    replies_by_sent_event: dict[str, list[OutreachReply]] = {}
    for reply in replies:
        if reply.marked_sent_event_id not in sent_event_ids:
            raise OutreachRepositoryError("an outreach reply has no sent attempt")
        replies_by_sent_event.setdefault(reply.marked_sent_event_id, []).append(reply)

    sent_attempts_by_contact: dict[str, list[OutreachSentAttemptResponse]] = {}
    for sent_event in state_events:
        if sent_event.event_type != "marked_sent":
            continue
        if (
            sent_event.application_contact_id is None
            or sent_event.message_version_id is None
            or sent_event.kind is None
            or sent_event.channel is None
        ):
            raise OutreachRepositoryError("a marked-sent event is incomplete")
        sent_message = sent_messages.get(sent_event.message_version_id)
        if sent_message is None or sent_message.kind != sent_event.kind:
            raise OutreachRepositoryError("a marked-sent message binding is invalid")
        sent_attempts_by_contact.setdefault(
            sent_event.application_contact_id,
            [],
        ).append(
            _sent_attempt_response(
                sent_event,
                message=sent_message,
                replies=replies_by_sent_event.get(sent_event.id, []),
                owner_timezone=owner_timezone,
                keyring=keyring,
            )
        )

    recipients: list[OutreachRecipientResponse] = []
    for application_contact, contact in recipient_rows:
        initial = latest_messages.get((application_contact.id, "initial"))
        follow_up = latest_messages.get((application_contact.id, "follow_up"))
        outcome_event = latest_outcomes.get(application_contact.id)
        initial_send = sent_by_contact_and_kind.get((application_contact.id, "initial"))
        follow_up_send = sent_by_contact_and_kind.get(
            (application_contact.id, "follow_up")
        )
        recipients.append(
            OutreachRecipientResponse(
                sequence_id=sequence.id,
                application_contact_id=application_contact.id,
                contact_id=application_contact.contact_id,
                public_name=contact.public_name,
                profile_url=contact.profile_url,
                lifecycle=contact.lifecycle,
                current_title=application_contact.current_title,
                current_company=application_contact.current_company,
                category=application_contact.category,
                bench_rank=cast(int, application_contact.bench_rank),
                wave=cast(int, application_contact.wave),
                bench_state=application_contact.bench_state,
                initial_message=(
                    _message_response(
                        initial,
                        keyring=keyring,
                        copied=copied_by_message.get(initial.id),
                        sent=sent_by_message.get(initial.id),
                    )
                    if initial is not None
                    else None
                ),
                follow_up_message=(
                    _message_response(
                        follow_up,
                        keyring=keyring,
                        copied=copied_by_message.get(follow_up.id),
                        sent=sent_by_message.get(follow_up.id),
                    )
                    if follow_up is not None
                    else None
                ),
                sent_attempts=sent_attempts_by_contact.get(application_contact.id, []),
                follow_up_due_at=(
                    _as_utc(initial_send.follow_up_due_at)
                    if initial_send is not None
                    and initial_send.follow_up_due_at is not None
                    else None
                ),
                no_reply_eligible_at=_no_reply_eligible_at(
                    initial_sent_at=(
                        initial_send.occurred_at if initial_send is not None else None
                    ),
                    follow_up_sent_at=(
                        follow_up_send.occurred_at
                        if follow_up_send is not None
                        else None
                    ),
                    timezone_name=owner_timezone,
                ),
                outcome=(outcome_event.outcome if outcome_event is not None else None),
                outcome_at=(
                    _as_utc(outcome_event.occurred_at)
                    if outcome_event is not None
                    else None
                ),
            )
        )

    timeline_events = list(
        session.scalars(
            select(OutreachEvent)
            .where(
                OutreachEvent.owner_id == owner_id,
                OutreachEvent.application_id == application_id,
                OutreachEvent.outreach_sequence_id == sequence.id,
            )
            .order_by(
                OutreachEvent.sequence_number.desc(),
                OutreachEvent.id.desc(),
            )
            .limit(MAX_TIMELINE_EVENTS)
        )
    )
    timeline_events.reverse()
    timeline = [
        *_reply_timeline(replies, messages=sent_messages, keyring=keyring),
        *_timeline(timeline_events, keyring=keyring),
    ]
    timeline.sort(key=lambda item: item.occurred_at)
    timeline = timeline[-MAX_TIMELINE_EVENTS:]
    return ApplicationOutreachResponse(
        application_id=application_id,
        status=sequence.status,
        sequence=OutreachSequenceResponse(
            id=sequence.id,
            version=sequence.version,
            application_id=sequence.application_id,
            contact_plan_id=sequence.contact_plan_id,
            status=sequence.status,
            active_wave=sequence.active_wave,
            reason=sequence.reason_code,
            started_at=_as_utc(sequence.started_at),
            paused_at=_optional_utc(sequence.paused_at),
            stopped_at=_optional_utc(sequence.stopped_at),
            completed_at=_optional_utc(sequence.completed_at),
            created_at=_as_utc(sequence.created_at),
            updated_at=_as_utc(sequence.updated_at),
        ),
        recipients=recipients,
        timeline=timeline,
    )


def start_outreach_sequence(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    expected_application_version: int,
    idempotency_key: str,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> ApplicationOutreachResponse | None:
    """Pin the last completed bench and unlock one bounded, useful first wave."""

    current = _as_utc(now or utcnow())
    application = session.scalar(
        select(Application)
        .where(
            Application.owner_id == owner_id,
            Application.id == application_id,
        )
        .with_for_update()
    )
    if application is None:
        return None

    request = {
        "application_id": application_id,
        "expected_application_version": expected_application_version,
        "policy_version": OUTREACH_POLICY_VERSION,
    }
    claim = claim_owner_mutation(
        session,
        owner_id=owner_id,
        namespace=f"outreach.sequence.start:{application_id}",
        idempotency_key=idempotency_key,
        request=request,
        now=current,
    )
    if claim.replay is not None:
        _require_replay_type(claim.replay.resource_type, "outreach_sequence")
        return load_application_outreach(
            session,
            owner_id=owner_id,
            application_id=application_id,
            keyring=keyring,
        )
    require_version(
        "application",
        application.id,
        expected=expected_application_version,
        actual=application.version,
    )
    if application.stage not in CONTACTABLE_APPLICATION_STAGE_VALUES:
        raise ResourceConflict("outreach requires an actively pursued application")
    interview_progress = session.scalar(
        select(ApplicationInterviewRound.id)
        .where(
            ApplicationInterviewRound.owner_id == owner_id,
            ApplicationInterviewRound.application_id == application.id,
        )
        .limit(1)
    )
    if interview_progress is not None:
        raise ResourceConflict("outreach stops after an interview round is recorded")
    if not _posting_is_open(session, application):
        raise ResourceConflict("the posting is closed; outreach cannot start")

    existing = session.scalar(
        select(OutreachSequence.id).where(
            OutreachSequence.owner_id == owner_id,
            OutreachSequence.application_id == application_id,
        )
    )
    if existing is not None:
        raise ResourceConflict("an outreach sequence already exists for this application")

    active_search = session.scalar(
        select(ContactPlan.id).where(
            ContactPlan.owner_id == owner_id,
            ContactPlan.application_id == application_id,
            ContactPlan.status.in_(("queued", "running")),
        )
    )
    if active_search is not None:
        raise ResourceConflict("wait for the active contact search to finish")

    contact_plan = session.scalar(
        select(ContactPlan)
        .where(
            ContactPlan.owner_id == owner_id,
            ContactPlan.application_id == application_id,
            ContactPlan.status == "completed",
            ContactPlan.selected_count > 0,
        )
        .order_by(
            ContactPlan.plan_number.desc(),
            ContactPlan.finalized_at.desc(),
            ContactPlan.id.desc(),
        )
        .limit(1)
        .with_for_update()
    )
    if contact_plan is None:
        raise ResourceConflict(
            "find at least one source-backed person before starting outreach"
        )

    rows = list(
        session.execute(
            select(ApplicationContact, Contact)
            .join(
                Contact,
                (Contact.owner_id == ApplicationContact.owner_id)
                & (Contact.id == ApplicationContact.contact_id),
            )
            .where(
                ApplicationContact.owner_id == owner_id,
                ApplicationContact.application_id == application_id,
                ApplicationContact.contact_plan_id == contact_plan.id,
                ApplicationContact.bench_rank.is_not(None),
            )
            .order_by(
                ApplicationContact.bench_rank.asc(),
                ApplicationContact.id.asc(),
            )
            .with_for_update()
        ).all()
    )
    if not rows:
        raise OutreachRepositoryError("completed contact plan has no selected contacts")

    eligible: list[tuple[ApplicationContact, Contact]] = []
    for application_contact, contact in rows:
        last_sent = _last_initial_send_for_contact(
            session,
            owner_id=owner_id,
            contact_id=contact.id,
        )
        cooldown_until = (
            _as_utc(last_sent) + timedelta(days=PERSON_COOLDOWN_DAYS)
            if last_sent is not None
            else _optional_utc(application_contact.cooldown_until)
        )
        if cooldown_until is not None and cooldown_until > current:
            application_contact.cooldown_until = cooldown_until
        is_eligible = (
            contact.lifecycle == "active"
            and application_contact.verification_status == "verified"
            and application_contact.confidence >= 0.75
            and application_contact.bench_state in {"reserve", "ready"}
            and (cooldown_until is None or cooldown_until <= current)
        )
        if is_eligible:
            eligible.append((application_contact, contact))

    if not eligible:
        raise ResourceConflict(
            "all source-backed people are restricted or in cooldown"
        )

    # Contact discovery already ranks a category-diverse bench.  Make every
    # currently eligible, distinct person in that bounded bench available at
    # once instead of hiding most of the useful leads behind week-long serial
    # waves.  Message bodies and send assertions remain per-person, while the
    # person cooldown and company rolling-window throttle are still enforced
    # at the consequential ``marked_sent`` transition.
    first_wave = _diverse_initial_wave(eligible)
    first_wave_ids = {application_contact.id for application_contact, _ in first_wave}
    for application_contact, _contact in rows:
        if application_contact.id in first_wave_ids:
            bench_state = "ready"
            unlocked_at = current
        else:
            # A pinned row that is restricted or cooling down is history, not
            # a silently scheduled future contact.  A later search can assess
            # it again from fresh source evidence.
            bench_state = "stopped"
            unlocked_at = None
        wave = 1
        if (
            application_contact.wave != wave
            or application_contact.bench_state != bench_state
            or _optional_utc(application_contact.unlocked_at) != unlocked_at
        ):
            application_contact.wave = wave
            application_contact.bench_state = bench_state
            application_contact.unlocked_at = unlocked_at
            application_contact.version += 1
            application_contact.updated_at = current

    sequence = OutreachSequence(
        owner_id=owner_id,
        application_id=application_id,
        contact_plan_id=contact_plan.id,
        status="active",
        active_wave=1,
        reason_code=None,
        version=1,
        started_at=current,
        created_at=current,
        updated_at=current,
    )
    session.add(sequence)
    session.flush()
    session.add(
        OutreachEvent(
            owner_id=owner_id,
            application_id=application_id,
            outreach_sequence_id=sequence.id,
            sequence_number=1,
            event_type="sequence_started",
            wave=1,
            occurred_at=current,
            idempotency_key_hash=_event_hash(idempotency_key, "sequence_started"),
            created_at=current,
        )
    )
    session.flush()
    complete_owner_mutation(
        session,
        owner_id=owner_id,
        receipt_id=claim.receipt_id,
        resource_type="outreach_sequence",
        resource_id=sequence.id,
        result_version=sequence.version,
        now=current,
    )
    return load_application_outreach(
        session,
        owner_id=owner_id,
        application_id=application_id,
        keyring=keyring,
    )


def _diverse_initial_wave(
    eligible: list[tuple[ApplicationContact, Contact]],
) -> list[tuple[ApplicationContact, Contact]]:
    """Return at most five distinct leads, preferring useful role coverage."""

    selected: list[tuple[ApplicationContact, Contact]] = []
    selected_contact_ids: set[str] = set()

    def take_first(categories: set[str]) -> None:
        for item in eligible:
            application_contact, contact = item
            if (
                contact.id not in selected_contact_ids
                and application_contact.category in categories
            ):
                selected.append(item)
                selected_contact_ids.add(contact.id)
                return

    # Warm paths are the most useful when present, followed by one engineering
    # peer, one likely team leader/hiring manager, and one recruiter.  Remaining
    # capacity follows the persisted bench rank.
    take_first({"warm_path"})
    take_first({"team_peer", "adjacent_peer"})
    take_first({"team_leader"})
    take_first({"recruiter"})
    for item in eligible:
        contact = item[1]
        if contact.id in selected_contact_ids:
            continue
        selected.append(item)
        selected_contact_ids.add(contact.id)
        if len(selected) >= MAX_OUTREACH_RECIPIENTS:
            break
    return selected[:MAX_OUTREACH_RECIPIENTS]


def save_outreach_message(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    sequence_id: str,
    payload: OutreachMessageCreate,
    expected_sequence_version: int,
    idempotency_key: str,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> ApplicationOutreachResponse | None:
    """Persist the next exact encrypted version without sending anything."""

    current = _as_utc(now or utcnow())
    sequence = _lock_sequence(
        session,
        owner_id=owner_id,
        application_id=application_id,
        sequence_id=sequence_id,
    )
    if sequence is None:
        return None
    claim = claim_owner_mutation(
        session,
        owner_id=owner_id,
        namespace=f"outreach.message.save:{sequence_id}",
        idempotency_key=idempotency_key,
        request={
            "payload": payload.model_dump(mode="json"),
            "expected_sequence_version": expected_sequence_version,
        },
        now=current,
    )
    if claim.replay is not None:
        _require_replay_type(claim.replay.resource_type, "outreach_sequence")
        return load_application_outreach(
            session,
            owner_id=owner_id,
            application_id=application_id,
            keyring=keyring,
        )
    require_version(
        "outreach_sequence",
        sequence.id,
        expected=expected_sequence_version,
        actual=sequence.version,
    )
    if sequence.status != "active":
        raise ResourceConflict("messages can be edited only while outreach is active")
    automatic_stop_reason = _sequence_stop_reason(session, sequence)
    if automatic_stop_reason is not None:
        _stop_sequence_rows(
            session,
            sequence=sequence,
            reason_code=automatic_stop_reason,
            now=current,
        )
        _add_sequence_event(
            session,
            sequence=sequence,
            sequence_number=_next_event_number(session, sequence),
            event_type="stopped",
            reason_code=automatic_stop_reason,
            occurred_at=current,
            mutation_hash=_event_hash(idempotency_key, automatic_stop_reason),
        )
        sequence.version += 1
        sequence.updated_at = current
        session.flush()
        complete_owner_mutation(
            session,
            owner_id=owner_id,
            receipt_id=claim.receipt_id,
            resource_type="outreach_sequence",
            resource_id=sequence.id,
            result_version=sequence.version,
            now=current,
        )
        return load_application_outreach(
            session,
            owner_id=owner_id,
            application_id=application_id,
            keyring=keyring,
        )

    application_contact, contact = _lock_recipient(
        session,
        sequence=sequence,
        application_contact_id=payload.application_contact_id,
    )
    _require_ready_recipient(sequence, application_contact, contact)
    kind = payload.kind.value
    if _sent_event(
        session,
        sequence=sequence,
        application_contact_id=application_contact.id,
        kind=kind,
    ) is not None:
        raise ResourceConflict(f"the {kind} message was already marked sent")
    if _latest_outcome(
        session,
        sequence=sequence,
        application_contact_id=application_contact.id,
    ) is not None:
        raise ResourceConflict("messages cannot be edited after an outcome")
    if kind == "follow_up" and _sent_event(
        session,
        sequence=sequence,
        application_contact_id=application_contact.id,
        kind="initial",
    ) is None:
        raise ResourceConflict("save an initial send before drafting a follow-up")

    next_version = (
        session.scalar(
            select(func.max(OutreachMessageVersion.version_number)).where(
                OutreachMessageVersion.owner_id == owner_id,
                OutreachMessageVersion.outreach_sequence_id == sequence.id,
                OutreachMessageVersion.application_contact_id == application_contact.id,
                OutreachMessageVersion.kind == kind,
            )
        )
        or 0
    ) + 1
    message_id = uuid4().hex
    envelope = encrypt_private_payload(
        keyring,
        record_kind="outreach_message",
        owner_id=owner_id,
        record_id=message_id,
        payload={"body": payload.body},
    )
    message = OutreachMessageVersion(
        id=message_id,
        owner_id=owner_id,
        application_id=application_id,
        outreach_sequence_id=sequence.id,
        application_contact_id=application_contact.id,
        kind=kind,
        version_number=next_version,
        encrypted_body=envelope.ciphertext,
        encryption_key_id=envelope.key_id,
        content_hash=hashlib.sha256(payload.body.encode("utf-8")).hexdigest(),
        created_at=current,
    )
    session.add(message)
    session.flush()
    _add_sequence_event(
        session,
        sequence=sequence,
        sequence_number=_next_event_number(session, sequence),
        event_type="message_saved",
        application_contact_id=application_contact.id,
        message_version_id=message.id,
        kind=kind,
        occurred_at=current,
        mutation_hash=_event_hash(idempotency_key, "message_saved"),
    )
    sequence.version += 1
    sequence.updated_at = current
    session.flush()
    complete_owner_mutation(
        session,
        owner_id=owner_id,
        receipt_id=claim.receipt_id,
        resource_type="outreach_sequence",
        resource_id=sequence.id,
        result_version=sequence.version,
        now=current,
    )
    return load_application_outreach(
        session,
        owner_id=owner_id,
        application_id=application_id,
        keyring=keyring,
    )


def record_outreach_event(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    sequence_id: str,
    payload: OutreachEventCreate,
    expected_sequence_version: int,
    idempotency_key: str,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> ApplicationOutreachResponse | None:
    """Record one explicit manual action and enforce sequence transitions."""

    current = _as_utc(now or utcnow())
    sequence = _lock_sequence(
        session,
        owner_id=owner_id,
        application_id=application_id,
        sequence_id=sequence_id,
    )
    if sequence is None:
        return None
    claim = claim_owner_mutation(
        session,
        owner_id=owner_id,
        namespace=f"outreach.event.record:{sequence_id}",
        idempotency_key=idempotency_key,
        request={
            "payload": payload.model_dump(mode="json"),
            "expected_sequence_version": expected_sequence_version,
        },
        now=current,
    )
    if claim.replay is not None:
        _require_replay_type(claim.replay.resource_type, "outreach_sequence")
        return load_application_outreach(
            session,
            owner_id=owner_id,
            application_id=application_id,
            keyring=keyring,
        )
    require_version(
        "outreach_sequence",
        sequence.id,
        expected=expected_sequence_version,
        actual=sequence.version,
    )

    event_number = _next_event_number(session, sequence)
    automatic_stop_reason = _sequence_stop_reason(session, sequence)
    if (
        not isinstance(payload, OutreachStopEventCreate)
        and sequence.status in {"active", "paused"}
        and automatic_stop_reason is not None
    ):
        _stop_sequence_rows(
            session,
            sequence=sequence,
            reason_code=automatic_stop_reason,
            now=current,
        )
        _add_sequence_event(
            session,
            sequence=sequence,
            sequence_number=event_number,
            event_type="stopped",
            reason_code=automatic_stop_reason,
            occurred_at=current,
            mutation_hash=_event_hash(idempotency_key, automatic_stop_reason),
        )
    elif isinstance(payload, OutreachCopiedEventCreate):
        _record_copy(
            session,
            sequence=sequence,
            payload=payload,
            event_number=event_number,
            idempotency_key=idempotency_key,
            now=current,
        )
    elif isinstance(payload, OutreachMarkedSentEventCreate):
        _record_marked_sent(
            session,
            sequence=sequence,
            payload=payload,
            event_number=event_number,
            idempotency_key=idempotency_key,
            now=current,
        )
    elif isinstance(payload, OutreachOutcomeEventCreate):
        event_number = _record_outcome(
            session,
            sequence=sequence,
            payload=payload,
            event_number=event_number,
            idempotency_key=idempotency_key,
            keyring=keyring,
            now=current,
        )
    elif isinstance(payload, OutreachPauseEventCreate):
        if sequence.status != "active":
            raise ResourceConflict("only active outreach can be paused")
        _pause_sequence_rows(
            session,
            sequence=sequence,
            reason_code="manual_pause",
            now=current,
        )
        _add_reasoned_event(
            session,
            sequence=sequence,
            sequence_number=event_number,
            event_type="paused",
            reason_code="manual_pause",
            note=payload.reason,
            keyring=keyring,
            occurred_at=current,
            mutation_hash=_event_hash(idempotency_key, "paused"),
        )
    elif isinstance(payload, OutreachResumeEventCreate):
        if sequence.status != "paused":
            raise ResourceConflict("only paused outreach can be resumed")
        resumed_wave = _resume_sequence_rows(session, sequence=sequence, now=current)
        _add_reasoned_event(
            session,
            sequence=sequence,
            sequence_number=event_number,
            event_type="resumed",
            reason_code="manual_resume",
            note=payload.reason,
            keyring=keyring,
            occurred_at=current,
            mutation_hash=_event_hash(idempotency_key, "resumed"),
        )
        if resumed_wave is not None:
            _add_sequence_event(
                session,
                sequence=sequence,
                sequence_number=event_number + 1,
                event_type="wave_advanced",
                wave=resumed_wave,
                occurred_at=current,
                mutation_hash=_event_hash(idempotency_key, "resume_wave_advanced"),
            )
    elif isinstance(payload, OutreachStopEventCreate):
        if sequence.status not in {"active", "paused"}:
            raise ResourceConflict("outreach is already terminal")
        _stop_sequence_rows(
            session,
            sequence=sequence,
            reason_code="manual_stop",
            now=current,
        )
        _add_reasoned_event(
            session,
            sequence=sequence,
            sequence_number=event_number,
            event_type="stopped",
            reason_code="manual_stop",
            note=payload.reason,
            keyring=keyring,
            occurred_at=current,
            mutation_hash=_event_hash(idempotency_key, "stopped"),
        )
    else:  # pragma: no cover - the discriminated schema makes this unreachable.
        raise ValueError("unsupported outreach event")

    sequence.version += 1
    sequence.updated_at = current
    session.flush()
    complete_owner_mutation(
        session,
        owner_id=owner_id,
        receipt_id=claim.receipt_id,
        resource_type="outreach_sequence",
        resource_id=sequence.id,
        result_version=sequence.version,
        now=current,
    )
    return load_application_outreach(
        session,
        owner_id=owner_id,
        application_id=application_id,
        keyring=keyring,
    )


def record_outreach_reply(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    sequence_id: str,
    payload: OutreachReplyCreate,
    expected_sequence_version: int,
    idempotency_key: str,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> ApplicationOutreachResponse | None:
    """Append one manual reply tied to an exact immutable sent attempt."""

    current = _as_utc(now or utcnow())
    sequence = _lock_sequence(
        session,
        owner_id=owner_id,
        application_id=application_id,
        sequence_id=sequence_id,
    )
    if sequence is None:
        return None
    claim = claim_owner_mutation(
        session,
        owner_id=owner_id,
        namespace=f"outreach.reply.record:{sequence_id}",
        idempotency_key=idempotency_key,
        request={
            "payload": payload.model_dump(mode="json"),
            "expected_sequence_version": expected_sequence_version,
        },
        now=current,
    )
    if claim.replay is not None:
        _require_replay_type(claim.replay.resource_type, "outreach_reply")
        return load_application_outreach(
            session,
            owner_id=owner_id,
            application_id=application_id,
            keyring=keyring,
        )
    require_version(
        "outreach_sequence",
        sequence.id,
        expected=expected_sequence_version,
        actual=sequence.version,
    )

    sent_event = session.scalar(
        select(OutreachEvent)
        .where(
            OutreachEvent.owner_id == owner_id,
            OutreachEvent.application_id == application_id,
            OutreachEvent.outreach_sequence_id == sequence.id,
            OutreachEvent.id == payload.marked_sent_event_id,
            OutreachEvent.event_type == "marked_sent",
        )
        .with_for_update()
    )
    if sent_event is None:
        raise ResourceConflict("the selected sent attempt is not part of this sequence")
    if (
        sent_event.application_contact_id is None
        or sent_event.message_version_id is None
        or sent_event.kind is None
    ):
        raise OutreachRepositoryError("the selected sent attempt is incomplete")

    message = _lock_message(
        session,
        sequence=sequence,
        message_id=sent_event.message_version_id,
    )
    if (
        message.application_contact_id != sent_event.application_contact_id
        or message.kind != sent_event.kind
    ):
        raise OutreachRepositoryError("the selected sent attempt message is invalid")
    application_contact, contact = _lock_recipient(
        session,
        sequence=sequence,
        application_contact_id=sent_event.application_contact_id,
    )

    owner_timezone = session.scalar(
        select(Owner.timezone).where(Owner.id == owner_id)
    )
    if owner_timezone is None:
        raise OutreachRepositoryError("outreach owner timezone is missing")
    sent_local_on = _owner_local_on(
        sent_event.occurred_at,
        timezone_name=owner_timezone,
    )
    today_local_on = _owner_local_on(current, timezone_name=owner_timezone)
    if payload.received_on < sent_local_on:
        raise ResourceConflict("reply date cannot precede the selected sent attempt")
    if payload.received_on > today_local_on:
        raise ResourceConflict("reply date cannot be in the owner's future")

    reply_id = uuid4().hex
    encrypted_note: str | None = None
    note_key_id: str | None = None
    if payload.note is not None:
        envelope = encrypt_private_payload(
            keyring,
            record_kind="outreach_reply_note",
            owner_id=owner_id,
            record_id=reply_id,
            payload={"note": payload.note},
        )
        encrypted_note = envelope.ciphertext
        note_key_id = envelope.key_id
    reply_kind = payload.reply_kind.value
    reply = OutreachReply(
        id=reply_id,
        owner_id=owner_id,
        application_id=application_id,
        outreach_sequence_id=sequence.id,
        application_contact_id=application_contact.id,
        marked_sent_event_id=sent_event.id,
        marked_sent_event_type="marked_sent",
        message_version_id=message.id,
        message_kind=message.kind,
        reply_kind=reply_kind,
        received_on=payload.received_on,
        encrypted_note=encrypted_note,
        note_key_id=note_key_id,
        recording_method="manual",
        recorded_at=current,
        idempotency_key_hash=_event_hash(idempotency_key, "reply_recorded"),
        created_at=current,
    )
    session.add(reply)

    if application_contact.bench_state != "stopped":
        application_contact.bench_state = "stopped"
        application_contact.version += 1
        application_contact.updated_at = current
    if reply_kind == "do_not_contact" and contact.lifecycle != "do_not_contact":
        contact.lifecycle = "do_not_contact"
        contact.do_not_contact_at = current
        contact.version += 1
        contact.updated_at = current

    if sequence.status in {"active", "paused"}:
        event_number = _next_event_number(session, sequence)
        automatic_stop_reason = _sequence_stop_reason(session, sequence)
        if automatic_stop_reason is not None:
            _stop_sequence_rows(
                session,
                sequence=sequence,
                reason_code=automatic_stop_reason,
                now=current,
            )
            _add_sequence_event(
                session,
                sequence=sequence,
                sequence_number=event_number,
                event_type="stopped",
                reason_code=automatic_stop_reason,
                occurred_at=current,
                mutation_hash=_event_hash(idempotency_key, "reply_automatic_stop"),
            )
        elif reply_kind in STOP_REPLY_KINDS:
            _stop_sequence_rows(
                session,
                sequence=sequence,
                reason_code=reply_kind,
                now=current,
            )
            _add_sequence_event(
                session,
                sequence=sequence,
                sequence_number=event_number,
                event_type="stopped",
                reason_code=reply_kind,
                occurred_at=current,
                mutation_hash=_event_hash(idempotency_key, "reply_stop"),
            )
        else:
            was_active = sequence.status == "active"
            session.flush()
            if _active_wave_is_resolved(session, sequence=sequence):
                next_wave = _next_reserve_wave(
                    session,
                    sequence=sequence,
                    now=current,
                )
                if next_wave is None:
                    sequence.status = "completed"
                    sequence.active_wave = None
                    sequence.reason_code = None
                    sequence.paused_at = None
                    sequence.stopped_at = None
                    sequence.completed_at = current
                else:
                    _unlock_wave(
                        session,
                        sequence=sequence,
                        wave=next_wave,
                        now=current,
                    )
                    sequence.active_wave = next_wave
                    _add_sequence_event(
                        session,
                        sequence=sequence,
                        sequence_number=event_number,
                        event_type="wave_advanced",
                        wave=next_wave,
                        occurred_at=current,
                        mutation_hash=_event_hash(idempotency_key, "reply_wave_advanced"),
                    )
                    _pause_sequence_rows(
                        session,
                        sequence=sequence,
                        reason_code=reply_kind,
                        now=current,
                    )
                    _add_sequence_event(
                        session,
                        sequence=sequence,
                        sequence_number=event_number + 1,
                        event_type="paused",
                        reason_code=reply_kind,
                        occurred_at=current,
                        mutation_hash=_event_hash(idempotency_key, "reply_pause"),
                    )
            elif was_active:
                _pause_sequence_rows(
                    session,
                    sequence=sequence,
                    reason_code=reply_kind,
                    now=current,
                )
                _add_sequence_event(
                    session,
                    sequence=sequence,
                    sequence_number=event_number,
                    event_type="paused",
                    reason_code=reply_kind,
                    occurred_at=current,
                    mutation_hash=_event_hash(idempotency_key, "reply_pause"),
                )

    sequence.version += 1
    sequence.updated_at = current
    session.flush()
    complete_owner_mutation(
        session,
        owner_id=owner_id,
        receipt_id=claim.receipt_id,
        resource_type="outreach_reply",
        resource_id=reply.id,
        result_version=sequence.version,
        now=current,
    )
    return load_application_outreach(
        session,
        owner_id=owner_id,
        application_id=application_id,
        keyring=keyring,
    )


def add_business_days(
    value: datetime,
    days: int,
    *,
    timezone_name: str,
) -> datetime:
    """Add Monday-Friday calendar days at the owner's local wall time."""

    if days < 0:
        raise ValueError("business-day count cannot be negative")
    try:
        owner_zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise OutreachRepositoryError("owner timezone is invalid") from exc
    candidate = _as_utc(value).astimezone(owner_zone)
    remaining = days
    while remaining:
        candidate += timedelta(days=1)
        if candidate.weekday() < 5:
            remaining -= 1
    return candidate.astimezone(timezone.utc)


def _owner_local_on(value: datetime, *, timezone_name: str) -> date:
    try:
        owner_zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise OutreachRepositoryError("owner timezone is invalid") from exc
    return _as_utc(value).astimezone(owner_zone).date()


def _no_reply_eligible_at(
    *,
    initial_sent_at: datetime | None,
    follow_up_sent_at: datetime | None,
    timezone_name: str,
) -> datetime | None:
    """Project the policy deadline from the latest completed cadence step."""

    if initial_sent_at is None:
        return None
    if follow_up_sent_at is not None:
        return add_business_days(
            follow_up_sent_at,
            FOLLOW_UP_BUSINESS_DAYS,
            timezone_name=timezone_name,
        )
    return add_business_days(
        initial_sent_at,
        NO_REPLY_WITHOUT_FOLLOW_UP_BUSINESS_DAYS,
        timezone_name=timezone_name,
    )


def _record_copy(
    session: Session,
    *,
    sequence: OutreachSequence,
    payload: OutreachCopiedEventCreate,
    event_number: int,
    idempotency_key: str,
    now: datetime,
) -> None:
    if sequence.status != "active":
        raise ResourceConflict("messages can be copied only while outreach is active")
    message = _lock_message(session, sequence=sequence, message_id=payload.message_version_id)
    application_contact, contact = _lock_recipient(
        session,
        sequence=sequence,
        application_contact_id=message.application_contact_id,
    )
    _require_ready_recipient(sequence, application_contact, contact)
    if _latest_outcome(
        session,
        sequence=sequence,
        application_contact_id=application_contact.id,
    ) is not None:
        raise ResourceConflict("messages cannot be copied after an outcome")
    latest = _latest_message(
        session,
        sequence=sequence,
        application_contact_id=application_contact.id,
        kind=message.kind,
    )
    if latest is None or latest.id != message.id:
        raise ResourceConflict("copy the latest saved message version")
    if _sent_event(
        session,
        sequence=sequence,
        application_contact_id=application_contact.id,
        kind=message.kind,
    ) is not None:
        raise ResourceConflict("the message was already marked sent")
    _add_sequence_event(
        session,
        sequence=sequence,
        sequence_number=event_number,
        event_type="copied",
        application_contact_id=application_contact.id,
        message_version_id=message.id,
        kind=message.kind,
        occurred_at=now,
        mutation_hash=_event_hash(idempotency_key, "copied"),
    )


def _record_marked_sent(
    session: Session,
    *,
    sequence: OutreachSequence,
    payload: OutreachMarkedSentEventCreate,
    event_number: int,
    idempotency_key: str,
    now: datetime,
) -> None:
    if sequence.status != "active":
        raise ResourceConflict("messages can be marked sent only while outreach is active")
    message = _lock_message(session, sequence=sequence, message_id=payload.message_version_id)
    application_contact, contact = _lock_recipient(
        session,
        sequence=sequence,
        application_contact_id=message.application_contact_id,
    )
    _require_ready_recipient(sequence, application_contact, contact)
    if _latest_outcome(
        session,
        sequence=sequence,
        application_contact_id=application_contact.id,
    ) is not None:
        raise ResourceConflict("messages cannot be marked sent after an outcome")
    latest = _latest_message(
        session,
        sequence=sequence,
        application_contact_id=application_contact.id,
        kind=message.kind,
    )
    if latest is None or latest.id != message.id:
        raise ResourceConflict("mark the latest saved message version")
    copied = session.scalar(
        select(OutreachEvent.id).where(
            OutreachEvent.owner_id == sequence.owner_id,
            OutreachEvent.outreach_sequence_id == sequence.id,
            OutreachEvent.event_type == "copied",
            OutreachEvent.message_version_id == message.id,
        )
    )
    if copied is None:
        raise ResourceConflict("copy this exact message version before marking it sent")
    if _sent_event(
        session,
        sequence=sequence,
        application_contact_id=application_contact.id,
        kind=message.kind,
    ) is not None:
        raise ResourceConflict(f"the {message.kind} message was already marked sent")

    follow_up_due_at: datetime | None = None
    if message.kind == "follow_up":
        initial_sent = _sent_event(
            session,
            sequence=sequence,
            application_contact_id=application_contact.id,
            kind="initial",
        )
        if initial_sent is None or initial_sent.follow_up_due_at is None:
            raise ResourceConflict("an initial send is required before a follow-up")
        if now < _as_utc(initial_sent.follow_up_due_at):
            raise ResourceConflict("the five-business-day follow-up window is not due")
    else:
        # One owner row is the shared throttle lock across every application.
        # This makes the person cooldown and company rolling-window count one
        # serial decision even when two sequences are marked sent concurrently.
        owner = session.scalar(
            select(Owner)
            .where(Owner.id == sequence.owner_id)
            .with_for_update()
        )
        if owner is None:
            raise OutreachRepositoryError("outreach owner is missing")
        last_sent = _last_initial_send_for_contact(
            session,
            owner_id=sequence.owner_id,
            contact_id=contact.id,
        )
        if last_sent is not None and _as_utc(last_sent) + timedelta(
            days=PERSON_COOLDOWN_DAYS
        ) > now:
            raise ResourceConflict("this person was contacted within the last 30 days")
        if application_contact.category not in {"recruiter", "warm_path"}:
            _enforce_company_cold_limit(
                session,
                sequence=sequence,
                application_contact=application_contact,
                now=now,
            )
        follow_up_due_at = add_business_days(
            now,
            FOLLOW_UP_BUSINESS_DAYS,
            timezone_name=owner.timezone,
        )
        application_contact.cooldown_until = now + timedelta(days=PERSON_COOLDOWN_DAYS)
        application_contact.version += 1
        application_contact.updated_at = now

    _add_sequence_event(
        session,
        sequence=sequence,
        sequence_number=event_number,
        event_type="marked_sent",
        application_contact_id=application_contact.id,
        message_version_id=message.id,
        kind=message.kind,
        channel=payload.channel.value,
        follow_up_due_at=follow_up_due_at,
        occurred_at=now,
        mutation_hash=_event_hash(idempotency_key, "marked_sent"),
    )


def _record_outcome(
    session: Session,
    *,
    sequence: OutreachSequence,
    payload: OutreachOutcomeEventCreate,
    event_number: int,
    idempotency_key: str,
    keyring: DataKeyring,
    now: datetime,
) -> int:
    if sequence.status not in {"active", "paused"}:
        raise ResourceConflict("outcomes cannot be added to terminal outreach")
    application_contact, contact = _lock_recipient(
        session,
        sequence=sequence,
        application_contact_id=payload.application_contact_id,
    )
    outcome = payload.outcome.value
    existing = _latest_outcome(
        session,
        sequence=sequence,
        application_contact_id=application_contact.id,
    )
    if sequence.status == "paused" and not (
        existing is not None
        and existing.outcome == "useful_reply"
        and outcome in STOP_OUTCOMES
    ):
        raise ResourceConflict("resume outreach before recording this outcome")
    if existing is not None and not (
        existing.outcome == "useful_reply" and outcome in STOP_OUTCOMES
    ):
        raise ResourceConflict("a final outcome is already recorded for this person")

    initial_sent = _sent_event(
        session,
        sequence=sequence,
        application_contact_id=application_contact.id,
        kind="initial",
    )
    if outcome != "unreachable" and initial_sent is None:
        raise ResourceConflict("record an initial send before this outcome")
    if outcome == "no_reply":
        follow_up_sent = _sent_event(
            session,
            sequence=sequence,
            application_contact_id=application_contact.id,
            kind="follow_up",
        )
        owner_timezone = session.scalar(
            select(Owner.timezone).where(Owner.id == sequence.owner_id)
        )
        if owner_timezone is None or initial_sent is None:
            raise OutreachRepositoryError("outreach cadence context is missing")
        eligible_at = _no_reply_eligible_at(
            initial_sent_at=initial_sent.occurred_at,
            follow_up_sent_at=(
                follow_up_sent.occurred_at if follow_up_sent is not None else None
            ),
            timezone_name=owner_timezone,
        )
        if eligible_at is None:  # pragma: no cover - initial_sent was required above.
            raise OutreachRepositoryError("outreach cadence context is missing")
        if now < eligible_at:
            raise ResourceConflict("the no-reply waiting window is not complete")

    _add_sequence_event(
        session,
        sequence=sequence,
        sequence_number=event_number,
        event_type="outcome_recorded",
        application_contact_id=application_contact.id,
        outcome=outcome,
        occurred_at=now,
        mutation_hash=_event_hash(idempotency_key, "outcome_recorded"),
    )
    event_number += 1
    if outcome == "do_not_contact":
        contact.lifecycle = "do_not_contact"
        contact.do_not_contact_at = now
        contact.version += 1
        contact.updated_at = now

    if outcome == "useful_reply":
        _pause_sequence_rows(
            session,
            sequence=sequence,
            reason_code="useful_reply",
            now=now,
        )
        _add_sequence_event(
            session,
            sequence=sequence,
            sequence_number=event_number,
            event_type="paused",
            reason_code="useful_reply",
            occurred_at=now,
            mutation_hash=_event_hash(idempotency_key, "useful_reply_pause"),
        )
        return event_number + 1
    if outcome in STOP_OUTCOMES:
        _stop_sequence_rows(
            session,
            sequence=sequence,
            reason_code=outcome,
            now=now,
        )
        _add_sequence_event(
            session,
            sequence=sequence,
            sequence_number=event_number,
            event_type="stopped",
            reason_code=outcome,
            occurred_at=now,
            mutation_hash=_event_hash(idempotency_key, f"{outcome}_stop"),
        )
        return event_number + 1

    application_contact.bench_state = "stopped"
    application_contact.version += 1
    application_contact.updated_at = now
    session.flush()
    if _active_wave_is_resolved(session, sequence=sequence):
        next_wave = _next_reserve_wave(session, sequence=sequence, now=now)
        if next_wave is None:
            sequence.status = "completed"
            sequence.active_wave = None
            sequence.reason_code = None
            sequence.paused_at = None
            sequence.completed_at = now
        else:
            _unlock_wave(session, sequence=sequence, wave=next_wave, now=now)
            sequence.active_wave = next_wave
            _add_sequence_event(
                session,
                sequence=sequence,
                sequence_number=event_number,
                event_type="wave_advanced",
                wave=next_wave,
                occurred_at=now,
                mutation_hash=_event_hash(idempotency_key, f"wave_{next_wave}"),
            )
            event_number += 1
    return event_number


def _message_response(
    message: OutreachMessageVersion,
    *,
    keyring: DataKeyring,
    copied: OutreachEvent | None,
    sent: OutreachEvent | None,
) -> OutreachMessageVersionResponse:
    body = _message_body(message, keyring=keyring)
    return OutreachMessageVersionResponse(
        id=message.id,
        version_number=message.version_number,
        kind=message.kind,
        body=body,
        copied_at=_as_utc(copied.occurred_at) if copied is not None else None,
        sent_at=_as_utc(sent.occurred_at) if sent is not None else None,
        sent_channel=sent.channel if sent is not None else None,
        created_at=_as_utc(message.created_at),
    )


def _message_body(
    message: OutreachMessageVersion,
    *,
    keyring: DataKeyring,
) -> str:
    payload = decrypt_private_payload(
        keyring,
        record_kind="outreach_message",
        owner_id=message.owner_id,
        record_id=message.id,
        encryption_key_id=message.encryption_key_id,
        ciphertext=message.encrypted_body,
    )
    body = payload.get("body")
    if not isinstance(body, str):
        raise OutreachRepositoryError("outreach message body is invalid")
    return body


def _sent_attempt_response(
    sent: OutreachEvent,
    *,
    message: OutreachMessageVersion,
    replies: Iterable[OutreachReply],
    owner_timezone: str,
    keyring: DataKeyring,
) -> OutreachSentAttemptResponse:
    if (
        sent.event_type != "marked_sent"
        or sent.message_version_id != message.id
        or sent.kind != message.kind
        or sent.channel is None
    ):
        raise OutreachRepositoryError("outreach sent-attempt binding is invalid")
    return OutreachSentAttemptResponse(
        marked_sent_event_id=sent.id,
        message_version_id=message.id,
        version_number=message.version_number,
        kind=message.kind,
        body=_message_body(message, keyring=keyring),
        channel=sent.channel,
        sent_at=_as_utc(sent.occurred_at),
        sent_local_on=_owner_local_on(sent.occurred_at, timezone_name=owner_timezone),
        replies=[
            _reply_response(reply, message=message, keyring=keyring)
            for reply in replies
        ],
    )


def _reply_response(
    reply: OutreachReply,
    *,
    message: OutreachMessageVersion,
    keyring: DataKeyring,
) -> OutreachReplyResponse:
    if (
        reply.message_version_id != message.id
        or reply.message_kind != message.kind
    ):
        raise OutreachRepositoryError("outreach reply message binding is invalid")
    return OutreachReplyResponse(
        id=reply.id,
        marked_sent_event_id=reply.marked_sent_event_id,
        message_version_id=message.id,
        message_version_number=message.version_number,
        message_kind=reply.message_kind,
        reply_kind=reply.reply_kind,
        received_on=reply.received_on,
        note=_reply_note(reply, keyring=keyring),
        recorded_at=_as_utc(reply.recorded_at),
    )


def _reply_note(reply: OutreachReply, *, keyring: DataKeyring) -> str | None:
    if reply.encrypted_note is None and reply.note_key_id is None:
        return None
    if reply.encrypted_note is None or reply.note_key_id is None:
        raise OutreachRepositoryError("outreach reply note envelope is incomplete")
    payload = decrypt_private_payload(
        keyring,
        record_kind="outreach_reply_note",
        owner_id=reply.owner_id,
        record_id=reply.id,
        encryption_key_id=reply.note_key_id,
        ciphertext=reply.encrypted_note,
    )
    note = payload.get("note")
    if not isinstance(note, str) or not note.strip():
        raise OutreachRepositoryError("outreach reply note is invalid")
    return note


def _reply_timeline(
    replies: Iterable[OutreachReply],
    *,
    messages: dict[str, OutreachMessageVersion],
    keyring: DataKeyring,
) -> list[OutreachReplyRecordedTimelineEvent]:
    items: list[OutreachReplyRecordedTimelineEvent] = []
    for reply in replies:
        message = messages.get(reply.message_version_id)
        if message is None:
            raise OutreachRepositoryError("an outreach reply message is missing")
        response = _reply_response(reply, message=message, keyring=keyring)
        items.append(
            OutreachReplyRecordedTimelineEvent(
                id=response.id,
                sequence_id=reply.outreach_sequence_id,
                event_type="reply_recorded",
                application_contact_id=reply.application_contact_id,
                marked_sent_event_id=response.marked_sent_event_id,
                message_version_id=response.message_version_id,
                message_version_number=response.message_version_number,
                message_kind=response.message_kind,
                reply_kind=response.reply_kind,
                received_on=response.received_on,
                note=response.note,
                occurred_at=response.recorded_at,
            )
        )
    return items


def _timeline(
    events: Iterable[OutreachEvent],
    *,
    keyring: DataKeyring,
) -> list[
    OutreachSequenceStartedTimelineEvent
    | OutreachMessageSavedTimelineEvent
    | OutreachCopiedTimelineEvent
    | OutreachMarkedSentTimelineEvent
    | OutreachOutcomeTimelineEvent
    | OutreachPausedTimelineEvent
    | OutreachResumedTimelineEvent
    | OutreachStoppedTimelineEvent
    | OutreachWaveAdvancedTimelineEvent
]:
    items: list[
        OutreachSequenceStartedTimelineEvent
        | OutreachMessageSavedTimelineEvent
        | OutreachCopiedTimelineEvent
        | OutreachMarkedSentTimelineEvent
        | OutreachOutcomeTimelineEvent
        | OutreachPausedTimelineEvent
        | OutreachResumedTimelineEvent
        | OutreachStoppedTimelineEvent
        | OutreachWaveAdvancedTimelineEvent
    ] = []
    for event in events:
        common = {
            "id": event.id,
            "sequence_id": event.outreach_sequence_id,
            "occurred_at": _as_utc(event.occurred_at),
        }
        if event.event_type == "sequence_started" and event.wave == 1:
            items.append(
                OutreachSequenceStartedTimelineEvent(
                    **common,
                    event_type="sequence_started",
                    wave=1,
                )
            )
        elif (
            event.event_type == "message_saved"
            and event.application_contact_id is not None
            and event.message_version_id is not None
            and event.kind is not None
        ):
            items.append(
                OutreachMessageSavedTimelineEvent(
                    **common,
                    event_type="message_saved",
                    application_contact_id=event.application_contact_id,
                    message_version_id=event.message_version_id,
                    kind=event.kind,
                )
            )
        elif event.event_type == "copied" and event.message_version_id is not None:
            items.append(
                OutreachCopiedTimelineEvent(
                    **common,
                    event_type="copied",
                    message_version_id=event.message_version_id,
                )
            )
        elif (
            event.event_type == "marked_sent"
            and event.message_version_id is not None
            and event.channel is not None
        ):
            items.append(
                OutreachMarkedSentTimelineEvent(
                    **common,
                    event_type="marked_sent",
                    message_version_id=event.message_version_id,
                    channel=event.channel,
                )
            )
        elif (
            event.event_type == "outcome_recorded"
            and event.application_contact_id is not None
            and event.outcome is not None
        ):
            items.append(
                OutreachOutcomeTimelineEvent(
                    **common,
                    event_type="outcome_recorded",
                    application_contact_id=event.application_contact_id,
                    outcome=event.outcome,
                )
            )
        elif event.event_type in {"paused", "resumed", "stopped"}:
            reason = _event_reason(event, keyring=keyring)
            if event.event_type == "paused":
                items.append(
                    OutreachPausedTimelineEvent(
                        **common,
                        event_type="paused",
                        reason=reason,
                    )
                )
            elif event.event_type == "resumed":
                items.append(
                    OutreachResumedTimelineEvent(
                        **common,
                        event_type="resumed",
                        reason=reason,
                    )
                )
            else:
                items.append(
                    OutreachStoppedTimelineEvent(
                        **common,
                        event_type="stopped",
                        reason=reason,
                    )
                )
        elif event.event_type == "wave_advanced" and event.wave is not None:
            items.append(
                OutreachWaveAdvancedTimelineEvent(
                    **common,
                    event_type="wave_advanced",
                    wave=event.wave,
                )
            )
    return items[-MAX_TIMELINE_EVENTS:]


def _event_reason(event: OutreachEvent, *, keyring: DataKeyring) -> str:
    if event.encrypted_note is not None and event.note_key_id is not None:
        payload = decrypt_private_payload(
            keyring,
            record_kind="outreach_event_note",
            owner_id=event.owner_id,
            record_id=event.id,
            encryption_key_id=event.note_key_id,
            ciphertext=event.encrypted_note,
        )
        note = payload.get("note")
        if isinstance(note, str) and note.strip():
            return note
        raise OutreachRepositoryError("outreach event note is invalid")
    return (event.reason_code or event.event_type).replace("_", " ")


def _lock_sequence(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    sequence_id: str,
) -> OutreachSequence | None:
    return session.scalar(
        select(OutreachSequence)
        .where(
            OutreachSequence.owner_id == owner_id,
            OutreachSequence.application_id == application_id,
            OutreachSequence.id == sequence_id,
        )
        .with_for_update()
    )


def _lock_recipient(
    session: Session,
    *,
    sequence: OutreachSequence,
    application_contact_id: str,
) -> tuple[ApplicationContact, Contact]:
    row = session.execute(
        select(ApplicationContact, Contact)
        .join(
            Contact,
            (Contact.owner_id == ApplicationContact.owner_id)
            & (Contact.id == ApplicationContact.contact_id),
        )
        .where(
            ApplicationContact.owner_id == sequence.owner_id,
            ApplicationContact.application_id == sequence.application_id,
            ApplicationContact.contact_plan_id == sequence.contact_plan_id,
            ApplicationContact.id == application_contact_id,
            ApplicationContact.bench_rank.is_not(None),
        )
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise ResourceConflict("the person is not part of this outreach sequence")
    return row[0], row[1]


def _lock_message(
    session: Session,
    *,
    sequence: OutreachSequence,
    message_id: str,
) -> OutreachMessageVersion:
    message = session.scalar(
        select(OutreachMessageVersion)
        .where(
            OutreachMessageVersion.owner_id == sequence.owner_id,
            OutreachMessageVersion.application_id == sequence.application_id,
            OutreachMessageVersion.outreach_sequence_id == sequence.id,
            OutreachMessageVersion.id == message_id,
        )
        .with_for_update()
    )
    if message is None:
        raise ResourceConflict("the message version is not part of this sequence")
    return message


def _require_ready_recipient(
    sequence: OutreachSequence,
    application_contact: ApplicationContact,
    contact: Contact,
) -> None:
    if contact.lifecycle != "active":
        raise ResourceConflict("this person is restricted from outreach")
    if (
        application_contact.bench_state != "ready"
        or application_contact.wave != sequence.active_wave
    ):
        raise ResourceConflict("this person is still reserved for a later wave")


def _latest_message(
    session: Session,
    *,
    sequence: OutreachSequence,
    application_contact_id: str,
    kind: str,
) -> OutreachMessageVersion | None:
    return session.scalar(
        select(OutreachMessageVersion)
        .where(
            OutreachMessageVersion.owner_id == sequence.owner_id,
            OutreachMessageVersion.outreach_sequence_id == sequence.id,
            OutreachMessageVersion.application_contact_id == application_contact_id,
            OutreachMessageVersion.kind == kind,
        )
        .order_by(
            OutreachMessageVersion.version_number.desc(),
            OutreachMessageVersion.created_at.desc(),
            OutreachMessageVersion.id.desc(),
        )
        .limit(1)
    )


def _sent_event(
    session: Session,
    *,
    sequence: OutreachSequence,
    application_contact_id: str,
    kind: str,
) -> OutreachEvent | None:
    return session.scalar(
        select(OutreachEvent).where(
            OutreachEvent.owner_id == sequence.owner_id,
            OutreachEvent.outreach_sequence_id == sequence.id,
            OutreachEvent.application_contact_id == application_contact_id,
            OutreachEvent.event_type == "marked_sent",
            OutreachEvent.kind == kind,
        )
    )


def _latest_outcome(
    session: Session,
    *,
    sequence: OutreachSequence,
    application_contact_id: str,
) -> OutreachEvent | None:
    return session.scalar(
        select(OutreachEvent)
        .where(
            OutreachEvent.owner_id == sequence.owner_id,
            OutreachEvent.outreach_sequence_id == sequence.id,
            OutreachEvent.application_contact_id == application_contact_id,
            OutreachEvent.event_type == "outcome_recorded",
        )
        .order_by(
            OutreachEvent.sequence_number.desc(),
            OutreachEvent.id.desc(),
        )
        .limit(1)
    )


def _last_initial_send_for_contact(
    session: Session,
    *,
    owner_id: str,
    contact_id: str,
) -> datetime | None:
    return session.scalar(
        select(func.max(OutreachEvent.occurred_at))
        .join(
            ApplicationContact,
            (ApplicationContact.owner_id == OutreachEvent.owner_id)
            & (ApplicationContact.id == OutreachEvent.application_contact_id),
        )
        .where(
            OutreachEvent.owner_id == owner_id,
            OutreachEvent.event_type == "marked_sent",
            OutreachEvent.kind == "initial",
            ApplicationContact.contact_id == contact_id,
        )
    )


def _enforce_company_cold_limit(
    session: Session,
    *,
    sequence: OutreachSequence,
    application_contact: ApplicationContact,
    now: datetime,
) -> None:
    cutoff = now - timedelta(days=COMPANY_COLD_WINDOW_DAYS)
    count = session.scalar(
        select(func.count(func.distinct(ApplicationContact.contact_id)))
        .select_from(OutreachEvent)
        .join(
            ApplicationContact,
            (ApplicationContact.owner_id == OutreachEvent.owner_id)
            & (ApplicationContact.id == OutreachEvent.application_contact_id),
        )
        .where(
            OutreachEvent.owner_id == sequence.owner_id,
            OutreachEvent.event_type == "marked_sent",
            OutreachEvent.kind == "initial",
            OutreachEvent.occurred_at >= cutoff,
            func.lower(func.trim(ApplicationContact.current_company))
            == application_contact.current_company.strip().lower(),
            ApplicationContact.category.not_in(("recruiter", "warm_path")),
        )
    )
    if (count or 0) >= COMPANY_COLD_LIMIT:
        raise ResourceConflict(
            "three cold employee contacts at this company were already used in seven days"
        )


def _active_wave_is_resolved(
    session: Session,
    *,
    sequence: OutreachSequence,
) -> bool:
    if sequence.active_wave is None:
        return False
    remaining = session.scalar(
        select(func.count(ApplicationContact.id)).where(
            ApplicationContact.owner_id == sequence.owner_id,
            ApplicationContact.application_id == sequence.application_id,
            ApplicationContact.contact_plan_id == sequence.contact_plan_id,
            ApplicationContact.wave == sequence.active_wave,
            ApplicationContact.bench_rank.is_not(None),
            ApplicationContact.bench_state.in_(("ready", "paused")),
        )
    )
    return (remaining or 0) == 0


def _next_reserve_wave(
    session: Session,
    *,
    sequence: OutreachSequence,
    now: datetime,
) -> int | None:
    current_wave = sequence.active_wave or 0
    rows = list(
        session.execute(
            select(ApplicationContact, Contact)
            .join(
                Contact,
                (Contact.owner_id == ApplicationContact.owner_id)
                & (Contact.id == ApplicationContact.contact_id),
            )
            .where(
                ApplicationContact.owner_id == sequence.owner_id,
                ApplicationContact.application_id == sequence.application_id,
                ApplicationContact.contact_plan_id == sequence.contact_plan_id,
                ApplicationContact.wave > current_wave,
                ApplicationContact.bench_rank.is_not(None),
            )
            .order_by(ApplicationContact.wave.asc())
            .with_for_update()
        ).all()
    )
    for application_contact, contact in rows:
        cooldown = _optional_utc(application_contact.cooldown_until)
        if (
            application_contact.bench_state == "reserve"
            and contact.lifecycle == "active"
            and (cooldown is None or cooldown <= now)
        ):
            return cast(int, application_contact.wave)
        if contact.lifecycle != "active" and application_contact.bench_state != "stopped":
            application_contact.bench_state = "stopped"
            application_contact.version += 1
            application_contact.updated_at = now
    return None


def _unlock_wave(
    session: Session,
    *,
    sequence: OutreachSequence,
    wave: int,
    now: datetime,
) -> None:
    rows = list(
        session.scalars(
            select(ApplicationContact)
            .where(
                ApplicationContact.owner_id == sequence.owner_id,
                ApplicationContact.application_id == sequence.application_id,
                ApplicationContact.contact_plan_id == sequence.contact_plan_id,
                ApplicationContact.wave == wave,
                ApplicationContact.bench_state == "reserve",
            )
            .with_for_update()
        )
    )
    if not rows:
        raise OutreachRepositoryError("next outreach wave has no reserve recipient")
    for row in rows:
        row.bench_state = "ready"
        row.unlocked_at = now
        row.version += 1
        row.updated_at = now


def _pause_sequence_rows(
    session: Session,
    *,
    sequence: OutreachSequence,
    reason_code: str,
    now: datetime,
) -> None:
    sequence.status = "paused"
    sequence.reason_code = reason_code
    sequence.paused_at = now
    for row in _pinned_contacts(session, sequence=sequence, lock=True):
        if row.bench_state == "ready":
            row.bench_state = "paused"
            row.version += 1
            row.updated_at = now


def _resume_sequence_rows(
    session: Session,
    *,
    sequence: OutreachSequence,
    now: datetime,
) -> int | None:
    sequence.status = "active"
    sequence.reason_code = None
    sequence.paused_at = None
    rows = session.execute(
        select(ApplicationContact, Contact)
        .join(
            Contact,
            (Contact.owner_id == ApplicationContact.owner_id)
            & (Contact.id == ApplicationContact.contact_id),
        )
        .where(
            ApplicationContact.owner_id == sequence.owner_id,
            ApplicationContact.application_id == sequence.application_id,
            ApplicationContact.contact_plan_id == sequence.contact_plan_id,
            ApplicationContact.bench_rank.is_not(None),
        )
        .with_for_update()
    ).all()
    restored = 0
    for row, contact in rows:
        if row.bench_state == "paused" and row.wave == sequence.active_wave:
            row.bench_state = "ready" if contact.lifecycle == "active" else "stopped"
            row.version += 1
            row.updated_at = now
            if row.bench_state == "ready":
                restored += 1
    if restored > 0:
        return None

    session.flush()
    if not _active_wave_is_resolved(session, sequence=sequence):
        raise ResourceConflict("no active recipient remains in the paused wave")
    next_wave = _next_reserve_wave(session, sequence=sequence, now=now)
    if next_wave is None:
        sequence.status = "completed"
        sequence.active_wave = None
        sequence.reason_code = None
        sequence.paused_at = None
        sequence.stopped_at = None
        sequence.completed_at = now
        return None
    _unlock_wave(session, sequence=sequence, wave=next_wave, now=now)
    sequence.active_wave = next_wave
    return next_wave


def _stop_sequence_rows(
    session: Session,
    *,
    sequence: OutreachSequence,
    reason_code: str,
    now: datetime,
) -> None:
    sequence.status = "stopped"
    sequence.active_wave = None
    sequence.reason_code = reason_code
    sequence.paused_at = None
    sequence.stopped_at = now
    sequence.completed_at = None
    for row in _pinned_contacts(session, sequence=sequence, lock=True):
        if row.bench_state != "stopped":
            row.bench_state = "stopped"
            row.version += 1
            row.updated_at = now


def _pinned_contacts(
    session: Session,
    *,
    sequence: OutreachSequence,
    lock: bool,
) -> list[ApplicationContact]:
    statement = select(ApplicationContact).where(
        ApplicationContact.owner_id == sequence.owner_id,
        ApplicationContact.application_id == sequence.application_id,
        ApplicationContact.contact_plan_id == sequence.contact_plan_id,
        ApplicationContact.bench_rank.is_not(None),
    )
    if lock:
        statement = statement.with_for_update()
    return list(session.scalars(statement))


def _add_reasoned_event(
    session: Session,
    *,
    sequence: OutreachSequence,
    sequence_number: int,
    event_type: str,
    reason_code: str,
    note: str,
    keyring: DataKeyring,
    occurred_at: datetime,
    mutation_hash: str,
) -> OutreachEvent:
    event_id = uuid4().hex
    envelope = encrypt_private_payload(
        keyring,
        record_kind="outreach_event_note",
        owner_id=sequence.owner_id,
        record_id=event_id,
        payload={"note": note},
    )
    event = OutreachEvent(
        id=event_id,
        owner_id=sequence.owner_id,
        application_id=sequence.application_id,
        outreach_sequence_id=sequence.id,
        sequence_number=sequence_number,
        event_type=event_type,
        reason_code=reason_code,
        encrypted_note=envelope.ciphertext,
        note_key_id=envelope.key_id,
        occurred_at=occurred_at,
        idempotency_key_hash=mutation_hash,
        created_at=occurred_at,
    )
    session.add(event)
    return event


def _add_sequence_event(
    session: Session,
    *,
    sequence: OutreachSequence,
    sequence_number: int,
    event_type: str,
    occurred_at: datetime,
    mutation_hash: str,
    application_contact_id: str | None = None,
    message_version_id: str | None = None,
    kind: str | None = None,
    channel: str | None = None,
    outcome: str | None = None,
    reason_code: str | None = None,
    wave: int | None = None,
    follow_up_due_at: datetime | None = None,
) -> OutreachEvent:
    event = OutreachEvent(
        owner_id=sequence.owner_id,
        application_id=sequence.application_id,
        outreach_sequence_id=sequence.id,
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
        occurred_at=occurred_at,
        idempotency_key_hash=mutation_hash,
        created_at=occurred_at,
    )
    session.add(event)
    return event


def _next_event_number(session: Session, sequence: OutreachSequence) -> int:
    return (
        session.scalar(
            select(func.max(OutreachEvent.sequence_number)).where(
                OutreachEvent.owner_id == sequence.owner_id,
                OutreachEvent.outreach_sequence_id == sequence.id,
            )
        )
        or 0
    ) + 1


def _posting_is_open(session: Session, application: Application) -> bool:
    state = session.scalar(
        select(JobPosting.lifecycle_state).where(
            JobPosting.owner_id == application.owner_id,
            JobPosting.id == application.job_posting_id,
        )
    )
    return state == "open"


def _sequence_stop_reason(
    session: Session,
    sequence: OutreachSequence,
) -> str | None:
    application = session.scalar(
        select(Application).where(
            Application.owner_id == sequence.owner_id,
            Application.id == sequence.application_id,
        )
    )
    if application is None:
        raise OutreachRepositoryError("outreach application is missing")
    if application.stage not in CONTACTABLE_APPLICATION_STAGE_VALUES:
        return "application_terminal"
    interview_progress = session.scalar(
        select(ApplicationInterviewRound.id)
        .where(
            ApplicationInterviewRound.owner_id == sequence.owner_id,
            ApplicationInterviewRound.application_id == sequence.application_id,
        )
        .limit(1)
    )
    if interview_progress is not None:
        return "hiring_progress"
    if not _posting_is_open(session, application):
        return "posting_closed"
    return None


def _event_hash(idempotency_key: str, suffix: str) -> str:
    return hashlib.sha256(f"{idempotency_key}:{suffix}".encode("utf-8")).hexdigest()


def _require_replay_type(actual: str, expected: str) -> None:
    if actual != expected:
        raise OutreachRepositoryError("idempotent outreach result has the wrong type")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_utc(value: datetime | None) -> datetime | None:
    return _as_utc(value) if value is not None else None


__all__ = [
    "OutreachRepositoryError",
    "add_business_days",
    "load_application_outreach",
    "record_outreach_event",
    "record_outreach_reply",
    "save_outreach_message",
    "start_outreach_sequence",
]
