"""Plan or apply a non-destructive legacy SQLite hunt-history import.

The source database is opened read-only. Dry-run is the default. ``--apply``
imports only completed results into an already-migrated owner-scoped database,
atomically; unsupported active/failed queue records are reported and left in
the source for manual recovery.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.engine import make_url

from job_hunt_agent import hunt_repository, privacy_repository
from job_hunt_agent.database import Database, DatabaseConfigError, resolve_database_url
from job_hunt_agent.hunt_payloads import (
    encrypt_hunt_outcome,
    encrypt_hunt_result,
)
from job_hunt_agent.models import (
    BackgroundJob,
    BackgroundJobEvent,
    HuntOutcome,
    HuntRun,
    Owner,
)
from job_hunt_agent.schemas import HuntResult, OutcomeLog
from job_hunt_agent.security import (
    DATA_KEYS_ENV,
    DataKeyring,
    DecryptionError,
    SecurityConfigError,
    hash_access_token,
    load_data_keyring,
)


RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
HASH_RE = re.compile(r"^[a-fA-F0-9]{64}$")


class LegacyImportError(RuntimeError):
    pass


@dataclass(frozen=True)
class LegacyRunRecord:
    run_id: str
    result: HuntResult
    outcomes: tuple[OutcomeLog, ...]
    created_at: datetime
    completed_at: datetime
    access_hash: str
    request_hash: str
    attempt_count: int
    max_attempts: int


@dataclass(frozen=True)
class ImportReport:
    mode: str
    source: str
    owner_id: str
    discovered: int
    importable: list[str]
    already_imported: list[str]
    expired: list[dict[str, str]]
    unsupported: list[dict[str, str]]
    failed: list[dict[str, str]]
    imported: list[str]


def import_legacy_hunts(
    source_path: Path,
    target_database_url: str,
    *,
    owner_id: str,
    apply: bool,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> ImportReport:
    source = source_path.expanduser().resolve()
    if not source.is_file():
        raise LegacyImportError("legacy SQLite source does not exist")
    normalized = resolve_database_url(target_database_url, production=False)
    assert normalized is not None
    target_url = make_url(normalized)
    if target_url.get_backend_name() == "sqlite":
        database_path = target_url.database
        if database_path and database_path != ":memory:":
            target_path = Path(database_path).expanduser().resolve()
            if target_path == source:
                raise LegacyImportError(
                    "legacy source and target SQLite database must be different files"
                )
    target = Database(normalized)
    try:
        if not target.reachable() or not target.migrations_current():
            raise LegacyImportError("target database must be reachable and migration-current")
        discovered, candidates, unsupported, failed = _read_source(source)
        importable: list[LegacyRunRecord] = []
        already: list[str] = []
        expired: list[dict[str, str]] = []
        current = _as_utc(now or datetime.now(timezone.utc))
        with target.session() as session:
            if session.get(Owner, owner_id) is None:
                raise LegacyImportError(
                    "target owner does not exist; sign in once or create the configured owner first"
                )
            retention_days = privacy_repository.get_owner_hunt_retention_days(
                session,
                owner_id=owner_id,
            )
            for candidate in candidates:
                existing = session.get(HuntRun, candidate.run_id)
                if existing is None:
                    access_expires_at = candidate.completed_at + timedelta(
                        days=retention_days
                    )
                    if access_expires_at <= current:
                        expired.append(
                            {
                                "run_id": candidate.run_id,
                                "reason": (
                                    "completed result is outside the owner's "
                                    f"{retention_days}-day retention window"
                                ),
                                "expires_at": access_expires_at.isoformat(),
                            }
                        )
                        continue
                    importable.append(candidate)
                    continue
                if existing.owner_id != owner_id:
                    failed.append(
                        {"run_id": candidate.run_id, "reason": "run id belongs to another owner"}
                    )
                    continue
                try:
                    matches = _matches_existing(
                        session,
                        candidate,
                        owner_id=owner_id,
                        keyring=keyring,
                    )
                except (DecryptionError, ValueError):
                    failed.append(
                        {
                            "run_id": candidate.run_id,
                            "reason": "target run cannot be decrypted or validated with configured keys",
                        }
                    )
                    continue
                if not matches:
                    failed.append(
                        {"run_id": candidate.run_id, "reason": "target run differs from source"}
                    )
                    continue
                already.append(candidate.run_id)

        imported: list[str] = []
        if apply:
            if failed:
                raise LegacyImportError(
                    "apply refused because one or more records failed validation; no rows were imported"
                )
            # One transaction: an unexpected conflict rolls back the complete batch.
            with target.session() as session:
                for candidate in importable:
                    if session.get(HuntRun, candidate.run_id) is not None:
                        raise LegacyImportError(
                            f"target changed during import for run {candidate.run_id!r}"
                        )
                    _insert_candidate(
                        session,
                        candidate,
                        owner_id=owner_id,
                        keyring=keyring,
                        retention_days=retention_days,
                    )
                    imported.append(candidate.run_id)
        return ImportReport(
            mode="apply" if apply else "dry_run",
            source=str(source),
            owner_id=owner_id,
            discovered=discovered,
            importable=[candidate.run_id for candidate in importable],
            already_imported=sorted(already),
            expired=expired,
            unsupported=unsupported,
            failed=failed,
            imported=imported,
        )
    finally:
        target.dispose()


def _read_source(
    source: Path,
) -> tuple[int, list[LegacyRunRecord], list[dict[str, str]], list[dict[str, str]]]:
    uri = f"{source.as_uri()}?mode=ro"
    candidates: list[LegacyRunRecord] = []
    unsupported: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        raise LegacyImportError("legacy SQLite source cannot be opened read-only") from exc
    with closing(connection):
        try:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "runs" not in tables:
                raise LegacyImportError("legacy SQLite source has no runs table")
            security = _security_rows(connection) if "run_security" in tables else {}
            outcome_rows = _outcome_rows(connection) if "outcomes" in tables else {}
            rows = connection.execute(
                "SELECT run_id, payload, created_at FROM runs ORDER BY created_at, run_id"
            ).fetchall()
            for row in rows:
                run_id = str(row["run_id"])
                state = security.get(run_id)
                if state is not None and str(state["status"]) != "succeeded":
                    unsupported.append(
                        {
                            "run_id": run_id,
                            "reason": (
                                f"queue status {state['status']!s} is not importable"
                            ),
                        }
                    )
                    continue
                try:
                    result = HuntResult.model_validate_json(row["payload"])
                    if result.run_id != run_id:
                        raise ValueError("result run_id does not match its row")
                    if RUN_ID_RE.fullmatch(run_id) is None:
                        raise ValueError(
                            "run_id is not compatible with the practical schema"
                        )
                    outcomes = tuple(
                        OutcomeLog.model_validate_json(payload)
                        for payload in outcome_rows.get(run_id, [])
                    )
                    known_drafts = {draft.draft_id for draft in result.outreach}
                    if any(entry.draft_id not in known_drafts for entry in outcomes):
                        raise ValueError(
                            "outcome references a draft absent from the result"
                        )
                    created_at = _datetime(row["created_at"])
                    completed_at = _datetime(
                        state["completed_at"]
                        if state is not None
                        else row["created_at"]
                    )
                    raw_access_hash = (
                        str(state["access_hash"]) if state is not None else ""
                    )
                    access_hash = (
                        raw_access_hash.lower()
                        if HASH_RE.fullmatch(raw_access_hash)
                        else hash_access_token(f"legacy-import:{run_id}")
                    )
                    raw_request_hash = (
                        str(state["request_hash"] or "") if state is not None else ""
                    )
                    request_hash = (
                        raw_request_hash.lower()
                        if HASH_RE.fullmatch(raw_request_hash)
                        else hashlib.sha256(
                            result.model_dump_json().encode("utf-8")
                        ).hexdigest()
                    )
                    candidates.append(
                        LegacyRunRecord(
                            run_id=run_id,
                            result=result,
                            outcomes=outcomes,
                            created_at=created_at,
                            completed_at=completed_at,
                            access_hash=access_hash,
                            request_hash=request_hash,
                            attempt_count=(
                                max(1, int(state["attempt_count"] or 1))
                                if state
                                else 1
                            ),
                            max_attempts=(
                                max(1, int(state["max_attempts"] or 3))
                                if state
                                else 3
                            ),
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - report invalid legacy rows.
                    failed.append({"run_id": run_id, "reason": str(exc)})
        except LegacyImportError:
            raise
        except sqlite3.Error as exc:
            raise LegacyImportError(
                "legacy SQLite source schema cannot be read safely"
            ) from exc
    return len(rows), candidates, unsupported, failed


def _security_rows(connection: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(run_security)")
    }
    required = {
        "run_id",
        "status",
        "access_hash",
        "completed_at",
        "request_hash",
        "attempt_count",
        "max_attempts",
    }
    if not required.issubset(columns):
        missing = ", ".join(sorted(required - columns))
        raise LegacyImportError(
            "legacy run_security schema is incompatible; missing columns: " + missing
        )
    return {
        str(row["run_id"]): row
        for row in connection.execute(
            "SELECT run_id, status, access_hash, completed_at, request_hash, "
            "attempt_count, max_attempts FROM run_security"
        )
    }


def _outcome_rows(connection: sqlite3.Connection) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for row in connection.execute(
        "SELECT run_id, payload FROM outcomes ORDER BY logged_at DESC, id DESC"
    ):
        grouped.setdefault(str(row["run_id"]), []).append(str(row["payload"]))
    return grouped


def _matches_existing(
    session,
    candidate: LegacyRunRecord,
    *,
    owner_id: str,
    keyring: DataKeyring,
) -> bool:
    result = hunt_repository.load_hunt_result(
        session,
        owner_id=owner_id,
        hunt_run_id=candidate.run_id,
        keyring=keyring,
    )
    outcomes = hunt_repository.load_hunt_outcomes(
        session,
        owner_id=owner_id,
        hunt_run_id=candidate.run_id,
        keyring=keyring,
    )
    return result == candidate.result and outcomes == list(candidate.outcomes)


def _insert_candidate(
    session,
    candidate: LegacyRunRecord,
    *,
    owner_id: str,
    keyring: DataKeyring,
    retention_days: int,
) -> None:
    job_id = uuid4().hex
    result_envelope = encrypt_hunt_result(
        keyring,
        owner_id=owner_id,
        hunt_run_id=candidate.run_id,
        payload=candidate.result.model_dump(mode="json"),
    )
    job = BackgroundJob(
        id=job_id,
        kind=hunt_repository.HUNT_JOB_KIND,
        owner_id=owner_id,
        dedupe_scope=f"owner:{owner_id}",
        subject_type="hunt_run",
        subject_id=candidate.run_id,
        payload={"hunt_run_id": candidate.run_id},
        dedupe_key=f"legacy-import:{candidate.run_id}",
        status="succeeded",
        attempt_count=candidate.attempt_count,
        max_attempts=candidate.max_attempts,
        run_after=candidate.created_at,
        stage="complete",
        stage_checkpoint="legacy_import",
        version=1,
        created_at=candidate.created_at,
        updated_at=candidate.completed_at,
        started_at=candidate.created_at,
        completed_at=candidate.completed_at,
    )
    session.add(job)
    session.flush()
    session.add(
        BackgroundJobEvent(
            job_id=job_id,
            from_status=None,
            to_status="succeeded",
            actor="legacy-import",
            reason="completed legacy result imported without rerunning providers",
            created_at=candidate.completed_at,
        )
    )
    session.add(
        HuntRun(
            id=candidate.run_id,
            owner_id=owner_id,
            background_job_id=job_id,
            access_hash=candidate.access_hash,
            idempotency_key_hash=None,
            request_hash=candidate.request_hash,
            encrypted_request=None,
            request_key_id=None,
            request_expires_at=candidate.created_at + timedelta(hours=24),
            encrypted_result=result_envelope.ciphertext,
            result_key_id=result_envelope.key_id,
            access_expires_at=candidate.completed_at + timedelta(days=retention_days),
            request_cleared_at=candidate.completed_at,
            completed_at=candidate.completed_at,
            created_at=candidate.created_at,
            updated_at=candidate.completed_at,
        )
    )
    for outcome in reversed(candidate.outcomes):
        logged_at = outcome.logged_at or candidate.completed_at
        payload = outcome.model_dump(mode="json")
        placeholder = encrypt_hunt_outcome(
            keyring,
            owner_id=owner_id,
            outcome_id=f"pending:{uuid4().hex}",
            draft_id=outcome.draft_id,
            payload=payload,
        )
        row = HuntOutcome(
            hunt_run_id=candidate.run_id,
            draft_id=outcome.draft_id,
            encrypted_payload=placeholder.ciphertext,
            encryption_key_id=placeholder.key_id,
            logged_at=logged_at,
        )
        session.add(row)
        session.flush()
        envelope = encrypt_hunt_outcome(
            keyring,
            owner_id=owner_id,
            outcome_id=str(row.id),
            draft_id=row.draft_id,
            payload=payload,
        )
        row.encrypted_payload = envelope.ciphertext
        row.encryption_key_id = envelope.key_id
    session.flush()


def _datetime(value: object) -> datetime:
    if value is None:
        raise ValueError("required legacy timestamp is missing")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument(
        "--database-url",
        help="target URL; prefer DATABASE_URL so credentials do not appear in process argv",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--allow-development-key",
        action="store_true",
        help="allow the deterministic local key only outside production",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    database_url = args.database_url or os.getenv("DATABASE_URL", "")
    if not database_url.strip():
        print("legacy import failed: DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        production = os.getenv("ENVIRONMENT", "").strip().lower() == "production"
        if not os.getenv(DATA_KEYS_ENV, "").strip() and not args.allow_development_key:
            raise LegacyImportError(
                f"{DATA_KEYS_ENV} must be configured for import; the development key "
                "requires --allow-development-key"
            )
        if production and args.allow_development_key:
            raise LegacyImportError(
                "--allow-development-key is forbidden when ENVIRONMENT=production"
            )
        report = import_legacy_hunts(
            args.source,
            database_url,
            owner_id=args.owner_id,
            apply=args.apply,
            keyring=load_data_keyring(production=production),
        )
    except (DatabaseConfigError, LegacyImportError, SecurityConfigError) as exc:
        print(f"legacy import failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(asdict(report), indent=2, sort_keys=True))
    return 1 if (report.failed or report.unsupported or report.expired) else 0


if __name__ == "__main__":
    raise SystemExit(main())
