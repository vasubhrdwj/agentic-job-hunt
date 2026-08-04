"""Migration parity and downgrade safety for encrypted resume imports."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from job_hunt_agent.database import MIGRATION_HEAD, Database


REVISION = "20260721_0020"
CURRENT_REVISION = "20260804_0021"
PREVIOUS_REVISION = "20260720_0019"


def test_resume_import_migration_matches_models_and_cascade_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'resume-import.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.check(config)
    database = Database(url)
    try:
        assert MIGRATION_HEAD == CURRENT_REVISION
        assert database.current_migration_revision() == CURRENT_REVISION
        inspector = inspect(database.engine)
        assert "resume_imports" in inspector.get_table_names()
        foreign_keys = inspector.get_foreign_keys("resume_imports")
        assert any(
            item["referred_table"] == "owners"
            and item["constrained_columns"] == ["owner_id"]
            and item["options"]["ondelete"] == "CASCADE"
            for item in foreign_keys
        )
        assert any(
            item["referred_table"] == "resume_versions"
            and item["constrained_columns"] == ["owner_id", "resume_version_id"]
            and item["options"]["ondelete"] == "CASCADE"
            for item in foreign_keys
        )
        indexes = {
            tuple(item["column_names"])
            for item in inspector.get_indexes("resume_imports")
        }
        assert ("owner_id", "created_at") in indexes
        assert ("owner_id", "resume_version_id") in indexes
    finally:
        database.dispose()


def test_resume_import_migration_empty_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'resume-import-round-trip.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.downgrade(config, PREVIOUS_REVISION)
    database = Database(url)
    try:
        assert database.current_migration_revision() == PREVIOUS_REVISION
        assert "resume_imports" not in inspect(database.engine).get_table_names()
    finally:
        database.dispose()
    command.upgrade(config, "head")
    upgraded = Database(url)
    try:
        assert upgraded.current_migration_revision() == CURRENT_REVISION
    finally:
        upgraded.dispose()


def test_resume_import_migration_refuses_lossy_downgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'resume-import-downgrade.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    database = Database(url)
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO owners (id, display_name, timezone) "
                    "VALUES ('owner', 'Owner', 'UTC')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO resume_versions "
                    "(id, owner_id, label, encrypted_content, encryption_key_id, "
                    "content_hash, source, is_base, version) VALUES "
                    "('resume', 'owner', 'Resume', 'cipher', 'v1', :hash, "
                    "'uploaded', 1, 1)"
                ),
                {"hash": "a" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO resume_imports "
                    "(id, owner_id, resume_version_id, parser_version, media_type, "
                    "encrypted_payload, encryption_key_id, version) VALUES "
                    "('import', 'owner', 'resume', '1', 'text/plain', "
                    "'cipher', 'v1', 1)"
                )
            )
    finally:
        database.dispose()

    with pytest.raises(RuntimeError, match="refusing resume-import downgrade"):
        command.downgrade(config, PREVIOUS_REVISION)
