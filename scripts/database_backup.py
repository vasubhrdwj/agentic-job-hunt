"""Create, verify, and safely restore SQLite or PostgreSQL backups.

PostgreSQL commands never place the password in argv. Restore refuses any
target that already has tables; this tool intentionally has no destructive
``--clean`` mode.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable, Sequence

from sqlalchemy import inspect
from sqlalchemy.engine import URL, make_url

from job_hunt_agent.database import MIGRATION_HEAD, Database, resolve_database_url


MANIFEST_FORMAT_VERSION = 1
Runner = Callable[..., subprocess.CompletedProcess[str]]


class BackupError(RuntimeError):
    """Raised before an unsafe or unverifiable backup operation."""


@dataclass(frozen=True)
class BackupManifest:
    format_version: int
    backend: str
    source_identity_hash: str
    created_at: str
    migration_revision: str | None
    sha256: str
    size_bytes: int


def manifest_path(backup_path: Path) -> Path:
    return backup_path.with_name(f"{backup_path.name}.manifest.json")


def create_backup(
    database_url: str,
    backup_path: Path,
    *,
    runner: Runner = subprocess.run,
) -> BackupManifest:
    """Create an atomic backup plus checksum/revision manifest."""

    normalized = resolve_database_url(database_url, production=False)
    assert normalized is not None
    url = make_url(normalized)
    backend = url.get_backend_name()
    if backend not in {"sqlite", "postgresql"}:
        raise BackupError(f"unsupported database backend {backend!r}")
    backup_path = backup_path.expanduser().resolve()
    sidecar = manifest_path(backup_path)
    if backend == "sqlite":
        source_path = _sqlite_path(url)
        if backup_path == source_path or sidecar == source_path:
            raise BackupError(
                "backup and manifest paths must not replace the live SQLite database"
            )
    if backup_path.exists() or sidecar.exists():
        raise BackupError("backup or manifest already exists; choose a new path")
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    database = Database(normalized)
    try:
        if not database.reachable():
            raise BackupError("source database is not reachable")
        revision = database.current_migration_revision()
    finally:
        database.dispose()

    with NamedTemporaryFile(
        prefix=f".{backup_path.name}.",
        suffix=".tmp",
        dir=backup_path.parent,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        if backend == "sqlite":
            _backup_sqlite(url, temporary_path)
        else:
            _backup_postgres(url, temporary_path, runner=runner)
        digest, size = _digest(temporary_path)
        result = BackupManifest(
            format_version=MANIFEST_FORMAT_VERSION,
            backend=backend,
            source_identity_hash=database_identity_hash(normalized),
            created_at=datetime.now(timezone.utc).isoformat(),
            migration_revision=revision,
            sha256=digest,
            size_bytes=size,
        )
        manifest_temporary = _write_manifest_temporary(sidecar, result)
        try:
            os.replace(temporary_path, backup_path)
            try:
                os.replace(manifest_temporary, sidecar)
            except Exception:
                # Never leave an archive that appears usable without its
                # matching integrity manifest.
                backup_path.unlink(missing_ok=True)
                raise
        finally:
            manifest_temporary.unlink(missing_ok=True)
        return result
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def verify_backup(
    backup_path: Path,
    *,
    expect_current: bool = False,
    runner: Runner = subprocess.run,
) -> BackupManifest:
    """Verify manifest, checksum, archive integrity, and migration revision."""

    backup_path = backup_path.expanduser().resolve()
    if not backup_path.is_file():
        raise BackupError("backup file does not exist")
    result = _read_manifest(manifest_path(backup_path))
    digest, size = _digest(backup_path)
    if digest != result.sha256 or size != result.size_bytes:
        raise BackupError("backup checksum or size does not match its manifest")
    if result.backend == "sqlite":
        revision = _verify_sqlite(backup_path)
        if revision != result.migration_revision:
            raise BackupError("SQLite backup revision does not match its manifest")
    elif result.backend == "postgresql":
        _run_checked(
            ["pg_restore", "--list", str(backup_path)],
            runner=runner,
            env=os.environ.copy(),
        )
    else:
        raise BackupError(f"unsupported manifest backend {result.backend!r}")
    if expect_current and result.migration_revision != MIGRATION_HEAD:
        raise BackupError(
            f"backup revision {result.migration_revision!r} is not current {MIGRATION_HEAD!r}"
        )
    return result


def restore_backup(
    backup_path: Path,
    target_database_url: str,
    *,
    confirm_empty_target: bool,
    runner: Runner = subprocess.run,
) -> BackupManifest:
    """Restore only into a confirmed empty target and verify the result."""

    if not confirm_empty_target:
        raise BackupError("restore requires explicit confirmation of an empty target")
    result = verify_backup(backup_path, runner=runner)
    normalized = resolve_database_url(target_database_url, production=False)
    assert normalized is not None
    url = make_url(normalized)
    if url.get_backend_name() != result.backend:
        raise BackupError("backup and target database backends do not match")
    if result.backend == "sqlite":
        target = _sqlite_path(url)
        if target.exists() and target.stat().st_size > 0:
            raise BackupError("SQLite restore target already contains data")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.restore.tmp")
        if temporary.exists():
            raise BackupError("stale SQLite restore temporary file exists")
        shutil.copyfile(backup_path, temporary)
        try:
            _verify_sqlite(temporary)
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    else:
        target_database = Database(normalized)
        try:
            if not target_database.reachable():
                raise BackupError("PostgreSQL restore target is not reachable")
            if inspect(target_database.engine).get_table_names():
                raise BackupError("PostgreSQL restore target is not empty")
        finally:
            target_database.dispose()
        args, env = _postgres_connection_args(url)
        _run_checked(
            [
                "pg_restore",
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-privileges",
                *args,
                str(backup_path),
            ],
            runner=runner,
            env=env,
        )

    restored = Database(normalized)
    try:
        if not restored.reachable():
            raise BackupError("restored database is not reachable")
        revision = restored.current_migration_revision()
    finally:
        restored.dispose()
    if revision != result.migration_revision:
        raise BackupError("restored migration revision does not match the backup")
    return result


def _backup_sqlite(url: URL, target: Path) -> None:
    source = _sqlite_path(url)
    if not source.is_file():
        raise BackupError("SQLite source database does not exist")
    if source.resolve() == target.resolve():
        raise BackupError("SQLite source and backup paths must differ")
    source_uri = f"{source.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source_connection:
        with sqlite3.connect(target) as target_connection:
            source_connection.backup(target_connection)
    _verify_sqlite(target)


def _backup_postgres(url: URL, target: Path, *, runner: Runner) -> None:
    args, env = _postgres_connection_args(url)
    _run_checked(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            f"--file={target}",
            *args,
        ],
        runner=runner,
        env=env,
    )
    if not target.is_file() or target.stat().st_size == 0:
        raise BackupError("pg_dump did not create a non-empty archive")
    _run_checked(
        ["pg_restore", "--list", str(target)],
        runner=runner,
        env=env,
    )


def _postgres_connection_args(url: URL) -> tuple[list[str], dict[str, str]]:
    if not url.database:
        raise BackupError("PostgreSQL URL must name a database")
    args = [f"--dbname={url.database}"]
    if url.host:
        args.append(f"--host={url.host}")
    if url.port:
        args.append(f"--port={url.port}")
    if url.username:
        args.append(f"--username={url.username}")
    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = url.password
    sslmode = url.query.get("sslmode")
    if isinstance(sslmode, str):
        env["PGSSLMODE"] = sslmode
    return args, env


def database_identity_hash(database_url: str) -> str:
    """Return a password-free identity used to bind downgrade backups."""

    normalized = resolve_database_url(database_url, production=False)
    assert normalized is not None
    url = make_url(normalized)
    if url.get_backend_name() == "sqlite":
        identity = f"sqlite:///{_sqlite_path(url)}"
    else:
        # Driver, host, port, user, and database are identity-bearing. Omit the
        # password and complete query mapping because credentials can also be
        # supplied through driver-specific query parameters.
        identity = "|".join(
            (
                url.drivername,
                url.host or "",
                str(url.port or ""),
                url.username or "",
                url.database or "",
            )
        )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _sqlite_path(url: URL) -> Path:
    if not url.database or url.database == ":memory:":
        raise BackupError("in-memory SQLite databases cannot be backed up or restored")
    return Path(url.database).expanduser().resolve()


def _verify_sqlite(path: Path) -> str | None:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise BackupError("SQLite integrity check failed")
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "alembic_version" not in tables:
                return None
            row = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
            return str(row[0]) if row else None
    except sqlite3.DatabaseError as exc:
        raise BackupError("SQLite backup cannot be opened") from exc


def _run_checked(
    args: Sequence[str],
    *,
    runner: Runner,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            list(args),
            check=True,
            text=True,
            capture_output=True,
            env=env,
        )
    except FileNotFoundError as exc:
        raise BackupError(f"required command {args[0]!r} is not installed") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        raise BackupError(
            f"{args[0]} failed" + (f": {detail}" if detail else "")
        ) from exc


def _digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _write_manifest_temporary(path: Path, manifest: BackupManifest) -> Path:
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists():
        raise BackupError("stale backup manifest temporary file exists")
    temporary.write_text(
        json.dumps(asdict(manifest), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return temporary


def _read_manifest(path: Path) -> BackupManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        result = BackupManifest(**raw)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BackupError("backup manifest is missing or invalid") from exc
    if result.format_version != MANIFEST_FORMAT_VERSION:
        raise BackupError("unsupported backup manifest version")
    return result


def _database_url(argument: str | None) -> str:
    value = argument or os.getenv("DATABASE_URL", "")
    if not value.strip():
        raise BackupError("DATABASE_URL is required")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create", help="create backup and manifest")
    create.add_argument("backup", type=Path)
    create.add_argument("--database-url")
    verify = subparsers.add_parser("verify", help="verify backup without restoring")
    verify.add_argument("backup", type=Path)
    verify.add_argument("--expect-current", action="store_true")
    restore = subparsers.add_parser("restore", help="restore into an empty target")
    restore.add_argument("backup", type=Path)
    restore.add_argument("--target-database-url")
    restore.add_argument("--confirm-empty-target", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "create":
            result = create_backup(
                _database_url(args.database_url),
                args.backup,
            )
        elif args.command == "verify":
            result = verify_backup(
                args.backup,
                expect_current=args.expect_current,
            )
        else:
            result = restore_backup(
                args.backup,
                _database_url(args.target_database_url),
                confirm_empty_target=args.confirm_empty_target,
            )
    except BackupError as exc:
        print(f"backup error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
