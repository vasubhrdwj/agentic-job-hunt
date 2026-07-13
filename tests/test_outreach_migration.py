"""Alembic parity and downgrade hygiene for manual outreach storage."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select

from job_hunt_agent.database import Database
from job_hunt_agent.models import Owner, OwnerMutationReceipt


OUTREACH_TABLES = {
    "outreach_sequences",
    "outreach_message_versions",
    "outreach_events",
}


def test_outreach_migration_round_trip_constraints_and_receipt_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'outreach-migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "20260713_0008")
    command.check(config)

    database = Database(url)
    try:
        inspector = inspect(database.engine)
        assert database.current_migration_revision() == "20260713_0008"
        assert OUTREACH_TABLES.issubset(inspector.get_table_names())

        sequence_uniques = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("outreach_sequences")
        }
        assert sequence_uniques["uq_outreach_sequences_owner_application"] == (
            "owner_id",
            "application_id",
        )

        message_foreign_keys = {
            foreign_key["name"]: tuple(foreign_key["constrained_columns"])
            for foreign_key in inspector.get_foreign_keys(
                "outreach_message_versions"
            )
        }
        assert message_foreign_keys[
            "fk_outreach_message_versions_owner_sequence"
        ] == ("owner_id", "application_id", "outreach_sequence_id")
        assert message_foreign_keys[
            "fk_outreach_message_versions_owner_contact"
        ] == ("owner_id", "application_id", "application_contact_id")

        event_indexes = {
            index["name"]: index
            for index in inspector.get_indexes("outreach_events")
        }
        assert event_indexes["uq_outreach_events_marked_sent"]["unique"] == 1
        event_checks = {
            constraint["name"]: constraint["sqltext"]
            for constraint in inspector.get_check_constraints("outreach_events")
        }
        assert "follow_up_due_at IS NOT NULL" in event_checks[
            "ck_outreach_events_follow_up_due_shape"
        ]
        assert "useful_reply" in event_checks["ck_outreach_events_outcome"]

        with database.session() as session:
            session.add(Owner(id="owner", display_name="Owner", timezone="UTC"))
            session.flush()
            session.add(
                OwnerMutationReceipt(
                    id="outreach-receipt",
                    owner_id="owner",
                    namespace="outreach.sequence.start",
                    idempotency_key_hash="a" * 64,
                    request_hash="b" * 64,
                    status="pending",
                    deleted=False,
                    version=1,
                )
            )
    finally:
        database.dispose()

    command.downgrade(config, "20260713_0007")
    downgraded = Database(url)
    try:
        assert downgraded.current_migration_revision() == "20260713_0007"
        assert OUTREACH_TABLES.isdisjoint(inspect(downgraded.engine).get_table_names())
        with downgraded.session() as session:
            assert (
                session.scalar(
                    select(func.count(OwnerMutationReceipt.id)).where(
                        OwnerMutationReceipt.namespace.like("outreach.%")
                    )
                )
                == 0
            )
    finally:
        downgraded.dispose()

    command.upgrade(config, "20260713_0008")
    command.check(config)

    migration_source = Path(
        "migrations/versions/20260713_0008_manual_outreach.py"
    ).read_text(encoding="utf-8")
    assert migration_source.index("DELETE FROM owner_mutation_receipts") < (
        migration_source.index('op.drop_table("outreach_events")')
    )
