"""HTTP contracts for multi-user signup, login, and legacy account claim."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from job_hunt_agent.auth import create_owner_session
from job_hunt_agent.database import Database
from job_hunt_agent.models import Owner, OwnerCredential, OwnerSession
from job_hunt_agent.routers.session import create_session_router


ORIGIN = "http://localhost:3000"
PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def account_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, Database]]:
    url = f"sqlite+pysqlite:///{tmp_path / 'accounts.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("JOB_HUNT_SIGNUP_MODE", "open")
    monkeypatch.setenv(
        "JOB_HUNT_PRIVACY_RECEIPT_SECRET",
        "stable-test-auth-secret-with-more-than-32-characters",
    )
    command.upgrade(Config("alembic.ini"), "head")
    database = Database(url)
    app = FastAPI()
    app.include_router(
        create_session_router(database, allowed_origins=[ORIGIN], production=False)
    )
    with TestClient(app) as client:
        yield client, database
    database.dispose()


def _signup(client: TestClient, email: str, *, timezone_name: str = "UTC"):
    return client.post(
        "/api/accounts",
        headers={"Origin": ORIGIN},
        json={
            "email": email,
            "password": PASSWORD,
            "display_name": "Vasu",
            "timezone": timezone_name,
        },
    )


def test_signup_creates_distinct_owners_and_opaque_sessions(
    account_client: tuple[TestClient, Database],
) -> None:
    client, database = account_client
    first = _signup(client, "first@example.com", timezone_name="Asia/Kolkata")
    assert first.status_code == 201, first.text
    first_body = first.json()
    first_cookie = client.cookies.get("job_hunt_session")
    assert first_body["timezone"] == "Asia/Kolkata"
    assert first_body["account_attached"] is True
    assert first_body["account_email"] == "first@example.com"
    assert first_cookie and first_cookie not in first.text

    client.cookies.clear()
    second = _signup(client, "second@example.com")
    assert second.status_code == 201, second.text
    second_body = second.json()
    assert second_body["owner_id"] != first_body["owner_id"]

    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(Owner)) == 2
        assert session.scalar(select(func.count()).select_from(OwnerCredential)) == 2
        assert session.scalar(select(func.count()).select_from(OwnerSession)) == 2
        assert all(
            row.token_hash != first_cookie
            for row in session.scalars(select(OwnerSession))
        )


def test_login_failures_are_generic_and_owner_token_login_is_removed(
    account_client: tuple[TestClient, Database],
) -> None:
    client, _database = account_client
    assert _signup(client, "vasu@example.com").status_code == 201
    client.cookies.clear()
    known = client.post(
        "/api/session",
        headers={"Origin": ORIGIN},
        json={"email": "vasu@example.com", "password": "wrong-password-value"},
    )
    unknown = client.post(
        "/api/session",
        headers={"Origin": ORIGIN},
        json={"email": "nobody@example.com", "password": "wrong-password-value"},
    )
    assert known.status_code == unknown.status_code == 401
    assert known.json() == unknown.json() == {
        "detail": "email or password is incorrect"
    }
    removed = client.post(
        "/api/session",
        headers={"Origin": ORIGIN},
        json={"owner_token": "old-private-passkey-value"},
    )
    assert removed.status_code == 422

    logged_in = client.post(
        "/api/session",
        headers={"Origin": ORIGIN},
        json={"email": " VASU@EXAMPLE.COM ", "password": PASSWORD},
    )
    assert logged_in.status_code == 200, logged_in.text
    assert logged_in.json()["account_email"] == "vasu@example.com"


def test_signup_mode_and_status_are_count_free(
    account_client: tuple[TestClient, Database],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _database = account_client
    opened = client.get("/api/session/status")
    assert opened.json() == {"state": "ready", "signup_enabled": True}
    assert "count" not in opened.text

    monkeypatch.setenv("JOB_HUNT_SIGNUP_MODE", "closed")
    closed = client.get("/api/session/status")
    assert closed.json() == {"state": "ready", "signup_enabled": False}
    denied = _signup(client, "closed@example.com")
    assert denied.status_code == 403


def test_claim_keeps_legacy_owner_data_on_the_only_active_session(
    account_client: tuple[TestClient, Database],
) -> None:
    client, database = account_client
    now = datetime.now(timezone.utc)
    with database.session() as session:
        session.add(
            Owner(id="legacy-owner", display_name="Legacy Vasu", timezone="Asia/Kolkata")
        )
        session.flush()
        current = create_owner_session(session, "legacy-owner", now=now)

    client.cookies.set(
        "job_hunt_session",
        current.token,
        domain="testserver.local",
        path="/",
    )
    old_cookie = current.token
    claimed = client.post(
        "/api/accounts/claim",
        headers={"Origin": ORIGIN},
        json={"email": "legacy@example.com", "password": PASSWORD},
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["owner_id"] == "legacy-owner"
    assert claimed.json()["display_name"] == "Legacy Vasu"
    assert claimed.json()["account_email"] == "legacy@example.com"
    rotated_cookie = client.cookies.get("job_hunt_session")
    assert rotated_cookie
    assert rotated_cookie != old_cookie

    with database.session() as session:
        credential = session.get(OwnerCredential, "legacy-owner")
        assert credential is not None
        sessions = list(
            session.scalars(
                select(OwnerSession).where(OwnerSession.owner_id == "legacy-owner")
            )
        )
    from job_hunt_agent.security import hash_access_token

    assert len(sessions) == 2
    sessions_by_hash = {row.token_hash: row for row in sessions}
    assert sessions_by_hash[hash_access_token(old_cookie)].revoked_at is not None
    assert sessions_by_hash[hash_access_token(rotated_cookie)].revoked_at is None
