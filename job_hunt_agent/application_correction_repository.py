"""Atomic append-only corrections for coarse application milestone dates."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from .application_milestone_dates import (
    ApplicationMilestoneDateError,
    CORRECTABLE_MILESTONE_TYPES,
    milestone_correction_window,
    resolved_application_milestone_dates,
)
from .application_repository import (
    _activity_response,
    _application_summary,
    _correction_response,
)
from .application_schemas import (
    ApplicationMilestoneCorrectionCreate,
    ApplicationMilestoneCorrectionMutationResponse,
    MAX_MILESTONE_CORRECTIONS_PER_EVENT,
)
from .job_queue import utcnow
from .models import (
    Application,
    ApplicationActivityEvent,
    ApplicationMilestoneCorrection,
    Owner,
)
from .mutation_receipts import claim_owner_mutation, complete_owner_mutation
from .repository_errors import ResourceConflict, require_version


class ApplicationCorrectionRepositoryError(RuntimeError):
    """A saved milestone-correction graph violated a product invariant."""


def record_application_milestone_correction(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    activity_event_id: str,
    payload: ApplicationMilestoneCorrectionCreate,
    expected_application_version: int,
    idempotency_key: str,
    now: datetime | None = None,
) -> ApplicationMilestoneCorrectionMutationResponse | None:
    """Append one date correction while preserving stage and action history."""

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
    target = session.scalar(
        select(ApplicationActivityEvent)
        .where(
            ApplicationActivityEvent.owner_id == owner_id,
            ApplicationActivityEvent.application_id == application.id,
            ApplicationActivityEvent.id == activity_event_id,
        )
        .with_for_update()
    )
    if target is None:
        return None
    namespace = (
        f"application.milestone_correction:{application.id}:{activity_event_id}"
    )
    claim = claim_owner_mutation(
        session,
        owner_id=owner_id,
        namespace=namespace,
        idempotency_key=idempotency_key,
        request={
            "payload": payload.model_dump(mode="json"),
            "expected_application_version": expected_application_version,
        },
        now=current,
    )
    if claim.replay is not None:
        if claim.replay.resource_type != "application_milestone_correction":
            raise ApplicationCorrectionRepositoryError(
                "milestone-correction receipt has the wrong resource type"
            )
        if (
            claim.replay.result_version is None
            or claim.replay.result_version > application.version
        ):
            raise ApplicationCorrectionRepositoryError(
                "milestone-correction receipt has an invalid result version"
            )
        return _replayed_correction(
            session,
            application=application,
            activity_event_id=activity_event_id,
            correction_id=claim.replay.resource_id,
        )

    require_version(
        "application",
        application.id,
        expected=expected_application_version,
        actual=application.version,
    )
    if (
        target.event_type not in CORRECTABLE_MILESTONE_TYPES
        or target.effective_on is None
        or target.interview_round_id is not None
    ):
        raise ResourceConflict(
            "only a manually recorded screening, interview, or offer milestone "
            "can have its date corrected"
        )
    owner = session.scalar(select(Owner).where(Owner.id == owner_id))
    if owner is None:
        raise ApplicationCorrectionRepositoryError(
            "milestone-correction owner is unavailable"
        )
    try:
        local_today = current.astimezone(ZoneInfo(owner.timezone)).date()
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ApplicationCorrectionRepositoryError(
            "milestone-correction owner timezone is invalid"
        ) from exc

    corrections = _correction_rows(
        session,
        owner_id=owner_id,
        application_id=application.id,
        activity_event_id=target.id,
        lock=True,
    )
    try:
        resolved = resolved_application_milestone_dates(
            session,
            owner_id=owner_id,
            application_id=application.id,
        )
        not_before, not_after = milestone_correction_window(
            session,
            target=target,
            owner_local_today=local_today,
        )
    except ApplicationMilestoneDateError as exc:
        raise ApplicationCorrectionRepositoryError(str(exc)) from exc
    previous_effective_on = resolved.get(target.id)
    if previous_effective_on is None:
        raise ApplicationCorrectionRepositoryError(
            "correctable milestone has no effective date"
        )
    corrected_on = payload.corrected_effective_on
    if corrected_on == previous_effective_on:
        raise ResourceConflict(
            "the corrected milestone date must differ from its current saved date"
        )
    if corrected_on < not_before or corrected_on > not_after:
        raise ValueError(
            "corrected_effective_on must be between "
            f"{not_before.isoformat()} and {not_after.isoformat()} inclusive"
        )
    if len(corrections) >= MAX_MILESTONE_CORRECTIONS_PER_EVENT:
        raise ResourceConflict(
            "this milestone has reached the correction-history limit"
        )

    previous = corrections[-1] if corrections else None
    correction = ApplicationMilestoneCorrection(
        id=uuid4().hex,
        owner_id=owner_id,
        application_id=application.id,
        activity_event_id=target.id,
        correction_number=len(corrections) + 1,
        supersedes_correction_id=previous.id if previous is not None else None,
        previous_effective_on=previous_effective_on,
        corrected_effective_on=corrected_on,
        recording_method="manual",
        recorded_at=current,
        created_at=current,
    )
    session.add(correction)
    application.version += 1
    application.updated_at = current
    session.flush()
    complete_owner_mutation(
        session,
        owner_id=owner_id,
        receipt_id=claim.receipt_id,
        resource_type="application_milestone_correction",
        resource_id=correction.id,
        result_version=application.version,
        now=current,
    )
    return _correction_mutation_response(
        session,
        application=application,
        target=target,
        correction=correction,
        correction_created=True,
    )


def _replayed_correction(
    session: Session,
    *,
    application: Application,
    activity_event_id: str,
    correction_id: str,
) -> ApplicationMilestoneCorrectionMutationResponse:
    target = session.scalar(
        select(ApplicationActivityEvent).where(
            ApplicationActivityEvent.owner_id == application.owner_id,
            ApplicationActivityEvent.application_id == application.id,
            ApplicationActivityEvent.id == activity_event_id,
        )
    )
    correction = session.scalar(
        select(ApplicationMilestoneCorrection).where(
            ApplicationMilestoneCorrection.owner_id == application.owner_id,
            ApplicationMilestoneCorrection.application_id == application.id,
            ApplicationMilestoneCorrection.activity_event_id == activity_event_id,
            ApplicationMilestoneCorrection.id == correction_id,
        )
    )
    if target is None or correction is None:
        raise ApplicationCorrectionRepositoryError(
            "milestone-correction replay resource is unavailable"
        )
    return _correction_mutation_response(
        session,
        application=application,
        target=target,
        correction=correction,
        correction_created=False,
    )


def _correction_mutation_response(
    session: Session,
    *,
    application: Application,
    target: ApplicationActivityEvent,
    correction: ApplicationMilestoneCorrection,
    correction_created: bool,
) -> ApplicationMilestoneCorrectionMutationResponse:
    corrections = _correction_rows(
        session,
        owner_id=application.owner_id,
        application_id=application.id,
        activity_event_id=target.id,
        lock=False,
    )
    return ApplicationMilestoneCorrectionMutationResponse(
        application=_application_summary(session, application),
        activity_event=_activity_response(target, corrections),
        correction=_correction_response(correction),
        correction_created=correction_created,
    )


def _correction_rows(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    activity_event_id: str,
    lock: bool,
) -> list[ApplicationMilestoneCorrection]:
    statement = (
        select(ApplicationMilestoneCorrection)
        .where(
            ApplicationMilestoneCorrection.owner_id == owner_id,
            ApplicationMilestoneCorrection.application_id == application_id,
            ApplicationMilestoneCorrection.activity_event_id == activity_event_id,
        )
        .order_by(
            ApplicationMilestoneCorrection.correction_number,
            ApplicationMilestoneCorrection.recorded_at,
            ApplicationMilestoneCorrection.id,
        )
    )
    if lock:
        statement = statement.with_for_update()
    return list(session.scalars(statement))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "ApplicationCorrectionRepositoryError",
    "record_application_milestone_correction",
]
