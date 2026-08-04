"""Free-tier embedded scan-worker lifecycle tests."""

from __future__ import annotations

from threading import Event

from fastapi.testclient import TestClient

from job_hunt_agent import embedded_scan_worker
from job_hunt_agent.contact_search_repository import CONTACT_SEARCH_JOB_KIND
from job_hunt_agent.opportunity_fit_worker import (
    FIT_EVALUATION_ENABLED_ENV,
    FIT_EVALUATION_JOB_KIND,
)
from job_hunt_agent.opportunity_scan_worker import SCAN_JOB_KIND
from job_hunt_agent.scheduled_scan_repository import ScheduledScanBatch
from job_hunt_agent.worker import WorkerResult


class _FakeDatabase:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


def test_embedded_scan_worker_enabled_is_explicit(monkeypatch) -> None:
    monkeypatch.delenv(embedded_scan_worker.EMBEDDED_SCAN_WORKER_ENV, raising=False)
    assert embedded_scan_worker.embedded_scan_worker_enabled() is False
    monkeypatch.setenv(embedded_scan_worker.EMBEDDED_SCAN_WORKER_ENV, "1")
    assert embedded_scan_worker.embedded_scan_worker_enabled() is True
    monkeypatch.setenv(embedded_scan_worker.EMBEDDED_SCAN_WORKER_ENV, "false")
    assert embedded_scan_worker.embedded_scan_worker_enabled() is False


def test_embedded_worker_advertises_contacts_only_when_configured(monkeypatch) -> None:
    monkeypatch.setenv(
        "JOB_HUNT_WORKER_KINDS",
        f"{SCAN_JOB_KIND},{CONTACT_SEARCH_JOB_KIND}",
    )
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("USE_MOCKS", "0")
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    assert embedded_scan_worker.embedded_worker_job_kinds() == frozenset(
        {SCAN_JOB_KIND}
    )

    monkeypatch.setenv("SERPAPI_API_KEY", "configured-test-key")
    assert embedded_scan_worker.embedded_worker_job_kinds() == frozenset(
        {SCAN_JOB_KIND, CONTACT_SEARCH_JOB_KIND}
    )


def test_embedded_worker_advertises_fit_only_when_enabled_by_default(
    monkeypatch,
) -> None:
    monkeypatch.delenv("JOB_HUNT_WORKER_KINDS", raising=False)
    monkeypatch.delenv(FIT_EVALUATION_ENABLED_ENV, raising=False)
    assert embedded_scan_worker.embedded_worker_job_kinds() == frozenset(
        {SCAN_JOB_KIND}
    )

    monkeypatch.setenv(FIT_EVALUATION_ENABLED_ENV, "1")
    assert embedded_scan_worker.embedded_worker_job_kinds() == frozenset(
        {SCAN_JOB_KIND, FIT_EVALUATION_JOB_KIND}
    )


def test_missing_contact_provider_never_removes_scan_capability(monkeypatch) -> None:
    monkeypatch.setenv(
        "JOB_HUNT_WORKER_KINDS",
        f"{SCAN_JOB_KIND},{CONTACT_SEARCH_JOB_KIND}",
    )
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ENABLE_PRACTICAL_MODE", "1")
    monkeypatch.setenv("USE_MOCKS", "0")
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_KEY", raising=False)

    assert embedded_scan_worker.embedded_worker_job_kinds() == frozenset(
        {SCAN_JOB_KIND}
    )


def test_embedded_worker_claims_only_scan_jobs_and_disposes_database(
    monkeypatch,
) -> None:
    database = _FakeDatabase()
    called = Event()
    scheduler_called = Event()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        embedded_scan_worker,
        "database_from_env",
        lambda *, required: database,
    )

    def fake_scheduler(_database, *, limit):
        assert _database is database
        assert limit > 0
        scheduler_called.set()
        return ScheduledScanBatch(items=())

    monkeypatch.setattr(
        embedded_scan_worker,
        "_run_scheduled_scan_tick",
        fake_scheduler,
    )

    def fake_run_worker_once(**kwargs):
        captured.update(kwargs)
        called.set()
        return WorkerResult(claimed=False)

    monkeypatch.setattr(
        embedded_scan_worker,
        "run_worker_once",
        fake_run_worker_once,
    )

    worker = embedded_scan_worker.EmbeddedScanWorker(
        worker_id="embedded-test",
        idle_sleep_seconds=0.01,
    )
    worker.start()
    assert called.wait(timeout=2)
    worker.stop(timeout_seconds=2)

    assert worker.alive is False
    assert scheduler_called.is_set()
    assert captured["worker_id"] == "embedded-test"
    assert captured["durable_database"] is database
    assert captured["practical_mode"] is True
    assert captured["job_kinds"] == {SCAN_JOB_KIND}
    assert database.disposed is True


def test_embedded_worker_retries_sanitized_cycle_failures(monkeypatch) -> None:
    database = _FakeDatabase()
    attempted_twice = Event()
    calls = 0

    monkeypatch.setattr(
        embedded_scan_worker,
        "database_from_env",
        lambda *, required: database,
    )

    def fail_worker_once(**_kwargs):
        nonlocal calls
        calls += 1
        if calls >= 2:
            attempted_twice.set()
        raise RuntimeError("private database detail")

    monkeypatch.setattr(
        embedded_scan_worker,
        "run_worker_once",
        fail_worker_once,
    )

    worker = embedded_scan_worker.EmbeddedScanWorker(
        worker_id="embedded-retry",
        error_sleep_seconds=0.01,
    )
    worker.start()
    assert attempted_twice.wait(timeout=2)
    worker.stop(timeout_seconds=2)

    assert calls >= 2
    assert database.disposed is True


def test_contact_only_embedded_worker_never_enqueues_role_scans(monkeypatch) -> None:
    database = _FakeDatabase()
    worker_called = Event()
    scheduler_calls = 0

    monkeypatch.setattr(
        embedded_scan_worker,
        "database_from_env",
        lambda *, required: database,
    )
    monkeypatch.setattr(
        embedded_scan_worker,
        "embedded_worker_job_kinds",
        lambda: frozenset({CONTACT_SEARCH_JOB_KIND}),
    )

    def fake_scheduler(_database, *, limit):
        nonlocal scheduler_calls
        scheduler_calls += 1
        return ScheduledScanBatch(items=())

    def fake_worker(**kwargs):
        assert kwargs["job_kinds"] == {CONTACT_SEARCH_JOB_KIND}
        worker_called.set()
        return WorkerResult(claimed=False)

    monkeypatch.setattr(
        embedded_scan_worker,
        "_run_scheduled_scan_tick",
        fake_scheduler,
    )
    monkeypatch.setattr(embedded_scan_worker, "run_worker_once", fake_worker)

    worker = embedded_scan_worker.EmbeddedScanWorker(
        worker_id="embedded-contact-only",
        idle_sleep_seconds=0.01,
    )
    worker.start()
    assert worker_called.wait(timeout=2)
    worker.stop(timeout_seconds=2)

    assert scheduler_calls == 0
    assert database.disposed is True


def test_api_lifespan_starts_and_stops_embedded_worker(
    tmp_path,
    monkeypatch,
) -> None:
    from job_hunt_agent import api

    states: list[str] = []

    class FakeEmbeddedWorker:
        def start(self) -> None:
            states.append("started")

        def stop(self) -> None:
            states.append("stopped")

    monkeypatch.setenv("JOB_HUNT_DB_PATH", str(tmp_path / "embedded-api.db"))
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("ENABLE_PRACTICAL_MODE", raising=False)
    monkeypatch.setattr(api, "embedded_scan_worker_enabled", lambda: True)
    monkeypatch.setattr(api, "EmbeddedScanWorker", FakeEmbeddedWorker)

    app = api.create_app()
    with TestClient(app):
        assert states == ["started"]

    assert states == ["started", "stopped"]
