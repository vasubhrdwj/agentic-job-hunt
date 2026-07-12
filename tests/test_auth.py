"""Single-owner credential and opaque-session persistence tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from job_hunt_agent.auth import (
    AuthConfigError,
    authenticate_owner_token,
    create_owner_session,
    load_owner_session,
    revoke_owner_session,
)
from job_hunt_agent.database import Database
from job_hunt_agent.models import Base, OwnerSession
from job_hunt_agent.security import hash_access_token


@pytest.fixture
def auth_db(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'auth.db'}")
    Base.metadata.create_all(database.engine)
    try:
        yield database
    finally:
        database.dispose()


def test_valid_owner_token_creates_only_a_hashed_session(
    auth_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_token = "owner-token-with-at-least-thirty-two-random-chars"
    monkeypatch.setenv("JOB_HUNT_OWNER_TOKEN_HASH", hash_access_token(owner_token))
    monkeypatch.setenv("JOB_HUNT_OWNER_ID", "raman")
    assert authenticate_owner_token(owner_token) == "raman"

    now = datetime.now(timezone.utc)
    with auth_db.session() as session:
        grant = create_owner_session(session, "raman", now=now, ttl_days=30)
        assert grant.token != owner_token
        assert grant.owner_id == "raman"

    with auth_db.session() as session:
        stored = session.scalar(select(OwnerSession))
        assert stored is not None
        assert stored.token_hash == hash_access_token(grant.token)
        assert grant.token not in stored.token_hash
        assert load_owner_session(session, grant.token, now=now) is not None


def test_invalid_owner_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "owner-token-with-at-least-thirty-two-random-chars"
    monkeypatch.setenv("JOB_HUNT_OWNER_TOKEN_HASH", hash_access_token(token))
    with pytest.raises(PermissionError, match="access denied"):
        authenticate_owner_token("wrong-token-with-at-least-thirty-two-characters")


def test_missing_owner_hash_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOB_HUNT_OWNER_TOKEN_HASH", raising=False)
    with pytest.raises(AuthConfigError, match="SHA-256 hash"):
        authenticate_owner_token("owner-token-with-at-least-thirty-two-random-chars")


def test_expired_and_revoked_sessions_are_rejected(auth_db: Database) -> None:
    now = datetime.now(timezone.utc)
    with auth_db.session() as session:
        expired = create_owner_session(session, "owner", now=now, ttl_days=1)
        active = create_owner_session(session, "owner", now=now, ttl_days=30)

    with auth_db.session() as session:
        assert (
            load_owner_session(session, expired.token, now=now + timedelta(days=2))
            is None
        )
        assert revoke_owner_session(session, active.token, now=now + timedelta(hours=1))

    with auth_db.session() as session:
        assert load_owner_session(session, active.token, now=now + timedelta(hours=2)) is None
