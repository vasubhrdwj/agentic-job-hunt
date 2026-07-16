"""Optional scan-only worker hosted inside the web process.

The embedded worker is a free-tier bridge for the private, user-triggered
product. A request wakes the web service and the same process drains durable
scan jobs while the service remains alive. Queue leases keep interrupted work
recoverable after a free-instance restart.

Provider-consuming contact and legacy jobs are intentionally excluded.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from dataclasses import dataclass, field

from .database import database_from_env
from .opportunity_scan_worker import SCAN_JOB_KIND
from .worker import run_worker_once


LOGGER = logging.getLogger(__name__)
EMBEDDED_SCAN_WORKER_ENV = "ENABLE_EMBEDDED_SCAN_WORKER"
DEFAULT_IDLE_SLEEP_SECONDS = 1.0
DEFAULT_ERROR_SLEEP_SECONDS = 5.0


def embedded_scan_worker_enabled() -> bool:
    return os.getenv(EMBEDDED_SCAN_WORKER_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@dataclass
class EmbeddedScanWorker:
    """Own one daemon thread that claims only first-party scan jobs."""

    worker_id: str = field(default_factory=lambda: _worker_id())
    idle_sleep_seconds: float = DEFAULT_IDLE_SLEEP_SECONDS
    error_sleep_seconds: float = DEFAULT_ERROR_SLEEP_SECONDS
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
            while not self._stop.is_set():
                try:
                    result = run_worker_once(
                        worker_id=self.worker_id,
                        durable_database=database,
                        practical_mode=True,
                        job_kinds={SCAN_JOB_KIND},
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


def _worker_id() -> str:
    configured = os.getenv("JOB_HUNT_EMBEDDED_WORKER_ID", "").strip()
    if configured:
        return configured
    return f"embedded-scan-{socket.gethostname()}-{os.getpid()}"


__all__ = [
    "EMBEDDED_SCAN_WORKER_ENV",
    "EmbeddedScanWorker",
    "embedded_scan_worker_enabled",
]
