"""SQLite persistence for hunt results, private queue state, and outcomes.

Completed hunt results remain JSON blobs. Resume-bearing request payloads live
in a separate security table and must be encrypted before they reach this
module. Access capabilities are stored only as SHA-256 hashes.

The DB path resolves from the ``JOB_HUNT_DB_PATH`` env var (default
``outcomes.db`` in the working directory) so tests can point at a
``tmp_path`` and production at a writable host path without code changes.
"""

from __future__ import annotations

import hmac
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from .schemas import HuntResult, OutcomeLog


DEFAULT_DB_PATH = "outcomes.db"
DEFAULT_MAX_ATTEMPTS = 3
RunStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "dead_letter",
]
ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "dead_letter"}
OUTCOME_PRIORITY = {
    "introduced": 3,
    "replied": 2,
    "pending": 1,
    "rejected": -1,
    "no_reply": -2,
}
_QUEUE_COLUMNS = """
    run_id,
    status,
    attempt_count,
    max_attempts,
    stage,
    stage_checkpoint,
    lease_token,
    lease_owner,
    lease_expires_at,
    heartbeat_at,
    next_attempt_at,
    last_error,
    created_at,
    updated_at,
    queued_at,
    started_at,
    completed_at,
    cancel_requested_at,
    cancelled_at,
    failed_at,
    dead_lettered_at,
    requeued_at
"""


@dataclass(frozen=True)
class RunQueueState:
    """Public queue metadata for a private run, excluding encrypted bytes."""

    run_id: str
    status: str
    attempt_count: int
    max_attempts: int
    stage: str
    stage_checkpoint: str | None
    lease_token: str | None
    lease_owner: str | None
    lease_expires_at: str | None
    heartbeat_at: str | None
    next_attempt_at: str | None
    last_error: str | None
    created_at: str
    updated_at: str
    queued_at: str | None
    started_at: str | None
    completed_at: str | None
    cancel_requested_at: str | None
    cancelled_at: str | None
    failed_at: str | None
    dead_lettered_at: str | None
    requeued_at: str | None


@dataclass(frozen=True)
class IdempotencyHit:
    """Existing queued run found for a submitted idempotency key."""

    state: RunQueueState
    request_hash: str | None


def _resolve_db_path(path: str | os.PathLike[str] | None) -> str:
    if path is not None:
        return str(path)
    return os.getenv("JOB_HUNT_DB_PATH", DEFAULT_DB_PATH)


def _connect(path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    resolved = _resolve_db_path(path)
    parent = Path(resolved).parent
    if parent and str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(resolved, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).isoformat()


def _state_from_row(row: sqlite3.Row | None) -> RunQueueState | None:
    if row is None:
        return None
    return RunQueueState(
        run_id=str(row["run_id"]),
        status=str(row["status"]),
        attempt_count=int(row["attempt_count"]),
        max_attempts=int(row["max_attempts"]),
        stage=str(row["stage"]),
        stage_checkpoint=row["stage_checkpoint"],
        lease_token=row["lease_token"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        heartbeat_at=row["heartbeat_at"],
        next_attempt_at=row["next_attempt_at"],
        last_error=row["last_error"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        queued_at=row["queued_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        cancel_requested_at=row["cancel_requested_at"],
        cancelled_at=row["cancelled_at"],
        failed_at=row["failed_at"],
        dead_lettered_at=row["dead_lettered_at"],
        requeued_at=row["requeued_at"],
    )


def _load_state_conn(conn: sqlite3.Connection, run_id: str) -> RunQueueState | None:
    return _state_from_row(
        conn.execute(
            f"SELECT {_QUEUE_COLUMNS} FROM run_security WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    )


def _worker_owns_live_lease(
    state: RunQueueState,
    *,
    worker_id: str,
    lease_token: str | None,
    now: datetime,
) -> bool:
    if (
        state.status != "running"
        or state.lease_owner != worker_id
        or state.lease_token != lease_token
        or state.lease_expires_at is None
    ):
        return False
    try:
        expires_at = datetime.fromisoformat(state.lease_expires_at)
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    current = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return expires_at.astimezone(timezone.utc) > current.astimezone(timezone.utc)


def _record_event(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    from_status: str | None,
    to_status: str,
    actor: str,
    reason: str | None = None,
    now: datetime | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO run_events (
            run_id, from_status, to_status, actor, reason, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (run_id, from_status, to_status, actor, reason, _iso(now)),
    )


def _ensure_run_security_columns(conn: sqlite3.Connection) -> None:
    """Migrate older Phase-1 SQLite databases in place."""

    existing = {row["name"] for row in conn.execute("PRAGMA table_info(run_security)")}
    columns = {
        "queued_at": "TEXT",
        "started_at": "TEXT",
        "cancel_requested_at": "TEXT",
        "cancelled_at": "TEXT",
        "failed_at": "TEXT",
        "dead_lettered_at": "TEXT",
        "requeued_at": "TEXT",
        "updated_at": "TEXT",
        "idempotency_key_hash": "TEXT",
        "request_hash": "TEXT",
        "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        "max_attempts": f"INTEGER NOT NULL DEFAULT {DEFAULT_MAX_ATTEMPTS}",
        "lease_token": "TEXT",
        "lease_owner": "TEXT",
        "lease_expires_at": "TEXT",
        "heartbeat_at": "TEXT",
        "next_attempt_at": "TEXT",
        "last_error": "TEXT",
        "stage": "TEXT NOT NULL DEFAULT 'queued'",
        "stage_checkpoint": "TEXT",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE run_security ADD COLUMN {name} {definition}")
    conn.execute(
        """
        UPDATE run_security
        SET queued_at = COALESCE(queued_at, created_at),
            updated_at = COALESCE(updated_at, completed_at, created_at),
            stage = COALESCE(stage, status),
            max_attempts = COALESCE(max_attempts, ?),
            attempt_count = COALESCE(attempt_count, 0)
        """,
        (DEFAULT_MAX_ATTEMPTS,),
    )


def init_db(path: str | os.PathLike[str] | None = None) -> None:
    """Create and migrate the queue, runs, outcomes, and audit tables."""
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
                run_id                TEXT PRIMARY KEY,
                access_hash           TEXT NOT NULL,
                status                TEXT NOT NULL,
                encrypted_request     TEXT,
                encryption_key_id     TEXT,
                request_expires_at    TEXT NOT NULL,
                access_expires_at     TEXT NOT NULL,
                created_at            TEXT NOT NULL,
                completed_at          TEXT,
                queued_at             TEXT,
                started_at            TEXT,
                cancel_requested_at   TEXT,
                cancelled_at          TEXT,
                failed_at             TEXT,
                dead_lettered_at      TEXT,
                requeued_at           TEXT,
                updated_at            TEXT,
                idempotency_key_hash  TEXT,
                request_hash          TEXT,
                attempt_count         INTEGER NOT NULL DEFAULT 0,
                max_attempts          INTEGER NOT NULL DEFAULT 3,
                lease_token           TEXT,
                lease_owner           TEXT,
                lease_expires_at      TEXT,
                heartbeat_at          TEXT,
                next_attempt_at       TEXT,
                last_error            TEXT,
                stage                 TEXT NOT NULL DEFAULT 'queued',
                stage_checkpoint      TEXT
            );

            CREATE TABLE IF NOT EXISTS run_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      TEXT NOT NULL,
                from_status TEXT,
                to_status   TEXT NOT NULL,
                actor       TEXT NOT NULL,
                reason      TEXT,
                created_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_outcomes_logged_at
                ON outcomes(logged_at);
            CREATE INDEX IF NOT EXISTS idx_run_security_status
                ON run_security(status);
            CREATE INDEX IF NOT EXISTS idx_run_security_lease
                ON run_security(status, lease_expires_at);
            CREATE INDEX IF NOT EXISTS idx_run_security_next_attempt
                ON run_security(status, next_attempt_at);
            CREATE INDEX IF NOT EXISTS idx_run_security_request_expiry
                ON run_security(request_expires_at);
            CREATE INDEX IF NOT EXISTS idx_run_security_access_expiry
                ON run_security(access_expires_at);
            CREATE INDEX IF NOT EXISTS idx_run_events_run
                ON run_events(run_id, created_at);
            """
        )
        _ensure_run_security_columns(conn)
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_run_security_idempotency
            ON run_security(idempotency_key_hash)
            WHERE idempotency_key_hash IS NOT NULL
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
    idempotency_key_hash: str | None = None,
    request_hash: str | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    path: str | os.PathLike[str] | None = None,
) -> RunQueueState:
    """Store encrypted request state as a queued job before work starts."""

    now = _utcnow()
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
                created_at,
                updated_at,
                queued_at,
                idempotency_key_hash,
                request_hash,
                max_attempts,
                stage,
                stage_checkpoint
            ) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 'queued')
            """,
            (
                run_id,
                access_hash,
                encrypted_request,
                encryption_key_id,
                request_expires_at.isoformat(),
                access_expires_at.isoformat(),
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
                idempotency_key_hash,
                request_hash,
                max_attempts,
            ),
        )
        _record_event(
            conn,
            run_id,
            from_status=None,
            to_status="queued",
            actor="api",
            reason="hunt queued",
            now=now,
        )
        state = _load_state_conn(conn, run_id)
    assert state is not None
    return state


def replace_access_hash(
    run_id: str,
    *,
    access_hash: str,
    actor: str = "api",
    path: str | os.PathLike[str] | None = None,
) -> bool:
    """Replace a run capability hash without storing the plaintext token."""

    now = _utcnow()
    with _connect(path) as conn:
        updated = conn.execute(
            """
            UPDATE run_security
            SET access_hash = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (access_hash, now.isoformat(), run_id),
        )
        if updated.rowcount == 1:
            _record_event(
                conn,
                run_id,
                from_status=None,
                to_status="access_rotated",
                actor=actor,
                reason="idempotent duplicate issued a fresh capability",
                now=now,
            )
    return updated.rowcount == 1


def complete_run_security(
    run_id: str,
    *,
    path: str | os.PathLike[str] | None = None,
) -> bool:
    """Mark a run complete and erase its resume-bearing request payload."""

    return mark_run_succeeded(run_id, path=path) is not None


def authorize_run(
    run_id: str,
    access_hash: str,
    *,
    now: datetime | None = None,
    path: str | os.PathLike[str] | None = None,
) -> bool:
    """Return whether a non-expired capability hash owns ``run_id``."""

    current = now or _utcnow()
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


def find_run_by_idempotency_key(
    idempotency_key_hash: str,
    *,
    path: str | os.PathLike[str] | None = None,
) -> IdempotencyHit | None:
    """Return an existing run for an idempotency key without exposing secrets."""

    with _connect(path) as conn:
        row = conn.execute(
            f"""
            SELECT {_QUEUE_COLUMNS}, request_hash
            FROM run_security
            WHERE idempotency_key_hash = ?
            """,
            (idempotency_key_hash,),
        ).fetchone()
    if row is None:
        return None
    state = _state_from_row(row)
    assert state is not None
    return IdempotencyHit(state=state, request_hash=row["request_hash"])


def get_run_state(
    run_id: str,
    *,
    path: str | os.PathLike[str] | None = None,
) -> RunQueueState | None:
    """Return queue metadata for a run without result/outcome payloads."""

    with _connect(path) as conn:
        return _load_state_conn(conn, run_id)


def list_queue_states(
    *,
    status: str | None = None,
    path: str | os.PathLike[str] | None = None,
) -> list[RunQueueState]:
    """Return queue states for tests/admin diagnostics."""

    with _connect(path) as conn:
        if status is None:
            rows = conn.execute(
                f"SELECT {_QUEUE_COLUMNS} FROM run_security ORDER BY created_at"
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT {_QUEUE_COLUMNS}
                FROM run_security
                WHERE status = ?
                ORDER BY created_at
                """,
                (status,),
            ).fetchall()
    return [state for row in rows if (state := _state_from_row(row)) is not None]


def load_encrypted_request(
    run_id: str,
    *,
    path: str | os.PathLike[str] | None = None,
) -> tuple[str, str] | None:
    """Return ``(key_id, ciphertext)`` for workers/tests, never plaintext."""

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


def recover_stale_leases(
    *,
    now: datetime | None = None,
    path: str | os.PathLike[str] | None = None,
) -> tuple[int, int]:
    """Requeue or dead-letter running jobs whose worker lease expired."""

    current = now or _utcnow()
    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        return _recover_stale_leases_conn(conn, current)


def _recover_stale_leases_conn(
    conn: sqlite3.Connection,
    now: datetime,
) -> tuple[int, int]:
    current = now.isoformat()
    rows = conn.execute(
        f"""
        SELECT {_QUEUE_COLUMNS}
        FROM run_security
        WHERE status = 'running'
          AND lease_expires_at IS NOT NULL
          AND lease_expires_at <= ?
        ORDER BY lease_expires_at
        """,
        (current,),
    ).fetchall()
    requeued = 0
    dead_lettered = 0
    for row in rows:
        run_id = row["run_id"]
        if int(row["attempt_count"]) >= int(row["max_attempts"]):
            conn.execute(
                """
                UPDATE run_security
                SET status = 'dead_letter',
                    lease_token = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    dead_lettered_at = ?,
                    updated_at = ?,
                    stage = 'dead_letter',
                    stage_checkpoint = 'dead_letter'
                WHERE run_id = ? AND status = 'running'
                """,
                (current, current, run_id),
            )
            _record_event(
                conn,
                run_id,
                from_status="running",
                to_status="dead_letter",
                actor="system",
                reason="worker lease expired after max attempts",
                now=now,
            )
            dead_lettered += 1
        else:
            conn.execute(
                """
                UPDATE run_security
                SET status = 'queued',
                    lease_token = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    next_attempt_at = ?,
                    queued_at = ?,
                    updated_at = ?,
                    stage = 'lease_expired',
                    stage_checkpoint = 'queued'
                WHERE run_id = ? AND status = 'running'
                """,
                (current, current, current, run_id),
            )
            _record_event(
                conn,
                run_id,
                from_status="running",
                to_status="queued",
                actor="system",
                reason="worker lease expired",
                now=now,
            )
            requeued += 1
    return requeued, dead_lettered


def claim_next_run(
    *,
    worker_id: str,
    lease_token: str,
    lease_seconds: int = 300,
    now: datetime | None = None,
    path: str | os.PathLike[str] | None = None,
) -> RunQueueState | None:
    """Atomically claim one queued run for a worker lease."""

    current = now or _utcnow()
    lease_expires_at = current + timedelta(seconds=lease_seconds)
    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _recover_stale_leases_conn(conn, current)
        row = conn.execute(
            f"""
            SELECT {_QUEUE_COLUMNS}
            FROM run_security
            WHERE status = 'queued'
              AND encrypted_request IS NOT NULL
              AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
            ORDER BY queued_at, created_at
            LIMIT 1
            """,
            (current.isoformat(),),
        ).fetchone()
        if row is None:
            return None
        run_id = str(row["run_id"])
        updated = conn.execute(
            """
            UPDATE run_security
            SET status = 'running',
                attempt_count = attempt_count + 1,
                lease_token = ?,
                lease_owner = ?,
                lease_expires_at = ?,
                heartbeat_at = ?,
                started_at = COALESCE(started_at, ?),
                updated_at = ?,
                stage = 'claimed',
                stage_checkpoint = 'running'
            WHERE run_id = ? AND status = 'queued'
            """,
            (
                lease_token,
                worker_id,
                lease_expires_at.isoformat(),
                current.isoformat(),
                current.isoformat(),
                current.isoformat(),
                run_id,
            ),
        )
        if updated.rowcount != 1:
            return None
        _record_event(
            conn,
            run_id,
            from_status="queued",
            to_status="running",
            actor=worker_id,
            reason="worker claimed lease",
            now=current,
        )
        return _load_state_conn(conn, run_id)


def heartbeat_run(
    run_id: str,
    *,
    worker_id: str,
    lease_token: str,
    lease_seconds: int = 300,
    now: datetime | None = None,
    path: str | os.PathLike[str] | None = None,
) -> bool:
    """Extend a running lease if the same worker still owns it."""

    current = now or _utcnow()
    lease_expires_at = current + timedelta(seconds=lease_seconds)
    with _connect(path) as conn:
        updated = conn.execute(
            """
            UPDATE run_security
            SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
            WHERE run_id = ?
              AND status = 'running'
              AND lease_owner = ?
              AND lease_token = ?
              AND lease_expires_at IS NOT NULL
              AND julianday(lease_expires_at) > julianday(?)
            """,
            (
                current.isoformat(),
                lease_expires_at.isoformat(),
                current.isoformat(),
                run_id,
                worker_id,
                lease_token,
                current.isoformat(),
            ),
        )
    return updated.rowcount == 1


def update_run_stage(
    run_id: str,
    *,
    worker_id: str,
    lease_token: str,
    stage: str,
    checkpoint: str | None = None,
    now: datetime | None = None,
    path: str | os.PathLike[str] | None = None,
) -> bool:
    """Record coarse worker progress without storing sensitive content."""

    current = now or _utcnow()
    with _connect(path) as conn:
        updated = conn.execute(
            """
            UPDATE run_security
            SET stage = ?,
                stage_checkpoint = COALESCE(?, stage_checkpoint),
                updated_at = ?
            WHERE run_id = ?
              AND status = 'running'
              AND lease_owner = ?
              AND lease_token = ?
              AND lease_expires_at IS NOT NULL
              AND julianday(lease_expires_at) > julianday(?)
            """,
            (
                _sanitize_stage(stage),
                checkpoint,
                current.isoformat(),
                run_id,
                worker_id,
                lease_token,
                current.isoformat(),
            ),
        )
    return updated.rowcount == 1


def complete_run_with_result(
    hunt_result: HuntResult,
    *,
    worker_id: str,
    lease_token: str,
    now: datetime | None = None,
    path: str | os.PathLike[str] | None = None,
) -> RunQueueState | None:
    """Atomically save the result, mark succeeded, and clear request ciphertext."""

    current = now or _utcnow()
    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        before = _load_state_conn(conn, hunt_result.run_id)
        if before is None:
            return None
        if not _worker_owns_live_lease(
            before,
            worker_id=worker_id,
            lease_token=lease_token,
            now=current,
        ):
            return before
        conn.execute(
            "INSERT INTO runs (run_id, payload, created_at) VALUES (?, ?, ?)",
            (hunt_result.run_id, hunt_result.model_dump_json(), current.isoformat()),
        )
        conn.execute(
            """
            UPDATE run_security
            SET status = 'succeeded',
                encrypted_request = NULL,
                encryption_key_id = NULL,
                lease_token = NULL,
                lease_owner = NULL,
                lease_expires_at = NULL,
                heartbeat_at = NULL,
                completed_at = ?,
                updated_at = ?,
                stage = 'succeeded',
                stage_checkpoint = 'succeeded',
                last_error = NULL
            WHERE run_id = ?
            """,
            (current.isoformat(), current.isoformat(), hunt_result.run_id),
        )
        _record_event(
            conn,
            hunt_result.run_id,
            from_status="running",
            to_status="succeeded",
            actor=worker_id,
            reason="worker completed hunt",
            now=current,
        )
        return _load_state_conn(conn, hunt_result.run_id)


def mark_run_succeeded(
    run_id: str,
    *,
    worker_id: str | None = None,
    lease_token: str | None = None,
    now: datetime | None = None,
    path: str | os.PathLike[str] | None = None,
) -> RunQueueState | None:
    """Mark an already-saved run succeeded and clear request ciphertext."""

    current = now or _utcnow()
    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        before = _load_state_conn(conn, run_id)
        if before is None:
            return None
        if before.status == "cancelled":
            return before
        if worker_id is not None and not _worker_owns_live_lease(
            before,
            worker_id=worker_id,
            lease_token=lease_token,
            now=current,
        ):
            return before
        updated = conn.execute(
            """
            UPDATE run_security
            SET status = 'succeeded',
                encrypted_request = NULL,
                encryption_key_id = NULL,
                lease_token = NULL,
                lease_owner = NULL,
                lease_expires_at = NULL,
                heartbeat_at = NULL,
                completed_at = COALESCE(completed_at, ?),
                updated_at = ?,
                stage = 'succeeded',
                stage_checkpoint = 'succeeded',
                last_error = NULL
            WHERE run_id = ?
            """,
            (current.isoformat(), current.isoformat(), run_id),
        )
        if updated.rowcount != 1:
            return before
        _record_event(
            conn,
            run_id,
            from_status=before.status,
            to_status="succeeded",
            actor=worker_id or "system",
            reason="run completed",
            now=current,
        )
        return _load_state_conn(conn, run_id)


def mark_run_failed(
    run_id: str,
    *,
    error: str,
    worker_id: str | None = None,
    lease_token: str | None = None,
    now: datetime | None = None,
    path: str | os.PathLike[str] | None = None,
) -> RunQueueState | None:
    """Mark a run terminal failed and clear request ciphertext."""

    current = now or _utcnow()
    sanitized_error = _sanitize_error(error)
    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        before = _load_state_conn(conn, run_id)
        if before is None:
            return None
        if worker_id is not None and not _worker_owns_live_lease(
            before,
            worker_id=worker_id,
            lease_token=lease_token,
            now=current,
        ):
            return before
        conn.execute(
            """
            UPDATE run_security
            SET status = 'failed',
                encrypted_request = NULL,
                encryption_key_id = NULL,
                lease_token = NULL,
                lease_owner = NULL,
                lease_expires_at = NULL,
                heartbeat_at = NULL,
                failed_at = ?,
                updated_at = ?,
                last_error = ?,
                stage = 'failed',
                stage_checkpoint = 'failed'
            WHERE run_id = ?
            """,
            (current.isoformat(), current.isoformat(), sanitized_error, run_id),
        )
        _record_event(
            conn,
            run_id,
            from_status=before.status,
            to_status="failed",
            actor=worker_id or "system",
            reason=sanitized_error,
            now=current,
        )
        return _load_state_conn(conn, run_id)


def mark_run_attempt_failed(
    run_id: str,
    *,
    worker_id: str,
    lease_token: str,
    error: str,
    retry_delay_seconds: int = 0,
    now: datetime | None = None,
    path: str | os.PathLike[str] | None = None,
) -> RunQueueState | None:
    """Return a failed attempt to the queue or dead-letter after max attempts."""

    current = now or _utcnow()
    next_attempt_at = current + timedelta(seconds=retry_delay_seconds)
    sanitized_error = _sanitize_error(error)
    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        before = _load_state_conn(conn, run_id)
        if before is None:
            return None
        if not _worker_owns_live_lease(
            before,
            worker_id=worker_id,
            lease_token=lease_token,
            now=current,
        ):
            return before
        if before.attempt_count >= before.max_attempts:
            to_status = "dead_letter"
            conn.execute(
                """
                UPDATE run_security
                SET status = 'dead_letter',
                    lease_token = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    dead_lettered_at = ?,
                    updated_at = ?,
                    last_error = ?,
                    stage = 'dead_letter',
                    stage_checkpoint = 'dead_letter'
                WHERE run_id = ?
                """,
                (current.isoformat(), current.isoformat(), sanitized_error, run_id),
            )
        else:
            to_status = "queued"
            conn.execute(
                """
                UPDATE run_security
                SET status = 'queued',
                    lease_token = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    next_attempt_at = ?,
                    queued_at = ?,
                    updated_at = ?,
                    last_error = ?,
                    stage = 'retry_waiting',
                    stage_checkpoint = 'queued'
                WHERE run_id = ?
                """,
                (
                    next_attempt_at.isoformat(),
                    current.isoformat(),
                    current.isoformat(),
                    sanitized_error,
                    run_id,
                ),
            )
        _record_event(
            conn,
            run_id,
            from_status="running",
            to_status=to_status,
            actor=worker_id,
            reason=sanitized_error,
            now=current,
        )
        return _load_state_conn(conn, run_id)


def cancel_run(
    run_id: str,
    *,
    actor: str = "user",
    path: str | os.PathLike[str] | None = None,
) -> RunQueueState | None:
    """Cancel a queued/running run and erase retryable request ciphertext."""

    now = _utcnow()
    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        before = _load_state_conn(conn, run_id)
        if before is None:
            return None
        if before.status not in ACTIVE_STATUSES:
            return before
        conn.execute(
            """
            UPDATE run_security
            SET status = 'cancelled',
                encrypted_request = NULL,
                encryption_key_id = NULL,
                lease_token = NULL,
                lease_owner = NULL,
                lease_expires_at = NULL,
                heartbeat_at = NULL,
                cancel_requested_at = COALESCE(cancel_requested_at, ?),
                cancelled_at = ?,
                updated_at = ?,
                last_error = NULL,
                stage = 'cancelled',
                stage_checkpoint = 'cancelled'
            WHERE run_id = ?
            """,
            (now.isoformat(), now.isoformat(), now.isoformat(), run_id),
        )
        _record_event(
            conn,
            run_id,
            from_status=before.status,
            to_status="cancelled",
            actor=actor,
            reason="cancel requested",
            now=now,
        )
        return _load_state_conn(conn, run_id)


def requeue_dead_letter(
    run_id: str,
    *,
    actor: str,
    reason: str | None = None,
    path: str | os.PathLike[str] | None = None,
) -> RunQueueState | None:
    """Move a dead-lettered run back to queued through an operator action."""

    now = _utcnow()
    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        before = _load_state_conn(conn, run_id)
        if before is None:
            return None
        if before.status != "dead_letter":
            return before
        encrypted_exists = conn.execute(
            """
            SELECT 1 FROM run_security
            WHERE run_id = ? AND encrypted_request IS NOT NULL
            """,
            (run_id,),
        ).fetchone()
        if encrypted_exists is None:
            return before
        conn.execute(
            """
            UPDATE run_security
            SET status = 'queued',
                attempt_count = 0,
                lease_token = NULL,
                lease_owner = NULL,
                lease_expires_at = NULL,
                heartbeat_at = NULL,
                next_attempt_at = NULL,
                queued_at = ?,
                requeued_at = ?,
                updated_at = ?,
                last_error = NULL,
                stage = 'operator_requeued',
                stage_checkpoint = 'queued'
            WHERE run_id = ?
            """,
            (now.isoformat(), now.isoformat(), now.isoformat(), run_id),
        )
        _record_event(
            conn,
            run_id,
            from_status="dead_letter",
            to_status="queued",
            actor=actor,
            reason=reason or "operator requeue",
            now=now,
        )
        return _load_state_conn(conn, run_id)


def load_run_events(
    run_id: str,
    *,
    path: str | os.PathLike[str] | None = None,
) -> list[dict[str, str | None]]:
    """Return auditable queue transitions for a run."""

    with _connect(path) as conn:
        rows = conn.execute(
            """
            SELECT from_status, to_status, actor, reason, created_at
            FROM run_events
            WHERE run_id = ?
            ORDER BY id
            """,
            (run_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_run(
    run_id: str,
    *,
    path: str | os.PathLike[str] | None = None,
) -> bool:
    """Delete private request metadata, queue audit, result, and outcomes atomically."""

    with _connect(path) as conn:
        existed = conn.execute(
            "SELECT 1 FROM run_security WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        conn.execute("DELETE FROM outcomes WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM run_events WHERE run_id = ?", (run_id,))
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

    current = (now or _utcnow()).isoformat()
    with _connect(path) as conn:
        cleared = conn.execute(
            """
            UPDATE run_security
            SET encrypted_request = NULL,
                encryption_key_id = NULL,
                status = CASE
                    WHEN status IN ('queued', 'running', 'dead_letter') THEN 'failed'
                    ELSE status
                END,
                lease_token = NULL,
                lease_owner = NULL,
                lease_expires_at = NULL,
                heartbeat_at = NULL,
                failed_at = CASE
                    WHEN status IN ('queued', 'running', 'dead_letter') THEN ?
                    ELSE failed_at
                END,
                updated_at = ?,
                last_error = COALESCE(last_error, 'encrypted request expired'),
                stage = CASE
                    WHEN status IN ('queued', 'running', 'dead_letter') THEN 'request_expired'
                    ELSE stage
                END,
                stage_checkpoint = CASE
                    WHEN status IN ('queued', 'running', 'dead_letter') THEN 'failed'
                    ELSE stage_checkpoint
                END
            WHERE encrypted_request IS NOT NULL AND request_expires_at <= ?
            """,
            (current, current, current),
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
                f"DELETE FROM run_events WHERE run_id IN ({placeholders})",
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
    created_at = _iso()
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
        logged_at = _utcnow()
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


def _sanitize_error(error: str) -> str:
    return " ".join(error.split())[:500]


def _sanitize_stage(stage: str) -> str:
    return " ".join(stage.split())[:80] or "running"
