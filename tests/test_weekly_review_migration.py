"""Alembic parity, backfill, and downgrade safety for Phase 6B."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select, text

from job_hunt_agent.database import MIGRATION_HEAD, Database
from job_hunt_agent.models import Owner, OwnerMutationReceipt


REVISION = "20260715_0016"
CURRENT_REVISION = "20260715_0018"
PREVIOUS_REVISION = "20260715_0015"


def _config(url: str, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", url)
    return Config("alembic.ini")


def _insert_legacy_application(url: str, *, suffix: str = "a") -> None:
    """Insert a pre-0016 application without inventing attribution history."""

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO applications ("
                    "id, owner_id, owner_opportunity_id, job_posting_id, "
                    "pursued_posting_version_id, stage, version, created_at, updated_at"
                    ") VALUES ("
                    ":application_id, :owner_id, :opportunity_id, :posting_id, "
                    ":posting_version_id, 'pursuing', 1, :created_at, :created_at"
                    ")"
                ),
                {
                    "application_id": f"application-{suffix}",
                    "owner_id": f"owner-{suffix}",
                    "opportunity_id": f"opportunity-{suffix}",
                    "posting_id": f"posting-{suffix}",
                    "posting_version_id": f"posting-version-{suffix}",
                    "created_at": "2026-07-10 09:30:00",
                },
            )
    finally:
        engine.dispose()


def test_weekly_review_migration_schema_and_metadata_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'weekly-review-schema.db'}"
    config = _config(url, monkeypatch)
    command.upgrade(config, "head")
    command.check(config)

    database = Database(url)
    try:
        assert MIGRATION_HEAD == CURRENT_REVISION
        assert database.current_migration_revision() == CURRENT_REVISION
        inspector = inspect(database.engine)
        assert {
            "application_metric_snapshots",
            "application_action_reviews",
        }.issubset(inspector.get_table_names())

        snapshot_columns = {
            column["name"]: column
            for column in inspector.get_columns("application_metric_snapshots")
        }
        assert snapshot_columns.keys() >= {
            "application_id",
            "job_posting_id",
            "pursued_posting_version_id",
            "acquisition_source",
            "attribution_status",
            "saved_search_id",
            "saved_search_version",
            "saved_search_name",
            "career_track_id",
            "career_track_version",
            "career_track_name",
            "assessment_state",
            "assessment_band",
            "assessment_algorithm_version",
            "assessment_reason",
            "recorded_at",
        }
        assert snapshot_columns["assessment_band"]["nullable"] is True
        assert snapshot_columns["assessment_reason"]["nullable"] is True

        snapshot_uniques = {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_unique_constraints(
                "application_metric_snapshots"
            )
        }
        assert snapshot_uniques[
            "uq_application_metric_snapshots_owner_application"
        ] == ("owner_id", "application_id")

        snapshot_foreign_keys = {
            item["name"]: item
            for item in inspector.get_foreign_keys("application_metric_snapshots")
        }
        application_edge = snapshot_foreign_keys[
            "fk_application_metric_snapshots_owner_application"
        ]
        assert tuple(application_edge["constrained_columns"]) == (
            "owner_id",
            "application_id",
            "job_posting_id",
            "pursued_posting_version_id",
        )
        assert tuple(application_edge["referred_columns"]) == (
            "owner_id",
            "id",
            "job_posting_id",
            "pursued_posting_version_id",
        )

        snapshot_checks = {
            item["name"]: item["sqltext"]
            for item in inspector.get_check_constraints(
                "application_metric_snapshots"
            )
        }
        assert "attribution_missing" in snapshot_checks[
            "ck_application_metric_snapshots_attribution_shape"
        ]
        assert "assessment_algorithm_version IS NOT NULL" in snapshot_checks[
            "ck_application_metric_snapshots_assessment_shape"
        ]

        review_columns = {
            column["name"]
            for column in inspector.get_columns("application_action_reviews")
        }
        assert review_columns >= {
            "action_item_id",
            "decision",
            "prior_due_on",
            "new_due_on",
            "prior_action_version",
            "new_action_version",
            "prior_application_version",
            "new_application_version",
            "recording_method",
            "recorded_at",
            "idempotency_key_hash",
        }
        review_foreign_keys = {
            item["name"]: item
            for item in inspector.get_foreign_keys("application_action_reviews")
        }
        action_edge = review_foreign_keys[
            "fk_application_action_reviews_owner_action"
        ]
        assert tuple(action_edge["constrained_columns"]) == (
            "owner_id",
            "application_id",
            "action_item_id",
        )
        assert tuple(action_edge["referred_columns"]) == (
            "owner_id",
            "application_id",
            "id",
        )

        application_indexes = {
            item["name"]: item
            for item in inspector.get_indexes("applications")
        }
        exact_target = application_indexes[
            "uq_applications_metric_snapshot_target"
        ]
        assert exact_target["unique"] == 1
        assert tuple(exact_target["column_names"]) == (
            "owner_id",
            "id",
            "job_posting_id",
            "pursued_posting_version_id",
        )
    finally:
        database.dispose()


def test_upgrade_preserves_applications_and_backfills_explicit_missing_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'weekly-review-backfill.db'}"
    config = _config(url, monkeypatch)
    command.upgrade(config, PREVIOUS_REVISION)
    _insert_legacy_application(url)

    command.upgrade(config, "head")
    command.check(config)

    database = Database(url)
    try:
        with database.engine.connect() as connection:
            snapshot = connection.execute(
                text(
                    "SELECT application_id, job_posting_id, "
                    "pursued_posting_version_id, acquisition_source, "
                    "attribution_status, saved_search_id, career_track_id, "
                    "assessment_state, assessment_band, assessment_reason, "
                    "recorded_at FROM application_metric_snapshots"
                )
            ).mappings().one()
            assert connection.execute(
                text("SELECT COUNT(*) FROM applications")
            ).scalar_one() == 1

        assert snapshot == {
            "application_id": "application-a",
            "job_posting_id": "posting-a",
            "pursued_posting_version_id": "posting-version-a",
            "acquisition_source": "job_hunt_search",
            "attribution_status": "attribution_missing",
            "saved_search_id": None,
            "career_track_id": None,
            "assessment_state": "not_assessed",
            "assessment_band": None,
            "assessment_reason": "not_requested",
            "recorded_at": "2026-07-10 09:30:00",
        }
    finally:
        database.dispose()


def test_missing_only_downgrade_is_lossless_and_reupgrade_backfills_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'weekly-review-roundtrip.db'}"
    config = _config(url, monkeypatch)
    command.upgrade(config, PREVIOUS_REVISION)
    _insert_legacy_application(url)
    command.upgrade(config, "head")

    command.downgrade(config, PREVIOUS_REVISION)
    downgraded = Database(url)
    try:
        assert downgraded.current_migration_revision() == PREVIOUS_REVISION
        inspector = inspect(downgraded.engine)
        assert "application_metric_snapshots" not in inspector.get_table_names()
        assert "application_action_reviews" not in inspector.get_table_names()
        assert "uq_applications_metric_snapshot_target" not in {
            item["name"] for item in inspector.get_indexes("applications")
        }
        with downgraded.engine.connect() as connection:
            assert connection.execute(
                text("SELECT id FROM applications")
            ).scalar_one() == "application-a"
    finally:
        downgraded.dispose()

    command.upgrade(config, "head")
    command.check(config)
    upgraded = Database(url)
    try:
        with upgraded.engine.connect() as connection:
            assert connection.execute(
                text("SELECT COUNT(*) FROM application_metric_snapshots")
            ).scalar_one() == 1
    finally:
        upgraded.dispose()


def test_downgrade_cleans_only_weekly_review_mutation_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'weekly-review-receipts.db'}"
    config = _config(url, monkeypatch)
    command.upgrade(config, "head")

    database = Database(url)
    try:
        with database.session() as session:
            session.add(Owner(id="owner", display_name="Owner", timezone="UTC"))
            session.flush()
            session.add_all(
                [
                    OwnerMutationReceipt(
                        id="review-receipt",
                        owner_id="owner",
                        namespace="application.action_review:application-a",
                        idempotency_key_hash="a" * 64,
                        request_hash="b" * 64,
                        status="pending",
                        deleted=False,
                        version=1,
                    ),
                    OwnerMutationReceipt(
                        id="other-receipt",
                        owner_id="owner",
                        namespace="application.transition:application-a",
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
        with downgraded.session() as session:
            assert session.scalar(
                select(func.count(OwnerMutationReceipt.id)).where(
                    OwnerMutationReceipt.namespace.like(
                        "application.action_review:%"
                    )
                )
            ) == 0
            assert session.scalar(
                select(func.count(OwnerMutationReceipt.id)).where(
                    OwnerMutationReceipt.namespace.like("application.transition:%")
                )
            ) == 1
    finally:
        downgraded.dispose()


def test_downgrade_refuses_to_drop_action_review_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'weekly-review-lossy-review.db'}"
    config = _config(url, monkeypatch)
    command.upgrade(config, "head")

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO application_action_reviews ("
                    "id, owner_id, application_id, action_item_id, decision, "
                    "prior_due_on, new_due_on, prior_action_version, "
                    "new_action_version, prior_application_version, "
                    "new_application_version, recording_method, recorded_at, "
                    "idempotency_key_hash"
                    ") VALUES ("
                    "'review', 'owner', 'application', 'action', 'waiting', "
                    "'2026-07-15', '2026-07-22', 1, 2, 1, 2, 'manual', "
                    "'2026-07-15 10:00:00', :hash"
                    ")"
                ),
                {"hash": "a" * 64},
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="action review history"):
        command.downgrade(config, PREVIOUS_REVISION)

    database = Database(url)
    try:
        assert database.current_migration_revision() == REVISION
    finally:
        database.dispose()


def test_downgrade_refuses_to_drop_captured_attribution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'weekly-review-lossy-snapshot.db'}"
    config = _config(url, monkeypatch)
    command.upgrade(config, PREVIOUS_REVISION)
    _insert_legacy_application(url)
    command.upgrade(config, "head")

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE application_metric_snapshots "
                    "SET acquisition_source = 'referral', "
                    "attribution_status = 'captured'"
                )
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="captured application attribution"):
        command.downgrade(config, PREVIOUS_REVISION)

    database = Database(url)
    try:
        assert database.current_migration_revision() == REVISION
    finally:
        database.dispose()
