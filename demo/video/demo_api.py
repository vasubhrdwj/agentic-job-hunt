"""Recording-only API that replays the canonical real run.

Run with:
    .venv/bin/uvicorn demo.video.demo_api:app --port 8000

This keeps the screen capture deterministic without changing production code.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse


ROOT = Path(__file__).resolve().parents[2]
CANONICAL_RUN_PATH = ROOT / "demo" / "canonical_run.json"
CANONICAL_RUN: dict[str, Any] = json.loads(CANONICAL_RUN_PATH.read_text(encoding="utf-8"))
TRACE_HTML_PATH = ROOT / "demo" / "video" / "trace.html"
TRACE_DATA_PATH = ROOT / "demo" / "video" / "build" / "trace.json"
OUTCOMES: list[dict[str, Any]] = []

app = FastAPI(title="Job Hunt Signal recording API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/demo/trace")
def trace_view() -> FileResponse:
    return FileResponse(TRACE_HTML_PATH)


@app.get("/demo/trace-data")
def trace_data() -> dict[str, Any]:
    if not TRACE_DATA_PATH.exists():
        raise HTTPException(status_code=404, detail="Run fetch_trace.py first")
    return json.loads(TRACE_DATA_PATH.read_text(encoding="utf-8"))


@app.post("/api/hunt")
async def post_hunt() -> dict[str, Any]:
    # Long enough to show the product's progress state, short enough for a demo.
    await asyncio.sleep(7)
    return CANONICAL_RUN


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    if run_id != CANONICAL_RUN["run_id"]:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"hunt_result": CANONICAL_RUN, "outcomes": OUTCOMES}


@app.post("/api/runs/{run_id}/outcomes")
def post_outcomes(run_id: str, request: dict[str, Any]) -> dict[str, Any]:
    if run_id != CANONICAL_RUN["run_id"]:
        raise HTTPException(status_code=404, detail="Run not found")

    stamped: list[dict[str, Any]] = []
    for item in request.get("outcomes", []):
        entry = {
            **item,
            "logged_at": datetime.now(timezone.utc).isoformat(),
        }
        stamped.append(entry)
    OUTCOMES[:0] = stamped
    return {"ok": True, "inserted": len(stamped), "outcomes": stamped}
