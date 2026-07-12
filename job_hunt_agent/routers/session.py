"""Private single-owner session endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response, Security, status
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, Field

from ..auth import (
    AuthConfigError,
    authenticate_owner_token,
    create_owner_session,
    load_owner_session,
    revoke_owner_session,
    session_cookie_name,
)
from ..database import Database


class SessionCreateRequest(BaseModel):
    owner_token: str = Field(min_length=32, max_length=512)


class SessionResponse(BaseModel):
    owner_id: str
    display_name: str
    expires_at: datetime


class SessionDeleteResponse(BaseModel):
    ok: bool


@dataclass(frozen=True)
class AuthenticatedOwner:
    owner_id: str
    display_name: str
    expires_at: datetime


def create_session_router(
    database: Database | None,
    *,
    allowed_origins: list[str],
    production: bool,
) -> APIRouter:
    router = APIRouter(tags=["owner-session"])
    owner_cookie = _owner_cookie_security()

    @router.post("/api/session", response_model=SessionResponse)
    def create_session(
        payload: SessionCreateRequest,
        request: Request,
        response: Response,
    ) -> SessionResponse:
        _require_allowed_origin(request, allowed_origins, production=production)
        db = require_migrated_database(database)
        try:
            owner_id = authenticate_owner_token(payload.owner_token)
        except AuthConfigError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="owner session is not configured",
            ) from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="owner access denied",
            ) from exc

        with db.session() as session:
            grant = create_owner_session(session, owner_id)
            stored = load_owner_session(session, grant.token, touch=False)
            assert stored is not None
            display_name = stored.owner.display_name

        response.set_cookie(
            key=session_cookie_name(),
            value=grant.token,
            max_age=max(1, int((grant.expires_at - datetime.now(grant.expires_at.tzinfo)).total_seconds())),
            expires=grant.expires_at,
            path="/",
            secure=production,
            httponly=True,
            samesite="strict",
        )
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return SessionResponse(
            owner_id=grant.owner_id,
            display_name=display_name,
            expires_at=grant.expires_at,
        )

    @router.get("/api/session", response_model=SessionResponse)
    def get_session(
        request: Request,
        response: Response,
        _session_cookie: str | None = Security(owner_cookie),
    ) -> SessionResponse:
        owner = require_owner_session(database, request)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return SessionResponse(
            owner_id=owner.owner_id,
            display_name=owner.display_name,
            expires_at=owner.expires_at,
        )

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
        return AuthenticatedOwner(
            owner_id=stored.owner_id,
            display_name=stored.owner.display_name,
            expires_at=stored.expires_at,
        )


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
        description="Opaque HttpOnly session issued by POST /api/session.",
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
