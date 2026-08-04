"""Run one bounded saved-search scheduler tick from a cron or worker host."""

from __future__ import annotations

import argparse
import json

from job_hunt_agent.cadence_service import (
    DEFAULT_CADENCE_MAX_BATCHES,
    run_cadence_tick,
)
from job_hunt_agent.database import database_from_env
from job_hunt_agent.scheduled_scan_repository import DEFAULT_SCHEDULED_SCAN_BATCH_SIZE


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enqueue due saved-search scans without fetching providers inline."
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_SCHEDULED_SCAN_BATCH_SIZE,
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=DEFAULT_CADENCE_MAX_BATCHES,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    database = database_from_env(required=True)
    if database is None:  # pragma: no cover - required=True fails closed.
        raise RuntimeError("cadence runner requires DATABASE_URL")
    try:
        if not database.migrations_current():
            raise RuntimeError("cadence runner requires current database migrations")
        result = run_cadence_tick(
            database,
            batch_size=args.batch_size,
            max_batches=args.max_batches,
        )
        print(
            json.dumps(
                {
                    "batches": result.batches,
                    "considered_searches": result.considered_searches,
                    "created_scans": result.created_scans,
                    "replayed_scans": result.replayed_scans,
                    "paused_invalid_searches": result.paused_invalid_searches,
                    "saturated": result.saturated,
                    "ticked_at": result.ticked_at.isoformat(),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
