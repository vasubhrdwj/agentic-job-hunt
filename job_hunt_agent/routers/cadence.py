"""Secret-authenticated wake endpoint for sleeping free-tier deployments."""

from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from ..cadence_schemas import CadenceTickResponse
from ..cadence_service import run_cadence_tick
from ..database import Database
from .session import require_migrated_database


CADENCE_SECRET_ENV = "CRON_SECRET"
MIN_CADENCE_SECRET_CHARS = 32


def create_cadence_router(database: Database | None) -> APIRouter:
    router = APIRouter(tags=["production-cadence"])

    @router.post(
        "/internal/cadence/tick",
        response_model=CadenceTickResponse,
        include_in_schema=False,
    )
    def tick_cadence(
        request: Request,
        response: Response,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CadenceTickResponse:
        _require_cadence_secret(authorization)
        db = require_migrated_database(database)
        tick = run_cadence_tick(db)
        worker = getattr(request.app.state, "embedded_scan_worker", None)
        worker_alive = bool(worker is not None and worker.alive)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return CadenceTickResponse(
            ticked_at=tick.ticked_at,
            batches=tick.batches,
            considered_searches=tick.considered_searches,
            created_scans=tick.created_scans,
            replayed_scans=tick.replayed_scans,
            paused_invalid_searches=tick.paused_invalid_searches,
            saturated=tick.saturated,
            embedded_worker_alive=worker_alive,
        )

    return router


def _require_cadence_secret(authorization: str | None) -> None:
    configured = os.getenv(CADENCE_SECRET_ENV, "")
    if len(configured) < MIN_CADENCE_SECRET_CHARS:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="production cadence is not configured",
        )
    expected = f"Bearer {configured}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="cadence authorization required",
        )


__all__ = [
    "CADENCE_SECRET_ENV",
    "MIN_CADENCE_SECRET_CHARS",
    "create_cadence_router",
]
