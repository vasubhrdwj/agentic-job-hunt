"""Migration coverage for encrypted interview preparation storage."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config


def _config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_interview_preparation_migration_upgrades_and_empty_downgrade_is_lossless(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'prep-migration.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = _config(database_url)
    command.upgrade(config, "20260715_0017")
    engine = sa.create_engine(database_url)
    try:
        inspector = sa.inspect(engine)
        assert "application_interview_preparations" in inspector.get_table_names()
        assert "application_interview_preparation_revisions" in inspector.get_table_names()
        revision_columns = {
            column["name"]
            for column in inspector.get_columns(
                "application_interview_preparation_revisions"
            )
        }
        assert {
            "application_submission_id",
            "grounding_revision_id",
            "posting_version_id",
            "interview_round_id",
            "interview_round_version",
            "encrypted_payload",
            "source_fingerprint",
        }.issubset(revision_columns)
    finally:
        engine.dispose()
    command.downgrade(config, "20260715_0016")


def test_interview_preparation_migration_refuses_lossy_downgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'prep-lossy.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = _config(database_url)
    command.upgrade(config, "20260715_0017")
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("PRAGMA foreign_keys=OFF"))
            connection.execute(
                sa.text(
                    "INSERT INTO application_interview_preparations "
                    "(id, owner_id, application_id, version) "
                    "VALUES ('prep1', 'owner1', 'application1', 1)"
                )
            )
        with pytest.raises(RuntimeError, match="without losing interview preparation"):
            command.downgrade(config, "20260715_0016")
        with engine.begin() as connection:
            connection.execute(
                sa.text("DELETE FROM application_interview_preparations")
            )
    finally:
        engine.dispose()
    command.downgrade(config, "20260715_0016")
