"""Persistence invariants for application-pack grounding history."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    Application,
    ApplicationPack,
    ApplicationPackEvent,
    ApplicationPackRevision,
    Base,
    JobPosting,
    JobPostingVersion,
    Owner,
    OwnerOpportunity,
    ResumeVersion,
)


NOW = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def pack_db(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'application-packs.db'}")
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        session.add_all(
            [
                Owner(id="owner-a", display_name="Owner A", timezone="Asia/Kolkata"),
                Owner(id="owner-b", display_name="Owner B", timezone="UTC"),
            ]
        )
        session.flush()
        session.add(
            JobPosting(
                id="posting1",
                owner_id="owner-a",
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
                id="postingversion1",
                owner_id="owner-a",
                job_posting_id="posting1",
                version_number=1,
                content_hash="2" * 64,
                source="example",
                source_job_id="1",
                company_name="Example",
                title="Backend Engineer",
                canonical_url="https://careers.example.com/jobs/1",
                apply_urls=["https://careers.example.com/jobs/1"],
                location="Remote",
                summary="Backend role",
                description="Requirements:\n- Python experience required.",
                employment_type="full_time",
                source_facts={},
                source_confidence=1.0,
                observed_at=NOW,
            )
        )
        session.flush()
        session.add(
            OwnerOpportunity(
                id="opportunity1",
                owner_id="owner-a",
                job_posting_id="posting1",
                decision="pursued",
                reviewed_posting_version_id="postingversion1",
                decision_updated_at=NOW,
                first_surfaced_at=NOW,
                last_surfaced_at=NOW,
                version=2,
            )
        )
        session.flush()
        session.add(
            Application(
                id="application1",
                owner_id="owner-a",
                owner_opportunity_id="opportunity1",
                job_posting_id="posting1",
                pursued_posting_version_id="postingversion1",
                stage="pursuing",
                version=1,
            )
        )
        session.add(
            ResumeVersion(
                id="resume1",
                owner_id="owner-a",
                label="Base",
                encrypted_content="ciphertext",
                encryption_key_id="v1",
                content_hash="3" * 64,
                source="pasted",
                is_base=True,
                version=1,
            )
        )
    try:
        yield database
    finally:
        database.dispose()


def _pack(**updates: object) -> ApplicationPack:
    values: dict[str, object] = {
        "id": "pack1",
        "owner_id": "owner-a",
        "application_id": "application1",
        "job_posting_id": "posting1",
        "posting_version_id": "postingversion1",
        "base_resume_version_id": "resume1",
        "version": 1,
    }
    values.update(updates)
    return ApplicationPack(**values)


def _revision(
    *,
    revision_id: str = "revision1",
    revision_number: int = 1,
    parent_revision_id: str | None = None,
    source: str = "extracted",
) -> ApplicationPackRevision:
    return ApplicationPackRevision(
        id=revision_id,
        owner_id="owner-a",
        application_id="application1",
        application_pack_id="pack1",
        parent_revision_id=parent_revision_id,
        revision_number=revision_number,
        source=source,
        encrypted_payload="ciphertext",
        encryption_key_id="v1",
        content_hash=("4" if revision_number == 1 else "5") * 64,
    )


def test_pack_graph_is_owner_scoped_versioned_and_revision_content_is_immutable(
    pack_db: Database,
) -> None:
    with pack_db.session() as session:
        session.add(_pack())
        session.flush()
        session.add(_revision())
        session.flush()
        session.add(
            ApplicationPackEvent(
                id="event1",
                owner_id="owner-a",
                application_id="application1",
                application_pack_id="pack1",
                revision_id="revision1",
                sequence_number=1,
                event_type="reviewed",
                occurred_at=NOW,
                idempotency_key_hash="6" * 64,
            )
        )

    with pack_db.session() as session:
        assert session.scalar(select(func.count(ApplicationPack.id))) == 1
        assert session.scalar(select(func.count(ApplicationPackRevision.id))) == 1
        assert session.scalar(select(func.count(ApplicationPackEvent.id))) == 1
        assert "updated_at" not in ApplicationPackRevision.__table__.columns
        assert "version" not in ApplicationPackRevision.__table__.columns
        assert "updated_at" not in ApplicationPackEvent.__table__.columns

    with pack_db.session() as session:
        application = session.get(Application, "application1")
        assert application is not None
        session.delete(application)

    with pack_db.session() as session:
        assert session.scalar(select(func.count(ApplicationPack.id))) == 0
        assert session.scalar(select(func.count(ApplicationPackRevision.id))) == 0
        assert session.scalar(select(func.count(ApplicationPackEvent.id))) == 0


def test_one_pack_per_application_and_owner_edges_are_enforced(pack_db: Database) -> None:
    with pack_db.session() as session:
        session.add(_pack())

    with pytest.raises(IntegrityError):
        with pack_db.session() as session:
            session.add(_pack(id="pack2"))

    with pytest.raises(IntegrityError):
        with pack_db.session() as session:
            session.add(_pack(id="foreignpack", owner_id="owner-b"))

    with pytest.raises(IntegrityError):
        with pack_db.session() as session:
            session.add(_revision(source="generated"))


def test_review_events_are_append_only_per_revision_and_sequence(pack_db: Database) -> None:
    with pack_db.session() as session:
        session.add(_pack())
        session.flush()
        session.add(_revision())
        session.flush()
        session.add(
            _revision(
                revision_id="revision2",
                revision_number=2,
                parent_revision_id="revision1",
                source="edited",
            )
        )
        session.flush()
        session.add_all(
            [
                ApplicationPackEvent(
                    id="event1",
                    owner_id="owner-a",
                    application_id="application1",
                    application_pack_id="pack1",
                    revision_id="revision1",
                    sequence_number=1,
                    event_type="reviewed",
                    occurred_at=NOW,
                    idempotency_key_hash="6" * 64,
                ),
                ApplicationPackEvent(
                    id="event2",
                    owner_id="owner-a",
                    application_id="application1",
                    application_pack_id="pack1",
                    revision_id="revision2",
                    sequence_number=2,
                    event_type="reviewed",
                    occurred_at=NOW,
                    idempotency_key_hash="7" * 64,
                ),
            ]
        )

    with pytest.raises(IntegrityError):
        with pack_db.session() as session:
            session.add(
                ApplicationPackEvent(
                    owner_id="owner-a",
                    application_id="application1",
                    application_pack_id="pack1",
                    revision_id="revision2",
                    sequence_number=3,
                    event_type="reviewed",
                    occurred_at=NOW,
                    idempotency_key_hash="8" * 64,
                )
            )
