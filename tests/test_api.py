"""End-to-end tests for the FastAPI surface."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from job_hunt_agent import persistence
from job_hunt_agent.schemas import (
    HuntResult,
    OutreachDraft,
    Person,
    Role,
)


def _fake_role() -> Role:
    return Role(
        company="Okta",
        title="Senior Engineer, Identity",
        url="https://www.linkedin.com/jobs/view/123",
        location="Remote-India",
        summary="Build SCIM provisioning.",
        match_reason="Listing mentions SCIM 2.0.",
    )


def _fake_person() -> Person:
    return Person(
        name="Priya Rao",
        title="Staff Engineer",
        company="Okta",
        profile_url="https://linkedin.com/in/priya",
        source="linkedin",
        why_relevant="Adjacent team.",
    )


def _fake_run_hunt(*, run_id: str, draft_ids: tuple[str, ...] = ("d-0", "d-1", "d-2")) -> HuntResult:
    role = _fake_role()
    person = _fake_person()
    drafts = [
        OutreachDraft(draft_id=did, role=role, person=person, message=f"hello {did}")
        for did in draft_ids
    ]
    return HuntResult(run_id=run_id, roles=[role], outreach=drafts)


@pytest.fixture
def api_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Reload the api module with a temp SQLite path and a stubbed run_hunt."""
    monkeypatch.setenv("JOB_HUNT_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173",
    )
    from job_hunt_agent import api as api_mod

    api_mod = importlib.reload(api_mod)

    def stub(  # noqa: ARG001
        *,
        resume_text,
        criteria,
        run_id,
        use_mocks=False,
        use_self_rag=True,
        enable_tracing=False,
        pack=None,
    ):
        return _fake_run_hunt(run_id=run_id)

    monkeypatch.setattr(api_mod, "run_hunt", stub)
    api_mod.app = api_mod.create_app()
    return api_mod


@pytest.fixture
def client(api_module) -> Iterator[TestClient]:
    with TestClient(api_module.app) as test_client:
        yield test_client


def _post_hunt(client: TestClient) -> dict:
    response = client.post(
        "/api/hunt",
        json={
            "resume_text": "Built SCIM systems.",
            "criteria": {
                "role_keywords": ["SCIM"],
                "seniority": "senior",
                "location": ["Remote-India"],
            },
            "pack": "backend_india",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_health_endpoint_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_health_endpoint_allows_head(client: TestClient) -> None:
    """Uptime monitors (UptimeRobot) probe /health with HEAD requests."""
    response = client.head("/health")
    assert response.status_code == 200


def test_post_hunt_returns_hunt_result_with_run_id(client: TestClient) -> None:
    body = _post_hunt(client)
    assert body["run_id"]
    assert len(body["outreach"]) == 3
    assert {d["draft_id"] for d in body["outreach"]} == {"d-0", "d-1", "d-2"}


def test_post_outcomes_accepts_known_draft_ids(client: TestClient) -> None:
    hunt = _post_hunt(client)
    run_id = hunt["run_id"]

    response = client.post(
        f"/api/runs/{run_id}/outcomes",
        json={
            "outcomes": [
                {"draft_id": "d-0", "outcome": "replied"},
                {"draft_id": "d-1", "outcome": "no_reply", "notes": "ghosted"},
            ]
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["inserted"] == 2
    for entry in body["outcomes"]:
        assert entry["logged_at"] is not None


def test_post_outcomes_rejects_unknown_draft_ids(client: TestClient) -> None:
    hunt = _post_hunt(client)
    run_id = hunt["run_id"]

    response = client.post(
        f"/api/runs/{run_id}/outcomes",
        json={
            "outcomes": [
                {"draft_id": "d-0", "outcome": "replied"},
                {"draft_id": "bogus", "outcome": "replied"},
            ]
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "bogus" in detail["unknown_draft_ids"]


def test_post_outcomes_rejects_unknown_run(client: TestClient) -> None:
    response = client.post(
        "/api/runs/missing/outcomes",
        json={"outcomes": [{"draft_id": "d-0", "outcome": "replied"}]},
    )
    assert response.status_code == 404


def test_get_run_returns_hunt_result_and_outcomes(client: TestClient) -> None:
    hunt = _post_hunt(client)
    run_id = hunt["run_id"]

    client.post(
        f"/api/runs/{run_id}/outcomes",
        json={"outcomes": [{"draft_id": "d-0", "outcome": "introduced"}]},
    )

    response = client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["hunt_result"]["run_id"] == run_id
    assert len(body["outcomes"]) == 1
    assert body["outcomes"][0]["draft_id"] == "d-0"
    assert body["outcomes"][0]["outcome"] == "introduced"


def test_get_run_returns_404_when_missing(client: TestClient) -> None:
    response = client.get("/api/runs/missing")
    assert response.status_code == 404


def test_persistence_survives_app_restart(api_module) -> None:
    with TestClient(api_module.app) as first:
        hunt = _post_hunt(first)
        run_id = hunt["run_id"]
        first.post(
            f"/api/runs/{run_id}/outcomes",
            json={"outcomes": [{"draft_id": "d-0", "outcome": "replied"}]},
        )

    # Build a fresh app instance pointing at the same DB.
    api_module.app = api_module.create_app()
    with TestClient(api_module.app) as second:
        response = second.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["hunt_result"]["run_id"] == run_id
        assert body["outcomes"][0]["outcome"] == "replied"


def test_logged_at_is_overwritten_by_server(client: TestClient) -> None:
    hunt = _post_hunt(client)
    run_id = hunt["run_id"]

    spoofed = "1999-01-01T00:00:00+00:00"
    response = client.post(
        f"/api/runs/{run_id}/outcomes",
        json={
            "outcomes": [
                {"draft_id": "d-0", "outcome": "replied", "logged_at": spoofed},
            ]
        },
    )
    assert response.status_code == 200
    stored = response.json()["outcomes"][0]
    assert stored["logged_at"] != spoofed


def test_db_path_env_var_is_honored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "scoped.db"
    monkeypatch.setenv("JOB_HUNT_DB_PATH", str(db))

    from job_hunt_agent import api as api_mod

    api_mod = importlib.reload(api_mod)

    def stub(  # noqa: ARG001
        *,
        resume_text,
        criteria,
        run_id,
        use_mocks=False,
        use_self_rag=True,
        enable_tracing=False,
        pack=None,
    ):
        return _fake_run_hunt(run_id=run_id)

    monkeypatch.setattr(api_mod, "run_hunt", stub)
    api_mod.app = api_mod.create_app()

    with TestClient(api_mod.app) as client:
        _post_hunt(client)

    assert db.exists()
    # The persistence module also reads the env var directly.
    assert persistence.load_run is not None  # sanity import check


def test_post_hunt_passes_tracing_flag_from_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "api.db"
    monkeypatch.setenv("JOB_HUNT_DB_PATH", str(db))
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000")
    monkeypatch.setenv("ENABLE_TRACING", "1")

    from job_hunt_agent import api as api_mod

    api_mod = importlib.reload(api_mod)
    captured: dict[str, object] = {}

    def stub(  # noqa: ARG001
        *,
        resume_text,
        criteria,
        run_id,
        use_mocks=False,
        use_self_rag=True,
        enable_tracing=False,
        pack=None,
    ):
        captured["enable_tracing"] = enable_tracing
        captured["use_mocks"] = use_mocks
        captured["use_self_rag"] = use_self_rag
        captured["pack"] = pack
        return _fake_run_hunt(run_id=run_id)

    monkeypatch.setattr(api_mod, "run_hunt", stub)
    api_mod.app = api_mod.create_app()

    with TestClient(api_mod.app) as client:
        _post_hunt(client)

    assert captured["enable_tracing"] is True
    assert captured["use_mocks"] is False
    assert captured["use_self_rag"] is True
    assert captured["pack"] == "backend_india"


def test_post_hunt_rejects_invalid_pack_name(client: TestClient) -> None:
    response = client.post(
        "/api/hunt",
        json={
            "resume_text": "Built backend systems.",
            "criteria": {
                "role_keywords": ["backend"],
                "seniority": "junior",
                "location": ["India"],
            },
            "pack": "../secret",
        },
    )

    assert response.status_code == 422


def test_production_config_fails_loudly_when_required_env_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import api as api_mod

    monkeypatch.setenv("ENVIRONMENT", "production")
    for name in (
        "GOOGLE_API_KEY",
        "SERPAPI_API_KEY",
        "SERPAPI_KEY",
        "PHOENIX_API_KEY",
        "PHOENIX_COLLECTOR_ENDPOINT",
        "JOB_HUNT_DB_PATH",
        "ALLOWED_ORIGINS",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="Invalid production config"):
        importlib.reload(api_mod)

    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("JOB_HUNT_DB_PATH", str(tmp_path / "recovered.db"))
    importlib.reload(api_mod)


def test_production_config_accepts_required_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import api as api_mod

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ENABLE_TRACING", "1")
    monkeypatch.setenv("USE_MOCKS", "0")
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-google")
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-serpapi")
    monkeypatch.setenv("PHOENIX_API_KEY", "fake-phoenix")
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "https://app.phoenix.arize.com/s/demo")
    monkeypatch.setenv("JOB_HUNT_DB_PATH", str(tmp_path / "data" / "outcomes.db"))
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://job-hunt-agent.vercel.app")

    api_mod = importlib.reload(api_mod)

    with TestClient(api_mod.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True}

    monkeypatch.delenv("ENVIRONMENT", raising=False)
    importlib.reload(api_mod)
