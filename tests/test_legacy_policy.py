"""Operational contract tests for the deprecated hunt compatibility API."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from job_hunt_agent.api import create_app
from job_hunt_agent.legacy_policy import (
    LegacyHuntApiMode,
    legacy_deprecation_headers,
    legacy_request_problem,
)


SUNSET = "Tue, 31 Dec 2030 23:59:59 GMT"


def _legacy_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str,
) -> TestClient:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENABLE_PRACTICAL_MODE", "0")
    monkeypatch.setenv("ENABLE_TRACING", "0")
    monkeypatch.setenv("USE_MOCKS", "1")
    monkeypatch.setenv("JOB_HUNT_DB_PATH", str(tmp_path / f"legacy-{mode}.db"))
    monkeypatch.setenv("LEGACY_HUNT_API_MODE", mode)
    monkeypatch.setenv("LEGACY_HUNT_API_SUNSET", SUNSET)
    monkeypatch.setenv(
        "LEGACY_HUNT_DEPRECATION_URL",
        "http://localhost/docs/legacy-hunt-deprecation",
    )
    return TestClient(create_app())


def test_read_only_blocks_writes_with_stable_problem_and_deprecation_headers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _legacy_client(tmp_path, monkeypatch, mode="read_only") as client:
        response = client.post("/api/hunt", json={})

        assert response.status_code == 410
        assert response.headers["content-type"].startswith(
            "application/problem+json"
        )
        assert response.headers["cache-control"] == "no-store, max-age=0"
        assert response.headers["deprecation"] == "true"
        assert response.headers["sunset"] == SUNSET
        assert response.headers["x-legacy-hunt-mode"] == "read_only"
        assert response.headers["x-request-id"] == response.json()["request_id"]
        assert response.json()["code"] == "legacy_read_only"
        assert response.json()["retryable"] is False


def test_read_only_allows_only_exact_run_reads_and_privacy_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _legacy_client(tmp_path, monkeypatch, mode="read_only") as client:
        read = client.get("/api/runs/missing")
        deletion = client.delete("/api/runs/missing")
        nested = client.delete("/api/runs/missing/outcomes")
        create = client.post("/api/runs/missing/outcomes", json=[])

        # Exact reads/deletes reach the existing capability checks.
        assert read.status_code == 401
        assert deletion.status_code == 401
        assert read.headers["x-legacy-hunt-mode"] == "read_only"
        assert deletion.headers["deprecation"] == "true"
        # No future nested route inherits the privacy-delete exception.
        assert nested.status_code == 410
        assert nested.json()["code"] == "legacy_read_only"
        assert create.status_code == 410


def test_disabled_mode_blocks_reads_before_capability_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _legacy_client(tmp_path, monkeypatch, mode="disabled") as client:
        response = client.get("/api/runs/missing")

        assert response.status_code == 410
        assert response.json()["code"] == "legacy_disabled"
        assert response.headers["x-legacy-hunt-mode"] == "disabled"


def test_policy_exact_path_and_head_semantics() -> None:
    assert (
        legacy_request_problem(
            LegacyHuntApiMode.read_only,
            method="HEAD",
            path="/api/runs/abc",
        )
        is None
    )
    assert legacy_request_problem(
        LegacyHuntApiMode.read_only,
        method="HEAD",
        path="/api/hunt",
    ) == {
        "code": "legacy_read_only",
        "message": (
            "The legacy hunt API is read-only. Existing runs remain readable and "
            "deletable; use the practical job-search workspace for new work."
        ),
        "retryable": False,
    }


def test_deprecation_metadata_requires_https_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEGACY_HUNT_API_SUNSET", SUNSET)
    monkeypatch.setenv("LEGACY_HUNT_DEPRECATION_URL", "http://example.com/migrate")

    with pytest.raises(RuntimeError, match="HTTPS URL in production"):
        legacy_deprecation_headers(
            LegacyHuntApiMode.read_only,
            production=True,
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

    headers = legacy_deprecation_headers(
        LegacyHuntApiMode.read_only,
        production=False,
        now=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )
    assert headers["Link"].startswith("<http://example.com/migrate>")
