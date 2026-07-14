"""Durable execution and atomic publication for application contact searches.

Provider access is deliberately injected and happens only after the claimed
plan transaction has committed.  The second transaction either publishes the
entire evidence-backed candidate pool and completes the queue job, or publishes
nothing.  This keeps retries, cancellations, and lease loss from exposing a
half-built contact bench.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .application_schemas import CONTACTABLE_APPLICATION_STAGE_VALUES
from .contact_discovery import (
    BenchCoverageStatus,
    ContactBenchSelection,
    ContactDiscoveryResult,
    ContactProviderConfigurationError,
    ContactSearchProvider,
    ContactShortfallReason,
    DiscoveredContact,
    DiscoveryOutcome,
    discover_contacts,
    select_contact_bench,
)
from .contact_search_repository import CONTACT_SEARCH_JOB_KIND
from .database import Database
from .job_queue import (
    cancel_job,
    complete_job,
    fail_job_attempt,
    lock_owned_running_job,
    update_job_stage,
)
from .models import (
    Application,
    ApplicationContact,
    ApplicationInterviewRound,
    BackgroundJob,
    Contact,
    ContactPlan,
    JobPosting,
    JobPostingVersion,
)
from .schemas import CompanySource, EmploymentType, Role


LOGGER = logging.getLogger(__name__)
TERMINAL_PLAN_STATUSES = frozenset({"completed", "failed", "cancelled"})
TERMINAL_JOB_STATUSES = frozenset({"failed", "cancelled", "dead_letter"})
NON_RETRYABLE_ERRORS = frozenset(
    {
        "invalid_contact_search_reference",
        "provider_configuration_failure",
        "publication_conflict",
    }
)
MAX_PERSISTED_COMPANY_CHARS = 200


class ContactSearchClaim(Protocol):
    """Detached queue identifiers required by the contact worker."""

    job_id: str
    run_id: str
    lease_token: str


class ContactSearchWorkerError(RuntimeError):
    """A sanitized durable contact-search invariant failed."""


class ContactPublicationConflict(ContactSearchWorkerError):
    """A non-terminal plan already contains output that cannot be republished."""


@dataclass(frozen=True)
class ContactSearchWorkPlan:
    plan_id: str
    owner_id: str
    application_id: str
    job_posting_id: str
    posting_version_id: str
    candidate_limit: int
    target_count: int
    confidence_floor: float
    scoring_version: str
    role: Role


def process_claimed_contact_search(
    claim: ContactSearchClaim,
    *,
    database: Database,
    worker_id: str,
    provider: ContactSearchProvider,
    retry_delay_seconds: int = 0,
) -> None:
    """Run one live claim and publish at most one complete contact result.

    Known provider failures become durable, safe state transitions.  Unexpected
    exceptions are logged by type only and scheduled for the generic queue
    retry policy; provider payloads and exception messages are never persisted.
    """

    try:
        work = _start_contact_search(
            database,
            claim,
            worker_id=worker_id,
        )
    except ContactPublicationConflict as exc:
        LOGGER.warning(
            "contact publication conflicted plan_id=%s error_type=%s",
            claim.run_id,
            type(exc).__name__,
        )
        finish_contact_search_attempt_failure(
            database,
            claim,
            worker_id=worker_id,
            error_code="publication_conflict",
            retryable=False,
            terminal=True,
            retry_delay_seconds=retry_delay_seconds,
        )
        return
    except (ContactSearchWorkerError, ValidationError, ValueError) as exc:
        LOGGER.warning(
            "contact search reference validation failed plan_id=%s error_type=%s",
            claim.run_id,
            type(exc).__name__,
        )
        finish_contact_search_attempt_failure(
            database,
            claim,
            worker_id=worker_id,
            error_code="invalid_contact_search_reference",
            retryable=False,
            terminal=True,
            retry_delay_seconds=retry_delay_seconds,
        )
        return

    if work is None:
        return

    # There is intentionally no open database session while the provider runs.
    try:
        discovery = discover_contacts(
            work.role,
            provider=provider,
            candidate_limit=work.candidate_limit,
            observed_at=_utcnow(),
        )
    except Exception as exc:  # noqa: BLE001 - fail through a fixed safe code.
        LOGGER.warning(
            "contact provider execution failed plan_id=%s error_type=%s",
            work.plan_id,
            type(exc).__name__,
        )
        finish_contact_search_attempt_failure(
            database,
            claim,
            worker_id=worker_id,
            error_code=(
                "provider_configuration_failure"
                if isinstance(exc, ContactProviderConfigurationError)
                else "contact_search_processing_failed"
            ),
            retryable=not isinstance(exc, ContactProviderConfigurationError),
            terminal=isinstance(exc, ContactProviderConfigurationError),
            retry_delay_seconds=retry_delay_seconds,
        )
        return

    diagnostics = discovery.diagnostics
    if diagnostics.queries_succeeded == 0 and diagnostics.provider_failed:
        configuration_failure = (
            diagnostics.outcome is DiscoveryOutcome.configuration_failure
        )
        finish_contact_search_attempt_failure(
            database,
            claim,
            worker_id=worker_id,
            error_code=(
                "provider_configuration_failure"
                if configuration_failure
                else "provider_unavailable"
            ),
            retryable=not configuration_failure,
            terminal=configuration_failure,
            retry_delay_seconds=retry_delay_seconds,
        )
        return

    try:
        _publish_contact_search(
            database,
            claim,
            work=work,
            discovery=discovery,
            worker_id=worker_id,
        )
    except ContactPublicationConflict as exc:
        LOGGER.warning(
            "contact publication conflicted plan_id=%s error_type=%s",
            work.plan_id,
            type(exc).__name__,
        )
        finish_contact_search_attempt_failure(
            database,
            claim,
            worker_id=worker_id,
            error_code="publication_conflict",
            retryable=False,
            terminal=True,
            retry_delay_seconds=retry_delay_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - queue only receives the safe code.
        LOGGER.warning(
            "contact publication failed plan_id=%s error_type=%s",
            work.plan_id,
            type(exc).__name__,
        )
        finish_contact_search_attempt_failure(
            database,
            claim,
            worker_id=worker_id,
            error_code="contact_search_processing_failed",
            retryable=True,
            terminal=False,
            retry_delay_seconds=retry_delay_seconds,
        )


def finish_contact_search_attempt_failure(
    database: Database,
    claim: ContactSearchClaim,
    *,
    worker_id: str,
    error_code: str,
    retryable: bool,
    terminal: bool,
    retry_delay_seconds: int = 0,
) -> str | None:
    """Guardedly fail, retry, or cancel a claimed job and its same-owner plan."""

    current = _utcnow()
    safe_error = _safe_error_code(error_code)
    with database.session() as session:
        owned = lock_owned_running_job(
            session,
            claim.job_id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
            now=current,
        )
        if owned is None:
            return None

        plan = _lock_owned_plan(session, owned=owned, plan_id=claim.run_id)
        if plan is not None and plan.status in TERMINAL_PLAN_STATUSES:
            _reconcile_running_job_from_terminal_plan(
                session,
                owned=owned,
                plan=plan,
                worker_id=worker_id,
                lease_token=claim.lease_token,
                now=current,
            )
            return owned.status

        failed_job = fail_job_attempt(
            session,
            owned.id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
            error_code=safe_error,
            retry_delay_seconds=retry_delay_seconds,
            terminal=terminal,
            now=current,
        )
        if failed_job is None:
            return None
        if plan is None or plan.status in TERMINAL_PLAN_STATUSES:
            return failed_job.status

        if failed_job.status == "queued":
            # A retry has no published output. Restore the exact queued shape
            # required by the plan constraints while preserving attempt audit
            # history on the background job.
            plan.status = "queued"
            plan.coverage_status = "pending"
            plan.discovered_count = 0
            plan.verified_count = 0
            plan.selected_count = 0
            plan.exhausted = False
            plan.retryable = retryable
            plan.shortfall_reasons = []
            plan.error_code = None
            plan.started_at = None
            plan.finalized_at = None
        elif failed_job.status == "cancelled":
            plan.status = "cancelled"
            plan.coverage_status = "pending"
            plan.exhausted = False
            plan.retryable = False
            plan.shortfall_reasons = []
            plan.error_code = None
            plan.started_at = plan.started_at or current
            plan.finalized_at = current
        else:
            plan.status = "failed"
            plan.coverage_status = "pending"
            plan.exhausted = False
            plan.retryable = retryable
            plan.shortfall_reasons = []
            plan.error_code = safe_error
            plan.started_at = plan.started_at or current
            plan.finalized_at = current
        plan.updated_at = current
        plan.version += 1
        session.flush()
        return failed_job.status


def reconcile_terminal_contact_plans(
    session: Session,
    *,
    now: datetime | None = None,
) -> int:
    """Make active plans agree with terminal queue jobs after lease recovery.

    The helper never promotes partial rows to a completed result.  A cancelled
    queue job becomes a cancelled plan; every other terminal failure becomes a
    failed plan with a fixed, sanitized error code.
    """

    current = _as_utc(now or _utcnow())
    rows = list(
        session.execute(
            select(ContactPlan, BackgroundJob)
            .join(BackgroundJob, ContactPlan.background_job_id == BackgroundJob.id)
            .where(
                ContactPlan.owner_id == BackgroundJob.owner_id,
                BackgroundJob.kind == CONTACT_SEARCH_JOB_KIND,
                BackgroundJob.subject_type == "contact_plan",
                BackgroundJob.subject_id == ContactPlan.id,
                BackgroundJob.status.in_(TERMINAL_JOB_STATUSES),
                ContactPlan.status.in_({"queued", "running"}),
            )
            .with_for_update()
        )
    )
    for plan, job in rows:
        cancelled = job.status == "cancelled"
        plan.status = "cancelled" if cancelled else "failed"
        plan.coverage_status = "pending"
        plan.exhausted = False
        plan.retryable = (
            False
            if cancelled or job.last_error in NON_RETRYABLE_ERRORS
            else True
        )
        plan.shortfall_reasons = []
        plan.error_code = None if cancelled else _safe_error_code(job.last_error)
        plan.started_at = plan.started_at or _optional_utc(job.started_at) or current
        plan.finalized_at = current
        plan.updated_at = current
        plan.version += 1
    session.flush()
    return len(rows)


def _start_contact_search(
    database: Database,
    claim: ContactSearchClaim,
    *,
    worker_id: str,
) -> ContactSearchWorkPlan | None:
    current = _utcnow()
    with database.session() as session:
        owned = lock_owned_running_job(
            session,
            claim.job_id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
            now=current,
        )
        if owned is None:
            return None
        _validate_job_reference(owned, claim)

        graph = _lock_owned_contact_graph(
            session,
            owned=owned,
            plan_id=claim.run_id,
        )
        if graph is None:
            raise ContactSearchWorkerError("contact plan graph is unavailable")
        posting, application, plan = graph
        if plan.status in TERMINAL_PLAN_STATUSES:
            _reconcile_running_job_from_terminal_plan(
                session,
                owned=owned,
                plan=plan,
                worker_id=worker_id,
                lease_token=claim.lease_token,
                now=current,
            )
            return None
        if plan.status not in {"queued", "running"}:
            raise ContactSearchWorkerError("contact plan state is unsupported")
        _validate_plan_job_payload(plan, owned)

        existing_output = int(
            session.scalar(
                select(func.count(ApplicationContact.id)).where(
                    ApplicationContact.owner_id == owned.owner_id,
                    ApplicationContact.contact_plan_id == plan.id,
                )
            )
            or 0
        )
        if existing_output:
            raise ContactPublicationConflict(
                "non-terminal contact plan already has published output"
            )

        version = session.scalar(
            select(JobPostingVersion)
            .where(
                JobPostingVersion.owner_id == owned.owner_id,
                JobPostingVersion.job_posting_id == application.job_posting_id,
                JobPostingVersion.id == application.pursued_posting_version_id,
            )
        )
        if version is None:
            raise ContactSearchWorkerError("pinned posting version is unavailable")

        plan.status = "running"
        plan.coverage_status = "pending"
        plan.discovered_count = 0
        plan.verified_count = 0
        plan.selected_count = 0
        plan.exhausted = False
        plan.retryable = False
        plan.shortfall_reasons = []
        plan.error_code = None
        plan.started_at = current
        plan.finalized_at = None
        plan.updated_at = current
        plan.version += 1

        interview_progress = _has_interview_progress(
            session,
            application=application,
        )
        if (
            owned.cancel_requested_at is not None
            or application.stage not in CONTACTABLE_APPLICATION_STAGE_VALUES
            or posting.lifecycle_state != "open"
            or interview_progress
        ):
            _cancel_running_plan(
                session,
                owned=owned,
                plan=plan,
                worker_id=worker_id,
                lease_token=claim.lease_token,
                now=current,
                reason=(
                    "cancel_requested"
                    if owned.cancel_requested_at is not None
                    else (
                        "hiring_progress"
                        if interview_progress
                        else "application_or_posting_inactive"
                    )
                ),
            )
            return None

        role = _role_from_pinned_version(version)
        updated = update_job_stage(
            session,
            owned.id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
            stage="discovering_contacts",
            checkpoint="provider_search",
            now=current,
        )
        if not updated:
            raise ContactSearchWorkerError("contact-search lease was lost")
        session.flush()
        return ContactSearchWorkPlan(
            plan_id=plan.id,
            owner_id=owned.owner_id or "",
            application_id=application.id,
            job_posting_id=application.job_posting_id,
            posting_version_id=application.pursued_posting_version_id,
            candidate_limit=plan.candidate_limit,
            target_count=plan.target_count,
            confidence_floor=plan.confidence_floor,
            scoring_version=plan.scoring_version,
            role=role,
        )


def _publish_contact_search(
    database: Database,
    claim: ContactSearchClaim,
    *,
    work: ContactSearchWorkPlan,
    discovery: ContactDiscoveryResult,
    worker_id: str,
) -> None:
    current = _utcnow()
    with database.session() as session:
        owned = lock_owned_running_job(
            session,
            claim.job_id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
            now=current,
        )
        if owned is None:
            # Lease loss is an expected no-publication outcome.  Recovery owns
            # the subsequent job/plan transition.
            return
        graph = _lock_owned_contact_graph(
            session,
            owned=owned,
            plan_id=work.plan_id,
            expected_application_id=work.application_id,
            expected_posting_id=work.job_posting_id,
        )
        if graph is None:
            raise ContactSearchWorkerError(
                "contact plan graph disappeared before publication"
            )
        posting, application, plan = graph
        if plan.status == "completed":
            complete_job(
                session,
                owned.id,
                worker_id=worker_id,
                lease_token=claim.lease_token,
                now=current,
            )
            return
        if plan.status != "running":
            raise ContactSearchWorkerError("contact plan is not publishable")
        if (
            owned.owner_id != work.owner_id
            or plan.application_id != work.application_id
            or plan.candidate_limit != work.candidate_limit
            or plan.target_count != work.target_count
            or plan.confidence_floor != work.confidence_floor
            or plan.scoring_version != work.scoring_version
        ):
            raise ContactPublicationConflict(
                "contact plan changed after provider discovery started"
            )

        version_exists = session.scalar(
            select(JobPostingVersion.id).where(
                JobPostingVersion.owner_id == owned.owner_id,
                JobPostingVersion.job_posting_id == work.job_posting_id,
                JobPostingVersion.id == work.posting_version_id,
            )
        )
        if (
            application.pursued_posting_version_id != work.posting_version_id
            or version_exists is None
        ):
            raise ContactSearchWorkerError("pinned posting changed before publication")
        interview_progress = _has_interview_progress(
            session,
            application=application,
        )
        if (
            owned.cancel_requested_at is not None
            or application.stage not in CONTACTABLE_APPLICATION_STAGE_VALUES
            or posting.lifecycle_state != "open"
            or interview_progress
        ):
            _cancel_running_plan(
                session,
                owned=owned,
                plan=plan,
                worker_id=worker_id,
                lease_token=claim.lease_token,
                now=current,
                reason=(
                    "cancel_requested"
                    if owned.cancel_requested_at is not None
                    else (
                        "hiring_progress"
                        if interview_progress
                        else "application_or_posting_inactive"
                    )
                ),
            )
            return

        existing_output = int(
            session.scalar(
                select(func.count(ApplicationContact.id)).where(
                    ApplicationContact.owner_id == owned.owner_id,
                    ApplicationContact.contact_plan_id == plan.id,
                )
            )
            or 0
        )
        if existing_output:
            raise ContactPublicationConflict(
                "non-terminal contact plan already has published output"
            )

        contacts_by_url: dict[str, Contact] = {}
        # Different applications can discover overlapping people concurrently.
        # Lock canonical identities in one global order to avoid A→B / B→A
        # PostgreSQL deadlocks, while application rows retain discovery order.
        for candidate in sorted(
            discovery.candidates,
            key=lambda item: item.normalized_profile_url,
        ):
            contacts_by_url[candidate.normalized_profile_url] = _upsert_contact(
                session,
                owner_id=work.owner_id,
                candidate=candidate,
                now=current,
            )

        active_candidates = tuple(
            candidate
            for candidate in discovery.candidates
            if contacts_by_url[candidate.normalized_profile_url].lifecycle == "active"
        )
        active_discovery = ContactDiscoveryResult(
            candidates=active_candidates,
            rejected_results=discovery.rejected_results,
            diagnostics=discovery.diagnostics,
        )
        selection = select_contact_bench(
            active_discovery,
            target_count=work.target_count,
            confidence_floor=work.confidence_floor,
        )
        selected_ranks = {
            candidate.normalized_profile_url: rank
            for rank, candidate in enumerate(selection.selected, start=1)
        }

        lifecycle_excluded = 0
        for pool_rank, candidate in enumerate(discovery.candidates, start=1):
            contact = contacts_by_url[candidate.normalized_profile_url]
            evidence = candidate.primary_evidence
            evidence_verified = bool(
                candidate.verified_current_employer
                and candidate.confidence >= work.confidence_floor
            )
            bench_rank = selected_ranks.get(candidate.normalized_profile_url)
            exclusion_reason: str | None = None
            if bench_rank is not None:
                bench_state = "reserve"
            elif contact.lifecycle != "active":
                bench_state = "excluded"
                exclusion_reason = f"contact_{contact.lifecycle}"
                lifecycle_excluded += 1
            elif not evidence_verified:
                bench_state = "excluded"
                exclusion_reason = "below_confidence_floor"
            else:
                bench_state = "overflow"

            session.add(
                ApplicationContact(
                    owner_id=work.owner_id,
                    application_id=work.application_id,
                    contact_plan_id=work.plan_id,
                    contact_id=contact.id,
                    discovery_provider=evidence.provider,
                    discovery_query=evidence.query,
                    result_position=evidence.result_position,
                    discovered_at=_as_utc(evidence.observed_at),
                    current_title=candidate.current_title,
                    current_company=candidate.current_company,
                    category=candidate.category.value,
                    verification_status=("verified" if evidence_verified else "unverified"),
                    confidence=candidate.confidence,
                    verified_at=(
                        _as_utc(evidence.observed_at) if evidence_verified else None
                    ),
                    employer_evidence_excerpt=evidence.result_excerpt,
                    employer_evidence_url=evidence.result_url,
                    employer_evidence_source=evidence.provider,
                    employer_evidence_observed_at=_as_utc(evidence.observed_at),
                    why_relevant=candidate.why_relevant,
                    relationship_status="unknown",
                    relationship_evidence_summary=None,
                    relationship_evidence_url=None,
                    team_proximity_status="unknown",
                    team_evidence_summary=None,
                    team_evidence_url=None,
                    score_total=candidate.score_total,
                    score_components=dict(candidate.score_components),
                    scoring_version=work.scoring_version,
                    pool_rank=pool_rank,
                    bench_rank=bench_rank,
                    wave=bench_rank,
                    bench_state=bench_state,
                    exclusion_reason=exclusion_reason,
                    cooldown_until=None,
                    unlocked_at=None,
                    version=1,
                    created_at=current,
                    updated_at=current,
                )
            )

        # Publication counts are derived from the rows the database accepted,
        # never from provider objects or the in-memory selector.  Flush first
        # so constraints and rank uniqueness fail before the plan/job can be
        # finalized in this same transaction.
        session.flush()
        discovered_count, verified_count, selected_count = _persisted_plan_counts(
            session,
            owner_id=work.owner_id,
            application_id=work.application_id,
            plan_id=work.plan_id,
            candidate_limit=work.candidate_limit,
            target_count=work.target_count,
        )
        if selected_count != len(selection.selected):
            raise ContactPublicationConflict(
                "persisted contact selection does not match the selected bench"
            )

        shortfall_reasons = _final_shortfall_reasons(
            selection,
            lifecycle_excluded=lifecycle_excluded,
        )
        coverage = (
            BenchCoverageStatus.met.value
            if selected_count == work.target_count
            else BenchCoverageStatus.partial.value
        )
        if coverage == BenchCoverageStatus.partial.value and not shortfall_reasons:
            raise ContactSearchWorkerError("partial contact bench has no shortfall reason")

        plan.status = "completed"
        plan.discovered_count = discovered_count
        plan.verified_count = verified_count
        plan.selected_count = selected_count
        plan.coverage_status = coverage
        plan.exhausted = selection.exhausted
        plan.retryable = selection.retryable
        plan.shortfall_reasons = shortfall_reasons
        plan.error_code = None
        plan.started_at = plan.started_at or current
        plan.finalized_at = current
        plan.updated_at = current
        plan.version += 1
        update_job_stage(
            session,
            owned.id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
            stage="finalizing_contacts",
            checkpoint=f"selected_{selected_count}_of_{work.target_count}",
            now=current,
        )
        completed = complete_job(
            session,
            owned.id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
            now=current,
        )
        if completed is None or completed.status != "succeeded":
            raise ContactSearchWorkerError(
                "contact queue job could not complete with its publication"
            )
        session.flush()


def _upsert_contact(
    session: Session,
    *,
    owner_id: str,
    candidate: DiscoveredContact,
    now: datetime,
) -> Contact:
    identity_key = f"profile_url:{candidate.normalized_profile_url}"
    identity_hash = hashlib.sha256(identity_key.encode("utf-8")).hexdigest()
    contact = session.scalar(
        select(Contact)
        .where(
            Contact.owner_id == owner_id,
            or_(
                Contact.normalized_profile_url == candidate.normalized_profile_url,
                Contact.identity_key_hash == identity_hash,
            ),
        )
        .with_for_update()
    )
    if contact is None:
        created = Contact(
            owner_id=owner_id,
            identity_key=identity_key,
            identity_key_hash=identity_hash,
            profile_url=candidate.profile_url,
            normalized_profile_url=candidate.normalized_profile_url,
            profile_source=candidate.profile_source,
            public_name=candidate.public_name,
            lifecycle="active",
            do_not_contact_at=None,
            version=1,
            created_at=now,
            updated_at=now,
        )
        try:
            with session.begin_nested():
                session.add(created)
                session.flush()
            return created
        except IntegrityError:
            contact = session.scalar(
                select(Contact)
                .where(
                    Contact.owner_id == owner_id,
                    or_(
                        Contact.normalized_profile_url
                        == candidate.normalized_profile_url,
                        Contact.identity_key_hash == identity_hash,
                    ),
                )
                .with_for_update()
            )
            if contact is None:
                raise

    if contact.normalized_profile_url != candidate.normalized_profile_url:
        raise ContactPublicationConflict("canonical contact identity hash collided")

    # Refresh only public identity presentation.  In particular, never reset
    # do-not-contact/retired lifecycle state or its timestamp during discovery.
    changed = False
    for field, value in (
        ("profile_url", candidate.profile_url),
        ("profile_source", candidate.profile_source),
        ("public_name", candidate.public_name),
    ):
        if getattr(contact, field) != value:
            setattr(contact, field, value)
            changed = True
    if changed:
        contact.updated_at = now
        contact.version += 1
    return contact


def _persisted_plan_counts(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    plan_id: str,
    candidate_limit: int,
    target_count: int,
) -> tuple[int, int, int]:
    rows = list(
        session.scalars(
            select(ApplicationContact)
            .where(
                ApplicationContact.owner_id == owner_id,
                ApplicationContact.application_id == application_id,
                ApplicationContact.contact_plan_id == plan_id,
            )
            .order_by(ApplicationContact.pool_rank, ApplicationContact.id)
            .with_for_update()
        )
    )
    discovered_count = len(rows)
    verified_count = sum(row.verification_status == "verified" for row in rows)
    selected_rows = [row for row in rows if row.bench_rank is not None]
    selected_count = len(selected_rows)
    pool_ranks = [row.pool_rank for row in rows]
    bench_ranks = sorted(
        row.bench_rank for row in selected_rows if row.bench_rank is not None
    )
    if pool_ranks != list(range(1, discovered_count + 1)):
        raise ContactPublicationConflict("persisted contact pool ranks are not consecutive")
    if bench_ranks != list(range(1, selected_count + 1)):
        raise ContactPublicationConflict("persisted contact bench ranks are not consecutive")
    if not (
        selected_count <= verified_count <= discovered_count <= candidate_limit
        and selected_count <= target_count
    ):
        raise ContactPublicationConflict("persisted contact counts are inconsistent")
    if any(
        row.verification_status != "verified"
        or row.bench_state != "reserve"
        or row.wave != row.bench_rank
        or row.unlocked_at is not None
        for row in selected_rows
    ):
        raise ContactPublicationConflict(
            "persisted selected contacts are not verified locked reserves"
        )
    return discovered_count, verified_count, selected_count


def _final_shortfall_reasons(
    selection: ContactBenchSelection,
    *,
    lifecycle_excluded: int,
) -> list[dict[str, object]]:
    if len(selection.selected) == selection.target_count:
        return []
    reasons = [
        (
            ContactShortfallReason(
                code=reason.code,
                count=reason.count,
                detail=(
                    f"Only {len(selection.selected)} of {selection.target_count} "
                    "distinct, evidence-backed, contactable profiles were eligible."
                ),
            )
            if lifecycle_excluded and reason.code == "verified_contacts_shortfall"
            else reason
        )
        for reason in selection.shortfall_reasons
    ]
    if lifecycle_excluded:
        reasons.append(
            ContactShortfallReason(
                code="contact_lifecycle_excluded",
                count=lifecycle_excluded,
                detail=(
                    "Previously paused, retired, or do-not-contact profiles were "
                    "kept out of this application's selected bench."
                ),
            )
        )
    deduplicated: dict[str, ContactShortfallReason] = {}
    for reason in reasons:
        existing = deduplicated.get(reason.code)
        if existing is None:
            deduplicated[reason.code] = reason
        else:
            deduplicated[reason.code] = ContactShortfallReason(
                code=reason.code,
                count=min(12, existing.count + reason.count),
                detail=existing.detail,
            )
    return [
        {"code": item.code, "count": item.count, "detail": item.detail}
        for item in deduplicated.values()
    ]


def _validate_job_reference(job: BackgroundJob, claim: ContactSearchClaim) -> None:
    payload = job.payload if isinstance(job.payload, dict) else {}
    if (
        job.owner_id is None
        or job.kind != CONTACT_SEARCH_JOB_KIND
        or job.subject_type != "contact_plan"
        or job.subject_id != claim.run_id
        or payload.get("contact_plan_id") != claim.run_id
    ):
        raise ContactSearchWorkerError("queue job does not reference this contact plan")


def _validate_plan_job_payload(plan: ContactPlan, job: BackgroundJob) -> None:
    payload = job.payload if isinstance(job.payload, dict) else {}
    if (
        plan.background_job_id != job.id
        or payload.get("candidate_limit") != plan.candidate_limit
        or payload.get("target_count") != plan.target_count
        or plan.target_count != 5
        or not 5 <= plan.candidate_limit <= 12
        or not 0.75 <= plan.confidence_floor <= 1.0
    ):
        raise ContactSearchWorkerError("contact plan and queue payload disagree")


def _lock_owned_plan(
    session: Session,
    *,
    owned: BackgroundJob,
    plan_id: str,
) -> ContactPlan | None:
    if owned.owner_id is None:
        return None
    return session.scalar(
        select(ContactPlan)
        .where(
            ContactPlan.id == plan_id,
            ContactPlan.owner_id == owned.owner_id,
            ContactPlan.background_job_id == owned.id,
        )
        .with_for_update()
    )


def _lock_owned_contact_graph(
    session: Session,
    *,
    owned: BackgroundJob,
    plan_id: str,
    expected_application_id: str | None = None,
    expected_posting_id: str | None = None,
) -> tuple[JobPosting, Application, ContactPlan] | None:
    """Lock Posting → Application → ContactPlan after owner-scoped ID reads.

    Contact-search creation locks Application → ContactPlan but never waits on
    a Posting lock, while pursuit lifecycle work follows Posting → Application.
    This order therefore avoids both PostgreSQL deadlock cycles without trusting
    the initial nonlocking identifiers; every relationship is revalidated after
    all three rows are locked.
    """

    if owned.owner_id is None:
        return None
    application_id = session.scalar(
        select(ContactPlan.application_id).where(
            ContactPlan.id == plan_id,
            ContactPlan.owner_id == owned.owner_id,
            ContactPlan.background_job_id == owned.id,
        )
    )
    if application_id is None or (
        expected_application_id is not None
        and application_id != expected_application_id
    ):
        return None
    posting_id = session.scalar(
        select(Application.job_posting_id).where(
            Application.owner_id == owned.owner_id,
            Application.id == application_id,
        )
    )
    if posting_id is None or (
        expected_posting_id is not None and posting_id != expected_posting_id
    ):
        return None

    posting = session.scalar(
        select(JobPosting)
        .where(
            JobPosting.owner_id == owned.owner_id,
            JobPosting.id == posting_id,
        )
        .with_for_update()
    )
    application = session.scalar(
        select(Application)
        .where(
            Application.owner_id == owned.owner_id,
            Application.id == application_id,
        )
        .with_for_update()
    )
    plan = session.scalar(
        select(ContactPlan)
        .where(
            ContactPlan.id == plan_id,
            ContactPlan.owner_id == owned.owner_id,
            ContactPlan.application_id == application_id,
            ContactPlan.background_job_id == owned.id,
        )
        .with_for_update()
    )
    if (
        posting is None
        or application is None
        or plan is None
        or application.job_posting_id != posting.id
        or plan.application_id != application.id
    ):
        return None
    return posting, application, plan


def _role_from_pinned_version(version: JobPostingVersion) -> Role:
    company = version.company_name.strip()
    if not company or len(company) > MAX_PERSISTED_COMPANY_CHARS:
        raise ContactSearchWorkerError(
            "posting company is outside the contact evidence storage bound"
        )
    return Role(
        company=company,
        title=version.title,
        url=version.canonical_url,
        location=version.location,
        summary=version.summary,
        match_reason="Pinned posting selected for contact discovery.",
        source=CompanySource(version.source),
        company_slug=None,
        source_job_id=None,
        apply_urls=list(version.apply_urls),
        posted_at=version.posted_at_text,
        source_updated_at=version.source_updated_at_text,
        employment_type=EmploymentType(version.employment_type),
        raw_description=version.description,
        confidence=version.source_confidence,
    )


def _has_interview_progress(
    session: Session,
    *,
    application: Application,
) -> bool:
    """Fail closed once any durable interview round exists for the application."""

    return (
        session.scalar(
            select(ApplicationInterviewRound.id)
            .where(
                ApplicationInterviewRound.owner_id == application.owner_id,
                ApplicationInterviewRound.application_id == application.id,
            )
            .limit(1)
        )
        is not None
    )


def _cancel_running_plan(
    session: Session,
    *,
    owned: BackgroundJob,
    plan: ContactPlan,
    worker_id: str,
    lease_token: str,
    now: datetime,
    reason: str,
) -> None:
    plan.status = "cancelled"
    plan.coverage_status = "pending"
    plan.exhausted = False
    plan.retryable = False
    plan.shortfall_reasons = []
    plan.error_code = None
    plan.started_at = plan.started_at or now
    plan.finalized_at = now
    plan.updated_at = now
    plan.version += 1
    cancel_job(session, owned.id, actor=worker_id, reason=reason, now=now)
    completed = complete_job(
        session,
        owned.id,
        worker_id=worker_id,
        lease_token=lease_token,
        now=now,
    )
    if completed is None or completed.status != "cancelled":
        raise ContactSearchWorkerError("contact cancellation lost queue ownership")


def _reconcile_running_job_from_terminal_plan(
    session: Session,
    *,
    owned: BackgroundJob,
    plan: ContactPlan,
    worker_id: str,
    lease_token: str,
    now: datetime,
) -> None:
    if plan.status == "completed":
        complete_job(
            session,
            owned.id,
            worker_id=worker_id,
            lease_token=lease_token,
            now=now,
        )
        return
    if plan.status == "cancelled":
        cancel_job(session, owned.id, actor=worker_id, reason="plan_cancelled", now=now)
        complete_job(
            session,
            owned.id,
            worker_id=worker_id,
            lease_token=lease_token,
            now=now,
        )
        return
    fail_job_attempt(
        session,
        owned.id,
        worker_id=worker_id,
        lease_token=lease_token,
        error_code=plan.error_code or "contact_search_failed",
        terminal=True,
        now=now,
    )


def _safe_error_code(value: str | None) -> str:
    cleaned = re.sub(r"[^a-z0-9_.-]+", "_", (value or "").strip().lower())
    cleaned = cleaned.strip("_.-")[:100]
    if not cleaned or not cleaned[0].isalpha():
        return "contact_search_interrupted"
    return cleaned


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_utc(value: datetime | None) -> datetime | None:
    return _as_utc(value) if value is not None else None


__all__ = [
    "CONTACT_SEARCH_JOB_KIND",
    "ContactPublicationConflict",
    "ContactSearchClaim",
    "ContactSearchWorkPlan",
    "ContactSearchWorkerError",
    "finish_contact_search_attempt_failure",
    "process_claimed_contact_search",
    "reconcile_terminal_contact_plans",
]
