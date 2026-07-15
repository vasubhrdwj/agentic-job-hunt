"""Alembic parity and downgrade safety for interview-round storage."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from job_hunt_agent.database import MIGRATION_HEAD, Database


REVISION = "20260715_0013"
CURRENT_REVISION = "20260715_0015"
PREVIOUS_REVISION = "20260715_0012"


def test_interview_round_migration_schema_and_metadata_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'interview-round-schema.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    command.check(config)

    database = Database(url)
    try:
        assert MIGRATION_HEAD == CURRENT_REVISION
        assert database.current_migration_revision() == CURRENT_REVISION
        inspector = inspect(database.engine)
        assert {
            "application_interview_rounds",
            "application_interview_round_events",
        }.issubset(inspector.get_table_names())

        round_columns = {
            column["name"]: column
            for column in inspector.get_columns("application_interview_rounds")
        }
        assert round_columns.keys() >= {
            "application_submission_id",
            "round_number",
            "kind",
            "title",
            "status",
            "scheduled_start_at",
            "scheduled_timezone",
            "duration_minutes",
            "meeting_format",
            "completed_on",
            "cancelled_on",
            "cancelled_by",
            "version",
        }
        round_uniques = {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_unique_constraints(
                "application_interview_rounds"
            )
        }
        assert round_uniques[
            "uq_application_interview_rounds_owner_number"
        ] == ("owner_id", "application_id", "round_number")
        round_indexes = {
            item["name"]: item
            for item in inspector.get_indexes("application_interview_rounds")
        }
        assert round_indexes[
            "uq_application_interview_rounds_owner_scheduled"
        ]["unique"] == 1
        round_checks = {
            item["name"]: item["sqltext"]
            for item in inspector.get_check_constraints(
                "application_interview_rounds"
            )
        }
        assert "status = 'scheduled'" in round_checks[
            "ck_application_interview_rounds_status_shape"
        ]
        assert "cancelled_by IS NOT NULL" in round_checks[
            "ck_application_interview_rounds_status_shape"
        ]

        event_columns = {
            column["name"]: column
            for column in inspector.get_columns(
                "application_interview_round_events"
            )
        }
        assert event_columns.keys() >= {
            "interview_round_id",
            "sequence_number",
            "event_type",
            "from_status",
            "to_status",
            "effective_on",
            "previous_action_item_id",
            "action_item_id",
            "idempotency_key_hash",
        }
        event_checks = {
            item["name"]: item["sqltext"]
            for item in inspector.get_check_constraints(
                "application_interview_round_events"
            )
        }
        assert "event_type = 'rescheduled'" in event_checks[
            "ck_application_interview_round_events_event_shape"
        ]
        assert "length(idempotency_key_hash) = 64" in event_checks[
            "ck_application_interview_round_events_mutation_hash"
        ]

        action_columns = {
            column["name"]: column
            for column in inspector.get_columns("action_items")
        }
        assert action_columns["interview_round_id"]["nullable"] is True
        action_foreign_keys = {
            item["name"]: item
            for item in inspector.get_foreign_keys("action_items")
        }
        assert tuple(
            action_foreign_keys[
                "fk_action_items_owner_interview_round"
            ]["constrained_columns"]
        ) == ("owner_id", "application_id", "interview_round_id")

        activity_columns = {
            column["name"]: column
            for column in inspector.get_columns("application_activity_events")
        }
        assert activity_columns["interview_round_id"]["nullable"] is True
        activity_checks = {
            item["name"]: item["sqltext"]
            for item in inspector.get_check_constraints(
                "application_activity_events"
            )
        }
        assert "interview_round_id IS NULL" in activity_checks[
            "ck_application_activity_events_event_shape"
        ]
    finally:
        database.dispose()


def test_interview_round_migration_empty_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'interview-round-empty.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.downgrade(config, PREVIOUS_REVISION)

    database = Database(url)
    try:
        assert database.current_migration_revision() == PREVIOUS_REVISION
        inspector = inspect(database.engine)
        assert "application_interview_rounds" not in inspector.get_table_names()
        assert "application_interview_round_events" not in inspector.get_table_names()
        assert "interview_round_id" not in {
            column["name"] for column in inspector.get_columns("action_items")
        }
        assert "interview_round_id" not in {
            column["name"]
            for column in inspector.get_columns("application_activity_events")
        }
    finally:
        database.dispose()

    command.upgrade(config, "head")
    command.check(config)


def test_interview_round_migration_refuses_lossy_downgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'interview-round-lossy.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO application_interview_rounds ("
                    "id, owner_id, application_id, application_submission_id, "
                    "round_number, kind, title, status, scheduled_start_at, "
                    "scheduled_timezone, duration_minutes, meeting_format, version"
                    ") VALUES ("
                    "'round1', 'owner1', 'application1', 'submission1', 1, "
                    "'technical', 'Technical interview', 'scheduled', "
                    "'2026-07-20 10:00:00', 'Asia/Kolkata', 60, 'video', 1"
                    ")"
                )
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="Cannot downgrade 20260715_0013"):
        command.downgrade(config, PREVIOUS_REVISION)

    database = Database(url)
    try:
        assert database.current_migration_revision() == REVISION
    finally:
        database.dispose()
