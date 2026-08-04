"""Keep deploy, recovery, and CI configuration aligned with the runbooks."""

from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_compose_restarts_durable_services_and_health_gates_frontend() -> None:
    compose = _yaml("docker-compose.yml")
    services = compose["services"]
    for name in ("postgres", "web", "worker"):
        assert services[name]["restart"] == "unless-stopped"
    assert services["web"]["healthcheck"]["test"]
    assert services["frontend"]["depends_on"]["web"]["condition"] == "service_healthy"
    assert compose["x-backend-environment"]["LEGACY_HUNT_API_MODE"].endswith(
        ":-read_only}"
    )
    assert compose["x-backend-environment"]["JOB_HUNT_WORKER_KINDS"].endswith(
        ":-legacy_hunt,scan_saved_search,discover_contacts}"
    )
    assert "${POSTGRES_PASSWORD:?" in compose["x-backend-environment"]["DATABASE_URL"]
    assert "${POSTGRES_PASSWORD:?" in services["postgres"]["environment"][
        "POSTGRES_PASSWORD"
    ]
    assert services["postgres"]["ports"] == ["127.0.0.1:5432:5432"]
    assert compose["x-backend-environment"]["ENABLE_EMBEDDED_SCAN_WORKER"].endswith(
        ":-0}"
    )


def test_render_runs_free_scan_worker_inside_database_ready_web() -> None:
    render = _yaml("render.yaml")
    web = render["services"][0]
    env = {item["key"]: item for item in web["envVars"]}
    assert web["healthCheckPath"] == "/web-ready"
    assert web["autoDeployTrigger"] == "checksPass"
    assert "autoDeploy" not in web
    assert env["LEGACY_HUNT_API_MODE"]["value"] == "read_only"
    assert env["LEGACY_HUNT_API_SUNSET"]["value"]
    assert env["DATABASE_URL"]["sync"] is False
    assert env["MIGRATE_ON_START"]["value"] == "1"
    assert env["JOB_HUNT_DATA_KEYS"]["sync"] is False
    assert env["JOB_HUNT_SIGNUP_MODE"]["value"] == "open"
    assert env["JOB_HUNT_LEGACY_RECOVERY_REQUIRED"]["value"] == "0"
    assert "JOB_HUNT_OWNER_ID" not in env
    assert "JOB_HUNT_OWNER_TOKEN_HASH" not in env
    assert env["ENABLE_EMBEDDED_SCAN_WORKER"]["value"] == "1"
    assert env["JOB_HUNT_WORKER_KINDS"]["value"] == (
        "scan_saved_search,discover_contacts"
    )
    assert env["USE_MOCKS"]["value"] == "0"
    assert len(render["services"]) == 1


def test_quality_workflow_runs_operational_gates_with_bounded_jobs() -> None:
    workflow_text = (ROOT / ".github/workflows/quality.yml").read_text(
        encoding="utf-8"
    )
    workflow = yaml.safe_load(workflow_text)
    assert workflow["concurrency"]["cancel-in-progress"] is True
    assert workflow["jobs"]["backend"]["timeout-minutes"] <= 30
    assert workflow["jobs"]["frontend"]["timeout-minutes"] <= 30
    assert workflow["jobs"]["runtime-image"]["timeout-minutes"] <= 30
    assert "python -m scripts.migration_gate check" in workflow_text
    assert "docker compose config --quiet" in workflow_text
    assert "docker build --tag job-hunt-agent-ci:" in workflow_text
    assert "pip check" in workflow_text
    assert "--max-warnings=0" in workflow_text
    assert "branches: [main, master, v2-rebuild]" not in workflow_text
    postgres_password = workflow["jobs"]["backend"]["services"]["postgres"]["env"][
        "POSTGRES_PASSWORD"
    ]
    assert "github.run_id" in postgres_password
    assert workflow["jobs"]["backend"]["env"]["POSTGRES_PASSWORD"] == postgres_password
    assert postgres_password in workflow["jobs"]["backend"]["env"]["POSTGRES_URL"]


def test_runtime_image_contains_restore_tools_and_operator_scripts() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "postgresql-client" in dockerfile
    assert "COPY scripts/ ./scripts/" in dockerfile
    assert 'CMD ["python", "-m", "scripts.start_web"]' in dockerfile


def test_operational_runbook_set_and_manual_browser_matrix_are_present() -> None:
    runbooks = ROOT / "docs/runbooks"
    required = {
        "README.md",
        "backup-restore.md",
        "deploy-rollback.md",
        "incident-recovery.md",
        "legacy-hunt-deprecation.md",
        "manual-browser-matrix.md",
        "source-outage.md",
    }
    assert required.issubset({path.name for path in runbooks.glob("*.md")})
    browser = (runbooks / "manual-browser-matrix.md").read_text(encoding="utf-8")
    for marker in ("Chromium", "Firefox", "WebKit", "390", "Weekly Review", "Privacy"):
        assert marker in browser

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    hosted_status = readme.split("## Hosted deployment status", maxsplit=1)[1]
    assert "onrender.com/web-ready" in hosted_status
    assert "authenticated `/api/health`" in hosted_status
    assert "onrender.com/health" not in hosted_status
