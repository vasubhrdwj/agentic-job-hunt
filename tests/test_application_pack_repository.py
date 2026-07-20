"""Repository tests for exact, encrypted application grounding reviews."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

import job_hunt_agent.application_pack_repository as pack_repository
from job_hunt_agent.application_pack_repository import (
    create_application_pack,
    create_application_pack_revision,
    load_application_pack,
    record_application_pack_event,
)
from job_hunt_agent.application_pack_schemas import (
    ApplicationPackCreate,
    ApplicationPackEventCreate,
    ApplicationPackRevisionCreate,
)
from job_hunt_agent.database import Database
from job_hunt_agent.evidence_repository import (
    create_achievement_evidence,
    update_achievement_evidence,
)
from job_hunt_agent.models import (
    AchievementEvidence,
    Application,
    ApplicationMetricSnapshot,
    ApplicationPack,
    ApplicationPackEvent,
    ApplicationPackRevision,
    Base,
    CareerTrack,
    JobPosting,
    JobPostingVersion,
    Owner,
    OwnerMutationReceipt,
    OwnerOpportunity,
    SavedSearch,
)
from job_hunt_agent.mutation_receipts import MutationIdempotencyConflict
from job_hunt_agent.profile_repository import (
    create_or_reuse_resume_version,
    delete_resume_version,
)
from job_hunt_agent.profile_schemas import (
    AchievementEvidenceCreate,
    AchievementEvidencePatch,
)
from job_hunt_agent.repository_errors import ResourceConflict, ResourceInUse, VersionConflict
from job_hunt_agent.security import DataKeyring, load_data_keyring


NOW = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)
DESCRIPTION = (
    "About the role\n"
    "Requirements:\n"
    "- Five years of experience with Python and distributed systems.\n"
    "- Kubernetes experience preferred.\n"
    "Benefits include flexible hours."
)


@pytest.fixture
def pack_workspace(tmp_path: Path) -> tuple[Database, DataKeyring, str]:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'pack-repository.db'}")
    Base.metadata.create_all(database.engine)
    keyring = load_data_keyring(production=False)
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
                created_at=NOW,
                updated_at=NOW,
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
                title="Senior Backend Engineer",
                canonical_url="https://careers.example.com/jobs/1",
                apply_urls=["https://careers.example.com/jobs/1"],
                location="Remote India",
                summary="Build useful products.",
                description=DESCRIPTION,
                employment_type="full_time",
                source_facts={},
                source_confidence=1.0,
                observed_at=NOW,
                created_at=NOW,
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
                created_at=NOW,
                updated_at=NOW,
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
                created_at=NOW,
                updated_at=NOW,
            )
        )
        resume = create_or_reuse_resume_version(
            session,
            owner_id="owner-a",
            label="Base resume",
            content=(
                "PRIVATE CANDIDATE\n"
                "PRIVATE CONTACT\n\n"
                "PRIVATE RESUME: Built reliable Python distributed systems."
            ),
            source="pasted",
            keyring=keyring,
            make_base=True,
            now=NOW,
        )
        evidence = create_achievement_evidence(
            session,
            owner_id="owner-a",
            payload=AchievementEvidenceCreate(
                statement="PRIVATE EVIDENCE: Reduced Python service failures by 40%.",
                skills=["Python", "Distributed systems"],
            ),
            keyring=keyring,
            now=NOW,
        )
        approved = update_achievement_evidence(
            session,
            owner_id="owner-a",
            evidence_id=evidence.id,
            patch=AchievementEvidencePatch(approval_state="approved"),
            expected_version=evidence.version,
            keyring=keyring,
            now=NOW + timedelta(minutes=1),
        )
        assert approved is not None
        resume_id = resume.resume.id
    try:
        yield database, keyring, resume_id
    finally:
        database.dispose()


def _create(
    database: Database,
    keyring: DataKeyring,
    resume_id: str,
    *,
    key: str = "pack-create-1",
):
    with database.session() as session:
        return create_application_pack(
            session,
            owner_id="owner-a",
            application_id="application1",
            payload=ApplicationPackCreate(base_resume_version_id=resume_id),
            expected_application_version=1,
            idempotency_key=key,
            keyring=keyring,
            now=NOW + timedelta(minutes=2),
        )


def _review_payload(
    created,
    *,
    needs_review: bool = False,
    confirm: bool = False,
) -> ApplicationPackRevisionCreate:
    assert created.current_revision is not None
    evidence = created.current_approved_evidence[0]
    requirements = []
    for index, item in enumerate(created.current_revision.requirements):
        if index == 0:
            requirements.append(
                {
                    "id": item.id,
                    "ordinal": item.ordinal,
                    "importance": item.importance,
                    "text": item.text,
                    "source_start": item.source_start,
                    "source_end": item.source_end,
                    "coverage": "needs_review" if needs_review else "supported",
                    "evidence_refs": [
                        {"id": evidence.id, "version": evidence.version}
                    ],
                }
            )
        else:
            requirements.append(
                {
                    "id": item.id,
                    "ordinal": item.ordinal,
                    "importance": item.importance,
                    "text": item.text,
                    "source_start": item.source_start,
                    "source_end": item.source_end,
                    "coverage": "unsupported",
                    "evidence_refs": [],
                }
            )
    return ApplicationPackRevisionCreate(
        parent_revision_id=created.current_revision.id,
        requirements=requirements,
        confirm_requirements_reviewed=True if confirm else None,
    )


def test_not_started_pack_suggests_the_unchanged_attributed_search_resume(
    pack_workspace: tuple[Database, DataKeyring, str],
) -> None:
    database, keyring, base_resume_id = pack_workspace
    with database.session() as session:
        alternate = create_or_reuse_resume_version(
            session,
            owner_id="owner-a",
            label="Backend search resume",
            content="PRIVATE BACKEND RESUME: Python, Kafka, and distributed systems.",
            source="pasted",
            keyring=keyring,
            make_base=False,
            now=NOW + timedelta(minutes=1),
        )
        assert alternate.resume.id != base_resume_id
        session.add(
            CareerTrack(
                id="track-attributed",
                owner_id="owner-a",
                name="Backend track",
                role_families=["Backend Engineer"],
                seniority_levels=["mid"],
                target_locations=["India"],
                priorities={},
                active=True,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            SavedSearch(
                id="search-attributed",
                owner_id="owner-a",
                career_track_id="track-attributed",
                resume_version_id=alternate.resume.id,
                name="Backend search",
                criteria_schema_version=1,
                criteria={"role_keywords": ["backend"]},
                pack="backend_india",
                use_self_rag=False,
                cadence="manual",
                schedule={"local_time": None, "days_of_week": []},
                timezone="Asia/Kolkata",
                active=True,
                version=3,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            ApplicationMetricSnapshot(
                id="metric-attributed",
                owner_id="owner-a",
                application_id="application1",
                job_posting_id="posting1",
                pursued_posting_version_id="postingversion1",
                acquisition_source="job_hunt_search",
                attribution_status="captured",
                saved_search_id="search-attributed",
                saved_search_version=3,
                saved_search_name="Backend search",
                career_track_id="track-attributed",
                career_track_version=1,
                career_track_name="Backend track",
                assessment_state="not_assessed",
                assessment_band=None,
                assessment_algorithm_version=None,
                assessment_reason="not_requested",
                recorded_at=NOW,
                created_at=NOW,
            )
        )
        attributed_resume_id = alternate.resume.id

    with database.session() as session:
        projection = load_application_pack(
            session,
            owner_id="owner-a",
            application_id="application1",
            keyring=keyring,
        )
        assert projection is not None
        assert projection.attributed_resume_version_id == attributed_resume_id

    with database.session() as session:
        search = session.get(SavedSearch, "search-attributed")
        assert search is not None
        search.version += 1

    with database.session() as session:
        changed = load_application_pack(
            session,
            owner_id="owner-a",
            application_id="application1",
            keyring=keyring,
        )
        assert changed is not None
        assert changed.attributed_resume_version_id is None


def test_create_pins_inputs_extracts_exact_spans_and_encrypts_private_snapshots(
    pack_workspace: tuple[Database, DataKeyring, str],
) -> None:
    database, keyring, resume_id = pack_workspace
    with database.session() as session:
        before = load_application_pack(
            session,
            owner_id="owner-a",
            application_id="application1",
            keyring=keyring,
        )
    assert before is not None and before.status.value == "not_started"
    assert before.current_approved_evidence[0].statement.startswith("PRIVATE EVIDENCE")

    created = _create(database, keyring, resume_id)
    assert created is not None and created.status.value == "draft"
    assert created.pack is not None and created.pack.posting_version_id == "postingversion1"
    assert created.pack.base_resume_version_id == resume_id
    assert created.current_revision is not None
    assert created.current_revision.extraction_version == "requirements-v1"
    assert created.current_revision.job_description == DESCRIPTION
    assert len(created.current_revision.requirements) == 2
    assert all(
        item.coverage.value == "needs_review"
        for item in created.current_revision.requirements
    )
    for item in created.current_revision.requirements:
        assert DESCRIPTION[item.source_start : item.source_end] == item.text
    assert created.current_revision.requirements[0].evidence[0].statement.startswith(
        "PRIVATE EVIDENCE"
    )

    with database.session() as session:
        row = session.scalar(select(ApplicationPackRevision))
        assert row is not None
        assert "PRIVATE EVIDENCE" not in row.encrypted_payload
        assert DESCRIPTION not in row.encrypted_payload
        receipts = list(session.scalars(select(OwnerMutationReceipt)))
        assert receipts and all("PRIVATE" not in item.request_hash for item in receipts)

    replayed = _create(database, keyring, resume_id)
    assert replayed is not None and replayed.pack is not None
    assert replayed.pack.id == created.pack.id
    with pytest.raises(MutationIdempotencyConflict):
        with database.session() as session:
            create_application_pack(
                session,
                owner_id="owner-a",
                application_id="application1",
                payload=ApplicationPackCreate(
                    base_resume_version_id=resume_id,
                    owner_job_description="changed private request",
                ),
                expected_application_version=1,
                idempotency_key="pack-create-1",
                keyring=keyring,
            )


def test_automatic_create_requires_the_resume_to_still_be_current_base(
    pack_workspace: tuple[Database, DataKeyring, str],
) -> None:
    database, keyring, stale_resume_id = pack_workspace
    with database.session() as session:
        promoted = create_or_reuse_resume_version(
            session,
            owner_id="owner-a",
            label="New current base",
            content="A newer base resume with Python and distributed systems.",
            source="pasted",
            keyring=keyring,
            make_base=True,
            now=NOW + timedelta(minutes=2),
        )
        assert promoted.resume.is_base is True

    with pytest.raises(ResourceConflict, match="saved resume choices changed"):
        with database.session() as session:
            create_application_pack(
                session,
                owner_id="owner-a",
                application_id="application1",
                payload=ApplicationPackCreate(
                    base_resume_version_id=stale_resume_id,
                    require_sole_current_base_resume=True,
                ),
                expected_application_version=1,
                idempotency_key="automatic-stale-base",
                keyring=keyring,
            )

    with database.session() as session:
        assert session.scalar(select(func.count(ApplicationPack.id))) == 0


def test_automatic_create_accepts_the_locked_current_base(
    pack_workspace: tuple[Database, DataKeyring, str],
) -> None:
    database, keyring, resume_id = pack_workspace
    with database.session() as session:
        created = create_application_pack(
            session,
            owner_id="owner-a",
            application_id="application1",
            payload=ApplicationPackCreate(
                base_resume_version_id=resume_id,
                require_sole_current_base_resume=True,
            ),
            expected_application_version=1,
            idempotency_key="automatic-current-base",
            keyring=keyring,
        )

    assert created is not None and created.pack is not None
    assert created.pack.base_resume_version_id == resume_id


def test_automatic_create_rejects_a_new_non_base_alternate(
    pack_workspace: tuple[Database, DataKeyring, str],
) -> None:
    database, keyring, resume_id = pack_workspace
    with database.session() as session:
        alternate = create_or_reuse_resume_version(
            session,
            owner_id="owner-a",
            label="Explicit alternate",
            content="A different owner-created resume for another backend track.",
            source="pasted",
            keyring=keyring,
            make_base=False,
            now=NOW + timedelta(minutes=2),
        )
        assert alternate.resume.is_base is False

    with pytest.raises(ResourceConflict, match="saved resume choices changed"):
        with database.session() as session:
            create_application_pack(
                session,
                owner_id="owner-a",
                application_id="application1",
                payload=ApplicationPackCreate(
                    base_resume_version_id=resume_id,
                    require_sole_current_base_resume=True,
                ),
                expected_application_version=1,
                idempotency_key="automatic-new-alternate",
                keyring=keyring,
            )


def test_automatic_base_precondition_locks_owner_and_resume_inventory() -> None:
    statements = []

    class CapturingSession:
        def scalar(self, statement):
            statements.append(statement)
            return object()

        def scalars(self, statement):
            statements.append(statement)
            return []

    pack_repository._resume_for_pack_creation(  # noqa: SLF001 - lock contract.
        CapturingSession(),  # type: ignore[arg-type]
        owner_id="owner-a",
        resume_version_id="resume-a",
        require_sole_current_base=True,
    )

    assert len(statements) == 2
    sql = [str(item.compile(dialect=postgresql.dialect())) for item in statements]
    assert all(item.rstrip().endswith("FOR UPDATE") for item in sql)


def test_review_revisions_are_immutable_and_latest_review_event_wins(
    pack_workspace: tuple[Database, DataKeyring, str],
) -> None:
    database, keyring, resume_id = pack_workspace
    created = _create(database, keyring, resume_id)
    assert created is not None and created.pack is not None
    reviewed_payload = _review_payload(created)
    with database.session() as session:
        revision = create_application_pack_revision(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=created.pack.id,
            payload=reviewed_payload,
            expected_pack_version=1,
            idempotency_key="revision-2",
            keyring=keyring,
            now=NOW + timedelta(minutes=3),
        )
    assert revision is not None and revision.pack is not None
    assert revision.pack.version == 2
    assert revision.current_revision is not None
    assert revision.current_revision.revision_number == 2
    assert revision.current_revision.source.value == "edited"

    with database.session() as session:
        reviewed = record_application_pack_event(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=revision.pack.id,
            payload=ApplicationPackEventCreate.model_validate(
                {
                    "event_type": "reviewed",
                    "revision_id": revision.current_revision.id,
                    "confirm_requirements_reviewed": True,
                }
            ),
            expected_pack_version=2,
            idempotency_key="review-2",
            keyring=keyring,
            now=NOW + timedelta(minutes=4),
        )
    assert reviewed is not None and reviewed.status.value == "reviewed"
    assert reviewed.review_event is not None and reviewed.review_event.sequence_number == 1

    assert reviewed.pack is not None and reviewed.current_revision is not None
    next_payload = ApplicationPackRevisionCreate(
        parent_revision_id=reviewed.current_revision.id,
        requirements=[
            {
                "id": item.id,
                "ordinal": item.ordinal,
                "importance": item.importance,
                "text": item.text,
                "source_start": item.source_start,
                "source_end": item.source_end,
                "coverage": "unsupported",
                "evidence_refs": [],
            }
            for item in reviewed.current_revision.requirements
        ],
    )
    with database.session() as session:
        later = create_application_pack_revision(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=reviewed.pack.id,
            payload=next_payload,
            expected_pack_version=3,
            idempotency_key="revision-3",
            keyring=keyring,
            now=NOW + timedelta(minutes=5),
        )
    assert later is not None and later.status.value == "draft"
    assert later.reviewed_revision is not None
    assert later.reviewed_revision.revision_number == 2
    assert later.current_revision is not None and later.current_revision.revision_number == 3

    with database.session() as session:
        latest = record_application_pack_event(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=reviewed.pack.id,
            payload=ApplicationPackEventCreate.model_validate(
                {
                    "event_type": "reviewed",
                    "revision_id": later.current_revision.id,
                    "confirm_requirements_reviewed": True,
                }
            ),
            expected_pack_version=4,
            idempotency_key="review-3",
            keyring=keyring,
            now=NOW + timedelta(minutes=6),
        )
    assert latest is not None and latest.status.value == "reviewed"
    assert latest.review_event is not None and latest.review_event.sequence_number == 2
    with database.session() as session:
        assert session.scalar(select(func.count(ApplicationPackRevision.id))) == 3
        assert session.scalar(select(func.count(ApplicationPackEvent.id))) == 2


def test_prepared_review_is_saved_and_confirmed_atomically_and_idempotently(
    pack_workspace: tuple[Database, DataKeyring, str],
) -> None:
    database, keyring, resume_id = pack_workspace
    created = _create(database, keyring, resume_id)
    assert created is not None and created.pack is not None
    payload = _review_payload(created, confirm=True)

    with database.session() as session:
        reviewed = create_application_pack_revision(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=created.pack.id,
            payload=payload,
            expected_pack_version=1,
            idempotency_key="prepared-review",
            keyring=keyring,
            now=NOW + timedelta(minutes=3),
        )

    assert reviewed is not None and reviewed.pack is not None
    assert reviewed.pack.version == 2
    assert reviewed.status.value == "reviewed"
    assert reviewed.current_revision is not None
    assert reviewed.current_revision.revision_number == 2
    assert reviewed.reviewed_revision is not None
    assert reviewed.reviewed_revision.id == reviewed.current_revision.id
    assert reviewed.review_event is not None
    assert reviewed.review_event.revision_id == reviewed.current_revision.id

    with database.session() as session:
        replayed = create_application_pack_revision(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=created.pack.id,
            payload=payload,
            expected_pack_version=1,
            idempotency_key="prepared-review",
            keyring=keyring,
            now=NOW + timedelta(minutes=4),
        )
    assert replayed is not None and replayed.review_event is not None
    assert replayed.review_event.id == reviewed.review_event.id

    with pytest.raises(MutationIdempotencyConflict):
        with database.session() as session:
            create_application_pack_revision(
                session,
                owner_id="owner-a",
                application_id="application1",
                pack_id=created.pack.id,
                payload=_review_payload(created, confirm=False),
                expected_pack_version=1,
                idempotency_key="prepared-review",
                keyring=keyring,
            )

    with database.session() as session:
        assert session.scalar(select(func.count(ApplicationPackRevision.id))) == 2
        assert session.scalar(select(func.count(ApplicationPackEvent.id))) == 1


def test_atomic_review_rejects_needs_review_and_stale_evidence_without_partial_writes(
    pack_workspace: tuple[Database, DataKeyring, str],
) -> None:
    database, keyring, resume_id = pack_workspace
    created = _create(database, keyring, resume_id)
    assert created is not None and created.pack is not None

    with pytest.raises(ResourceConflict, match="every requirement"):
        with database.session() as session:
            create_application_pack_revision(
                session,
                owner_id="owner-a",
                application_id="application1",
                pack_id=created.pack.id,
                payload=_review_payload(created, needs_review=True, confirm=True),
                expected_pack_version=1,
                idempotency_key="incomplete-prepared-review",
                keyring=keyring,
            )

    with database.session() as session:
        evidence = session.scalar(
            select(AchievementEvidence).where(AchievementEvidence.owner_id == "owner-a")
        )
        assert evidence is not None
        retired = update_achievement_evidence(
            session,
            owner_id="owner-a",
            evidence_id=evidence.id,
            patch=AchievementEvidencePatch(approval_state="retired"),
            expected_version=evidence.version,
            keyring=keyring,
            now=NOW + timedelta(minutes=5),
        )
        assert retired is not None

    with pytest.raises(ResourceConflict, match="mapped evidence changed|currently approved"):
        with database.session() as session:
            create_application_pack_revision(
                session,
                owner_id="owner-a",
                application_id="application1",
                pack_id=created.pack.id,
                payload=_review_payload(created, confirm=True),
                expected_pack_version=1,
                idempotency_key="stale-prepared-review",
                keyring=keyring,
            )

    with database.session() as session:
        pack = session.get(ApplicationPack, created.pack.id)
        assert pack is not None and pack.version == 1
        assert session.scalar(select(func.count(ApplicationPackRevision.id))) == 1
        assert session.scalar(select(func.count(ApplicationPackEvent.id))) == 0


def test_review_blocks_needs_review_stale_evidence_and_stale_pack_version(
    pack_workspace: tuple[Database, DataKeyring, str],
) -> None:
    database, keyring, resume_id = pack_workspace
    created = _create(database, keyring, resume_id)
    assert created is not None and created.pack is not None
    with pytest.raises(VersionConflict):
        with database.session() as session:
            create_application_pack_revision(
                session,
                owner_id="owner-a",
                application_id="application1",
                pack_id=created.pack.id,
                payload=_review_payload(created, confirm=True),
                expected_pack_version=99,
                idempotency_key="stale-revision",
                keyring=keyring,
            )

    with database.session() as session:
        draft = create_application_pack_revision(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=created.pack.id,
            payload=_review_payload(created, needs_review=True),
            expected_pack_version=1,
            idempotency_key="needs-review-revision",
            keyring=keyring,
        )
    assert draft is not None and draft.current_revision is not None
    with pytest.raises(ResourceConflict, match="every requirement"):
        with database.session() as session:
            record_application_pack_event(
                session,
                owner_id="owner-a",
                application_id="application1",
                pack_id=created.pack.id,
                payload=ApplicationPackEventCreate.model_validate(
                    {
                        "event_type": "reviewed",
                        "revision_id": draft.current_revision.id,
                        "confirm_requirements_reviewed": True,
                    }
                ),
                expected_pack_version=2,
                idempotency_key="bad-review",
                keyring=keyring,
            )

    with database.session() as session:
        evidence = session.scalar(
            select(AchievementEvidence).where(AchievementEvidence.owner_id == "owner-a")
        )
        assert evidence is not None
        retired = update_achievement_evidence(
            session,
            owner_id="owner-a",
            evidence_id=evidence.id,
            patch=AchievementEvidencePatch(approval_state="retired"),
            expected_version=evidence.version,
            keyring=keyring,
            now=NOW + timedelta(minutes=7),
        )
        assert retired is not None
    with database.session() as session:
        projection = load_application_pack(
            session,
            owner_id="owner-a",
            application_id="application1",
            keyring=keyring,
        )
    assert projection is not None
    assert "mapped_evidence_changed" in {item.value for item in projection.blockers}


def test_review_confirmation_locks_posting_and_mapped_evidence(
    pack_workspace: tuple[Database, DataKeyring, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, keyring, resume_id = pack_workspace
    created = _create(database, keyring, resume_id)
    assert created is not None and created.pack is not None
    with database.session() as session:
        revision = create_application_pack_revision(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=created.pack.id,
            payload=_review_payload(created),
            expected_pack_version=1,
            idempotency_key="locking-revision",
            keyring=keyring,
        )
    assert revision is not None and revision.current_revision is not None

    locked_tables: set[str] = set()
    with database.session() as session:
        original_scalar = session.scalar
        original_scalars = session.scalars

        def remember_lock(statement: Any) -> None:
            if getattr(statement, "_for_update_arg", None) is None:
                return
            locked_tables.update(
                table.name
                for table in statement.get_final_froms()
                if getattr(table, "name", None)
            )

        def tracked_scalar(statement: Any, *args: Any, **kwargs: Any) -> Any:
            remember_lock(statement)
            return original_scalar(statement, *args, **kwargs)

        def tracked_scalars(statement: Any, *args: Any, **kwargs: Any) -> Any:
            remember_lock(statement)
            return original_scalars(statement, *args, **kwargs)

        monkeypatch.setattr(session, "scalar", tracked_scalar)
        monkeypatch.setattr(session, "scalars", tracked_scalars)
        confirmed = record_application_pack_event(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=created.pack.id,
            payload=ApplicationPackEventCreate.model_validate(
                {
                    "event_type": "reviewed",
                    "revision_id": revision.current_revision.id,
                    "confirm_requirements_reviewed": True,
                }
            ),
            expected_pack_version=2,
            idempotency_key="locking-review",
            keyring=keyring,
        )

    assert confirmed is not None and confirmed.status.value == "reviewed"
    assert {"job_postings", "achievement_evidence"} <= locked_tables


def test_missing_description_requires_owner_copy_and_summary_is_not_promoted(
    pack_workspace: tuple[Database, DataKeyring, str],
) -> None:
    database, keyring, resume_id = pack_workspace
    with database.session() as session:
        posting_version = session.get(JobPostingVersion, "postingversion1")
        assert posting_version is not None
        posting_version.description = None
    with pytest.raises(ValueError, match="owner_job_description is required"):
        _create(database, keyring, resume_id)

    owner_description = "Requirements:\n- Experience with Python is required."
    with database.session() as session:
        created = create_application_pack(
            session,
            owner_id="owner-a",
            application_id="application1",
            payload=ApplicationPackCreate(
                base_resume_version_id=resume_id,
                owner_job_description=owner_description,
            ),
            expected_application_version=1,
            idempotency_key="owner-description",
            keyring=keyring,
        )
    assert created is not None and created.current_revision is not None
    assert created.current_revision.job_description_source.value == "owner_supplied"
    assert created.current_revision.job_description == owner_description


def test_closed_posting_is_readable_but_all_pack_mutations_are_blocked(
    pack_workspace: tuple[Database, DataKeyring, str],
) -> None:
    database, keyring, resume_id = pack_workspace
    created = _create(database, keyring, resume_id)
    assert created is not None and created.pack is not None
    with database.session() as session:
        posting = session.get(JobPosting, "posting1")
        assert posting is not None
        posting.lifecycle_state = "closed"
        posting.closure_reason = "explicit"
        posting.closed_at = NOW + timedelta(minutes=8)
        posting.version += 1
    with database.session() as session:
        projection = load_application_pack(
            session,
            owner_id="owner-a",
            application_id="application1",
            keyring=keyring,
        )
    assert projection is not None
    assert "posting_closed" in {item.value for item in projection.blockers}
    with pytest.raises(ResourceConflict, match="closed postings"):
        with database.session() as session:
            create_application_pack_revision(
                session,
                owner_id="owner-a",
                application_id="application1",
                pack_id=created.pack.id,
                payload=_review_payload(created, confirm=True),
                expected_pack_version=1,
                idempotency_key="closed-revision",
                keyring=keyring,
            )


def test_owner_scope_and_explicit_resume_delete_guard(
    pack_workspace: tuple[Database, DataKeyring, str],
) -> None:
    database, keyring, resume_id = pack_workspace
    created = _create(database, keyring, resume_id)
    assert created is not None
    with database.session() as session:
        assert load_application_pack(
            session,
            owner_id="owner-b",
            application_id="application1",
            keyring=keyring,
        ) is None
    with pytest.raises(ResourceInUse, match="application pack"):
        with database.session() as session:
            delete_resume_version(
                session,
                owner_id="owner-a",
                resume_version_id=resume_id,
                expected_version=1,
            )
