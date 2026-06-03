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


DEFAULT_ALLOWED_ORIGINS = (
    "http://localhost:3000,"
    "http://127.0.0.1:3000,"
    "http://localhost:5173"
)


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


def _parse_allowed_origins() -> list[str]:
    raw = os.getenv("ALLOWED_ORIGINS", DEFAULT_ALLOWED_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _unknown_draft_ids(
    outcomes: Iterable[OutcomeLog],
    known: set[str],
) -> set[str]:
    return {entry.draft_id for entry in outcomes if entry.draft_id not in known}


def create_app() -> FastAPI:
    """Application factory. Tests use this to swap the SQLite path per run."""
    app = FastAPI(title="Job Hunt Signal API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_parse_allowed_origins(),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    persistence.init_db()

    use_mocks = os.getenv("USE_MOCKS", "0") == "1"

    @app.post("/api/hunt", response_model=HuntResult)
    def post_hunt(request: HuntRequest) -> HuntResult:
        """Run the pipeline once and persist the HuntResult."""
        new_run_id = uuid4().hex
        result = run_hunt(
            resume_text=request.resume_text,
            criteria=request.criteria,
            run_id=new_run_id,
            use_mocks=use_mocks,
            use_self_rag=request.use_self_rag,
        )
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
