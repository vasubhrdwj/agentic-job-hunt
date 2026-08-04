"""Optional interactive worker hosted inside the web process.

The embedded worker is a free-tier bridge for the private, user-triggered
product. A request wakes the web service and the same process drains durable
scan jobs while the service remains alive. Queue leases keep interrupted work
recoverable after a free-instance restart.

Legacy hunts are intentionally excluded. Contact discovery is advertised only
when its live provider and the production provider safeguards are configured;
otherwise the worker remains provider-free and continues serving role scans.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from dataclasses import dataclass, field

from .config import env_bool, is_production
from .contact_discovery import ContactProviderConfigurationError
from .contact_providers import SerpAPIContactProvider
from .contact_search_repository import CONTACT_SEARCH_JOB_KIND
from .database import database_from_env
from .opportunity_fit_worker import (
    DEFAULT_FIT_BACKFILL_BATCH_SIZE,
    FIT_EVALUATION_JOB_KIND,
    OpportunityFitBackfillBatch,
    enqueue_missing_opportunity_fit_evaluations,
    fit_evaluation_jobs_enabled,
)
from .opportunity_scan_worker import SCAN_JOB_KIND
from .production_runtime import validate_contact_search_runtime
from .scheduled_scan_repository import (
    DEFAULT_SCHEDULED_SCAN_BATCH_SIZE,
    ScheduledScanBatch,
    enqueue_due_saved_search_scans,
)
from .worker import WORKER_KINDS_ENV, resolve_practical_job_kinds, run_worker_once


LOGGER = logging.getLogger(__name__)
EMBEDDED_SCAN_WORKER_ENV = "ENABLE_EMBEDDED_SCAN_WORKER"
DEFAULT_IDLE_SLEEP_SECONDS = 1.0
DEFAULT_ERROR_SLEEP_SECONDS = 5.0
DEFAULT_SCHEDULER_INTERVAL_SECONDS = 60.0


def embedded_scan_worker_enabled() -> bool:
    return os.getenv(EMBEDDED_SCAN_WORKER_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@dataclass
class EmbeddedScanWorker:
    """Own one daemon thread for interactive role and configured contact work."""

    worker_id: str = field(default_factory=lambda: _worker_id())
    idle_sleep_seconds: float = DEFAULT_IDLE_SLEEP_SECONDS
    error_sleep_seconds: float = DEFAULT_ERROR_SLEEP_SECONDS
    scheduler_interval_seconds: float = DEFAULT_SCHEDULER_INTERVAL_SECONDS
    scheduler_batch_size: int = DEFAULT_SCHEDULED_SCAN_BATCH_SIZE
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="embedded-scan-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout_seconds: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, timeout_seconds))

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        database = None
        try:
            database = database_from_env(required=True)
            if database is None:  # pragma: no cover - required=True is fail-closed.
                raise RuntimeError("embedded scan worker requires DATABASE_URL")
            supported_kinds = embedded_worker_job_kinds()
            next_scheduler_tick = 0.0
            while not self._stop.is_set():
                monotonic_now = time.monotonic()
                if monotonic_now >= next_scheduler_tick and supported_kinds & {
                    SCAN_JOB_KIND,
                    FIT_EVALUATION_JOB_KIND,
                }:
                    try:
                        if SCAN_JOB_KIND in supported_kinds:
                            scheduled = _run_scheduled_scan_tick(
                                database,
                                limit=self.scheduler_batch_size,
                            )
                            if scheduled.invalid_search_count:
                                LOGGER.warning(
                                    "embedded scan scheduler skipped invalid searches "
                                    "count=%s",
                                    scheduled.invalid_search_count,
                                )
                        if FIT_EVALUATION_JOB_KIND in supported_kinds:
                            _run_fit_backfill_tick(
                                database,
                                limit=DEFAULT_FIT_BACKFILL_BATCH_SIZE,
                            )
                    except Exception as exc:  # noqa: BLE001 - keep queue drain alive.
                        LOGGER.error(
                            "embedded scan scheduler cycle failed worker_id=%s error_type=%s",
                            self.worker_id,
                            type(exc).__name__,
                        )
                    finally:
                        next_scheduler_tick = time.monotonic() + max(
                            1.0,
                            self.scheduler_interval_seconds,
                        )
                try:
                    result = run_worker_once(
                        worker_id=self.worker_id,
                        durable_database=database,
                        practical_mode=True,
                        job_kinds=supported_kinds,
                    )
                    delay = 0.0 if result.claimed else self.idle_sleep_seconds
                except Exception as exc:  # noqa: BLE001 - logs omit secret values.
                    LOGGER.error(
                        "embedded scan worker cycle failed worker_id=%s error_type=%s",
                        self.worker_id,
                        type(exc).__name__,
                    )
                    delay = self.error_sleep_seconds
                if delay > 0 and self._stop.wait(delay):
                    break
        finally:
            if database is not None:
                database.dispose()


def _run_scheduled_scan_tick(database, *, limit: int) -> ScheduledScanBatch:
    with database.session() as session:
        return enqueue_due_saved_search_scans(session, limit=limit)


def _run_fit_backfill_tick(
    database,
    *,
    limit: int,
) -> OpportunityFitBackfillBatch:
    with database.session() as session:
        return enqueue_missing_opportunity_fit_evaluations(session, limit=limit)


def embedded_worker_job_kinds() -> frozenset[str]:
    """Resolve only the work this in-process bridge can truthfully complete."""

    configured_value = os.getenv(WORKER_KINDS_ENV)
    default_kinds = {SCAN_JOB_KIND}
    if fit_evaluation_jobs_enabled():
        default_kinds.add(FIT_EVALUATION_JOB_KIND)
    configured = resolve_practical_job_kinds(
        default_kinds if configured_value is None else configured_value
    )
    supported = {SCAN_JOB_KIND} if SCAN_JOB_KIND in configured else set()
    if FIT_EVALUATION_JOB_KIND in configured:
        # Even a temporarily unavailable provider is serviceable: the fit job
        # completes against the deterministic fallback and never blocks scans.
        supported.add(FIT_EVALUATION_JOB_KIND)
    if CONTACT_SEARCH_JOB_KIND in configured and _contact_search_runtime_ready():
        supported.add(CONTACT_SEARCH_JOB_KIND)
    if not supported:
        raise RuntimeError(
            "embedded worker must enable scan_saved_search, "
            "evaluate_opportunity_fit, or a configured discover_contacts capability"
        )
    return frozenset(supported)


def _contact_search_runtime_ready() -> bool:
    use_mocks = env_bool("USE_MOCKS", default=False)
    if use_mocks:
        return not is_production()
    try:
        SerpAPIContactProvider.from_env()
        validate_contact_search_runtime(
            practical_mode=True,
            use_mocks=False,
        )
    except (ContactProviderConfigurationError, RuntimeError):
        return False
    return True


def _worker_id() -> str:
    configured = os.getenv("JOB_HUNT_EMBEDDED_WORKER_ID", "").strip()
    if configured:
        return configured
    return f"embedded-scan-{socket.gethostname()}-{os.getpid()}"


__all__ = [
    "EMBEDDED_SCAN_WORKER_ENV",
    "DEFAULT_SCHEDULER_INTERVAL_SECONDS",
    "EmbeddedScanWorker",
    "embedded_worker_job_kinds",
    "embedded_scan_worker_enabled",
]
