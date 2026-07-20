"""Account credentials, throttling, and opaque-session persistence tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

import job_hunt_agent.auth as auth_module
from job_hunt_agent.auth import (
    AUTH_FAILURE_LIMIT,
    AccountConflict,
    AuthConfigError,
    authenticate_account,
    claim_account,
    create_account,
    create_owner_session,
    legacy_recovery_available,
    legacy_recovery_configured,
    load_owner_session,
    normalize_email,
    revoke_owner_session,
    signup_enabled,
)
from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    AuthThrottleBucket,
    Base,
    Owner,
    OwnerCredential,
    OwnerSession,
)
from job_hunt_agent.security import hash_access_token


PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def auth_db(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'auth.db'}")
    Base.metadata.create_all(database.engine)
    try:
        yield database
    finally:
        database.dispose()


def test_signup_mode_is_explicit_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JOB_HUNT_SIGNUP_MODE", raising=False)
    assert signup_enabled() is False
    monkeypatch.setenv("JOB_HUNT_SIGNUP_MODE", "open")
    assert signup_enabled() is True
    monkeypatch.setenv("JOB_HUNT_SIGNUP_MODE", "typo")
    with pytest.raises(AuthConfigError, match="open.*closed"):
        signup_enabled()


def test_legacy_recovery_configuration_and_availability_are_fail_closed(
    auth_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery_token = "legacy-recovery-token-with-at-least-32-characters"
    monkeypatch.delenv("JOB_HUNT_OWNER_ID", raising=False)
    monkeypatch.delenv("JOB_HUNT_OWNER_TOKEN_HASH", raising=False)
    assert legacy_recovery_configured() is False
    with auth_db.session() as session:
        assert legacy_recovery_available(session) is False

    monkeypatch.setenv("JOB_HUNT_OWNER_ID", "legacy-owner")
    monkeypatch.setenv("JOB_HUNT_OWNER_TOKEN_HASH", "not-a-sha256-hash")
    assert legacy_recovery_configured() is False

    monkeypatch.setenv(
        "JOB_HUNT_OWNER_TOKEN_HASH",
        hash_access_token(recovery_token),
    )
    assert legacy_recovery_configured() is True
    with auth_db.session() as session:
        assert legacy_recovery_available(session) is False
        session.add(
            Owner(id="legacy-owner", display_name="Vasu", timezone="Asia/Kolkata")
        )

    with auth_db.session() as session:
        assert legacy_recovery_available(session) is True
        session.add(
            OwnerCredential(
                owner_id="legacy-owner",
                normalized_email="vasu@example.com",
                password_hash=auth_module.hash_password(PASSWORD),
            )
        )

    with auth_db.session() as session:
        assert legacy_recovery_available(session) is False


def test_account_email_is_normalized_password_is_argon2id_and_owner_id_is_generated(
    auth_db: Database,
) -> None:
    with auth_db.session() as session:
        owner = create_account(
            session,
            email="  Vasu@Example.COM ",
            password=PASSWORD,
            display_name="Vasu",
            timezone_name="Asia/Kolkata",
        )
        owner_id = owner.id

    assert len(owner_id) == 32
    assert owner_id != "vasu@example.com"
    with auth_db.session() as session:
        credential = session.get(OwnerCredential, owner_id)
        assert credential is not None
        assert credential.normalized_email == "vasu@example.com"
        assert credential.password_hash.startswith("$argon2id$")
        assert PASSWORD not in credential.password_hash
        authenticated = authenticate_account(
            session,
            email="VASU@example.com",
            password=PASSWORD,
        )
        assert authenticated.owner_id == owner_id


def test_duplicate_normalized_email_does_not_create_a_second_owner(
    auth_db: Database,
) -> None:
    with auth_db.session() as session:
        create_account(session, email="vasu@example.com", password=PASSWORD)
    with pytest.raises(AccountConflict, match="cannot be created"):
        with auth_db.session() as session:
            create_account(session, email=" VASU@EXAMPLE.COM ", password=PASSWORD)
    with auth_db.session() as session:
        assert session.scalar(select(func.count()).select_from(Owner)) == 1


def test_wrong_and_unknown_logins_are_generic_and_durably_throttled_without_raw_email(
    auth_db: Database,
) -> None:
    with auth_db.session() as session:
        create_account(session, email="vasu@example.com", password=PASSWORD)

    for attempt in range(AUTH_FAILURE_LIMIT):
        with auth_db.session() as session:
            result = authenticate_account(
                session,
                email="unknown.person@example.com",
                password="definitely-not-the-password",
            )
        assert result.owner_id is None
        assert result.throttled is (attempt == AUTH_FAILURE_LIMIT - 1)

    with auth_db.session() as session:
        blocked = authenticate_account(
            session,
            email="unknown.person@example.com",
            password="definitely-not-the-password",
        )
        rows = list(session.scalars(select(AuthThrottleBucket)))
    assert blocked.throttled is True
    serialized = " ".join(
        f"{row.bucket_id} {row.failure_count} {row.blocked_until}" for row in rows
    )
    assert "unknown.person@example.com" not in serialized
    assert all(len(row.bucket_id) == 3 for row in rows)
    # Twelve-bit keyed buckets plus one global signup bucket can never grow
    # beyond 4,097 rows, regardless of how many identifiers are attempted.
    assert 2**12 + 1 == 4097


def test_correct_password_recovers_even_after_targeted_failures(
    auth_db: Database,
) -> None:
    with auth_db.session() as session:
        owner = create_account(session, email="vasu@example.com", password=PASSWORD)
        owner_id = owner.id

    for _attempt in range(AUTH_FAILURE_LIMIT):
        with auth_db.session() as session:
            failed = authenticate_account(
                session,
                email="vasu@example.com",
                password="deliberately-wrong-password",
            )
        assert failed.owner_id is None

    with auth_db.session() as session:
        recovered = authenticate_account(
            session,
            email="vasu@example.com",
            password=PASSWORD,
        )
    assert recovered.owner_id == owner_id
    assert recovered.throttled is False


def test_authentication_locks_the_throttle_bucket_before_first_failure(
    auth_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    locked: list[str] = []
    monkeypatch.setattr(
        auth_module,
        "_lock_auth_bucket",
        lambda _session, bucket_id: locked.append(bucket_id),
    )

    with auth_db.session() as session:
        authenticate_account(
            session,
            email="new.person@example.com",
            password="definitely-not-the-password",
        )

    assert locked == [auth_module._throttle_bucket_id("new.person@example.com")]


def test_password_hash_work_is_rejected_when_bounded_capacity_is_full(
    auth_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FullCapacity:
        def acquire(self, *, blocking: bool) -> bool:
            assert blocking is False
            return False

        def release(self) -> None:
            raise AssertionError("an unacquired slot must not be released")

    with auth_db.session() as session:
        owner = create_account(session, email="vasu@example.com", password=PASSWORD)
        owner_id = owner.id
    monkeypatch.setattr(auth_module, "_PASSWORD_HASH_SLOTS", FullCapacity())

    with auth_db.session() as session:
        busy = authenticate_account(
            session,
            email="vasu@example.com",
            password=PASSWORD,
        )
    assert busy.owner_id is None
    assert busy.throttled is True
    assert owner_id is not None


def test_claim_requires_one_active_session_then_preserves_owner_and_session(
    auth_db: Database,
) -> None:
    now = datetime.now(timezone.utc)
    with auth_db.session() as session:
        session.add(Owner(id="legacy-owner", display_name="Vasu", timezone="Asia/Kolkata"))
        session.flush()
        current = create_owner_session(session, "legacy-owner", now=now)
        other = create_owner_session(session, "legacy-owner", now=now)

    with auth_db.session() as session:
        with pytest.raises(AccountConflict, match="uniquely authorized"):
            claim_account(
                session,
                owner_id="legacy-owner",
                email="vasu@example.com",
                password=PASSWORD,
                current_session_token=current.token,
                now=now + timedelta(minutes=1),
            )

    with auth_db.session() as session:
        assert revoke_owner_session(session, other.token, now=now + timedelta(minutes=1))

    with auth_db.session() as session:
        credential = claim_account(
            session,
            owner_id="legacy-owner",
            email="vasu@example.com",
            password=PASSWORD,
            current_session_token=current.token,
            now=now + timedelta(minutes=2),
        )
        assert credential.owner_id == "legacy-owner"

    with auth_db.session() as session:
        owner = session.get(Owner, "legacy-owner")
        assert owner is not None
        assert owner.display_name == "Vasu"
        assert load_owner_session(session, current.token, now=now + timedelta(minutes=3))
        assert load_owner_session(session, other.token, now=now + timedelta(minutes=3)) is None


def test_existing_legacy_session_still_loads_without_credentials(auth_db: Database) -> None:
    now = datetime.now(timezone.utc)
    with auth_db.session() as session:
        grant = create_owner_session(session, "legacy-owner", now=now, ttl_days=30)
    with auth_db.session() as session:
        stored = load_owner_session(session, grant.token, now=now)
        assert stored is not None
        assert session.get(OwnerCredential, "legacy-owner") is None


def test_expired_and_revoked_sessions_are_rejected(auth_db: Database) -> None:
    now = datetime.now(timezone.utc)
    with auth_db.session() as session:
        expired = create_owner_session(session, "owner", now=now, ttl_days=1)
        active = create_owner_session(session, "owner", now=now, ttl_days=30)

    with auth_db.session() as session:
        assert load_owner_session(session, expired.token, now=now + timedelta(days=2)) is None
        assert revoke_owner_session(session, active.token, now=now + timedelta(hours=1))

    with auth_db.session() as session:
        assert load_owner_session(session, active.token, now=now + timedelta(hours=2)) is None
        stored = session.scalar(
            select(OwnerSession).where(
                OwnerSession.token_hash == hash_access_token(active.token)
            )
        )
        assert stored is not None
