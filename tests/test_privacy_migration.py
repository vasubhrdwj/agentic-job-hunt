"""Migration parity and downgrade safety for Phase 6C privacy controls."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from job_hunt_agent.database import MIGRATION_HEAD, Database
from job_hunt_agent.models import Owner, OwnerPrivacySetting, PrivacyDeletionReceipt


REVISION = "20260715_0018"
PREVIOUS_REVISION = "20260715_0017"


def _config(url: str, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", url)
    return Config("alembic.ini")


def test_privacy_migration_matches_models_and_receipt_has_no_owner_fk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'privacy-schema.db'}"
    config = _config(url, monkeypatch)
    command.upgrade(config, "head")
    command.check(config)

    database = Database(url)
    try:
        assert database.current_migration_revision() == MIGRATION_HEAD
        inspector = inspect(database.engine)
        assert {
            "owner_privacy_settings",
            "privacy_deletion_receipts",
        }.issubset(inspector.get_table_names())
        assert inspector.get_foreign_keys("privacy_deletion_receipts") == []
        settings_fks = inspector.get_foreign_keys("owner_privacy_settings")
        assert len(settings_fks) == 1
        assert settings_fks[0]["referred_table"] == "owners"
        assert settings_fks[0]["options"]["ondelete"] == "CASCADE"
    finally:
        database.dispose()


@pytest.mark.parametrize("row_kind", ["setting", "receipt"])
def test_downgrade_refuses_to_discard_live_privacy_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    row_kind: str,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / f'privacy-{row_kind}.db'}"
    config = _config(url, monkeypatch)
    command.upgrade(config, "head")
    database = Database(url)
    try:
        with database.session() as session:
            if row_kind == "setting":
                session.add(Owner(id="owner", display_name="Owner", timezone="UTC"))
                session.flush()
                session.add(
                    OwnerPrivacySetting(
                        owner_id="owner", hunt_run_retention_days=7, version=1
                    )
                )
            else:
                session.add(
                    PrivacyDeletionReceipt(
                        id="receipt",
                        owner_id_hash="a" * 64,
                        idempotency_key_hash="b" * 64,
                        request_hash="c" * 64,
                    )
                )
    finally:
        database.dispose()

    with pytest.raises(RuntimeError, match="refusing privacy-controls downgrade"):
        command.downgrade(config, PREVIOUS_REVISION)
    checked = Database(url)
    try:
        assert checked.current_migration_revision() == REVISION
    finally:
        checked.dispose()


def test_empty_privacy_tables_can_downgrade_and_reupgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'privacy-roundtrip.db'}"
    config = _config(url, monkeypatch)
    command.upgrade(config, "head")
    command.downgrade(config, PREVIOUS_REVISION)

    downgraded = Database(url)
    try:
        assert downgraded.current_migration_revision() == PREVIOUS_REVISION
        assert "owner_privacy_settings" not in inspect(downgraded.engine).get_table_names()
        assert "privacy_deletion_receipts" not in inspect(
            downgraded.engine
        ).get_table_names()
    finally:
        downgraded.dispose()

    command.upgrade(config, "head")
    command.check(config)
