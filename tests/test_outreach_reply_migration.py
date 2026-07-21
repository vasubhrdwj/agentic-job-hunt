"""Alembic parity, compatibility, and downgrade safety for outreach replies."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select, text

from job_hunt_agent.database import MIGRATION_HEAD, Database
from job_hunt_agent.models import Owner, OwnerMutationReceipt


REVISION = "20260715_0015"
CURRENT_REVISION = "20260721_0020"
PREVIOUS_REVISION = "20260715_0014"


def test_outreach_reply_migration_schema_and_metadata_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'outreach-reply-schema.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.check(config)

    database = Database(url)
    try:
        assert MIGRATION_HEAD == CURRENT_REVISION
        assert database.current_migration_revision() == CURRENT_REVISION
        inspector = inspect(database.engine)
        assert "outreach_replies" in inspector.get_table_names()

        columns = {
            column["name"]: column
            for column in inspector.get_columns("outreach_replies")
        }
        assert columns.keys() >= {
            "marked_sent_event_id",
            "marked_sent_event_type",
            "message_version_id",
            "message_kind",
            "reply_kind",
            "received_on",
            "encrypted_note",
            "note_key_id",
            "recording_method",
            "recorded_at",
            "idempotency_key_hash",
        }
        assert columns["encrypted_note"]["nullable"] is True
        assert columns["note_key_id"]["nullable"] is True

        uniques = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("outreach_replies")
        }
        assert uniques["uq_outreach_replies_owner_id_id"] == ("owner_id", "id")
        assert uniques["uq_outreach_replies_owner_sequence_mutation"] == (
            "owner_id",
            "outreach_sequence_id",
            "idempotency_key_hash",
        )

        foreign_keys = {
            foreign_key["name"]: foreign_key
            for foreign_key in inspector.get_foreign_keys("outreach_replies")
        }
        sent_edge = foreign_keys["fk_outreach_replies_owner_sent_event"]
        assert tuple(sent_edge["constrained_columns"]) == (
            "owner_id",
            "application_id",
            "outreach_sequence_id",
            "application_contact_id",
            "marked_sent_event_id",
            "marked_sent_event_type",
            "message_version_id",
            "message_kind",
        )
        assert tuple(sent_edge["referred_columns"]) == (
            "owner_id",
            "application_id",
            "outreach_sequence_id",
            "application_contact_id",
            "id",
            "event_type",
            "message_version_id",
            "kind",
        )

        checks = {
            constraint["name"]: constraint["sqltext"]
            for constraint in inspector.get_check_constraints("outreach_replies")
        }
        assert "marked_sent" in checks[
            "ck_outreach_replies_marked_sent_event_type"
        ]
        assert "reply_received" in checks["ck_outreach_replies_reply_kind"]
        assert "referred" in checks["ck_outreach_replies_reply_kind"]
        assert "encrypted_note IS NULL" in checks[
            "ck_outreach_replies_note_envelope"
        ]
        assert "recording_method = 'manual'" in checks[
            "ck_outreach_replies_recording_method"
        ]
        assert "length(idempotency_key_hash) = 64" in checks[
            "ck_outreach_replies_mutation_hash"
        ]

        indexes = {
            index["name"]: index
            for index in inspector.get_indexes("outreach_replies")
        }
        assert tuple(indexes["ix_outreach_replies_sent_attempt"]["column_names"]) == (
            "owner_id",
            "outreach_sequence_id",
            "marked_sent_event_id",
            "recorded_at",
            "id",
        )
        assert tuple(indexes["ix_outreach_replies_timeline"]["column_names"]) == (
            "owner_id",
            "outreach_sequence_id",
            "recorded_at",
            "id",
        )
        event_indexes = {
            index["name"]: index
            for index in inspector.get_indexes("outreach_events")
        }
        assert event_indexes["uq_outreach_events_reply_target"]["unique"] == 1
    finally:
        database.dispose()


def test_upgrade_preserves_existing_outreach_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'outreach-reply-upgrade.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, PREVIOUS_REVISION)

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO outreach_events ("
                    "id, owner_id, application_id, outreach_sequence_id, "
                    "sequence_number, event_type, wave, occurred_at, "
                    "idempotency_key_hash"
                    ") VALUES ("
                    "'existing-event', 'owner', 'application', 'sequence', "
                    "1, 'sequence_started', 1, '2026-07-15 10:00:00', :hash"
                    ")"
                ),
                {"hash": "a" * 64},
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    command.check(config)

    database = Database(url)
    try:
        assert database.current_migration_revision() == CURRENT_REVISION
        with database.engine.connect() as connection:
            assert connection.execute(
                text("SELECT event_type FROM outreach_events WHERE id='existing-event'")
            ).scalar_one() == "sequence_started"
            assert connection.execute(
                text("SELECT COUNT(*) FROM outreach_replies")
            ).scalar_one() == 0
    finally:
        database.dispose()


def test_empty_downgrade_cleans_only_reply_mutation_receipts_and_reupgrades(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'outreach-reply-roundtrip.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")

    database = Database(url)
    try:
        with database.session() as session:
            session.add(Owner(id="owner", display_name="Owner", timezone="UTC"))
            session.flush()
            session.add_all(
                [
                    OwnerMutationReceipt(
                        id="reply-receipt",
                        owner_id="owner",
                        namespace="outreach.reply.record:sequence",
                        idempotency_key_hash="a" * 64,
                        request_hash="b" * 64,
                        status="pending",
                        deleted=False,
                        version=1,
                    ),
                    OwnerMutationReceipt(
                        id="sequence-receipt",
                        owner_id="owner",
                        namespace="outreach.sequence.start",
                        idempotency_key_hash="c" * 64,
                        request_hash="d" * 64,
                        status="pending",
                        deleted=False,
                        version=1,
                    ),
                ]
            )
    finally:
        database.dispose()

    command.downgrade(config, PREVIOUS_REVISION)
    downgraded = Database(url)
    try:
        assert downgraded.current_migration_revision() == PREVIOUS_REVISION
        assert "outreach_replies" not in inspect(downgraded.engine).get_table_names()
        with downgraded.session() as session:
            assert session.scalar(
                select(func.count(OwnerMutationReceipt.id)).where(
                    OwnerMutationReceipt.namespace.like("outreach.reply.record:%")
                )
            ) == 0
            assert session.scalar(
                select(func.count(OwnerMutationReceipt.id)).where(
                    OwnerMutationReceipt.namespace == "outreach.sequence.start"
                )
            ) == 1
    finally:
        downgraded.dispose()

    command.upgrade(config, "head")
    command.check(config)


def test_outreach_reply_migration_refuses_lossy_downgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'outreach-reply-lossy.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO outreach_replies ("
                    "id, owner_id, application_id, outreach_sequence_id, "
                    "application_contact_id, marked_sent_event_id, "
                    "marked_sent_event_type, message_version_id, message_kind, "
                    "reply_kind, received_on, recording_method, recorded_at, "
                    "idempotency_key_hash"
                    ") VALUES ("
                    "'reply', 'owner', 'application', 'sequence', 'contact', "
                    "'sent-event', 'marked_sent', 'message', 'initial', "
                    "'reply_received', '2026-07-15', 'manual', "
                    "'2026-07-15 10:00:00', :hash"
                    ")"
                ),
                {"hash": "a" * 64},
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="Cannot downgrade 20260715_0015"):
        command.downgrade(config, PREVIOUS_REVISION)

    database = Database(url)
    try:
        assert database.current_migration_revision() == REVISION
        with database.engine.connect() as connection:
            assert connection.execute(
                text("SELECT COUNT(*) FROM outreach_replies")
            ).scalar_one() == 1
    finally:
        database.dispose()
