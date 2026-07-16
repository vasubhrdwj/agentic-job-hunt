"""FastAPI surface for the queued job-hunt pipeline.

The frontend POSTs ``/api/hunt`` to enqueue an encrypted request. Practical
workspaces authorize every run through the owner's opaque cookie and durable
owner scope. The returned run capability remains only for the development-only
legacy API compatibility path.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Iterable, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, Security, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from . import hunt_repository, persistence, privacy_repository
from .database import DatabaseConfigError, database_from_env
from .requests import HuntRequestPayload, canonical_request_json
from .production_runtime import production_runtime_errors
from .routers.health import create_health_router
from .routers.privacy import PRIVACY_RECEIPT_SECRET_ENV, create_privacy_router
from .routers.applications import create_application_router
from .routers.opportunities import create_opportunity_router
from .routers.workspace import (
    create_workspace_router,
    install_workspace_error_handler,
)
from .auth import session_cookie_name
from .routers.session import (
    AuthenticatedOwner,
    create_session_router,
    require_migrated_database,
    require_owner_mutation,
    require_owner_session,
)
from .schemas import HuntResult, OutcomeLog
from .security import (
    REQUEST_RETENTION_HOURS,
    RUN_RETENTION_DAYS,
    SecurityConfigError,
    generate_access_token,
    hash_access_token,
    load_data_keyring,
)
from .sources.registry import RegistryError, load_company_pack
from .sqlalchemy_owner_workspace import SqlAlchemyOwnerWorkspaceStore
from .sqlalchemy_application_workspace import SqlAlchemyApplicationWorkspaceStore
from .sqlalchemy_opportunity_workspace import SqlAlchemyOpportunityWorkspaceStore
from .legacy_policy import (
    is_legacy_hunt_path,
    legacy_deprecation_headers,
    legacy_hunt_api_mode,
    legacy_request_problem,
)


TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
FALSY_ENV_VALUES = {"0", "false", "no", "off"}
DEFAULT_ALLOWED_ORIGINS = (
    "http://localhost:3000,"
    "http://127.0.0.1:3000,"
    "http://localhost:5173"
)
LOCAL_ORIGIN_MARKERS = ("localhost", "127.0.0.1", "[::1]")
DEFAULT_RETENTION_CLEANUP_INTERVAL_SECONDS = 3600.0
MAX_IDEMPOTENCY_KEY_CHARS = 200
LOGGER = logging.getLogger(__name__)
RunStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "dead_letter",
]


class OutcomesRequest(BaseModel):
    outcomes: list[OutcomeLog] = Field(
        description=(
            "Outcome entries to append. The owning run_id comes from the URL "
            "path, not from the entries themselves."
        ),
    )


class OutcomesResponse(BaseModel):
    ok: bool
    inserted: int
    outcomes: list[OutcomeLog]


class RunStateResponse(BaseModel):
    run_id: str
    status: RunStatus
    stage: str
    attempt_count: int
    max_attempts: int
    queued_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    cancelled_at: str | None = None
    failed_at: str | None = None
    dead_lettered_at: str | None = None
    last_error: str | None = None


class RunDetailResponse(RunStateResponse):
    hunt_result: HuntResult | None = None
    outcomes: list[OutcomeLog] = Field(default_factory=list)


class HuntCreatedResponse(RunStateResponse):
    access_token: str = Field(
        description=(
            "Legacy-compatible run capability. Practical workspaces authorize "
            "through the owner session and do not require this token."
        ),
    )
    reused: bool = Field(
        default=False,
        description="True when an idempotency key returned an existing run.",
    )


class HealthResponse(BaseModel):
    ok: bool


class DeleteResponse(BaseModel):
    ok: bool


class RequeueRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


def _env_bool(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in TRUTHY_ENV_VALUES:
        return True
    if normalized in FALSY_ENV_VALUES:
        return False
    return default


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "").strip().lower() == "production"


def _practical_mode_enabled() -> bool:
    # Public paid-provider submission must be an explicit development-only
    # compatibility choice. Production defaults to the private workspace.
    return _env_bool("ENABLE_PRACTICAL_MODE", default=_is_production())


def _retention_cleanup_interval_seconds() -> float:
    raw = os.getenv("RETENTION_CLEANUP_INTERVAL_SECONDS", "").strip()
    if not raw:
        return DEFAULT_RETENTION_CLEANUP_INTERVAL_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_RETENTION_CLEANUP_INTERVAL_SECONDS


def _parse_allowed_origins() -> list[str]:
    raw = os.getenv("ALLOWED_ORIGINS", DEFAULT_ALLOWED_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _production_origin_error(origin: str) -> str | None:
    """Return why a production CORS entry is not an exact HTTPS origin."""

    try:
        parsed = urlsplit(origin)
        parsed.port
    except ValueError:
        return "is not a valid URL"
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return "must use HTTPS"
    if parsed.username is not None or parsed.password is not None:
        return "must not include credentials"
    if parsed.path or parsed.query or parsed.fragment:
        return "must be an origin only, without a path, query, or fragment"
    return None


def _validate_production_config() -> None:
    # Validate the enum and deprecation metadata even outside production so a
    # typo cannot silently reopen or hide the compatibility API.
    legacy_mode = legacy_hunt_api_mode(production=_is_production())
    legacy_deprecation_headers(legacy_mode, production=_is_production())
    if not _is_production():
        return

    errors = production_runtime_errors()
    if not os.getenv("ALLOWED_ORIGINS", "").strip():
        errors.append("ALLOWED_ORIGINS is required when ENVIRONMENT=production")
    if len(os.getenv(PRIVACY_RECEIPT_SECRET_ENV, "").strip()) < 32:
        errors.append(
            f"{PRIVACY_RECEIPT_SECRET_ENV} must be a stable 32+ character "
            "secret when ENVIRONMENT=production"
        )

    if _practical_mode_enabled():
        for name in ("JOB_HUNT_OWNER_ID", "JOB_HUNT_OWNER_TOKEN_HASH"):
            if not os.getenv(name, "").strip():
                errors.append(f"{name} is required when practical mode is enabled")

    allowed_origins = _parse_allowed_origins()
    if "*" in allowed_origins:
        errors.append("ALLOWED_ORIGINS must name the Vercel URL; '*' is not allowed in production")
    for origin in allowed_origins:
        if any(marker in origin for marker in LOCAL_ORIGIN_MARKERS):
            errors.append("ALLOWED_ORIGINS must not include localhost in production")
            break
        origin_error = _production_origin_error(origin)
        if origin_error is not None:
            errors.append(f"ALLOWED_ORIGINS entry {origin!r} {origin_error}")

    if errors:
        raise RuntimeError("Invalid production config: " + "; ".join(errors))


def _unknown_draft_ids(
    outcomes: Iterable[OutcomeLog],
    known: set[str],
) -> set[str]:
    return {entry.draft_id for entry in outcomes if entry.draft_id not in known}


def _request_hash(canonical_json: str) -> str:
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _idempotency_hash(idempotency_key: str | None) -> str | None:
    if idempotency_key is None:
        return None
    normalized = idempotency_key.strip()
    if not normalized:
        return None
    if len(normalized) > MAX_IDEMPOTENCY_KEY_CHARS:
        raise HTTPException(status_code=400, detail="idempotency key is too long")
    return hash_access_token(normalized)


def _safe_access_token(header_token: str | None) -> str:
    token = (header_token or "").strip()
    if not token:
        return generate_access_token()
    if len(token) < 32:
        raise HTTPException(status_code=400, detail="client run access token is too short")
    return token


def _state_response(state: persistence.RunQueueState) -> dict[str, object]:
    return {
        "run_id": state.run_id,
        "status": state.status,
        "stage": state.stage,
        "attempt_count": state.attempt_count,
        "max_attempts": state.max_attempts,
        "queued_at": state.queued_at,
        "started_at": state.started_at,
        "completed_at": state.completed_at,
        "cancelled_at": state.cancelled_at,
        "failed_at": state.failed_at,
        "dead_lettered_at": state.dead_lettered_at,
        "last_error": state.last_error,
    }


def _practical_state_response(state: hunt_repository.HuntState) -> dict[str, object]:
    """Serialize durable queue state without exposing owner/job internals."""

    return {
        "run_id": state.run_id,
        "status": state.status,
        "stage": state.stage,
        "attempt_count": state.attempt_count,
        "max_attempts": state.max_attempts,
        "queued_at": state.created_at.isoformat(),
        "started_at": _optional_datetime_text(state.started_at),
        "completed_at": _optional_datetime_text(state.completed_at),
        "cancelled_at": _optional_datetime_text(state.cancelled_at),
        "failed_at": _optional_datetime_text(state.failed_at),
        "dead_lettered_at": _optional_datetime_text(state.dead_lettered_at),
        "last_error": state.last_error,
    }


def _optional_datetime_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _is_expired(value: datetime, now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return normalized.astimezone(timezone.utc) <= current.astimezone(timezone.utc)


def create_app() -> FastAPI:
    """Application factory. Tests use this to swap the SQLite path per run."""
    _validate_production_config()
    app = FastAPI(title="Job Hunt Signal API", version="0.4.0")
    practical_mode = _practical_mode_enabled()
    legacy_mode = legacy_hunt_api_mode(production=_is_production())
    legacy_headers = legacy_deprecation_headers(
        legacy_mode,
        production=_is_production(),
    )
    allowed_origins = _parse_allowed_origins()
    owner_cookie = APIKeyCookie(
        name=session_cookie_name(),
        scheme_name="OwnerSessionCookie",
        description="Opaque HttpOnly session issued by POST /api/session.",
        auto_error=False,
    )
    run_bearer = HTTPBearer(
        scheme_name="RunCapability",
        description="Private bearer capability returned when a hunt is created.",
        auto_error=False,
    )
    operator_bearer = HTTPBearer(
        scheme_name="OperatorCapability",
        description="Operator-only bearer capability for dead-letter recovery.",
        auto_error=False,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["DELETE", "GET", "HEAD", "PATCH", "POST", "PUT"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "If-Match",
            "Origin",
            "X-Run-Access-Token",
            "X-CSRF-Token",
        ],
        expose_headers=[
            "Deprecation",
            "ETag",
            "Link",
            "Sunset",
            "X-Legacy-Hunt-Mode",
            "X-Request-ID",
        ],
    )

    @app.middleware("http")
    async def enforce_legacy_hunt_policy(request: Request, call_next):
        if not is_legacy_hunt_path(request.url.path):
            return await call_next(request)
        request_id = getattr(request.state, "request_id", None) or uuid4().hex
        request.state.request_id = request_id
        problem = legacy_request_problem(
            legacy_mode,
            method=request.method,
            path=request.url.path,
        )
        if problem is not None:
            problem["request_id"] = request_id
            return JSONResponse(
                status_code=status.HTTP_410_GONE,
                content=problem,
                headers={
                    **legacy_headers,
                    "Cache-Control": "no-store, max-age=0",
                    "Pragma": "no-cache",
                    "X-Request-ID": request_id,
                },
                media_type="application/problem+json",
            )
        response = await call_next(request)
        for name, value in legacy_headers.items():
            response.headers[name] = value
        response.headers.setdefault("X-Request-ID", request_id)
        return response

    try:
        practical_database = database_from_env(required=False)
    except DatabaseConfigError as exc:
        raise RuntimeError(f"Invalid durable database config: {exc}") from exc
    app.state.practical_database = practical_database
    app.include_router(
        create_session_router(
            practical_database,
            allowed_origins=allowed_origins,
            production=_is_production(),
        )
    )
    app.include_router(create_health_router(practical_database))

    @app.middleware("http")
    async def prevent_private_response_caching(request: Request, call_next):
        nonlocal last_retention_cleanup_at
        now = datetime.now(timezone.utc)
        if request.url.path != "/health" and (
            retention_cleanup_interval == 0
            or (now - last_retention_cleanup_at).total_seconds()
            >= retention_cleanup_interval
        ):
            cleanup_succeeded = False
            try:
                if practical_mode:
                    if (
                        practical_database is not None
                        and practical_database.migrations_current()
                    ):
                        with practical_database.session() as session:
                            hunt_repository.purge_expired_hunts(session, now=now)
                            privacy_repository.purge_configured_hunts(session, now=now)
                        cleanup_succeeded = True
                else:
                    persistence.purge_expired_data(now=now)
                    cleanup_succeeded = True
            except SQLAlchemyError as exc:
                # Retention is retried on a later request. Liveness must remain
                # available while readiness reports the durable outage.
                LOGGER.warning(
                    "durable hunt retention failed error_type=%s",
                    type(exc).__name__,
                )
            if cleanup_succeeded:
                last_retention_cleanup_at = now
        response = await call_next(request)
        if (
            request.url.path in {"/api/hunt", "/api/session", "/api/health", "/ready"}
            or request.url.path.startswith("/api/runs/")
            or request.url.path.startswith("/api/me/")
            or request.url.path.startswith("/api/career-tracks")
            or request.url.path.startswith("/api/saved-searches")
            or request.url.path.startswith("/api/scans")
            or request.url.path == "/api/today"
            or request.url.path.startswith("/api/today/")
            or request.url.path.startswith("/api/opportunities")
            or request.url.path.startswith("/api/applications")
            or request.url.path.startswith("/api/review")
            or request.url.path.startswith("/api/privacy")
        ):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.exception_handler(RequestValidationError)
    async def sanitized_validation_error(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        detail = [
            {
                key: value
                for key, value in error.items()
                if key not in {"ctx", "input", "url"}
            }
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": detail})

    @app.exception_handler(SQLAlchemyError)
    async def sanitized_database_error(
        _request: Request,
        exc: SQLAlchemyError,
    ) -> JSONResponse:
        LOGGER.warning("durable database request failed error_type=%s", type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "durable database is unavailable"},
        )

    if not practical_mode:
        persistence.init_db()
        persistence.purge_expired_data()
    retention_cleanup_interval = _retention_cleanup_interval_seconds()
    last_retention_cleanup_at = (
        datetime.min.replace(tzinfo=timezone.utc)
        if practical_mode
        else datetime.now(timezone.utc)
    )
    try:
        data_keyring = load_data_keyring(production=_is_production())
    except SecurityConfigError as exc:
        raise RuntimeError(f"Invalid production config: {exc}") from exc

    workspace_store = (
        SqlAlchemyOwnerWorkspaceStore(practical_database, data_keyring)
        if practical_mode and practical_database is not None
        else None
    )
    opportunity_store = (
        SqlAlchemyOpportunityWorkspaceStore(practical_database, data_keyring)
        if practical_mode and practical_database is not None
        else None
    )
    application_store = (
        SqlAlchemyApplicationWorkspaceStore(practical_database, data_keyring)
        if practical_mode and practical_database is not None
        else None
    )
    app.state.owner_workspace_store = workspace_store
    app.state.opportunity_workspace_store = opportunity_store
    app.state.application_workspace_store = application_store
    app.state.contact_workspace_store = application_store
    if practical_mode:
        app.include_router(
            create_privacy_router(
                practical_database,
                data_keyring,
                allowed_origins=allowed_origins,
                production=_is_production(),
            )
        )
    if practical_mode:
        app.include_router(
            create_workspace_router(
                practical_database,
                workspace_store,
                allowed_origins=allowed_origins,
                production=_is_production(),
            )
        )
        app.include_router(
            create_opportunity_router(
                practical_database,
                opportunity_store,
                allowed_origins=allowed_origins,
                production=_is_production(),
            )
        )
        app.include_router(
            create_application_router(
                practical_database,
                application_store,
                allowed_origins=allowed_origins,
                production=_is_production(),
            )
        )
        install_workspace_error_handler(app)

    use_mocks = _env_bool("USE_MOCKS", default=False)

    def require_run_access(run_id: str, authorization: str | None) -> None:
        if not authorization:
            raise HTTPException(status_code=401, detail="run access token required")
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(status_code=401, detail="invalid run authorization")
        if not persistence.authorize_run(run_id, hash_access_token(token.strip())):
            raise HTTPException(status_code=404, detail="run not found")

    def require_operator_access(authorization: str | None) -> None:
        configured_hash = os.getenv("JOB_HUNT_OPERATOR_TOKEN_HASH", "").strip()
        if not configured_hash:
            raise HTTPException(status_code=503, detail="operator requeue is not configured")
        if not authorization:
            raise HTTPException(status_code=401, detail="operator token required")
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(status_code=401, detail="invalid operator authorization")
        if not hmac.compare_digest(configured_hash, hash_access_token(token.strip())):
            raise HTTPException(status_code=403, detail="operator access denied")

    def bearer_header(credentials: HTTPAuthorizationCredentials | None) -> str | None:
        if credentials is None:
            return None
        return f"{credentials.scheme} {credentials.credentials}"

    def require_practical_hunt_owner(
        raw_request: Request,
        _session_cookie: str | None = Security(owner_cookie),
    ) -> AuthenticatedOwner:
        """Protect provider-consuming work with the private owner session."""

        return require_owner_mutation(
            practical_database,
            raw_request,
            allowed_origins=allowed_origins,
            production=_is_production(),
        )

    def require_practical_run_read(
        raw_request: Request,
        _session_cookie: str | None = Security(owner_cookie),
    ) -> AuthenticatedOwner:
        return require_owner_session(practical_database, raw_request)

    def require_practical_run_mutation(
        raw_request: Request,
        _session_cookie: str | None = Security(owner_cookie),
    ) -> AuthenticatedOwner:
        return require_owner_mutation(
            practical_database,
            raw_request,
            allowed_origins=allowed_origins,
            production=_is_production(),
        )

    def allow_legacy_hunt() -> None:
        return None

    def require_legacy_run_capability(
        run_id: str,
        credentials: HTTPAuthorizationCredentials | None = Security(run_bearer),
    ) -> None:
        require_run_access(run_id, bearer_header(credentials))

    def require_legacy_operator_capability(
        credentials: HTTPAuthorizationCredentials | None = Security(operator_bearer),
    ) -> None:
        require_operator_access(bearer_header(credentials))

    hunt_owner_dependency = (
        require_practical_hunt_owner if practical_mode else allow_legacy_hunt
    )
    run_read_dependency = (
        require_practical_run_read if practical_mode else require_legacy_run_capability
    )
    run_mutation_dependency = (
        require_practical_run_mutation
        if practical_mode
        else require_legacy_run_capability
    )
    requeue_dependency = (
        require_practical_run_mutation
        if practical_mode
        else require_legacy_operator_capability
    )

    @app.get("/health", response_model=HealthResponse, operation_id="health_liveness")
    def health() -> HealthResponse:
        """Lightweight liveness check for the deployment platform.

        HEAD is allowed because uptime monitors (UptimeRobot) probe with it.
        """
        return HealthResponse(ok=True)

    @app.head("/health", include_in_schema=False)
    def health_head() -> Response:
        return Response(status_code=200)

    @app.post(
        "/api/hunt",
        response_model=HuntCreatedResponse,
        status_code=status.HTTP_202_ACCEPTED,
        deprecated=True,
    )
    def post_hunt(
        payload: HuntRequestPayload,
        owner: AuthenticatedOwner | None = Depends(hunt_owner_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        run_access_token: str | None = Header(default=None, alias="X-Run-Access-Token"),
    ) -> HuntCreatedResponse:
        """Persist an encrypted request and return immediately with queued state."""

        if not use_mocks:
            try:
                load_company_pack(payload.pack)
            except RegistryError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        canonical_json = canonical_request_json(payload)
        request_fingerprint = _request_hash(canonical_json)
        idempotency_key_hash = _idempotency_hash(idempotency_key)
        access_token = _safe_access_token(run_access_token)

        if practical_mode:
            assert owner is not None
            now = datetime.now(timezone.utc)
            database = require_migrated_database(practical_database)
            try:
                with database.session() as session:
                    # Idempotency is bounded by run retention. Purge first so
                    # an expired key cannot resurrect or extend an old run.
                    hunt_repository.purge_expired_hunts(session, now=now)
                    privacy_repository.purge_configured_hunts(session, now=now)
                    retention_days = privacy_repository.get_owner_hunt_retention_days(
                        session,
                        owner_id=owner.owner_id,
                    )
                    created = hunt_repository.create_or_reuse_hunt(
                        session,
                        owner_id=owner.owner_id,
                        request_json=canonical_json,
                        request_hash=request_fingerprint,
                        access_hash=hash_access_token(access_token),
                        keyring=data_keyring,
                        request_expires_at=now + timedelta(hours=REQUEST_RETENTION_HOURS),
                        access_expires_at=now + timedelta(days=retention_days),
                        idempotency_key_hash=idempotency_key_hash,
                        actor=f"owner:{owner.owner_id}",
                        now=now,
                    )
            except hunt_repository.IdempotencyConflict as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except hunt_repository.HuntRepositoryError as exc:
                LOGGER.error(
                    "durable hunt creation invariant failed error_type=%s",
                    type(exc).__name__,
                )
                raise HTTPException(
                    status_code=500,
                    detail="durable hunt state is inconsistent",
                ) from exc
            return HuntCreatedResponse(
                **_practical_state_response(created.state),
                access_token=access_token,
                reused=created.reused,
            )

        if idempotency_key_hash is not None:
            hit = persistence.find_run_by_idempotency_key(idempotency_key_hash)
            if hit is not None:
                if hit.request_hash != request_fingerprint:
                    raise HTTPException(
                        status_code=409,
                        detail="idempotency key was already used for a different request",
                    )
                persistence.replace_access_hash(
                    hit.state.run_id,
                    access_hash=hash_access_token(access_token),
                )
                return HuntCreatedResponse(
                    **_state_response(hit.state),
                    access_token=access_token,
                    reused=True,
                )

        new_run_id = uuid4().hex
        now = datetime.now(timezone.utc)
        encrypted_request = data_keyring.encrypt(canonical_json)
        state = persistence.create_run_security(
            new_run_id,
            access_hash=hash_access_token(access_token),
            encrypted_request=encrypted_request.ciphertext,
            encryption_key_id=encrypted_request.key_id,
            request_expires_at=now + timedelta(hours=REQUEST_RETENTION_HOURS),
            access_expires_at=now + timedelta(days=RUN_RETENTION_DAYS),
            idempotency_key_hash=idempotency_key_hash,
            request_hash=request_fingerprint,
        )
        return HuntCreatedResponse(
            **_state_response(state),
            access_token=access_token,
            reused=False,
        )

    @app.post(
        "/api/runs/{run_id}/outcomes",
        response_model=OutcomesResponse,
        deprecated=True,
    )
    def post_outcomes(
        run_id: str,
        request: OutcomesRequest,
        owner: AuthenticatedOwner | None = Depends(run_mutation_dependency),
    ) -> OutcomesResponse:
        """Append user-logged outcomes for a completed hunt."""
        if practical_mode:
            assert owner is not None
            database = require_migrated_database(practical_database)
            with database.session() as session:
                state = hunt_repository.load_hunt_state(
                    session,
                    owner_id=owner.owner_id,
                    hunt_run_id=run_id,
                )
                if state is None:
                    raise HTTPException(status_code=404, detail="run not found")
                if state.status != "succeeded":
                    raise HTTPException(
                        status_code=409,
                        detail="outcomes can only be logged after a run succeeds",
                    )
                stored = hunt_repository.load_hunt_result(
                    session,
                    owner_id=owner.owner_id,
                    hunt_run_id=run_id,
                    keyring=data_keyring,
                )
                if stored is None:
                    raise HTTPException(status_code=404, detail="run not found")
                known_draft_ids = {draft.draft_id for draft in stored.outreach}
                unknown = _unknown_draft_ids(request.outcomes, known_draft_ids)
                if unknown:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "message": "outcomes reference draft_ids not in this run",
                            "unknown_draft_ids": sorted(unknown),
                        },
                    )
                try:
                    inserted = hunt_repository.append_hunt_outcomes(
                        session,
                        owner_id=owner.owner_id,
                        hunt_run_id=run_id,
                        outcomes=request.outcomes,
                        keyring=data_keyring,
                    )
                except hunt_repository.HuntStateConflict as exc:
                    raise HTTPException(
                        status_code=409,
                        detail="outcomes can only be logged after a run succeeds",
                    ) from exc
            return OutcomesResponse(ok=True, inserted=len(inserted), outcomes=inserted)

        state = persistence.get_run_state(run_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"run_id {run_id!r} not found")
        if state.status != "succeeded":
            raise HTTPException(
                status_code=409,
                detail="outcomes can only be logged after a run succeeds",
            )
        stored = persistence.load_run(run_id)
        if stored is None:
            raise HTTPException(status_code=404, detail=f"run_id {run_id!r} not found")

        known_draft_ids = {draft.draft_id for draft in stored.outreach}
        unknown = _unknown_draft_ids(request.outcomes, known_draft_ids)
        if unknown:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "outcomes reference draft_ids not in this run",
                    "unknown_draft_ids": sorted(unknown),
                },
            )

        inserted = persistence.save_outcomes(run_id, request.outcomes)
        return OutcomesResponse(ok=True, inserted=len(inserted), outcomes=inserted)

    @app.get(
        "/api/runs/{run_id}",
        response_model=RunDetailResponse,
        deprecated=True,
    )
    def get_run(
        run_id: str,
        owner: AuthenticatedOwner | None = Depends(run_read_dependency),
    ) -> RunDetailResponse:
        """Return queue state, plus result/outcomes after the run succeeds."""
        if practical_mode:
            assert owner is not None
            database = require_migrated_database(practical_database)
            expired = False
            with database.session() as session:
                state = hunt_repository.load_hunt_state(
                    session,
                    owner_id=owner.owner_id,
                    hunt_run_id=run_id,
                )
                if state is not None and _is_expired(state.access_expires_at):
                    hunt_repository.delete_hunt(
                        session,
                        owner_id=owner.owner_id,
                        hunt_run_id=run_id,
                    )
                    expired = True
                    stored = None
                    outcomes = []
                elif state is not None:
                    stored = hunt_repository.load_hunt_result(
                        session,
                        owner_id=owner.owner_id,
                        hunt_run_id=run_id,
                        keyring=data_keyring,
                    )
                    outcomes = hunt_repository.load_hunt_outcomes(
                        session,
                        owner_id=owner.owner_id,
                        hunt_run_id=run_id,
                        keyring=data_keyring,
                    )
                else:
                    stored = None
                    outcomes = []
            if state is None or expired:
                raise HTTPException(status_code=404, detail="run not found")
            return RunDetailResponse(
                **_practical_state_response(state),
                hunt_result=stored,
                outcomes=outcomes,
            )

        state = persistence.get_run_state(run_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"run_id {run_id!r} not found")
        stored = persistence.load_run(run_id)
        outcomes = persistence.load_outcomes(run_id) if stored is not None else []
        return RunDetailResponse(
            **_state_response(state),
            hunt_result=stored,
            outcomes=outcomes,
        )

    @app.post(
        "/api/runs/{run_id}/cancel",
        response_model=RunStateResponse,
        deprecated=True,
    )
    def cancel_run(
        run_id: str,
        owner: AuthenticatedOwner | None = Depends(run_mutation_dependency),
    ) -> RunStateResponse:
        """Cancel a queued/running run through its authorized workspace."""
        if practical_mode:
            assert owner is not None
            database = require_migrated_database(practical_database)
            with database.session() as session:
                state = hunt_repository.cancel_hunt(
                    session,
                    owner_id=owner.owner_id,
                    hunt_run_id=run_id,
                    actor=f"owner:{owner.owner_id}",
                )
                if state is None:
                    raise HTTPException(status_code=404, detail="run not found")
            return RunStateResponse(**_practical_state_response(state))

        state = persistence.cancel_run(run_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"run_id {run_id!r} not found")
        return RunStateResponse(**_state_response(state))

    @app.post(
        "/api/runs/{run_id}/requeue",
        response_model=RunStateResponse,
        deprecated=True,
    )
    def requeue_run(
        run_id: str,
        request: RequeueRequest,
        owner: AuthenticatedOwner | None = Depends(requeue_dependency),
    ) -> RunStateResponse:
        """Move a dead-letter run back to queued through an authorized action."""
        if practical_mode:
            assert owner is not None
            database = require_migrated_database(practical_database)
            with database.session() as session:
                state = hunt_repository.requeue_hunt_dead_letter(
                    session,
                    owner_id=owner.owner_id,
                    hunt_run_id=run_id,
                    actor=f"owner:{owner.owner_id}",
                    reason=request.reason,
                )
                if state is None:
                    raise HTTPException(status_code=404, detail="run not found")
                if state.status != "queued":
                    if state.status != "dead_letter":
                        raise HTTPException(
                            status_code=409,
                            detail="only dead-letter runs can be requeued",
                        )
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "dead-letter run cannot be requeued because its "
                            "request is unavailable"
                        ),
                    )
            return RunStateResponse(**_practical_state_response(state))

        before = persistence.get_run_state(run_id)
        if before is None:
            raise HTTPException(status_code=404, detail=f"run_id {run_id!r} not found")
        if before.status != "dead_letter":
            raise HTTPException(status_code=409, detail="only dead-letter runs can be requeued")
        state = persistence.requeue_dead_letter(
            run_id,
            actor="operator",
            reason=request.reason,
        )
        if state is None:
            raise HTTPException(status_code=404, detail=f"run_id {run_id!r} not found")
        if state.status != "queued":
            raise HTTPException(
                status_code=409,
                detail="dead-letter run cannot be requeued because its request is unavailable",
            )
        return RunStateResponse(**_state_response(state))

    @app.delete(
        "/api/runs/{run_id}",
        response_model=DeleteResponse,
        deprecated=True,
    )
    def delete_run(
        run_id: str,
        owner: AuthenticatedOwner | None = Depends(run_mutation_dependency),
    ) -> DeleteResponse:
        """Delete a run, its encrypted request metadata, audit events, and outcomes."""
        if practical_mode:
            assert owner is not None
            database = require_migrated_database(practical_database)
            with database.session() as session:
                deleted = hunt_repository.delete_hunt(
                    session,
                    owner_id=owner.owner_id,
                    hunt_run_id=run_id,
                )
                if not deleted:
                    raise HTTPException(status_code=404, detail="run not found")
            return DeleteResponse(ok=True)

        persistence.delete_run(run_id)
        return DeleteResponse(ok=True)

    return app


app = create_app()
