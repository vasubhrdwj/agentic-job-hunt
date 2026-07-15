"""Owner-scoped persistence for the first practical application workflow.

This repository is deliberately provider-free. Pursuit reads only already
persisted opportunity facts and atomically creates the application, its dated
next action, the immutable creation activity, and the pursued decision event.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from .application_schemas import (
    ActionItemResponse,
    ApplicationActivityEventResponse,
    ApplicationActivityListResponse,
    ApplicationDetailResponse,
    ApplicationListResponse,
    ApplicationMilestoneCorrectionResponse,
    ApplicationOutcomeResponse,
    ApplicationPostingState,
    ApplicationPostingSummary,
    ApplicationSummary,
    CursorToken,
    PursuitBundle,
    TodayApplicationActionApplication,
    TodayApplicationActionGroup,
    TodayApplicationActionItem,
    TodayApplicationActionsResponse,
)
from .job_queue import utcnow
from .models import (
    ActionItem,
    Application,
    ApplicationActivityEvent,
    ApplicationInterviewRound,
    ApplicationMetricSnapshot,
    ApplicationMilestoneCorrection,
    ApplicationOutcome,
    CareerTrack,
    JobObservation,
    JobPosting,
    JobPostingVersion,
    OpportunityDecisionEvent as OpportunityDecisionEventRow,
    Owner,
    OwnerOpportunity,
    SavedSearch,
    SavedSearchMatch,
)
from .mutation_receipts import claim_owner_mutation, complete_owner_mutation
from .opportunity_repository import DecisionIdempotencyConflict, OpportunityNotFound
from .opportunity_schemas import (
    ApplicationAcquisitionSource,
    OpportunityDecisionAction,
    OpportunityDecisionEvent,
    OpportunityDecisionResponse,
    OpportunityDecisionState,
    PursueOpportunityRequest,
)
from .repository_errors import ResourceConflict, require_version


_INITIAL_ACTION_TITLE = "Review role and prepare application"
_MAX_APPLICATION_PAGE_SIZE = 50
_MAX_ACTIVITY_ITEMS = 500
_ACTIVE_ACTION_KIND_BY_STAGE = {
    "pursuing": "review_and_prepare_application",
    "ready_to_apply": "submit_application",
    "applied": "follow_up_application",
    "screening": "prepare_recruiter_screen",
    "interviewing": "prepare_interview",
    "offer": "review_offer",
}


class ApplicationRepositoryError(RuntimeError):
    """A safe application graph invariant failed."""


def pursue_owner_opportunity(
    session: Session,
    owner_id: str,
    opportunity_id: str,
    request: PursueOpportunityRequest,
    expected_version: int,
    idempotency_key: str,
    now: datetime | None = None,
) -> OpportunityDecisionResponse:
    """Atomically create or replay one application pursuit graph."""

    current = _as_utc(now or utcnow())
    normalized_key = idempotency_key.strip()
    if not normalized_key or len(normalized_key) > 200:
        raise ValueError("idempotency key must be 1-200 characters")
    key_hash = _sha256(normalized_key)
    request_payload = request.model_dump(mode="json")
    # Preserve replay for pursuit keys accepted before attribution fields
    # existed. Defaults add no new semantic choice and therefore keep the
    # byte-for-byte Phase 2B request hash.
    if (
        request.acquisition_source
        is ApplicationAcquisitionSource.job_hunt_search
        and request.selected_saved_search_id is None
    ):
        request_payload.pop("acquisition_source", None)
        request_payload.pop("selected_saved_search_id", None)
    request_hash = _sha256(_canonical_json(request_payload))

    posting_id = session.scalar(
        select(OwnerOpportunity.job_posting_id).where(
            OwnerOpportunity.owner_id == owner_id,
            OwnerOpportunity.id == opportunity_id,
        )
    )
    if posting_id is None:
        raise OpportunityNotFound("opportunity not found")
    # Scan persistence locks posting -> opportunity. Follow that same order so
    # a Pursue during an active scan cannot form a PostgreSQL deadlock cycle.
    posting = session.scalar(
        select(JobPosting)
        .where(
            JobPosting.owner_id == owner_id,
            JobPosting.id == posting_id,
        )
        .with_for_update()
    )
    if posting is None:
        raise ApplicationRepositoryError("opportunity posting is unavailable")
    opportunity = session.scalar(
        select(OwnerOpportunity)
        .where(
            OwnerOpportunity.owner_id == owner_id,
            OwnerOpportunity.id == opportunity_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if opportunity is None:
        raise OpportunityNotFound("opportunity not found")
    if opportunity.job_posting_id != posting.id:
        raise ApplicationRepositoryError("opportunity posting identity changed")

    receipt = claim_owner_mutation(
        session,
        owner_id=owner_id,
        namespace=f"opportunity.pursue:{opportunity.id}",
        idempotency_key=normalized_key,
        request=request_payload,
        now=current,
    )
    if receipt.replay is not None:
        if receipt.replay.resource_type != "application":
            raise ApplicationRepositoryError(
                "pursuit receipt has an inconsistent resource type"
            )
        application = session.scalar(
            select(Application)
            .where(
                Application.owner_id == owner_id,
                Application.id == receipt.replay.resource_id,
                Application.owner_opportunity_id == opportunity.id,
            )
            .with_for_update()
        )
        if application is None:
            raise ApplicationRepositoryError("pursuit receipt has no application")
        event = _creation_decision_event(
            session,
            owner_id=owner_id,
            opportunity_id=opportunity.id,
        )
        if event is None:
            raise ApplicationRepositoryError("application has no pursued decision event")
        return _pursuit_response(
            session,
            opportunity=opportunity,
            application=application,
            event=event,
            application_created=hmac.compare_digest(
                event.idempotency_key_hash,
                key_hash,
            ),
        )

    keyed_event = session.scalar(
        select(OpportunityDecisionEventRow).where(
            OpportunityDecisionEventRow.owner_id == owner_id,
            OpportunityDecisionEventRow.owner_opportunity_id == opportunity.id,
            OpportunityDecisionEventRow.idempotency_key_hash == key_hash,
        )
    )
    if keyed_event is not None:
        if not hmac.compare_digest(keyed_event.request_hash, request_hash):
            raise DecisionIdempotencyConflict(
                "idempotency key was already used for another opportunity decision"
            )
        if keyed_event.new_decision != "pursued":
            raise DecisionIdempotencyConflict(
                "idempotency key was already used for another opportunity decision"
            )
        application = _application_for_opportunity(
            session,
            owner_id=owner_id,
            opportunity_id=opportunity.id,
            for_update=True,
        )
        if application is None:
            raise ApplicationRepositoryError("pursuit event has no application")
        complete_owner_mutation(
            session,
            owner_id=owner_id,
            receipt_id=receipt.receipt_id,
            resource_type="application",
            resource_id=application.id,
            result_version=application.version,
            now=current,
        )
        return _pursuit_response(
            session,
            opportunity=opportunity,
            application=application,
            event=keyed_event,
            application_created=True,
        )

    application = _application_for_opportunity(
        session,
        owner_id=owner_id,
        opportunity_id=opportunity.id,
        for_update=True,
    )
    if application is not None:
        event = _creation_decision_event(
            session,
            owner_id=owner_id,
            opportunity_id=opportunity.id,
        )
        if event is None:
            raise ApplicationRepositoryError("application has no pursued decision event")
        complete_owner_mutation(
            session,
            owner_id=owner_id,
            receipt_id=receipt.receipt_id,
            resource_type="application",
            resource_id=application.id,
            result_version=application.version,
            now=current,
        )
        return _pursuit_response(
            session,
            opportunity=opportunity,
            application=application,
            event=event,
            application_created=False,
        )

    require_version(
        "opportunity",
        opportunity.id,
        expected=expected_version,
        actual=opportunity.version,
    )
    if opportunity.decision not in {"inbox", "watch", "dismiss"}:
        raise ResourceConflict(
            "only inbox, watched, or dismissed opportunities can be pursued"
        )

    owner = session.scalar(select(Owner).where(Owner.id == owner_id))
    if owner is None:
        raise OpportunityNotFound("opportunity not found")
    if posting.lifecycle_state != "open":
        raise ResourceConflict("closed postings cannot be pursued")
    posting_version = _latest_posting_version(
        session,
        owner_id=owner_id,
        posting_id=posting.id,
    )
    if posting_version is None:
        raise ApplicationRepositoryError("opportunity posting has no version")

    attribution = _pursuit_metric_attribution(
        session,
        owner_id=owner_id,
        posting_id=posting.id,
        request=request,
    )

    due_on = _initial_action_due_on(
        request.initial_action_due_on,
        owner_timezone=owner.timezone,
        now=current,
    )
    application_id = uuid4().hex
    action_id = uuid4().hex
    activity_id = uuid4().hex
    event_id = uuid4().hex
    previous_decision = opportunity.decision

    application = Application(
        id=application_id,
        owner_id=owner_id,
        owner_opportunity_id=opportunity.id,
        job_posting_id=posting.id,
        pursued_posting_version_id=posting_version.id,
        stage="pursuing",
        version=1,
        created_at=current,
        updated_at=current,
    )
    metric_snapshot = ApplicationMetricSnapshot(
        id=uuid4().hex,
        owner_id=owner_id,
        application_id=application_id,
        job_posting_id=posting.id,
        pursued_posting_version_id=posting_version.id,
        acquisition_source=request.acquisition_source.value,
        attribution_status=attribution["attribution_status"],
        saved_search_id=attribution["saved_search_id"],
        saved_search_version=attribution["saved_search_version"],
        saved_search_name=attribution["saved_search_name"],
        career_track_id=attribution["career_track_id"],
        career_track_version=attribution["career_track_version"],
        career_track_name=attribution["career_track_name"],
        assessment_state="not_assessed",
        assessment_band=None,
        assessment_algorithm_version=None,
        assessment_reason="not_requested",
        recorded_at=current,
        created_at=current,
    )
    action = ActionItem(
        id=action_id,
        owner_id=owner_id,
        application_id=application_id,
        kind="review_and_prepare_application",
        title=_INITIAL_ACTION_TITLE,
        status="open",
        due_on=due_on,
        version=1,
        created_at=current,
        updated_at=current,
    )
    activity = ApplicationActivityEvent(
        id=activity_id,
        owner_id=owner_id,
        application_id=application_id,
        sequence_number=1,
        event_type="application_created",
        from_stage=None,
        to_stage="pursuing",
        action_item_id=action_id,
        occurred_at=current,
        created_at=current,
    )
    event = OpportunityDecisionEventRow(
        id=event_id,
        owner_id=owner_id,
        owner_opportunity_id=opportunity.id,
        job_posting_id=posting.id,
        posting_version_id=posting_version.id,
        previous_decision=previous_decision,
        new_decision="pursued",
        reason_code=None,
        encrypted_note=None,
        note_key_id=None,
        compensates_event_id=None,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
        occurred_at=current,
        created_at=current,
    )
    # These models intentionally expose no ORM relationships. Flush each
    # foreign-key layer explicitly while retaining one database transaction.
    session.add(application)
    opportunity.decision = "pursued"
    opportunity.decision_reason_code = None
    opportunity.reviewed_posting_version_id = posting_version.id
    opportunity.decision_updated_at = current
    opportunity.version += 1
    opportunity.updated_at = current
    session.flush()
    session.add(metric_snapshot)
    session.flush()
    session.add(action)
    session.flush()
    session.add_all([activity, event])
    session.flush()
    complete_owner_mutation(
        session,
        owner_id=owner_id,
        receipt_id=receipt.receipt_id,
        resource_type="application",
        resource_id=application.id,
        result_version=application.version,
        now=current,
    )
    return _pursuit_response(
        session,
        opportunity=opportunity,
        application=application,
        event=event,
        application_created=True,
    )


def list_applications(
    session: Session,
    owner_id: str,
    limit: int = _MAX_APPLICATION_PAGE_SIZE,
    cursor: CursorToken | None = None,
) -> ApplicationListResponse:
    """List the owner's persisted applications without invoking live services."""

    if limit < 1 or limit > _MAX_APPLICATION_PAGE_SIZE:
        raise ValueError("application list limit must be 1-50")
    count_table = Application.__table__.alias("application_count")
    total_subquery = (
        select(func.count(count_table.c.id))
        .where(count_table.c.owner_id == owner_id)
        .scalar_subquery()
    )
    statement = select(
        Application,
        total_subquery.label("application_total"),
    ).where(Application.owner_id == owner_id)
    if cursor is not None:
        cursor_time, cursor_id = _decode_application_cursor(cursor)
        statement = statement.where(
            (Application.updated_at < cursor_time)
            | (
                (Application.updated_at == cursor_time)
                & (Application.id < cursor_id)
            )
        )
    page = list(
        session.execute(
            statement.order_by(
                Application.updated_at.desc(),
                Application.id.desc(),
            ).limit(limit + 1)
        )
    )
    has_more = len(page) > limit
    rows = [row[0] for row in page[:limit]]
    total = (
        int(page[0][1])
        if page
        else int(
            session.scalar(
                select(func.count(Application.id)).where(
                    Application.owner_id == owner_id
                )
            )
            or 0
        )
    )
    return ApplicationListResponse(
        data_source="database",
        items=_application_summaries(session, rows),
        total=total,
        next_cursor=(
            _encode_application_cursor(rows[-1].updated_at, rows[-1].id)
            if has_more and rows
            else None
        ),
    )


def list_today_application_actions(
    session: Session,
    owner_id: str,
    limit: int = 20,
    now: datetime | None = None,
) -> TodayApplicationActionsResponse:
    """Project bounded, owner-local open application actions for Today."""

    if limit < 1 or limit > _MAX_APPLICATION_PAGE_SIZE:
        raise ValueError("Today application action limit must be 1-50")
    current = _as_utc(now or utcnow())
    owner_timezone = session.scalar(
        select(Owner.timezone).where(Owner.id == owner_id)
    )
    if owner_timezone is None:
        raise ApplicationRepositoryError("application action owner is missing")
    try:
        owner_zone = ZoneInfo(owner_timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ApplicationRepositoryError(
            "application action owner timezone is invalid"
        ) from exc

    owner_local_date = current.astimezone(owner_zone).date()
    window_ends_on = owner_local_date + timedelta(days=7)
    _validate_today_action_graph(session, owner_id=owner_id)
    return TodayApplicationActionsResponse(
        data_source="database",
        as_of=current,
        owner_timezone=owner_timezone,
        owner_local_date=owner_local_date,
        window_ends_on=window_ends_on,
        overdue=_today_application_action_group(
            session,
            owner_id=owner_id,
            limit=limit,
            due_before=owner_local_date,
        ),
        today=_today_application_action_group(
            session,
            owner_id=owner_id,
            limit=limit,
            due_on=owner_local_date,
        ),
        next_7_days=_today_application_action_group(
            session,
            owner_id=owner_id,
            limit=limit,
            due_after=owner_local_date,
            due_through=window_ends_on,
        ),
    )


def load_application_detail(
    session: Session,
    owner_id: str,
    application_id: str,
) -> ApplicationDetailResponse | None:
    """Load one owner-scoped application dossier from persisted records only."""

    application = _owned_application(session, owner_id, application_id)
    if application is None:
        return None
    activity = _activity_rows(session, owner_id, application.id)
    if not activity:
        raise ApplicationRepositoryError("application has no creation activity")
    return ApplicationDetailResponse(
        data_source="database",
        application=_application_summary(session, application),
        activity=_activity_responses(
            session,
            owner_id=owner_id,
            application_id=application.id,
            rows=activity,
        ),
    )


def list_application_activity(
    session: Session,
    owner_id: str,
    application_id: str,
) -> ApplicationActivityListResponse | None:
    """Load one owner-scoped, immutable application activity stream."""

    application = _owned_application(session, owner_id, application_id)
    if application is None:
        return None
    return ApplicationActivityListResponse(
        data_source="database",
        items=_activity_responses(
            session,
            owner_id=owner_id,
            application_id=application.id,
            rows=_activity_rows(session, owner_id, application.id),
        ),
    )


def _pursuit_response(
    session: Session,
    *,
    opportunity: OwnerOpportunity,
    application: Application,
    event: OpportunityDecisionEventRow,
    application_created: bool,
) -> OpportunityDecisionResponse:
    if opportunity.decision != "pursued" or event.new_decision != "pursued":
        raise ApplicationRepositoryError("application and opportunity pursuit disagree")
    if (
        event.owner_opportunity_id != opportunity.id
        or event.posting_version_id != application.pursued_posting_version_id
    ):
        raise ApplicationRepositoryError("pursuit decision does not match application")
    activity = session.scalar(
        select(ApplicationActivityEvent).where(
            ApplicationActivityEvent.owner_id == application.owner_id,
            ApplicationActivityEvent.application_id == application.id,
            ApplicationActivityEvent.event_type == "application_created",
        )
    )
    if activity is None:
        raise ApplicationRepositoryError("application has no creation activity")
    return OpportunityDecisionResponse(
        opportunity_id=opportunity.id,
        opportunity_version=opportunity.version,
        state=OpportunityDecisionState.pursued,
        event=_decision_event_response(event),
        pursuit=PursuitBundle(
            application=_application_summary(session, application),
            activity=_activity_response(activity),
            application_created=application_created,
        ),
    )


def _application_summary(
    session: Session,
    application: Application,
) -> ApplicationSummary:
    action = session.scalar(
        select(ActionItem).where(
            ActionItem.owner_id == application.owner_id,
            ActionItem.application_id == application.id,
            ActionItem.status == "open",
        )
    )
    outcome = (
        session.scalar(
            select(ApplicationOutcome).where(
                ApplicationOutcome.owner_id == application.owner_id,
                ApplicationOutcome.application_id == application.id,
                ApplicationOutcome.id == application.outcome_id,
            )
        )
        if application.outcome_id is not None
        else None
    )
    posting = session.scalar(
        select(JobPosting).where(
            JobPosting.owner_id == application.owner_id,
            JobPosting.id == application.job_posting_id,
        )
    )
    version = session.scalar(
        select(JobPostingVersion).where(
            JobPostingVersion.owner_id == application.owner_id,
            JobPostingVersion.job_posting_id == application.job_posting_id,
            JobPostingVersion.id == application.pursued_posting_version_id,
        )
    )
    if posting is None or version is None:
        raise ApplicationRepositoryError("application graph is incomplete")
    if (application.stage == "closed") != (action is None):
        raise ApplicationRepositoryError(
            "closed applications must have no open action and active applications one"
        )
    if (application.outcome_id is None) != (outcome is None):
        raise ApplicationRepositoryError("application outcome graph is incomplete")
    _require_action_round_consistency(session, application=application, action=action)
    verified = (
        session.scalar(
            select(JobObservation.id)
            .where(
                JobObservation.owner_id == application.owner_id,
                JobObservation.job_posting_id == application.job_posting_id,
                JobObservation.job_posting_version_id == version.id,
                JobObservation.first_party_url_verified.is_(True),
            )
            .limit(1)
        )
        is not None
    )
    return _application_summary_from_rows(
        application,
        action=action,
        posting=posting,
        version=version,
        first_party=verified,
        outcome=outcome,
    )


def _validate_today_action_graph(session: Session, *, owner_id: str) -> None:
    """Refuse to hide an active application whose current action is malformed."""

    open_action_exists = (
        select(ActionItem.id)
        .where(
            ActionItem.owner_id == Application.owner_id,
            ActionItem.application_id == Application.id,
            ActionItem.status == "open",
        )
        .exists()
    )
    missing_action = session.scalar(
        select(Application.id)
        .where(
            Application.owner_id == owner_id,
            Application.stage != "closed",
            ~open_action_exists,
        )
        .limit(1)
    )
    if missing_action is not None:
        raise ApplicationRepositoryError(
            "an active application has no open current action"
        )

    application_join = and_(
        Application.owner_id == ActionItem.owner_id,
        Application.id == ActionItem.application_id,
    )
    default_action_matches = or_(
        *(
            and_(
                Application.stage == stage,
                ActionItem.interview_round_id.is_(None),
                ActionItem.kind == expected_kind,
            )
            for stage, expected_kind in _ACTIVE_ACTION_KIND_BY_STAGE.items()
        )
    )
    scheduled_round_action_matches = and_(
        Application.stage.in_(("applied", "screening", "interviewing")),
        ActionItem.interview_round_id.is_not(None),
        ActionItem.kind == "prepare_interview",
    )
    wrong_action = session.scalar(
        select(ActionItem.id)
        .select_from(ActionItem)
        .join(Application, application_join)
        .where(
            ActionItem.owner_id == owner_id,
            Application.owner_id == owner_id,
            ActionItem.status == "open",
            or_(
                Application.stage == "closed",
                ~or_(default_action_matches, scheduled_round_action_matches),
            ),
        )
        .limit(1)
    )
    if wrong_action is not None:
        raise ApplicationRepositoryError(
            "an application has an invalid open current action"
        )

    scheduled_round_exists = (
        select(ApplicationInterviewRound.id)
        .where(
            ApplicationInterviewRound.owner_id == ActionItem.owner_id,
            ApplicationInterviewRound.application_id == ActionItem.application_id,
            ApplicationInterviewRound.id == ActionItem.interview_round_id,
            ApplicationInterviewRound.status == "scheduled",
        )
        .exists()
    )
    orphaned_round_action = session.scalar(
        select(ActionItem.id)
        .where(
            ActionItem.owner_id == owner_id,
            ActionItem.status == "open",
            ActionItem.interview_round_id.is_not(None),
            ~scheduled_round_exists,
        )
        .limit(1)
    )
    if orphaned_round_action is not None:
        raise ApplicationRepositoryError(
            "an interview preparation action has no scheduled round"
        )

    open_round_action_exists = (
        select(ActionItem.id)
        .where(
            ActionItem.owner_id == ApplicationInterviewRound.owner_id,
            ActionItem.application_id == ApplicationInterviewRound.application_id,
            ActionItem.interview_round_id == ApplicationInterviewRound.id,
            ActionItem.status == "open",
            ActionItem.kind == "prepare_interview",
        )
        .exists()
    )
    round_without_action = session.scalar(
        select(ApplicationInterviewRound.id)
        .where(
            ApplicationInterviewRound.owner_id == owner_id,
            ApplicationInterviewRound.status == "scheduled",
            ~open_round_action_exists,
        )
        .limit(1)
    )
    if round_without_action is not None:
        raise ApplicationRepositoryError(
            "a scheduled interview round does not own the current action"
        )


def _today_application_action_group(
    session: Session,
    *,
    owner_id: str,
    limit: int,
    due_on: date | None = None,
    due_before: date | None = None,
    due_after: date | None = None,
    due_through: date | None = None,
) -> TodayApplicationActionGroup:
    application_join = and_(
        Application.owner_id == ActionItem.owner_id,
        Application.id == ActionItem.application_id,
    )
    predicates = [
        ActionItem.owner_id == owner_id,
        Application.owner_id == owner_id,
        ActionItem.status == "open",
        Application.stage != "closed",
    ]
    if due_on is not None:
        predicates.append(ActionItem.due_on == due_on)
    if due_before is not None:
        predicates.append(ActionItem.due_on < due_before)
    if due_after is not None:
        predicates.append(ActionItem.due_on > due_after)
    if due_through is not None:
        predicates.append(ActionItem.due_on <= due_through)

    total = int(
        session.scalar(
            select(func.count(ActionItem.id))
            .select_from(ActionItem)
            .join(Application, application_join)
            .where(*predicates)
        )
        or 0
    )
    applications = list(
        session.scalars(
            select(Application)
            .join(ActionItem, application_join)
            .where(*predicates)
            .order_by(
                ActionItem.due_on,
                ActionItem.created_at,
                ActionItem.id,
            )
            .limit(limit)
        )
    )
    summaries = _application_summaries(session, applications)
    items: list[TodayApplicationActionItem] = []
    for application, summary in zip(applications, summaries, strict=True):
        if summary.current_action is None:
            raise ApplicationRepositoryError(
                "Today application action projection has no current action"
            )
        items.append(
            TodayApplicationActionItem(
                source="application",
                application=TodayApplicationActionApplication(
                    id=application.id,
                    version=application.version,
                    opportunity_id=application.owner_opportunity_id,
                    job_posting_id=application.job_posting_id,
                    pursued_posting_version_id=(
                        application.pursued_posting_version_id
                    ),
                    stage=application.stage,
                ),
                posting=summary.posting,
                action=summary.current_action,
            )
        )
    return TodayApplicationActionGroup(total=total, items=items)


def _application_summaries(
    session: Session,
    applications: list[Application],
) -> list[ApplicationSummary]:
    """Batch-load one list page without per-application database queries."""

    if not applications:
        return []
    owner_id = applications[0].owner_id
    if any(application.owner_id != owner_id for application in applications):
        raise ApplicationRepositoryError("application page crosses owner scope")
    application_ids = [application.id for application in applications]
    posting_ids = {application.job_posting_id for application in applications}
    version_ids = {
        application.pursued_posting_version_id for application in applications
    }
    actions = {
        row.application_id: row
        for row in session.scalars(
            select(ActionItem).where(
                ActionItem.owner_id == owner_id,
                ActionItem.application_id.in_(application_ids),
                ActionItem.status == "open",
            )
        )
    }
    scheduled_rounds = {
        row.application_id: row
        for row in session.scalars(
            select(ApplicationInterviewRound).where(
                ApplicationInterviewRound.owner_id == owner_id,
                ApplicationInterviewRound.application_id.in_(application_ids),
                ApplicationInterviewRound.status == "scheduled",
            )
        )
    }
    postings = {
        row.id: row
        for row in session.scalars(
            select(JobPosting).where(
                JobPosting.owner_id == owner_id,
                JobPosting.id.in_(posting_ids),
            )
        )
    }
    versions = {
        row.id: row
        for row in session.scalars(
            select(JobPostingVersion).where(
                JobPostingVersion.owner_id == owner_id,
                JobPostingVersion.id.in_(version_ids),
            )
        )
    }
    verified_version_ids = set(
        session.scalars(
            select(JobObservation.job_posting_version_id)
            .where(
                JobObservation.owner_id == owner_id,
                JobObservation.job_posting_version_id.in_(version_ids),
                JobObservation.first_party_url_verified.is_(True),
            )
            .distinct()
        )
    )
    outcome_ids = {
        application.outcome_id
        for application in applications
        if application.outcome_id is not None
    }
    outcomes = (
        {
            row.id: row
            for row in session.scalars(
                select(ApplicationOutcome).where(
                    ApplicationOutcome.owner_id == owner_id,
                    ApplicationOutcome.id.in_(outcome_ids),
                )
            )
        }
        if outcome_ids
        else {}
    )
    summaries: list[ApplicationSummary] = []
    for application in applications:
        action = actions.get(application.id)
        posting = postings.get(application.job_posting_id)
        version = versions.get(application.pursued_posting_version_id)
        outcome = (
            outcomes.get(application.outcome_id)
            if application.outcome_id is not None
            else None
        )
        if posting is None or version is None:
            raise ApplicationRepositoryError("application graph is incomplete")
        if (application.stage == "closed") != (action is None):
            raise ApplicationRepositoryError(
                "closed applications must have no open action and active applications one"
            )
        if (application.outcome_id is None) != (outcome is None):
            raise ApplicationRepositoryError("application outcome graph is incomplete")
        scheduled_round = scheduled_rounds.get(application.id)
        if action is not None and action.interview_round_id is not None:
            if scheduled_round is None or scheduled_round.id != action.interview_round_id:
                raise ApplicationRepositoryError(
                    "an interview preparation action has no scheduled round"
                )
        elif scheduled_round is not None:
            raise ApplicationRepositoryError(
                "a scheduled interview round does not own the current action"
            )
        if version.job_posting_id != posting.id:
            raise ApplicationRepositoryError(
                "application posting version is inconsistent"
            )
        summaries.append(
            _application_summary_from_rows(
                application,
                action=action,
                posting=posting,
                version=version,
                first_party=version.id in verified_version_ids,
                outcome=outcome,
            )
        )
    return summaries


def _application_summary_from_rows(
    application: Application,
    *,
    action: ActionItem | None,
    posting: JobPosting,
    version: JobPostingVersion,
    first_party: bool,
    outcome: ApplicationOutcome | None = None,
) -> ApplicationSummary:
    return ApplicationSummary(
        id=application.id,
        version=application.version,
        opportunity_id=application.owner_opportunity_id,
        pursued_posting_version_id=application.pursued_posting_version_id,
        stage=application.stage,
        posting=ApplicationPostingSummary(
            id=posting.id,
            company=version.company_name,
            title=version.title,
            canonical_url=version.canonical_url,
            first_party=first_party,
            state=ApplicationPostingState(posting.lifecycle_state),
        ),
        current_action=_action_response(action) if action is not None else None,
        outcome=_outcome_response(outcome) if outcome is not None else None,
        created_at=_as_utc(application.created_at),
        updated_at=_as_utc(application.updated_at),
    )


def _action_response(row: ActionItem) -> ActionItemResponse:
    return ActionItemResponse(
        id=row.id,
        version=row.version,
        application_id=row.application_id,
        interview_round_id=row.interview_round_id,
        kind=row.kind,
        status=row.status,
        title=row.title,
        due_on=row.due_on,
        completed_at=_optional_utc(row.completed_at),
        cancelled_at=_optional_utc(row.cancelled_at),
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
    )


def _activity_response(
    row: ApplicationActivityEvent,
    corrections: list[ApplicationMilestoneCorrection] | None = None,
) -> ApplicationActivityEventResponse:
    saved_corrections = corrections or []
    return ApplicationActivityEventResponse(
        id=row.id,
        application_id=row.application_id,
        sequence_number=row.sequence_number,
        event_type=row.event_type,
        from_stage=row.from_stage,
        to_stage=row.to_stage,
        action_item_id=row.action_item_id,
        previous_action_item_id=row.previous_action_item_id,
        submission_id=row.submission_id,
        effective_on=row.effective_on,
        outcome_id=row.outcome_id,
        interview_round_id=row.interview_round_id,
        occurred_at=_as_utc(row.occurred_at),
        resolved_effective_on=(
            saved_corrections[-1].corrected_effective_on
            if saved_corrections
            else row.effective_on
        ),
        corrections=[_correction_response(item) for item in saved_corrections],
    )


def _correction_response(
    row: ApplicationMilestoneCorrection,
) -> ApplicationMilestoneCorrectionResponse:
    return ApplicationMilestoneCorrectionResponse(
        id=row.id,
        application_id=row.application_id,
        activity_event_id=row.activity_event_id,
        correction_number=row.correction_number,
        supersedes_correction_id=row.supersedes_correction_id,
        previous_effective_on=row.previous_effective_on,
        corrected_effective_on=row.corrected_effective_on,
        recording_method="manual",
        recorded_at=_as_utc(row.recorded_at),
        created_at=_as_utc(row.created_at),
    )


def _activity_responses(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    rows: list[ApplicationActivityEvent],
) -> list[ApplicationActivityEventResponse]:
    if not rows:
        return []
    corrections_by_event: dict[str, list[ApplicationMilestoneCorrection]] = {}
    for correction in session.scalars(
        select(ApplicationMilestoneCorrection)
        .where(
            ApplicationMilestoneCorrection.owner_id == owner_id,
            ApplicationMilestoneCorrection.application_id == application_id,
        )
        .order_by(
            ApplicationMilestoneCorrection.activity_event_id,
            ApplicationMilestoneCorrection.correction_number,
            ApplicationMilestoneCorrection.recorded_at,
            ApplicationMilestoneCorrection.id,
        )
    ):
        corrections_by_event.setdefault(correction.activity_event_id, []).append(
            correction
        )
    return [
        _activity_response(row, corrections_by_event.get(row.id)) for row in rows
    ]


def _require_action_round_consistency(
    session: Session,
    *,
    application: Application,
    action: ActionItem | None,
) -> None:
    scheduled_round = session.scalar(
        select(ApplicationInterviewRound).where(
            ApplicationInterviewRound.owner_id == application.owner_id,
            ApplicationInterviewRound.application_id == application.id,
            ApplicationInterviewRound.status == "scheduled",
        )
    )
    if action is not None and action.interview_round_id is not None:
        if scheduled_round is None or scheduled_round.id != action.interview_round_id:
            raise ApplicationRepositoryError(
                "an interview preparation action has no scheduled round"
            )
    elif scheduled_round is not None:
        raise ApplicationRepositoryError(
            "a scheduled interview round does not own the current action"
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


def _decision_event_response(
    row: OpportunityDecisionEventRow,
) -> OpportunityDecisionEvent:
    return OpportunityDecisionEvent(
        id=row.id,
        opportunity_id=row.owner_opportunity_id,
        action=OpportunityDecisionAction.pursue,
        previous_state=OpportunityDecisionState(row.previous_decision),
        state=OpportunityDecisionState.pursued,
        created_at=_as_utc(row.occurred_at),
    )


def _application_for_opportunity(
    session: Session,
    *,
    owner_id: str,
    opportunity_id: str,
    for_update: bool,
) -> Application | None:
    statement = select(Application).where(
        Application.owner_id == owner_id,
        Application.owner_opportunity_id == opportunity_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return session.scalar(statement)


def _owned_application(
    session: Session,
    owner_id: str,
    application_id: str,
) -> Application | None:
    return session.scalar(
        select(Application).where(
            Application.owner_id == owner_id,
            Application.id == application_id,
        )
    )


def _activity_rows(
    session: Session,
    owner_id: str,
    application_id: str,
) -> list[ApplicationActivityEvent]:
    return list(
        session.scalars(
            select(ApplicationActivityEvent)
            .where(
                ApplicationActivityEvent.owner_id == owner_id,
                ApplicationActivityEvent.application_id == application_id,
            )
            .order_by(
                ApplicationActivityEvent.sequence_number,
                ApplicationActivityEvent.occurred_at,
                ApplicationActivityEvent.id,
            )
            .limit(_MAX_ACTIVITY_ITEMS)
        )
    )


def _creation_decision_event(
    session: Session,
    *,
    owner_id: str,
    opportunity_id: str,
) -> OpportunityDecisionEventRow | None:
    return session.scalar(
        select(OpportunityDecisionEventRow)
        .where(
            OpportunityDecisionEventRow.owner_id == owner_id,
            OpportunityDecisionEventRow.owner_opportunity_id == opportunity_id,
            OpportunityDecisionEventRow.new_decision == "pursued",
        )
        .order_by(
            OpportunityDecisionEventRow.occurred_at,
            OpportunityDecisionEventRow.created_at,
            OpportunityDecisionEventRow.id,
        )
        .limit(1)
    )


def _latest_posting_version(
    session: Session,
    *,
    owner_id: str,
    posting_id: str,
) -> JobPostingVersion | None:
    return session.scalar(
        select(JobPostingVersion)
        .where(
            JobPostingVersion.owner_id == owner_id,
            JobPostingVersion.job_posting_id == posting_id,
        )
        .order_by(
            JobPostingVersion.version_number.desc(),
            JobPostingVersion.created_at.desc(),
            JobPostingVersion.id.desc(),
        )
        .limit(1)
    )


def _pursuit_metric_attribution(
    session: Session,
    *,
    owner_id: str,
    posting_id: str,
    request: PursueOpportunityRequest,
) -> dict[str, str | int | None]:
    """Freeze exact current search/track identity without guessing."""

    empty: dict[str, str | int | None] = {
        "saved_search_id": None,
        "saved_search_version": None,
        "saved_search_name": None,
        "career_track_id": None,
        "career_track_version": None,
        "career_track_name": None,
    }
    if (
        request.acquisition_source
        is not ApplicationAcquisitionSource.job_hunt_search
    ):
        return {"attribution_status": "captured", **empty}

    rows = list(
        session.execute(
            select(SavedSearch, CareerTrack)
            .join(
                SavedSearchMatch,
                and_(
                    SavedSearchMatch.owner_id == SavedSearch.owner_id,
                    SavedSearchMatch.saved_search_id == SavedSearch.id,
                ),
            )
            .join(
                CareerTrack,
                and_(
                    CareerTrack.owner_id == SavedSearch.owner_id,
                    CareerTrack.id == SavedSearch.career_track_id,
                ),
            )
            .where(
                SavedSearch.owner_id == owner_id,
                SavedSearchMatch.job_posting_id == posting_id,
            )
            .order_by(SavedSearch.id)
        )
    )
    selected: tuple[SavedSearch, CareerTrack] | None = None
    if request.selected_saved_search_id is not None:
        selected = next(
            (
                (search, track)
                for search, track in rows
                if search.id == request.selected_saved_search_id
            ),
            None,
        )
        if selected is None:
            raise ResourceConflict(
                "selected saved search did not produce this opportunity"
            )
    elif len(rows) == 1:
        selected = (rows[0][0], rows[0][1])
    elif len(rows) > 1:
        raise ResourceConflict(
            "multiple saved searches matched; select the search to attribute"
        )

    if selected is None:
        return {"attribution_status": "attribution_missing", **empty}
    search, track = selected
    if search.career_track_id != track.id:
        raise ApplicationRepositoryError("saved search career track identity changed")
    return {
        "attribution_status": "captured",
        "saved_search_id": search.id,
        "saved_search_version": search.version,
        "saved_search_name": search.name,
        "career_track_id": track.id,
        "career_track_version": track.version,
        "career_track_name": track.name,
    }


def _initial_action_due_on(
    requested: date | None,
    *,
    owner_timezone: str,
    now: datetime,
) -> date:
    try:
        zone = ZoneInfo(owner_timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ApplicationRepositoryError("owner timezone is invalid") from exc
    local_today = _as_utc(now).astimezone(zone).date()
    due_on = requested or (local_today + timedelta(days=1))
    if due_on < local_today or due_on > local_today + timedelta(days=365):
        raise ValueError("initial action due date must be local today through 365 days ahead")
    return due_on


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _encode_application_cursor(value: datetime, application_id: str) -> str:
    raw = _canonical_json([_as_utc(value).isoformat(), application_id]).encode(
        "utf-8"
    )
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_application_cursor(value: str) -> tuple[datetime, str]:
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        timestamp, application_id = decoded
        parsed = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("application cursor is invalid") from exc
    valid_id = isinstance(application_id, str) and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}", application_id
    )
    if not valid_id:
        raise ValueError("application cursor is invalid")
    return _as_utc(parsed), application_id


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_utc(value: datetime | None) -> datetime | None:
    return _as_utc(value) if value is not None else None


__all__ = [
    "ApplicationRepositoryError",
    "list_application_activity",
    "list_applications",
    "list_today_application_actions",
    "load_application_detail",
    "pursue_owner_opportunity",
]
