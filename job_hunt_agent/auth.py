"""Multi-user account credentials and opaque browser sessions."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .models import AuthThrottleBucket, Owner, OwnerCredential, OwnerSession
from .security import hash_access_token


SESSION_TTL_DAYS_ENV = "JOB_HUNT_SESSION_TTL_DAYS"
SESSION_COOKIE_ENV = "JOB_HUNT_SESSION_COOKIE"
SIGNUP_MODE_ENV = "JOB_HUNT_SIGNUP_MODE"
AUTH_THROTTLE_SECRET_ENV = "JOB_HUNT_AUTH_THROTTLE_SECRET"
PRIVACY_RECEIPT_SECRET_ENV = "JOB_HUNT_PRIVACY_RECEIPT_SECRET"
LEGACY_OWNER_ID_ENV = "JOB_HUNT_OWNER_ID"
LEGACY_RECOVERY_TOKEN_HASH_ENV = "JOB_HUNT_OWNER_TOKEN_HASH"
DEFAULT_SESSION_TTL_DAYS = 30
DEFAULT_SESSION_COOKIE = "job_hunt_session"
DEFAULT_SIGNUP_MODE = "closed"
MIN_PASSWORD_CHARS = 12
MAX_PASSWORD_CHARS = 128
AUTH_FAILURE_LIMIT = 5
GLOBAL_SIGNUP_ATTEMPT_LIMIT = 20
PASSWORD_HASH_CONCURRENCY = 2
AUTH_WINDOW = timedelta(minutes=15)
AUTH_BLOCK = timedelta(minutes=15)
_SIGNUP_GLOBAL_BUCKET = "sgn"
_AUTH_THROTTLE_ADVISORY_NAMESPACE = 0x4A4F4241
_HASH_RE = re.compile(r"^[a-fA-F0-9]{64}$")
_EMAIL_RE = re.compile(
    r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_PASSWORD_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=19_456,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)
_PASSWORD_HASH_SLOTS = threading.BoundedSemaphore(PASSWORD_HASH_CONCURRENCY)
# Unknown accounts still pay the same Argon2 verification cost as real accounts.
_DUMMY_PASSWORD_HASH = _PASSWORD_HASHER.hash(
    "dummy-password-used-only-for-login-timing"
)


class AuthConfigError(RuntimeError):
    """Raised when account or session configuration is malformed."""


class AccountConflict(RuntimeError):
    """Raised when credentials cannot be attached without changing ownership."""


class AuthCapacityExceeded(RuntimeError):
    """Raised when bounded password-hash capacity is already in use."""


@dataclass(frozen=True)
class SessionGrant:
    token: str
    owner_id: str
    expires_at: datetime


@dataclass(frozen=True)
class AccountAuthentication:
    owner_id: str | None
    throttled: bool = False

    @property
    def authenticated(self) -> bool:
        return self.owner_id is not None


def signup_mode() -> Literal["open", "closed"]:
    value = os.getenv(SIGNUP_MODE_ENV, DEFAULT_SIGNUP_MODE).strip().lower()
    if value not in {"open", "closed"}:
        raise AuthConfigError(f"{SIGNUP_MODE_ENV} must be 'open' or 'closed'")
    return value  # type: ignore[return-value]


def signup_enabled() -> bool:
    return signup_mode() == "open"


def legacy_recovery_configured() -> bool:
    """Return whether the retired access key can prove one migration claim."""

    configured_hash = os.getenv(LEGACY_RECOVERY_TOKEN_HASH_ENV, "").strip()
    owner_id = os.getenv(LEGACY_OWNER_ID_ENV, "").strip()
    return bool(_HASH_RE.fullmatch(configured_hash) and owner_id and len(owner_id) <= 64)


def legacy_recovery_available(session: Session) -> bool:
    """Expose only whether the configured legacy workspace still needs an account."""

    if not legacy_recovery_configured():
        return False
    owner_id = _legacy_owner_id()
    return (
        session.get(Owner, owner_id) is not None
        and session.get(OwnerCredential, owner_id) is None
    )


def session_cookie_name() -> str:
    value = os.getenv(SESSION_COOKIE_ENV, DEFAULT_SESSION_COOKIE).strip()
    if not value or len(value) > 64:
        raise AuthConfigError(f"{SESSION_COOKIE_ENV} must be 1-64 characters")
    return value


def session_ttl_days() -> int:
    raw = os.getenv(SESSION_TTL_DAYS_ENV, str(DEFAULT_SESSION_TTL_DAYS)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise AuthConfigError(f"{SESSION_TTL_DAYS_ENV} must be an integer") from exc
    if not 1 <= value <= 365:
        raise AuthConfigError(f"{SESSION_TTL_DAYS_ENV} must be between 1 and 365")
    return value


def normalize_email(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    if (
        len(normalized) > 254
        or not _EMAIL_RE.fullmatch(normalized)
        or ".." in normalized.partition("@")[0]
    ):
        raise ValueError("enter a valid email address")
    return normalized


def validate_password(password: str) -> None:
    if not MIN_PASSWORD_CHARS <= len(password) <= MAX_PASSWORD_CHARS:
        raise ValueError(
            f"password must be {MIN_PASSWORD_CHARS}-{MAX_PASSWORD_CHARS} characters"
        )


def hash_password(password: str) -> str:
    validate_password(password)
    if not _PASSWORD_HASH_SLOTS.acquire(blocking=False):
        raise AuthCapacityExceeded("password verification is busy")
    try:
        return _PASSWORD_HASHER.hash(password)
    finally:
        _PASSWORD_HASH_SLOTS.release()


def ensure_owner(
    session: Session,
    owner_id: str,
    *,
    display_name: str = "Owner",
    timezone_name: str = "UTC",
) -> Owner:
    """Keep legacy sessions usable while their owner claims an account."""

    owner = session.get(Owner, owner_id)
    if owner is None:
        owner = Owner(id=owner_id, display_name=display_name, timezone=timezone_name)
        session.add(owner)
        session.flush()
    return owner


def create_account(
    session: Session,
    *,
    email: str,
    password: str,
    display_name: str = "Job seeker",
    timezone_name: str = "UTC",
    now: datetime | None = None,
) -> Owner:
    """Create an isolated owner with a server-generated, non-email identifier."""

    normalized_email = normalize_email(email)
    password_hash = hash_password(password)
    if session.scalar(
        select(OwnerCredential.owner_id).where(
            OwnerCredential.normalized_email == normalized_email
        )
    ) is not None:
        raise AccountConflict("account cannot be created")
    current = now or datetime.now(timezone.utc)
    owner = Owner(
        id=uuid4().hex,
        display_name=display_name.strip() or "Job seeker",
        timezone=timezone_name,
        created_at=current,
        updated_at=current,
    )
    session.add(owner)
    session.flush()
    session.add(
        OwnerCredential(
            owner_id=owner.id,
            normalized_email=normalized_email,
            password_hash=password_hash,
            created_at=current,
            updated_at=current,
        )
    )
    session.flush()
    return owner


def claim_account(
    session: Session,
    *,
    owner_id: str,
    email: str,
    password: str,
    current_session_token: str,
    now: datetime | None = None,
) -> OwnerCredential:
    """Attach login credentials without moving or recreating workspace data."""

    normalized_email = normalize_email(email)
    current = now or datetime.now(timezone.utc)
    owner = session.scalar(
        select(Owner).where(Owner.id == owner_id).with_for_update()
    )
    if owner is None:
        raise AccountConflict("workspace does not exist")
    if session.get(OwnerCredential, owner_id) is not None:
        raise AccountConflict("workspace already has an account")
    current_token_hash = hash_access_token(current_session_token)
    active_sessions = list(
        session.scalars(
            select(OwnerSession)
            .where(
                OwnerSession.owner_id == owner_id,
                OwnerSession.revoked_at.is_(None),
                OwnerSession.expires_at > current,
            )
            .with_for_update()
        )
    )
    if (
        len(active_sessions) != 1
        or active_sessions[0].token_hash != current_token_hash
    ):
        raise AccountConflict("workspace account claim is not uniquely authorized")
    if session.scalar(
        select(OwnerCredential.owner_id).where(
            OwnerCredential.normalized_email == normalized_email
        )
    ) is not None:
        raise AccountConflict("account cannot be claimed")
    password_hash = hash_password(password)
    credential = OwnerCredential(
        owner_id=owner_id,
        normalized_email=normalized_email,
        password_hash=password_hash,
        created_at=current,
        updated_at=current,
    )
    session.add(credential)
    session.execute(
        update(OwnerSession)
        .where(
            OwnerSession.owner_id == owner_id,
            OwnerSession.token_hash != current_token_hash,
            OwnerSession.revoked_at.is_(None),
        )
        .values(revoked_at=current)
    )
    session.flush()
    return credential


def recover_legacy_account(
    session: Session,
    *,
    recovery_token: str,
    email: str,
    password: str,
    now: datetime | None = None,
) -> OwnerCredential:
    """Attach normal credentials after one proof with the retired access key."""

    configured_hash = os.getenv(LEGACY_RECOVERY_TOKEN_HASH_ENV, "").strip()
    candidate = recovery_token.strip()
    if (
        not _HASH_RE.fullmatch(configured_hash)
        or len(candidate) < 32
        or not hmac.compare_digest(
            configured_hash.lower(),
            hash_access_token(candidate).lower(),
        )
    ):
        raise PermissionError("legacy recovery denied")

    normalized_email = normalize_email(email)
    current = now or datetime.now(timezone.utc)
    owner_id = _legacy_owner_id()
    owner = session.scalar(
        select(Owner).where(Owner.id == owner_id).with_for_update()
    )
    if owner is None or session.get(OwnerCredential, owner_id) is not None:
        raise AccountConflict("legacy workspace cannot be recovered")
    if session.scalar(
        select(OwnerCredential.owner_id).where(
            OwnerCredential.normalized_email == normalized_email
        )
    ) is not None:
        raise AccountConflict("account cannot be recovered")

    password_hash = hash_password(password)
    credential = OwnerCredential(
        owner_id=owner_id,
        normalized_email=normalized_email,
        password_hash=password_hash,
        created_at=current,
        updated_at=current,
    )
    session.add(credential)
    session.execute(
        update(OwnerSession)
        .where(
            OwnerSession.owner_id == owner_id,
            OwnerSession.revoked_at.is_(None),
        )
        .values(revoked_at=current)
    )
    session.flush()
    return credential


def authenticate_account(
    session: Session,
    *,
    email: str,
    password: str,
    now: datetime | None = None,
) -> AccountAuthentication:
    """Verify credentials and durably throttle a fixed, keyed identifier bucket."""

    current = now or datetime.now(timezone.utc)
    try:
        normalized_email = normalize_email(email)
    except ValueError:
        normalized_email = unicodedata.normalize("NFKC", email).strip().casefold()[:254]
    bucket_id = _throttle_bucket_id(normalized_email)
    _lock_auth_bucket(session, bucket_id)
    bucket = session.scalar(
        select(AuthThrottleBucket)
        .where(AuthThrottleBucket.bucket_id == bucket_id)
        .with_for_update()
    )
    blocked = bool(
        bucket is not None
        and bucket.blocked_until is not None
        and _as_utc(bucket.blocked_until) > _as_utc(current)
    )
    if bucket is not None and bucket.blocked_until is not None and not blocked:
        bucket.failure_count = 0
        bucket.window_started_at = current
        bucket.blocked_until = None

    credential = session.scalar(
        select(OwnerCredential).where(
            OwnerCredential.normalized_email == normalized_email
        )
    )
    # A blocked unknown identifier can be rejected without another expensive
    # hash. A real account still verifies the submitted password so targeted
    # failures cannot lock its owner out.
    if blocked and credential is None:
        return AccountAuthentication(owner_id=None, throttled=True)
    password_hash = credential.password_hash if credential else _DUMMY_PASSWORD_HASH
    if not _PASSWORD_HASH_SLOTS.acquire(blocking=False):
        return AccountAuthentication(owner_id=None, throttled=True)
    try:
        verified = _verify_password(password_hash, password)
        replacement_hash = (
            _PASSWORD_HASHER.hash(password)
            if credential is not None
            and verified
            and _PASSWORD_HASHER.check_needs_rehash(credential.password_hash)
            else None
        )
    finally:
        _PASSWORD_HASH_SLOTS.release()
    if credential is not None and verified:
        if replacement_hash is not None:
            credential.password_hash = replacement_hash
            credential.updated_at = current
        if bucket is not None:
            session.delete(bucket)
        session.flush()
        return AccountAuthentication(owner_id=credential.owner_id)

    if blocked:
        return AccountAuthentication(owner_id=None, throttled=True)

    if bucket is None:
        bucket = AuthThrottleBucket(
            bucket_id=bucket_id,
            failure_count=0,
            window_started_at=current,
            updated_at=current,
        )
        session.add(bucket)
    elif _as_utc(bucket.window_started_at) + AUTH_WINDOW <= _as_utc(current):
        bucket.failure_count = 0
        bucket.window_started_at = current
    bucket.failure_count += 1
    bucket.updated_at = current
    if bucket.failure_count >= AUTH_FAILURE_LIMIT:
        bucket.blocked_until = current + AUTH_BLOCK
    session.flush()
    return AccountAuthentication(
        owner_id=None,
        throttled=bucket.blocked_until is not None,
    )


def consume_signup_capacity(
    session: Session,
    *,
    now: datetime | None = None,
) -> bool:
    """Reserve one globally bounded signup hash operation."""

    return _consume_auth_capacity(
        session,
        bucket_id=_SIGNUP_GLOBAL_BUCKET,
        limit=GLOBAL_SIGNUP_ATTEMPT_LIMIT,
        now=now or datetime.now(timezone.utc),
    )


def create_owner_session(
    session: Session,
    owner_id: str,
    *,
    ttl_days: int | None = None,
    now: datetime | None = None,
) -> SessionGrant:
    current = now or datetime.now(timezone.utc)
    ttl = ttl_days if ttl_days is not None else session_ttl_days()
    if not 1 <= ttl <= 365:
        raise ValueError("session ttl must be between 1 and 365 days")
    ensure_owner(session, owner_id)
    token = secrets.token_urlsafe(32)
    expires_at = current + timedelta(days=ttl)
    session.add(
        OwnerSession(
            owner_id=owner_id,
            token_hash=hash_access_token(token),
            expires_at=expires_at,
            last_seen_at=current,
        )
    )
    session.flush()
    return SessionGrant(token=token, owner_id=owner_id, expires_at=expires_at)


def load_owner_session(
    session: Session,
    token: str | None,
    *,
    now: datetime | None = None,
    touch: bool = True,
) -> OwnerSession | None:
    if not token:
        return None
    current = now or datetime.now(timezone.utc)
    stored = session.scalar(
        select(OwnerSession).where(OwnerSession.token_hash == hash_access_token(token))
    )
    if stored is None or stored.revoked_at is not None:
        return None
    if _as_utc(stored.expires_at) <= _as_utc(current):
        return None
    if touch:
        stored.last_seen_at = current
        session.flush()
    return stored


def revoke_owner_session(
    session: Session,
    token: str | None,
    *,
    now: datetime | None = None,
) -> bool:
    stored = load_owner_session(session, token, now=now, touch=False)
    if stored is None:
        return False
    stored.revoked_at = now or datetime.now(timezone.utc)
    session.flush()
    return True


def _verify_password(password_hash: str, password: str) -> bool:
    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def _throttle_bucket_id(normalized_email: str) -> str:
    # Twelve keyed bits cap durable throttle state at exactly 4,096 rows. The
    # secret prevents an attacker from choosing collisions for another user.
    digest = hmac.new(
        _auth_throttle_secret(),
        normalized_email.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:3]


def _consume_auth_capacity(
    session: Session,
    *,
    bucket_id: str,
    limit: int,
    now: datetime,
) -> bool:
    _lock_auth_bucket(session, bucket_id)
    bucket = session.scalar(
        select(AuthThrottleBucket)
        .where(AuthThrottleBucket.bucket_id == bucket_id)
        .with_for_update()
    )
    if bucket is None:
        bucket = AuthThrottleBucket(
            bucket_id=bucket_id,
            failure_count=1,
            window_started_at=now,
            updated_at=now,
        )
        session.add(bucket)
        session.flush()
        return True
    if bucket.blocked_until is not None and _as_utc(bucket.blocked_until) > _as_utc(now):
        return False
    if _as_utc(bucket.window_started_at) + AUTH_WINDOW <= _as_utc(now):
        bucket.failure_count = 1
        bucket.window_started_at = now
        bucket.blocked_until = None
        bucket.updated_at = now
        session.flush()
        return True
    if bucket.failure_count >= limit:
        bucket.blocked_until = now + AUTH_BLOCK
        bucket.updated_at = now
        session.flush()
        return False
    bucket.failure_count += 1
    bucket.updated_at = now
    session.flush()
    return True


def _lock_auth_bucket(session: Session, bucket_id: str) -> None:
    """Serialize creation and mutation of one throttle bucket on PostgreSQL."""

    if session.get_bind().dialect.name != "postgresql":
        return
    digest = hashlib.sha256(bucket_id.encode("utf-8")).digest()
    bucket_key = int.from_bytes(digest[:4], "big") & 0x7FFFFFFF
    session.execute(
        select(
            func.pg_advisory_xact_lock(
                _AUTH_THROTTLE_ADVISORY_NAMESPACE,
                bucket_key,
            )
        )
    )


def _auth_throttle_secret() -> bytes:
    configured = os.getenv(AUTH_THROTTLE_SECRET_ENV, "").strip()
    if not configured:
        configured = os.getenv(PRIVACY_RECEIPT_SECRET_ENV, "").strip()
    if not configured:
        configured = "local-development-auth-throttle-key"
    return configured.encode("utf-8")


def _legacy_owner_id() -> str:
    value = os.getenv(LEGACY_OWNER_ID_ENV, "").strip()
    if not value or len(value) > 64:
        raise AuthConfigError(f"{LEGACY_OWNER_ID_ENV} must be 1-64 characters")
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
