"""Start the web API with an optional fail-closed migration step."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence


MIGRATE_ON_START_ENV = "MIGRATE_ON_START"
PORT_ENV = "PORT"
DEFAULT_PORT = 8000


def main() -> None:
    if _env_enabled(MIGRATE_ON_START_ENV):
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
        )

    args = _uvicorn_args()
    os.execv(sys.executable, args)


def _uvicorn_args() -> Sequence[str]:
    raw_port = os.getenv(PORT_ENV, str(DEFAULT_PORT)).strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError(f"{PORT_ENV} must be an integer") from exc
    if not 1 <= port <= 65_535:
        raise RuntimeError(f"{PORT_ENV} must be between 1 and 65535")
    return (
        sys.executable,
        "-m",
        "uvicorn",
        "job_hunt_agent.api:app",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
    )


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    main()
