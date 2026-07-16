"""Phase-0 database configuration and migration tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from job_hunt_agent.database import (
    MIGRATION_HEAD,
    Database,
    DatabaseConfigError,
    normalize_database_url,
    resolve_database_url,
)


def test_normalize_hosted_postgres_urls_uses_psycopg3() -> None:
    assert (
        normalize_database_url("postgres://user:pass@db.example/jobs")
        == "postgresql+psycopg://user:pass@db.example/jobs"
    )
    assert (
        normalize_database_url("postgresql://user:pass@db.example/jobs")
        == "postgresql+psycopg://user:pass@db.example/jobs"
    )


def test_production_rejects_non_postgres_database() -> None:
    with pytest.raises(DatabaseConfigError, match="must use PostgreSQL"):
        resolve_database_url("sqlite+pysqlite:///:memory:", production=True)


def test_production_postgres_requires_explicit_tls() -> None:
    insecure = "postgresql+psycopg://user:pass@db.example/jobs"
    with pytest.raises(DatabaseConfigError, match="must require PostgreSQL TLS"):
        resolve_database_url(insecure, production=True)
    with pytest.raises(DatabaseConfigError, match="must require PostgreSQL TLS"):
        resolve_database_url(f"{insecure}?sslmode=disable", production=True)
    for sslmode in ("require", "verify-ca", "verify-full"):
        assert (
            resolve_database_url(
                f"{insecure}?sslmode={sslmode}",
                production=True,
            )
            == f"{insecure}?sslmode={sslmode}"
        )


def test_empty_database_upgrades_to_foundation_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "foundation.db"
    url = f"sqlite+pysqlite:///{path}"
    monkeypatch.setenv("DATABASE_URL", url)

    config = Config("alembic.ini")
    command.upgrade(config, "head")

    database = Database(url)
    try:
        assert database.reachable()
        assert database.current_migration_revision() == MIGRATION_HEAD
        assert database.migrations_current()
        assert {
            "owners",
            "owner_sessions",
            "background_jobs",
            "background_job_events",
            "worker_heartbeats",
            "alembic_version",
        }.issubset(set(inspect(database.engine).get_table_names()))
        columns = {
            column["name"]: column
            for column in inspect(database.engine).get_columns("background_jobs")
        }
        assert columns["dedupe_scope"]["nullable"] is False
        unique_constraints = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspect(database.engine).get_unique_constraints("background_jobs")
        }
        assert unique_constraints["uq_background_jobs_scope_kind_dedupe"] == (
            "dedupe_scope",
            "kind",
            "dedupe_key",
        )
        check_constraints = {
            constraint["name"]
            for constraint in inspect(database.engine).get_check_constraints(
                "background_jobs"
            )
        }
        assert "ck_background_jobs_dedupe_scope_matches_owner" in check_constraints
    finally:
        database.dispose()


def test_unmigrated_database_is_not_current(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'empty.db'}")
    try:
        assert database.reachable()
        assert database.current_migration_revision() is None
        assert not database.migrations_current()
    finally:
        database.dispose()


def test_queue_scope_migration_backfills_existing_owner_and_system_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "backfill.db"
    url = f"sqlite+pysqlite:///{path}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "20260711_0001")

    database = Database(url)
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO owners (id, display_name, timezone) "
                    "VALUES ('system', 'System-named owner', 'UTC')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO background_jobs "
                    "(id, kind, owner_id, payload, dedupe_key) VALUES "
                    "('owner-job', 'scan_company', 'system', '{}', 'owner-slot'), "
                    "('system-job', 'scan_company', NULL, '{}', 'system-slot')"
                )
            )
    finally:
        database.dispose()

    command.upgrade(config, "head")
    migrated = Database(url)
    try:
        with migrated.engine.connect() as connection:
            rows = connection.execute(
                text("SELECT id, dedupe_scope FROM background_jobs ORDER BY id")
            )
            scopes = {str(row.id): str(row.dedupe_scope) for row in rows}
        assert scopes == {"owner-job": "owner:system", "system-job": "system"}
    finally:
        migrated.dispose()


def test_queue_scope_migration_can_downgrade_and_upgrade_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'roundtrip.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    populated = Database(url)
    try:
        with populated.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO owners (id, display_name, timezone) VALUES "
                    "('owner-a', 'Owner A', 'UTC'), ('owner-b', 'Owner B', 'UTC')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO background_jobs "
                    "(id, kind, owner_id, dedupe_scope, payload, dedupe_key) VALUES "
                    "('job-a', 'scan_company', 'owner-a', 'owner:owner-a', '{}', 'same'), "
                    "('job-b', 'scan_company', 'owner-b', 'owner:owner-b', '{}', 'same')"
                )
            )
    finally:
        populated.dispose()

    command.downgrade(config, "20260711_0001")

    downgraded = Database(url)
    try:
        columns = {
            column["name"]
            for column in inspect(downgraded.engine).get_columns("background_jobs")
        }
        constraints = {
            constraint["name"]
            for constraint in inspect(downgraded.engine).get_unique_constraints(
                "background_jobs"
            )
        }
        assert "dedupe_scope" not in columns
        assert "uq_background_jobs_kind_dedupe" in constraints
        with downgraded.engine.connect() as connection:
            keys = list(
                connection.execute(
                    text(
                        "SELECT dedupe_key FROM background_jobs "
                        "WHERE kind = 'scan_company' ORDER BY id"
                    )
                ).scalars()
            )
        assert len(keys) == 2
        assert len(set(keys)) == 2
    finally:
        downgraded.dispose()

    command.upgrade(config, "head")
    command.check(config)
