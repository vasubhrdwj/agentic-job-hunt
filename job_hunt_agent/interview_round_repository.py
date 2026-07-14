"""Atomic interview-round scheduling and lifecycle persistence."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .application_repository import _application_summary
from .interview_round_schemas import (
    ApplicationInterviewRoundsResponse,
    InterviewRoundCancelledCreate,
    InterviewRoundCompletedCreate,
    InterviewRoundCreate,
    InterviewRoundEventCreate,
    InterviewRoundEventResponse,
    InterviewRoundMutationResponse,
    InterviewRoundRescheduledCreate,
    InterviewRoundResponse,
)
from .job_queue import utcnow
from .models import (
    ActionItem,
    Application,
    ApplicationActivityEvent,
    ApplicationInterviewRound,
    ApplicationInterviewRoundEvent,
    ApplicationSubmission,
    Owner,
)
from .mutation_receipts import claim_owner_mutation, complete_owner_mutation
from .repository_errors import ResourceConflict, require_version


_MAX_ROUNDS = 100
_MAX_EVENTS = 500
_MAX_DATE_WINDOW_DAYS = 365
_SCHEDULABLE_STAGES = frozenset({"applied", "screening", "interviewing"})
_DEFAULT_ACTION_BY_STAGE = {
    "applied": ("follow_up_application", "Follow up on application"),
    "screening": ("prepare_recruiter_screen", "Follow up after recruiter screen"),
    "interviewing": ("prepare_interview", "Follow up after interview"),
}


class InterviewRoundRepositoryError(RuntimeError):
    """A persisted interview-round graph violated a product invariant."""


def load_application_interview_rounds(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
) -> ApplicationInterviewRoundsResponse | None:
    """Load saved rounds and events without providers or background work."""

    application = _owned_application(session, owner_id, application_id)
    if application is None:
        return None
    return _rounds_projection(session, application=application)


def schedule_interview_round(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    payload: InterviewRoundCreate,
    expected_application_version: int,
    idempotency_key: str,
    now: datetime | None = None,
) -> InterviewRoundMutationResponse | None:
    """Schedule one stable round and make its preparation the current task."""

    current = _as_utc(now or utcnow())
    application = _owned_application(session, owner_id, application_id, lock=True)
    if application is None:
        return None
    mutation_namespace = f"interview_round.schedule:{application.id}"
    claim = claim_owner_mutation(
        session,
        owner_id=owner_id,
        namespace=mutation_namespace,
        idempotency_key=idempotency_key,
        request={
            "payload": payload.model_dump(mode="json"),
            "expected_application_version": expected_application_version,
        },
        now=current,
    )
    if claim.replay is not None:
        return _replayed_mutation(
            session,
            application=application,
            event_id=claim.replay.resource_id,
            resource_type=claim.replay.resource_type,
            result_version=claim.replay.result_version,
        )

    require_version(
        "application",
        application.id,
        expected=expected_application_version,
        actual=application.version,
    )
    _require_schedulable_application(application)
    owner = _owner(session, owner_id)
    submission = _submission(session, application, lock=True)
    previous_action = _current_action(session, application, lock=True)
    if _scheduled_round(session, application, lock=True) is not None:
        raise ResourceConflict(
            "complete or cancel the scheduled interview before adding another round"
        )
    _require_unlinked_stage_action(application, previous_action)
    round_count = int(
        session.scalar(
            select(func.count(ApplicationInterviewRound.id)).where(
                ApplicationInterviewRound.owner_id == owner_id,
                ApplicationInterviewRound.application_id == application.id,
            )
        )
        or 0
    )
    if round_count >= _MAX_ROUNDS:
        raise ResourceConflict("this application has reached the interview-round limit")

    start_at = _validated_appointment(
        scheduled_local=payload.scheduled_local,
        scheduled_timezone=payload.scheduled_timezone,
        owner_timezone=owner.timezone,
        next_action_due_on=payload.next_action_due_on,
        now=current,
    )
    round_ = ApplicationInterviewRound(
        id=uuid4().hex,
        owner_id=owner_id,
        application_id=application.id,
        application_submission_id=submission.id,
        round_number=round_count + 1,
        kind=payload.kind.value,
        title=payload.title,
        status="scheduled",
        scheduled_start_at=start_at,
        scheduled_timezone=payload.scheduled_timezone,
        duration_minutes=payload.duration_minutes,
        meeting_format=payload.meeting_format.value,
        completed_on=None,
        cancelled_on=None,
        cancelled_by=None,
        version=1,
        created_at=current,
        updated_at=current,
    )
    session.add(round_)
    session.flush()
    next_action = _replace_action(
        session,
        application=application,
        previous_action=previous_action,
        previous_status="completed",
        kind="prepare_interview",
        title=f"Prepare for {round_.title}",
        due_on=payload.next_action_due_on,
        interview_round_id=round_.id,
        now=current,
    )
    application.version += 1
    application.updated_at = current
    event = _new_round_event(
        round_=round_,
        sequence_number=1,
        event_type="scheduled",
        from_status=None,
        to_status="scheduled",
        effective_on=None,
        cancelled_by=None,
        previous_action=previous_action,
        action=next_action,
        idempotency_namespace=mutation_namespace,
        idempotency_key=idempotency_key,
        now=current,
    )
    session.add(event)
    session.flush()
    complete_owner_mutation(
        session,
        owner_id=owner_id,
        receipt_id=claim.receipt_id,
        resource_type="interview_round_event",
        resource_id=event.id,
        result_version=round_.version,
        now=current,
    )
    return _mutation_response(
        session,
        application=application,
        round_=round_,
        event=event,
        mutation_created=True,
    )


def record_interview_round_event(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    interview_round_id: str,
    payload: InterviewRoundEventCreate,
    expected_round_version: int,
    idempotency_key: str,
    now: datetime | None = None,
) -> InterviewRoundMutationResponse | None:
    """Reschedule, complete, or cancel the current appointment atomically."""

    current = _as_utc(now or utcnow())
    application = _owned_application(session, owner_id, application_id, lock=True)
    if application is None:
        return None
    round_ = _owned_round(
        session,
        owner_id=owner_id,
        application_id=application.id,
        interview_round_id=interview_round_id,
        lock=True,
    )
    if round_ is None:
        return None
    mutation_namespace = f"interview_round.event:{round_.id}"
    claim = claim_owner_mutation(
        session,
        owner_id=owner_id,
        namespace=mutation_namespace,
        idempotency_key=idempotency_key,
        request={
            "payload": payload.model_dump(mode="json"),
            "expected_round_version": expected_round_version,
        },
        now=current,
    )
    if claim.replay is not None:
        return _replayed_mutation(
            session,
            application=application,
            event_id=claim.replay.resource_id,
            resource_type=claim.replay.resource_type,
            result_version=claim.replay.result_version,
        )

    require_version(
        "interview round",
        round_.id,
        expected=expected_round_version,
        actual=round_.version,
    )
    _require_schedulable_application(application)
    if round_.status != "scheduled":
        raise ResourceConflict("this interview round is already resolved")
    scheduled = _scheduled_round(session, application, lock=True)
    if scheduled is None or scheduled.id != round_.id:
        raise InterviewRoundRepositoryError(
            "the current scheduled interview round is inconsistent"
        )
    owner = _owner(session, owner_id)
    previous_action = _current_action(session, application, lock=True)
    if (
        previous_action.kind != "prepare_interview"
        or previous_action.interview_round_id != round_.id
    ):
        raise InterviewRoundRepositoryError(
            "the scheduled interview round does not own the current action"
        )

    if isinstance(payload, InterviewRoundRescheduledCreate):
        start_at = _validated_appointment(
            scheduled_local=payload.scheduled_local,
            scheduled_timezone=payload.scheduled_timezone,
            owner_timezone=owner.timezone,
            next_action_due_on=payload.next_action_due_on,
            now=current,
        )
        round_.scheduled_start_at = start_at
        round_.scheduled_timezone = payload.scheduled_timezone
        round_.duration_minutes = payload.duration_minutes
        round_.meeting_format = payload.meeting_format.value
        effective_on = None
        cancelled_by = None
        event_type = "rescheduled"
        to_status = "scheduled"
        next_action = _replace_action(
            session,
            application=application,
            previous_action=previous_action,
            previous_status="cancelled",
            kind="prepare_interview",
            title=f"Prepare for {round_.title}",
            due_on=payload.next_action_due_on,
            interview_round_id=round_.id,
            now=current,
        )
    elif isinstance(payload, InterviewRoundCompletedCreate):
        scheduled_at = _as_utc(round_.scheduled_start_at)
        if current < scheduled_at:
            raise ValueError("an interview round cannot be completed before it starts")
        local_today = _owner_local_date(current, owner.timezone)
        scheduled_on = _owner_local_date(scheduled_at, owner.timezone)
        _require_effective_date(
            payload.completed_on,
            not_before=scheduled_on,
            local_today=local_today,
            field_name="completed_on",
        )
        _require_next_action_due_on(payload.next_action_due_on, local_today)
        previous_stage = application.stage
        if previous_stage not in _SCHEDULABLE_STAGES:
            raise ResourceConflict("this application cannot complete an interview")
        application.stage = "interviewing"
        round_.status = "completed"
        round_.completed_on = payload.completed_on
        round_.cancelled_on = None
        round_.cancelled_by = None
        effective_on = payload.completed_on
        cancelled_by = None
        event_type = "completed"
        to_status = "completed"
        next_action = _replace_action(
            session,
            application=application,
            previous_action=previous_action,
            previous_status="completed",
            kind="prepare_interview",
            title=f"Follow up after {round_.title}",
            due_on=payload.next_action_due_on,
            interview_round_id=None,
            now=current,
        )
        if previous_stage in {"applied", "screening"}:
            activity = ApplicationActivityEvent(
                id=uuid4().hex,
                owner_id=owner_id,
                application_id=application.id,
                sequence_number=_next_activity_sequence(session, application),
                event_type="application_interviewing",
                from_stage=previous_stage,
                to_stage="interviewing",
                action_item_id=next_action.id,
                previous_action_item_id=previous_action.id,
                submission_id=None,
                effective_on=payload.completed_on,
                outcome_id=None,
                interview_round_id=round_.id,
                occurred_at=current,
                created_at=current,
            )
            session.add(activity)
    elif isinstance(payload, InterviewRoundCancelledCreate):
        local_today = _owner_local_date(current, owner.timezone)
        latest_event_on = _owner_local_date(
            _as_utc(round_.updated_at), owner.timezone
        )
        _require_effective_date(
            payload.cancelled_on,
            not_before=latest_event_on,
            local_today=local_today,
            field_name="cancelled_on",
        )
        _require_next_action_due_on(payload.next_action_due_on, local_today)
        default_action = _DEFAULT_ACTION_BY_STAGE.get(application.stage)
        if default_action is None:
            raise ResourceConflict("this application cannot cancel an interview")
        round_.status = "cancelled"
        round_.completed_on = None
        round_.cancelled_on = payload.cancelled_on
        round_.cancelled_by = payload.cancelled_by.value
        effective_on = payload.cancelled_on
        cancelled_by = payload.cancelled_by.value
        event_type = "cancelled"
        to_status = "cancelled"
        next_action = _replace_action(
            session,
            application=application,
            previous_action=previous_action,
            previous_status="cancelled",
            kind=default_action[0],
            title=default_action[1],
            due_on=payload.next_action_due_on,
            interview_round_id=None,
            now=current,
        )
    else:  # pragma: no cover - the discriminated schema makes this unreachable.
        raise ValueError("unsupported interview-round event")

    round_.version += 1
    round_.updated_at = current
    application.version += 1
    application.updated_at = current
    event = _new_round_event(
        round_=round_,
        sequence_number=round_.version,
        event_type=event_type,
        from_status="scheduled",
        to_status=to_status,
        effective_on=effective_on,
        cancelled_by=cancelled_by,
        previous_action=previous_action,
        action=next_action,
        idempotency_namespace=mutation_namespace,
        idempotency_key=idempotency_key,
        now=current,
    )
    session.add(event)
    session.flush()
    complete_owner_mutation(
        session,
        owner_id=owner_id,
        receipt_id=claim.receipt_id,
        resource_type="interview_round_event",
        resource_id=event.id,
        result_version=round_.version,
        now=current,
    )
    return _mutation_response(
        session,
        application=application,
        round_=round_,
        event=event,
        mutation_created=True,
    )


def _rounds_projection(
    session: Session,
    *,
    application: Application,
) -> ApplicationInterviewRoundsResponse:
    rounds = list(
        session.scalars(
            select(ApplicationInterviewRound)
            .where(
                ApplicationInterviewRound.owner_id == application.owner_id,
                ApplicationInterviewRound.application_id == application.id,
            )
            .order_by(
                ApplicationInterviewRound.round_number,
                ApplicationInterviewRound.id,
            )
            .limit(_MAX_ROUNDS + 1)
        )
    )
    if len(rounds) > _MAX_ROUNDS:
        raise InterviewRoundRepositoryError(
            "the application exceeds the interview-round projection limit"
        )
    return ApplicationInterviewRoundsResponse(
        application=_application_summary(session, application),
        rounds=[_round_response(session, row) for row in rounds],
    )


def _mutation_response(
    session: Session,
    *,
    application: Application,
    round_: ApplicationInterviewRound,
    event: ApplicationInterviewRoundEvent,
    mutation_created: bool,
) -> InterviewRoundMutationResponse:
    round_response = _round_response(session, round_)
    event_response = _event_response(event)
    if round_response.events[-1].id != event_response.id:
        raise InterviewRoundRepositoryError(
            "the interview-round mutation is not the latest event"
        )
    return InterviewRoundMutationResponse(
        application=_application_summary(session, application),
        round=round_response,
        event=event_response,
        mutation_created=mutation_created,
    )


def _round_response(
    session: Session,
    row: ApplicationInterviewRound,
) -> InterviewRoundResponse:
    events = list(
        session.scalars(
            select(ApplicationInterviewRoundEvent)
            .where(
                ApplicationInterviewRoundEvent.owner_id == row.owner_id,
                ApplicationInterviewRoundEvent.application_id == row.application_id,
                ApplicationInterviewRoundEvent.interview_round_id == row.id,
            )
            .order_by(
                ApplicationInterviewRoundEvent.sequence_number,
                ApplicationInterviewRoundEvent.id,
            )
            .limit(_MAX_EVENTS + 1)
        )
    )
    if len(events) > _MAX_EVENTS:
        raise InterviewRoundRepositoryError(
            "an interview round exceeds the event projection limit"
        )
    return InterviewRoundResponse(
        id=row.id,
        version=row.version,
        application_id=row.application_id,
        application_submission_id=row.application_submission_id,
        round_number=row.round_number,
        kind=row.kind,
        title=row.title,
        status=row.status,
        scheduled_start_at=_as_utc(row.scheduled_start_at),
        scheduled_timezone=row.scheduled_timezone,
        duration_minutes=row.duration_minutes,
        meeting_format=row.meeting_format,
        completed_on=row.completed_on,
        cancelled_on=row.cancelled_on,
        cancelled_by=row.cancelled_by,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        events=[_event_response(event) for event in events],
    )


def _event_response(
    row: ApplicationInterviewRoundEvent,
) -> InterviewRoundEventResponse:
    return InterviewRoundEventResponse(
        id=row.id,
        application_id=row.application_id,
        interview_round_id=row.interview_round_id,
        sequence_number=row.sequence_number,
        event_type=row.event_type,
        from_status=row.from_status,
        to_status=row.to_status,
        scheduled_start_at=_as_utc(row.scheduled_start_at),
        scheduled_timezone=row.scheduled_timezone,
        duration_minutes=row.duration_minutes,
        meeting_format=row.meeting_format,
        effective_on=row.effective_on,
        cancelled_by=row.cancelled_by,
        previous_action_item_id=row.previous_action_item_id,
        action_item_id=row.action_item_id,
        recording_method=row.recording_method,
        occurred_at=_as_utc(row.occurred_at),
        created_at=_as_utc(row.created_at),
    )


def _replayed_mutation(
    session: Session,
    *,
    application: Application,
    event_id: str,
    resource_type: str,
    result_version: int | None,
) -> InterviewRoundMutationResponse:
    if resource_type != "interview_round_event" or result_version is None:
        raise InterviewRoundRepositoryError(
            "interview-round receipt has an invalid result"
        )
    event = session.scalar(
        select(ApplicationInterviewRoundEvent).where(
            ApplicationInterviewRoundEvent.owner_id == application.owner_id,
            ApplicationInterviewRoundEvent.application_id == application.id,
            ApplicationInterviewRoundEvent.id == event_id,
        )
    )
    if event is None:
        raise InterviewRoundRepositoryError(
            "interview-round replay event is unavailable"
        )
    round_ = _owned_round(
        session,
        owner_id=application.owner_id,
        application_id=application.id,
        interview_round_id=event.interview_round_id,
    )
    if round_ is None:
        raise InterviewRoundRepositoryError(
            "interview-round replay resource is unavailable"
        )
    if round_.version != result_version or event.sequence_number != result_version:
        raise ResourceConflict(
            "the interview round changed after this update; reload its current state"
        )
    return _mutation_response(
        session,
        application=application,
        round_=round_,
        event=event,
        mutation_created=False,
    )


def _new_round_event(
    *,
    round_: ApplicationInterviewRound,
    sequence_number: int,
    event_type: str,
    from_status: str | None,
    to_status: str,
    effective_on: date | None,
    cancelled_by: str | None,
    previous_action: ActionItem,
    action: ActionItem,
    idempotency_namespace: str,
    idempotency_key: str,
    now: datetime,
) -> ApplicationInterviewRoundEvent:
    return ApplicationInterviewRoundEvent(
        id=uuid4().hex,
        owner_id=round_.owner_id,
        application_id=round_.application_id,
        interview_round_id=round_.id,
        sequence_number=sequence_number,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        scheduled_start_at=round_.scheduled_start_at,
        scheduled_timezone=round_.scheduled_timezone,
        duration_minutes=round_.duration_minutes,
        meeting_format=round_.meeting_format,
        effective_on=effective_on,
        cancelled_by=cancelled_by,
        previous_action_item_id=previous_action.id,
        action_item_id=action.id,
        recording_method="manual",
        idempotency_key_hash=_sha256(
            f"{idempotency_namespace.strip()}:{idempotency_key.strip()}"
        ),
        occurred_at=now,
        created_at=now,
    )


def _replace_action(
    session: Session,
    *,
    application: Application,
    previous_action: ActionItem,
    previous_status: str,
    kind: str,
    title: str,
    due_on: date,
    interview_round_id: str | None,
    now: datetime,
) -> ActionItem:
    if previous_status == "completed":
        previous_action.status = "completed"
        previous_action.completed_at = now
        previous_action.cancelled_at = None
    elif previous_status == "cancelled":
        previous_action.status = "cancelled"
        previous_action.completed_at = None
        previous_action.cancelled_at = now
    else:  # pragma: no cover - internal callers are fixed.
        raise ValueError("unsupported previous action status")
    previous_action.version += 1
    previous_action.updated_at = now
    session.flush()

    action = ActionItem(
        id=uuid4().hex,
        owner_id=application.owner_id,
        application_id=application.id,
        interview_round_id=interview_round_id,
        kind=kind,
        title=title,
        status="open",
        due_on=due_on,
        version=1,
        completed_at=None,
        cancelled_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(action)
    session.flush()
    return action


def _validated_appointment(
    *,
    scheduled_local: datetime,
    scheduled_timezone: str,
    owner_timezone: str,
    next_action_due_on: date,
    now: datetime,
) -> datetime:
    start_at = _resolve_local_datetime(scheduled_local, scheduled_timezone)
    if start_at <= now:
        raise ValueError("scheduled_local must be in the future")
    local_today = _owner_local_date(now, owner_timezone)
    appointment_on = _owner_local_date(start_at, owner_timezone)
    if appointment_on > local_today + timedelta(days=_MAX_DATE_WINDOW_DAYS):
        raise ValueError("scheduled_local must be within 365 owner-local days")
    if not local_today <= next_action_due_on <= appointment_on:
        raise ValueError(
            "next_action_due_on must be from today through the appointment date"
        )
    return start_at


def _resolve_local_datetime(value: datetime, timezone_name: str) -> datetime:
    if value.tzinfo is not None and value.utcoffset() is not None:
        raise ValueError("scheduled_local must not include a UTC offset")
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("scheduled_timezone must be a valid IANA timezone") from exc

    local = value.replace(tzinfo=None)
    candidates: list[datetime] = []
    for fold in (0, 1):
        candidate = local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        round_trip = candidate.astimezone(zone).replace(tzinfo=None)
        if round_trip == local and candidate not in candidates:
            candidates.append(candidate)
    if not candidates:
        raise ValueError("scheduled_local falls in a daylight-saving time gap")
    if len(candidates) > 1:
        raise ValueError("scheduled_local is ambiguous in scheduled_timezone")
    return candidates[0]


def _require_effective_date(
    value: date,
    *,
    not_before: date,
    local_today: date,
    field_name: str,
) -> None:
    if value < not_before or value > local_today:
        raise ValueError(
            f"{field_name} must be on or after the scheduled record and not in the future"
        )


def _require_next_action_due_on(value: date, local_today: date) -> None:
    if not local_today <= value <= local_today + timedelta(days=_MAX_DATE_WINDOW_DAYS):
        raise ValueError("next_action_due_on must be from today through 365 days")


def _require_schedulable_application(application: Application) -> None:
    if application.stage not in _SCHEDULABLE_STAGES or application.outcome_id is not None:
        raise ResourceConflict(
            "interview rounds require an applied, screening, or interviewing application"
        )


def _require_unlinked_stage_action(
    application: Application,
    current_action: ActionItem,
) -> None:
    default_action = _DEFAULT_ACTION_BY_STAGE.get(application.stage)
    if (
        default_action is None
        or current_action.kind != default_action[0]
        or current_action.interview_round_id is not None
    ):
        raise InterviewRoundRepositoryError(
            "the current action is inconsistent with the application stage"
        )


def _owned_application(
    session: Session,
    owner_id: str,
    application_id: str,
    *,
    lock: bool = False,
) -> Application | None:
    statement = select(Application).where(
        Application.owner_id == owner_id,
        Application.id == application_id,
    )
    if lock:
        statement = statement.with_for_update()
    return session.scalar(statement)


def _owned_round(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    interview_round_id: str,
    lock: bool = False,
) -> ApplicationInterviewRound | None:
    statement = select(ApplicationInterviewRound).where(
        ApplicationInterviewRound.owner_id == owner_id,
        ApplicationInterviewRound.application_id == application_id,
        ApplicationInterviewRound.id == interview_round_id,
    )
    if lock:
        statement = statement.with_for_update()
    return session.scalar(statement)


def _scheduled_round(
    session: Session,
    application: Application,
    *,
    lock: bool = False,
) -> ApplicationInterviewRound | None:
    statement = select(ApplicationInterviewRound).where(
        ApplicationInterviewRound.owner_id == application.owner_id,
        ApplicationInterviewRound.application_id == application.id,
        ApplicationInterviewRound.status == "scheduled",
    )
    if lock:
        statement = statement.with_for_update()
    return session.scalar(statement)


def _current_action(
    session: Session,
    application: Application,
    *,
    lock: bool = False,
) -> ActionItem:
    statement = select(ActionItem).where(
        ActionItem.owner_id == application.owner_id,
        ActionItem.application_id == application.id,
        ActionItem.status == "open",
    )
    if lock:
        statement = statement.with_for_update()
    rows = list(session.scalars(statement))
    if len(rows) != 1:
        raise InterviewRoundRepositoryError(
            "an active interview application must have exactly one current action"
        )
    return rows[0]


def _submission(
    session: Session,
    application: Application,
    *,
    lock: bool = False,
) -> ApplicationSubmission:
    statement = select(ApplicationSubmission).where(
        ApplicationSubmission.owner_id == application.owner_id,
        ApplicationSubmission.application_id == application.id,
    )
    if lock:
        statement = statement.with_for_update()
    row = session.scalar(statement)
    if row is None:
        raise InterviewRoundRepositoryError(
            "an interview round requires the exact application submission"
        )
    return row


def _owner(session: Session, owner_id: str) -> Owner:
    row = session.scalar(select(Owner).where(Owner.id == owner_id))
    if row is None:
        raise InterviewRoundRepositoryError("interview-round owner is unavailable")
    try:
        ZoneInfo(row.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InterviewRoundRepositoryError(
            "interview-round owner timezone is invalid"
        ) from exc
    return row


def _next_activity_sequence(session: Session, application: Application) -> int:
    return int(
        session.scalar(
            select(func.max(ApplicationActivityEvent.sequence_number)).where(
                ApplicationActivityEvent.owner_id == application.owner_id,
                ApplicationActivityEvent.application_id == application.id,
            )
        )
        or 0
    ) + 1


def _owner_local_date(value: datetime, timezone_name: str) -> date:
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InterviewRoundRepositoryError(
            "interview-round timezone is invalid"
        ) from exc
    return _as_utc(value).astimezone(zone).date()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "InterviewRoundRepositoryError",
    "load_application_interview_rounds",
    "record_interview_round_event",
    "schedule_interview_round",
]
