"""Backup, import, restart, migration, and deployment-smoke safety gates."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.engine import make_url

from job_hunt_agent import hunt_repository, persistence
from job_hunt_agent.database import MIGRATION_HEAD, Database
from job_hunt_agent.job_queue import record_worker_heartbeat
from job_hunt_agent.models import HuntRun, Owner, OwnerPrivacySetting
from job_hunt_agent.routers.health import readiness_snapshot
from job_hunt_agent.schemas import HuntResult
from job_hunt_agent.security import load_data_keyring
from scripts import deployment_smoke
from scripts.database_backup import (
    BackupError,
    _postgres_connection_args,
    create_backup,
    database_identity_hash,
    manifest_path,
    restore_backup,
    verify_backup,
)
from scripts.import_legacy_hunts import (
    LegacyImportError,
    import_legacy_hunts,
    main as import_main,
)
from scripts.migration_gate import (
    MigrationGateError,
    check_migrations,
    guarded_downgrade,
    migration_graph,
)


def _migrated_database(
    path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[str, Database]:
    url = f"sqlite+pysqlite:///{path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ENVIRONMENT", "development")
    command.upgrade(Config("alembic.ini"), "head")
    return url, Database(url)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sqlite_backup_verify_restore_and_tamper_detection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_url, source = _migrated_database(tmp_path / "source.db", monkeypatch)
    try:
        with source.session() as session:
            session.add(Owner(id="owner", display_name="Owner", timezone="UTC"))
    finally:
        source.dispose()

    backup = tmp_path / "backups" / "source.sqlite"
    manifest = create_backup(source_url, backup)
    assert manifest.backend == "sqlite"
    assert manifest.migration_revision == MIGRATION_HEAD
    assert manifest.source_identity_hash == database_identity_hash(source_url)
    assert verify_backup(backup, expect_current=True) == manifest

    restored_url = f"sqlite+pysqlite:///{tmp_path / 'restored.db'}"
    restore_backup(backup, restored_url, confirm_empty_target=True)
    restored = Database(restored_url)
    try:
        with restored.session() as session:
            assert session.get(Owner, "owner") is not None
        assert restored.current_migration_revision() == MIGRATION_HEAD
    finally:
        restored.dispose()

    backup.write_bytes(backup.read_bytes() + b"tamper")
    with pytest.raises(BackupError, match="checksum or size"):
        verify_backup(backup)


def test_backup_never_replaces_live_sqlite_or_publishes_without_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "live.db"
    source_url, database = _migrated_database(source_path, monkeypatch)
    database.dispose()
    before = _sha256(source_path)

    with pytest.raises(BackupError, match="live SQLite"):
        create_backup(source_url, source_path)
    assert _sha256(source_path) == before

    backup = tmp_path / "safe-backup.db"
    manifest_path(backup).write_text("occupied", encoding="utf-8")
    with pytest.raises(BackupError, match="already exists"):
        create_backup(source_url, backup)
    assert not backup.exists()


def test_postgres_backup_credentials_never_enter_argv() -> None:
    first = "postgresql+psycopg://operator:secret-one@db.example:5433/jobs"
    second = "postgresql+psycopg://operator:secret-two@db.example:5433/jobs"
    args, env = _postgres_connection_args(make_url(first))

    assert all("secret-one" not in argument for argument in args)
    assert env["PGPASSWORD"] == "secret-one"
    assert database_identity_hash(first) == database_identity_hash(second)


def test_migration_gate_uses_requested_database_and_requires_matching_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url, database = _migrated_database(tmp_path / "migrations.db", monkeypatch)
    database.dispose()
    head, previous = migration_graph()
    assert head == MIGRATION_HEAD
    assert previous is not None

    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///do-not-touch.db")
    result = check_migrations(database_url)
    assert result["revision"] == MIGRATION_HEAD
    assert os.environ["DATABASE_URL"] == "sqlite+pysqlite:///do-not-touch.db"
    assert guarded_downgrade(database_url, None, apply=False)["to_revision"] == previous

    other_url, other = _migrated_database(tmp_path / "other.db", monkeypatch)
    other.dispose()
    other_backup = tmp_path / "other-backup.db"
    create_backup(other_url, other_backup)
    with pytest.raises(MigrationGateError, match="different database"):
        guarded_downgrade(database_url, other_backup, apply=True)


def test_legacy_import_is_read_only_idempotent_and_honors_owner_retention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "legacy ?# history.db"
    persistence.init_db(source)
    current = datetime.now(timezone.utc)
    recent = HuntResult(run_id="recent-run", roles=[], outreach=[])
    expired = HuntResult(run_id="expired-run", roles=[], outreach=[])
    persistence.save_run(recent, path=source)
    persistence.save_run(expired, path=source)
    with sqlite3.connect(source) as connection:
        connection.execute(
            "UPDATE runs SET created_at = ? WHERE run_id = ?",
            ((current - timedelta(days=2)).isoformat(), recent.run_id),
        )
        connection.execute(
            "UPDATE runs SET created_at = ? WHERE run_id = ?",
            ((current - timedelta(days=10)).isoformat(), expired.run_id),
        )
    source_before = _sha256(source)

    target_url, target = _migrated_database(tmp_path / "target.db", monkeypatch)
    try:
        with target.session() as session:
            session.add(Owner(id="owner", display_name="Owner", timezone="UTC"))
            session.add(
                OwnerPrivacySetting(owner_id="owner", hunt_run_retention_days=7)
            )
    finally:
        target.dispose()
    keyring = load_data_keyring(production=False)

    dry_run = import_legacy_hunts(
        source,
        target_url,
        owner_id="owner",
        apply=False,
        keyring=keyring,
        now=current,
    )
    assert dry_run.importable == ["recent-run"]
    assert [item["run_id"] for item in dry_run.expired] == ["expired-run"]
    assert dry_run.imported == []

    applied = import_legacy_hunts(
        source,
        target_url,
        owner_id="owner",
        apply=True,
        keyring=keyring,
        now=current,
    )
    assert applied.imported == ["recent-run"]
    assert _sha256(source) == source_before

    target = Database(target_url)
    try:
        with target.session() as session:
            imported = session.get(HuntRun, "recent-run")
            assert imported is not None
            expiry = imported.access_expires_at
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            assert expiry == current - timedelta(days=2) + timedelta(days=7)
            assert hunt_repository.load_hunt_result(
                session,
                owner_id="owner",
                hunt_run_id="recent-run",
                keyring=keyring,
            ) == recent
    finally:
        target.dispose()

    repeated = import_legacy_hunts(
        source,
        target_url,
        owner_id="owner",
        apply=True,
        keyring=keyring,
        now=current,
    )
    assert repeated.imported == []
    assert repeated.already_imported == ["recent-run"]


def test_legacy_import_rejects_same_file_and_incompatible_security_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "legacy.db"
    persistence.init_db(source)
    with pytest.raises(LegacyImportError, match="must be different"):
        import_legacy_hunts(
            source,
            f"sqlite+pysqlite:///{source}",
            owner_id="owner",
            apply=False,
            keyring=load_data_keyring(production=False),
        )

    incompatible = tmp_path / "incompatible.db"
    with sqlite3.connect(incompatible) as connection:
        connection.execute(
            "CREATE TABLE runs (run_id TEXT PRIMARY KEY, payload TEXT, created_at TEXT)"
        )
        connection.execute("CREATE TABLE run_security (run_id TEXT PRIMARY KEY)")
    target_url, target = _migrated_database(tmp_path / "target.db", monkeypatch)
    try:
        with target.session() as session:
            session.add(Owner(id="owner", display_name="Owner", timezone="UTC"))
    finally:
        target.dispose()
    with pytest.raises(LegacyImportError, match="schema is incompatible"):
        import_legacy_hunts(
            incompatible,
            target_url,
            owner_id="owner",
            apply=False,
            keyring=load_data_keyring(production=False),
        )


def test_legacy_import_cli_requires_explicit_real_key_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "legacy.db"
    persistence.init_db(source)
    monkeypatch.delenv("JOB_HUNT_DATA_KEYS", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'target.db'}")

    assert import_main(["--source", str(source), "--owner-id", "owner"]) == 2
    assert "JOB_HUNT_DATA_KEYS must be configured" in capsys.readouterr().err


def test_database_state_and_readiness_survive_process_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url, database = _migrated_database(tmp_path / "restart.db", monkeypatch)
    now = datetime.now(timezone.utc)
    with database.session() as session:
        session.add(Owner(id="owner", display_name="Owner", timezone="UTC"))
        record_worker_heartbeat(
            session,
            worker_id="restart-worker",
            supported_kinds={"legacy_hunt"},
            now=now,
        )
    before = readiness_snapshot(database, now=now + timedelta(seconds=1))
    database.dispose()

    restarted = Database(database_url)
    try:
        with restarted.session() as session:
            assert session.scalar(select(Owner).where(Owner.id == "owner")) is not None
        after = readiness_snapshot(restarted, now=now + timedelta(seconds=2))
    finally:
        restarted.dispose()
    assert before["migrations"] == after["migrations"]
    assert after["ok"] is True


def _ready_snapshot(revision: str = MIGRATION_HEAD) -> dict[str, object]:
    return {
        "ok": True,
        "database": {"configured": True, "reachable": True},
        "migrations": {
            "current": True,
            "revision": revision,
            "expected_revision": revision,
        },
        "worker": {
            "fresh": True,
            "unsupported_active_kinds": [],
        },
    }


def test_deployment_smoke_is_provider_free_and_restart_aware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_snapshot()

    def fake_request(_base: str, path: str, **kwargs):
        if path == "/health":
            return deployment_smoke.HttpResult(200, {}, b'{"ok":true}')
        if path == "/ready":
            return deployment_smoke.HttpResult(
                200,
                {"cache-control": "no-store, max-age=0"},
                json.dumps(ready).encode(),
            )
        assert path == "/api/hunt"
        assert kwargs["method"] == "POST"
        return deployment_smoke.HttpResult(
            410,
            {
                "cache-control": "no-store, max-age=0",
                "content-type": "application/problem+json",
                "deprecation": "true",
                "sunset": "Tue, 31 Dec 2030 23:59:59 GMT",
                "link": "<https://example.com/migrate>; rel=deprecation",
                "x-request-id": "request-id",
                "x-legacy-hunt-mode": "read_only",
            },
            b'{"code":"legacy_read_only","retryable":false}',
        )

    monkeypatch.setattr(deployment_smoke, "_request", fake_request)
    report = deployment_smoke.deployment_smoke(
        "https://jobs.example",
        expect_legacy_mode="read_only",
        previous_snapshot=ready,
    )
    assert report["ok"] is True
    assert report["legacy_policy"] == {"mode": "read_only", "status": 410}

    changed = _ready_snapshot("unexpected-revision")
    with pytest.raises(deployment_smoke.SmokeError, match="restart changed"):
        deployment_smoke._compare_restart_snapshot(ready, changed)
