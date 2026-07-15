"""Alembic round-trip checks for exact application submissions."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from job_hunt_agent.database import Database


def test_application_submission_migration_round_trip_and_composite_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'application-submission-migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")

    database = Database(url)
    try:
        assert database.current_migration_revision() == "20260715_0014"
        inspector = inspect(database.engine)
        assert "application_submissions" in inspector.get_table_names()
        columns = {
            column["name"]: column
            for column in inspector.get_columns("application_submissions")
        }
        assert columns["application_pack_revision_id"]["nullable"] is False
        assert columns["application_artifact_approval_event_id"]["nullable"] is False
        assert columns["tailored_resume_version_id"]["nullable"] is False
        uniques = {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_unique_constraints("application_submissions")
        }
        assert uniques["uq_application_submissions_owner_application"] == (
            "owner_id",
            "application_id",
        )
        foreign_keys = {
            item["name"]: tuple(item["constrained_columns"])
            for item in inspector.get_foreign_keys("application_submissions")
        }
        assert foreign_keys["fk_application_submissions_owner_pack_review"] == (
            "owner_id",
            "application_id",
            "application_pack_id",
            "application_pack_revision_id",
            "application_pack_review_event_id",
        )
        assert foreign_keys[
            "fk_application_submissions_owner_artifact_approval"
        ] == (
            "owner_id",
            "application_id",
            "application_pack_id",
            "application_artifact_revision_id",
            "application_artifact_approval_event_id",
        )
        activity_columns = {
            column["name"]
            for column in inspector.get_columns("application_activity_events")
        }
        assert {"previous_action_item_id", "submission_id"}.issubset(
            activity_columns
        )
        activity_indexes = {
            item["name"] for item in inspector.get_indexes("application_activity_events")
        }
        assert {
            "uq_application_activity_events_owner_ready",
            "uq_application_activity_events_owner_applied",
            "uq_application_activity_events_owner_submission",
        }.issubset(activity_indexes)
    finally:
        database.dispose()

    command.downgrade(config, "20260714_0010")
    downgraded = Database(url)
    try:
        assert downgraded.current_migration_revision() == "20260714_0010"
        inspector = inspect(downgraded.engine)
        assert "application_submissions" not in inspector.get_table_names()
        activity_columns = {
            column["name"]
            for column in inspector.get_columns("application_activity_events")
        }
        assert "previous_action_item_id" not in activity_columns
        assert "submission_id" not in activity_columns
    finally:
        downgraded.dispose()

    command.upgrade(config, "head")
    upgraded = Database(url)
    try:
        assert upgraded.current_migration_revision() == "20260715_0014"
        assert "application_submissions" in inspect(upgraded.engine).get_table_names()
    finally:
        upgraded.dispose()

    source = Path(
        "migrations/versions/20260714_0011_application_submission.py"
    ).read_text(encoding="utf-8")
    assert source.index("DELETE FROM owner_mutation_receipts") < source.index(
        'op.drop_table("application_submissions")'
    )
