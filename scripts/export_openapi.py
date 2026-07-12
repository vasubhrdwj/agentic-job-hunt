"""Export FastAPI's OpenAPI contract deterministically for the frontend."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "frontend" / "openapi" / "job-hunt.openapi.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def export_schema() -> dict[str, object]:
    """Construct the app with isolated local state and return its schema."""

    with tempfile.TemporaryDirectory(prefix="job-hunt-openapi-") as directory:
        os.environ["ENVIRONMENT"] = "development"
        os.environ["USE_MOCKS"] = "1"
        os.environ["ENABLE_TRACING"] = "0"
        os.environ["ENABLE_PRACTICAL_MODE"] = "1"
        os.environ["ALLOWED_ORIGINS"] = "http://localhost:3000"
        os.environ["JOB_HUNT_SESSION_COOKIE"] = "job_hunt_session"
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("JOB_HUNT_DATA_KEYS", None)
        os.environ.pop("JOB_HUNT_DB_PATH", None)

        # Import after installing the deterministic practical-mode environment.
        # Schema generation must not require or create either database backend.
        from job_hunt_agent.api import create_app

        return create_app().openapi()


def render_schema() -> str:
    return json.dumps(export_schema(), indent=2, sort_keys=True) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail instead of writing when the committed schema is stale.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = args.output.resolve()
    rendered = render_schema()
    if args.check:
        current = output.read_text(encoding="utf-8") if output.exists() else None
        if current != rendered:
            raise SystemExit(f"OpenAPI snapshot is stale: run {Path(__file__).name}")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
