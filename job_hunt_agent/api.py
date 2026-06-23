"""FastAPI surface for the job-hunt pipeline.

Three endpoints, no auth. The frontend POSTs ``/api/hunt`` to run the
pipeline, then POSTs each user-logged outcome to
``/api/runs/{run_id}/outcomes`` and reads everything back via
``GET /api/runs/{run_id}``.

The pipeline call blocks for the full duration of ``run_hunt`` (~90s on
real tools). The SQLite write happens after the pipeline returns so the
DB is never held open across that window.
"""

from __future__ import annotations

import os
from typing import Iterable
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import persistence
from .run import run_hunt
from .schemas import HuntResult, JobCriteria, OutcomeLog
from .sources.registry import RegistryError


TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
FALSY_ENV_VALUES = {"0", "false", "no", "off"}
DEFAULT_ALLOWED_ORIGINS = (
    "http://localhost:3000,"
    "http://127.0.0.1:3000,"
    "http://localhost:5173"
)
LOCAL_ORIGIN_MARKERS = ("localhost", "127.0.0.1", "[::1]")


class HuntRequest(BaseModel):
    resume_text: str = Field(description="Plain-text resume body.")
    criteria: JobCriteria = Field(description="Filters for the hunt.")
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


class HealthResponse(BaseModel):
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
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    persistence.init_db()

    use_mocks = _env_bool("USE_MOCKS", default=False)
    enable_tracing = _tracing_enabled()

    @app.api_route("/health", methods=["GET", "HEAD"], response_model=HealthResponse)
    def health() -> HealthResponse:
        """Lightweight liveness check for the deployment platform.

        HEAD is allowed because uptime monitors (UptimeRobot) probe with it.
        """
        return HealthResponse(ok=True)

    @app.post("/api/hunt", response_model=HuntResult)
    def post_hunt(request: HuntRequest) -> HuntResult:
        """Run the pipeline once and persist the HuntResult."""
        new_run_id = uuid4().hex
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
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        persistence.save_run(result)
        return result

    @app.post(
        "/api/runs/{run_id}/outcomes",
        response_model=OutcomesResponse,
    )
    def post_outcomes(run_id: str, request: OutcomesRequest) -> OutcomesResponse:
        """Append user-logged outcomes for a previously stored hunt."""
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
    def get_run(run_id: str) -> RunDetailResponse:
        """Return the stored HuntResult plus every logged outcome for it."""
        stored = persistence.load_run(run_id)
        if stored is None:
            raise HTTPException(status_code=404, detail=f"run_id {run_id!r} not found")
        outcomes = persistence.load_outcomes(run_id)
        return RunDetailResponse(hunt_result=stored, outcomes=outcomes)

    return app


app = create_app()
