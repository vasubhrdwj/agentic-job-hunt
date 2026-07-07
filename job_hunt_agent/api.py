"""FastAPI surface for the job-hunt pipeline.

The frontend POSTs ``/api/hunt`` to run the pipeline, then uses the returned
run capability as a bearer token to read, delete, or append outcomes for that
private run.

The pipeline call blocks for the full duration of ``run_hunt`` (~90s on
real tools). The SQLite write happens after the pipeline returns so the
DB is never held open across that window.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Iterable
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from . import persistence
from .run import run_hunt
from .schemas import HuntResult, JobCriteria, OutcomeLog
from .security import (
    MAX_RESUME_CHARS,
    REQUEST_RETENTION_HOURS,
    RUN_RETENTION_DAYS,
    SecurityConfigError,
    generate_access_token,
    hash_access_token,
    load_data_keyring,
)
from .sources.registry import RegistryError


TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
FALSY_ENV_VALUES = {"0", "false", "no", "off"}
DEFAULT_ALLOWED_ORIGINS = (
    "http://localhost:3000,"
    "http://127.0.0.1:3000,"
    "http://localhost:5173"
)
LOCAL_ORIGIN_MARKERS = ("localhost", "127.0.0.1", "[::1]")
DEFAULT_RETENTION_CLEANUP_INTERVAL_SECONDS = 3600.0


class HuntRequest(BaseModel):
    resume_text: str = Field(
        min_length=1,
        max_length=MAX_RESUME_CHARS,
        description="Plain-text resume body.",
    )
    criteria: JobCriteria = Field(description="Filters for the hunt.")
    provider_consent: Literal[True] = Field(
        description=(
            "Explicit consent to send bounded resume excerpts to the configured "
            "paid model provider under the disclosed retention terms."
        ),
    )
    use_self_rag: bool = Field(
        default=True,
        description=(
            "Toggle V8 self-RAG. Set False for the V10 round-1 baseline so the "
            "demo can compare drafts with and without past-trace exemplars."
        ),
    )
    pack: str = Field(
        default="backend_india",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
        description="Curated company pack used for first-party job discovery.",
    )

    @field_validator("resume_text")
    @classmethod
    def resume_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("resume_text must not be blank")
        return value


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


class RunDetailResponse(BaseModel):
    hunt_result: HuntResult
    outcomes: list[OutcomeLog]


class HuntCreatedResponse(HuntResult):
    status: Literal["succeeded"]
    access_token: str = Field(
        description=(
            "Run capability. Send it only as an Authorization bearer token; "
            "it is not recoverable from the server."
        ),
    )


class HealthResponse(BaseModel):
    ok: bool


class DeleteResponse(BaseModel):
    ok: bool


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


def _tracing_enabled() -> bool:
    return _env_bool("ENABLE_TRACING", default=_is_production())


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


def _validate_production_config() -> None:
    if not _is_production():
        return

    errors: list[str] = []
    required = (
        "GOOGLE_API_KEY",
        "PHOENIX_API_KEY",
        "PHOENIX_COLLECTOR_ENDPOINT",
        "JOB_HUNT_DB_PATH",
        "JOB_HUNT_DATA_KEYS",
        "ALLOWED_ORIGINS",
    )
    for name in required:
        if not os.getenv(name, "").strip():
            errors.append(f"{name} is required when ENVIRONMENT=production")

    if not (os.getenv("SERPAPI_API_KEY") or os.getenv("SERPAPI_KEY")):
        errors.append("SERPAPI_API_KEY or SERPAPI_KEY is required when ENVIRONMENT=production")

    if _env_bool("USE_MOCKS", default=False):
        errors.append("USE_MOCKS must be false when ENVIRONMENT=production")

    if not _tracing_enabled():
        errors.append("ENABLE_TRACING must not be false when ENVIRONMENT=production")

    if not _env_bool("GEMINI_PAID_SERVICE_ACK", default=False):
        errors.append(
            "GEMINI_PAID_SERVICE_ACK must be true: resumes must not use unpaid Gemini quota"
        )

    if _env_bool("ENABLE_TRACE_DRAFT_CONTENT", default=False):
        errors.append("ENABLE_TRACE_DRAFT_CONTENT must be false in production")

    db_path = os.getenv("JOB_HUNT_DB_PATH", "").strip()
    if db_path and not os.path.isabs(db_path):
        errors.append("JOB_HUNT_DB_PATH must be an absolute path when ENVIRONMENT=production")

    allowed_origins = _parse_allowed_origins()
    if "*" in allowed_origins:
        errors.append("ALLOWED_ORIGINS must name the Vercel URL; '*' is not allowed in production")
    for origin in allowed_origins:
        if any(marker in origin for marker in LOCAL_ORIGIN_MARKERS):
            errors.append("ALLOWED_ORIGINS must not include localhost in production")
            break

    if errors:
        raise RuntimeError("Invalid production config: " + "; ".join(errors))


def _unknown_draft_ids(
    outcomes: Iterable[OutcomeLog],
    known: set[str],
) -> set[str]:
    return {entry.draft_id for entry in outcomes if entry.draft_id not in known}


def create_app() -> FastAPI:
    """Application factory. Tests use this to swap the SQLite path per run."""
    _validate_production_config()
    app = FastAPI(title="Job Hunt Signal API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_parse_allowed_origins(),
        allow_methods=["DELETE", "GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def prevent_private_response_caching(request: Request, call_next):
        nonlocal last_retention_cleanup_at
        now = datetime.now(timezone.utc)
        if (
            retention_cleanup_interval == 0
            or (now - last_retention_cleanup_at).total_seconds()
            >= retention_cleanup_interval
        ):
            persistence.purge_expired_data(now=now)
            last_retention_cleanup_at = now
        response = await call_next(request)
        if request.url.path == "/api/hunt" or request.url.path.startswith("/api/runs/"):
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

    persistence.init_db()
    persistence.purge_expired_data()
    retention_cleanup_interval = _retention_cleanup_interval_seconds()
    last_retention_cleanup_at = datetime.now(timezone.utc)
    try:
        data_keyring = load_data_keyring(production=_is_production())
    except SecurityConfigError as exc:
        raise RuntimeError(f"Invalid production config: {exc}") from exc

    use_mocks = _env_bool("USE_MOCKS", default=False)
    enable_tracing = _tracing_enabled()

    def require_run_access(run_id: str, authorization: str | None) -> None:
        if not authorization:
            raise HTTPException(status_code=401, detail="run access token required")
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(status_code=401, detail="invalid run authorization")
        if not persistence.authorize_run(run_id, hash_access_token(token.strip())):
            raise HTTPException(status_code=404, detail="run not found")

    @app.api_route("/health", methods=["GET", "HEAD"], response_model=HealthResponse)
    def health() -> HealthResponse:
        """Lightweight liveness check for the deployment platform.

        HEAD is allowed because uptime monitors (UptimeRobot) probe with it.
        """
        return HealthResponse(ok=True)

    @app.post("/api/hunt", response_model=HuntCreatedResponse)
    def post_hunt(request: HuntRequest) -> HuntCreatedResponse:
        """Run the pipeline once and persist the HuntResult."""
        new_run_id = uuid4().hex
        access_token = generate_access_token()
        now = datetime.now(timezone.utc)
        encrypted_request = data_keyring.encrypt(request.model_dump_json())
        persistence.create_run_security(
            new_run_id,
            access_hash=hash_access_token(access_token),
            encrypted_request=encrypted_request.ciphertext,
            encryption_key_id=encrypted_request.key_id,
            request_expires_at=now + timedelta(hours=REQUEST_RETENTION_HOURS),
            access_expires_at=now + timedelta(days=RUN_RETENTION_DAYS),
        )
        try:
            result = run_hunt(
                resume_text=request.resume_text,
                criteria=request.criteria,
                run_id=new_run_id,
                use_mocks=use_mocks,
                use_self_rag=request.use_self_rag,
                enable_tracing=enable_tracing,
                pack=request.pack,
            )
        except RegistryError as exc:
            persistence.delete_run(new_run_id)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception:
            persistence.delete_run(new_run_id)
            raise
        try:
            persistence.save_run(result)
            persistence.complete_run_security(new_run_id)
        except Exception:
            persistence.delete_run(new_run_id)
            raise
        return HuntCreatedResponse(
            **result.model_dump(),
            status="succeeded",
            access_token=access_token,
        )

    @app.post(
        "/api/runs/{run_id}/outcomes",
        response_model=OutcomesResponse,
    )
    def post_outcomes(
        run_id: str,
        request: OutcomesRequest,
        authorization: str | None = Header(default=None),
    ) -> OutcomesResponse:
        """Append user-logged outcomes for a previously stored hunt."""
        require_run_access(run_id, authorization)
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
    )
    def get_run(
        run_id: str,
        authorization: str | None = Header(default=None),
    ) -> RunDetailResponse:
        """Return the stored HuntResult plus every logged outcome for it."""
        require_run_access(run_id, authorization)
        stored = persistence.load_run(run_id)
        if stored is None:
            raise HTTPException(status_code=404, detail=f"run_id {run_id!r} not found")
        outcomes = persistence.load_outcomes(run_id)
        return RunDetailResponse(hunt_result=stored, outcomes=outcomes)

    @app.delete(
        "/api/runs/{run_id}",
        response_model=DeleteResponse,
    )
    def delete_run(
        run_id: str,
        authorization: str | None = Header(default=None),
    ) -> DeleteResponse:
        """Delete a run, its encrypted request metadata, and all outcomes."""

        require_run_access(run_id, authorization)
        persistence.delete_run(run_id)
        return DeleteResponse(ok=True)

    return app


app = create_app()
