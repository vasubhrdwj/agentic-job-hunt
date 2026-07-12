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
from sqlalchemy.orm import Session

from . import persistence
from .config import env_bool, is_production, practical_mode_enabled
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
from .models import BackgroundJob, HuntRun
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
PRACTICAL_JOB_KINDS = frozenset({"legacy_hunt"})
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
    """Process only generic ``legacy_hunt`` jobs from the durable queue."""

    keyring = load_data_keyring(production=_is_production())
    lease_token = uuid4().hex
    with database.session() as session:
        recover_stale_jobs(session)
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

        run_id = _hunt_run_id(job)
        _record_worker_state(session, worker_id=worker_id, current_job_id=job.id)
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

        claim = ClaimedPracticalHunt(
            job_id=job.id,
            run_id=run_id,
            lease_token=lease_token,
        )

    return process_claimed_practical_hunt(
        claim,
        database=database,
        keyring=keyring,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        retry_delay_seconds=retry_delay_seconds,
        use_mocks=use_mocks,
        enable_tracing=enable_tracing,
    )


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


def _hunt_run_id(job: BackgroundJob) -> str | None:
    payload = job.payload if isinstance(job.payload, dict) else {}
    run_id = payload.get("hunt_run_id")
    if not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 128:
        return None
    normalized = run_id.strip()
    if job.subject_type != "hunt_run" or job.subject_id != normalized:
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
    claim: ClaimedPracticalHunt,
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
    claim: ClaimedPracticalHunt,
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
        claim: ClaimedPracticalHunt,
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
