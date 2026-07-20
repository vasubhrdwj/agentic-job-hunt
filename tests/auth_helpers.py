"""Reusable account seeding and login helpers for authenticated router tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from job_hunt_agent.auth import ensure_owner, hash_password, normalize_email
from job_hunt_agent.database import Database
from job_hunt_agent.models import OwnerCredential


TEST_ACCOUNT_EMAIL = "owner@example.com"
TEST_ACCOUNT_PASSWORD = "correct-horse-battery-staple"


def seed_test_account(
    database: Database,
    *,
    owner_id: str = "owner",
    email: str = TEST_ACCOUNT_EMAIL,
    password: str = TEST_ACCOUNT_PASSWORD,
) -> None:
    with database.session() as session:
        ensure_owner(session, owner_id)
        if session.get(OwnerCredential, owner_id) is None:
            session.add(
                OwnerCredential(
                    owner_id=owner_id,
                    normalized_email=normalize_email(email),
                    password_hash=hash_password(password),
                )
            )


def login_test_account(
    client: TestClient,
    *,
    origin: str,
    email: str = TEST_ACCOUNT_EMAIL,
    password: str = TEST_ACCOUNT_PASSWORD,
):
    return client.post(
        "/api/session",
        headers={"Origin": origin},
        json={"email": email, "password": password},
    )
