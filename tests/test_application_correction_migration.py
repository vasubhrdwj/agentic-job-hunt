"""Alembic parity and downgrade safety for milestone-date corrections."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from job_hunt_agent.database import MIGRATION_HEAD, Database


REVISION = "20260715_0014"
CURRENT_REVISION = "20260721_0020"
PREVIOUS_REVISION = "20260715_0013"


def test_application_correction_migration_schema_and_metadata_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'application-correction-schema.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    command.check(config)

    database = Database(url)
    try:
        assert MIGRATION_HEAD == CURRENT_REVISION
        assert database.current_migration_revision() == CURRENT_REVISION
        inspector = inspect(database.engine)
        assert "application_milestone_corrections" in inspector.get_table_names()

        columns = {
            column["name"]: column
            for column in inspector.get_columns(
                "application_milestone_corrections"
            )
        }
        assert columns.keys() >= {
            "activity_event_id",
            "correction_number",
            "supersedes_correction_id",
            "previous_effective_on",
            "corrected_effective_on",
            "recording_method",
            "recorded_at",
        }
        assert columns["supersedes_correction_id"]["nullable"] is True
        assert columns["previous_effective_on"]["nullable"] is False
        assert columns["corrected_effective_on"]["nullable"] is False

        uniques = {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_unique_constraints(
                "application_milestone_corrections"
            )
        }
        assert uniques[
            "uq_application_milestone_corrections_owner_event_id"
        ] == ("owner_id", "application_id", "activity_event_id", "id")
        assert uniques[
            "uq_application_milestone_corrections_owner_number"
        ] == (
            "owner_id",
            "application_id",
            "activity_event_id",
            "correction_number",
        )

        foreign_keys = {
            item["name"]: item
            for item in inspector.get_foreign_keys(
                "application_milestone_corrections"
            )
        }
        activity_edge = foreign_keys[
            "fk_application_milestone_corrections_owner_activity"
        ]
        assert tuple(activity_edge["constrained_columns"]) == (
            "owner_id",
            "application_id",
            "activity_event_id",
        )
        assert tuple(activity_edge["referred_columns"]) == (
            "owner_id",
            "application_id",
            "id",
        )
        supersedes_edge = foreign_keys[
            "fk_application_milestone_corrections_owner_supersedes"
        ]
        assert tuple(supersedes_edge["constrained_columns"]) == (
            "owner_id",
            "application_id",
            "activity_event_id",
            "supersedes_correction_id",
        )
        assert tuple(supersedes_edge["referred_columns"]) == (
            "owner_id",
            "application_id",
            "activity_event_id",
            "id",
        )

        checks = {
            item["name"]: item["sqltext"]
            for item in inspector.get_check_constraints(
                "application_milestone_corrections"
            )
        }
        assert "BETWEEN 1 AND 50" in checks[
            "ck_application_milestone_corrections_number_range"
        ]
        assert "correction_number = 1" in checks[
            "ck_application_milestone_corrections_chain_shape"
        ]
        assert "previous_effective_on <> corrected_effective_on" in checks[
            "ck_application_milestone_corrections_date_changed"
        ]
        assert "recording_method = 'manual'" in checks[
            "ck_application_milestone_corrections_recording_method"
        ]

        indexes = {
            item["name"]: item
            for item in inspector.get_indexes(
                "application_milestone_corrections"
            )
        }
        assert indexes[
            "uq_application_milestone_corrections_owner_supersedes"
        ]["unique"] == 1
        assert tuple(
            indexes["ix_application_milestone_corrections_timeline"][
                "column_names"
            ]
        ) == (
            "owner_id",
            "application_id",
            "activity_event_id",
            "correction_number",
        )

        activity_uniques = {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_unique_constraints(
                "application_activity_events"
            )
        }
        assert activity_uniques[
            "uq_application_activity_events_owner_application_id"
        ] == ("owner_id", "application_id", "id")
    finally:
        database.dispose()


def test_application_correction_migration_empty_round_trip_preserves_activity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'application-correction-empty.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")

    # This engine intentionally leaves foreign-key enforcement disabled so the
    # migration can prove that ordinary Phase 6 activity survives independently
    # of the full application graph.
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO application_activity_events ("
                    "id, owner_id, application_id, sequence_number, event_type, "
                    "to_stage, action_item_id, occurred_at"
                    ") VALUES ("
                    "'activity', 'owner', 'application', 1, "
                    "'application_created', 'pursuing', 'action', "
                    "'2026-07-15 10:00:00'"
                    ")"
                )
            )
    finally:
        engine.dispose()

    command.downgrade(config, PREVIOUS_REVISION)

    downgraded = Database(url)
    try:
        assert downgraded.current_migration_revision() == PREVIOUS_REVISION
        inspector = inspect(downgraded.engine)
        assert "application_milestone_corrections" not in inspector.get_table_names()
        activity_uniques = {
            item["name"]
            for item in inspector.get_unique_constraints(
                "application_activity_events"
            )
        }
        assert (
            "uq_application_activity_events_owner_application_id"
            not in activity_uniques
        )
        with downgraded.engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT id FROM application_activity_events "
                    "WHERE id = 'activity'"
                )
            ).scalar_one() == "activity"
    finally:
        downgraded.dispose()

    command.upgrade(config, "head")
    command.check(config)


def test_application_correction_migration_refuses_lossy_downgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'application-correction-lossy.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")

    # Isolate the downgrade guard without constructing the complete application
    # graph; the inserted row still satisfies every correction-table check.
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO application_milestone_corrections ("
                    "id, owner_id, application_id, activity_event_id, "
                    "correction_number, previous_effective_on, "
                    "corrected_effective_on, recording_method, recorded_at"
                    ") VALUES ("
                    "'correction', 'owner', 'application', 'activity', 1, "
                    "'2026-07-14', '2026-07-15', 'manual', "
                    "'2026-07-15 10:00:00'"
                    ")"
                )
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="Cannot downgrade 20260715_0014"):
        command.downgrade(config, PREVIOUS_REVISION)

    database = Database(url)
    try:
        assert database.current_migration_revision() == REVISION
        with database.engine.connect() as connection:
            assert connection.execute(
                text("SELECT COUNT(*) FROM application_milestone_corrections")
            ).scalar_one() == 1
        activity_uniques = {
            item["name"]
            for item in inspect(database.engine).get_unique_constraints(
                "application_activity_events"
            )
        }
        assert "uq_application_activity_events_owner_application_id" in (
            activity_uniques
        )
    finally:
        database.dispose()
