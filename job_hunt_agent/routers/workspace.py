"""Owner-authenticated profile, evidence, track, and saved-search routes."""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from pathlib import PurePath
from typing import Any, TypeVar
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    Security,
    UploadFile,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyCookie
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..auth import load_owner_session, session_cookie_name
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
    ResumeUploadReport,
    SavedSearchCreate,
    SavedSearchHuntInputResponse,
    SavedSearchList,
    SavedSearchPatch,
    SavedSearchResponse,
    WorkspaceDeleteResponse,
)
from ..resume_ingestion import (
    MAX_RESUME_FILE_BYTES,
    ResumeIngestionError,
    parse_resume_upload,
)
from .session import AuthenticatedOwner, require_owner_mutation, require_owner_session


MAX_IDEMPOTENCY_KEY_CHARS = 200
MAX_RESUME_UPLOAD_REQUEST_BYTES = MAX_RESUME_FILE_BYTES + 1024 * 1024
OWNER_SESSION_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
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


class ResumeUploadBodyLimitMiddleware:
    """Bound multipart bytes before Starlette can spool an upload to disk."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int = MAX_RESUME_UPLOAD_REQUEST_BYTES,
        allowed_origins: list[str] | tuple[str, ...] = (),
        production: bool = False,
        database: Database | None = None,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.allowed_origins = frozenset(
            origin.rstrip("/") for origin in allowed_origins if origin
        )
        self.production = production
        self.session_cookie = session_cookie_name()
        self.database = database

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not (
            scope["type"] == "http"
            and scope.get("method") == "POST"
            and scope.get("path") == "/api/me/resume-versions/upload"
        ):
            await self.app(scope, receive, send)
            return

        origin = _request_origin(scope)
        cors_origin = _allowed_cors_origin(origin, self.allowed_origins)
        if (origin and cors_origin is None) or (not origin and self.production):
            await _send_resume_access_problem(
                scope,
                receive,
                send,
                status_code=403,
                code="origin_forbidden",
                message="request origin is not allowed",
            )
            return

        session_token = _session_cookie_token(scope, self.session_cookie)
        if session_token is None:
            await _send_resume_access_problem(
                scope,
                receive,
                send,
                status_code=401,
                code="owner_session_required",
                message="owner session required",
                cors_origin=cors_origin,
            )
            return

        if self.database is None:
            await _send_resume_access_problem(
                scope,
                receive,
                send,
                status_code=503,
                code="workspace_unavailable",
                message="owner workspace storage is unavailable",
                retryable=True,
                cors_origin=cors_origin,
            )
            return
        try:
            session_is_valid = await run_in_threadpool(
                _owner_session_is_valid,
                self.database,
                session_token,
            )
        except SQLAlchemyError:
            await _send_resume_access_problem(
                scope,
                receive,
                send,
                status_code=503,
                code="workspace_unavailable",
                message="owner workspace storage is unavailable",
                retryable=True,
                cors_origin=cors_origin,
            )
            return
        if not session_is_valid:
            await _send_resume_access_problem(
                scope,
                receive,
                send,
                status_code=401,
                code="owner_session_required",
                message="owner session required",
                cors_origin=cors_origin,
            )
            return

        declared_length = _content_length(scope)
        if declared_length is not None and declared_length > self.max_body_bytes:
            await _send_resume_body_too_large(
                scope,
                receive,
                send,
                cors_origin=cors_origin,
            )
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > self.max_body_bytes:
                await _send_resume_body_too_large(
                    scope,
                    receive,
                    send,
                    cors_origin=cors_origin,
                )
                return
            if not message.get("more_body", False):
                break

        delivered = False

        async def replay_receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)


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

    @router.post(
        "/api/me/resume-versions/upload",
        response_model=ResumeUploadReport,
        status_code=status.HTTP_201_CREATED,
        responses={
            **COMMON_ERROR_RESPONSES,
            413: {"model": ProblemResponse},
        },
    )
    async def upload_resume_version(
        response: Response,
        file: UploadFile = File(...),
        label: str | None = Form(default=None),
        set_as_base: bool = Form(default=True),
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ResumeUploadReport:
        mutation_key = _required_idempotency_key(idempotency_key)
        filename = file.filename or ""
        content_type = file.content_type
        upload_label = _resume_upload_label(filename=filename, label=label)
        try:
            content = await file.read(MAX_RESUME_FILE_BYTES + 1)
        finally:
            await file.close()
        if len(content) > MAX_RESUME_FILE_BYTES:
            _raise_resume_ingestion_problem(
                ResumeIngestionError(
                    "resume_too_large",
                    "Resume files must be 3 MB or smaller.",
                )
            )
        try:
            parsed = await run_in_threadpool(
                parse_resume_upload,
                content,
                filename=filename,
                content_type=content_type,
            )
        except ResumeIngestionError as exc:
            _raise_resume_ingestion_problem(exc)

        report = await run_in_threadpool(
            _invoke,
            _store(store).upload_resume_version,
            owner_id=owner.owner_id,
            parsed=parsed,
            label=upload_label,
            set_as_base=set_as_base,
            idempotency_key=mutation_key,
        )
        _set_etag(response, report.resume_version.version)
        return report

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


def install_workspace_error_handler(
    app: FastAPI,
    *,
    allowed_origins: list[str] | None = None,
    production: bool | None = None,
    database: Database | None = None,
) -> None:
    configured_origins = (
        allowed_origins
        if allowed_origins is not None
        else _configured_cors_origins(app)
    )
    app.add_middleware(
        ResumeUploadBodyLimitMiddleware,
        allowed_origins=configured_origins,
        production=(
            os.getenv("ENVIRONMENT", "").strip().lower() == "production"
            if production is None
            else production
        ),
        database=(
            database
            if database is not None
            else getattr(app.state, "practical_database", None)
        ),
    )
    app.add_exception_handler(WorkspaceApiError, workspace_error_handler)
    app.add_exception_handler(RequestValidationError, workspace_validation_error_handler)


def _configured_cors_origins(app: FastAPI) -> list[str]:
    """Reuse the app's CORS allowlist for direct middleware responses."""

    for middleware in app.user_middleware:
        if middleware.cls is not CORSMiddleware:
            continue
        configured = middleware.kwargs.get("allow_origins", ())
        return [origin for origin in configured if isinstance(origin, str)]
    return []


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


def _resume_upload_label(*, filename: str, label: str | None) -> str:
    supplied = label is not None
    if supplied:
        candidate = unicodedata.normalize("NFKC", label or "")
    else:
        safe_name = unicodedata.normalize("NFKC", filename).replace("\\", "/")
        candidate = PurePath(safe_name.rsplit("/", 1)[-1]).stem
        candidate = re.sub(r"[_-]+", " ", candidate)
    normalized = " ".join(candidate.split())
    if not supplied and len(normalized) > 120:
        normalized = normalized[:120].rstrip()
    if not normalized or len(normalized) > 120 or "\x00" in normalized:
        raise WorkspaceApiError(
            422,
            "resume_label_invalid",
            "Resume labels must be between 1 and 120 characters.",
            field_errors=[
                ProblemFieldError(
                    field="label",
                    message="Use a resume label between 1 and 120 characters.",
                )
            ],
        )
    return normalized


def _raise_resume_ingestion_problem(exc: ResumeIngestionError) -> None:
    raise WorkspaceApiError(
        413 if exc.code == "resume_too_large" else 422,
        exc.code,
        str(exc),
        field_errors=[ProblemFieldError(field="file", message=str(exc))],
    ) from exc


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name.lower() != b"content-length":
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None
    return None


def _request_origin(scope: Scope) -> str:
    values = [
        value
        for name, value in scope.get("headers", [])
        if name.lower() == b"origin"
    ]
    if not values:
        return ""
    if len(values) != 1:
        return "\x00invalid-origin"
    return values[0].decode("latin-1").strip().rstrip("/")


def _allowed_cors_origin(
    origin: str,
    allowed_origins: frozenset[str],
) -> str | None:
    if not origin or "\r" in origin or "\n" in origin or "\x00" in origin:
        return None
    if "*" in allowed_origins:
        return origin
    return origin if origin in allowed_origins else None


def _session_cookie_token(scope: Scope, cookie_name: str) -> str | None:
    cookie = SimpleCookie()
    try:
        for name, value in scope.get("headers", []):
            if name.lower() == b"cookie":
                cookie.load(value.decode("latin-1"))
    except (CookieError, UnicodeDecodeError):
        return None
    morsel = cookie.get(cookie_name)
    if morsel is None or OWNER_SESSION_TOKEN_PATTERN.fullmatch(morsel.value) is None:
        return None
    return morsel.value


def _owner_session_is_valid(database: Database, token: str) -> bool:
    with database.session() as session:
        return load_owner_session(session, token, touch=False) is not None


def _resume_direct_response_headers(
    request_id: str,
    *,
    cors_origin: str | None = None,
) -> dict[str, str]:
    headers = {
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
        "X-Request-ID": request_id,
    }
    if cors_origin is not None:
        headers.update(
            {
                "Access-Control-Allow-Origin": cors_origin,
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Expose-Headers": "X-Request-ID",
                "Vary": "Origin",
            }
        )
    return headers


async def _send_resume_access_problem(
    scope: Scope,
    receive: Receive,
    send: Send,
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool = False,
    cors_origin: str | None = None,
) -> None:
    request_id = uuid4().hex
    problem = ProblemResponse(
        code=code,
        message=message,
        retryable=retryable,
        request_id=request_id,
    )
    response = JSONResponse(
        status_code=status_code,
        content=problem.model_dump(mode="json", exclude_none=True),
        headers=_resume_direct_response_headers(
            request_id,
            cors_origin=cors_origin,
        ),
    )
    await response(scope, receive, send)


async def _send_resume_body_too_large(
    scope: Scope,
    receive: Receive,
    send: Send,
    *,
    cors_origin: str | None = None,
) -> None:
    request_id = uuid4().hex
    problem = ProblemResponse(
        code="resume_too_large",
        message="Resume files must be 3 MB or smaller.",
        retryable=False,
        request_id=request_id,
        field_errors=[
            ProblemFieldError(
                field="file",
                message="Choose a resume file that is 3 MB or smaller.",
            )
        ],
    )
    response = JSONResponse(
        status_code=413,
        content=problem.model_dump(mode="json"),
        headers=_resume_direct_response_headers(
            request_id,
            cors_origin=cors_origin,
        ),
    )
    await response(scope, receive, send)


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
    "MAX_RESUME_UPLOAD_REQUEST_BYTES",
    "ResumeUploadBodyLimitMiddleware",
    "WorkspaceApiError",
    "create_workspace_router",
    "install_workspace_error_handler",
    "is_workspace_api_path",
    "workspace_error_handler",
    "workspace_validation_error_handler",
]
