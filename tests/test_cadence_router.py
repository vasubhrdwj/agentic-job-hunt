"""Authentication and sanitized response tests for the external cadence wake."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from job_hunt_agent.cadence_service import CadenceTick
from job_hunt_agent.routers import cadence as cadence_router


SECRET = "cadence-test-secret-that-is-long-enough-123"
NOW = datetime(2026, 8, 5, 4, 0, tzinfo=timezone.utc)


class _Database:
    def reachable(self) -> bool:
        return True

    def migrations_current(self) -> bool:
        return True


class _Worker:
    alive = True


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv(cadence_router.CADENCE_SECRET_ENV, SECRET)
    monkeypatch.setattr(
        cadence_router,
        "run_cadence_tick",
        lambda _database: CadenceTick(
            ticked_at=NOW,
            batches=1,
            considered_searches=2,
            created_scans=1,
            replayed_scans=1,
            paused_invalid_searches=0,
            saturated=False,
        ),
    )
    app = FastAPI()
    app.state.embedded_scan_worker = _Worker()
    app.include_router(cadence_router.create_cadence_router(_Database()))  # type: ignore[arg-type]
    return TestClient(app)


def test_cadence_tick_rejects_missing_and_wrong_bearer(monkeypatch) -> None:
    with _client(monkeypatch) as client:
        assert client.post("/internal/cadence/tick").status_code == 401
        assert client.post(
            "/internal/cadence/tick",
            headers={"Authorization": "Bearer wrong"},
        ).status_code == 401


def test_cadence_tick_returns_only_sanitized_counts(monkeypatch) -> None:
    with _client(monkeypatch) as client:
        response = client.post(
            "/internal/cadence/tick",
            headers={"Authorization": f"Bearer {SECRET}"},
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.json() == {
        "data_source": "database",
        "ticked_at": NOW.isoformat().replace("+00:00", "Z"),
        "batches": 1,
        "considered_searches": 2,
        "created_scans": 1,
        "replayed_scans": 1,
        "paused_invalid_searches": 0,
        "saturated": False,
        "embedded_worker_alive": True,
    }


def test_cadence_tick_fails_closed_when_secret_is_not_configured(monkeypatch) -> None:
    monkeypatch.delenv(cadence_router.CADENCE_SECRET_ENV, raising=False)
    app = FastAPI()
    app.include_router(cadence_router.create_cadence_router(_Database()))  # type: ignore[arg-type]
    with TestClient(app) as client:
        response = client.post("/internal/cadence/tick")
    assert response.status_code == 503
    assert response.json() == {"detail": "production cadence is not configured"}
