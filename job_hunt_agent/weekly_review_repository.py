"""Owner-scoped weekly review projection and explicit action decisions."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .application_milestone_dates import (
    ApplicationMilestoneDateError,
    resolved_application_milestone_dates,
)
from .application_repository import _action_response, _application_summary
from .job_queue import utcnow
from .models import (
    ActionItem,
    Application,
    ApplicationActionReview,
    ApplicationActivityEvent,
    ApplicationMetricSnapshot,
    ApplicationOutcome,
    ApplicationSubmission,
    ApplicationContact,
    Owner,
    OutreachEvent,
    OutreachReply,
)
from .mutation_receipts import claim_owner_mutation, complete_owner_mutation
from .repository_errors import ResourceConflict, require_version
from .weekly_review_schemas import (
    ApplicationActionReviewCreate,
    ApplicationActionReviewDecision,
    ApplicationActionReviewMutationResponse,
    ApplicationActionReviewResponse,
    FunnelSegmentMetric,
    FunnelStage,
    FunnelStageMetric,
    OutreachObservedMetric,
    OutreachRescueMetric,
    WeeklyReviewFunnel,
    WeeklyReviewOutreach,
    WeeklyReviewPolicy,
    WeeklyReviewResponse,
    WeeklyReviewStaleApplication,
    WeeklyReviewWindow,
)


_OBSERVATION_DAYS = 84
_APPLICATION_MATURITY_DAYS = 14
_ACTION_REVIEW_MAX_DAYS = 90
_STALE_PAGE_SIZE = 50
_STAGE_EVENT = {
    FunnelStage.screen: "application_screening",
    FunnelStage.interview: "application_interviewing",
    FunnelStage.offer: "application_offer",
}
_SOURCE_LABELS = {
    "job_hunt_search": "Job Hunt search",
    "referral": "Referral",
    "recruiter_inbound": "Recruiter inbound",
    "direct_company": "Direct company",
    "job_board": "Job board",
    "other": "Other",
}
_CONTACT_CATEGORY_LABELS = {
    "warm_path": "Warm path",
    "team_peer": "Team peer",
    "adjacent_peer": "Adjacent peer",
    "team_leader": "Team leader",
    "recruiter": "Recruiter",
    "other": "Other",
}
_SUCCESS_REPLY_KINDS = frozenset({"useful_reply", "introduced", "referred"})
_NONREPLY_OUTCOMES = frozenset({"no_reply", "unreachable"})


class WeeklyReviewRepositoryError(RuntimeError):
    """A saved weekly-review graph is incomplete or inconsistent."""


@dataclass(frozen=True)
class _FunnelApplication:
    application: Application
    submission: ApplicationSubmission
    snapshot: ApplicationMetricSnapshot | None
    converted_on: dict[FunnelStage, date]
    terminal: bool
    graph_missing: bool


@dataclass(frozen=True)
class _OutreachAttempt:
    event_id: str
    application_id: str
    application_contact_id: str
    category: str
    bench_rank: int | None
    occurred_at: datetime
    sequence_number: int
    sent_on: date
    success_on: date | None
    resolved: bool
    mature: bool


def load_weekly_review(
    session: Session,
    *,
    owner_id: str,
    now: datetime | None = None,
) -> WeeklyReviewResponse:
    """Build one bounded database-only projection at a single as-of instant."""

    current = _as_utc(now or utcnow())
    owner = session.scalar(select(Owner).where(Owner.id == owner_id))
    if owner is None:
        raise WeeklyReviewRepositoryError("weekly review owner is unavailable")
    zone = _owner_zone(owner.timezone)
    local_today = current.astimezone(zone).date()
    starts_on = local_today - timedelta(days=_OBSERVATION_DAYS - 1)

    stale_total, stale = _stale_applications(
        session,
        owner_id=owner_id,
        local_today=local_today,
    )
    funnel_rows = _funnel_applications(
        session,
        owner_id=owner_id,
        starts_on=starts_on,
        ends_on=local_today,
    )
    return WeeklyReviewResponse(
        data_source="database",
        as_of=current,
        owner_timezone=owner.timezone,
        owner_local_date=local_today,
        window=WeeklyReviewWindow(starts_on=starts_on, ends_on=local_today),
        policy=WeeklyReviewPolicy(),
        stale_application_total=stale_total,
        stale_applications=stale,
        funnel=_funnel_metrics(funnel_rows, local_today=local_today),
        outreach=_outreach_metrics(
            session,
            owner_id=owner_id,
            zone=zone,
            starts_on=starts_on,
            ends_on=local_today,
        ),
    )


def record_application_action_review(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    action_id: str,
    payload: ApplicationActionReviewCreate,
    expected_application_version: int,
    idempotency_key: str,
    now: datetime | None = None,
) -> ApplicationActionReviewMutationResponse | None:
    """Append a weekly decision and reschedule the exact current action."""

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
        select(ActionItem)
        .where(
            ActionItem.owner_id == owner_id,
            ActionItem.application_id == application.id,
            ActionItem.id == action_id,
        )
        .with_for_update()
    )
    if target is None:
        return None

    claim = claim_owner_mutation(
        session,
        owner_id=owner_id,
        namespace=f"application.action_review:{application.id}",
        idempotency_key=idempotency_key,
        request={
            "action_id": action_id,
            "payload": payload.model_dump(mode="json"),
            "expected_application_version": expected_application_version,
        },
        now=current,
    )
    if claim.replay is not None:
        if claim.replay.resource_type != "application_action_review":
            raise WeeklyReviewRepositoryError(
                "action-review receipt has the wrong resource type"
            )
        review = session.scalar(
            select(ApplicationActionReview).where(
                ApplicationActionReview.owner_id == owner_id,
                ApplicationActionReview.application_id == application.id,
                ApplicationActionReview.id == claim.replay.resource_id,
            )
        )
        if review is None or review.action_item_id != target.id:
            raise WeeklyReviewRepositoryError(
                "action-review receipt has no matching review"
            )
        if (
            claim.replay.result_version != review.new_application_version
            or review.new_application_version > application.version
            or review.new_action_version > target.version
        ):
            raise WeeklyReviewRepositoryError(
                "action-review receipt has an invalid result version"
            )
        return _action_review_mutation_response(
            session,
            application=application,
            action=target,
            review=review,
            mutation_created=False,
        )

    require_version(
        "application",
        application.id,
        expected=expected_application_version,
        actual=application.version,
    )
    if application.stage == "closed":
        raise ResourceConflict("closed applications cannot review a current action")
    if target.status != "open":
        raise ResourceConflict("only the exact current open action can be reviewed")
    if target.interview_round_id is not None:
        raise ResourceConflict(
            "interview-round actions must be managed through the interview round"
        )
    current_action_id = session.scalar(
        select(ActionItem.id).where(
            ActionItem.owner_id == owner_id,
            ActionItem.application_id == application.id,
            ActionItem.status == "open",
        )
    )
    if current_action_id != target.id:
        raise ResourceConflict("the routed action is no longer current")

    owner = session.scalar(select(Owner).where(Owner.id == owner_id))
    if owner is None:
        raise WeeklyReviewRepositoryError("action-review owner is unavailable")
    local_today = current.astimezone(_owner_zone(owner.timezone)).date()
    if (
        payload.new_due_on < local_today
        or payload.new_due_on > local_today + timedelta(days=_ACTION_REVIEW_MAX_DAYS)
    ):
        raise ValueError("new_due_on must be owner-local today through 90 days ahead")
    if payload.new_due_on <= target.due_on:
        raise ValueError("new_due_on must be strictly after the current due date")

    prior_action_version = target.version
    prior_application_version = application.version
    review = ApplicationActionReview(
        id=uuid4().hex,
        owner_id=owner_id,
        application_id=application.id,
        action_item_id=target.id,
        decision=payload.decision.value,
        prior_due_on=target.due_on,
        new_due_on=payload.new_due_on,
        prior_action_version=prior_action_version,
        new_action_version=prior_action_version + 1,
        prior_application_version=prior_application_version,
        new_application_version=prior_application_version + 1,
        recording_method="manual",
        recorded_at=current,
        idempotency_key_hash=_sha256(idempotency_key.strip()),
        created_at=current,
    )
    target.due_on = payload.new_due_on
    target.version += 1
    target.updated_at = current
    application.version += 1
    application.updated_at = current
    session.add(review)
    session.flush()
    complete_owner_mutation(
        session,
        owner_id=owner_id,
        receipt_id=claim.receipt_id,
        resource_type="application_action_review",
        resource_id=review.id,
        result_version=application.version,
        now=current,
    )
    return _action_review_mutation_response(
        session,
        application=application,
        action=target,
        review=review,
        mutation_created=True,
    )


def _stale_applications(
    session: Session,
    *,
    owner_id: str,
    local_today: date,
) -> tuple[int, list[WeeklyReviewStaleApplication]]:
    predicate = (
        (Application.owner_id == owner_id)
        & (Application.stage != "closed")
        & (ActionItem.owner_id == Application.owner_id)
        & (ActionItem.application_id == Application.id)
        & (ActionItem.status == "open")
        & (ActionItem.due_on < local_today)
    )
    total = int(
        session.scalar(
            select(func.count(ActionItem.id)).select_from(Application, ActionItem).where(predicate)
        )
        or 0
    )
    rows = list(
        session.execute(
            select(Application, ActionItem)
            .where(predicate)
            .order_by(ActionItem.due_on, Application.id, ActionItem.id)
            .limit(_STALE_PAGE_SIZE)
        )
    )
    result: list[WeeklyReviewStaleApplication] = []
    for application, action in rows:
        summary = _application_summary(session, application)
        if summary.current_action is None or summary.current_action.id != action.id:
            raise WeeklyReviewRepositoryError("stale action is not the current action")
        result.append(
            WeeklyReviewStaleApplication(
                application=summary,
                posting=summary.posting,
                current_action=summary.current_action,
                days_overdue=(local_today - action.due_on).days,
            )
        )
    return total, result


def _funnel_applications(
    session: Session,
    *,
    owner_id: str,
    starts_on: date,
    ends_on: date,
) -> list[_FunnelApplication]:
    rows = list(
        session.execute(
            select(
                ApplicationSubmission,
                Application,
                ApplicationMetricSnapshot,
                ApplicationOutcome,
            )
            .join(
                Application,
                (Application.owner_id == ApplicationSubmission.owner_id)
                & (Application.id == ApplicationSubmission.application_id),
            )
            .outerjoin(
                ApplicationMetricSnapshot,
                (ApplicationMetricSnapshot.owner_id == Application.owner_id)
                & (ApplicationMetricSnapshot.application_id == Application.id),
            )
            .outerjoin(
                ApplicationOutcome,
                (ApplicationOutcome.owner_id == Application.owner_id)
                & (ApplicationOutcome.application_id == Application.id),
            )
            .where(
                ApplicationSubmission.owner_id == owner_id,
                ApplicationSubmission.applied_on >= starts_on,
                ApplicationSubmission.applied_on <= ends_on,
            )
            .order_by(ApplicationSubmission.applied_on, ApplicationSubmission.id)
        )
    )
    result: list[_FunnelApplication] = []
    for submission, application, snapshot, outcome in rows:
        graph_missing = False
        converted_on: dict[FunnelStage, date] = {}
        activities = list(
            session.scalars(
                select(ApplicationActivityEvent).where(
                    ApplicationActivityEvent.owner_id == owner_id,
                    ApplicationActivityEvent.application_id == application.id,
                )
            )
        )
        applied_events = [
            item for item in activities if item.event_type == "application_applied"
        ]
        if (
            len(applied_events) != 1
            or applied_events[0].submission_id != submission.id
            or application.stage in {"pursuing", "ready_to_apply"}
        ):
            graph_missing = True
        try:
            resolved = resolved_application_milestone_dates(
                session,
                owner_id=owner_id,
                application_id=application.id,
            )
        except ApplicationMilestoneDateError:
            resolved = {}
            graph_missing = True
        observed_dates: list[date] = []
        for stage, event_type in _STAGE_EVENT.items():
            events = [item for item in activities if item.event_type == event_type]
            if len(events) > 1:
                graph_missing = True
                continue
            if events:
                effective_on = resolved.get(events[0].id)
                if (
                    effective_on is None
                    or effective_on < submission.applied_on
                    or effective_on > ends_on
                ):
                    graph_missing = True
                    continue
                observed_dates.append(effective_on)
                converted_on[stage] = effective_on
        if observed_dates != sorted(observed_dates):
            graph_missing = True
        terminal = outcome is not None
        if terminal and (
            application.stage != "closed"
            or outcome.application_submission_id != submission.id
            or outcome.outcome_on < submission.applied_on
            or outcome.outcome_on > ends_on
        ):
            graph_missing = True
        if (application.stage == "closed") != terminal:
            graph_missing = True
        if snapshot is not None and (
            snapshot.job_posting_id != application.job_posting_id
            or snapshot.pursued_posting_version_id
            != application.pursued_posting_version_id
        ):
            graph_missing = True
        result.append(
            _FunnelApplication(
                application=application,
                submission=submission,
                snapshot=snapshot,
                converted_on=converted_on,
                terminal=terminal,
                graph_missing=graph_missing,
            )
        )
    return result


def _funnel_metrics(
    rows: list[_FunnelApplication],
    *,
    local_today: date,
) -> WeeklyReviewFunnel:
    sources: dict[str, list[_FunnelApplication]] = defaultdict(list)
    tracks: dict[str, list[_FunnelApplication]] = defaultdict(list)
    bands: dict[str, list[_FunnelApplication]] = defaultdict(list)
    source_labels: dict[str, str] = {}
    track_labels: dict[str, str] = {}
    attribution_missing = 0
    assessment_missing = 0
    for row in rows:
        snapshot = row.snapshot
        if snapshot is None or snapshot.attribution_status != "captured":
            attribution_missing += 1
        else:
            sources[snapshot.acquisition_source].append(row)
            source_labels[snapshot.acquisition_source] = _SOURCE_LABELS.get(
                snapshot.acquisition_source,
                snapshot.acquisition_source.replace("_", " ").title(),
            )
            if snapshot.career_track_id is not None:
                tracks[snapshot.career_track_id].append(row)
                track_labels[snapshot.career_track_id] = (
                    snapshot.career_track_name or snapshot.career_track_id
                )
        if (
            snapshot is None
            or snapshot.attribution_status != "captured"
            or snapshot.assessment_state != "assessed"
            or snapshot.assessment_band is None
        ):
            assessment_missing += 1
        else:
            bands[snapshot.assessment_band].append(row)

    return WeeklyReviewFunnel(
        overall=_stage_metrics(rows, local_today=local_today),
        by_acquisition_source=[
            _segment_metric(key, source_labels[key], values, local_today=local_today)
            for key, values in sorted(sources.items())
        ],
        by_career_track=[
            _segment_metric(key, track_labels[key], values, local_today=local_today)
            for key, values in sorted(tracks.items(), key=lambda item: track_labels[item[0]])
        ],
        by_assessment_band=[
            _segment_metric(
                key,
                key.title(),
                bands[key],
                local_today=local_today,
            )
            for key in ("strong", "core", "stretch")
            if key in bands
        ],
        attribution_missing=attribution_missing,
        assessment_missing=assessment_missing,
    )


def _segment_metric(
    key: str,
    label: str,
    rows: list[_FunnelApplication],
    *,
    local_today: date,
) -> FunnelSegmentMetric:
    stages = _stage_metrics(rows, local_today=local_today)
    return FunnelSegmentMetric(
        key=key,
        label=label,
        cohort_total=len(rows),
        missing=max((stage.missing for stage in stages), default=0),
        stages=stages,
    )


def _stage_metrics(
    rows: Iterable[_FunnelApplication],
    *,
    local_today: date,
) -> list[FunnelStageMetric]:
    materialized = list(rows)
    result: list[FunnelStageMetric] = []
    for stage in FunnelStage:
        mature = evaluable = immature = censored = converted = late_converted = missing = 0
        for row in materialized:
            conversion_on = row.converted_on.get(stage)
            is_mature = (
                local_today - row.submission.applied_on
            ).days >= _APPLICATION_MATURITY_DAYS
            if not is_mature:
                immature += 1
                if row.application.stage != "closed" and conversion_on is None:
                    censored += 1
                continue
            mature += 1
            if row.graph_missing:
                missing += 1
                continue
            evaluable += 1
            if conversion_on is not None:
                horizon = row.submission.applied_on + timedelta(
                    days=_APPLICATION_MATURITY_DAYS
                )
                if conversion_on <= horizon:
                    converted += 1
                else:
                    late_converted += 1
        result.append(
            FunnelStageMetric(
                stage=stage,
                cohort_total=len(materialized),
                mature=mature,
                evaluable=evaluable,
                immature=immature,
                censored_open=censored,
                converted=converted,
                late_converted=late_converted,
                missing=missing,
                rate=None if evaluable == 0 else converted / evaluable,
            )
        )
    return result


def _outreach_metrics(
    session: Session,
    *,
    owner_id: str,
    zone: ZoneInfo,
    starts_on: date,
    ends_on: date,
) -> WeeklyReviewOutreach:
    sent_rows = list(
        session.execute(
            select(OutreachEvent, ApplicationContact)
            .join(
                ApplicationContact,
                (ApplicationContact.owner_id == OutreachEvent.owner_id)
                & (
                    ApplicationContact.application_id
                    == OutreachEvent.application_id
                )
                & (
                    ApplicationContact.id
                    == OutreachEvent.application_contact_id
                ),
            )
            .where(
                OutreachEvent.owner_id == owner_id,
                OutreachEvent.event_type == "marked_sent",
                OutreachEvent.kind == "initial",
            )
            .order_by(
                OutreachEvent.occurred_at,
                OutreachEvent.sequence_number,
                OutreachEvent.id,
            )
        )
    )
    sent_rows = [
        (event, contact)
        for event, contact in sent_rows
        if _as_utc(event.occurred_at).astimezone(zone).date() <= ends_on
    ]
    contact_ids = list({contact.id for _event, contact in sent_rows})
    replies_by_contact: dict[str, list[OutreachReply]] = defaultdict(list)
    if contact_ids:
        for reply in session.scalars(
            select(OutreachReply)
            .where(
                OutreachReply.owner_id == owner_id,
                OutreachReply.application_contact_id.in_(contact_ids),
            )
            .order_by(
                OutreachReply.received_on,
                OutreachReply.recorded_at,
                OutreachReply.id,
            )
        ):
            replies_by_contact[reply.application_contact_id].append(reply)

    outcomes_by_contact: dict[str, list[tuple[date, str]]] = defaultdict(list)
    legacy_successes = 0
    for outcome in session.scalars(
        select(OutreachEvent)
        .where(
            OutreachEvent.owner_id == owner_id,
            OutreachEvent.event_type == "outcome_recorded",
            OutreachEvent.application_contact_id.is_not(None),
        )
        .order_by(
            OutreachEvent.occurred_at,
            OutreachEvent.sequence_number,
            OutreachEvent.id,
        )
    ):
        occurred_on = _as_utc(outcome.occurred_at).astimezone(zone).date()
        if occurred_on > ends_on or outcome.application_contact_id is None:
            continue
        if outcome.outcome in _NONREPLY_OUTCOMES:
            outcomes_by_contact[outcome.application_contact_id].append(
                (occurred_on, outcome.outcome)
            )
        if (
            starts_on <= occurred_on <= ends_on
            and outcome.outcome in _SUCCESS_REPLY_KINDS
        ):
            legacy_successes += 1

    attempts: list[_OutreachAttempt] = []
    for event, contact in sent_rows:
        if event.application_contact_id is None:
            raise WeeklyReviewRepositoryError(
                "an initial marked send has no application contact"
            )
        sent_on = _as_utc(event.occurred_at).astimezone(zone).date()
        replies = [
            reply
            for reply in replies_by_contact.get(contact.id, [])
            if reply.received_on <= ends_on
        ]
        if any(reply.received_on < sent_on for reply in replies):
            raise WeeklyReviewRepositoryError(
                "an outreach reply predates its exact sent attempt"
            )
        success_dates = [
            reply.received_on
            for reply in replies
            if reply.reply_kind in _SUCCESS_REPLY_KINDS
        ]
        nonreply_resolved = any(
            occurred_on >= sent_on
            for occurred_on, _outcome in outcomes_by_contact.get(contact.id, [])
        )
        resolved = bool(replies) or nonreply_resolved
        attempts.append(
            _OutreachAttempt(
                event_id=event.id,
                application_id=event.application_id,
                application_contact_id=contact.id,
                category=contact.category,
                bench_rank=contact.bench_rank,
                occurred_at=_as_utc(event.occurred_at),
                sequence_number=event.sequence_number,
                sent_on=sent_on,
                success_on=min(success_dates) if success_dates else None,
                resolved=resolved,
                mature=(
                    resolved
                    or (ends_on - sent_on).days >= _APPLICATION_MATURITY_DAYS
                ),
            )
        )

    window_attempts = [
        item for item in attempts if starts_on <= item.sent_on <= ends_on
    ]
    categories: dict[str, list[_OutreachAttempt]] = defaultdict(list)
    positions: dict[int, list[_OutreachAttempt]] = defaultdict(list)
    for attempt in window_attempts:
        categories[attempt.category].append(attempt)
        if attempt.bench_rank is not None:
            positions[attempt.bench_rank].append(attempt)
    rescue = _rescue_metrics(attempts, starts_on=starts_on, ends_on=ends_on)
    return WeeklyReviewOutreach(
        by_contact_category=[
            _observed_outreach_metric(
                key=key,
                label=_CONTACT_CATEGORY_LABELS.get(
                    key, key.replace("_", " ").title()
                ),
                attempts=values,
            )
            for key, values in sorted(
                categories.items(),
                key=lambda item: (
                    list(_CONTACT_CATEGORY_LABELS).index(item[0])
                    if item[0] in _CONTACT_CATEGORY_LABELS
                    else len(_CONTACT_CATEGORY_LABELS)
                ),
            )
        ],
        by_sequence_position=[
            _observed_outreach_metric(
                key=str(position),
                label=f"Bench position {position}",
                attempts=values,
            )
            for position, values in sorted(positions.items())
        ],
        contacts_two_through_five=rescue,
        unattributed_legacy_successes=legacy_successes,
    )


def _observed_outreach_metric(
    *,
    key: str,
    label: str,
    attempts: list[_OutreachAttempt],
) -> OutreachObservedMetric:
    mature = sum(item.mature for item in attempts)
    successes = sum(item.success_on is not None for item in attempts)
    immature = len(attempts) - mature
    return OutreachObservedMetric(
        key=key,
        label=label,
        reached=len(attempts),
        mature=mature,
        evaluable=mature,
        successes=successes,
        censored_open=immature,
        immature=immature,
        ambiguity_excluded=0,
        observed_rate=None if mature == 0 else successes / mature,
    )


def _rescue_metrics(
    attempts: list[_OutreachAttempt],
    *,
    starts_on: date,
    ends_on: date,
) -> list[OutreachRescueMetric]:
    by_application: dict[str, list[_OutreachAttempt]] = defaultdict(list)
    for attempt in attempts:
        by_application[attempt.application_id].append(attempt)
    for application_attempts in by_application.values():
        application_attempts.sort(
            key=lambda item: (
                item.occurred_at,
                item.sequence_number,
                item.event_id,
            )
        )

    result: list[OutreachRescueMetric] = []
    for position in (2, 3, 4, 5):
        eligible: list[tuple[_OutreachAttempt, bool]] = []
        ambiguity = 0
        for application_attempts in by_application.values():
            if len(application_attempts) < position:
                continue
            target = application_attempts[position - 1]
            if not starts_on <= target.sent_on <= ends_on:
                continue
            prior_success_dates = [
                attempt.success_on
                for attempt in application_attempts[: position - 1]
                if attempt.success_on is not None
            ]
            if any(value < target.sent_on for value in prior_success_dates):
                continue
            if any(value == target.sent_on for value in prior_success_dates):
                ambiguity += 1
                continue
            target_is_first_success = False
            if target.success_on is not None:
                if any(value == target.success_on for value in prior_success_dates):
                    ambiguity += 1
                    continue
                target_is_first_success = not any(
                    value < target.success_on for value in prior_success_dates
                )
            eligible.append((target, target_is_first_success))
        mature = sum(item.mature for item, _success in eligible)
        successes = sum(success for _item, success in eligible)
        immature = len(eligible) - mature
        result.append(
            OutreachRescueMetric(
                position=position,
                reached=len(eligible) + ambiguity,
                mature=mature,
                evaluable=mature,
                successes=successes,
                censored_open=immature,
                immature=immature,
                ambiguity_excluded=ambiguity,
                observed_rate=None if mature == 0 else successes / mature,
            )
        )
    return result


def _empty_outreach() -> WeeklyReviewOutreach:
    return WeeklyReviewOutreach(
        by_contact_category=[],
        by_sequence_position=[],
        contacts_two_through_five=[
            OutreachRescueMetric(
                position=position,
                reached=0,
                mature=0,
                evaluable=0,
                successes=0,
                censored_open=0,
                immature=0,
                ambiguity_excluded=0,
                observed_rate=None,
            )
            for position in (2, 3, 4, 5)
        ],
        unattributed_legacy_successes=0,
    )


def _action_review_mutation_response(
    session: Session,
    *,
    application: Application,
    action: ActionItem,
    review: ApplicationActionReview,
    mutation_created: bool,
) -> ApplicationActionReviewMutationResponse:
    return ApplicationActionReviewMutationResponse(
        data_source="database",
        application=_application_summary(session, application),
        action=_action_response(action),
        review=ApplicationActionReviewResponse(
            id=review.id,
            application_id=review.application_id,
            prior_action_item_id=review.action_item_id,
            action_item_id=review.action_item_id,
            decision=ApplicationActionReviewDecision(review.decision),
            prior_due_on=review.prior_due_on,
            new_due_on=review.new_due_on,
            prior_action_version=review.prior_action_version,
            action_version=review.new_action_version,
            prior_application_version=review.prior_application_version,
            application_version=review.new_application_version,
            recording_method="manual",
            recorded_at=_as_utc(review.recorded_at),
            created_at=_as_utc(review.created_at),
        ),
        mutation_created=mutation_created,
    )


def _owner_zone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise WeeklyReviewRepositoryError("owner timezone is invalid") from exc


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "WeeklyReviewRepositoryError",
    "load_weekly_review",
    "record_application_action_review",
]
