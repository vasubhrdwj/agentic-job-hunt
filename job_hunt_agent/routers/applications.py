"""Authenticated database-only application workspace routes."""

from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    Security,
    status,
)
from fastapi.security import APIKeyCookie

from ..application_schemas import (
    ApplicationActivityListResponse,
    ApplicationDetailResponse,
    ApplicationListResponse,
    CursorToken,
    OpaqueId,
)
from ..application_workspace import ApplicationWorkspaceStore
from ..auth import session_cookie_name
from ..database import Database
from .session import AuthenticatedOwner, require_owner_session
from .workspace import (
    COMMON_ERROR_RESPONSES,
    WorkspaceApiError,
    _invoke,
    _not_found,
    _raise_auth_problem,
    _set_etag,
)


def create_application_router(
    database: Database | None,
    store: ApplicationWorkspaceStore | None,
) -> APIRouter:
    """Build owner-scoped application reads with no provider side effects."""

    def prevent_private_caching(response: Response) -> None:
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"

    router = APIRouter(
        tags=["applications"],
        dependencies=[Depends(prevent_private_caching)],
    )
    owner_cookie = APIKeyCookie(
        name=session_cookie_name(),
        scheme_name="OwnerSessionCookie",
        description="Opaque HttpOnly session issued by POST /api/session.",
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
        "/api/applications",
        response_model=ApplicationListResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def list_owner_applications(
        limit: int = Query(default=50, ge=1, le=50),
        cursor: CursorToken | None = Query(default=None),
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> ApplicationListResponse:
        return _invoke(
            _store(store).list_applications,
            owner_id=owner.owner_id,
            limit=limit,
            cursor=cursor,
        )

    @router.get(
        "/api/applications/{application_id}",
        response_model=ApplicationDetailResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def get_owner_application(
        application_id: OpaqueId,
        response: Response,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> ApplicationDetailResponse:
        application = _invoke(
            _store(store).get_application,
            owner_id=owner.owner_id,
            application_id=application_id,
        )
        if application is None:
            _not_found("application")
        _set_etag(response, application.application.version)
        return application

    @router.get(
        "/api/applications/{application_id}/activity",
        response_model=ApplicationActivityListResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def list_owner_application_activity(
        application_id: OpaqueId,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> ApplicationActivityListResponse:
        activity = _invoke(
            _store(store).list_activity,
            owner_id=owner.owner_id,
            application_id=application_id,
        )
        if activity is None:
            _not_found("application")
        return activity

    return router


def _store(store: ApplicationWorkspaceStore | None) -> ApplicationWorkspaceStore:
    if store is None:
        raise WorkspaceApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "workspace_unavailable",
            "application workspace storage is unavailable",
            retryable=True,
        )
    return store


__all__ = ["create_application_router"]
