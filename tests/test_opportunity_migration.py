"""Alembic parity and round-trip coverage for opportunity-radar storage."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from job_hunt_agent.database import MIGRATION_HEAD, Database


RADAR_TABLES = {
    "opportunity_scans",
    "opportunity_scan_sources",
    "job_postings",
    "job_posting_aliases",
    "job_posting_versions",
    "job_observations",
    "saved_search_matches",
    "owner_opportunities",
    "opportunity_decision_events",
}

APPLICATION_TABLES = {
    "applications",
    "action_items",
    "application_activity_events",
}


def test_opportunity_radar_migration_round_trip_and_metadata_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'opportunity-migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.check(config)

    database = Database(url)
    try:
        inspector = inspect(database.engine)
        assert database.current_migration_revision() == MIGRATION_HEAD
        assert MIGRATION_HEAD == "20260804_0021"
        assert RADAR_TABLES.issubset(inspector.get_table_names())
        assert APPLICATION_TABLES.issubset(inspector.get_table_names())

        posting_columns = {
            column["name"]: column
            for column in inspector.get_columns("job_postings")
        }
        assert posting_columns["source_job_id"]["type"].length == 512
        assert "current_version_id" not in posting_columns

        scan_columns = {
            column["name"]: column
            for column in inspector.get_columns("opportunity_scans")
        }
        assert scan_columns["pack_snapshot"]["nullable"] is False

        version_columns = {
            column["name"]: column
            for column in inspector.get_columns("job_posting_versions")
        }
        assert version_columns["summary"]["nullable"] is False
        assert version_columns["description"]["nullable"] is True
        assert version_columns["source_facts"]["nullable"] is False
        version_uniques = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("job_posting_versions")
        }
        assert "uq_job_posting_versions_owner_content" not in version_uniques
        assert version_uniques["uq_job_posting_versions_owner_number"] == (
            "owner_id",
            "job_posting_id",
            "version_number",
        )

        opportunity_uniques = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("owner_opportunities")
        }
        assert opportunity_uniques["uq_owner_opportunities_owner_posting"] == (
            "owner_id",
            "job_posting_id",
        )
        match_uniques = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("saved_search_matches")
        }
        assert match_uniques["uq_saved_search_matches_owner_search_posting"] == (
            "owner_id",
            "saved_search_id",
            "job_posting_id",
        )

        observation_foreign_keys = {
            foreign_key["name"]: tuple(foreign_key["constrained_columns"])
            for foreign_key in inspector.get_foreign_keys("job_observations")
        }
        assert observation_foreign_keys[
            "fk_job_observations_owner_scan_source"
        ] == (
            "owner_id",
            "opportunity_scan_id",
            "opportunity_scan_source_id",
        )
        assert observation_foreign_keys[
            "fk_job_observations_owner_posting_version"
        ] == (
            "owner_id",
            "job_posting_id",
            "job_posting_version_id",
        )

        scan_checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("opportunity_scans")
        }
        assert "ck_opportunity_scans_stage_nonempty" in scan_checks
        assert "ck_opportunity_scans_source_counts_ordered" in scan_checks
        source_checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints(
                "opportunity_scan_sources"
            )
        }
        assert "ck_opportunity_scan_sources_complete_board_only" in source_checks
        decision_checks = {
            constraint["name"]: constraint["sqltext"]
            for constraint in inspector.get_check_constraints(
                "opportunity_decision_events"
            )
        }
        assert "ck_opportunity_decision_events_note_envelope_complete" in decision_checks
        assert "ck_opportunity_decision_events_decision_reason" in decision_checks
        assert "pursued" in decision_checks[
            "ck_opportunity_decision_events_decision_values"
        ]

        application_uniques = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("applications")
        }
        assert application_uniques["uq_applications_owner_opportunity"] == (
            "owner_id",
            "owner_opportunity_id",
        )
        action_columns = {
            column["name"]: column
            for column in inspector.get_columns("action_items")
        }
        assert action_columns["due_on"]["nullable"] is False
        assert action_columns["title"]["type"].length == 240
        action_indexes = {
            index["name"]: index for index in inspector.get_indexes("action_items")
        }
        assert action_indexes["uq_action_items_owner_application_open"]["unique"] == 1
        activity_columns = {
            column["name"] for column in inspector.get_columns(
                "application_activity_events"
            )
        }
        assert "version" not in activity_columns
        assert "updated_at" not in activity_columns
        activity_indexes = {
            index["name"]: index
            for index in inspector.get_indexes("application_activity_events")
        }
        assert activity_indexes[
            "uq_application_activity_events_owner_created"
        ]["unique"] == 1
        activity_foreign_keys = {
            foreign_key["name"]: tuple(foreign_key["constrained_columns"])
            for foreign_key in inspector.get_foreign_keys(
                "application_activity_events"
            )
        }
        assert activity_foreign_keys[
            "fk_application_activity_events_owner_action"
        ] == ("owner_id", "application_id", "action_item_id")
    finally:
        database.dispose()

    command.downgrade(config, "20260713_0005")
    application_downgraded = Database(url)
    try:
        tables = inspect(application_downgraded.engine).get_table_names()
        assert application_downgraded.current_migration_revision() == "20260713_0005"
        assert APPLICATION_TABLES.isdisjoint(tables)
        assert RADAR_TABLES.issubset(tables)
    finally:
        application_downgraded.dispose()

    command.downgrade(config, "20260713_0004")
    downgraded = Database(url)
    try:
        assert RADAR_TABLES.isdisjoint(inspect(downgraded.engine).get_table_names())
    finally:
        downgraded.dispose()

    command.upgrade(config, "head")
    command.check(config)
    upgraded = Database(url)
    try:
        assert upgraded.current_migration_revision() == MIGRATION_HEAD
        assert RADAR_TABLES.issubset(inspect(upgraded.engine).get_table_names())
    finally:
        upgraded.dispose()
