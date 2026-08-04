from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from job_hunt_agent.database import Database
from job_hunt_agent.fit_evaluation import FitVerdict, InvalidFitVerdict
from job_hunt_agent.fit_evaluation_repository import (
    FitEvaluatorIdentity,
    load_cached_fit_verdict,
    prepare_fit_evaluation,
    store_fit_verdict,
)
from job_hunt_agent.models import (
    AchievementEvidence,
    Base,
    CandidateProfile,
    CareerTrack,
    JobPosting,
    JobPostingVersion,
    OpportunityFitEvaluation,
    OpportunityScan,
    Owner,
    ResumeVersion,
    SavedSearch,
    SavedSearchMatch,
)
from job_hunt_agent.private_payloads import encrypt_private_payload
from job_hunt_agent.security import DataKeyring


NOW = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)
IDENTITY = FitEvaluatorIdentity(
    provider="google_gemini",
    model="gemini-test",
    prompt_version="opportunity-fit-v1",
)


@pytest.fixture
def fit_repository(tmp_path: Path) -> tuple[Database, DataKeyring]:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'fit-repository.db'}")
    keyring = DataKeyring([("test-v1", Fernet.generate_key().decode("ascii"))])
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        _seed_owner_graph(session, keyring, "a")
        _seed_owner_graph(session, keyring, "b")
    try:
        yield database, keyring
    finally:
        database.dispose()


def test_prepare_uses_full_profile_and_exact_versioned_fingerprint(
    fit_repository: tuple[Database, DataKeyring],
) -> None:
    database, keyring = fit_repository
    with database.session() as session:
        prepared = _prepare(session, keyring, "a")
        other_model = prepare_fit_evaluation(
            session,
            owner_id="owner-a",
            posting_version_id="version-a",
            saved_search_id="search-a",
            identity=FitEvaluatorIdentity(
                provider="google_gemini",
                model="gemini-next",
                prompt_version="opportunity-fit-v1",
            ),
            keyring=keyring,
        )

    assert prepared.inputs.profile.career_thesis == "Build reliable backend systems."
    assert prepared.inputs.profile.current_title == "Software Engineer"
    assert prepared.inputs.profile.skills == ("AWS", "Kafka", "Python")
    assert prepared.inputs.evidence[0].id == "evidence-a"
    assert prepared.deterministic.eligibility == "eligible"
    assert len(prepared.profile_input_fingerprint) == 64
    assert prepared.profile_input_fingerprint == other_model.profile_input_fingerprint
    assert prepared.input_fingerprint != other_model.input_fingerprint


def test_cache_is_encrypted_owner_scoped_and_idempotent(
    fit_repository: tuple[Database, DataKeyring],
) -> None:
    database, keyring = fit_repository
    verdict = FitVerdict(
        band="strong",
        reasons=("Production event-pipeline evidence supports the backend role.",),
        gaps=("The posting does not state compensation.",),
        evidence_ids=("evidence-a",),
    )
    with database.session() as session:
        prepared = _prepare(session, keyring, "a")
        created = store_fit_verdict(
            session,
            prepared=prepared,
            verdict=verdict,
            keyring=keyring,
            now=NOW,
        )
        replay = store_fit_verdict(
            session,
            prepared=prepared,
            verdict=verdict,
            keyring=keyring,
            now=NOW,
        )
        row = session.scalar(
            select(OpportunityFitEvaluation).where(
                OpportunityFitEvaluation.id == created.record_id
            )
        )
        assert row is not None
        ciphertext = row.encrypted_payload

    assert created.created is True
    assert replay.created is False
    assert replay.record_id == created.record_id
    assert "event-pipeline" not in ciphertext
    assert "Production" not in ciphertext

    with database.session() as session:
        prepared = _prepare(session, keyring, "a")
        cached = load_cached_fit_verdict(
            session,
            prepared=prepared,
            keyring=keyring,
        )
        foreign = _prepare(session, keyring, "b")
        foreign_cache = load_cached_fit_verdict(
            session,
            prepared=foreign,
            keyring=keyring,
        )

    assert cached is not None
    assert cached.verdict == verdict
    assert foreign_cache is None


def test_unapproved_evidence_id_is_never_cached(
    fit_repository: tuple[Database, DataKeyring],
) -> None:
    database, keyring = fit_repository
    with database.session() as session:
        prepared = _prepare(session, keyring, "a")
        with pytest.raises(InvalidFitVerdict):
            store_fit_verdict(
                session,
                prepared=prepared,
                verdict=FitVerdict(
                    band="strong",
                    reasons=("Unsupported claim.",),
                    evidence_ids=("invented",),
                ),
                keyring=keyring,
                now=NOW,
            )
        assert session.scalar(select(func.count(OpportunityFitEvaluation.id))) == 0


def test_profile_version_change_misses_old_cache(
    fit_repository: tuple[Database, DataKeyring],
) -> None:
    database, keyring = fit_repository
    verdict = FitVerdict(
        band="promising",
        reasons=("Approved production evidence is relevant.",),
        evidence_ids=("evidence-a",),
    )
    with database.session() as session:
        original = _prepare(session, keyring, "a")
        store_fit_verdict(
            session,
            prepared=original,
            verdict=verdict,
            keyring=keyring,
            now=NOW,
        )
        profile = session.scalar(
            select(CandidateProfile).where(CandidateProfile.owner_id == "owner-a")
        )
        assert profile is not None
        profile.version += 1
        session.flush()
        changed = _prepare(session, keyring, "a")
        cached = load_cached_fit_verdict(
            session,
            prepared=changed,
            keyring=keyring,
        )

    assert changed.profile_input_fingerprint != original.profile_input_fingerprint
    assert changed.input_fingerprint != original.input_fingerprint
    assert cached is None


def _prepare(session, keyring: DataKeyring, suffix: str):
    return prepare_fit_evaluation(
        session,
        owner_id=f"owner-{suffix}",
        posting_version_id=f"version-{suffix}",
        saved_search_id=f"search-{suffix}",
        identity=IDENTITY,
        keyring=keyring,
    )


def _seed_owner_graph(session, keyring: DataKeyring, suffix: str) -> None:
    owner_id = f"owner-{suffix}"
    resume_id = f"resume-{suffix}"
    profile_id = f"profile-{suffix}"
    evidence_id = f"evidence-{suffix}"
    track_id = f"track-{suffix}"
    search_id = f"search-{suffix}"
    scan_id = f"scan-{suffix}"
    posting_id = f"posting-{suffix}"
    version_id = f"version-{suffix}"

    session.add(Owner(id=owner_id, display_name=owner_id, timezone="UTC"))
    session.flush()
    resume_envelope = encrypt_private_payload(
        keyring,
        record_kind="resume_version",
        owner_id=owner_id,
        record_id=resume_id,
        payload={
            "content": (
                "Software engineer owning Python and AWS backend services, Kafka event "
                "pipelines, REST APIs, retries, and DLQ handling in production."
            )
        },
    )
    profile_envelope = encrypt_private_payload(
        keyring,
        record_kind="candidate_profile",
        owner_id=owner_id,
        record_id=profile_id,
        payload={
            "career_thesis": "Build reliable backend systems.",
            "current_title": "Software Engineer",
            "current_location": "India",
            "years_of_experience": 1.0,
            "skills": ["AWS", "Kafka", "Python"],
            "work_authorizations": [
                {"country_code": "IN", "status": "citizen"}
            ],
            "work_modes": ["remote", "hybrid"],
            "employment_types": ["full_time"],
            "notice_period_days": 30,
        },
    )
    evidence_envelope = encrypt_private_payload(
        keyring,
        record_kind="achievement_evidence",
        owner_id=owner_id,
        record_id=evidence_id,
        payload={
            "statement": "Owned a production AWS and Kafka event pipeline with DLQs.",
            "source_excerpt": None,
        },
    )
    session.add_all(
        [
            ResumeVersion(
                id=resume_id,
                owner_id=owner_id,
                label="Backend resume",
                encrypted_content=resume_envelope.ciphertext,
                encryption_key_id=resume_envelope.key_id,
                content_hash=("a" if suffix == "a" else "b") * 64,
                source="uploaded",
                is_base=True,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            ),
            CandidateProfile(
                id=profile_id,
                owner_id=owner_id,
                encrypted_payload=profile_envelope.ciphertext,
                encryption_key_id=profile_envelope.key_id,
                onboarding_state="complete",
                version=1,
                created_at=NOW,
                updated_at=NOW,
            ),
            CareerTrack(
                id=track_id,
                owner_id=owner_id,
                name="Backend roles",
                role_families=["Backend Engineer"],
                seniority_levels=["junior", "mid"],
                target_locations=["India", "Remote"],
                priorities={
                    "compensation": 3,
                    "scope": 4,
                    "learning": 4,
                    "company_quality": 3,
                    "flexibility": 3,
                },
                active=True,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            ),
            AchievementEvidence(
                id=evidence_id,
                owner_id=owner_id,
                source_resume_version_id=None,
                encrypted_payload=evidence_envelope.ciphertext,
                encryption_key_id=evidence_envelope.key_id,
                skills=["AWS", "Kafka", "DLQ"],
                origin="owner_entered",
                approval_state="approved",
                approved_at=NOW,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            ),
        ]
    )
    session.flush()
    session.add(
        SavedSearch(
            id=search_id,
            owner_id=owner_id,
            career_track_id=track_id,
            resume_version_id=resume_id,
            name="Backend India",
            criteria_schema_version=1,
            criteria={
                "role_keywords": ["backend"],
                "seniority": "mid",
                "location": ["India", "Remote"],
                "employment_types": ["full_time"],
                "max_age_days": 45,
                "country": "in",
            },
            pack="backend_india",
            use_self_rag=False,
            cadence="manual",
            schedule={},
            timezone="UTC",
            active=True,
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.flush()
    session.add(
        OpportunityScan(
            id=scan_id,
            owner_id=owner_id,
            saved_search_id=search_id,
            saved_search_version=1,
            criteria_schema_version=1,
            criteria_snapshot={"role_keywords": ["backend"]},
            pack_snapshot="backend_india",
            trigger="manual",
            scheduled_for=NOW,
            dedupe_key=scan_id,
            request_hash=("c" if suffix == "a" else "d") * 64,
            status="succeeded",
            stage="complete",
            source_count=0,
            finalized_at=NOW,
            started_at=NOW,
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    posting = JobPosting(
        id=posting_id,
        owner_id=owner_id,
        identity_kind="native",
        identity_key=f"greenhouse:example:{suffix}",
        identity_key_hash=("e" if suffix == "a" else "f") * 64,
        source="greenhouse",
        company_slug=f"example-{suffix}",
        source_job_id=f"job-{suffix}",
        canonical_url=f"https://jobs.example.com/{suffix}",
        lifecycle_state="open",
        consecutive_complete_omissions=0,
        first_confirmed_at=NOW,
        last_confirmed_at=NOW,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(posting)
    session.flush()
    session.add(
        JobPostingVersion(
            id=version_id,
            owner_id=owner_id,
            job_posting_id=posting_id,
            version_number=1,
            content_hash=("1" if suffix == "a" else "2") * 64,
            source="greenhouse",
            source_job_id=f"job-{suffix}",
            company_name="Example",
            title="Software Engineer",
            canonical_url=f"https://jobs.example.com/{suffix}",
            apply_urls=[f"https://jobs.example.com/{suffix}"],
            location="India",
            summary="Build backend services.",
            description=(
                "Build reliable Python backend services, REST APIs, AWS event pipelines, "
                "and Kafka integrations. Own production reliability, retries, and DLQs. "
                "Work with PostgreSQL and distributed systems in a collaborative team."
            ),
            employment_type="full_time",
            source_facts={},
            source_confidence=1.0,
            observed_at=NOW,
            created_at=NOW,
        )
    )
    session.flush()
    session.add(
        SavedSearchMatch(
            id=f"match-{suffix}",
            owner_id=owner_id,
            saved_search_id=search_id,
            job_posting_id=posting_id,
            first_scan_id=scan_id,
            last_scan_id=scan_id,
            last_posting_version_id=version_id,
            match_count=1,
            first_matched_at=NOW,
            last_matched_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.flush()
