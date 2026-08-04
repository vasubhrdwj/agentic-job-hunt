"""Alembic parity and downgrade safety for application progress storage."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from job_hunt_agent.database import MIGRATION_HEAD, Database


PROGRESS_REVISION = "20260715_0012"
CURRENT_REVISION = "20260804_0021"
PREVIOUS_REVISION = "20260714_0011"


def test_application_progress_migration_schema_and_metadata_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'application-progress-schema.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    command.check(config)

    database = Database(url)
    try:
        assert MIGRATION_HEAD == CURRENT_REVISION
        assert database.current_migration_revision() == CURRENT_REVISION

        inspector = inspect(database.engine)
        assert "application_outcomes" in inspector.get_table_names()

        outcome_columns = {
            column["name"]: column
            for column in inspector.get_columns("application_outcomes")
        }
        assert outcome_columns.keys() >= {
            "id",
            "owner_id",
            "application_id",
            "application_submission_id",
            "stage_at_outcome",
            "outcome",
            "outcome_on",
            "recording_method",
            "recorded_at",
            "created_at",
        }
        assert outcome_columns["application_submission_id"]["nullable"] is True
        assert outcome_columns["stage_at_outcome"]["nullable"] is False
        assert outcome_columns["outcome_on"]["nullable"] is False
        assert outcome_columns["recorded_at"]["nullable"] is False

        outcome_uniques = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(
                "application_outcomes"
            )
        }
        assert outcome_uniques["uq_application_outcomes_owner_application"] == (
            "owner_id",
            "application_id",
        )
        assert outcome_uniques[
            "uq_application_outcomes_owner_application_id"
        ] == ("owner_id", "application_id", "id")

        outcome_foreign_keys = {
            foreign_key["name"]: foreign_key
            for foreign_key in inspector.get_foreign_keys("application_outcomes")
        }
        assert tuple(
            outcome_foreign_keys[
                "fk_application_outcomes_owner_application"
            ]["constrained_columns"]
        ) == ("owner_id", "application_id")
        assert tuple(
            outcome_foreign_keys[
                "fk_application_outcomes_owner_submission"
            ]["constrained_columns"]
        ) == ("owner_id", "application_id", "application_submission_id")
        assert tuple(
            outcome_foreign_keys[
                "fk_application_outcomes_owner_submission"
            ]["referred_columns"]
        ) == ("owner_id", "application_id", "id")

        outcome_checks = {
            constraint["name"]: constraint["sqltext"]
            for constraint in inspector.get_check_constraints(
                "application_outcomes"
            )
        }
        assert "offer_accepted" in outcome_checks[
            "ck_application_outcomes_outcome"
        ]
        assert "recording_method = 'manual'" in outcome_checks[
            "ck_application_outcomes_recording_method"
        ]
        assert "application_submission_id IS NULL" in outcome_checks[
            "ck_application_outcomes_submission_shape"
        ]
        assert "application_submission_id IS NOT NULL" in outcome_checks[
            "ck_application_outcomes_submission_shape"
        ]
        assert "stage_at_outcome = 'offer'" in outcome_checks[
            "ck_application_outcomes_offer_outcome_stage"
        ]

        outcome_indexes = {
            index["name"]: index
            for index in inspector.get_indexes("application_outcomes")
        }
        assert outcome_indexes["ix_application_outcomes_owner_metrics"][
            "column_names"
        ] == ["owner_id", "outcome", "outcome_on"]

        application_columns = {
            column["name"]: column
            for column in inspector.get_columns("applications")
        }
        assert application_columns["outcome_id"]["nullable"] is True
        application_foreign_keys = {
            foreign_key["name"]: foreign_key
            for foreign_key in inspector.get_foreign_keys("applications")
        }
        outcome_edge = application_foreign_keys[
            "fk_applications_owner_outcome"
        ]
        assert tuple(outcome_edge["constrained_columns"]) == (
            "owner_id",
            "id",
            "outcome_id",
        )
        assert tuple(outcome_edge["referred_columns"]) == (
            "owner_id",
            "application_id",
            "id",
        )
        application_checks = {
            constraint["name"]: constraint["sqltext"]
            for constraint in inspector.get_check_constraints("applications")
        }
        assert "'screening', 'interviewing', 'offer', 'closed'" in (
            application_checks["ck_applications_stage"]
        )
        assert "stage = 'closed' AND outcome_id IS NOT NULL" in (
            application_checks["ck_applications_outcome_shape"]
        )

        action_checks = {
            constraint["name"]: constraint["sqltext"]
            for constraint in inspector.get_check_constraints("action_items")
        }
        assert "prepare_recruiter_screen" in action_checks[
            "ck_action_items_kind"
        ]
        assert "prepare_interview" in action_checks["ck_action_items_kind"]
        assert "review_offer" in action_checks["ck_action_items_kind"]

        activity_columns = {
            column["name"]: column
            for column in inspector.get_columns("application_activity_events")
        }
        assert activity_columns["action_item_id"]["nullable"] is True
        assert activity_columns["effective_on"]["nullable"] is True
        assert activity_columns["outcome_id"]["nullable"] is True
        activity_foreign_keys = {
            foreign_key["name"]: foreign_key
            for foreign_key in inspector.get_foreign_keys(
                "application_activity_events"
            )
        }
        assert tuple(
            activity_foreign_keys[
                "fk_application_activity_events_owner_outcome"
            ]["constrained_columns"]
        ) == ("owner_id", "application_id", "outcome_id")
        activity_checks = {
            constraint["name"]: constraint["sqltext"]
            for constraint in inspector.get_check_constraints(
                "application_activity_events"
            )
        }
        assert "application_screening" in activity_checks[
            "ck_application_activity_events_event_type"
        ]
        assert "sequence_number >= 4" in activity_checks[
            "ck_application_activity_events_event_shape"
        ]
        assert "action_item_id IS NULL" in activity_checks[
            "ck_application_activity_events_event_shape"
        ]
        assert "effective_on IS NOT NULL" in activity_checks[
            "ck_application_activity_events_event_shape"
        ]

        activity_indexes = {
            index["name"]: index
            for index in inspector.get_indexes("application_activity_events")
        }
        for index_name in (
            "uq_application_activity_events_owner_screening",
            "uq_application_activity_events_owner_interviewing",
            "uq_application_activity_events_owner_offer",
            "uq_application_activity_events_owner_closed",
            "uq_application_activity_events_owner_outcome",
        ):
            assert activity_indexes[index_name]["unique"] == 1
    finally:
        database.dispose()


def test_application_progress_migration_empty_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'application-progress-round-trip.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    command.downgrade(config, PREVIOUS_REVISION)

    downgraded = Database(url)
    try:
        assert downgraded.current_migration_revision() == PREVIOUS_REVISION
        inspector = inspect(downgraded.engine)
        assert "application_outcomes" not in inspector.get_table_names()
        assert "outcome_id" not in {
            column["name"] for column in inspector.get_columns("applications")
        }
        activity_columns = {
            column["name"]: column
            for column in inspector.get_columns("application_activity_events")
        }
        assert "effective_on" not in activity_columns
        assert "outcome_id" not in activity_columns
        assert activity_columns["action_item_id"]["nullable"] is False
    finally:
        downgraded.dispose()

    command.upgrade(config, "head")
    command.check(config)
    upgraded = Database(url)
    try:
        assert upgraded.current_migration_revision() == CURRENT_REVISION
        assert "application_outcomes" in inspect(
            upgraded.engine
        ).get_table_names()
    finally:
        upgraded.dispose()


@pytest.mark.parametrize(
    ("case", "statement"),
    (
        (
            "outcome",
            "INSERT INTO application_outcomes ("
            "id, owner_id, application_id, stage_at_outcome, outcome, "
            "outcome_on, recording_method, recorded_at"
            ") VALUES ("
            "'outcome', 'owner', 'application', 'pursuing', 'withdrawn', "
            "'2026-07-15', 'manual', '2026-07-15 10:00:00'"
            ")",
        ),
        (
            "application-stage",
            "INSERT INTO applications ("
            "id, owner_id, owner_opportunity_id, job_posting_id, "
            "pursued_posting_version_id, stage, version"
            ") VALUES ("
            "'application', 'owner', 'opportunity', 'posting', "
            "'posting-version', 'screening', 1"
            ")",
        ),
        (
            "progress-event",
            "INSERT INTO application_activity_events ("
            "id, owner_id, application_id, sequence_number, event_type, "
            "from_stage, to_stage, action_item_id, previous_action_item_id, "
            "effective_on, occurred_at"
            ") VALUES ("
            "'event', 'owner', 'application', 4, 'application_screening', "
            "'applied', 'screening', 'next-action', 'previous-action', "
            "'2026-07-15', '2026-07-15 10:00:00'"
            ")",
        ),
        (
            "progress-action",
            "INSERT INTO action_items ("
            "id, owner_id, application_id, kind, title, status, due_on, version"
            ") VALUES ("
            "'action', 'owner', 'application', 'prepare_interview', "
            "'Prepare for interview', 'open', '2026-07-16', 1"
            ")",
        ),
    ),
)
def test_application_progress_migration_refuses_lossy_downgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    statement: str,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / f'application-progress-{case}.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")

    # This engine intentionally leaves SQLite foreign-key enforcement disabled.
    # Each row still has to satisfy the Phase 6A table checks, while the test can
    # isolate the downgrade guard without building the complete application graph.
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text(statement))
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="Cannot downgrade 20260715_0012"):
        command.downgrade(config, PREVIOUS_REVISION)

    database = Database(url)
    try:
        assert database.current_migration_revision() == PROGRESS_REVISION
    finally:
        database.dispose()
