"""Migration parity and rollback behavior for encrypted fit-evaluation cache."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from job_hunt_agent.database import MIGRATION_HEAD, Database
from job_hunt_agent.models import (
    JobPosting,
    JobPostingVersion,
    OpportunityFitEvaluation,
    Owner,
)


REVISION = "20260804_0021"
PREVIOUS_REVISION = "20260721_0020"
NOW = datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)


def test_fit_evaluation_migration_matches_models_and_owner_scoped_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'fit-evaluation.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.check(config)
    database = Database(url)
    try:
        assert MIGRATION_HEAD == REVISION
        assert database.current_migration_revision() == REVISION
        inspector = inspect(database.engine)
        assert "opportunity_fit_evaluations" in inspector.get_table_names()
        foreign_keys = inspector.get_foreign_keys("opportunity_fit_evaluations")
        assert any(
            item["referred_table"] == "owners"
            and item["constrained_columns"] == ["owner_id"]
            and item["options"]["ondelete"] == "CASCADE"
            for item in foreign_keys
        )
        assert any(
            item["referred_table"] == "job_posting_versions"
            and item["constrained_columns"]
            == ["owner_id", "job_posting_id", "posting_version_id"]
            and item["referred_columns"]
            == ["owner_id", "job_posting_id", "id"]
            and item["options"]["ondelete"] == "CASCADE"
            for item in foreign_keys
        )
        indexes = {
            tuple(item["column_names"])
            for item in inspector.get_indexes("opportunity_fit_evaluations")
        }
        assert ("owner_id", "job_posting_id", "created_at") in indexes
        assert ("owner_id", "created_at") in indexes
        unique_constraints = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints(
                "opportunity_fit_evaluations"
            )
        }
        assert ("owner_id", "input_fingerprint") in unique_constraints
    finally:
        database.dispose()


def test_fit_evaluation_migration_can_drop_derived_cache_and_recreate_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'fit-evaluation-round-trip.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    database = Database(url)
    try:
        _seed_cached_evaluation(database)
    finally:
        database.dispose()

    command.downgrade(config, PREVIOUS_REVISION)
    downgraded = Database(url)
    try:
        assert downgraded.current_migration_revision() == PREVIOUS_REVISION
        inspector = inspect(downgraded.engine)
        assert "opportunity_fit_evaluations" not in inspector.get_table_names()
        assert "job_postings" in inspector.get_table_names()
    finally:
        downgraded.dispose()

    command.upgrade(config, "head")
    upgraded = Database(url)
    try:
        assert upgraded.current_migration_revision() == REVISION
        assert (
            "opportunity_fit_evaluations"
            in inspect(upgraded.engine).get_table_names()
        )
    finally:
        upgraded.dispose()


def _seed_cached_evaluation(database: Database) -> None:
    with database.session() as session:
        session.add(Owner(id="owner", display_name="Owner", timezone="UTC"))
        session.flush()
        session.add(
            JobPosting(
                id="posting",
                owner_id="owner",
                identity_kind="native",
                identity_key="source:example:1",
                identity_key_hash="1" * 64,
                source="example",
                company_slug="example",
                source_job_id="1",
                canonical_url="https://careers.example.com/jobs/1",
                lifecycle_state="open",
                consecutive_complete_omissions=0,
                first_confirmed_at=NOW,
                last_confirmed_at=NOW,
                version=1,
            )
        )
        session.flush()
        session.add(
            JobPostingVersion(
                id="posting-version",
                owner_id="owner",
                job_posting_id="posting",
                version_number=1,
                content_hash="2" * 64,
                source="example",
                source_job_id="1",
                company_name="Example",
                title="Backend Engineer",
                canonical_url="https://careers.example.com/jobs/1",
                apply_urls=["https://careers.example.com/jobs/1"],
                location="Remote",
                summary="Build reliable backend systems.",
                description="Design APIs and event-driven services.",
                employment_type="full_time",
                source_facts={},
                source_confidence=1.0,
                observed_at=NOW,
            )
        )
        session.flush()
        session.add(
            OpportunityFitEvaluation(
                id="evaluation",
                owner_id="owner",
                job_posting_id="posting",
                posting_version_id="posting-version",
                posting_hash="3" * 64,
                profile_input_fingerprint="4" * 64,
                input_fingerprint="5" * 64,
                evaluator_version="fit-policy-v1",
                provider="google-gemini",
                model="gemini-2.5-flash",
                result_schema_version=1,
                encrypted_payload="ciphertext",
                encryption_key_id="local-dev",
                version=1,
                created_at=NOW,
            )
        )
