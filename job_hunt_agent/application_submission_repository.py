"""Atomic, owner-scoped manual application transitions and submission records."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .application_artifact_repository import load_application_artifacts
from .application_artifact_schemas import (
    ApplicationArtifactBlocker,
    ApplicationArtifactStatus,
)
from .application_pack_repository import load_application_pack
from .application_pack_schemas import (
    ApplicationPackBlocker,
    ApplicationPackStatus,
)
from .application_repository import _activity_response, _application_summary
from .application_schemas import ApplicationOutcomeResponse
from .application_submission_schemas import (
    AppliedTransitionCreate,
    ApplicationSubmissionProjection,
    ApplicationSubmissionResponse,
    ApplicationTransitionCreate,
    ApplicationTransitionResponse,
    ClosedTransitionCreate,
    InterviewingTransitionCreate,
    OfferTransitionCreate,
    ReadyToApplyTransitionCreate,
    ScreeningTransitionCreate,
)
from .job_queue import utcnow
from .models.application import ActionItem, Application, ApplicationActivityEvent
from .models.application_artifact import (
    ApplicationArtifactEvent,
    ApplicationArtifactRevision,
)
from .models.application_pack import (
    ApplicationPack,
    ApplicationPackEvent,
    ApplicationPackRevision,
)
from .models.application_submission import ApplicationSubmission
from .models.application_outcome import ApplicationOutcome
from .models.foundation import Owner
from .models.opportunity import JobObservation, JobPosting, JobPostingVersion
from .models.profile import ResumeVersion
from .mutation_receipts import claim_owner_mutation, complete_owner_mutation
from .opportunity_repository import canonicalize_posting_url
from .repository_errors import ResourceConflict, require_version
from .security import DataKeyring


_READY_ACTION_TITLE = "Submit application"
_APPLIED_ACTION_TITLE = "Follow up on application"
_SCREENING_ACTION_TITLE = "Follow up after recruiter screen"
_INTERVIEWING_ACTION_TITLE = "Follow up after interview"
_OFFER_ACTION_TITLE = "Review and respond to offer"
_MAX_DATE_WINDOW_DAYS = 365
_CURRENT_GROUNDING_BLOCKERS = frozenset(
    {
        ApplicationPackBlocker.no_requirements_extracted,
        ApplicationPackBlocker.requirements_need_review,
        ApplicationPackBlocker.mapped_evidence_changed,
    }
)


class ApplicationSubmissionRepositoryError(RuntimeError):
    """A persisted transition or submission invariant failed."""


def load_application_submission(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
) -> ApplicationSubmissionProjection | None:
    """Load exact submission state and persisted destinations without live work."""

    application = _owned_application(session, owner_id, application_id)
    if application is None:
        return None
    _posting, version, destinations, first_party_verified = _posting_state(
        session,
        application=application,
        lock=False,
    )
    if version.id != application.pursued_posting_version_id:
        raise ApplicationSubmissionRepositoryError(
            "submission posting version does not match the application"
        )
    submission = _owned_submission(session, owner_id, application.id)
    return ApplicationSubmissionProjection(
        application_id=application.id,
        stage=application.stage,
        available_destinations=destinations,
        first_party_verified=first_party_verified,
        submission=(
            _submission_response(submission) if submission is not None else None
        ),
    )


def transition_application(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    payload: ApplicationTransitionCreate,
    expected_application_version: int,
    idempotency_key: str,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> ApplicationTransitionResponse | None:
    """Move one application forward and replace its next action atomically."""

    current = _as_utc(now or utcnow())
    application = _owned_application(
        session,
        owner_id,
        application_id,
        lock=True,
    )
    if application is None:
        return None
    claim = claim_owner_mutation(
        session,
        owner_id=owner_id,
        namespace=f"application.transition:{application.id}",
        idempotency_key=idempotency_key,
        request={
            "payload": payload.model_dump(mode="json"),
            "expected_application_version": expected_application_version,
        },
        now=current,
    )
    if claim.replay is not None:
        if claim.replay.resource_type != "application_transition":
            raise ApplicationSubmissionRepositoryError(
                "application-transition receipt has the wrong resource type"
            )
        if (
            claim.replay.result_version is None
            or claim.replay.result_version > application.version
        ):
            raise ApplicationSubmissionRepositoryError(
                "application-transition receipt has an invalid result version"
            )
        return _replayed_transition_response(
            session,
            application=application,
            event_id=claim.replay.resource_id,
            result_version=claim.replay.result_version,
            transition_created=False,
        )

    require_version(
        "application",
        application.id,
        expected=expected_application_version,
        actual=application.version,
    )
    owner = session.scalar(select(Owner).where(Owner.id == owner_id))
    if owner is None:
        raise ApplicationSubmissionRepositoryError("application owner is unavailable")
    local_today = _owner_local_date(current, owner.timezone)
    posting: JobPosting | None = None
    destinations: list[str] = []
    if isinstance(payload, (ReadyToApplyTransitionCreate, AppliedTransitionCreate)):
        posting, _version, destinations, first_party_verified = _posting_state(
            session,
            application=application,
            lock=True,
        )
        if not first_party_verified or not destinations:
            raise ResourceConflict(
                "the pinned posting has no verified first-party application destination"
            )
        _require_exact_reviewed_materials(
            session,
            application=application,
            payload=payload,
            keyring=keyring,
        )
    if application.stage == "closed":
        raise ResourceConflict("this application is already closed")
    current_action = _current_action(session, application, lock=True)

    if isinstance(payload, ReadyToApplyTransitionCreate):
        if posting is None:  # pragma: no cover - guarded by payload type.
            raise ApplicationSubmissionRepositoryError("posting state was not loaded")
        if application.stage != "pursuing":
            raise ResourceConflict(
                "only a pursuing application can become ready to apply"
            )
        if posting.lifecycle_state != "open":
            raise ResourceConflict("closed postings cannot become ready to apply")
        _require_due_date(payload.next_action_due_on, local_today=local_today)
        if current_action.kind != "review_and_prepare_application":
            raise ApplicationSubmissionRepositoryError(
                "pursuing application has the wrong current action"
            )
        event = _replace_action_and_stage(
            session,
            application=application,
            previous_action=current_action,
            to_stage="ready_to_apply",
            action_kind="submit_application",
            action_title=_READY_ACTION_TITLE,
            action_due_on=payload.next_action_due_on,
            event_type="application_ready_to_apply",
            sequence_number=2,
            submission=None,
            now=current,
        )
    elif isinstance(payload, ScreeningTransitionCreate):
        event = _record_progress_stage(
            session,
            application=application,
            current_action=current_action,
            allowed_from={"applied"},
            to_stage="screening",
            event_type="application_screening",
            effective_on=payload.reached_on,
            action_kind="prepare_recruiter_screen",
            action_title=_SCREENING_ACTION_TITLE,
            action_due_on=payload.next_action_due_on,
            local_today=local_today,
            now=current,
        )
    elif isinstance(payload, InterviewingTransitionCreate):
        event = _record_progress_stage(
            session,
            application=application,
            current_action=current_action,
            allowed_from={"applied", "screening"},
            to_stage="interviewing",
            event_type="application_interviewing",
            effective_on=payload.reached_on,
            action_kind="prepare_interview",
            action_title=_INTERVIEWING_ACTION_TITLE,
            action_due_on=payload.next_action_due_on,
            local_today=local_today,
            now=current,
        )
    elif isinstance(payload, OfferTransitionCreate):
        event = _record_progress_stage(
            session,
            application=application,
            current_action=current_action,
            allowed_from={"applied", "screening", "interviewing"},
            to_stage="offer",
            event_type="application_offer",
            effective_on=payload.received_on,
            action_kind="review_offer",
            action_title=_OFFER_ACTION_TITLE,
            action_due_on=payload.next_action_due_on,
            local_today=local_today,
            now=current,
        )
    elif isinstance(payload, ClosedTransitionCreate):
        event = _record_terminal_outcome(
            session,
            application=application,
            current_action=current_action,
            payload=payload,
            local_today=local_today,
            application_created_on=_owner_local_date(
                _as_utc(application.created_at), owner.timezone
            ),
            now=current,
        )
    elif isinstance(payload, AppliedTransitionCreate):
        if posting is None:  # pragma: no cover - guarded by payload type.
            raise ApplicationSubmissionRepositoryError("posting state was not loaded")
        if application.stage != "ready_to_apply":
            raise ResourceConflict(
                "only a ready-to-apply application can be marked applied"
            )
        _require_applied_date(
            payload.applied_on,
            local_today=local_today,
            application_created_on=_owner_local_date(
                _as_utc(application.created_at), owner.timezone
            ),
            posting=posting,
            owner_timezone=owner.timezone,
        )
        _require_due_date(
            payload.next_action_due_on,
            local_today=local_today,
            not_before=max(local_today, payload.applied_on),
        )
        if current_action.kind != "submit_application":
            raise ApplicationSubmissionRepositoryError(
                "ready-to-apply application has the wrong current action"
            )
        destination = canonicalize_posting_url(payload.destination_url)
        if destination not in destinations:
            raise ResourceConflict(
                "destination_url must name a persisted first-party apply URL "
                "from the pursued posting version"
            )
        if _owned_submission(session, owner_id, application.id, lock=True) is not None:
            raise ResourceConflict("this application already has a submission record")
        submission = ApplicationSubmission(
            id=uuid4().hex,
            owner_id=owner_id,
            application_id=application.id,
            application_pack_id=payload.application_pack_id,
            application_pack_revision_id=payload.application_pack_revision_id,
            application_pack_review_event_id=payload.application_pack_review_event_id,
            application_artifact_revision_id=payload.application_artifact_revision_id,
            application_artifact_approval_event_id=(
                payload.application_artifact_approval_event_id
            ),
            tailored_resume_version_id=payload.tailored_resume_version_id,
            destination_url=destination,
            applied_on=payload.applied_on,
            submission_method="manual",
            recorded_at=current,
            created_at=current,
        )
        session.add(submission)
        session.flush()
        event = _replace_action_and_stage(
            session,
            application=application,
            previous_action=current_action,
            to_stage="applied",
            action_kind="follow_up_application",
            action_title=_APPLIED_ACTION_TITLE,
            action_due_on=payload.next_action_due_on,
            event_type="application_applied",
            sequence_number=3,
            submission=submission,
            now=current,
        )
    else:  # pragma: no cover - discriminated schema makes this unreachable.
        raise ValueError("unsupported application transition")

    complete_owner_mutation(
        session,
        owner_id=owner_id,
        receipt_id=claim.receipt_id,
        resource_type="application_transition",
        resource_id=event.id,
        result_version=application.version,
        now=current,
    )
    return _transition_response(
        session,
        application=application,
        event=event,
        transition_created=True,
    )


def _require_exact_reviewed_materials(
    session: Session,
    *,
    application: Application,
    payload: ReadyToApplyTransitionCreate | AppliedTransitionCreate,
    keyring: DataKeyring,
) -> None:
    pack = session.scalar(
        select(ApplicationPack)
        .where(
            ApplicationPack.owner_id == application.owner_id,
            ApplicationPack.application_id == application.id,
            ApplicationPack.id == payload.application_pack_id,
        )
        .with_for_update()
    )
    if pack is None:
        raise ResourceConflict(
            "application_pack_id is not available for this application"
        )
    latest_grounding = session.scalar(
        select(ApplicationPackRevision)
        .where(
            ApplicationPackRevision.owner_id == application.owner_id,
            ApplicationPackRevision.application_id == application.id,
            ApplicationPackRevision.application_pack_id == pack.id,
        )
        .order_by(
            ApplicationPackRevision.revision_number.desc(),
            ApplicationPackRevision.id.desc(),
        )
        .limit(1)
        .with_for_update()
    )
    if (
        latest_grounding is None
        or latest_grounding.id != payload.application_pack_revision_id
    ):
        raise ResourceConflict(
            "application_pack_revision_id must be the current revision"
        )
    grounding_review = session.scalar(
        select(ApplicationPackEvent)
        .where(
            ApplicationPackEvent.owner_id == application.owner_id,
            ApplicationPackEvent.application_id == application.id,
            ApplicationPackEvent.application_pack_id == pack.id,
            ApplicationPackEvent.revision_id == latest_grounding.id,
            ApplicationPackEvent.id == payload.application_pack_review_event_id,
            ApplicationPackEvent.event_type == "reviewed",
        )
        .with_for_update()
    )
    if grounding_review is None:
        raise ResourceConflict("the exact current grounding revision is not reviewed")
    pack_projection = load_application_pack(
        session,
        owner_id=application.owner_id,
        application_id=application.id,
        keyring=keyring,
    )
    allowed_pack_freshness_blockers = (
        {ApplicationPackBlocker.mapped_evidence_changed}
        if application.stage == "ready_to_apply"
        else set()
    )
    if (
        pack_projection is None
        or pack_projection.status is not ApplicationPackStatus.reviewed
        or pack_projection.pack is None
        or pack_projection.pack.id != pack.id
        or pack_projection.current_revision is None
        or pack_projection.current_revision.id != latest_grounding.id
        or pack_projection.review_event is None
        or pack_projection.review_event.id != grounding_review.id
        or any(
            blocker in _CURRENT_GROUNDING_BLOCKERS
            and blocker not in allowed_pack_freshness_blockers
            for blocker in pack_projection.blockers
        )
    ):
        raise ResourceConflict(
            "the current grounding review is no longer ready for use"
        )

    artifact = session.scalar(
        select(ApplicationArtifactRevision)
        .where(
            ApplicationArtifactRevision.owner_id == application.owner_id,
            ApplicationArtifactRevision.application_id == application.id,
            ApplicationArtifactRevision.application_pack_id == pack.id,
            ApplicationArtifactRevision.id == payload.application_artifact_revision_id,
        )
        .with_for_update()
    )
    if artifact is None or artifact.grounding_revision_id != latest_grounding.id:
        raise ResourceConflict(
            "the exact artifact revision is not grounded in the reviewed pack revision"
        )
    latest_artifact_id = session.scalar(
        select(ApplicationArtifactRevision.id)
        .where(
            ApplicationArtifactRevision.owner_id == application.owner_id,
            ApplicationArtifactRevision.application_id == application.id,
            ApplicationArtifactRevision.application_pack_id == pack.id,
        )
        .order_by(
            ApplicationArtifactRevision.revision_number.desc(),
            ApplicationArtifactRevision.id.desc(),
        )
        .limit(1)
    )
    if latest_artifact_id != artifact.id:
        raise ResourceConflict("application_artifact_revision_id must be current")
    artifact_approval = session.scalar(
        select(ApplicationArtifactEvent)
        .where(
            ApplicationArtifactEvent.owner_id == application.owner_id,
            ApplicationArtifactEvent.application_id == application.id,
            ApplicationArtifactEvent.application_pack_id == pack.id,
            ApplicationArtifactEvent.artifact_revision_id == artifact.id,
            ApplicationArtifactEvent.id
            == payload.application_artifact_approval_event_id,
            ApplicationArtifactEvent.event_type == "approved",
        )
        .with_for_update()
    )
    if (
        artifact_approval is None
        or artifact_approval.tailored_resume_version_id
        != payload.tailored_resume_version_id
    ):
        raise ResourceConflict(
            "the exact artifact revision does not have the named resume approval"
        )
    resume = session.scalar(
        select(ResumeVersion)
        .where(
            ResumeVersion.owner_id == application.owner_id,
            ResumeVersion.id == payload.tailored_resume_version_id,
        )
        .with_for_update()
    )
    if resume is None:
        raise ResourceConflict("tailored_resume_version_id is unavailable")
    if resume.parent_id != pack.base_resume_version_id or (
        application.stage == "pursuing" and resume.is_base
    ):
        raise ResourceConflict(
            "the tailored resume must be a non-base child of the pack base resume"
        )
    artifact_projection = load_application_artifacts(
        session,
        owner_id=application.owner_id,
        application_id=application.id,
        keyring=keyring,
    )
    allowed_blockers = (
        {
            ApplicationArtifactBlocker.grounding_evidence_changed,
            ApplicationArtifactBlocker.posting_closed,
        }
        if application.stage == "ready_to_apply"
        else set()
    )
    if (
        artifact_projection is None
        or artifact_projection.status is not ApplicationArtifactStatus.approved
        or artifact_projection.pack is None
        or artifact_projection.pack.id != pack.id
        or artifact_projection.current_revision is None
        or artifact_projection.current_revision.id != artifact.id
        or artifact_projection.approved_revision is None
        or artifact_projection.approved_revision.id != artifact.id
        or artifact_projection.current_event is None
        or artifact_projection.current_event.id != artifact_approval.id
        or artifact_projection.approval_event is None
        or artifact_projection.approval_event.id != artifact_approval.id
        or artifact_projection.tailored_resume_version is None
        or artifact_projection.tailored_resume_version.id != resume.id
        or any(
            blocker not in allowed_blockers
            for blocker in artifact_projection.blockers
        )
    ):
        raise ResourceConflict(
            "the current application artifacts are not approved and ready"
        )


def _replace_action_and_stage(
    session: Session,
    *,
    application: Application,
    previous_action: ActionItem,
    to_stage: str,
    action_kind: str,
    action_title: str,
    action_due_on: date,
    event_type: str,
    sequence_number: int | None,
    submission: ApplicationSubmission | None,
    effective_on: date | None = None,
    now: datetime,
) -> ApplicationActivityEvent:
    previous_stage = application.stage
    previous_action.status = "completed"
    previous_action.completed_at = now
    previous_action.cancelled_at = None
    previous_action.version += 1
    previous_action.updated_at = now
    session.flush()

    next_action = ActionItem(
        id=uuid4().hex,
        owner_id=application.owner_id,
        application_id=application.id,
        kind=action_kind,
        title=action_title,
        status="open",
        due_on=action_due_on,
        version=1,
        created_at=now,
        updated_at=now,
    )
    session.add(next_action)
    session.flush()
    application.stage = to_stage
    application.version += 1
    application.updated_at = now
    session.flush()

    existing_sequence = int(
        session.scalar(
            select(func.max(ApplicationActivityEvent.sequence_number)).where(
                ApplicationActivityEvent.owner_id == application.owner_id,
                ApplicationActivityEvent.application_id == application.id,
            )
        )
        or 0
    )
    next_sequence = existing_sequence + 1
    if sequence_number is not None and next_sequence != sequence_number:
        raise ApplicationSubmissionRepositoryError(
            "application activity sequence is inconsistent with its stage"
        )
    event = ApplicationActivityEvent(
        id=uuid4().hex,
        owner_id=application.owner_id,
        application_id=application.id,
        sequence_number=next_sequence,
        event_type=event_type,
        from_stage=previous_stage,
        to_stage=to_stage,
        action_item_id=next_action.id,
        previous_action_item_id=previous_action.id,
        submission_id=submission.id if submission is not None else None,
        effective_on=effective_on,
        outcome_id=None,
        occurred_at=now,
        created_at=now,
    )
    session.add(event)
    session.flush()
    return event


def _record_progress_stage(
    session: Session,
    *,
    application: Application,
    current_action: ActionItem,
    allowed_from: set[str],
    to_stage: str,
    event_type: str,
    effective_on: date,
    action_kind: str,
    action_title: str,
    action_due_on: date,
    local_today: date,
    now: datetime,
) -> ApplicationActivityEvent:
    if application.stage not in allowed_from:
        raise ResourceConflict(
            f"an application in {application.stage} cannot move to {to_stage}"
        )
    _require_current_action_kind(application, current_action)
    submission = _owned_submission(
        session,
        application.owner_id,
        application.id,
        lock=True,
    )
    if submission is None:
        raise ApplicationSubmissionRepositoryError(
            "post-application progress requires the immutable submission"
        )
    _require_milestone_date(
        session,
        application=application,
        submission=submission,
        value=effective_on,
        local_today=local_today,
    )
    _require_due_date(action_due_on, local_today=local_today)
    return _replace_action_and_stage(
        session,
        application=application,
        previous_action=current_action,
        to_stage=to_stage,
        action_kind=action_kind,
        action_title=action_title,
        action_due_on=action_due_on,
        event_type=event_type,
        sequence_number=None,
        submission=None,
        effective_on=effective_on,
        now=now,
    )


def _record_terminal_outcome(
    session: Session,
    *,
    application: Application,
    current_action: ActionItem,
    payload: ClosedTransitionCreate,
    local_today: date,
    application_created_on: date,
    now: datetime,
) -> ApplicationActivityEvent:
    if application.stage == "closed":
        raise ResourceConflict("this application is already closed")
    _require_current_action_kind(application, current_action)
    outcome_value = payload.outcome.value
    if outcome_value in {"offer_accepted", "offer_declined"}:
        if application.stage != "offer":
            raise ResourceConflict(
                "an offer can be accepted or declined only after recording the offer"
            )
    elif outcome_value in {"rejected", "no_response"}:
        if application.stage not in {"applied", "screening", "interviewing", "offer"}:
            raise ResourceConflict(
                "rejection or no response can be recorded only after applying"
            )
    elif outcome_value not in {"withdrawn", "posting_closed"}:
        raise ValueError("unsupported terminal outcome")

    submission = _owned_submission(
        session,
        application.owner_id,
        application.id,
        lock=True,
    )
    if application.stage in {"applied", "screening", "interviewing", "offer"}:
        if submission is None:
            raise ApplicationSubmissionRepositoryError(
                "post-application closure requires the immutable submission"
            )
        lower_bound = max(
            submission.applied_on,
            _latest_effective_on(session, application) or submission.applied_on,
        )
    else:
        if submission is not None:
            raise ApplicationSubmissionRepositoryError(
                "pre-submission application unexpectedly has a submission"
            )
        lower_bound = application_created_on
    _require_recorded_date(
        payload.outcome_on,
        local_today=local_today,
        not_before=lower_bound,
        field_name="outcome_on",
    )
    if _owned_outcome(
        session,
        application.owner_id,
        application.id,
        lock=True,
    ) is not None:
        raise ResourceConflict("this application already has a terminal outcome")

    previous_stage = application.stage
    outcome = ApplicationOutcome(
        id=uuid4().hex,
        owner_id=application.owner_id,
        application_id=application.id,
        application_submission_id=submission.id if submission is not None else None,
        stage_at_outcome=previous_stage,
        outcome=outcome_value,
        outcome_on=payload.outcome_on,
        recording_method="manual",
        recorded_at=now,
        created_at=now,
    )
    session.add(outcome)
    session.flush()

    current_action.status = "cancelled"
    current_action.completed_at = None
    current_action.cancelled_at = now
    current_action.version += 1
    current_action.updated_at = now
    application.stage = "closed"
    application.outcome_id = outcome.id
    application.version += 1
    application.updated_at = now
    session.flush()

    event = ApplicationActivityEvent(
        id=uuid4().hex,
        owner_id=application.owner_id,
        application_id=application.id,
        sequence_number=_next_activity_sequence(session, application),
        event_type="application_closed",
        from_stage=previous_stage,
        to_stage="closed",
        action_item_id=None,
        previous_action_item_id=current_action.id,
        submission_id=None,
        effective_on=payload.outcome_on,
        outcome_id=outcome.id,
        occurred_at=now,
        created_at=now,
    )
    session.add(event)
    session.flush()
    return event


def _require_current_action_kind(
    application: Application,
    current_action: ActionItem,
) -> None:
    expected_kind = {
        "pursuing": "review_and_prepare_application",
        "ready_to_apply": "submit_application",
        "applied": "follow_up_application",
        "screening": "prepare_recruiter_screen",
        "interviewing": "prepare_interview",
        "offer": "review_offer",
    }.get(application.stage)
    if expected_kind is None or current_action.kind != expected_kind:
        raise ApplicationSubmissionRepositoryError(
            f"{application.stage} application has the wrong current action"
        )


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


def _latest_effective_on(
    session: Session,
    application: Application,
) -> date | None:
    return session.scalar(
        select(func.max(ApplicationActivityEvent.effective_on)).where(
            ApplicationActivityEvent.owner_id == application.owner_id,
            ApplicationActivityEvent.application_id == application.id,
            ApplicationActivityEvent.effective_on.is_not(None),
        )
    )


def _require_milestone_date(
    session: Session,
    *,
    application: Application,
    submission: ApplicationSubmission,
    value: date,
    local_today: date,
) -> None:
    lower_bound = max(
        submission.applied_on,
        _latest_effective_on(session, application) or submission.applied_on,
    )
    _require_recorded_date(
        value,
        local_today=local_today,
        not_before=lower_bound,
        field_name="milestone date",
    )


def _require_recorded_date(
    value: date,
    *,
    local_today: date,
    not_before: date,
    field_name: str,
) -> None:
    if value < not_before or value > local_today:
        raise ValueError(
            f"{field_name} must be on or after the prior milestone and not in the future"
        )


def _replayed_transition_response(
    session: Session,
    *,
    application: Application,
    event_id: str,
    result_version: int,
    transition_created: bool,
) -> ApplicationTransitionResponse:
    event = session.scalar(
        select(ApplicationActivityEvent)
        .where(
            ApplicationActivityEvent.owner_id == application.owner_id,
            ApplicationActivityEvent.application_id == application.id,
            ApplicationActivityEvent.id == event_id,
            ApplicationActivityEvent.event_type.in_(
                (
                    "application_ready_to_apply",
                    "application_applied",
                    "application_screening",
                    "application_interviewing",
                    "application_offer",
                    "application_closed",
                )
            ),
        )
    )
    if event is None:
        raise ApplicationSubmissionRepositoryError(
            "application-transition replay activity is unavailable"
        )
    if application.version != result_version or application.stage != event.to_stage:
        raise ResourceConflict(
            "the application progressed after this transition; reload its current state"
        )
    return _transition_response(
        session,
        application=application,
        event=event,
        transition_created=transition_created,
    )


def _transition_response(
    session: Session,
    *,
    application: Application,
    event: ApplicationActivityEvent,
    transition_created: bool,
) -> ApplicationTransitionResponse:
    submission = _owned_submission(session, application.owner_id, application.id)
    outcome = _owned_outcome(session, application.owner_id, application.id)
    return ApplicationTransitionResponse(
        application=_application_summary(session, application),
        activity_event=_activity_response(event),
        submission=(
            _submission_response(submission) if submission is not None else None
        ),
        outcome=(
            _outcome_response(outcome) if outcome is not None else None
        ),
        transition_created=transition_created,
    )


def _posting_state(
    session: Session,
    *,
    application: Application,
    lock: bool,
) -> tuple[JobPosting, JobPostingVersion, list[str], bool]:
    posting_statement = select(JobPosting).where(
        JobPosting.owner_id == application.owner_id,
        JobPosting.id == application.job_posting_id,
    )
    if lock:
        posting_statement = posting_statement.with_for_update()
    posting = session.scalar(posting_statement)
    version = session.scalar(
        select(JobPostingVersion).where(
            JobPostingVersion.owner_id == application.owner_id,
            JobPostingVersion.job_posting_id == application.job_posting_id,
            JobPostingVersion.id == application.pursued_posting_version_id,
        )
    )
    if posting is None or version is None:
        raise ApplicationSubmissionRepositoryError(
            "application posting graph is unavailable"
        )
    destinations: list[str] = []
    for raw in version.apply_urls:
        try:
            normalized = canonicalize_posting_url(raw)
        except ValueError as exc:
            raise ApplicationSubmissionRepositoryError(
                "pinned posting contains an unsafe apply URL"
            ) from exc
        if normalized not in destinations:
            destinations.append(normalized)
    first_party_verified = (
        session.scalar(
            select(JobObservation.id)
            .where(
                JobObservation.owner_id == application.owner_id,
                JobObservation.job_posting_id == application.job_posting_id,
                JobObservation.job_posting_version_id
                == application.pursued_posting_version_id,
                JobObservation.first_party_url_verified.is_(True),
            )
            .limit(1)
        )
        is not None
    )
    return posting, version, destinations, first_party_verified


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


def _current_action(
    session: Session,
    application: Application,
    *,
    lock: bool,
) -> ActionItem:
    statement = select(ActionItem).where(
        ActionItem.owner_id == application.owner_id,
        ActionItem.application_id == application.id,
        ActionItem.status == "open",
    )
    if lock:
        statement = statement.with_for_update()
    actions = list(session.scalars(statement))
    if len(actions) != 1:
        raise ApplicationSubmissionRepositoryError(
            "application must have exactly one open current action"
        )
    return actions[0]


def _owned_submission(
    session: Session,
    owner_id: str,
    application_id: str,
    *,
    lock: bool = False,
) -> ApplicationSubmission | None:
    statement = select(ApplicationSubmission).where(
        ApplicationSubmission.owner_id == owner_id,
        ApplicationSubmission.application_id == application_id,
    )
    if lock:
        statement = statement.with_for_update()
    return session.scalar(statement)


def _owned_outcome(
    session: Session,
    owner_id: str,
    application_id: str,
    *,
    lock: bool = False,
) -> ApplicationOutcome | None:
    statement = select(ApplicationOutcome).where(
        ApplicationOutcome.owner_id == owner_id,
        ApplicationOutcome.application_id == application_id,
    )
    if lock:
        statement = statement.with_for_update()
    return session.scalar(statement)


def _submission_response(row: ApplicationSubmission) -> ApplicationSubmissionResponse:
    return ApplicationSubmissionResponse(
        id=row.id,
        application_id=row.application_id,
        application_pack_id=row.application_pack_id,
        application_pack_revision_id=row.application_pack_revision_id,
        application_pack_review_event_id=row.application_pack_review_event_id,
        application_artifact_revision_id=row.application_artifact_revision_id,
        application_artifact_approval_event_id=(
            row.application_artifact_approval_event_id
        ),
        tailored_resume_version_id=row.tailored_resume_version_id,
        destination_url=row.destination_url,
        applied_on=row.applied_on,
        submission_method="manual",
        recorded_at=_as_utc(row.recorded_at),
        created_at=_as_utc(row.created_at),
    )


def _outcome_response(row: ApplicationOutcome) -> ApplicationOutcomeResponse:
    return ApplicationOutcomeResponse(
        id=row.id,
        application_id=row.application_id,
        application_submission_id=row.application_submission_id,
        stage_at_outcome=row.stage_at_outcome,
        outcome=row.outcome,
        outcome_on=row.outcome_on,
        recording_method="manual",
        recorded_at=_as_utc(row.recorded_at),
        created_at=_as_utc(row.created_at),
    )


def _require_due_date(
    value: date,
    *,
    local_today: date,
    not_before: date | None = None,
) -> None:
    lower = not_before or local_today
    if value < lower or value > local_today + timedelta(days=_MAX_DATE_WINDOW_DAYS):
        raise ValueError(
            "next_action_due_on must be within the next 365 owner-local days"
        )


def _require_applied_date(
    value: date,
    *,
    local_today: date,
    application_created_on: date,
    posting: JobPosting,
    owner_timezone: str,
) -> None:
    if value > local_today or value < application_created_on:
        raise ValueError(
            "applied_on must be on or after application creation and not in the future"
        )
    if posting.lifecycle_state == "closed":
        if posting.closed_at is None:
            raise ApplicationSubmissionRepositoryError(
                "closed posting has no closure timestamp"
            )
        closed_on = _owner_local_date(_as_utc(posting.closed_at), owner_timezone)
        if value > closed_on:
            raise ResourceConflict("applied_on cannot follow the posting closure date")


def _owner_local_date(value: datetime, timezone_name: str) -> date:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ApplicationSubmissionRepositoryError(
            "application owner timezone is invalid"
        ) from exc
    return value.astimezone(zone).date()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "ApplicationSubmissionRepositoryError",
    "load_application_submission",
    "transition_application",
]
