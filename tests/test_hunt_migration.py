"""Migration parity and round-trip tests for encrypted hunt storage."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from job_hunt_agent.database import MIGRATION_HEAD, Database


def test_hunt_storage_migration_round_trip_and_metadata_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'hunt-migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.check(config)

    database = Database(url)
    try:
        inspector = inspect(database.engine)
        assert database.current_migration_revision() == MIGRATION_HEAD
        assert {"hunt_runs", "hunt_outcomes"}.issubset(inspector.get_table_names())
        run_columns = {column["name"] for column in inspector.get_columns("hunt_runs")}
        assert {
            "owner_id",
            "background_job_id",
            "access_hash",
            "idempotency_key_hash",
            "request_hash",
            "encrypted_request",
            "request_key_id",
            "request_expires_at",
            "encrypted_result",
            "result_key_id",
            "access_expires_at",
        }.issubset(run_columns)
        unique_constraints = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("hunt_runs")
        }
        assert unique_constraints["uq_hunt_runs_owner_idempotency_key_hash"] == (
            "owner_id",
            "idempotency_key_hash",
        )
        assert unique_constraints["uq_hunt_runs_background_job_id"] == (
            "background_job_id",
        )
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO owners (id, display_name, timezone) "
                    "VALUES ('owner-migration', 'Migration', 'UTC')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO background_jobs "
                    "(id, kind, owner_id, dedupe_scope, subject_type, subject_id, "
                    "payload, dedupe_key) VALUES "
                    "('job-migration', 'legacy_hunt', 'owner-migration', "
                    "'owner:owner-migration', 'hunt_run', 'run-migration', "
                    "'{\"hunt_run_id\":\"run-migration\"}', 'migration')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO hunt_runs "
                    "(id, owner_id, background_job_id, access_hash, request_hash, "
                    "encrypted_request, request_key_id, request_expires_at, "
                    "access_expires_at) VALUES "
                    "('run-migration', 'owner-migration', 'job-migration', "
                    "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                    "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
                    "'ciphertext', 'v1', '2099-01-01T00:00:00+00:00', "
                    "'2099-02-01T00:00:00+00:00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO hunt_outcomes "
                    "(hunt_run_id, draft_id, encrypted_payload, encryption_key_id, logged_at) "
                    "VALUES ('run-migration', 'draft-1', 'ciphertext', 'v1', "
                    "'2099-01-01T00:00:00+00:00')"
                )
            )
    finally:
        database.dispose()

    command.downgrade(config, "20260711_0002")
    downgraded = Database(url)
    try:
        assert "hunt_runs" not in inspect(downgraded.engine).get_table_names()
        with downgraded.engine.connect() as connection:
            assert connection.execute(
                text("SELECT COUNT(*) FROM background_jobs WHERE id = 'job-migration'")
            ).scalar_one() == 1
    finally:
        downgraded.dispose()

    command.upgrade(config, "head")
    command.check(config)
    upgraded = Database(url)
    try:
        assert upgraded.current_migration_revision() == MIGRATION_HEAD
        with upgraded.engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM hunt_runs")).scalar_one() == 0
    finally:
        upgraded.dispose()
