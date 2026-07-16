"""Durable queue worker for processing encrypted hunt requests."""

from __future__ import annotations

import argparse
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import persistence
from .config import env_bool, is_production, practical_mode_enabled
from .contact_discovery import ContactProviderConfigurationError
from .contact_providers import MockContactSearchProvider, SerpAPIContactProvider
from .contact_search_repository import CONTACT_SEARCH_JOB_KIND
from .contact_search_worker import (
    finish_contact_search_attempt_failure,
    process_claimed_contact_search,
    reconcile_terminal_contact_plans,
)
from .database import Database, database_from_env
from .job_queue import (
    claim_next_job,
    fail_job_attempt,
    heartbeat_job,
    lock_owned_running_job,
    record_worker_heartbeat,
    recover_stale_jobs,
    update_job_stage,
)
from .models import (
    BackgroundJob,
    ContactPlan,
    HuntRun,
    JobObservation,
    OpportunityScan,
    OpportunityScanSource,
)
from .opportunity_scan_worker import SCAN_JOB_KIND, process_claimed_opportunity_scan
from .production_runtime import validate_production_runtime
from .requests import HuntRequestPayload
from .run import run_hunt
from .security import DataKeyring, DecryptionError, EncryptedEnvelope, load_data_keyring
from .sources.registry import RegistryError


LOGGER = logging.getLogger(__name__)
DEFAULT_LEASE_SECONDS = 300
DEFAULT_IDLE_SLEEP_SECONDS = 2.0
DEFAULT_RETRY_DELAY_SECONDS = 0
DEFAULT_WORKER_HEARTBEAT_MAX_AGE_SECONDS = 90.0
DEFAULT_BUSY_HEARTBEAT_INTERVAL_SECONDS = 30.0
PRACTICAL_JOB_KINDS = frozenset(
    {"legacy_hunt", SCAN_JOB_KIND, CONTACT_SEARCH_JOB_KIND}
)
WORKER_STARTED_AT = datetime.now(timezone.utc)


@dataclass(frozen=True)
class WorkerResult:
    """Outcome of one worker poll/process cycle."""

    claimed: bool
    run_id: str | None = None
    status: str | None = None
    stage: str | None = None


class PracticalWorkerError(RuntimeError):
    """Sanitized practical-worker failure safe to surface in process logs."""


@dataclass(frozen=True)
class ClaimedPracticalHunt:
    """Detached identifiers for one Postgres claim."""

    job_id: str
    run_id: str
    lease_token: str


@dataclass(frozen=True)
class ClaimedPracticalScan:
    """Detached identifiers for one search-only opportunity scan claim."""

    job_id: str
    run_id: str
    lease_token: str


@dataclass(frozen=True)
class ClaimedPracticalContact:
    """Detached identifiers for one verified-contact discovery claim."""

    job_id: str
    run_id: str
    lease_token: str


def _env_bool(name: str, *, default: bool = False) -> bool:
    return env_bool(name, default=default)


def _is_production() -> bool:
    return is_production()


def _tracing_enabled() -> bool:
    return _env_bool("ENABLE_TRACING", default=_is_production())


def _worker_id() -> str:
    configured = os.getenv("JOB_HUNT_WORKER_ID", "").strip()
    if configured:
        return configured
    return f"{socket.gethostname()}-{os.getpid()}"


def run_worker_once(
    *,
    worker_id: str | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    retry_delay_seconds: int = DEFAULT_RETRY_DELAY_SECONDS,
    use_mocks: bool | None = None,
    enable_tracing: bool | None = None,
    durable_database: Database | None = None,
    practical_mode: bool | None = None,
) -> WorkerResult:
    """Claim and process one hunt from exactly one configured backend."""

    practical = practical_mode_enabled() if practical_mode is None else practical_mode
    worker = worker_id or _worker_id()
    resolved_use_mocks = (
        _env_bool("USE_MOCKS", default=False) if use_mocks is None else use_mocks
    )
    resolved_tracing = _tracing_enabled() if enable_tracing is None else enable_tracing
    validate_production_runtime(
        practical_mode=practical,
        use_mocks=resolved_use_mocks,
        enable_tracing=resolved_tracing,
    )

    if practical:
        database = durable_database or database_from_env(required=True)
        if database is None:  # pragma: no cover - required=True is fail-closed.
            raise RuntimeError("practical worker requires DATABASE_URL")
        if not database.migrations_current():
            raise RuntimeError("practical worker requires current database migrations")
        try:
            return _run_practical_worker_once(
                database,
                worker_id=worker,
                lease_seconds=lease_seconds,
                retry_delay_seconds=retry_delay_seconds,
                use_mocks=resolved_use_mocks,
                enable_tracing=resolved_tracing,
            )
        except PracticalWorkerError:
            _clear_practical_current_job(
                database,
                worker_id=worker,
                available=False,
            )
            raise
        except Exception as exc:  # noqa: BLE001 - suppress DB/ciphertext parameters.
            LOGGER.error(
                "practical worker cycle failed worker_id=%s error_type=%s",
                worker,
                type(exc).__name__,
            )
            _clear_practical_current_job(
                database,
                worker_id=worker,
                available=False,
            )
            raise PracticalWorkerError("practical worker cycle failed") from None

    return _run_legacy_worker_once(
        worker_id=worker,
        lease_seconds=lease_seconds,
        retry_delay_seconds=retry_delay_seconds,
        use_mocks=resolved_use_mocks,
        enable_tracing=resolved_tracing,
    )


def _run_legacy_worker_once(
    *,
    worker_id: str,
    lease_seconds: int,
    retry_delay_seconds: int,
    use_mocks: bool,
    enable_tracing: bool,
) -> WorkerResult:
    """Process only the SQLite compatibility queue."""

    persistence.init_db()
    lease_token = uuid4().hex
    state = persistence.claim_next_run(
        worker_id=worker_id,
        lease_token=lease_token,
        lease_seconds=lease_seconds,
    )
    if state is None:
        return WorkerResult(claimed=False)

    result_state = process_claimed_run(
        state,
        worker_id=worker_id,
        lease_token=lease_token,
        lease_seconds=lease_seconds,
        retry_delay_seconds=retry_delay_seconds,
        use_mocks=use_mocks,
        enable_tracing=enable_tracing,
    )
    return WorkerResult(
        claimed=True,
        run_id=state.run_id,
        status=result_state.status if result_state is not None else None,
        stage=result_state.stage if result_state is not None else None,
    )


def _run_practical_worker_once(
    database: Database,
    *,
    worker_id: str,
    lease_seconds: int,
    retry_delay_seconds: int,
    use_mocks: bool,
    enable_tracing: bool,
) -> WorkerResult:
    """Claim and dispatch one supported durable practical job."""

    keyring = load_data_keyring(production=_is_production())
    lease_token = uuid4().hex
    hunt_claim: ClaimedPracticalHunt | None = None
    scan_claim: ClaimedPracticalScan | None = None
    contact_claim: ClaimedPracticalContact | None = None
    with database.session() as session:
        current = datetime.now(timezone.utc)
        recover_stale_jobs(session, now=current)
        _reconcile_terminal_opportunity_scans(session, now=current)
        reconcile_terminal_contact_plans(session, now=current)
        job = claim_next_job(
            session,
            worker_id=worker_id,
            lease_token=lease_token,
            lease_seconds=lease_seconds,
            kinds=PRACTICAL_JOB_KINDS,
        )
        if job is None:
            _record_worker_state(session, worker_id=worker_id, current_job_id=None)
            return WorkerResult(claimed=False)

        _record_worker_state(session, worker_id=worker_id, current_job_id=job.id)
        if job.kind == SCAN_JOB_KIND:
            scan_id = _opportunity_scan_id(job)
            scan = session.get(OpportunityScan, scan_id) if scan_id is not None else None
            if (
                scan is None
                or scan.background_job_id != job.id
                or scan.owner_id != job.owner_id
            ):
                # If only the payload was corrupted, fail the matching
                # same-owner domain row as well. Never cross owner scope or
                # mutate a scan linked to another queue record.
                subject_scan = (
                    session.scalar(
                        select(OpportunityScan)
                        .where(
                            OpportunityScan.owner_id == job.owner_id,
                            OpportunityScan.id == job.subject_id,
                            OpportunityScan.background_job_id == job.id,
                        )
                        .with_for_update()
                    )
                    if job.subject_type == "opportunity_scan" and job.subject_id
                    else None
                )
                if subject_scan is not None:
                    current = datetime.now(timezone.utc)
                    subject_scan.status = "failed"
                    subject_scan.stage = "complete"
                    subject_scan.started_at = subject_scan.started_at or current
                    subject_scan.finalized_at = current
                    subject_scan.updated_at = current
                    subject_scan.version += 1
                failed = fail_job_attempt(
                    session,
                    job.id,
                    worker_id=worker_id,
                    lease_token=lease_token,
                    error_code="InvalidScanReference",
                    terminal=True,
                )
                if subject_scan is not None:
                    _reconcile_terminal_opportunity_scans(session, now=current)
                _record_worker_state(session, worker_id=worker_id, current_job_id=None)
                return WorkerResult(
                    claimed=True,
                    status=failed.status if failed is not None else None,
                    stage=failed.stage if failed is not None else None,
                )
            scan_claim = ClaimedPracticalScan(
                job_id=job.id,
                run_id=scan.id,
                lease_token=lease_token,
            )
        elif job.kind == CONTACT_SEARCH_JOB_KIND:
            plan_id = _contact_plan_id(job)
            plan = session.get(ContactPlan, plan_id) if plan_id is not None else None
            if (
                plan is None
                or plan.background_job_id != job.id
                or plan.owner_id != job.owner_id
            ):
                subject_plan = (
                    session.scalar(
                        select(ContactPlan)
                        .where(
                            ContactPlan.owner_id == job.owner_id,
                            ContactPlan.id == job.subject_id,
                            ContactPlan.background_job_id == job.id,
                        )
                        .with_for_update()
                    )
                    if job.subject_type == "contact_plan" and job.subject_id
                    else None
                )
                if subject_plan is not None and subject_plan.status in {
                    "queued",
                    "running",
                }:
                    current = datetime.now(timezone.utc)
                    subject_plan.status = "failed"
                    subject_plan.coverage_status = "pending"
                    subject_plan.exhausted = False
                    subject_plan.retryable = False
                    subject_plan.shortfall_reasons = []
                    subject_plan.error_code = "invalid_contact_search_reference"
                    subject_plan.started_at = subject_plan.started_at or current
                    subject_plan.finalized_at = current
                    subject_plan.updated_at = current
                    subject_plan.version += 1
                failed = fail_job_attempt(
                    session,
                    job.id,
                    worker_id=worker_id,
                    lease_token=lease_token,
                    error_code="InvalidContactReference",
                    terminal=True,
                )
                _record_worker_state(session, worker_id=worker_id, current_job_id=None)
                return WorkerResult(
                    claimed=True,
                    run_id=job.subject_id,
                    status=failed.status if failed is not None else None,
                    stage=failed.stage if failed is not None else None,
                )
            contact_claim = ClaimedPracticalContact(
                job_id=job.id,
                run_id=plan.id,
                lease_token=lease_token,
            )
        else:
            run_id = _hunt_run_id(job)
            hunt = session.get(HuntRun, run_id) if run_id is not None else None
            if (
                hunt is None
                or hunt.background_job_id != job.id
                or hunt.owner_id != job.owner_id
            ):
                failed = fail_job_attempt(
                    session,
                    job.id,
                    worker_id=worker_id,
                    lease_token=lease_token,
                    error_code="InvalidHuntReference",
                    terminal=True,
                )
                _record_worker_state(session, worker_id=worker_id, current_job_id=None)
                return WorkerResult(
                    claimed=True,
                    status=failed.status if failed is not None else None,
                    stage=failed.stage if failed is not None else None,
                )
            assert run_id is not None
            hunt_claim = ClaimedPracticalHunt(
                job_id=job.id,
                run_id=run_id,
                lease_token=lease_token,
            )

    if scan_claim is not None:
        return process_claimed_practical_scan(
            scan_claim,
            database=database,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            retry_delay_seconds=retry_delay_seconds,
            use_mocks=use_mocks,
        )
    if contact_claim is not None:
        return process_claimed_practical_contact(
            contact_claim,
            database=database,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            retry_delay_seconds=retry_delay_seconds,
            use_mocks=use_mocks,
        )
    assert hunt_claim is not None
    return process_claimed_practical_hunt(
        hunt_claim,
        database=database,
        keyring=keyring,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        retry_delay_seconds=retry_delay_seconds,
        use_mocks=use_mocks,
        enable_tracing=enable_tracing,
    )


def _reconcile_terminal_opportunity_scans(
    session: Session,
    *,
    now: datetime | None = None,
) -> int:
    """Make scan rows agree with terminal queue jobs after lease recovery."""

    current = now or datetime.now(timezone.utc)
    terminal_jobs = list(
        session.execute(
            select(OpportunityScan, BackgroundJob)
            .join(
                BackgroundJob,
                OpportunityScan.background_job_id == BackgroundJob.id,
            )
            .where(
                OpportunityScan.owner_id == BackgroundJob.owner_id,
                BackgroundJob.kind == SCAN_JOB_KIND,
                BackgroundJob.status.in_({"cancelled", "dead_letter"}),
                OpportunityScan.status.in_(
                    {"queued", "running", "failed", "cancelled"}
                ),
            )
            .with_for_update()
        ).all()
    )
    reconciled = 0
    for scan, job in terminal_jobs:
        target_status = "cancelled" if job.status == "cancelled" else "failed"
        target_source_status = "cancelled" if job.status == "cancelled" else "failed"
        sources = list(
            session.scalars(
                select(OpportunityScanSource)
                .where(
                    OpportunityScanSource.owner_id == scan.owner_id,
                    OpportunityScanSource.opportunity_scan_id == scan.id,
                )
                .with_for_update()
            )
        )
        changed = False
        for source in sources:
            if source.status in {"succeeded", "failed", "cancelled"}:
                continue
            source.status = target_source_status
            if target_source_status == "failed":
                # A recovered dead-letter represents an attempted source run,
                # even when the process died before it could mark the source
                # running. Keep the source timestamp/error invariants truthful.
                source.started_at = source.started_at or current
                source.error_code = "scan_interrupted"
            else:
                # Cancelled source rows intentionally carry no failure code.
                source.error_code = None
            source.warning_codes = sorted(
                set([*source.warning_codes, "scan_interrupted"])
            )
            source.completed_at = current
            source.updated_at = current
            source.version += 1
            changed = True

        successful = sum(source.status == "succeeded" for source in sources)
        failed = sum(source.status in {"failed", "cancelled"} for source in sources)
        observed = int(
            session.scalar(
                select(func.count(JobObservation.id)).where(
                    JobObservation.owner_id == scan.owner_id,
                    JobObservation.opportunity_scan_id == scan.id,
                )
            )
            or 0
        )
        expected_values = {
            "status": target_status,
            "stage": "complete",
            "source_count": len(sources),
            "terminal_source_count": len(sources),
            "successful_source_count": successful,
            "failed_source_count": failed,
            "observed_count": observed,
        }
        if any(getattr(scan, field) != value for field, value in expected_values.items()):
            changed = True
        for field, value in expected_values.items():
            setattr(scan, field, value)
        if scan.started_at is None:
            scan.started_at = job.started_at or current
            changed = True
        if scan.finalized_at is None:
            scan.finalized_at = current
            changed = True
        if changed:
            scan.updated_at = current
            scan.version += 1
            reconciled += 1

    session.flush()
    return reconciled


def process_claimed_practical_hunt(
    claim: ClaimedPracticalHunt,
    *,
    database: Database,
    keyring: DataKeyring,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    retry_delay_seconds: int = DEFAULT_RETRY_DELAY_SECONDS,
    use_mocks: bool = False,
    enable_tracing: bool = False,
) -> WorkerResult:
    """Process a detached Postgres claim without holding a long transaction."""

    # Imported lazily so legacy-only installations never initialize the
    # practical repository path.
    from . import hunt_repository

    heartbeat = _PracticalLeaseHeartbeat(
        database,
        claim,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )
    heartbeat.start()
    fatal_error = False
    try:
        if not _update_practical_stage(
            database,
            claim,
            worker_id=worker_id,
            stage="decrypting_request",
            checkpoint="decrypting_request",
        ):
            return _practical_worker_result(database, claim)

        try:
            with database.session() as session:
                payload_json = hunt_repository.load_hunt_request_for_worker(
                    session,
                    hunt_run_id=claim.run_id,
                    worker_id=worker_id,
                    lease_token=claim.lease_token,
                    keyring=keyring,
                )
                if payload_json is None:
                    recovered = (
                        hunt_repository.complete_existing_hunt_result_for_worker(
                            session,
                            hunt_run_id=claim.run_id,
                            worker_id=worker_id,
                            lease_token=claim.lease_token,
                            keyring=keyring,
                        )
                    )
                    if recovered is not None:
                        job = session.get(BackgroundJob, claim.job_id)
                        _record_worker_state(
                            session,
                            worker_id=worker_id,
                            current_job_id=None,
                        )
                        return _result_from_job(job, run_id=claim.run_id)
            if payload_json is None:
                return _finish_practical_failure(
                    database,
                    claim,
                    worker_id=worker_id,
                    error_code="RequestUnavailable",
                    retry_delay_seconds=retry_delay_seconds,
                    terminal=True,
                )
            request = HuntRequestPayload.model_validate_json(payload_json)
        except (DecryptionError, ValidationError) as exc:
            return _finish_practical_failure(
                database,
                claim,
                worker_id=worker_id,
                error_code=type(exc).__name__,
                retry_delay_seconds=retry_delay_seconds,
                terminal=True,
            )

        if heartbeat.lease_lost or not _renew_practical_lease(
            database,
            claim,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        ):
            heartbeat.mark_lease_lost()
            return _practical_worker_result(database, claim)

        if not _update_practical_stage(
            database,
            claim,
            worker_id=worker_id,
            stage="run_hunt",
            checkpoint="run_hunt",
        ):
            return _practical_worker_result(database, claim)

        try:
            result = run_hunt(
                resume_text=request.resume_text,
                criteria=request.criteria,
                run_id=claim.run_id,
                use_mocks=use_mocks,
                use_self_rag=request.use_self_rag,
                enable_tracing=enable_tracing,
                pack=request.pack,
            )
        except RegistryError as exc:
            return _finish_practical_failure(
                database,
                claim,
                worker_id=worker_id,
                error_code=type(exc).__name__,
                retry_delay_seconds=retry_delay_seconds,
                terminal=True,
            )
        except Exception as exc:  # noqa: BLE001 - the queue stores only the type code.
            LOGGER.warning(
                "hunt worker attempt failed for run_id=%s error_type=%s",
                claim.run_id,
                type(exc).__name__,
            )
            return _finish_practical_failure(
                database,
                claim,
                worker_id=worker_id,
                error_code=type(exc).__name__,
                retry_delay_seconds=retry_delay_seconds,
                terminal=False,
            )

        if heartbeat.lease_lost or not _update_practical_stage(
            database,
            claim,
            worker_id=worker_id,
            stage="finalizing",
            checkpoint="finalizing",
        ):
            return _practical_worker_result(database, claim)

        with database.session() as session:
            hunt_repository.store_hunt_success(
                session,
                hunt_result=result,
                worker_id=worker_id,
                lease_token=claim.lease_token,
                keyring=keyring,
            )
            job = session.get(BackgroundJob, claim.job_id)
            _record_worker_state(session, worker_id=worker_id, current_job_id=None)
            return _result_from_job(job, run_id=claim.run_id)
    except PracticalWorkerError:
        fatal_error = True
        raise
    except Exception as exc:  # noqa: BLE001 - never surface SQL/ciphertext details.
        fatal_error = True
        LOGGER.error(
            "practical worker processing failed run_id=%s error_type=%s",
            claim.run_id,
            type(exc).__name__,
        )
        raise PracticalWorkerError("practical worker processing failed") from None
    finally:
        heartbeat.stop()
        _clear_practical_current_job(
            database,
            worker_id=worker_id,
            available=not fatal_error,
        )


def process_claimed_practical_scan(
    claim: ClaimedPracticalScan,
    *,
    database: Database,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    retry_delay_seconds: int = DEFAULT_RETRY_DELAY_SECONDS,
    use_mocks: bool = False,
) -> WorkerResult:
    """Run a provider-free saved-search scan under a renewable queue lease."""

    heartbeat = _PracticalLeaseHeartbeat(
        database,
        claim,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )
    heartbeat.start()
    fatal_error = False
    try:
        process_claimed_opportunity_scan(
            claim,
            database=database,
            worker_id=worker_id,
            use_mocks=use_mocks,
        )
        return _practical_worker_result(database, claim)
    except Exception as exc:  # noqa: BLE001 - queue records a fixed safe code.
        LOGGER.warning(
            "opportunity scan worker attempt failed scan_id=%s error_type=%s",
            claim.run_id,
            type(exc).__name__,
        )
        try:
            return _finish_practical_scan_attempt_failure(
                database,
                claim,
                worker_id=worker_id,
                retry_delay_seconds=retry_delay_seconds,
            )
        except Exception as failure_exc:  # noqa: BLE001 - a DB outage invalidates the lease.
            fatal_error = True
            LOGGER.error(
                "opportunity scan failure transition failed scan_id=%s error_type=%s",
                claim.run_id,
                type(failure_exc).__name__,
            )
            raise PracticalWorkerError("practical scan worker failed") from None
    finally:
        heartbeat.stop()
        _clear_practical_current_job(
            database,
            worker_id=worker_id,
            available=not fatal_error,
        )


def process_claimed_practical_contact(
    claim: ClaimedPracticalContact,
    *,
    database: Database,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    retry_delay_seconds: int = DEFAULT_RETRY_DELAY_SECONDS,
    use_mocks: bool = False,
) -> WorkerResult:
    """Run public-profile discovery under a renewable durable queue lease."""

    heartbeat = _PracticalLeaseHeartbeat(
        database,
        claim,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )
    heartbeat.start()
    fatal_error = False
    try:
        try:
            provider = (
                MockContactSearchProvider()
                if use_mocks
                else SerpAPIContactProvider.from_env()
            )
        except ContactProviderConfigurationError:
            finish_contact_search_attempt_failure(
                database,
                claim,
                worker_id=worker_id,
                error_code="provider_configuration_failure",
                retryable=False,
                terminal=True,
                retry_delay_seconds=retry_delay_seconds,
            )
        else:
            process_claimed_contact_search(
                claim,
                database=database,
                worker_id=worker_id,
                provider=provider,
                retry_delay_seconds=retry_delay_seconds,
            )
        return _practical_worker_result(database, claim)
    except Exception as exc:  # noqa: BLE001 - persist only a fixed safe code.
        LOGGER.warning(
            "contact search worker attempt failed plan_id=%s error_type=%s",
            claim.run_id,
            type(exc).__name__,
        )
        try:
            finish_contact_search_attempt_failure(
                database,
                claim,
                worker_id=worker_id,
                error_code="contact_search_processing_failed",
                retryable=True,
                terminal=False,
                retry_delay_seconds=retry_delay_seconds,
            )
            return _practical_worker_result(database, claim)
        except Exception as failure_exc:  # noqa: BLE001 - DB loss invalidates lease.
            fatal_error = True
            LOGGER.error(
                "contact search failure transition failed plan_id=%s error_type=%s",
                claim.run_id,
                type(failure_exc).__name__,
            )
            raise PracticalWorkerError("practical contact worker failed") from None
    finally:
        heartbeat.stop()
        _clear_practical_current_job(
            database,
            worker_id=worker_id,
            available=not fatal_error,
        )


def _finish_practical_scan_attempt_failure(
    database: Database,
    claim: ClaimedPracticalScan,
    *,
    worker_id: str,
    retry_delay_seconds: int,
) -> WorkerResult:
    """Reset an interrupted scan for retry or finalize it after exhaustion."""

    current = datetime.now(timezone.utc)
    with database.session() as session:
        owned = lock_owned_running_job(
            session,
            claim.job_id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
        )
        if owned is None:
            _record_worker_state(session, worker_id=worker_id, current_job_id=None)
            return _result_from_job(
                session.get(BackgroundJob, claim.job_id),
                run_id=claim.run_id,
            )
        failed = fail_job_attempt(
            session,
            owned.id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
            error_code="ScanProcessingFailed",
            retry_delay_seconds=retry_delay_seconds,
            terminal=False,
            now=current,
        )
        scan = session.scalar(
            select(OpportunityScan)
            .where(
                OpportunityScan.owner_id == owned.owner_id,
                OpportunityScan.id == claim.run_id,
            )
            .with_for_update()
        )
        if scan is not None and failed is not None:
            terminal = failed.status in {"cancelled", "dead_letter"}
            scan.status = (
                "cancelled"
                if failed.status == "cancelled"
                else "failed" if terminal else "queued"
            )
            scan.stage = "complete" if terminal else "queued"
            scan.finalized_at = current if terminal else None
            scan.updated_at = current
            scan.version += 1
            sources = list(
                session.scalars(
                    select(OpportunityScanSource)
                    .where(
                        OpportunityScanSource.owner_id == owned.owner_id,
                        OpportunityScanSource.opportunity_scan_id == scan.id,
                        OpportunityScanSource.status == "running",
                    )
                    .with_for_update()
                )
            )
            for source in sources:
                if terminal:
                    source.status = "cancelled" if failed.status == "cancelled" else "failed"
                    source.error_code = "scan_processing_failed"
                    source.warning_codes = sorted(
                        set([*source.warning_codes, "scan_processing_failed"])
                    )
                    source.completed_at = current
                else:
                    source.status = "pending"
                    source.started_at = None
                    source.completed_at = None
                    source.error_code = None
                    source.warning_codes = sorted(
                        set([*source.warning_codes, "scan_retrying"])
                    )
                source.updated_at = current
                source.version += 1
        _record_worker_state(session, worker_id=worker_id, current_job_id=None)
        return _result_from_job(failed, run_id=claim.run_id)


def _hunt_run_id(job: BackgroundJob) -> str | None:
    payload = job.payload if isinstance(job.payload, dict) else {}
    run_id = payload.get("hunt_run_id")
    if not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 128:
        return None
    normalized = run_id.strip()
    if job.subject_type != "hunt_run" or job.subject_id != normalized:
        return None
    return normalized


def _opportunity_scan_id(job: BackgroundJob) -> str | None:
    payload = job.payload if isinstance(job.payload, dict) else {}
    scan_id = payload.get("opportunity_scan_id")
    if not isinstance(scan_id, str) or not scan_id.strip() or len(scan_id) > 128:
        return None
    normalized = scan_id.strip()
    if job.subject_type != "opportunity_scan" or job.subject_id != normalized:
        return None
    return normalized


def _contact_plan_id(job: BackgroundJob) -> str | None:
    payload = job.payload if isinstance(job.payload, dict) else {}
    plan_id = payload.get("contact_plan_id")
    if not isinstance(plan_id, str) or not plan_id.strip() or len(plan_id) > 128:
        return None
    normalized = plan_id.strip()
    if job.subject_type != "contact_plan" or job.subject_id != normalized:
        return None
    return normalized


def _finish_practical_failure(
    database: Database,
    claim: ClaimedPracticalHunt,
    *,
    worker_id: str,
    error_code: str,
    retry_delay_seconds: int,
    terminal: bool,
) -> WorkerResult:
    """Apply one guarded failure transition without leaking exception text."""

    from . import hunt_repository

    with database.session() as session:
        owned = lock_owned_running_job(
            session,
            claim.job_id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
        )
        if owned is None:
            _record_worker_state(session, worker_id=worker_id, current_job_id=None)
            return _result_from_job(
                session.get(BackgroundJob, claim.job_id),
                run_id=claim.run_id,
            )

        # A running cancellation always wins and must erase its retryable
        # private request before the generic transition releases the lease.
        if owned.cancel_requested_at is not None:
            hunt_repository.clear_hunt_request_for_worker(
                session,
                hunt_run_id=claim.run_id,
                worker_id=worker_id,
                lease_token=claim.lease_token,
            )
        failed = fail_job_attempt(
            session,
            claim.job_id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
            error_code=error_code,
            retry_delay_seconds=retry_delay_seconds,
            terminal=terminal,
        )
        _record_worker_state(session, worker_id=worker_id, current_job_id=None)
        return _result_from_job(failed, run_id=claim.run_id)


def _update_practical_stage(
    database: Database,
    claim: ClaimedPracticalHunt,
    *,
    worker_id: str,
    stage: str,
    checkpoint: str,
) -> bool:
    with database.session() as session:
        return update_job_stage(
            session,
            claim.job_id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
            stage=stage,
            checkpoint=checkpoint,
        )


def _renew_practical_lease(
    database: Database,
    claim: ClaimedPracticalHunt | ClaimedPracticalScan | ClaimedPracticalContact,
    *,
    worker_id: str,
    lease_seconds: int,
) -> bool:
    with database.session() as session:
        renewed = heartbeat_job(
            session,
            claim.job_id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
            lease_seconds=lease_seconds,
        )
        _record_worker_state(
            session,
            worker_id=worker_id,
            current_job_id=claim.job_id if renewed else None,
        )
        return renewed


def _record_worker_state(
    session: Session,
    *,
    worker_id: str,
    current_job_id: str | None,
    supported_kinds: frozenset[str] = PRACTICAL_JOB_KINDS,
) -> None:
    record_worker_heartbeat(
        session,
        worker_id=worker_id,
        supported_kinds=supported_kinds,
        current_job_id=current_job_id,
        build_version=os.getenv("RENDER_GIT_COMMIT") or os.getenv("APP_VERSION"),
        started_at=WORKER_STARTED_AT,
    )


def _practical_worker_result(
    database: Database,
    claim: ClaimedPracticalHunt | ClaimedPracticalScan | ClaimedPracticalContact,
) -> WorkerResult:
    with database.session() as session:
        return _result_from_job(
            session.get(BackgroundJob, claim.job_id),
            run_id=claim.run_id,
        )


def _result_from_job(
    job: BackgroundJob | None,
    *,
    run_id: str,
) -> WorkerResult:
    return WorkerResult(
        claimed=True,
        run_id=run_id,
        status=job.status if job is not None else None,
        stage=job.stage if job is not None else None,
    )


def _clear_practical_current_job(
    database: Database,
    *,
    worker_id: str,
    available: bool = True,
) -> None:
    try:
        with database.session() as session:
            _record_worker_state(
                session,
                worker_id=worker_id,
                current_job_id=None,
                supported_kinds=PRACTICAL_JOB_KINDS if available else frozenset(),
            )
    except Exception as exc:  # noqa: BLE001 - a lost DB already invalidates the lease.
        LOGGER.warning(
            "worker heartbeat clear failed worker_id=%s error_type=%s",
            worker_id,
            type(exc).__name__,
        )


class _PracticalLeaseHeartbeat:
    """Renew a generic Postgres lease and its readiness record together."""

    def __init__(
        self,
        database: Database,
        claim: ClaimedPracticalHunt | ClaimedPracticalScan | ClaimedPracticalContact,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> None:
        self._database = database
        self._claim = claim
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._stop = threading.Event()
        self._lease_lost = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    @property
    def lease_lost(self) -> bool:
        return self._lease_lost.is_set()

    def mark_lease_lost(self) -> None:
        self._lease_lost.set()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _loop(self) -> None:
        interval = _busy_heartbeat_interval_seconds(self._lease_seconds)
        while not self._stop.wait(interval):
            try:
                renewed = _renew_practical_lease(
                    self._database,
                    self._claim,
                    worker_id=self._worker_id,
                    lease_seconds=self._lease_seconds,
                )
            except Exception as exc:  # noqa: BLE001 - continuing risks stale writes.
                LOGGER.warning(
                    "practical lease heartbeat failed job_id=%s error_type=%s",
                    self._claim.job_id,
                    type(exc).__name__,
                )
                self._lease_lost.set()
                return
            if not renewed:
                self._lease_lost.set()
                return


def process_claimed_run(
    state: persistence.RunQueueState,
    *,
    worker_id: str,
    lease_token: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    retry_delay_seconds: int = DEFAULT_RETRY_DELAY_SECONDS,
    use_mocks: bool = False,
    enable_tracing: bool = False,
    durable_database: Database | None = None,
) -> persistence.RunQueueState | None:
    """Run a claimed SQLite job if this worker still owns the lease."""

    # Kept as a compatibility keyword for callers from the bridge release. A
    # legacy worker deliberately never opens the durable database.
    _ = durable_database
    heartbeat = _LeaseHeartbeat(
        state.run_id,
        worker_id=worker_id,
        lease_token=lease_token,
        lease_seconds=lease_seconds,
    )
    heartbeat.start()
    try:
        persistence.update_run_stage(
            state.run_id,
            worker_id=worker_id,
            lease_token=lease_token,
            stage="decrypting_request",
            checkpoint="decrypting_request",
        )
        encrypted = persistence.load_encrypted_request(state.run_id)
        if encrypted is None:
            return persistence.mark_run_failed(
                state.run_id,
                worker_id=worker_id,
                lease_token=lease_token,
                error="missing encrypted request",
            )

        key_id, ciphertext = encrypted
        try:
            payload_json = load_data_keyring(production=_is_production()).decrypt(
                EncryptedEnvelope(key_id=key_id, ciphertext=ciphertext)
            )
            request = HuntRequestPayload.model_validate_json(payload_json)
        except (DecryptionError, ValidationError) as exc:
            return persistence.mark_run_failed(
                state.run_id,
                worker_id=worker_id,
                lease_token=lease_token,
                error=type(exc).__name__,
            )

        if not persistence.heartbeat_run(
            state.run_id,
            worker_id=worker_id,
            lease_token=lease_token,
            lease_seconds=lease_seconds,
        ):
            return persistence.get_run_state(state.run_id)

        persistence.update_run_stage(
            state.run_id,
            worker_id=worker_id,
            lease_token=lease_token,
            stage="run_hunt",
            checkpoint="run_hunt",
        )
        try:
            result = run_hunt(
                resume_text=request.resume_text,
                criteria=request.criteria,
                run_id=state.run_id,
                use_mocks=use_mocks,
                use_self_rag=request.use_self_rag,
                enable_tracing=enable_tracing,
                pack=request.pack,
            )
        except RegistryError as exc:
            return persistence.mark_run_failed(
                state.run_id,
                worker_id=worker_id,
                lease_token=lease_token,
                error=type(exc).__name__,
            )
        except Exception as exc:  # noqa: BLE001 - retry boundary sanitizes details.
            LOGGER.warning(
                "hunt worker attempt failed for run_id=%s error_type=%s",
                state.run_id,
                type(exc).__name__,
            )
            return persistence.mark_run_attempt_failed(
                state.run_id,
                worker_id=worker_id,
                lease_token=lease_token,
                error=type(exc).__name__,
                retry_delay_seconds=retry_delay_seconds,
            )

        persistence.update_run_stage(
            state.run_id,
            worker_id=worker_id,
            lease_token=lease_token,
            stage="finalizing",
            checkpoint="finalizing",
        )
        return persistence.complete_run_with_result(
            result,
            worker_id=worker_id,
            lease_token=lease_token,
        )
    finally:
        heartbeat.stop()


def run_worker_loop(
    *,
    worker_id: str | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    idle_sleep_seconds: float = DEFAULT_IDLE_SLEEP_SECONDS,
    retry_delay_seconds: int = DEFAULT_RETRY_DELAY_SECONDS,
) -> None:
    """Continuously process queued hunts until the process is interrupted."""

    practical = practical_mode_enabled()
    durable_database = database_from_env(required=True) if practical else None
    if practical and durable_database is not None and not durable_database.migrations_current():
        raise RuntimeError("practical worker requires current database migrations")
    while True:
        result = run_worker_once(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            retry_delay_seconds=retry_delay_seconds,
            durable_database=durable_database,
            practical_mode=practical,
        )
        if not result.claimed:
            time.sleep(idle_sleep_seconds)


class _LeaseHeartbeat:
    """SQLite compatibility heartbeat for monolithic ``run_hunt`` calls."""

    def __init__(
        self,
        run_id: str,
        *,
        worker_id: str,
        lease_token: str,
        lease_seconds: int,
        durable_database: Database | None = None,
    ) -> None:
        # Compatibility-only argument; SQLite mode never reports a Postgres
        # capability heartbeat.
        _ = durable_database
        self._run_id = run_id
        self._worker_id = worker_id
        self._lease_token = lease_token
        self._lease_seconds = lease_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _loop(self) -> None:
        interval = _busy_heartbeat_interval_seconds(self._lease_seconds)
        while not self._stop.wait(interval):
            if not persistence.heartbeat_run(
                self._run_id,
                worker_id=self._worker_id,
                lease_token=self._lease_token,
                lease_seconds=self._lease_seconds,
            ):
                return


def _busy_heartbeat_interval_seconds(lease_seconds: int) -> float:
    """Renew long-running work well inside lease/readiness thresholds."""

    raw_max_age = os.getenv(
        "JOB_HUNT_WORKER_HEARTBEAT_MAX_AGE_SECONDS",
        str(DEFAULT_WORKER_HEARTBEAT_MAX_AGE_SECONDS),
    ).strip()
    try:
        max_age_seconds = max(1.0, float(raw_max_age))
    except ValueError:
        max_age_seconds = DEFAULT_WORKER_HEARTBEAT_MAX_AGE_SECONDS
    return min(
        max(0.25, lease_seconds / 3),
        DEFAULT_BUSY_HEARTBEAT_INTERVAL_SECONDS,
        max(0.25, max_age_seconds / 3),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process queued job-hunt runs.")
    parser.add_argument("--once", action="store_true", help="Process at most one run.")
    parser.add_argument("--worker-id", help="Stable worker id for leases/audit.")
    parser.add_argument("--lease-seconds", type=int, default=DEFAULT_LEASE_SECONDS)
    parser.add_argument("--idle-sleep-seconds", type=float, default=DEFAULT_IDLE_SLEEP_SECONDS)
    parser.add_argument("--retry-delay-seconds", type=int, default=DEFAULT_RETRY_DELAY_SECONDS)
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    args = parse_args()
    if args.once:
        result = run_worker_once(
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
            retry_delay_seconds=args.retry_delay_seconds,
        )
        print(result)
        return
    run_worker_loop(
        worker_id=args.worker_id,
        lease_seconds=args.lease_seconds,
        idle_sleep_seconds=args.idle_sleep_seconds,
        retry_delay_seconds=args.retry_delay_seconds,
    )


if __name__ == "__main__":
    main()
