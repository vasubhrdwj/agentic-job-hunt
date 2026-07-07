"""SQLite persistence for hunt results, private requests, and outcomes.

Completed hunt results remain JSON blobs. Resume-bearing request payloads live
in a separate security table and must be encrypted before they reach this
module. Access capabilities are stored only as SHA-256 hashes.

The DB path resolves from the ``JOB_HUNT_DB_PATH`` env var (default
``outcomes.db`` in the working directory) so tests can point at a
``tmp_path`` and production at a writable host path without code changes.
"""

from __future__ import annotations

import os
import hmac
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .schemas import HuntResult, OutcomeLog


DEFAULT_DB_PATH = "outcomes.db"
OUTCOME_PRIORITY = {
    "introduced": 3,
    "replied": 2,
    "pending": 1,
    "rejected": -1,
    "no_reply": -2,
}


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

            CREATE TABLE IF NOT EXISTS run_security (
                run_id              TEXT PRIMARY KEY,
                access_hash         TEXT NOT NULL,
                status              TEXT NOT NULL,
                encrypted_request   TEXT,
                encryption_key_id   TEXT,
                request_expires_at  TEXT NOT NULL,
                access_expires_at   TEXT NOT NULL,
                created_at          TEXT NOT NULL,
                completed_at        TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_run_security_request_expiry
                ON run_security(request_expires_at);
            CREATE INDEX IF NOT EXISTS idx_run_security_access_expiry
                ON run_security(access_expires_at);
            """
        )


def create_run_security(
    run_id: str,
    *,
    access_hash: str,
    encrypted_request: str,
    encryption_key_id: str,
    request_expires_at: datetime,
    access_expires_at: datetime,
    path: str | os.PathLike[str] | None = None,
) -> None:
    """Store encrypted request state before provider-backed work starts."""

    created_at = datetime.now(timezone.utc).isoformat()
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO run_security (
                run_id,
                access_hash,
                status,
                encrypted_request,
                encryption_key_id,
                request_expires_at,
                access_expires_at,
                created_at
            ) VALUES (?, ?, 'running', ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                access_hash,
                encrypted_request,
                encryption_key_id,
                request_expires_at.isoformat(),
                access_expires_at.isoformat(),
                created_at,
            ),
        )


def complete_run_security(
    run_id: str,
    *,
    path: str | os.PathLike[str] | None = None,
) -> None:
    """Mark a run complete and erase its resume-bearing request payload."""

    completed_at = datetime.now(timezone.utc).isoformat()
    with _connect(path) as conn:
        conn.execute(
            """
            UPDATE run_security
            SET status = 'succeeded',
                encrypted_request = NULL,
                encryption_key_id = NULL,
                completed_at = ?
            WHERE run_id = ?
            """,
            (completed_at, run_id),
        )


def authorize_run(
    run_id: str,
    access_hash: str,
    *,
    now: datetime | None = None,
    path: str | os.PathLike[str] | None = None,
) -> bool:
    """Return whether a non-expired capability hash owns ``run_id``."""

    current = now or datetime.now(timezone.utc)
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT access_hash, access_expires_at FROM run_security WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        return False
    stored_hash, access_expires_at = row
    if datetime.fromisoformat(access_expires_at) <= current:
        return False
    return hmac.compare_digest(stored_hash, access_hash)


def load_encrypted_request(
    run_id: str,
    *,
    path: str | os.PathLike[str] | None = None,
) -> tuple[str, str] | None:
    """Return ``(key_id, ciphertext)`` for worker/tests, never plaintext."""

    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT encryption_key_id, encrypted_request
            FROM run_security
            WHERE run_id = ? AND encrypted_request IS NOT NULL
            """,
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    return str(row[0]), str(row[1])


def delete_run(
    run_id: str,
    *,
    path: str | os.PathLike[str] | None = None,
) -> bool:
    """Delete private request metadata, result, and outcomes atomically."""

    with _connect(path) as conn:
        existed = conn.execute(
            "SELECT 1 FROM run_security WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        conn.execute("DELETE FROM outcomes WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM run_security WHERE run_id = ?", (run_id,))
    return existed is not None


def purge_expired_data(
    *,
    now: datetime | None = None,
    path: str | os.PathLike[str] | None = None,
) -> tuple[int, int]:
    """Clear expired requests and delete expired run records.

    Returns ``(request_payloads_cleared, runs_deleted)``.
    """

    current = (now or datetime.now(timezone.utc)).isoformat()
    with _connect(path) as conn:
        cleared = conn.execute(
            """
            UPDATE run_security
            SET encrypted_request = NULL, encryption_key_id = NULL
            WHERE encrypted_request IS NOT NULL AND request_expires_at <= ?
            """,
            (current,),
        ).rowcount
        expired_run_ids = [
            row[0]
            for row in conn.execute(
                "SELECT run_id FROM run_security WHERE access_expires_at <= ?",
                (current,),
            ).fetchall()
        ]
        if expired_run_ids:
            placeholders = ",".join("?" for _ in expired_run_ids)
            conn.execute(
                f"DELETE FROM outcomes WHERE run_id IN ({placeholders})",
                expired_run_ids,
            )
            conn.execute(
                f"DELETE FROM runs WHERE run_id IN ({placeholders})",
                expired_run_ids,
            )
            conn.execute(
                f"DELETE FROM run_security WHERE run_id IN ({placeholders})",
                expired_run_ids,
            )
    return cleared, len(expired_run_ids)


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


def load_draft_outcomes(
    *,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    """Return the best logged outcome keyed by normalized draft message."""

    resolved = _resolve_db_path(path)
    if resolved != ":memory:" and not Path(resolved).exists():
        return {}
    try:
        with _connect(path) as conn:
            run_rows = conn.execute("SELECT run_id, payload FROM runs").fetchall()
            outcome_rows = conn.execute(
                "SELECT run_id, draft_id, payload FROM outcomes "
                "ORDER BY logged_at DESC, id DESC"
            ).fetchall()
    except sqlite3.OperationalError:
        return {}

    messages: dict[tuple[str, str], str] = {}
    for run_id, payload in run_rows:
        try:
            result = HuntResult.model_validate_json(payload)
        except ValueError:
            continue
        for draft in result.outreach:
            messages[(run_id, draft.draft_id)] = normalize_draft_message(draft.message)

    best: dict[str, str] = {}
    seen_entries: set[tuple[str, str]] = set()
    for run_id, draft_id, payload in outcome_rows:
        key = (run_id, draft_id)
        if key in seen_entries:
            continue
        seen_entries.add(key)
        message = messages.get(key)
        if not message:
            continue
        try:
            outcome = OutcomeLog.model_validate_json(payload).outcome
        except ValueError:
            continue
        current = best.get(message)
        if current is None or OUTCOME_PRIORITY[outcome] > OUTCOME_PRIORITY[current]:
            best[message] = outcome
    return best


def normalize_draft_message(message: str) -> str:
    return " ".join(message.split()).casefold()
