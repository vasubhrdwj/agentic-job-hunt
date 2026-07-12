"""Alembic parity and round-trip tests for Phase 1 profile/search storage."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from job_hunt_agent.database import MIGRATION_HEAD, Database


def test_profile_search_migration_round_trip_and_metadata_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'profile-migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.check(config)

    database = Database(url)
    expected = {
        "candidate_profiles",
        "career_tracks",
        "resume_versions",
        "achievement_evidence",
        "saved_searches",
        "owner_mutation_receipts",
    }
    try:
        inspector = inspect(database.engine)
        assert database.current_migration_revision() == MIGRATION_HEAD
        assert expected.issubset(inspector.get_table_names())
        resume_indexes = {
            index["name"]: index for index in inspector.get_indexes("resume_versions")
        }
        assert resume_indexes["uq_resume_versions_owner_base"]["unique"] == 1
        search_foreign_keys = {
            fk["name"]: tuple(fk["constrained_columns"])
            for fk in inspector.get_foreign_keys("saved_searches")
        }
        assert search_foreign_keys["fk_saved_searches_owner_track"] == (
            "owner_id",
            "career_track_id",
        )
        assert search_foreign_keys["fk_saved_searches_owner_resume"] == (
            "owner_id",
            "resume_version_id",
        )
    finally:
        database.dispose()

    command.downgrade(config, "20260712_0003")
    downgraded = Database(url)
    try:
        assert expected.isdisjoint(inspect(downgraded.engine).get_table_names())
    finally:
        downgraded.dispose()

    command.upgrade(config, "head")
    command.check(config)
    upgraded = Database(url)
    try:
        assert upgraded.current_migration_revision() == MIGRATION_HEAD
        assert expected.issubset(inspect(upgraded.engine).get_table_names())
    finally:
        upgraded.dispose()
