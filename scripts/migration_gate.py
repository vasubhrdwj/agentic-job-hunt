"""Verify migration head parity and guard every operator downgrade with a backup."""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from job_hunt_agent.database import (
    MIGRATION_HEAD,
    Database,
    DatabaseConfigError,
    resolve_database_url,
)
from scripts.database_backup import BackupError, database_identity_hash, verify_backup


class MigrationGateError(RuntimeError):
    pass


def migration_graph() -> tuple[str, str | None]:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = script.get_heads()
    if len(heads) != 1:
        raise MigrationGateError(f"expected one Alembic head, found {heads}")
    head = heads[0]
    if head != MIGRATION_HEAD:
        raise MigrationGateError(
            f"code expects {MIGRATION_HEAD!r}, but Alembic head is {head!r}"
        )
    revision = script.get_revision(head)
    down_revision = revision.down_revision
    if isinstance(down_revision, tuple):
        raise MigrationGateError("merge heads are not allowed in the linear product history")
    return head, str(down_revision) if down_revision is not None else None


def check_migrations(database_url: str) -> dict[str, object]:
    normalized = resolve_database_url(database_url, production=False)
    assert normalized is not None
    head, previous = migration_graph()
    database = Database(normalized)
    try:
        if not database.reachable():
            raise MigrationGateError("database is not reachable")
        revision = database.current_migration_revision()
    finally:
        database.dispose()
    if revision != head:
        raise MigrationGateError(
            f"database revision {revision!r} does not match current head {head!r}"
        )
    # migrations/env.py deliberately resolves DATABASE_URL through the same
    # production validation as the application. Make the requested target
    # authoritative for the duration of the Alembic command instead of
    # accidentally checking whichever database happens to be in the shell.
    with _database_url_environment(normalized):
        command.check(Config("alembic.ini"))
    return {
        "ok": True,
        "revision": revision,
        "head": head,
        "previous_revision": previous,
    }


def guarded_downgrade(
    database_url: str,
    backup_path: Path | None,
    *,
    apply: bool,
) -> dict[str, object]:
    head, previous = migration_graph()
    if previous is None:
        raise MigrationGateError("foundation migration has no downgrade target")
    plan = {
        "apply": apply,
        "from_revision": head,
        "to_revision": previous,
        "backup": str(backup_path) if backup_path else None,
    }
    if not apply:
        return plan
    if backup_path is None:
        raise MigrationGateError("downgrade --apply requires --verified-backup")
    try:
        manifest = verify_backup(backup_path, expect_current=True)
    except BackupError as exc:
        raise MigrationGateError(f"downgrade backup is not verified: {exc}") from exc
    if manifest.migration_revision != head:
        raise MigrationGateError("downgrade backup does not capture the current head")
    normalized = resolve_database_url(database_url, production=False)
    assert normalized is not None
    if manifest.source_identity_hash != database_identity_hash(normalized):
        raise MigrationGateError("downgrade backup belongs to a different database")
    database = Database(normalized)
    try:
        if database.current_migration_revision() != head:
            raise MigrationGateError("database moved after the downgrade plan was created")
    finally:
        database.dispose()
    with _database_url_environment(normalized):
        command.downgrade(Config("alembic.ini"), previous)
    return plan


@contextmanager
def _database_url_environment(database_url: str) -> Iterator[None]:
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def _database_url(argument: str | None) -> str:
    value = argument or os.getenv("DATABASE_URL", "")
    if not value.strip():
        raise MigrationGateError("DATABASE_URL is required")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check")
    check.add_argument("--database-url")
    downgrade = subparsers.add_parser("downgrade")
    downgrade.add_argument("--database-url")
    downgrade.add_argument("--verified-backup", type=Path)
    downgrade.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "check":
            result = check_migrations(_database_url(args.database_url))
        else:
            result = guarded_downgrade(
                _database_url(args.database_url),
                args.verified_backup,
                apply=args.apply,
            )
    except (MigrationGateError, BackupError, DatabaseConfigError) as exc:
        print(f"migration gate failed: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        if args.command != "downgrade" or not _is_data_loss_refusal(exc):
            raise
        print(f"migration gate failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


def _is_data_loss_refusal(exc: RuntimeError) -> bool:
    message = str(exc).strip().lower()
    return message.startswith("cannot downgrade ") or message.startswith(
        "refusing privacy-controls downgrade"
    )


if __name__ == "__main__":
    raise SystemExit(main())
