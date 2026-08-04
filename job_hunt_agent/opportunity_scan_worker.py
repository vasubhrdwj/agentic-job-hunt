"""Search-only practical worker for durable saved-search scans.

This module fetches first-party job postings and persists normalized public
facts.  It never loads a resume, calls referral discovery, drafts outreach, or
invokes a model provider.
"""

from __future__ import annotations

import hashlib
import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol

from pydantic import ValidationError
from sqlalchemy import func, select

from .database import Database
from .job_queue import complete_job, fail_job_attempt, lock_owned_running_job, update_job_stage
from .models import (
    JobObservation,
    OpportunityScan,
    OpportunityScanSource,
    SavedSearch,
)
from .opportunity_repository import (
    OpportunityRepositoryError,
    PostingIdentityConflict,
    persist_scan_source_role,
)
from .opportunity_fit_worker import (
    enqueue_opportunity_fit_evaluation,
    fit_evaluation_jobs_enabled,
    fit_profile_revision_token,
)
from .schemas import Company, JobCriteria, Role
from .sources.base import FetchCompleteness, FetchScope, SourceFetchResult
from .sources.registry import CompanyRegistry, RegistryError, load_company_pack
from .sources.resolver import SourceResolver, is_first_party_role
from .worker_health import ROLE_SCAN_JOB_KIND


LOGGER = logging.getLogger(__name__)
SCAN_JOB_KIND = ROLE_SCAN_JOB_KIND
TERMINAL_SCAN_STATUSES = frozenset(
    {"succeeded", "partial", "failed", "cancelled"}
)
TERMINAL_SOURCE_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
MAX_FETCH_WORKERS = 4


class ScanClaim(Protocol):
    job_id: str
    run_id: str
    lease_token: str


@dataclass(frozen=True)
class ScanSourcePlan:
    id: str
    company_slug: str
    source: str


@dataclass(frozen=True)
class ScanPlan:
    scan_id: str
    owner_id: str
    saved_search_id: str
    pack: str
    criteria: JobCriteria
    sources: tuple[ScanSourcePlan, ...]


def process_claimed_opportunity_scan(
    claim: ScanClaim,
    *,
    database: Database,
    worker_id: str,
    use_mocks: bool = False,
) -> None:
    """Process one live queue claim without holding a network transaction."""

    try:
        plan = _start_scan(database, claim, worker_id=worker_id)
        if plan is None:
            return
        registry = load_company_pack(plan.pack)
    except (RegistryError, ValidationError, ValueError) as exc:
        LOGGER.warning(
            "opportunity scan configuration failed scan_id=%s error_type=%s",
            claim.run_id,
            type(exc).__name__,
        )
        _fail_scan(
            database,
            claim,
            worker_id=worker_id,
            error_code="invalid_scan_configuration",
        )
        return

    companies = {company.slug: company for company in registry.active_companies}
    runnable: list[tuple[int, ScanSourcePlan, Company]] = []
    for index, source in enumerate(plan.sources):
        company = companies.get(source.company_slug)
        if company is None or company.source.value != source.source:
            _record_source_failure(
                database,
                claim,
                source,
                worker_id=worker_id,
                error_code="source_configuration_changed",
            )
            continue
        if _mark_source_running(
            database,
            claim,
            source,
            worker_id=worker_id,
        ):
            runnable.append((index, source, company))

    futures: dict[Future[SourceFetchResult], tuple[ScanSourcePlan, Company]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(MAX_FETCH_WORKERS, len(runnable)))) as pool:
        for index, source, company in runnable:
            future = pool.submit(
                _fetch_company,
                company,
                plan.criteria,
                use_mocks=use_mocks,
                mock_index=index,
            )
            futures[future] = (source, company)

        for future in as_completed(futures):
            source, company = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # resolver normally converts errors to safe results.
                LOGGER.warning(
                    "opportunity source fetch failed scan_id=%s company=%s error_type=%s",
                    claim.run_id,
                    company.slug,
                    type(exc).__name__,
                )
                _record_source_failure(
                    database,
                    claim,
                    source,
                    worker_id=worker_id,
                    error_code="source_fetch_failed",
                )
                continue
            _persist_source_result(
                database,
                claim,
                source,
                company,
                result,
                worker_id=worker_id,
            )

    _finalize_scan(database, claim, worker_id=worker_id)


def _start_scan(
    database: Database,
    claim: ScanClaim,
    *,
    worker_id: str,
) -> ScanPlan | None:
    current = _utcnow()
    with database.session() as session:
        owned = lock_owned_running_job(
            session,
            claim.job_id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
        )
        if owned is None:
            return None
        scan = session.scalar(
            select(OpportunityScan)
            .where(
                OpportunityScan.id == claim.run_id,
                OpportunityScan.owner_id == owned.owner_id,
                OpportunityScan.background_job_id == owned.id,
            )
            .with_for_update()
        )
        if scan is None:
            raise ValueError("scan claim does not reference an owner scan")
        if scan.status in TERMINAL_SCAN_STATUSES:
            complete_job(
                session,
                owned.id,
                worker_id=worker_id,
                lease_token=claim.lease_token,
            )
            return None
        criteria = JobCriteria.model_validate(scan.criteria_snapshot)
        sources = tuple(
            ScanSourcePlan(
                id=row.id,
                company_slug=row.company_slug,
                source=row.source,
            )
            for row in session.scalars(
                select(OpportunityScanSource)
                .where(
                    OpportunityScanSource.owner_id == scan.owner_id,
                    OpportunityScanSource.opportunity_scan_id == scan.id,
                )
                .order_by(
                    OpportunityScanSource.company_slug,
                    OpportunityScanSource.source,
                    OpportunityScanSource.id,
                )
            )
        )
        scan.status = "running"
        scan.stage = "fetching"
        scan.started_at = scan.started_at or current
        scan.source_count = len(sources)
        scan.updated_at = current
        scan.version += 1
        update_job_stage(
            session,
            owned.id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
            stage="fetching",
            checkpoint="source_inventory",
            now=current,
        )
        session.flush()
        return ScanPlan(
            scan_id=scan.id,
            owner_id=scan.owner_id,
            saved_search_id=scan.saved_search_id,
            pack=scan.pack_snapshot,
            criteria=criteria,
            sources=sources,
        )


def _mark_source_running(
    database: Database,
    claim: ScanClaim,
    source: ScanSourcePlan,
    *,
    worker_id: str,
) -> bool:
    current = _utcnow()
    with database.session() as session:
        owned = lock_owned_running_job(
            session,
            claim.job_id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
        )
        if owned is None or owned.cancel_requested_at is not None:
            return False
        row = session.scalar(
            select(OpportunityScanSource)
            .where(
                OpportunityScanSource.owner_id == owned.owner_id,
                OpportunityScanSource.opportunity_scan_id == claim.run_id,
                OpportunityScanSource.id == source.id,
            )
            .with_for_update()
        )
        if row is None or row.status == "succeeded":
            return False
        row.status = "running"
        row.started_at = current
        row.completed_at = None
        row.error_code = None
        row.updated_at = current
        row.version += 1
        update_job_stage(
            session,
            owned.id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
            stage="fetching",
            checkpoint=source.company_slug,
            now=current,
        )
        session.flush()
        return True


def _fetch_company(
    company: Company,
    criteria: JobCriteria,
    *,
    use_mocks: bool,
    mock_index: int,
) -> SourceFetchResult:
    if use_mocks:
        return _mock_fetch_result(company, criteria, index=mock_index)
    return SourceResolver().fetch_company_roles_result(
        company,
        criteria,
        use_cache=False,
        allow_fallback=False,
    )


def _persist_source_result(
    database: Database,
    claim: ScanClaim,
    source: ScanSourcePlan,
    company: Company,
    result: SourceFetchResult,
    *,
    worker_id: str,
) -> None:
    completed_at = _utcnow()
    with database.session() as session:
        owned = lock_owned_running_job(
            session,
            claim.job_id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
        )
        if owned is None:
            return
        row = session.scalar(
            select(OpportunityScanSource)
            .where(
                OpportunityScanSource.owner_id == owned.owner_id,
                OpportunityScanSource.opportunity_scan_id == claim.run_id,
                OpportunityScanSource.id == source.id,
            )
            .with_for_update()
        )
        if row is None:
            return
        scan = session.scalar(
            select(OpportunityScan).where(
                OpportunityScan.owner_id == owned.owner_id,
                OpportunityScan.id == row.opportunity_scan_id,
            )
        )
        if scan is None:
            return
        plan_saved_search_id = scan.saved_search_id
        fit_revision_token = (
            fit_profile_revision_token(
                session,
                owner_id=owned.owner_id,
                saved_search_id=plan_saved_search_id,
            )
            if fit_evaluation_jobs_enabled()
            else None
        )

        warning_codes = set(result.warning_codes)
        # The repository increments persisted_count per observation, so pin
        # the source's truthful fetch bounds before the first child flush.
        row.fetch_scope = result.scope.value
        row.completeness = result.completeness.value
        row.observed_count = max(result.observed_count, row.persisted_count)
        row.returned_count = max(result.returned_count, row.persisted_count)
        row.started_at = _as_utc(result.started_at)
        row.updated_at = completed_at
        session.flush()
        for role in result.roles:
            if not is_first_party_role(role, company):
                warning_codes.add("untrusted_url_skipped")
                continue
            safe_role, skipped_apply_url = _role_with_first_party_apply_urls(
                role,
                company,
            )
            if skipped_apply_url:
                warning_codes.add("untrusted_apply_url_skipped")
            try:
                with session.begin_nested():
                    persisted = persist_scan_source_role(
                        session,
                        owner_id=owned.owner_id,
                        scan_source_id=row.id,
                        role=safe_role,
                        first_party_url_verified=True,
                        now=completed_at,
                    )
                if persisted.version_created:
                    enqueue_opportunity_fit_evaluation(
                        session,
                        owner_id=owned.owner_id,
                        posting_id=persisted.posting_id,
                        posting_version_id=persisted.posting_version_id,
                        saved_search_id=plan_saved_search_id,
                        profile_revision_token=fit_revision_token,
                    )
            except (ValueError, PostingIdentityConflict, OpportunityRepositoryError):
                warning_codes.add("source_invalid_record")

        fetch_failed = "source_fetch_failed" in warning_codes
        row.status = "failed" if fetch_failed else "succeeded"
        row.observed_count = max(result.observed_count, row.persisted_count)
        row.returned_count = max(result.returned_count, row.persisted_count)
        row.warning_codes = sorted(warning_codes)
        row.error_code = "source_fetch_failed" if fetch_failed else None
        row.used_fallback = result.used_fallback
        row.cache_hit = result.cache_hit
        row.started_at = _as_utc(result.started_at)
        row.completed_at = max(_as_utc(result.completed_at), completed_at)
        row.updated_at = completed_at
        row.version += 1
        session.flush()


def _role_with_first_party_apply_urls(
    role: Role,
    company: Company,
) -> tuple[Role, bool]:
    """Drop alternate apply links that fail the role URL trust boundary."""

    safe_urls: list[str] = []
    skipped = False
    for apply_url in role.apply_urls:
        candidate = role.model_copy(
            update={
                "url": apply_url,
                "apply_urls": [],
            }
        )
        if is_first_party_role(candidate, company):
            safe_urls.append(apply_url)
        else:
            skipped = True
    return role.model_copy(update={"apply_urls": safe_urls}), skipped


def _record_source_failure(
    database: Database,
    claim: ScanClaim,
    source: ScanSourcePlan,
    *,
    worker_id: str,
    error_code: str,
) -> None:
    current = _utcnow()
    with database.session() as session:
        owned = lock_owned_running_job(
            session,
            claim.job_id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
        )
        if owned is None:
            return
        row = session.scalar(
            select(OpportunityScanSource)
            .where(
                OpportunityScanSource.owner_id == owned.owner_id,
                OpportunityScanSource.opportunity_scan_id == claim.run_id,
                OpportunityScanSource.id == source.id,
            )
            .with_for_update()
        )
        if row is None:
            return
        row.status = "failed"
        row.completeness = "unknown"
        row.error_code = error_code
        row.warning_codes = sorted(set([*row.warning_codes, error_code]))
        row.started_at = row.started_at or current
        row.completed_at = current
        row.updated_at = current
        row.version += 1
        session.flush()


def _finalize_scan(
    database: Database,
    claim: ScanClaim,
    *,
    worker_id: str,
) -> None:
    current = _utcnow()
    with database.session() as session:
        owned = lock_owned_running_job(
            session,
            claim.job_id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
        )
        if owned is None:
            return
        scan = session.scalar(
            select(OpportunityScan)
            .where(
                OpportunityScan.owner_id == owned.owner_id,
                OpportunityScan.id == claim.run_id,
            )
            .with_for_update()
        )
        if scan is None:
            return
        sources = list(
            session.scalars(
                select(OpportunityScanSource).where(
                    OpportunityScanSource.owner_id == owned.owner_id,
                    OpportunityScanSource.opportunity_scan_id == scan.id,
                )
            )
        )
        if any(source.status not in TERMINAL_SOURCE_STATUSES for source in sources):
            return

        failed = sum(source.status in {"failed", "cancelled"} for source in sources)
        successful = sum(source.status == "succeeded" for source in sources)
        degraded = sum(
            source.status == "succeeded"
            and (
                source.completeness != "complete"
                or bool(source.warning_codes)
            )
            for source in sources
        )
        scan.source_count = len(sources)
        scan.terminal_source_count = len(sources)
        scan.successful_source_count = successful
        scan.failed_source_count = failed
        scan.observed_count = int(
            session.scalar(
                select(func.count(JobObservation.id)).where(
                    JobObservation.owner_id == owned.owner_id,
                    JobObservation.opportunity_scan_id == scan.id,
                )
            )
            or 0
        )
        if owned.cancel_requested_at is not None:
            scan.status = "cancelled"
        elif sources and failed == len(sources):
            scan.status = "failed"
        elif failed or degraded:
            scan.status = "partial"
        else:
            scan.status = "succeeded"
        scan.stage = "complete"
        scan.started_at = scan.started_at or current
        scan.finalized_at = current
        scan.updated_at = current
        scan.version += 1

        search = session.scalar(
            select(SavedSearch)
            .where(
                SavedSearch.owner_id == scan.owner_id,
                SavedSearch.id == scan.saved_search_id,
            )
            .with_for_update()
        )
        if search is not None and scan.status in {"succeeded", "partial"}:
            search.last_scan_at = current
            search.updated_at = current

        complete_job(
            session,
            owned.id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
            now=current,
        )
        session.flush()


def _fail_scan(
    database: Database,
    claim: ScanClaim,
    *,
    worker_id: str,
    error_code: str,
) -> None:
    current = _utcnow()
    with database.session() as session:
        owned = lock_owned_running_job(
            session,
            claim.job_id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
        )
        if owned is None:
            return
        scan = session.scalar(
            select(OpportunityScan)
            .where(
                OpportunityScan.owner_id == owned.owner_id,
                OpportunityScan.id == claim.run_id,
            )
            .with_for_update()
        )
        if scan is not None:
            sources = list(
                session.scalars(
                    select(OpportunityScanSource)
                    .where(
                        OpportunityScanSource.owner_id == owned.owner_id,
                        OpportunityScanSource.opportunity_scan_id == scan.id,
                        OpportunityScanSource.status.not_in(TERMINAL_SOURCE_STATUSES),
                    )
                    .with_for_update()
                )
            )
            for source in sources:
                source.status = "failed"
                source.completeness = "unknown"
                source.error_code = error_code
                source.warning_codes = sorted(set([*source.warning_codes, error_code]))
                source.started_at = source.started_at or current
                source.completed_at = current
                source.updated_at = current
                source.version += 1
            scan.status = "cancelled" if owned.cancel_requested_at else "failed"
            scan.stage = "complete"
            scan.started_at = scan.started_at or current
            scan.finalized_at = current
            scan.terminal_source_count = len(sources)
            scan.failed_source_count = len(sources)
            scan.updated_at = current
            scan.version += 1
        fail_job_attempt(
            session,
            owned.id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
            error_code=error_code,
            terminal=True,
            now=current,
        )


def _mock_fetch_result(
    company: Company,
    criteria: JobCriteria,
    *,
    index: int,
) -> SourceFetchResult:
    started = _utcnow()
    roles: list[Role] = []
    if index < 3:
        keyword = next(
            (value.strip() for value in criteria.role_keywords if value.strip()),
            "Backend",
        )
        title = f"{criteria.seniority.title()} {keyword.title()} Engineer"
        stable = hashlib.sha256(
            f"{company.slug}:{criteria.model_dump_json()}".encode("utf-8")
        ).hexdigest()[:16]
        hostname = company.careers_domains[0]
        url = f"https://{hostname}/codex-mock/jobs/{stable}"
        roles.append(
            Role(
                company=company.name,
                title=title,
                url=url,
                location=criteria.location[0] if criteria.location else "Remote",
                summary=(
                    "Build reliable backend services and own production outcomes "
                    "for a growing engineering team."
                ),
                match_reason="Deterministic local fixture matching the saved search.",
                source=company.source,
                company_slug=company.slug,
                source_job_id=f"mock-{stable}",
                apply_urls=[url],
                posted_at=date.today().isoformat(),
                employment_type=(
                    criteria.employment_types[0]
                    if criteria.employment_types
                    else "unknown"
                ),
                raw_description=(
                    "Design, build, and operate backend services. Collaborate across "
                    "product and infrastructure teams and improve system reliability."
                ),
            )
        )
    completed = _utcnow()
    return SourceFetchResult(
        company_slug=company.slug,
        source=company.source,
        started_at=started,
        completed_at=completed,
        scope=FetchScope.criteria_filtered,
        completeness=FetchCompleteness.partial,
        roles=roles,
        observed_count=len(roles),
        returned_count=len(roles),
        warning_codes=[],
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "SCAN_JOB_KIND",
    "ScanClaim",
    "process_claimed_opportunity_scan",
]
