"""Tests for the durable queue worker."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Thread

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet

from job_hunt_agent import hunt_repository, persistence
from job_hunt_agent.database import Database, DatabaseConfigError
from job_hunt_agent.hunt_repository import create_or_reuse_hunt
from job_hunt_agent.job_queue import recover_stale_jobs
from job_hunt_agent.models import BackgroundJob, HuntRun, Owner, WorkerHeartbeat
from job_hunt_agent.requests import HuntRequestPayload, canonical_request_json
from job_hunt_agent.schemas import HuntResult, OutreachDraft, Person, Role
from job_hunt_agent.security import hash_access_token, load_data_keyring


def _payload() -> HuntRequestPayload:
    return HuntRequestPayload.model_validate(
        {
            "resume_text": "Built SCIM systems.",
            "criteria": {
                "role_keywords": ["SCIM"],
                "seniority": "senior",
                "location": ["Remote-India"],
            },
            "pack": "backend_india",
            "provider_consent": True,
        }
    )


def _enqueue(run_id: str, *, max_attempts: int = 3) -> None:
    now = datetime.now(timezone.utc)
    envelope = load_data_keyring(production=False).encrypt(canonical_request_json(_payload()))
    persistence.create_run_security(
        run_id,
        access_hash=hash_access_token("token"),
        encrypted_request=envelope.ciphertext,
        encryption_key_id=envelope.key_id,
        request_expires_at=now + timedelta(hours=1),
        access_expires_at=now + timedelta(days=1),
        request_hash=hash_access_token("request"),
        max_attempts=max_attempts,
    )


def _create_practical_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Database:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'practical-worker.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    command.upgrade(Config("alembic.ini"), "head")
    return Database(database_url)


def _enqueue_practical(
    database: Database,
    run_id: str,
    *,
    max_attempts: int = 3,
) -> tuple[str, str]:
    request_json = canonical_request_json(_payload())
    now = datetime.now(timezone.utc)
    with database.session() as session:
        if session.get(Owner, "owner") is None:
            session.add(Owner(id="owner", display_name="Owner", timezone="UTC"))
            session.flush()
        created = create_or_reuse_hunt(
            session,
            owner_id="owner",
            request_json=request_json,
            request_hash=hashlib.sha256(request_json.encode("utf-8")).hexdigest(),
            access_hash=hash_access_token("token"),
            keyring=load_data_keyring(production=False),
            request_expires_at=now + timedelta(hours=1),
            access_expires_at=now + timedelta(days=1),
            max_attempts=max_attempts,
            run_id=run_id,
        )
        return created.state.run_id, created.state.background_job_id


def _fake_result(run_id: str) -> HuntResult:
    role = Role(
        company="Okta",
        title="Senior Engineer, Identity",
        url="https://www.linkedin.com/jobs/view/123",
        location="Remote-India",
        summary="Build SCIM provisioning.",
        match_reason="Listing mentions SCIM 2.0.",
    )
    person = Person(
        name="Priya Rao",
        title="Staff Engineer",
        company="Okta",
        profile_url="https://linkedin.com/in/priya",
        source="linkedin",
        why_relevant="Adjacent team.",
        verified_current_employer=True,
        confidence=0.9,
    )
    return HuntResult(
        run_id=run_id,
        roles=[role],
        outreach=[OutreachDraft(draft_id="d-0", role=role, person=person, message="hi")],
    )


@pytest.fixture(autouse=True)
def db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOB_HUNT_DB_PATH", str(tmp_path / "worker.db"))
    monkeypatch.setenv("USE_MOCKS", "1")
    monkeypatch.setenv("ENABLE_PRACTICAL_MODE", "0")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("JOB_HUNT_WORKER_KINDS", raising=False)
    persistence.init_db()


def test_worker_once_processes_queued_run_and_clears_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    _enqueue("worker-success")
    captured: dict[str, object] = {}

    def stub_run_hunt(**kwargs):
        captured.update(kwargs)
        return _fake_result(kwargs["run_id"])

    monkeypatch.setattr(worker, "run_hunt", stub_run_hunt)

    result = worker.run_worker_once(
        worker_id="worker-test",
        lease_seconds=60,
        use_mocks=True,
        enable_tracing=False,
    )

    assert result.claimed is True
    assert result.run_id == "worker-success"
    assert result.status == "succeeded"
    assert captured["resume_text"] == "Built SCIM systems."
    assert captured["use_mocks"] is True
    assert captured["enable_tracing"] is False
    assert captured["pack"] == "backend_india"
    assert persistence.load_run("worker-success") is not None
    assert persistence.load_encrypted_request("worker-success") is None


def test_production_worker_rejects_mock_mode_before_claiming_or_touching_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    database = _create_practical_database(tmp_path, monkeypatch)
    try:
        _run_id, job_id = _enqueue_practical(database, "production-mock-guard")
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setattr(
            worker.SerpAPIContactProvider,
            "from_env",
            classmethod(
                lambda _cls: pytest.fail("production mock guard ran too late")
            ),
        )
        monkeypatch.setattr(
            worker,
            "MockContactSearchProvider",
            lambda: pytest.fail("production constructed the mock contact provider"),
        )

        with pytest.raises(
            RuntimeError,
            match="USE_MOCKS must be false when ENVIRONMENT=production",
        ):
            worker.run_worker_once(
                worker_id="production-worker",
                use_mocks=True,
                enable_tracing=False,
                durable_database=database,
                practical_mode=True,
            )

        with database.session() as session:
            job = session.get(BackgroundJob, job_id)
            heartbeat = session.get(WorkerHeartbeat, "production-worker")
            assert job is not None and job.status == "queued"
            assert job.attempt_count == 0
            assert heartbeat is None
    finally:
        database.dispose()


@pytest.mark.parametrize(
    ("missing_or_unsafe", "value", "message"),
    [
        (
            "GEMINI_PAID_SERVICE_ACK",
            None,
            "GEMINI_PAID_SERVICE_ACK must be true",
        ),
        (
            "ENABLE_TRACE_DRAFT_CONTENT",
            "1",
            "ENABLE_TRACE_DRAFT_CONTENT must be false",
        ),
    ],
)
def test_production_worker_enforces_shared_provider_privacy_before_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_or_unsafe: str,
    value: str | None,
    message: str,
) -> None:
    from job_hunt_agent import worker

    database = _create_practical_database(tmp_path, monkeypatch)
    try:
        _run_id, job_id = _enqueue_practical(database, "production-provider-guard")
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("ENABLE_PRACTICAL_MODE", "1")
        monkeypatch.setenv("ENABLE_TRACING", "1")
        monkeypatch.setenv("ENABLE_TRACE_DRAFT_CONTENT", "0")
        monkeypatch.setenv("USE_MOCKS", "0")
        monkeypatch.setenv("GOOGLE_API_KEY", "test-google")
        monkeypatch.setenv("SERPAPI_API_KEY", "test-serpapi")
        monkeypatch.setenv("PHOENIX_API_KEY", "test-phoenix")
        monkeypatch.setenv(
            "PHOENIX_COLLECTOR_ENDPOINT",
            "https://app.phoenix.arize.com/s/test",
        )
        monkeypatch.setenv("JOB_HUNT_DATA_KEYS", "v1:test-only-key")
        monkeypatch.setenv("GEMINI_PAID_SERVICE_ACK", "1")
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+psycopg://worker:test@db.invalid/jobs?sslmode=require",
        )
        if value is None:
            monkeypatch.delenv(missing_or_unsafe, raising=False)
        else:
            monkeypatch.setenv(missing_or_unsafe, value)

        with pytest.raises(RuntimeError, match=message):
            worker.run_worker_once(
                worker_id="production-provider-worker",
                durable_database=database,
                practical_mode=True,
                use_mocks=False,
                enable_tracing=True,
            )

        with database.session() as session:
            job = session.get(BackgroundJob, job_id)
            heartbeat = session.get(WorkerHeartbeat, "production-provider-worker")
            assert job is not None and job.status == "queued"
            assert job.attempt_count == 0
            assert heartbeat is None
    finally:
        database.dispose()


def test_practical_job_kinds_default_normalize_and_reject_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    assert worker.resolve_practical_job_kinds() == worker.PRACTICAL_JOB_KINDS
    assert worker.resolve_practical_job_kinds(
        " scan_saved_search,legacy_hunt,scan_saved_search "
    ) == frozenset({"scan_saved_search", "legacy_hunt"})

    monkeypatch.setenv("JOB_HUNT_WORKER_KINDS", "discover_contacts")
    assert worker.resolve_practical_job_kinds() == frozenset({"discover_contacts"})

    with pytest.raises(RuntimeError, match="at least one supported practical job kind"):
        worker.resolve_practical_job_kinds(" , ")
    with pytest.raises(RuntimeError, match="unsupported job kinds: unknown"):
        worker.resolve_practical_job_kinds("scan_saved_search,unknown")


def test_production_scan_only_worker_bypasses_providers_and_leaves_hunts_queued(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    database = _create_practical_database(tmp_path, monkeypatch)
    try:
        _run_id, job_id = _enqueue_practical(database, "provider-free-scan-worker")
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("ENABLE_PRACTICAL_MODE", "1")
        monkeypatch.setenv("ENABLE_TRACE_DRAFT_CONTENT", "0")
        monkeypatch.setenv("USE_MOCKS", "0")
        monkeypatch.setenv(
            "JOB_HUNT_DATA_KEYS",
            f"v1:{Fernet.generate_key().decode('ascii')}",
        )
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+psycopg://worker:test@db.invalid/jobs?sslmode=require",
        )
        for name in (
            "GOOGLE_API_KEY",
            "SERPAPI_API_KEY",
            "SERPAPI_KEY",
            "PHOENIX_API_KEY",
            "PHOENIX_COLLECTOR_ENDPOINT",
            "GEMINI_PAID_SERVICE_ACK",
        ):
            monkeypatch.delenv(name, raising=False)

        result = worker.run_worker_once(
            worker_id="production-scan-only",
            durable_database=database,
            practical_mode=True,
            use_mocks=False,
            enable_tracing=False,
            job_kinds={worker.SCAN_JOB_KIND},
        )

        assert result.claimed is False
        with database.session() as session:
            job = session.get(BackgroundJob, job_id)
            heartbeat = session.get(WorkerHeartbeat, "production-scan-only")
            assert job is not None and job.status == "queued"
            assert job.attempt_count == 0
            assert heartbeat is not None
            assert heartbeat.current_job_id is None
            assert heartbeat.supported_kinds == [worker.SCAN_JOB_KIND]
    finally:
        database.dispose()


def test_production_contact_only_worker_does_not_require_ai_or_tracing_services(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    database = _create_practical_database(tmp_path, monkeypatch)
    try:
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("ENABLE_PRACTICAL_MODE", "1")
        monkeypatch.setenv("ENABLE_TRACE_DRAFT_CONTENT", "0")
        monkeypatch.setenv("USE_MOCKS", "0")
        monkeypatch.setenv("SERPAPI_API_KEY", "configured-test-key")
        monkeypatch.setenv(
            "JOB_HUNT_DATA_KEYS",
            f"v1:{Fernet.generate_key().decode('ascii')}",
        )
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+psycopg://worker:test@db.invalid/jobs?sslmode=require",
        )
        for name in (
            "GOOGLE_API_KEY",
            "PHOENIX_API_KEY",
            "PHOENIX_COLLECTOR_ENDPOINT",
            "GEMINI_PAID_SERVICE_ACK",
        ):
            monkeypatch.delenv(name, raising=False)

        result = worker.run_worker_once(
            worker_id="production-contact-only",
            durable_database=database,
            practical_mode=True,
            use_mocks=False,
            enable_tracing=False,
            job_kinds={worker.CONTACT_SEARCH_JOB_KIND},
        )

        assert result.claimed is False
        with database.session() as session:
            heartbeat = session.get(WorkerHeartbeat, "production-contact-only")
            assert heartbeat is not None
            assert heartbeat.supported_kinds == [worker.CONTACT_SEARCH_JOB_KIND]
    finally:
        database.dispose()


def test_production_scan_only_worker_still_requires_core_safety(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    database = _create_practical_database(tmp_path, monkeypatch)
    try:
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("ENABLE_PRACTICAL_MODE", "1")
        monkeypatch.setenv("ENABLE_TRACE_DRAFT_CONTENT", "0")
        monkeypatch.setenv("USE_MOCKS", "0")
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+psycopg://worker:test@db.invalid/jobs?sslmode=require",
        )
        monkeypatch.delenv("JOB_HUNT_DATA_KEYS", raising=False)

        with pytest.raises(
            RuntimeError,
            match="JOB_HUNT_DATA_KEYS is required",
        ):
            worker.run_worker_once(
                worker_id="unsafe-production-scan",
                durable_database=database,
                practical_mode=True,
                use_mocks=False,
                enable_tracing=False,
                job_kinds={worker.SCAN_JOB_KIND},
            )

        with database.session() as session:
            assert session.get(WorkerHeartbeat, "unsafe-production-scan") is None
    finally:
        database.dispose()


def test_worker_failure_retries_then_dead_letters(monkeypatch: pytest.MonkeyPatch) -> None:
    from job_hunt_agent import worker

    _enqueue("worker-poison", max_attempts=1)

    def broken_run_hunt(**_kwargs):
        raise RuntimeError("private details should not be stored")

    monkeypatch.setattr(worker, "run_hunt", broken_run_hunt)

    result = worker.run_worker_once(
        worker_id="worker-test",
        lease_seconds=60,
        use_mocks=True,
        enable_tracing=False,
    )

    state = persistence.get_run_state("worker-poison")
    assert result.claimed is True
    assert state is not None
    assert state.status == "dead_letter"
    assert state.last_error == "RuntimeError"
    assert persistence.load_run("worker-poison") is None
    assert persistence.load_encrypted_request("worker-poison") is not None


def test_worker_decryption_failure_is_terminal_and_clears_request() -> None:
    from job_hunt_agent import worker

    now = datetime.now(timezone.utc)
    persistence.create_run_security(
        "bad-ciphertext",
        access_hash=hash_access_token("token"),
        encrypted_request="not-a-valid-fernet-token",
        encryption_key_id="local-dev",
        request_expires_at=now + timedelta(hours=1),
        access_expires_at=now + timedelta(days=1),
    )

    result = worker.run_worker_once(
        worker_id="worker-test",
        lease_seconds=60,
        use_mocks=True,
        enable_tracing=False,
    )

    state = persistence.get_run_state("bad-ciphertext")
    assert result.claimed is True
    assert state is not None
    assert state.status == "failed"
    assert state.last_error == "DecryptionError"
    assert persistence.load_encrypted_request("bad-ciphertext") is None


def test_legacy_worker_does_not_advertise_postgres_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    database_url = f"sqlite+pysqlite:///{tmp_path / 'durable-worker.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    command.upgrade(Config("alembic.ini"), "head")
    durable_database = Database(database_url)
    try:
        result = worker.run_worker_once(
            worker_id="durable-worker",
            use_mocks=True,
            enable_tracing=False,
            durable_database=durable_database,
        )
        assert result.claimed is False
        with durable_database.session() as session:
            heartbeat = session.get(WorkerHeartbeat, "durable-worker")
            assert heartbeat is None
    finally:
        durable_database.dispose()


def test_busy_worker_heartbeat_interval_stays_below_readiness_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    monkeypatch.delenv("JOB_HUNT_WORKER_HEARTBEAT_MAX_AGE_SECONDS", raising=False)
    assert worker._busy_heartbeat_interval_seconds(300) == 30.0
    assert (
        worker._busy_heartbeat_interval_seconds(300)
        < worker.DEFAULT_WORKER_HEARTBEAT_MAX_AGE_SECONDS
    )

    monkeypatch.setenv("JOB_HUNT_WORKER_HEARTBEAT_MAX_AGE_SECONDS", "30")
    assert worker._busy_heartbeat_interval_seconds(300) == 10.0


def test_legacy_worker_never_opens_the_practical_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    class FailingDurableDatabase:
        touched = False

        def migrations_current(self) -> bool:
            self.touched = True
            return True

        def session(self):
            self.touched = True
            raise RuntimeError("database unavailable")

    _enqueue("heartbeat-outage")
    monkeypatch.setattr(
        worker,
        "run_hunt",
        lambda **kwargs: _fake_result(kwargs["run_id"]),
    )

    database = FailingDurableDatabase()
    result = worker.run_worker_once(
        worker_id="legacy-worker",
        lease_seconds=60,
        use_mocks=True,
        enable_tracing=False,
        durable_database=database,  # type: ignore[arg-type]
        practical_mode=False,
    )

    assert result.claimed is True
    assert result.status == "succeeded"
    assert database.touched is False


def test_busy_loop_renews_only_the_legacy_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    events: list[str] = []
    attempted = Event()

    def legacy_heartbeat(*_args, **_kwargs) -> bool:
        events.append("legacy")
        attempted.set()
        return False

    monkeypatch.setattr(worker, "_busy_heartbeat_interval_seconds", lambda _lease: 0.01)
    monkeypatch.setattr(worker.persistence, "heartbeat_run", legacy_heartbeat)
    heartbeat = worker._LeaseHeartbeat(
        "run",
        worker_id="worker",
        lease_token="lease",
        lease_seconds=60,
        durable_database=None,
    )
    heartbeat.start()
    assert attempted.wait(timeout=1)
    heartbeat.stop()

    assert events == ["legacy"]


def test_practical_worker_processes_only_postgres_and_clears_current_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    database = _create_practical_database(tmp_path, monkeypatch)
    try:
        run_id, job_id = _enqueue_practical(database, "practical-success")
        _enqueue("legacy-stays-queued")
        captured: dict[str, object] = {}

        def stub_run_hunt(**kwargs):
            captured.update(kwargs)
            return _fake_result(kwargs["run_id"])

        monkeypatch.setattr(worker, "run_hunt", stub_run_hunt)
        result = worker.run_worker_once(
            worker_id="practical-worker",
            lease_seconds=60,
            use_mocks=True,
            enable_tracing=False,
            durable_database=database,
            practical_mode=True,
        )

        assert result == worker.WorkerResult(
            claimed=True,
            run_id=run_id,
            status="succeeded",
            stage="succeeded",
        )
        assert captured["resume_text"] == "Built SCIM systems."
        legacy_state = persistence.get_run_state("legacy-stays-queued")
        assert legacy_state is not None and legacy_state.status == "queued"
        with database.session() as session:
            hunt = session.get(HuntRun, run_id)
            job = session.get(BackgroundJob, job_id)
            heartbeat = session.get(WorkerHeartbeat, "practical-worker")
            assert hunt is not None
            assert hunt.encrypted_request is None
            assert hunt.encrypted_result is not None
            assert "Built SCIM systems." not in hunt.encrypted_result
            assert job is not None and job.status == "succeeded"
            assert heartbeat is not None
            assert set(heartbeat.supported_kinds) == {
                "discover_contacts",
                worker.FIT_EVALUATION_JOB_KIND,
                "legacy_hunt",
                "scan_saved_search",
            }
            assert heartbeat.current_job_id is None
    finally:
        database.dispose()


def test_practical_worker_finishes_work_created_before_database_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    original = _create_practical_database(tmp_path, monkeypatch)
    database_url = original.url
    run_id, _job_id = _enqueue_practical(original, "restart-durable-hunt")
    original.dispose()

    restarted = Database(database_url)
    try:
        monkeypatch.setattr(
            worker,
            "run_hunt",
            lambda **kwargs: _fake_result(kwargs["run_id"]),
        )
        result = worker.run_worker_once(
            worker_id="restarted-worker",
            durable_database=restarted,
            practical_mode=True,
            use_mocks=True,
            enable_tracing=False,
        )

        assert result.run_id == run_id
        assert result.status == "succeeded"
        with restarted.session() as session:
            hunt = session.get(HuntRun, run_id)
            assert hunt is not None
            assert hunt.encrypted_result is not None
            assert hunt.encrypted_request is None
    finally:
        restarted.dispose()


def test_practical_worker_heartbeat_exposes_current_job_while_busy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    database = _create_practical_database(tmp_path, monkeypatch)
    entered = Event()
    release = Event()
    outcomes: list[worker.WorkerResult] = []
    try:
        run_id, job_id = _enqueue_practical(database, "busy-current-job")

        def blocked_run(**kwargs):
            entered.set()
            assert release.wait(timeout=3)
            return _fake_result(kwargs["run_id"])

        monkeypatch.setattr(worker, "run_hunt", blocked_run)
        thread = Thread(
            target=lambda: outcomes.append(
                worker.run_worker_once(
                    worker_id="busy-worker",
                    lease_seconds=60,
                    durable_database=database,
                    practical_mode=True,
                    use_mocks=True,
                    enable_tracing=False,
                )
            )
        )
        thread.start()
        assert entered.wait(timeout=3)
        with database.session() as session:
            heartbeat = session.get(WorkerHeartbeat, "busy-worker")
            assert heartbeat is not None
            assert heartbeat.current_job_id == job_id

        release.set()
        thread.join(timeout=3)
        assert not thread.is_alive()
        assert outcomes[0].run_id == run_id
        assert outcomes[0].status == "succeeded"
    finally:
        release.set()
        database.dispose()


def test_legacy_worker_leaves_postgres_hunt_queued(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    database = _create_practical_database(tmp_path, monkeypatch)
    try:
        _run_id, job_id = _enqueue_practical(database, "postgres-stays-queued")
        result = worker.run_worker_once(
            worker_id="legacy-only",
            durable_database=database,
            practical_mode=False,
            use_mocks=True,
            enable_tracing=False,
        )
        assert result.claimed is False
        with database.session() as session:
            job = session.get(BackgroundJob, job_id)
            assert job is not None and job.status == "queued"
            assert session.get(WorkerHeartbeat, "legacy-only") is None
    finally:
        database.dispose()


def test_practical_mode_never_falls_back_to_a_queued_sqlite_hunt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    _enqueue("must-not-run-from-sqlite")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(DatabaseConfigError, match="DATABASE_URL is required"):
        worker.run_worker_once(
            worker_id="practical-worker",
            practical_mode=True,
            use_mocks=True,
            enable_tracing=False,
        )
    state = persistence.get_run_state("must-not-run-from-sqlite")
    assert state is not None and state.status == "queued"


def test_practical_decryption_failure_dead_letters_but_retains_ciphertext(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    database = _create_practical_database(tmp_path, monkeypatch)
    try:
        run_id, job_id = _enqueue_practical(database, "bad-practical-cipher")
        with database.session() as session:
            hunt = session.get(HuntRun, run_id)
            assert hunt is not None
            hunt.encrypted_request = "not-a-valid-fernet-token"

        result = worker.run_worker_once(
            worker_id="practical-worker",
            durable_database=database,
            practical_mode=True,
            use_mocks=True,
            enable_tracing=False,
        )
        assert result.status == "dead_letter"
        with database.session() as session:
            hunt = session.get(HuntRun, run_id)
            job = session.get(BackgroundJob, job_id)
            assert hunt is not None and hunt.encrypted_request is not None
            assert job is not None and job.last_error == "DecryptionError"
    finally:
        database.dispose()


def test_practical_failure_retries_then_dead_letters_with_safe_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from job_hunt_agent import worker

    database = _create_practical_database(tmp_path, monkeypatch)
    try:
        run_id, job_id = _enqueue_practical(database, "retry-practical", max_attempts=2)

        def fail_with_private_detail(**_kwargs):
            raise RuntimeError("DISTINCTIVE PRIVATE RESUME CONTENT")

        monkeypatch.setattr(worker, "run_hunt", fail_with_private_detail)
        first = worker.run_worker_once(
            worker_id="practical-worker",
            durable_database=database,
            practical_mode=True,
            use_mocks=True,
            enable_tracing=False,
        )
        second = worker.run_worker_once(
            worker_id="practical-worker",
            durable_database=database,
            practical_mode=True,
            use_mocks=True,
            enable_tracing=False,
        )

        assert first.status == "queued"
        assert second.status == "dead_letter"
        assert "DISTINCTIVE PRIVATE RESUME CONTENT" not in caplog.text
        with database.session() as session:
            hunt = session.get(HuntRun, run_id)
            job = session.get(BackgroundJob, job_id)
            assert hunt is not None and hunt.encrypted_request is not None
            assert job is not None
            assert job.attempt_count == 2
            assert job.last_error == "RuntimeError"
    finally:
        database.dispose()


def test_practical_worker_completes_an_existing_encrypted_result_without_rerun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    database = _create_practical_database(tmp_path, monkeypatch)
    try:
        run_id, job_id = _enqueue_practical(database, "existing-result")
        envelope = load_data_keyring(production=False).encrypt(
            _fake_result(run_id).model_dump_json()
        )
        with database.session() as session:
            hunt = session.get(HuntRun, run_id)
            assert hunt is not None
            hunt.encrypted_result = envelope.ciphertext
            hunt.result_key_id = envelope.key_id
            hunt.encrypted_request = None
            hunt.request_key_id = None

        monkeypatch.setattr(
            worker,
            "run_hunt",
            lambda **_kwargs: pytest.fail("existing result was recomputed"),
        )
        result = worker.run_worker_once(
            worker_id="recovery-worker",
            durable_database=database,
            practical_mode=True,
            use_mocks=True,
            enable_tracing=False,
        )

        assert result.status == "succeeded"
        with database.session() as session:
            job = session.get(BackgroundJob, job_id)
            assert job is not None and job.status == "succeeded"
    finally:
        database.dispose()


def test_practical_worker_cannot_store_after_losing_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    database = _create_practical_database(tmp_path, monkeypatch)
    try:
        run_id, job_id = _enqueue_practical(database, "lost-practical-lease")

        def expire_lease_then_return(**kwargs):
            with database.session() as session:
                job = session.get(BackgroundJob, job_id)
                assert job is not None
                job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            with database.session() as session:
                assert recover_stale_jobs(session) == 1
            return _fake_result(kwargs["run_id"])

        monkeypatch.setattr(worker, "run_hunt", expire_lease_then_return)
        result = worker.run_worker_once(
            worker_id="stale-worker",
            lease_seconds=60,
            durable_database=database,
            practical_mode=True,
            use_mocks=True,
            enable_tracing=False,
        )

        assert result.status == "queued"
        with database.session() as session:
            hunt = session.get(HuntRun, run_id)
            job = session.get(BackgroundJob, job_id)
            assert hunt is not None and hunt.encrypted_result is None
            assert hunt.encrypted_request is not None
            assert job is not None and job.stage == "lease_recovered"
    finally:
        database.dispose()


def test_practical_running_cancellation_wins_over_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    database = _create_practical_database(tmp_path, monkeypatch)
    try:
        run_id, job_id = _enqueue_practical(database, "cancel-practical")

        def cancel_then_return(**kwargs):
            with database.session() as session:
                state = hunt_repository.cancel_hunt(
                    session,
                    owner_id="owner",
                    hunt_run_id=run_id,
                )
                assert state is not None
                assert state.cancel_requested_at is not None
            return _fake_result(kwargs["run_id"])

        monkeypatch.setattr(worker, "run_hunt", cancel_then_return)
        result = worker.run_worker_once(
            worker_id="practical-worker",
            durable_database=database,
            practical_mode=True,
            use_mocks=True,
            enable_tracing=False,
        )

        assert result.status == "cancelled"
        with database.session() as session:
            hunt = session.get(HuntRun, run_id)
            job = session.get(BackgroundJob, job_id)
            assert hunt is not None
            assert hunt.encrypted_request is None
            assert hunt.encrypted_result is None
            assert job is not None and job.status == "cancelled"
    finally:
        database.dispose()


def test_practical_claim_rejects_cross_owner_repository_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker

    database = _create_practical_database(tmp_path, monkeypatch)
    try:
        _run_id, job_id = _enqueue_practical(database, "owner-mismatch")
        with database.session() as session:
            session.add(Owner(id="other", display_name="Other", timezone="UTC"))
            job = session.get(BackgroundJob, job_id)
            assert job is not None
            job.owner_id = "other"
            job.dedupe_scope = "owner:other"

        monkeypatch.setattr(
            worker,
            "run_hunt",
            lambda **_kwargs: pytest.fail("invalid owner link reached run_hunt"),
        )
        result = worker.run_worker_once(
            worker_id="practical-worker",
            durable_database=database,
            practical_mode=True,
            use_mocks=True,
            enable_tracing=False,
        )
        assert result.status == "dead_letter"
        with database.session() as session:
            job = session.get(BackgroundJob, job_id)
            assert job is not None and job.last_error == "InvalidHuntReference"
    finally:
        database.dispose()


def test_practical_repository_error_is_sanitized_and_leased_for_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from job_hunt_agent import worker

    database = _create_practical_database(tmp_path, monkeypatch)
    try:
        _run_id, job_id = _enqueue_practical(database, "repository-outage")

        def explode(*_args, **_kwargs):
            raise RuntimeError("DISTINCTIVE PRIVATE RESUME CONTENT")

        monkeypatch.setattr(hunt_repository, "load_hunt_request_for_worker", explode)
        with pytest.raises(worker.PracticalWorkerError) as raised:
            worker.run_worker_once(
                worker_id="practical-worker",
                durable_database=database,
                practical_mode=True,
                use_mocks=True,
                enable_tracing=False,
            )
        assert str(raised.value) == "practical worker processing failed"
        assert "DISTINCTIVE PRIVATE RESUME CONTENT" not in caplog.text
        with database.session() as session:
            job = session.get(BackgroundJob, job_id)
            heartbeat = session.get(WorkerHeartbeat, "practical-worker")
            assert job is not None and job.status == "running"
            assert heartbeat is not None and heartbeat.current_job_id is None
            assert heartbeat.supported_kinds == []
    finally:
        database.dispose()
