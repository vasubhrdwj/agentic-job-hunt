"""Authenticated, database-only daily digest endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Security, status
from fastapi.security import APIKeyCookie

from ..auth import session_cookie_name
from ..daily_digest_schemas import DailyDigestResponse
from ..daily_digest_workspace import DailyDigestWorkspaceStore
from ..database import Database
from .session import AuthenticatedOwner, require_owner_session
from .workspace import COMMON_ERROR_RESPONSES, WorkspaceApiError, _invoke, _raise_auth_problem


def create_daily_digest_router(
    database: Database | None,
    store: DailyDigestWorkspaceStore | None,
) -> APIRouter:
    def prevent_private_caching(response: Response) -> None:
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"

    router = APIRouter(
        tags=["daily-digest"],
        dependencies=[Depends(prevent_private_caching)],
    )
    owner_cookie = APIKeyCookie(
        name=session_cookie_name(),
        scheme_name="OwnerSessionCookie",
        auto_error=False,
    )

    def require_read_owner(
        request: Request,
        _session_cookie: str | None = Security(owner_cookie),
    ) -> AuthenticatedOwner:
        try:
            return require_owner_session(database, request)
        except HTTPException as exc:
            _raise_auth_problem(exc)

    @router.get(
        "/api/today/digest",
        response_model=DailyDigestResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def get_daily_digest(
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> DailyDigestResponse:
        return _invoke(
            _store(store).get_daily_digest,
            owner_id=owner.owner_id,
            owner_timezone=owner.timezone,
            owner_local_date=owner.local_date,
        )

    return router


def _store(store: DailyDigestWorkspaceStore | None) -> DailyDigestWorkspaceStore:
    if store is None:
        raise WorkspaceApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "workspace_unavailable",
            "daily digest storage is unavailable",
            retryable=True,
        )
    return store


__all__ = ["create_daily_digest_router"]
