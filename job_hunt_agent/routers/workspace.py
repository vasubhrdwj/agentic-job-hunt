"""Owner-authenticated profile, evidence, track, and saved-search routes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    Security,
    status,
)
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyCookie
from fastapi.exceptions import RequestValidationError

from ..auth import session_cookie_name
from ..database import Database
from ..owner_workspace import (
    OwnerWorkspaceStore,
    WorkspaceCapabilityUnavailable,
    WorkspaceConflict,
    WorkspaceInputError,
    WorkspaceNotFound,
    WorkspaceUnavailable,
)
from ..profile_schemas import (
    AchievementEvidenceCreate,
    AchievementEvidenceList,
    AchievementEvidencePatch,
    AchievementEvidenceResponse,
    CandidateProfileResponse,
    CandidateProfileWrite,
    CareerTrackCreate,
    CareerTrackList,
    CareerTrackPatch,
    CareerTrackResponse,
    EvidenceApprovalState,
    OpaqueId,
    ProblemFieldError,
    ProblemResponse,
    ResumeVersionCreate,
    ResumeVersionDetail,
    ResumeVersionList,
    ResumeVersionSummary,
    SavedSearchCreate,
    SavedSearchHuntInputResponse,
    SavedSearchList,
    SavedSearchPatch,
    SavedSearchResponse,
    WorkspaceDeleteResponse,
)
from .session import AuthenticatedOwner, require_owner_mutation, require_owner_session


MAX_IDEMPOTENCY_KEY_CHARS = 200
COMMON_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ProblemResponse},
    401: {"model": ProblemResponse},
    403: {"model": ProblemResponse},
    404: {"model": ProblemResponse},
    409: {"model": ProblemResponse},
    422: {"model": ProblemResponse},
    428: {"model": ProblemResponse},
    503: {"model": ProblemResponse},
}
T = TypeVar("T")


@dataclass(frozen=True)
class WorkspaceApiError(Exception):
    status_code: int
    code: str
    message: str
    retryable: bool = False
    field_errors: list[ProblemFieldError] | None = None


def create_workspace_router(
    database: Database | None,
    store: OwnerWorkspaceStore | None,
    *,
    allowed_origins: list[str],
    production: bool,
) -> APIRouter:
    """Build practical-only routes against a transaction-owning data adapter."""

    def prevent_workspace_caching(response: Response) -> None:
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"

    router = APIRouter(
        tags=["owner-workspace"],
        dependencies=[Depends(prevent_workspace_caching)],
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
        "/api/me/profile",
        response_model=CandidateProfileResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def get_profile(
        response: Response,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> CandidateProfileResponse:
        profile = _invoke(_store(store).get_profile, owner_id=owner.owner_id)
        if profile is None:
            _not_found("candidate profile")
        _set_etag(response, profile.version)
        return profile

    @router.put(
        "/api/me/profile",
        response_model=CandidateProfileResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def put_profile(
        payload: CandidateProfileWrite,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> CandidateProfileResponse:
        profile = _invoke(
            _store(store).put_profile,
            owner_id=owner.owner_id,
            payload=payload,
            expected_version=_expected_version(if_match),
        )
        _set_etag(response, profile.version)
        return profile

    @router.get(
        "/api/me/resume-versions",
        response_model=ResumeVersionList,
        responses=COMMON_ERROR_RESPONSES,
    )
    def list_resume_versions(
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> ResumeVersionList:
        return _invoke(_store(store).list_resume_versions, owner_id=owner.owner_id)

    @router.post(
        "/api/me/resume-versions",
        response_model=ResumeVersionDetail,
        status_code=status.HTTP_201_CREATED,
        responses=COMMON_ERROR_RESPONSES,
    )
    def create_resume_version(
        payload: ResumeVersionCreate,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ResumeVersionDetail:
        resume = _invoke(
            _store(store).create_resume_version,
            owner_id=owner.owner_id,
            payload=payload,
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        _set_etag(response, resume.version)
        return resume

    @router.get(
        "/api/me/resume-versions/{resume_version_id}",
        response_model=ResumeVersionDetail,
        responses=COMMON_ERROR_RESPONSES,
    )
    def get_resume_version(
        resume_version_id: OpaqueId,
        response: Response,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> ResumeVersionDetail:
        resume = _invoke(
            _store(store).get_resume_version,
            owner_id=owner.owner_id,
            resume_version_id=resume_version_id,
        )
        if resume is None:
            _not_found("resume version")
        _set_etag(response, resume.version)
        return resume

    @router.post(
        "/api/me/resume-versions/{resume_version_id}/base",
        response_model=ResumeVersionSummary,
        responses=COMMON_ERROR_RESPONSES,
    )
    def set_base_resume(
        resume_version_id: OpaqueId,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ResumeVersionSummary:
        resume = _invoke(
            _store(store).set_base_resume,
            owner_id=owner.owner_id,
            resume_version_id=resume_version_id,
            expected_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        _set_etag(response, resume.version)
        return resume

    @router.get(
        "/api/me/evidence",
        response_model=AchievementEvidenceList,
        responses=COMMON_ERROR_RESPONSES,
    )
    def list_evidence(
        approval_state: EvidenceApprovalState | None = Query(default=None),
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> AchievementEvidenceList:
        return _invoke(
            _store(store).list_evidence,
            owner_id=owner.owner_id,
            approval_state=approval_state,
        )

    @router.post(
        "/api/me/evidence",
        response_model=AchievementEvidenceResponse,
        status_code=status.HTTP_201_CREATED,
        responses=COMMON_ERROR_RESPONSES,
    )
    def create_evidence(
        payload: AchievementEvidenceCreate,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> AchievementEvidenceResponse:
        evidence = _invoke(
            _store(store).create_evidence,
            owner_id=owner.owner_id,
            payload=payload,
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        _set_etag(response, evidence.version)
        return evidence

    @router.patch(
        "/api/me/evidence/{evidence_id}",
        response_model=AchievementEvidenceResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def patch_evidence(
        evidence_id: OpaqueId,
        payload: AchievementEvidencePatch,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> AchievementEvidenceResponse:
        evidence = _invoke(
            _store(store).patch_evidence,
            owner_id=owner.owner_id,
            evidence_id=evidence_id,
            payload=payload,
            expected_version=_expected_version(if_match),
        )
        _set_etag(response, evidence.version)
        return evidence

    @router.get(
        "/api/career-tracks",
        response_model=CareerTrackList,
        responses=COMMON_ERROR_RESPONSES,
    )
    def list_career_tracks(
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> CareerTrackList:
        return _invoke(_store(store).list_career_tracks, owner_id=owner.owner_id)

    @router.post(
        "/api/career-tracks",
        response_model=CareerTrackResponse,
        status_code=status.HTTP_201_CREATED,
        responses=COMMON_ERROR_RESPONSES,
    )
    def create_career_track(
        payload: CareerTrackCreate,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> CareerTrackResponse:
        track = _invoke(
            _store(store).create_career_track,
            owner_id=owner.owner_id,
            payload=payload,
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        _set_etag(response, track.version)
        return track

    @router.get(
        "/api/career-tracks/{career_track_id}",
        response_model=CareerTrackResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def get_career_track(
        career_track_id: OpaqueId,
        response: Response,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> CareerTrackResponse:
        track = _invoke(
            _store(store).get_career_track,
            owner_id=owner.owner_id,
            career_track_id=career_track_id,
        )
        if track is None:
            _not_found("career track")
        _set_etag(response, track.version)
        return track

    @router.patch(
        "/api/career-tracks/{career_track_id}",
        response_model=CareerTrackResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def patch_career_track(
        career_track_id: OpaqueId,
        payload: CareerTrackPatch,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> CareerTrackResponse:
        track = _invoke(
            _store(store).patch_career_track,
            owner_id=owner.owner_id,
            career_track_id=career_track_id,
            payload=payload,
            expected_version=_expected_version(if_match),
        )
        _set_etag(response, track.version)
        return track

    @router.delete(
        "/api/career-tracks/{career_track_id}",
        response_model=WorkspaceDeleteResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def delete_career_track(
        career_track_id: OpaqueId,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> WorkspaceDeleteResponse:
        _invoke(
            _store(store).delete_career_track,
            owner_id=owner.owner_id,
            career_track_id=career_track_id,
            expected_version=_expected_version(if_match),
        )
        return WorkspaceDeleteResponse()

    @router.get(
        "/api/saved-searches",
        response_model=SavedSearchList,
        responses=COMMON_ERROR_RESPONSES,
    )
    def list_saved_searches(
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> SavedSearchList:
        return _invoke(_store(store).list_saved_searches, owner_id=owner.owner_id)

    @router.post(
        "/api/saved-searches",
        response_model=SavedSearchResponse,
        status_code=status.HTTP_201_CREATED,
        responses=COMMON_ERROR_RESPONSES,
    )
    def create_saved_search(
        payload: SavedSearchCreate,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> SavedSearchResponse:
        saved_search = _invoke(
            _store(store).create_saved_search,
            owner_id=owner.owner_id,
            payload=payload,
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        _set_etag(response, saved_search.version)
        return saved_search

    @router.get(
        "/api/saved-searches/{saved_search_id}",
        response_model=SavedSearchResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def get_saved_search(
        saved_search_id: OpaqueId,
        response: Response,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> SavedSearchResponse:
        saved_search = _invoke(
            _store(store).get_saved_search,
            owner_id=owner.owner_id,
            saved_search_id=saved_search_id,
        )
        if saved_search is None:
            _not_found("saved search")
        _set_etag(response, saved_search.version)
        return saved_search

    @router.patch(
        "/api/saved-searches/{saved_search_id}",
        response_model=SavedSearchResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def patch_saved_search(
        saved_search_id: OpaqueId,
        payload: SavedSearchPatch,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> SavedSearchResponse:
        saved_search = _invoke(
            _store(store).patch_saved_search,
            owner_id=owner.owner_id,
            saved_search_id=saved_search_id,
            payload=payload,
            expected_version=_expected_version(if_match),
        )
        _set_etag(response, saved_search.version)
        return saved_search

    @router.delete(
        "/api/saved-searches/{saved_search_id}",
        response_model=WorkspaceDeleteResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def delete_saved_search(
        saved_search_id: OpaqueId,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> WorkspaceDeleteResponse:
        _invoke(
            _store(store).delete_saved_search,
            owner_id=owner.owner_id,
            saved_search_id=saved_search_id,
            expected_version=_expected_version(if_match),
        )
        return WorkspaceDeleteResponse()

    @router.get(
        "/api/saved-searches/{saved_search_id}/hunt-input",
        response_model=SavedSearchHuntInputResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def get_hunt_input(
        saved_search_id: OpaqueId,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> SavedSearchHuntInputResponse:
        projection = _invoke(
            _store(store).build_hunt_input,
            owner_id=owner.owner_id,
            saved_search_id=saved_search_id,
        )
        if projection is None:
            _not_found("saved search")
        return projection

    return router


def install_workspace_error_handler(app: FastAPI) -> None:
    app.add_exception_handler(WorkspaceApiError, workspace_error_handler)
    app.add_exception_handler(RequestValidationError, workspace_validation_error_handler)


async def workspace_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, WorkspaceApiError)
    request_id = getattr(request.state, "request_id", None) or uuid4().hex
    problem = ProblemResponse(
        code=exc.code,
        message=exc.message,
        retryable=exc.retryable,
        request_id=request_id,
        field_errors=exc.field_errors,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=problem.model_dump(mode="json", exclude_none=True),
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Request-ID": request_id,
        },
    )


async def workspace_validation_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    if not is_workspace_api_path(request.url.path):
        detail = [
            {
                key: value
                for key, value in error.items()
                if key not in {"ctx", "input", "url"}
            }
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": detail})

    request_id = getattr(request.state, "request_id", None) or uuid4().hex
    field_errors = [
        ProblemFieldError(
            field=_validation_field(error.get("loc")),
            message=str(error.get("msg") or "Invalid value"),
        )
        for error in exc.errors()
    ]
    problem = ProblemResponse(
        code="invalid_request",
        message="Request validation failed",
        retryable=False,
        request_id=request_id,
        field_errors=field_errors,
    )
    return JSONResponse(
        status_code=422,
        content=problem.model_dump(mode="json"),
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Request-ID": request_id,
        },
    )


def is_workspace_api_path(path: str) -> bool:
    return (
        path == "/api/me"
        or path.startswith("/api/me/")
        or path == "/api/career-tracks"
        or path.startswith("/api/career-tracks/")
        or path == "/api/saved-searches"
        or path.startswith("/api/saved-searches/")
        or path == "/api/scans"
        or path.startswith("/api/scans/")
        or path == "/api/today"
        or path.startswith("/api/today/")
        or path == "/api/opportunities"
        or path.startswith("/api/opportunities/")
        or path == "/api/applications"
        or path.startswith("/api/applications/")
        or path == "/api/review"
        or path.startswith("/api/review/")
        or path == "/api/privacy"
        or path.startswith("/api/privacy/")
    )


def _store(store: OwnerWorkspaceStore | None) -> OwnerWorkspaceStore:
    if store is None:
        raise WorkspaceApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "workspace_unavailable",
            "owner workspace storage is unavailable",
            retryable=True,
        )
    return store


def _invoke(operation: Callable[..., T], **kwargs: object) -> T:
    try:
        return operation(**kwargs)
    except WorkspaceNotFound as exc:
        raise WorkspaceApiError(404, "resource_not_found", "resource not found") from exc
    except WorkspaceConflict as exc:
        raise WorkspaceApiError(
            409,
            exc.code,
            str(exc),
            retryable=exc.code == "mutation_pending",
        ) from exc
    except WorkspaceInputError as exc:
        fields = (
            [ProblemFieldError(field=exc.field, message=str(exc))]
            if exc.field is not None
            else None
        )
        raise WorkspaceApiError(422, exc.code, str(exc), field_errors=fields) from exc
    except WorkspaceCapabilityUnavailable as exc:
        if exc.capability == "role_scan":
            raise WorkspaceApiError(
                503,
                "scan_worker_unavailable",
                (
                    "Role scans are temporarily paused because the scan service "
                    "is not ready yet."
                ),
                retryable=True,
            ) from exc
        if exc.capability == "contact_search":
            raise WorkspaceApiError(
                503,
                "contact_worker_unavailable",
                (
                    "Contact search is temporarily unavailable because no "
                    "compatible contact-search worker is ready."
                ),
                retryable=True,
            ) from exc
        raise WorkspaceApiError(
            503,
            "workspace_capability_unavailable",
            "A required background service is temporarily unavailable.",
            retryable=True,
        ) from exc
    except WorkspaceUnavailable as exc:
        raise WorkspaceApiError(
            503,
            "workspace_unavailable",
            "owner workspace storage is unavailable",
            retryable=True,
        ) from exc


def _not_found(label: str) -> None:
    raise WorkspaceApiError(404, "resource_not_found", f"{label} not found")


def _raise_auth_problem(exc: HTTPException) -> None:
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        raise WorkspaceApiError(401, "owner_session_required", "owner session required") from exc
    if exc.status_code == status.HTTP_403_FORBIDDEN:
        raise WorkspaceApiError(403, "origin_forbidden", "request origin is not allowed") from exc
    if exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
        raise WorkspaceApiError(
            503,
            "workspace_unavailable",
            "owner workspace storage is unavailable",
            retryable=True,
        ) from exc
    raise WorkspaceApiError(500, "internal_error", "owner authorization failed") from exc


def _required_idempotency_key(value: str | None) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise WorkspaceApiError(
            400,
            "idempotency_key_required",
            "Idempotency-Key header is required for create operations",
        )
    if len(normalized) > MAX_IDEMPOTENCY_KEY_CHARS:
        raise WorkspaceApiError(
            400,
            "invalid_idempotency_key",
            f"Idempotency-Key must be at most {MAX_IDEMPOTENCY_KEY_CHARS} characters",
        )
    return normalized


def _expected_version(value: str | None) -> int:
    if value is None:
        raise WorkspaceApiError(
            status.HTTP_428_PRECONDITION_REQUIRED,
            "precondition_required",
            'If-Match: "<version>" is required for this mutation',
        )
    normalized = value.strip()
    if (
        len(normalized) < 3
        or not normalized.startswith('"')
        or not normalized.endswith('"')
        or not normalized[1:-1].isdigit()
    ):
        raise WorkspaceApiError(
            400,
            "invalid_if_match",
            'If-Match must contain one strong integer ETag such as "3"',
        )
    return int(normalized[1:-1])


def _set_etag(response: Response, version: int) -> None:
    response.headers["ETag"] = f'"{version}"'
    response.headers["Cache-Control"] = "no-store, max-age=0"


def _validation_field(location: object) -> str:
    if not isinstance(location, (tuple, list)):
        return "request"
    parts = [str(part) for part in location if str(part) not in {"body", "query", "path"}]
    return ".".join(parts) or "request"


__all__ = [
    "WorkspaceApiError",
    "create_workspace_router",
    "install_workspace_error_handler",
    "is_workspace_api_path",
    "workspace_error_handler",
    "workspace_validation_error_handler",
]
