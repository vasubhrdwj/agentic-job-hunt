"""Migration parity for deterministic application artifacts."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from job_hunt_agent.database import Database


def test_application_artifact_migration_round_trip_and_constraints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'artifact-migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "20260714_0010")
    database = Database(url)
    try:
        inspector = inspect(database.engine)
        assert database.current_migration_revision() == "20260714_0010"
        assert {
            "application_artifact_revisions",
            "application_artifact_events",
        }.issubset(inspector.get_table_names())
        revision_uniques = {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_unique_constraints(
                "application_artifact_revisions"
            )
        }
        assert revision_uniques[
            "uq_application_artifact_revisions_owner_number"
        ] == ("owner_id", "application_pack_id", "revision_number")
        revision_foreign_keys = {
            item["name"]: tuple(item["constrained_columns"])
            for item in inspector.get_foreign_keys("application_artifact_revisions")
        }
        assert revision_foreign_keys[
            "fk_application_artifact_revisions_owner_grounding"
        ] == (
            "owner_id",
            "application_id",
            "application_pack_id",
            "grounding_revision_id",
        )
        event_uniques = {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_unique_constraints("application_artifact_events")
        }
        assert event_uniques[
            "uq_application_artifact_events_submission_ref"
        ] == (
            "owner_id",
            "application_id",
            "application_pack_id",
            "artifact_revision_id",
            "id",
        )
        event_checks = {
            item["name"]: item["sqltext"]
            for item in inspector.get_check_constraints("application_artifact_events")
        }
        assert "approved" in event_checks[
            "ck_application_artifact_events_event_resume_shape"
        ]
    finally:
        database.dispose()

    command.downgrade(config, "20260714_0009")
    database = Database(url)
    try:
        assert not {
            "application_artifact_revisions",
            "application_artifact_events",
        }.intersection(inspect(database.engine).get_table_names())
    finally:
        database.dispose()


def test_application_artifact_downgrade_cleans_receipt_namespace() -> None:
    source = Path(
        "migrations/versions/20260714_0010_application_artifacts.py"
    ).read_text(encoding="utf-8")
    assert "namespace LIKE 'application_artifact.%'" in source
