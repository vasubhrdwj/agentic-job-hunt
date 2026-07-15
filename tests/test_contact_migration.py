"""Alembic parity and round-trip coverage for contact-bench storage."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select

from job_hunt_agent.database import MIGRATION_HEAD, Database
from job_hunt_agent.models import (
    Application,
    BackgroundJob,
    ContactPlan,
    JobPosting,
    JobPostingVersion,
    Owner,
    OwnerMutationReceipt,
    OwnerOpportunity,
)


CONTACT_TABLES = {"contact_plans", "contacts", "application_contacts"}


def test_contact_bench_migration_round_trip_and_metadata_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'contact-migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.check(config)

    database = Database(url)
    try:
        inspector = inspect(database.engine)
        assert MIGRATION_HEAD == "20260715_0014"
        assert database.current_migration_revision() == MIGRATION_HEAD
        assert CONTACT_TABLES.issubset(inspector.get_table_names())

        plan_columns = {
            column["name"]: column
            for column in inspector.get_columns("contact_plans")
        }
        assert plan_columns["target_count"]["nullable"] is False
        assert plan_columns["shortfall_reasons"]["nullable"] is False
        plan_checks = {
            constraint["name"]: constraint["sqltext"]
            for constraint in inspector.get_check_constraints("contact_plans")
        }
        assert "target_count = 5" in plan_checks[
            "ck_contact_plans_target_count_five"
        ]
        assert "selected_count = target_count" in plan_checks[
            "ck_contact_plans_coverage_counts"
        ]
        plan_indexes = {
            index["name"]: index
            for index in inspector.get_indexes("contact_plans")
        }
        assert plan_indexes[
            "uq_contact_plans_owner_application_active"
        ]["unique"] == 1

        contact_uniques = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("contacts")
        }
        assert contact_uniques["uq_contacts_owner_identity_hash"] == (
            "owner_id",
            "identity_key_hash",
        )
        assert contact_uniques["uq_contacts_owner_normalized_profile_url"] == (
            "owner_id",
            "normalized_profile_url",
        )

        role_foreign_keys = {
            foreign_key["name"]: tuple(foreign_key["constrained_columns"])
            for foreign_key in inspector.get_foreign_keys("application_contacts")
        }
        assert role_foreign_keys[
            "fk_application_contacts_owner_application"
        ] == ("owner_id", "application_id")
        assert role_foreign_keys["fk_application_contacts_owner_plan"] == (
            "owner_id",
            "application_id",
            "contact_plan_id",
        )
        assert role_foreign_keys["fk_application_contacts_owner_contact"] == (
            "owner_id",
            "contact_id",
        )

        role_uniques = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("application_contacts")
        }
        assert role_uniques["uq_application_contacts_owner_plan_pool_rank"] == (
            "owner_id",
            "contact_plan_id",
            "pool_rank",
        )
        role_indexes = {
            index["name"]: index
            for index in inspector.get_indexes("application_contacts")
        }
        assert role_indexes[
            "uq_application_contacts_owner_plan_bench_rank"
        ]["unique"] == 1
        role_checks = {
            constraint["name"]: constraint["sqltext"]
            for constraint in inspector.get_check_constraints("application_contacts")
        }
        assert "confidence >= 0.75" in role_checks[
            "ck_application_contacts_verified_evidence"
        ]
        assert "bench_rank BETWEEN 1 AND 5" in role_checks[
            "ck_application_contacts_bench_rank"
        ]

        now = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
        with database.session() as session:
            session.add(Owner(id="owner", display_name="Owner", timezone="UTC"))
            session.flush()
            session.add(
                JobPosting(
                    id="posting",
                    owner_id="owner",
                    identity_kind="native",
                    identity_key="source:greenhouse:acme:123",
                    identity_key_hash="1" * 64,
                    source="greenhouse",
                    company_slug="acme",
                    source_job_id="123",
                    canonical_url="https://boards.greenhouse.io/acme/jobs/123",
                    lifecycle_state="open",
                    consecutive_complete_omissions=0,
                    first_confirmed_at=now,
                    last_confirmed_at=now,
                    version=1,
                    created_at=now,
                    updated_at=now,
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
                    source="greenhouse",
                    source_job_id="123",
                    company_name="Acme",
                    title="Staff Backend Engineer",
                    canonical_url="https://boards.greenhouse.io/acme/jobs/123",
                    apply_urls=["https://boards.greenhouse.io/acme/jobs/123"],
                    location="Remote India",
                    summary="Build reliable services.",
                    description="Build reliable services.",
                    employment_type="full_time",
                    source_facts={},
                    source_confidence=1.0,
                    observed_at=now,
                    created_at=now,
                )
            )
            session.add(
                OwnerOpportunity(
                    id="opportunity",
                    owner_id="owner",
                    job_posting_id="posting",
                    decision="pursued",
                    reviewed_posting_version_id="posting-version",
                    decision_updated_at=now,
                    first_surfaced_at=now,
                    last_surfaced_at=now,
                    version=2,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()
            session.add(
                Application(
                    id="application",
                    owner_id="owner",
                    owner_opportunity_id="opportunity",
                    job_posting_id="posting",
                    pursued_posting_version_id="posting-version",
                    stage="pursuing",
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()
            session.add(
                BackgroundJob(
                    id="contact-job",
                    kind="discover_contacts",
                    owner_id="owner",
                    dedupe_scope="owner:owner",
                    subject_type="contact_plan",
                    subject_id="contact-plan",
                    payload={"contact_plan_id": "contact-plan"},
                    dedupe_key="contacts:contact-plan",
                    status="queued",
                    stage="queued",
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()
            session.add(
                ContactPlan(
                    id="contact-plan",
                    owner_id="owner",
                    application_id="application",
                    plan_number=1,
                    status="queued",
                    target_count=5,
                    candidate_limit=12,
                    confidence_floor=0.75,
                    policy_version="contact-bench-v1",
                    scoring_version="contact-score-v1",
                    background_job_id="contact-job",
                    discovered_count=0,
                    verified_count=0,
                    selected_count=0,
                    coverage_status="pending",
                    exhausted=False,
                    retryable=False,
                    shortfall_reasons=[],
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                OwnerMutationReceipt(
                    id="contact-receipt",
                    owner_id="owner",
                    namespace="contact_search.create:application",
                    idempotency_key_hash="a" * 64,
                    request_hash="b" * 64,
                    status="completed",
                    resource_type="contact_plan",
                    resource_id="contact-plan",
                    result_version=1,
                    deleted=False,
                    version=2,
                    created_at=now,
                    updated_at=now,
                    completed_at=now,
                )
            )
    finally:
        database.dispose()

    command.downgrade(config, "20260713_0006")
    downgraded = Database(url)
    try:
        tables = inspect(downgraded.engine).get_table_names()
        assert downgraded.current_migration_revision() == "20260713_0006"
        assert CONTACT_TABLES.isdisjoint(tables)
        assert "applications" in tables
        with downgraded.session() as session:
            assert (
                session.scalar(
                    select(func.count(BackgroundJob.id)).where(
                        BackgroundJob.kind == "discover_contacts"
                    )
                )
                == 0
            )
            assert (
                session.scalar(
                    select(func.count(OwnerMutationReceipt.id)).where(
                        OwnerMutationReceipt.namespace.like(
                            "contact_search.create:%"
                        )
                    )
                )
                == 0
            )
    finally:
        downgraded.dispose()

    command.upgrade(config, "head")
    command.check(config)
    upgraded = Database(url)
    try:
        assert upgraded.current_migration_revision() == MIGRATION_HEAD
        assert CONTACT_TABLES.issubset(inspect(upgraded.engine).get_table_names())
    finally:
        upgraded.dispose()

    migration_source = Path(
        "migrations/versions/20260713_0007_contact_bench.py"
    ).read_text(encoding="utf-8")
    assert migration_source.index('op.drop_table("contact_plans")') < (
        migration_source.index(
            'DELETE FROM background_jobs WHERE kind = \'discover_contacts\''
        )
    )
