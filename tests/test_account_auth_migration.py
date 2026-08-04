"""Migration parity and downgrade safety for account authentication."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from job_hunt_agent.auth import create_account
from job_hunt_agent.database import MIGRATION_HEAD, Database


REVISION = "20260720_0019"
CURRENT_REVISION = "20260804_0021"
PREVIOUS_REVISION = "20260715_0018"


def test_account_auth_migration_matches_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'account-auth.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.check(config)
    database = Database(url)
    try:
        assert MIGRATION_HEAD == CURRENT_REVISION
        assert database.current_migration_revision() == CURRENT_REVISION
        inspector = inspect(database.engine)
        assert {"owner_credentials", "auth_throttle_buckets"}.issubset(
            inspector.get_table_names()
        )
        credential_fks = inspector.get_foreign_keys("owner_credentials")
        assert credential_fks[0]["referred_table"] == "owners"
        assert credential_fks[0]["options"]["ondelete"] == "CASCADE"
        unique = inspector.get_unique_constraints("owner_credentials")
        assert any(item["column_names"] == ["normalized_email"] for item in unique)
    finally:
        database.dispose()


def test_downgrade_refuses_to_discard_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'account-downgrade.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    database = Database(url)
    try:
        with database.session() as session:
            create_account(
                session,
                email="vasu@example.com",
                password="correct-horse-battery-staple",
            )
    finally:
        database.dispose()
    with pytest.raises(RuntimeError, match="refusing account-auth downgrade"):
        command.downgrade(config, PREVIOUS_REVISION)


def test_upgrade_keeps_only_the_most_recent_active_legacy_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'legacy-session-claim.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, PREVIOUS_REVISION)
    database = Database(url)
    now = datetime.now(timezone.utc)
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO owners (id, display_name, timezone) "
                    "VALUES ('owner', 'Vasu', 'Asia/Kolkata')"
                )
            )
            for session_id, token_hash, last_seen in (
                ("older-session", "a" * 64, now - timedelta(hours=2)),
                ("current-session", "b" * 64, now - timedelta(minutes=1)),
            ):
                connection.execute(
                    text(
                        "INSERT INTO owner_sessions "
                        "(id, owner_id, token_hash, created_at, expires_at, last_seen_at) "
                        "VALUES (:id, 'owner', :token_hash, :created_at, :expires_at, :last_seen_at)"
                    ),
                    {
                        "id": session_id,
                        "token_hash": token_hash,
                        "created_at": (now - timedelta(days=1)).isoformat(),
                        "expires_at": (now + timedelta(days=30)).isoformat(),
                        "last_seen_at": last_seen.isoformat(),
                    },
                )
    finally:
        database.dispose()

    command.upgrade(config, "head")
    database = Database(url)
    try:
        with database.engine.connect() as connection:
            rows = dict(
                connection.execute(
                    text("SELECT id, revoked_at FROM owner_sessions ORDER BY id")
                ).all()
            )
        assert rows["current-session"] is None
        assert rows["older-session"] is not None
    finally:
        database.dispose()
