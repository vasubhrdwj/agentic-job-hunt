"""Multi-user account and opaque-session endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request, Response, Security, status
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from ..auth import (
    AccountConflict,
    AuthCapacityExceeded,
    authenticate_account,
    claim_account,
    consume_signup_capacity,
    create_account,
    create_owner_session,
    legacy_recovery_available,
    load_owner_session,
    recover_legacy_account,
    revoke_owner_session,
    session_cookie_name,
    signup_enabled,
)
from ..database import Database
from ..models import OwnerCredential


class SessionCreateRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=128)


class AccountCreateRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=12, max_length=128)
    display_name: str | None = Field(default=None, max_length=200)
    timezone: str | None = Field(default=None, max_length=64)


class AccountClaimRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=12, max_length=128)


class LegacyAccountRecoveryRequest(BaseModel):
    recovery_token: str = Field(min_length=32, max_length=512)
    email: str = Field(max_length=320)
    password: str = Field(min_length=12, max_length=128)


class SessionResponse(BaseModel):
    owner_id: str
    display_name: str
    timezone: str
    local_date: date
    expires_at: datetime
    account_attached: bool
    account_email: str | None


class SessionDeleteResponse(BaseModel):
    ok: bool


class SessionStatusResponse(BaseModel):
    state: Literal["ready", "setup_required"]
    signup_enabled: bool
    legacy_recovery_enabled: bool


@dataclass(frozen=True)
class AuthenticatedOwner:
    owner_id: str
    display_name: str
    timezone: str
    local_date: date
    expires_at: datetime
    account_attached: bool
    account_email: str | None


def create_session_router(
    database: Database | None,
    *,
    allowed_origins: list[str],
    production: bool,
) -> APIRouter:
    router = APIRouter(tags=["account-session"])
    owner_cookie = _owner_cookie_security()

    @router.get("/api/session/status", response_model=SessionStatusResponse)
    def session_status(response: Response) -> SessionStatusResponse:
        database_ready = bool(
            database is not None
            and database.reachable()
            and database.migrations_current()
        )
        recovery_enabled = False
        if database_ready and database is not None:
            with database.session() as session:
                recovery_enabled = legacy_recovery_available(session)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return SessionStatusResponse(
            state="ready" if database_ready else "setup_required",
            signup_enabled=(
                database_ready and signup_enabled() and not recovery_enabled
            ),
            legacy_recovery_enabled=database_ready and recovery_enabled,
        )

    @router.post("/api/accounts", response_model=SessionResponse, status_code=201)
    def signup(
        payload: AccountCreateRequest,
        request: Request,
        response: Response,
    ) -> SessionResponse:
        _require_allowed_origin(request, allowed_origins, production=production)
        if not signup_enabled():
            raise HTTPException(status_code=403, detail="signup is closed")
        db = require_migrated_database(database)
        with db.session() as recovery_session:
            recovery_pending = legacy_recovery_available(recovery_session)
        if recovery_pending:
            raise HTTPException(
                status_code=409,
                detail="recover the previous workspace before creating new accounts",
            )
        timezone_name = _validated_timezone(payload.timezone or "UTC")
        with db.session() as throttle_session:
            signup_capacity = consume_signup_capacity(throttle_session)
        if not signup_capacity:
            raise HTTPException(
                status_code=429,
                detail="signup temporarily unavailable; try again later",
                headers={"Retry-After": "900"},
            )
        try:
            with db.session() as session:
                owner = create_account(
                    session,
                    email=payload.email,
                    password=payload.password,
                    display_name=payload.display_name or "Job seeker",
                    timezone_name=timezone_name,
                )
                grant = create_owner_session(session, owner.id)
                result = _load_session_response(session, grant.token)
        except AuthCapacityExceeded as exc:
            raise HTTPException(
                status_code=429,
                detail="account creation is busy; try again shortly",
                headers={"Retry-After": "5"},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (AccountConflict, IntegrityError) as exc:
            raise HTTPException(status_code=409, detail="account cannot be created") from exc
        _set_session_cookie(response, token=grant.token, expires_at=grant.expires_at, production=production)
        return result

    @router.post("/api/accounts/recover", response_model=SessionResponse)
    def recover_legacy_workspace(
        payload: LegacyAccountRecoveryRequest,
        request: Request,
        response: Response,
    ) -> SessionResponse:
        _require_allowed_origin(request, allowed_origins, production=production)
        db = require_migrated_database(database)
        try:
            with db.session() as session:
                credential = recover_legacy_account(
                    session,
                    recovery_token=payload.recovery_token,
                    email=payload.email,
                    password=payload.password,
                )
                grant = create_owner_session(session, credential.owner_id)
                result = _load_session_response(session, grant.token)
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="workspace recovery details are incorrect",
            ) from exc
        except AuthCapacityExceeded as exc:
            raise HTTPException(
                status_code=429,
                detail="workspace recovery is busy; try again shortly",
                headers={"Retry-After": "5"},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (AccountConflict, IntegrityError) as exc:
            raise HTTPException(
                status_code=409,
                detail="workspace cannot be recovered with these details",
            ) from exc
        _set_session_cookie(
            response,
            token=grant.token,
            expires_at=grant.expires_at,
            production=production,
        )
        return result

    @router.post("/api/session", response_model=SessionResponse)
    def create_session(
        payload: SessionCreateRequest,
        request: Request,
        response: Response,
    ) -> SessionResponse:
        _require_allowed_origin(request, allowed_origins, production=production)
        db = require_migrated_database(database)
        with db.session() as session:
            authentication = authenticate_account(
                session,
                email=payload.email,
                password=payload.password,
            )
            if authentication.authenticated:
                assert authentication.owner_id is not None
                grant = create_owner_session(session, authentication.owner_id)
                result = _load_session_response(session, grant.token)
            else:
                grant = None
                result = None
        if grant is None or result is None:
            if authentication.throttled:
                raise HTTPException(
                    status_code=429,
                    detail="sign-in temporarily unavailable; try again later",
                    headers={"Retry-After": "900"},
                )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="email or password is incorrect",
            )
        _set_session_cookie(response, token=grant.token, expires_at=grant.expires_at, production=production)
        return result

    @router.post("/api/accounts/claim", response_model=SessionResponse)
    def claim_legacy_workspace(
        payload: AccountClaimRequest,
        request: Request,
        response: Response,
        _session_cookie: str | None = Security(owner_cookie),
    ) -> SessionResponse:
        owner = require_owner_mutation(
            database,
            request,
            allowed_origins=allowed_origins,
            production=production,
        )
        db = require_migrated_database(database)
        token = request.cookies.get(session_cookie_name()) or ""
        try:
            with db.session() as session:
                claim_account(
                    session,
                    owner_id=owner.owner_id,
                    email=payload.email,
                    password=payload.password,
                    current_session_token=token,
                )
                revoke_owner_session(session, token)
                grant = create_owner_session(session, owner.owner_id)
                result = _load_session_response(session, grant.token)
        except AuthCapacityExceeded as exc:
            raise HTTPException(
                status_code=429,
                detail="account security is busy; try again shortly",
                headers={"Retry-After": "5"},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (AccountConflict, IntegrityError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _set_session_cookie(
            response,
            token=grant.token,
            expires_at=grant.expires_at,
            production=production,
        )
        return result

    @router.get("/api/session", response_model=SessionResponse)
    def get_session(
        request: Request,
        response: Response,
        _session_cookie: str | None = Security(owner_cookie),
    ) -> SessionResponse:
        owner = require_owner_session(database, request)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return _owner_response(owner)

    @router.delete("/api/session", response_model=SessionDeleteResponse)
    def delete_session(
        request: Request,
        response: Response,
        _session_cookie: str | None = Security(owner_cookie),
    ) -> SessionDeleteResponse:
        require_owner_mutation(
            database,
            request,
            allowed_origins=allowed_origins,
            production=production,
        )
        db = require_migrated_database(database)
        token = request.cookies.get(session_cookie_name())
        with db.session() as session:
            revoke_owner_session(session, token)
        response.delete_cookie(
            key=session_cookie_name(),
            path="/",
            secure=production,
            httponly=True,
            samesite="strict",
        )
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return SessionDeleteResponse(ok=True)

    return router


def require_migrated_database(database: Database | None) -> Database:
    if database is None or not database.reachable():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="durable database is unavailable",
        )
    if not database.migrations_current():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="durable database migration is required",
        )
    return database


def require_owner_session(database: Database | None, request: Request) -> AuthenticatedOwner:
    db = require_migrated_database(database)
    token = request.cookies.get(session_cookie_name())
    with db.session() as session:
        stored = load_owner_session(session, token)
        if stored is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="owner session required",
            )
        credential = session.get(OwnerCredential, stored.owner_id)
        return AuthenticatedOwner(
            owner_id=stored.owner_id,
            display_name=stored.owner.display_name,
            timezone=stored.owner.timezone,
            local_date=_local_date(stored.owner.timezone),
            expires_at=stored.expires_at,
            account_attached=credential is not None,
            account_email=credential.normalized_email if credential else None,
        )


def _load_session_response(session, token: str) -> SessionResponse:
    stored = load_owner_session(session, token, touch=False)
    assert stored is not None
    credential = session.get(OwnerCredential, stored.owner_id)
    return SessionResponse(
        owner_id=stored.owner_id,
        display_name=stored.owner.display_name,
        timezone=stored.owner.timezone,
        local_date=_local_date(stored.owner.timezone),
        expires_at=stored.expires_at,
        account_attached=credential is not None,
        account_email=credential.normalized_email if credential else None,
    )


def _owner_response(owner: AuthenticatedOwner) -> SessionResponse:
    return SessionResponse(
        owner_id=owner.owner_id,
        display_name=owner.display_name,
        timezone=owner.timezone,
        local_date=owner.local_date,
        expires_at=owner.expires_at,
        account_attached=owner.account_attached,
        account_email=owner.account_email,
    )


def _set_session_cookie(
    response: Response,
    *,
    token: str,
    expires_at: datetime,
    production: bool,
) -> None:
    response.set_cookie(
        key=session_cookie_name(),
        value=token,
        max_age=max(
            1,
            int(
                (
                    expires_at
                    - datetime.now(expires_at.tzinfo or timezone.utc)
                ).total_seconds()
            ),
        ),
        expires=expires_at,
        path="/",
        secure=production,
        httponly=True,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"


def _validated_timezone(timezone_name: str) -> str:
    normalized = timezone_name.strip()
    try:
        ZoneInfo(normalized)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise HTTPException(status_code=422, detail="timezone must be a valid IANA timezone") from exc
    return normalized


def _local_date(timezone_name: str) -> date:
    try:
        zone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="owner timezone is invalid",
        ) from exc
    return datetime.now(timezone.utc).astimezone(zone).date()


def require_owner_mutation(
    database: Database | None,
    request: Request,
    *,
    allowed_origins: list[str],
    production: bool,
) -> AuthenticatedOwner:
    """Authorize a cookie-authenticated state change and enforce its origin."""

    _require_allowed_origin(request, allowed_origins, production=production)
    return require_owner_session(database, request)


def _owner_cookie_security() -> APIKeyCookie:
    return APIKeyCookie(
        name=session_cookie_name(),
        scheme_name="OwnerSessionCookie",
        description="Opaque HttpOnly session issued after account authentication.",
        auto_error=False,
    )


def _require_allowed_origin(
    request: Request,
    allowed_origins: list[str],
    *,
    production: bool,
) -> None:
    origin = request.headers.get("origin", "").rstrip("/")
    normalized_allowed = {value.rstrip("/") for value in allowed_origins}
    if not origin:
        if production:
            raise HTTPException(status_code=403, detail="request origin required")
        return
    if origin not in normalized_allowed:
        raise HTTPException(status_code=403, detail="request origin is not allowed")
