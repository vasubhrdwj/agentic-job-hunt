"""Authenticated database-only application workspace routes."""

from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    Header,
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
from ..contact_schemas import ApplicationContactBenchResponse
from ..database import Database
from ..outreach_schemas import (
    ApplicationOutreachResponse,
    OutreachEventCreate,
    OutreachMessageCreate,
)
from .session import AuthenticatedOwner, require_owner_mutation, require_owner_session
from .workspace import (
    COMMON_ERROR_RESPONSES,
    WorkspaceApiError,
    _expected_version,
    _invoke,
    _not_found,
    _raise_auth_problem,
    _required_idempotency_key,
    _set_etag,
)


def create_application_router(
    database: Database | None,
    store: ApplicationWorkspaceStore | None,
    *,
    allowed_origins: list[str],
    production: bool,
) -> APIRouter:
    """Build owner-scoped application reads and durable contact-search starts."""

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

    def require_mutation_owner(
        request: Request,
        _session_cookie: str | None = Security(owner_cookie),
    ) -> AuthenticatedOwner:
        try:
            return require_owner_mutation(
                database,
                request,
                allowed_origins=allowed_origins,
                production=production,
            )
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

    @router.get(
        "/api/applications/{application_id}/contacts",
        response_model=ApplicationContactBenchResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def get_owner_application_contacts(
        application_id: OpaqueId,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> ApplicationContactBenchResponse:
        contacts = _invoke(
            _store(store).get_application_contacts,
            owner_id=owner.owner_id,
            application_id=application_id,
        )
        if contacts is None:
            _not_found("application")
        return contacts

    @router.post(
        "/api/applications/{application_id}/contact-searches",
        response_model=ApplicationContactBenchResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses=COMMON_ERROR_RESPONSES,
    )
    def create_owner_application_contact_search(
        application_id: OpaqueId,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApplicationContactBenchResponse:
        contacts = _invoke(
            _store(store).create_application_contact_search,
            owner_id=owner.owner_id,
            application_id=application_id,
            expected_application_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        if contacts is None:
            _not_found("application")
        return contacts

    @router.get(
        "/api/applications/{application_id}/outreach",
        response_model=ApplicationOutreachResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def get_owner_application_outreach(
        application_id: OpaqueId,
        response: Response,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> ApplicationOutreachResponse:
        outreach = _invoke(
            _store(store).get_application_outreach,
            owner_id=owner.owner_id,
            application_id=application_id,
        )
        if outreach is None:
            _not_found("application")
        if outreach.sequence is not None:
            _set_etag(response, outreach.sequence.version)
        return outreach

    @router.post(
        "/api/applications/{application_id}/outreach-sequences",
        response_model=ApplicationOutreachResponse,
        status_code=status.HTTP_201_CREATED,
        responses=COMMON_ERROR_RESPONSES,
    )
    def start_owner_application_outreach(
        application_id: OpaqueId,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApplicationOutreachResponse:
        outreach = _invoke(
            _store(store).start_application_outreach,
            owner_id=owner.owner_id,
            application_id=application_id,
            expected_application_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        if outreach is None:
            _not_found("application")
        if outreach.sequence is None:
            raise WorkspaceApiError(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "workspace_unavailable",
                "application outreach storage is unavailable",
                retryable=True,
            )
        _set_etag(response, outreach.sequence.version)
        return outreach

    @router.post(
        "/api/applications/{application_id}/outreach-sequences/{sequence_id}/messages",
        response_model=ApplicationOutreachResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def save_owner_application_outreach_message(
        application_id: OpaqueId,
        sequence_id: OpaqueId,
        payload: OutreachMessageCreate,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApplicationOutreachResponse:
        outreach = _invoke(
            _store(store).save_outreach_message,
            owner_id=owner.owner_id,
            application_id=application_id,
            sequence_id=sequence_id,
            payload=payload,
            expected_sequence_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        if outreach is None:
            _not_found("outreach sequence")
        if outreach.sequence is None:
            raise WorkspaceApiError(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "workspace_unavailable",
                "application outreach storage is unavailable",
                retryable=True,
            )
        _set_etag(response, outreach.sequence.version)
        return outreach

    @router.post(
        "/api/applications/{application_id}/outreach-sequences/{sequence_id}/events",
        response_model=ApplicationOutreachResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def record_owner_application_outreach_event(
        application_id: OpaqueId,
        sequence_id: OpaqueId,
        payload: OutreachEventCreate,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApplicationOutreachResponse:
        outreach = _invoke(
            _store(store).record_outreach_event,
            owner_id=owner.owner_id,
            application_id=application_id,
            sequence_id=sequence_id,
            payload=payload,
            expected_sequence_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        if outreach is None:
            _not_found("outreach sequence")
        if outreach.sequence is None:
            raise WorkspaceApiError(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "workspace_unavailable",
                "application outreach storage is unavailable",
                retryable=True,
            )
        _set_etag(response, outreach.sequence.version)
        return outreach

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
