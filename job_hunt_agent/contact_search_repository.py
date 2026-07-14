"""Provider-free creation of durable application contact-search jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .application_schemas import ACTIVE_APPLICATION_STAGE_VALUES
from .job_queue import enqueue_job, utcnow
from .models import Application, BackgroundJob, ContactPlan, JobPosting
from .mutation_receipts import claim_owner_mutation, complete_owner_mutation
from .repository_errors import ResourceConflict, require_version


CONTACT_SEARCH_JOB_KIND = "discover_contacts"
CONTACT_POLICY_VERSION = "contact-bench-v1"
CONTACT_SCORING_VERSION = "contact-score-v1"
CONTACT_TARGET_COUNT = 5
CONTACT_CANDIDATE_LIMIT = 12


class ContactSearchRepositoryError(RuntimeError):
    """A safe durable contact-search invariant failed."""


@dataclass(frozen=True)
class ContactSearchCreateResult:
    plan: ContactPlan
    created: bool


def create_contact_search(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    expected_application_version: int,
    idempotency_key: str,
    now: datetime | None = None,
) -> ContactSearchCreateResult | None:
    """Create or replay one queued contact search without invoking a provider."""

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

    claim = claim_owner_mutation(
        session,
        owner_id=owner_id,
        namespace=f"contact_search.create:{application.id}",
        idempotency_key=idempotency_key,
        request={},
        now=current,
    )
    if claim.replay is not None:
        replay_version = claim.replay.result_version
        if (
            claim.replay.resource_type != "contact_plan"
            or claim.replay.deleted
            or isinstance(replay_version, bool)
            or not isinstance(replay_version, int)
            or replay_version < 1
        ):
            raise ContactSearchRepositoryError(
                "contact-search receipt has inconsistent result metadata"
            )
        plan = _owned_plan(
            session,
            owner_id=owner_id,
            application_id=application.id,
            plan_id=claim.replay.resource_id,
        )
        if plan is None:
            raise ContactSearchRepositoryError(
                "contact-search receipt has no contact plan"
            )
        if replay_version > plan.version:
            raise ContactSearchRepositoryError(
                "contact-search receipt result version is ahead of its plan"
            )
        return ContactSearchCreateResult(plan=plan, created=False)

    active = session.scalar(
        select(ContactPlan)
        .where(
            ContactPlan.owner_id == owner_id,
            ContactPlan.application_id == application.id,
            ContactPlan.status.in_({"queued", "running"}),
        )
        .with_for_update()
    )
    if active is not None:
        active_job = (
            session.get(BackgroundJob, active.background_job_id)
            if active.background_job_id is not None
            else None
        )
        active_job_is_valid = (
            active_job is not None
            and active_job.owner_id == owner_id
            and active_job.kind == CONTACT_SEARCH_JOB_KIND
            and active_job.subject_type == "contact_plan"
            and active_job.subject_id == active.id
            and active_job.status in {"queued", "running"}
        )
        if active_job_is_valid:
            complete_owner_mutation(
                session,
                owner_id=owner_id,
                receipt_id=claim.receipt_id,
                resource_type="contact_plan",
                resource_id=active.id,
                result_version=active.version,
                now=current,
            )
            return ContactSearchCreateResult(plan=active, created=False)
        # Never return an active-looking plan whose queue work has vanished or
        # terminated. Persist this attempt as an auditable failure and stop.
        # A later request with a new idempotency key may create replacement
        # work after the normal version, stage, and posting checks.
        active.status = "failed"
        active.coverage_status = "pending"
        active.exhausted = False
        active.retryable = True
        active.shortfall_reasons = []
        active.error_code = "search_job_unavailable"
        active.started_at = active.started_at or current
        active.finalized_at = current
        active.updated_at = current
        active.version += 1
        session.flush()
        complete_owner_mutation(
            session,
            owner_id=owner_id,
            receipt_id=claim.receipt_id,
            resource_type="contact_plan",
            resource_id=active.id,
            result_version=active.version,
            now=current,
        )
        return ContactSearchCreateResult(plan=active, created=False)

    require_version(
        "application",
        application.id,
        expected=expected_application_version,
        actual=application.version,
    )
    if application.stage not in ACTIVE_APPLICATION_STAGE_VALUES:
        raise ResourceConflict("only active applications can search for contacts")
    posting_state = session.scalar(
        select(JobPosting.lifecycle_state).where(
            JobPosting.owner_id == owner_id,
            JobPosting.id == application.job_posting_id,
        )
    )
    if posting_state is None:
        raise ContactSearchRepositoryError("application posting is unavailable")
    if posting_state != "open":
        raise ResourceConflict("closed postings cannot start a new contact search")

    previous_number = int(
        session.scalar(
            select(func.max(ContactPlan.plan_number)).where(
                ContactPlan.owner_id == owner_id,
                ContactPlan.application_id == application.id,
            )
        )
        or 0
    )
    plan_id = uuid4().hex
    queued = enqueue_job(
        session,
        kind=CONTACT_SEARCH_JOB_KIND,
        dedupe_key=f"contacts:{plan_id}",
        owner_id=owner_id,
        subject_type="contact_plan",
        subject_id=plan_id,
        payload={
            "contact_plan_id": plan_id,
            "candidate_limit": CONTACT_CANDIDATE_LIMIT,
            "target_count": CONTACT_TARGET_COUNT,
        },
        priority=75,
        max_attempts=3,
        run_after=current,
        actor=f"owner:{owner_id}",
    )
    if not queued.created or queued.job.owner_id != owner_id:
        raise ContactSearchRepositoryError("contact-search queue identity is inconsistent")
    plan = ContactPlan(
        id=plan_id,
        owner_id=owner_id,
        application_id=application.id,
        plan_number=previous_number + 1,
        status="queued",
        target_count=CONTACT_TARGET_COUNT,
        candidate_limit=CONTACT_CANDIDATE_LIMIT,
        confidence_floor=0.75,
        policy_version=CONTACT_POLICY_VERSION,
        scoring_version=CONTACT_SCORING_VERSION,
        background_job_id=queued.job.id,
        discovered_count=0,
        verified_count=0,
        selected_count=0,
        coverage_status="pending",
        exhausted=False,
        retryable=False,
        shortfall_reasons=[],
        version=1,
        created_at=current,
        updated_at=current,
    )
    session.add(plan)
    session.flush()
    complete_owner_mutation(
        session,
        owner_id=owner_id,
        receipt_id=claim.receipt_id,
        resource_type="contact_plan",
        resource_id=plan.id,
        result_version=plan.version,
        now=current,
    )
    return ContactSearchCreateResult(plan=plan, created=True)


def _owned_plan(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    plan_id: str,
) -> ContactPlan | None:
    return session.scalar(
        select(ContactPlan).where(
            ContactPlan.owner_id == owner_id,
            ContactPlan.application_id == application_id,
            ContactPlan.id == plan_id,
        )
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "CONTACT_CANDIDATE_LIMIT",
    "CONTACT_POLICY_VERSION",
    "CONTACT_SCORING_VERSION",
    "CONTACT_SEARCH_JOB_KIND",
    "CONTACT_TARGET_COUNT",
    "ContactSearchCreateResult",
    "ContactSearchRepositoryError",
    "create_contact_search",
]
