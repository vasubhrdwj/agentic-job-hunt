"""Provider-free creation of durable application contact-search jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .application_schemas import CONTACTABLE_APPLICATION_STAGE_VALUES
from .contact_search_budget import (
    ContactSearchBudget,
    contact_search_budget_from_env,
)
from .job_queue import enqueue_job, utcnow
from .models import (
    Application,
    ApplicationInterviewRound,
    BackgroundJob,
    ContactPlan,
    JobPosting,
)
from .mutation_receipts import (
    MutationReplay,
    claim_owner_mutation,
    complete_owner_mutation,
    load_owner_mutation_replay,
)
from .repository_errors import ResourceConflict, require_version


CONTACT_SEARCH_JOB_KIND = "discover_contacts"
CONTACT_POLICY_VERSION = "contact-bench-v1"
CONTACT_SCORING_VERSION = "contact-score-v1"
CONTACT_TARGET_COUNT = 5
CONTACT_CANDIDATE_LIMIT = 12
# One transaction-scoped lock serializes production Postgres budget checks.
# This needs no new singleton table and is ignored by local SQLite tests.
CONTACT_SEARCH_BUDGET_ADVISORY_LOCK_KEY = 0x4A4F425F434F4E54


class ContactSearchBudgetExceeded(ResourceConflict):
    """A safe owner/global provider budget stopped new queue work."""

    code = "contact_search_budget_exhausted"

    def __init__(self, message: str, *, window: str) -> None:
        super().__init__(message)
        self.window = window


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
    budget: ContactSearchBudget | None = None,
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

    # Serialize the entire replay/reuse/budget decision on PostgreSQL. The lock
    # must come before the first replay lookup: otherwise a concurrent copy of
    # the same idempotent request can wait behind the winning transaction, see
    # its newly consumed budget slot, and be rejected instead of replayed.
    _lock_contact_search_budget(session)

    mutation_namespace = f"contact_search.create:{application.id}"
    replay = load_owner_mutation_replay(
        session,
        owner_id=owner_id,
        namespace=mutation_namespace,
        idempotency_key=idempotency_key,
        request={},
    )
    if replay is not None:
        return _replayed_contact_search(
            session,
            owner_id=owner_id,
            application_id=application.id,
            replay=replay,
        )

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
            claim = claim_owner_mutation(
                session,
                owner_id=owner_id,
                namespace=mutation_namespace,
                idempotency_key=idempotency_key,
                request={},
                now=current,
            )
            if claim.replay is not None:
                return _replayed_contact_search(
                    session,
                    owner_id=owner_id,
                    application_id=application.id,
                    replay=claim.replay,
                )
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
        claim = claim_owner_mutation(
            session,
            owner_id=owner_id,
            namespace=mutation_namespace,
            idempotency_key=idempotency_key,
            request={},
            now=current,
        )
        if claim.replay is not None:
            return _replayed_contact_search(
                session,
                owner_id=owner_id,
                application_id=application.id,
                replay=claim.replay,
            )
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
    if application.stage not in CONTACTABLE_APPLICATION_STAGE_VALUES:
        raise ResourceConflict("only active applications can search for contacts")
    interview_progress = session.scalar(
        select(ApplicationInterviewRound.id)
        .where(
            ApplicationInterviewRound.owner_id == owner_id,
            ApplicationInterviewRound.application_id == application.id,
        )
        .limit(1)
    )
    if interview_progress is not None:
        raise ResourceConflict(
            "contact discovery stops after an interview round is recorded"
        )
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

    _enforce_contact_search_budget(
        session,
        owner_id=owner_id,
        now=current,
        budget=budget or contact_search_budget_from_env(),
    )

    claim = claim_owner_mutation(
        session,
        owner_id=owner_id,
        namespace=mutation_namespace,
        idempotency_key=idempotency_key,
        request={},
        now=current,
    )
    if claim.replay is not None:
        return _replayed_contact_search(
            session,
            owner_id=owner_id,
            application_id=application.id,
            replay=claim.replay,
        )

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
        # One discovery attempt runs all three paid search lanes. Until
        # provider results are checkpointed, an automatic retry could repeat
        # every charged request after a publication failure. A user-started
        # replacement is instead counted as a new, budgeted plan.
        max_attempts=1,
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


def _replayed_contact_search(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    replay: MutationReplay,
) -> ContactSearchCreateResult:
    replay_version = replay.result_version
    if (
        replay.resource_type != "contact_plan"
        or replay.deleted
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
        application_id=application_id,
        plan_id=replay.resource_id,
    )
    if plan is None:
        raise ContactSearchRepositoryError("contact-search receipt has no contact plan")
    if replay_version > plan.version:
        raise ContactSearchRepositoryError(
            "contact-search receipt result version is ahead of its plan"
        )
    return ContactSearchCreateResult(plan=plan, created=False)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _enforce_contact_search_budget(
    session: Session,
    *,
    owner_id: str,
    now: datetime,
    budget: ContactSearchBudget,
) -> None:
    """Reject new provider work after all replay/reuse exits have run."""

    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    next_day = day_start + timedelta(days=1)
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    next_month = (
        datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
        if now.month == 12
        else datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    )

    owner_daily_count = _created_plan_count(
        session,
        start=day_start,
        end=next_day,
        owner_id=owner_id,
    )
    if owner_daily_count >= budget.owner_daily_limit:
        raise ContactSearchBudgetExceeded(
            "You have used today's contact-search allowance. "
            "Try again after 00:00 UTC.",
            window="owner_day",
        )

    global_daily_count = _created_plan_count(
        session,
        start=day_start,
        end=next_day,
    )
    if global_daily_count >= budget.global_daily_limit:
        raise ContactSearchBudgetExceeded(
            "Contact search has reached today's beta capacity. "
            "Try again after 00:00 UTC.",
            window="global_day",
        )

    global_monthly_count = _created_plan_count(
        session,
        start=month_start,
        end=next_month,
    )
    if global_monthly_count >= budget.global_monthly_limit:
        raise ContactSearchBudgetExceeded(
            "Contact search has reached this month's beta capacity. "
            "Try again next month.",
            window="global_month",
        )


def _created_plan_count(
    session: Session,
    *,
    start: datetime,
    end: datetime,
    owner_id: str | None = None,
) -> int:
    statement = select(func.count()).select_from(ContactPlan).where(
        ContactPlan.created_at >= start,
        ContactPlan.created_at < end,
    )
    if owner_id is not None:
        statement = statement.where(ContactPlan.owner_id == owner_id)
    return int(session.scalar(statement) or 0)


def _lock_contact_search_budget(session: Session) -> None:
    """Serialize plan replay, reuse, and shared-budget checks on PostgreSQL."""

    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        select(func.pg_advisory_xact_lock(CONTACT_SEARCH_BUDGET_ADVISORY_LOCK_KEY))
    )


__all__ = [
    "CONTACT_CANDIDATE_LIMIT",
    "CONTACT_POLICY_VERSION",
    "CONTACT_SCORING_VERSION",
    "CONTACT_SEARCH_JOB_KIND",
    "CONTACT_SEARCH_BUDGET_ADVISORY_LOCK_KEY",
    "CONTACT_TARGET_COUNT",
    "ContactSearchBudgetExceeded",
    "ContactSearchCreateResult",
    "ContactSearchRepositoryError",
    "create_contact_search",
]
