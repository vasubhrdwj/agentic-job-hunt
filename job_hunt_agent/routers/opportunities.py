"""Authenticated manual-scan, Today, and opportunity decision routes."""

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

from ..auth import session_cookie_name
from ..database import Database
from ..opportunity_schemas import (
    CursorToken,
    OpportunityDecisionRequest,
    OpportunityDecisionResponse,
    OpportunityDetailResponse,
    OpportunityLane,
    OpaqueId,
    ScanCreateRequest,
    ScanCreateResponse,
    ScanStatusResponse,
    TodayListResponse,
    TodayQuery,
    TodayView,
)
from ..opportunity_workspace import OpportunityWorkspaceStore
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


def create_opportunity_router(
    database: Database | None,
    store: OpportunityWorkspaceStore | None,
    *,
    allowed_origins: list[str],
    production: bool,
) -> APIRouter:
    """Build the practical opportunity routes against a transaction owner."""

    def prevent_private_caching(response: Response) -> None:
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"

    router = APIRouter(
        tags=["opportunity-radar"],
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

    @router.post(
        "/api/saved-searches/{saved_search_id}/scans",
        response_model=ScanCreateResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses=COMMON_ERROR_RESPONSES,
    )
    def create_scan(
        saved_search_id: OpaqueId,
        payload: ScanCreateRequest,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ScanCreateResponse:
        scan = _invoke(
            _store(store).create_scan,
            owner_id=owner.owner_id,
            saved_search_id=saved_search_id,
            expected_saved_search_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
            payload=payload,
        )
        _set_etag(response, scan.version)
        return scan

    @router.get(
        "/api/scans/{scan_id}",
        response_model=ScanStatusResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def get_scan(
        scan_id: OpaqueId,
        response: Response,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> ScanStatusResponse:
        scan = _invoke(_store(store).get_scan, owner_id=owner.owner_id, scan_id=scan_id)
        if scan is None:
            _not_found("scan")
        _set_etag(response, scan.version)
        return scan

    @router.get(
        "/api/today",
        response_model=TodayListResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def list_today(
        view: TodayView = Query(default=TodayView.inbox),
        saved_search_id: OpaqueId | None = Query(default=None),
        lane: OpportunityLane | None = Query(default=None),
        cursor: CursorToken | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=50),
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> TodayListResponse:
        query = TodayQuery(
            view=view,
            saved_search_id=saved_search_id,
            lane=lane,
            cursor=cursor,
            limit=limit,
        )
        return _invoke(_store(store).list_today, owner_id=owner.owner_id, query=query)

    @router.get(
        "/api/opportunities/{opportunity_id}",
        response_model=OpportunityDetailResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def get_opportunity(
        opportunity_id: OpaqueId,
        response: Response,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> OpportunityDetailResponse:
        opportunity = _invoke(
            _store(store).get_opportunity,
            owner_id=owner.owner_id,
            opportunity_id=opportunity_id,
        )
        if opportunity is None:
            _not_found("opportunity")
        _set_etag(response, opportunity.version)
        return opportunity

    @router.post(
        "/api/opportunities/{opportunity_id}/decision",
        response_model=OpportunityDecisionResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def decide_opportunity(
        opportunity_id: OpaqueId,
        payload: OpportunityDecisionRequest,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> OpportunityDecisionResponse:
        decision = _invoke(
            _store(store).decide_opportunity,
            owner_id=owner.owner_id,
            opportunity_id=opportunity_id,
            expected_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
            payload=payload,
        )
        _set_etag(response, decision.opportunity_version)
        return decision

    return router


def _store(store: OpportunityWorkspaceStore | None) -> OpportunityWorkspaceStore:
    if store is None:
        raise WorkspaceApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "workspace_unavailable",
            "opportunity workspace storage is unavailable",
            retryable=True,
        )
    return store


__all__ = ["create_opportunity_router"]
