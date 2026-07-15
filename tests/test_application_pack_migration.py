"""Alembic parity and downgrade hygiene for application-pack grounding."""

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
    JobPosting,
    JobPostingVersion,
    Owner,
    OwnerMutationReceipt,
    OwnerOpportunity,
    ResumeVersion,
)
from job_hunt_agent.models.application_pack import (
    ApplicationPack,
    ApplicationPackEvent,
    ApplicationPackRevision,
)


APPLICATION_PACK_TABLES = {
    "application_packs",
    "application_pack_revisions",
    "application_pack_events",
}
NOW = datetime(2026, 7, 14, 8, 30, tzinfo=timezone.utc)


def test_application_pack_migration_round_trip_constraints_and_receipt_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'application-pack-migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.check(config)

    database = Database(url)
    try:
        inspector = inspect(database.engine)
        assert MIGRATION_HEAD == "20260715_0014"
        assert database.current_migration_revision() == MIGRATION_HEAD
        assert APPLICATION_PACK_TABLES.issubset(inspector.get_table_names())

        pack_columns = {
            column["name"]: column
            for column in inspector.get_columns("application_packs")
        }
        assert pack_columns["posting_version_id"]["nullable"] is False
        assert pack_columns["base_resume_version_id"]["nullable"] is False
        pack_uniques = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("application_packs")
        }
        assert pack_uniques["uq_application_packs_owner_application"] == (
            "owner_id",
            "application_id",
        )
        pack_foreign_keys = {
            foreign_key["name"]: tuple(foreign_key["constrained_columns"])
            for foreign_key in inspector.get_foreign_keys("application_packs")
        }
        assert pack_foreign_keys["fk_application_packs_owner_application"] == (
            "owner_id",
            "application_id",
        )
        assert pack_foreign_keys[
            "fk_application_packs_owner_posting_version"
        ] == ("owner_id", "job_posting_id", "posting_version_id")
        assert pack_foreign_keys["fk_application_packs_owner_base_resume"] == (
            "owner_id",
            "base_resume_version_id",
        )
        pack_indexes = {
            index["name"]: index
            for index in inspector.get_indexes("application_packs")
        }
        assert pack_indexes["ix_application_packs_owner_updated"][
            "column_names"
        ] == ["owner_id", "updated_at"]

        revision_uniques = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(
                "application_pack_revisions"
            )
        }
        assert revision_uniques[
            "uq_application_pack_revisions_owner_number"
        ] == ("owner_id", "application_pack_id", "revision_number")
        revision_foreign_keys = {
            foreign_key["name"]: tuple(foreign_key["constrained_columns"])
            for foreign_key in inspector.get_foreign_keys(
                "application_pack_revisions"
            )
        }
        assert revision_foreign_keys[
            "fk_application_pack_revisions_owner_pack"
        ] == ("owner_id", "application_id", "application_pack_id")
        assert revision_foreign_keys[
            "fk_application_pack_revisions_owner_parent"
        ] == (
            "owner_id",
            "application_id",
            "application_pack_id",
            "parent_revision_id",
        )
        revision_checks = {
            constraint["name"]: constraint["sqltext"]
            for constraint in inspector.get_check_constraints(
                "application_pack_revisions"
            )
        }
        assert "revision_number >= 1" in revision_checks[
            "ck_application_pack_revisions_revision_number_positive"
        ]
        assert "'extracted', 'edited'" in revision_checks[
            "ck_application_pack_revisions_source"
        ]
        revision_indexes = {
            index["name"]: index
            for index in inspector.get_indexes("application_pack_revisions")
        }
        assert revision_indexes[
            "ix_application_pack_revisions_pack_created"
        ]["column_names"] == [
            "owner_id",
            "application_pack_id",
            "revision_number",
        ]

        event_uniques = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(
                "application_pack_events"
            )
        }
        assert event_uniques["uq_application_pack_events_owner_sequence"] == (
            "owner_id",
            "application_pack_id",
            "sequence_number",
        )
        assert event_uniques["uq_application_pack_events_owner_reviewed"] == (
            "owner_id",
            "application_pack_id",
            "revision_id",
            "event_type",
        )
        event_foreign_keys = {
            foreign_key["name"]: tuple(foreign_key["constrained_columns"])
            for foreign_key in inspector.get_foreign_keys("application_pack_events")
        }
        assert event_foreign_keys["fk_application_pack_events_owner_pack"] == (
            "owner_id",
            "application_id",
            "application_pack_id",
        )
        assert event_foreign_keys[
            "fk_application_pack_events_owner_revision"
        ] == (
            "owner_id",
            "application_id",
            "application_pack_id",
            "revision_id",
        )
        event_checks = {
            constraint["name"]: constraint["sqltext"]
            for constraint in inspector.get_check_constraints(
                "application_pack_events"
            )
        }
        assert "sequence_number >= 1" in event_checks[
            "ck_application_pack_events_sequence_number_positive"
        ]
        assert "event_type = 'reviewed'" in event_checks[
            "ck_application_pack_events_event_type"
        ]
        event_indexes = {
            index["name"]: index
            for index in inspector.get_indexes("application_pack_events")
        }
        assert event_indexes["ix_application_pack_events_timeline"][
            "column_names"
        ] == [
            "owner_id",
            "application_pack_id",
            "occurred_at",
            "sequence_number",
        ]

        _seed_two_reviewed_revisions(database)
        with database.session() as session:
            assert session.scalar(select(func.count(ApplicationPack.id))) == 1
            assert (
                session.scalar(select(func.count(ApplicationPackRevision.id)))
                == 2
            )
            assert session.scalar(select(func.count(ApplicationPackEvent.id))) == 2
            session.add_all(
                [
                    OwnerMutationReceipt(
                        id="pack-receipt",
                        owner_id="owner",
                        namespace="application_pack.revision.create",
                        idempotency_key_hash="e" * 64,
                        request_hash="f" * 64,
                        status="pending",
                        deleted=False,
                        version=1,
                    ),
                    OwnerMutationReceipt(
                        id="unrelated-receipt",
                        owner_id="owner",
                        namespace="outreach.sequence.start",
                        idempotency_key_hash="1" * 64,
                        request_hash="2" * 64,
                        status="pending",
                        deleted=False,
                        version=1,
                    ),
                ]
            )
    finally:
        database.dispose()

    command.downgrade(config, "20260713_0008")
    downgraded = Database(url)
    try:
        assert downgraded.current_migration_revision() == "20260713_0008"
        assert APPLICATION_PACK_TABLES.isdisjoint(
            inspect(downgraded.engine).get_table_names()
        )
        with downgraded.session() as session:
            assert (
                session.scalar(
                    select(func.count(OwnerMutationReceipt.id)).where(
                        OwnerMutationReceipt.namespace.like("application_pack.%")
                    )
                )
                == 0
            )
            assert (
                session.scalar(
                    select(func.count(OwnerMutationReceipt.id)).where(
                        OwnerMutationReceipt.namespace == "outreach.sequence.start"
                    )
                )
                == 1
            )
    finally:
        downgraded.dispose()

    command.upgrade(config, "head")
    command.check(config)
    upgraded = Database(url)
    try:
        assert upgraded.current_migration_revision() == MIGRATION_HEAD
        assert APPLICATION_PACK_TABLES.issubset(
            inspect(upgraded.engine).get_table_names()
        )
    finally:
        upgraded.dispose()

    migration_source = Path(
        "migrations/versions/20260714_0009_application_pack.py"
    ).read_text(encoding="utf-8")
    cleanup_index = migration_source.index(
        "DELETE FROM owner_mutation_receipts "
    )
    assert cleanup_index < migration_source.index(
        'op.drop_table("application_pack_events")'
    )
    assert cleanup_index < migration_source.index(
        'op.drop_table("application_pack_revisions")'
    )
    assert cleanup_index < migration_source.index(
        'op.drop_table("application_packs")'
    )


def _seed_two_reviewed_revisions(database: Database) -> None:
    with database.session() as session:
        session.add(Owner(id="owner", display_name="Owner", timezone="UTC"))
        session.flush()
        session.add(
            JobPosting(
                id="posting",
                owner_id="owner",
                identity_kind="native",
                identity_key="source:greenhouse:acme:123",
                identity_key_hash="a" * 64,
                source="greenhouse",
                company_slug="acme",
                source_job_id="123",
                canonical_url="https://boards.greenhouse.io/acme/jobs/123",
                lifecycle_state="open",
                consecutive_complete_omissions=0,
                first_confirmed_at=NOW,
                last_confirmed_at=NOW,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            JobPostingVersion(
                id="posting-version",
                owner_id="owner",
                job_posting_id="posting",
                version_number=1,
                content_hash="b" * 64,
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
                observed_at=NOW,
                created_at=NOW,
            )
        )
        session.add(
            ResumeVersion(
                id="resume",
                owner_id="owner",
                parent_id=None,
                label="Base resume",
                encrypted_content="encrypted-resume",
                encryption_key_id="key",
                content_hash="c" * 64,
                source="pasted",
                is_base=True,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            OwnerOpportunity(
                id="opportunity",
                owner_id="owner",
                job_posting_id="posting",
                decision="pursued",
                reviewed_posting_version_id="posting-version",
                decision_updated_at=NOW,
                first_surfaced_at=NOW,
                last_surfaced_at=NOW,
                version=2,
                created_at=NOW,
                updated_at=NOW,
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
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            ApplicationPack(
                id="pack",
                owner_id="owner",
                application_id="application",
                job_posting_id="posting",
                posting_version_id="posting-version",
                base_resume_version_id="resume",
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            ApplicationPackRevision(
                id="revision-one",
                owner_id="owner",
                application_id="application",
                application_pack_id="pack",
                parent_revision_id=None,
                revision_number=1,
                source="extracted",
                encrypted_payload="encrypted-review-one",
                encryption_key_id="key",
                content_hash="d" * 64,
                created_at=NOW,
            )
        )
        session.flush()
        session.add(
            ApplicationPackRevision(
                id="revision-two",
                owner_id="owner",
                application_id="application",
                application_pack_id="pack",
                parent_revision_id="revision-one",
                revision_number=2,
                source="edited",
                encrypted_payload="encrypted-review-two",
                encryption_key_id="key",
                content_hash="e" * 64,
                created_at=NOW,
            )
        )
        session.flush()
        session.add_all(
            [
                ApplicationPackEvent(
                    id="event-one",
                    owner_id="owner",
                    application_id="application",
                    application_pack_id="pack",
                    revision_id="revision-one",
                    sequence_number=1,
                    event_type="reviewed",
                    occurred_at=NOW,
                    idempotency_key_hash="f" * 64,
                    created_at=NOW,
                ),
                ApplicationPackEvent(
                    id="event-two",
                    owner_id="owner",
                    application_id="application",
                    application_pack_id="pack",
                    revision_id="revision-two",
                    sequence_number=2,
                    event_type="reviewed",
                    occurred_at=NOW,
                    idempotency_key_hash="0" * 64,
                    created_at=NOW,
                ),
            ]
        )
