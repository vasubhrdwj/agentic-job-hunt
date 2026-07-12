"""Single-owner authentication primitives for the practical workspace."""

from __future__ import annotations

import hmac
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Owner, OwnerSession
from .security import hash_access_token


OWNER_ID_ENV = "JOB_HUNT_OWNER_ID"
OWNER_TOKEN_HASH_ENV = "JOB_HUNT_OWNER_TOKEN_HASH"
SESSION_TTL_DAYS_ENV = "JOB_HUNT_SESSION_TTL_DAYS"
SESSION_COOKIE_ENV = "JOB_HUNT_SESSION_COOKIE"
DEFAULT_OWNER_ID = "owner"
DEFAULT_SESSION_TTL_DAYS = 30
DEFAULT_SESSION_COOKIE = "job_hunt_session"
_HASH_RE = re.compile(r"^[a-fA-F0-9]{64}$")


class AuthConfigError(RuntimeError):
    """Raised when the private owner credential is missing or malformed."""


@dataclass(frozen=True)
class SessionGrant:
    token: str
    owner_id: str
    expires_at: datetime


def configured_owner_id() -> str:
    value = os.getenv(OWNER_ID_ENV, DEFAULT_OWNER_ID).strip()
    if not value or len(value) > 64:
        raise AuthConfigError(f"{OWNER_ID_ENV} must be 1-64 characters")
    return value


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


def authenticate_owner_token(token: str) -> str:
    """Validate a high-entropy bootstrap token and return the configured owner id."""

    configured_hash = os.getenv(OWNER_TOKEN_HASH_ENV, "").strip()
    if not _HASH_RE.fullmatch(configured_hash):
        raise AuthConfigError(
            f"{OWNER_TOKEN_HASH_ENV} must be the SHA-256 hash of a random owner token"
        )
    candidate = token.strip()
    if len(candidate) < 32 or not hmac.compare_digest(
        configured_hash.lower(), hash_access_token(candidate).lower()
    ):
        raise PermissionError("owner access denied")
    return configured_owner_id()


def ensure_owner(
    session: Session,
    owner_id: str,
    *,
    display_name: str = "Owner",
    timezone_name: str = "UTC",
) -> Owner:
    owner = session.get(Owner, owner_id)
    if owner is None:
        owner = Owner(id=owner_id, display_name=display_name, timezone=timezone_name)
        session.add(owner)
        session.flush()
    return owner


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


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
