"""SQLite persistence for hunt results and user-logged outcomes.

Storage is intentionally minimal: two tables, JSON blobs as payloads,
no migration framework. v1 demo scope.

The DB path resolves from the ``JOB_HUNT_DB_PATH`` env var (default
``outcomes.db`` in the working directory) so tests can point at a
``tmp_path`` and production at a writable host path without code changes.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .schemas import HuntResult, OutcomeLog


DEFAULT_DB_PATH = "outcomes.db"


def _resolve_db_path(path: str | os.PathLike[str] | None) -> str:
    if path is not None:
        return str(path)
    return os.getenv("JOB_HUNT_DB_PATH", DEFAULT_DB_PATH)


def _connect(path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    resolved = _resolve_db_path(path)
    parent = Path(resolved).parent
    if parent and str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(resolved)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: str | os.PathLike[str] | None = None) -> None:
    """Create the runs and outcomes tables if they do not yet exist."""
    with _connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id     TEXT PRIMARY KEY,
                payload    TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS outcomes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id     TEXT NOT NULL,
                draft_id   TEXT NOT NULL,
                payload    TEXT NOT NULL,
                logged_at  TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(run_id)
            );

            CREATE INDEX IF NOT EXISTS idx_outcomes_run
                ON outcomes(run_id);
            CREATE INDEX IF NOT EXISTS idx_outcomes_draft
                ON outcomes(draft_id);
            """
        )


def save_run(
    hunt_result: HuntResult,
    *,
    path: str | os.PathLike[str] | None = None,
) -> None:
    """Insert a HuntResult. Raises if the run_id already exists."""
    payload = hunt_result.model_dump_json()
    created_at = datetime.now(timezone.utc).isoformat()
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO runs (run_id, payload, created_at) VALUES (?, ?, ?)",
            (hunt_result.run_id, payload, created_at),
        )


def load_run(
    run_id: str,
    *,
    path: str | os.PathLike[str] | None = None,
) -> HuntResult | None:
    """Return the stored HuntResult for ``run_id`` or ``None`` if missing."""
    with _connect(path) as conn:
        cursor = conn.execute(
            "SELECT payload FROM runs WHERE run_id = ?",
            (run_id,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return HuntResult.model_validate_json(row[0])


def save_outcomes(
    run_id: str,
    outcomes: list[OutcomeLog],
    *,
    path: str | os.PathLike[str] | None = None,
) -> list[OutcomeLog]:
    """Persist outcomes as an append-only log.

    Each log entry's ``logged_at`` is overwritten with a fresh UTC timestamp
    so client clocks cannot influence ordering. The persisted entries are
    returned so callers can hand the stamped values back to the frontend.
    """
    stamped: list[OutcomeLog] = []
    rows: list[tuple[str, str, str, str]] = []
    for entry in outcomes:
        logged_at = datetime.now(timezone.utc)
        stamped_entry = entry.model_copy(update={"logged_at": logged_at})
        stamped.append(stamped_entry)
        rows.append(
            (
                run_id,
                stamped_entry.draft_id,
                stamped_entry.model_dump_json(),
                logged_at.isoformat(),
            )
        )

    if not rows:
        return stamped

    with _connect(path) as conn:
        conn.executemany(
            "INSERT INTO outcomes (run_id, draft_id, payload, logged_at) "
            "VALUES (?, ?, ?, ?)",
            rows,
        )
    return stamped


def load_outcomes(
    run_id: str,
    *,
    path: str | os.PathLike[str] | None = None,
) -> list[OutcomeLog]:
    """Return every outcome logged against ``run_id``, newest first."""
    with _connect(path) as conn:
        cursor = conn.execute(
            "SELECT payload FROM outcomes WHERE run_id = ? "
            "ORDER BY logged_at DESC, id DESC",
            (run_id,),
        )
        rows = cursor.fetchall()
    return [OutcomeLog.model_validate_json(row[0]) for row in rows]
