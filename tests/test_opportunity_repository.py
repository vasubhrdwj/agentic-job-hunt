from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import event, func, select
from sqlalchemy.dialects import postgresql

import job_hunt_agent.opportunity_repository as opportunity_repository_module
from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    AchievementEvidence,
    Base,
    CandidateProfile,
    CareerTrack,
    JobObservation,
    JobPosting,
    JobPostingAlias,
    JobPostingVersion,
    OpportunityDecisionEvent,
    OpportunityScan,
    OpportunityScanSource,
    Owner,
    OwnerOpportunity,
    ResumeVersion,
    SavedSearch,
    SavedSearchMatch,
)
from job_hunt_agent.opportunity_repository import (
    OpportunityNotFound,
    canonicalize_posting_url,
    decide_owner_opportunity,
    list_today_opportunities,
    load_opportunity_detail,
    persist_scan_source_role,
    posting_identity,
)
from job_hunt_agent.opportunity_assessment import OpportunityAssessment
from job_hunt_agent.opportunity_schemas import OpportunityDecisionRequest, TodayQuery
from job_hunt_agent.private_payloads import encrypt_private_payload
from job_hunt_agent.repository_errors import ResourceConflict, VersionConflict
from job_hunt_agent.schemas import CompanySource, EmploymentType, Role
from job_hunt_agent.security import DataKeyring


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def radar(tmp_path: Path) -> tuple[Database, DataKeyring]:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'radar.db'}")
    Base.metadata.create_all(database.engine)
    keyring = DataKeyring([("test-v1", Fernet.generate_key().decode("ascii"))])
    with database.session() as session:
        for owner_id in ("owner-a", "owner-b"):
            _seed_owner_search(
                session,
                owner_id,
                f"search-{owner_id[-1]}",
                keyring=keyring,
            )
        _seed_scan_source(session, "owner-a", "search-a", "scan-a1", "source-a1")
    try:
        yield database, keyring
    finally:
        database.dispose()


def _seed_owner_search(
    session,
    owner_id: str,
    search_id: str,
    *,
    keyring: DataKeyring,
) -> None:
    track_id = f"track-{owner_id[-1]}"
    resume_id = f"resume-{owner_id[-1]}"
    session.add(Owner(id=owner_id, display_name=owner_id, timezone="UTC"))
    session.add(
        CareerTrack(
            id=track_id,
            owner_id=owner_id,
            name=f"Track {owner_id}",
            role_families=["Backend Engineer"],
            seniority_levels=["senior"],
            target_locations=["India"],
            priorities={},
            active=True,
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    resume_content = (
        "Backend software engineer building Python, distributed systems, REST APIs, "
        "AWS, Docker, PostgreSQL, Kafka, OAuth, and reliable event pipelines."
    )
    resume_envelope = encrypt_private_payload(
        keyring,
        record_kind="resume_version",
        owner_id=owner_id,
        record_id=resume_id,
        payload={"content": resume_content},
    )
    session.add(
        ResumeVersion(
            id=resume_id,
            owner_id=owner_id,
            label="Resume",
            encrypted_content=resume_envelope.ciphertext,
            encryption_key_id=resume_envelope.key_id,
            content_hash=("a" if owner_id == "owner-a" else "b") * 64,
            source="pasted",
            is_base=True,
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.flush()
    session.add(
        SavedSearch(
            id=search_id,
            owner_id=owner_id,
            career_track_id=track_id,
            resume_version_id=resume_id,
            name=f"Search {owner_id}",
            criteria_schema_version=1,
            criteria={
                "role_keywords": ["backend"],
                "seniority": "senior",
                "location": ["India"],
                "employment_types": ["full_time"],
                "country": "in",
            },
            pack="backend_india",
            use_self_rag=False,
            cadence="manual",
            schedule={"local_time": None, "days_of_week": []},
            timezone="UTC",
            active=True,
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def _seed_scan_source(
    session,
    owner_id: str,
    search_id: str,
    scan_id: str,
    source_id: str,
    *,
    company_slug: str = "acme",
) -> None:
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
            request_hash=(scan_id[0] * 64),
            status="running",
            stage="persisting",
            source_count=1,
            started_at=NOW,
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.flush()
    session.add(
        OpportunityScanSource(
            id=source_id,
            owner_id=owner_id,
            opportunity_scan_id=scan_id,
            company_slug=company_slug,
            source="greenhouse",
            status="running",
            fetch_scope="criteria_filtered",
            completeness="partial",
            observed_count=1,
            returned_count=1,
            persisted_count=0,
            warning_codes=[],
            version=1,
            started_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def _finish_scan_source(
    session,
    *,
    scan_id: str,
    source_id: str,
    completed_at: datetime,
    succeeded: bool = True,
) -> None:
    source = session.get(OpportunityScanSource, source_id)
    scan = session.get(OpportunityScan, scan_id)
    assert source is not None and scan is not None
    source.observed_count = max(source.observed_count, source.persisted_count)
    source.returned_count = max(source.returned_count, source.persisted_count)
    source.status = "succeeded" if succeeded else "failed"
    source.error_code = None if succeeded else "source_fetch_failed"
    source.completed_at = completed_at
    source.updated_at = completed_at
    source.version += 1
    scan.status = "partial" if succeeded else "failed"
    scan.stage = "complete"
    scan.source_count = 1
    scan.terminal_source_count = 1
    scan.successful_source_count = int(succeeded)
    scan.failed_source_count = int(not succeeded)
    scan.finalized_at = completed_at
    scan.updated_at = completed_at
    scan.version += 1
    session.flush()


def _seed_approved_evidence(
    session,
    *,
    owner_id: str,
    evidence_id: str,
    keyring: DataKeyring,
    statement: str = "Owned reliable Python distributed systems in production.",
    skills: list[str] | None = None,
) -> None:
    envelope = encrypt_private_payload(
        keyring,
        record_kind="achievement_evidence",
        owner_id=owner_id,
        record_id=evidence_id,
        payload={"statement": statement, "source_excerpt": None},
    )
    session.add(
        AchievementEvidence(
            id=evidence_id,
            owner_id=owner_id,
            source_resume_version_id=None,
            encrypted_payload=envelope.ciphertext,
            encryption_key_id=envelope.key_id,
            skills=skills or ["Python", "distributed systems"],
            origin="owner_entered",
            approval_state="approved",
            approved_at=NOW,
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def _seed_candidate_profile(
    session,
    *,
    owner_id: str,
    profile_id: str,
    keyring: DataKeyring,
) -> None:
    envelope = encrypt_private_payload(
        keyring,
        record_kind="candidate_profile",
        owner_id=owner_id,
        record_id=profile_id,
        payload={
            "current_location": "Gurugram, India",
            "years_of_experience": 1.5,
            "work_authorizations": [
                {"country_code": "IN", "status": "citizen"}
            ],
            "work_modes": ["remote", "hybrid"],
            "employment_types": ["full_time"],
        },
    )
    session.add(
        CandidateProfile(
            id=profile_id,
            owner_id=owner_id,
            encrypted_payload=envelope.ciphertext,
            encryption_key_id=envelope.key_id,
            onboarding_state="complete",
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def _role(**updates) -> Role:
    values = {
        "company": "Acme",
        "title": "Senior Backend Engineer",
        "url": "https://jobs.acme.example/openings/123?utm_source=google#apply",
        "location": "India",
        "summary": "Build reliable backend systems.",
        "match_reason": "PRIVATE RESUME MATCH REASON",
        "source": CompanySource.greenhouse,
        "company_slug": "acme",
        "source_job_id": "123",
        "apply_urls": [
            "https://jobs.acme.example/openings/123/?utm_medium=organic",
        ],
        "posted_at": "2026-07-12",
        "source_updated_at": "2026-07-13T08:00:00Z",
        "employment_type": EmploymentType.full_time,
        "raw_description": "Build Python and distributed systems.",
        "fit_score": 0.99,
        "confidence": 1.0,
    }
    values.update(updates)
    return Role(**values)


def _categorical_rank_assessment(*, posting, **_kwargs) -> OpportunityAssessment:
    if posting.title.startswith("Strong"):
        fit_band, confidence, eligibility = "strong", "high", "eligible"
    elif posting.title.startswith("Promising"):
        fit_band, confidence, eligibility = "promising", "medium", "eligible"
    elif posting.title.startswith("Stretch"):
        fit_band, confidence, eligibility = "stretch", "medium", "eligible"
    else:
        fit_band, confidence, eligibility = "low", "high", "likely_ineligible"
    return OpportunityAssessment(
        algorithm_version="test-categorical-v1",
        fit_band=fit_band,
        confidence=confidence,
        eligibility=eligibility,
        matched_terms=("Python",) if fit_band != "low" else (),
        representative_requirement="Build reliable backend systems.",
        approved_evidence_ids=("evidence-a",) if fit_band != "low" else (),
        strengths=("Relevant backend evidence",) if fit_band != "low" else (),
        gaps=("Eligibility does not match",) if fit_band == "low" else (),
    )


def _seed_categorical_rank_roles(session) -> dict[str, str]:
    specifications = [
        ("alpha", "Strong Alpha Older", "strong-alpha-old", 1),
        ("alpha", "Strong Alpha Newer", "strong-alpha-new", 4),
        ("beta", "Strong Beta", "strong-beta", 5),
        ("gamma", "Promising Gamma", "promising-gamma", 20),
        ("delta", "Stretch Delta", "stretch-delta", 30),
        ("epsilon", "Low Epsilon", "low-epsilon", 40),
    ]
    source_by_company: dict[str, str] = {}
    for company, _title, _job_id, _minute in specifications:
        if company in source_by_company:
            continue
        scan_id = f"scan-rank-{company}"
        source_id = f"source-rank-{company}"
        _seed_scan_source(
            session,
            "owner-a",
            "search-a",
            scan_id,
            source_id,
            company_slug=company,
        )
        source = session.get(OpportunityScanSource, source_id)
        assert source is not None
        source.observed_count = sum(
            1 for candidate in specifications if candidate[0] == company
        )
        source.returned_count = source.observed_count
        source_by_company[company] = source_id
    result: dict[str, str] = {}
    for company, title, job_id, minute in specifications:
        persisted = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id=source_by_company[company],
            role=_role(
                company=company.title(),
                company_slug=company,
                title=title,
                source_job_id=job_id,
                url=f"https://jobs.{company}.example/{job_id}",
            ),
            first_party_url_verified=True,
            now=NOW + timedelta(minutes=minute),
        )
        result[job_id] = persisted.opportunity_id
    return result


@pytest.mark.parametrize(
    "value",
    [
        "http://jobs.example/1",
        "https://user@jobs.example/1",
        "https://jobs.example/a/../1",
        "https://jobs.example/%252e%252e/secret",
        "https://jobs.example/a\\b",
    ],
)
def test_canonical_url_rejects_untrusted_values(value: str) -> None:
    with pytest.raises(ValueError):
        canonicalize_posting_url(value)


def test_canonical_url_and_identity_are_stable_and_never_use_title() -> None:
    canonical = canonicalize_posting_url(
        "HTTPS://Jobs.Acme.Example:443/openings/123/?b=2&utm_source=x&a=1#apply"
    )
    assert canonical == "https://jobs.acme.example/openings/123?a=1&b=2"
    first = posting_identity(_role(title="Old title"), canonical_url=canonical)
    changed = posting_identity(_role(title="New title"), canonical_url=canonical)
    assert first.kind == "native"
    assert first.key_hash == changed.key_hash
    url_only = _role(
        title="Legacy title",
        company_slug=None,
        source_job_id=None,
    )
    fallback = posting_identity(
        url_only,
        canonical_url=canonical,
        company_slug="acme",
    )
    renamed_fallback = posting_identity(
        url_only.model_copy(update={"title": "Renamed legacy title"}),
        canonical_url=canonical,
        company_slug="acme",
    )
    assert fallback.kind == "url"
    assert fallback.key_hash == renamed_fallback.key_hash
    other_company = posting_identity(
        url_only,
        canonical_url=canonical,
        company_slug="beta",
    )
    assert other_company.key_hash != fallback.key_hash


@pytest.mark.parametrize("shared_canonical_url", [False, True])
def test_distinct_native_requisitions_never_merge_through_shared_urls(
    radar: tuple[Database, DataKeyring],
    shared_canonical_url: bool,
) -> None:
    database, _keyring = radar
    generic_apply = "https://jobs.acme.example/apply"
    with database.session() as session:
        first = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(
                source_job_id="req-a",
                url="https://jobs.acme.example/openings/req-a",
                apply_urls=[generic_apply],
            ),
            first_party_url_verified=True,
            now=NOW,
        )
        _seed_scan_source(session, "owner-a", "search-a", "scan-a2", "source-a2")
        second = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a2",
            role=_role(
                source_job_id="req-b",
                url=(
                    "https://jobs.acme.example/openings/req-a"
                    if shared_canonical_url
                    else "https://jobs.acme.example/openings/req-b"
                ),
                apply_urls=[generic_apply],
            ),
            first_party_url_verified=True,
            now=NOW + timedelta(minutes=1),
        )
        assert second.posting_id != first.posting_id
        assert second.opportunity_id != first.opportunity_id
        assert session.scalar(select(func.count(JobPosting.id))) == 2
        assert session.scalar(select(func.count(JobPostingVersion.id))) == 2
        assert session.scalar(select(func.count(JobObservation.id))) == 2
        assert session.scalar(select(func.count(OwnerOpportunity.id))) == 2
        assert session.scalar(
            select(func.count(JobPostingAlias.id)).where(
                JobPostingAlias.normalized_url == generic_apply
            )
        ) == 0


def test_url_fallback_identity_is_scoped_to_registry_company(
    radar: tuple[Database, DataKeyring],
) -> None:
    database, _keyring = radar
    shared_url = "https://shared-ats.example/openings/123"
    with database.session() as session:
        first = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(
                company_slug=None,
                source_job_id=None,
                url=shared_url,
                apply_urls=[shared_url],
            ),
            first_party_url_verified=True,
            now=NOW,
        )
        _seed_scan_source(
            session,
            "owner-a",
            "search-a",
            "scan-a2",
            "source-a2",
            company_slug="beta",
        )
        second = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a2",
            role=_role(
                company="Beta",
                company_slug=None,
                source_job_id=None,
                url=shared_url,
                apply_urls=[shared_url],
            ),
            first_party_url_verified=True,
            now=NOW + timedelta(minutes=1),
        )
        assert second.posting_id != first.posting_id
        assert second.opportunity_id != first.opportunity_id
        assert session.scalar(select(func.count(JobPosting.id))) == 2
        assert session.scalar(select(func.count(OwnerOpportunity.id))) == 2


def test_first_native_sighting_promotes_url_fallback_and_fences_later_ids(
    radar: tuple[Database, DataKeyring],
) -> None:
    database, _keyring = radar
    shared_url = "https://jobs.acme.example/openings/shared"
    with database.session() as session:
        fallback = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(
                company_slug=None,
                source_job_id=None,
                url=shared_url,
                apply_urls=[shared_url],
            ),
            first_party_url_verified=True,
            now=NOW,
        )
        _seed_scan_source(session, "owner-a", "search-a", "scan-a2", "source-a2")
        enriched = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a2",
            role=_role(
                source_job_id="req-a",
                url=shared_url,
                apply_urls=[shared_url],
            ),
            first_party_url_verified=True,
            now=NOW + timedelta(minutes=1),
        )
        assert enriched.posting_id == fallback.posting_id
        promoted = session.get(JobPosting, fallback.posting_id)
        assert promoted is not None
        assert promoted.identity_kind == "native"
        assert promoted.source_job_id == "req-a"

        _seed_scan_source(session, "owner-a", "search-a", "scan-a3", "source-a3")
        different = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a3",
            role=_role(
                source_job_id="req-b",
                url=shared_url,
                apply_urls=[shared_url],
            ),
            first_party_url_verified=True,
            now=NOW + timedelta(minutes=2),
        )
        assert different.posting_id != fallback.posting_id
        assert different.opportunity_id != fallback.opportunity_id
        assert session.scalar(select(func.count(JobPosting.id))) == 2
        assert session.scalar(select(func.count(OwnerOpportunity.id))) == 2


def test_repeated_and_changed_sightings_version_one_stable_opportunity(
    radar: tuple[Database, DataKeyring],
) -> None:
    database, keyring = radar
    with database.session() as session:
        first = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(),
            first_party_url_verified=True,
            now=NOW,
        )
        replay = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(
                title="Replay payload must not mutate history",
                raw_description="A retried source result with changed bytes.",
            ),
            first_party_url_verified=True,
            now=NOW,
        )
        assert first.posting_created and first.version_created and first.opportunity_created
        assert replay.replayed and replay.opportunity_id == first.opportunity_id
        assert session.scalar(select(func.count(JobPostingVersion.id))) == 1

        _seed_scan_source(session, "owner-a", "search-a", "scan-a2", "source-a2")
        unchanged = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a2",
            role=_role(),
            first_party_url_verified=True,
            now=NOW + timedelta(hours=1),
        )
        assert not unchanged.version_created and not unchanged.posting_changed
        opportunity = session.get(OwnerOpportunity, first.opportunity_id)
        assert opportunity is not None
        assert opportunity.last_surfaced_at.replace(tzinfo=timezone.utc) == NOW

        _seed_scan_source(session, "owner-a", "search-a", "scan-a3", "source-a3")
        changed = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a3",
            role=_role(
                title="Staff Backend Engineer",
                location="Remote India",
                raw_description="Build Python, Go, and distributed systems.",
            ),
            first_party_url_verified=True,
            now=NOW + timedelta(hours=2),
        )
        assert changed.posting_id == first.posting_id
        assert changed.opportunity_id == first.opportunity_id
        assert changed.version_created and changed.posting_changed
        assert opportunity.last_surfaced_at.replace(
            tzinfo=timezone.utc
        ) == NOW + timedelta(hours=2)

        _seed_scan_source(session, "owner-a", "search-a", "scan-a4", "source-a4")
        reverted = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a4",
            role=_role(),
            first_party_url_verified=True,
            now=NOW + timedelta(hours=3),
        )
        assert reverted.posting_id == first.posting_id
        assert reverted.opportunity_id == first.opportunity_id
        assert reverted.version_created and reverted.posting_changed

        assert session.scalar(select(func.count(JobPosting.id))) == 1
        assert session.scalar(select(func.count(JobPostingAlias.id))) == 2
        assert session.scalar(select(func.count(JobPostingVersion.id))) == 3
        assert session.scalar(select(func.count(JobObservation.id))) == 4
        assert session.scalar(select(func.count(SavedSearchMatch.id))) == 1
        assert session.scalar(select(func.count(OwnerOpportunity.id))) == 1
        match = session.scalar(select(SavedSearchMatch))
        assert match is not None and match.match_count == 4
        raw_versions = list(session.scalars(select(JobPostingVersion)))
        assert all("PRIVATE RESUME" not in str(row.__dict__) for row in raw_versions)
        assert all("0.99" not in str(row.__dict__) for row in raw_versions)
        assert all(not hasattr(row, "fit_score") for row in raw_versions)

        today = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(),
            keyring=keyring,
            now=NOW + timedelta(hours=4),
        )
        assert today.data_source == "database"
        assert today.summary.needs_decision == 1
        assert len(today.items) == 1
        assert today.items[0].posting.title == "Senior Backend Engineer"
        assert [unknown.field.value for unknown in today.items[0].unknowns] == [
            "compensation"
        ]
        detail = load_opportunity_detail(
            session,
            owner_id="owner-a",
            opportunity_id=first.opportunity_id,
            keyring=keyring,
        )
        assert detail is not None and detail.data_source == "database"
        assert [version.version for version in detail.posting_versions] == [1, 2, 3]
        assert detail.description == "Build Python and distributed systems."
        assert load_opportunity_detail(
            session,
            owner_id="owner-b",
            opportunity_id=first.opportunity_id,
            keyring=keyring,
        ) is None


def test_unknown_source_date_and_employment_type_remain_visible(
    radar: tuple[Database, DataKeyring],
) -> None:
    database, keyring = radar
    with database.session() as session:
        persisted = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(
                posted_at="not-a-date",
                employment_type=EmploymentType.unknown,
            ),
            first_party_url_verified=True,
            now=NOW,
        )

        today = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(),
            keyring=keyring,
            now=NOW,
        )

        assert len(today.items) == 1
        item = today.items[0]
        assert item.id == persisted.opportunity_id
        assert item.facts.employment_type.state.value == "unknown"
        assert item.facts.posted_date.state.value == "unknown"
        assert [unknown.field.value for unknown in item.unknowns] == [
            "employment_type",
            "posted_date",
            "compensation",
        ]


def test_automatic_assessment_reuses_private_inputs_and_matches_detail(
    radar: tuple[Database, DataKeyring],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, keyring = radar
    with database.session() as session:
        _seed_candidate_profile(
            session,
            owner_id="owner-a",
            profile_id="profile-a",
            keyring=keyring,
        )
        _seed_approved_evidence(
            session,
            owner_id="owner-a",
            evidence_id="evidence-a",
            keyring=keyring,
        )
        first = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(location="Remote India"),
            first_party_url_verified=True,
            now=NOW,
        )
        scan_source = session.get(OpportunityScanSource, "source-a1")
        assert scan_source is not None
        scan_source.observed_count = 2
        scan_source.returned_count = 2
        second = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(
                source_job_id="456",
                url="https://jobs.acme.example/openings/456",
                location="Remote India",
                raw_description="Operate reliable Python and Kafka backend systems.",
            ),
            first_party_url_verified=True,
            now=NOW + timedelta(minutes=1),
        )

        decrypt_calls: list[str] = []
        assessed_experience: list[float | None] = []
        original_decrypt = opportunity_repository_module.decrypt_private_payload
        original_assess = opportunity_repository_module.assess_opportunity

        def counted_decrypt(*args, **kwargs):
            decrypt_calls.append(kwargs["record_kind"])
            return original_decrypt(*args, **kwargs)

        def capture_assessment_profile(*args, **kwargs):
            assessed_experience.append(kwargs["profile"].years_of_experience)
            return original_assess(*args, **kwargs)

        monkeypatch.setattr(
            opportunity_repository_module,
            "decrypt_private_payload",
            counted_decrypt,
        )
        monkeypatch.setattr(
            opportunity_repository_module,
            "assess_opportunity",
            capture_assessment_profile,
        )
        today = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(),
            keyring=keyring,
            now=NOW + timedelta(minutes=2),
        )

        by_id = {item.id: item for item in today.items}
        assert set(by_id) == {first.opportunity_id, second.opportunity_id}
        assert all(item.match.state.value == "assessed" for item in today.items)
        assert all(
            item.match.assessment_saved_search_id == "search-a"
            and item.match.resume_version_id == "resume-a"
            and item.match.eligibility.value == "eligible"
            for item in today.items
        )
        assert "evidence-a" in by_id[first.opportunity_id].match.approved_evidence_ids
        assert len(by_id[first.opportunity_id].match.assessment_input_fingerprint or "") == 64
        assert decrypt_calls.count("candidate_profile") == 1
        assert decrypt_calls.count("achievement_evidence") == 1
        assert decrypt_calls.count("resume_version") == 1

        detail = load_opportunity_detail(
            session,
            owner_id="owner-a",
            opportunity_id=first.opportunity_id,
            keyring=keyring,
        )
        assert detail is not None
        assert detail.match == by_id[first.opportunity_id].match
        assert assessed_experience == [1.5, 1.5, 1.5]


def test_assessment_search_precedence_and_latest_match_are_deterministic(
    radar: tuple[Database, DataKeyring],
) -> None:
    database, keyring = radar
    with database.session() as session:
        first = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(),
            first_party_url_verified=True,
            now=NOW,
        )
        base = session.get(SavedSearch, "search-a")
        assert base is not None
        resume_id = "resume-a2"
        envelope = encrypt_private_payload(
            keyring,
            record_kind="resume_version",
            owner_id="owner-a",
            record_id=resume_id,
            payload={"content": "Frontend JavaScript and React engineer."},
        )
        session.add(
            ResumeVersion(
                id=resume_id,
                owner_id="owner-a",
                label="Second resume",
                encrypted_content=envelope.ciphertext,
                encryption_key_id=envelope.key_id,
                content_hash="c" * 64,
                source="pasted",
                is_base=False,
                version=1,
                created_at=NOW + timedelta(minutes=1),
                updated_at=NOW + timedelta(minutes=1),
            )
        )
        session.add(
            SavedSearch(
                id="search-a2",
                owner_id="owner-a",
                career_track_id=base.career_track_id,
                resume_version_id=resume_id,
                name="Second search",
                criteria_schema_version=base.criteria_schema_version,
                criteria=dict(base.criteria),
                pack=base.pack,
                use_self_rag=False,
                cadence="manual",
                schedule={"local_time": None, "days_of_week": []},
                timezone="UTC",
                active=True,
                version=1,
                created_at=NOW + timedelta(minutes=1),
                updated_at=NOW + timedelta(minutes=1),
            )
        )
        session.flush()
        _seed_scan_source(
            session,
            "owner-a",
            "search-a2",
            "scan-a2",
            "source-a2",
        )
        persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a2",
            role=_role(),
            first_party_url_verified=True,
            now=NOW + timedelta(minutes=2),
        )

        default_today = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(),
            keyring=keyring,
            now=NOW + timedelta(minutes=3),
        )
        scan_today = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(scan_id="scan-a1"),
            keyring=keyring,
            now=NOW + timedelta(minutes=3),
        )
        explicit_today = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(saved_search_id="search-a"),
            keyring=keyring,
            now=NOW + timedelta(minutes=3),
        )
        detail = load_opportunity_detail(
            session,
            owner_id="owner-a",
            opportunity_id=first.opportunity_id,
            keyring=keyring,
        )
        selected_detail = load_opportunity_detail(
            session,
            owner_id="owner-a",
            opportunity_id=first.opportunity_id,
            keyring=keyring,
            selected_saved_search_id="search-a",
        )

        assert default_today.items[0].match.assessment_saved_search_id == "search-a2"
        assert default_today.items[0].match.resume_version_id == "resume-a2"
        assert detail is not None and detail.match == default_today.items[0].match
        assert (
            selected_detail is not None
            and selected_detail.match == scan_today.items[0].match
        )
        assert (
            detail.match.assessment_input_fingerprint
            != selected_detail.match.assessment_input_fingerprint
        )
        assert scan_today.items[0].match.assessment_saved_search_id == "search-a"
        assert scan_today.items[0].match.resume_version_id == "resume-a"
        assert explicit_today.items[0].match == scan_today.items[0].match

        second_search = session.get(SavedSearch, "search-a2")
        assert second_search is not None
        second_search.active = False
        second_search.version += 1
        session.flush()
        after_deactivation = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(),
            keyring=keyring,
            now=NOW + timedelta(minutes=4),
        )

        assert after_deactivation.items[0].match.assessment_saved_search_id == "search-a"
        assert after_deactivation.items[0].match.resume_version_id == "resume-a"


@pytest.mark.parametrize("corrupt_kind", ["resume", "profile", "evidence"])
def test_private_assessment_failure_is_safe_and_does_not_break_today(
    radar: tuple[Database, DataKeyring],
    corrupt_kind: str,
) -> None:
    database, keyring = radar
    marker = f"PRIVATE CORRUPT {corrupt_kind.upper()} CONTENT"
    with database.session() as session:
        persisted = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(),
            first_party_url_verified=True,
            now=NOW,
        )
        if corrupt_kind == "resume":
            resume = session.get(ResumeVersion, "resume-a")
            assert resume is not None
            resume.encrypted_content = marker
        elif corrupt_kind == "profile":
            session.add(
                CandidateProfile(
                    id="profile-a",
                    owner_id="owner-a",
                    encrypted_payload=marker,
                    encryption_key_id="test-v1",
                    onboarding_state="complete",
                    version=1,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        else:
            _seed_approved_evidence(
                session,
                owner_id="owner-a",
                evidence_id="evidence-a",
                keyring=keyring,
            )
            evidence = session.get(AchievementEvidence, "evidence-a")
            assert evidence is not None
            evidence.encrypted_payload = marker

        today = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(),
            keyring=keyring,
            now=NOW + timedelta(minutes=1),
        )

        assert [item.id for item in today.items] == [persisted.opportunity_id]
        assert today.items[0].match.state.value == "not_assessed"
        assert (
            today.items[0].match.not_assessed_reason.value
            == "assessment_unavailable"
        )
        assert marker not in today.model_dump_json()


def test_missing_job_description_is_explicitly_not_assessed(
    radar: tuple[Database, DataKeyring],
) -> None:
    database, keyring = radar
    with database.session() as session:
        persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(raw_description=None),
            first_party_url_verified=True,
            now=NOW,
        )
        today = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(),
            keyring=keyring,
            now=NOW,
        )

        assert today.items[0].match.state.value == "not_assessed"
        assert (
            today.items[0].match.not_assessed_reason.value
            == "description_unavailable"
        )


def test_today_scan_filter_returns_only_owner_observations_from_that_scan(
    radar: tuple[Database, DataKeyring],
) -> None:
    database, keyring = radar
    with database.session() as session:
        first = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(source_job_id="first", url="https://jobs.acme.example/first"),
            first_party_url_verified=True,
            now=NOW,
        )
        _seed_scan_source(session, "owner-a", "search-a", "scan-a2", "source-a2")
        second = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a2",
            role=_role(source_job_id="second", url="https://jobs.acme.example/second"),
            first_party_url_verified=True,
            now=NOW + timedelta(minutes=1),
        )

        first_scan = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(scan_id="scan-a1"),
            keyring=keyring,
            now=NOW + timedelta(minutes=2),
        )
        second_scan = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(scan_id="scan-a2"),
            keyring=keyring,
            now=NOW + timedelta(minutes=2),
        )
        foreign_owner = list_today_opportunities(
            session,
            owner_id="owner-b",
            query=TodayQuery(scan_id="scan-a1"),
            keyring=keyring,
            now=NOW + timedelta(minutes=2),
        )
        missing_scan = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(scan_id="does-not-exist"),
            keyring=keyring,
            now=NOW + timedelta(minutes=2),
        )

        assert [item.id for item in first_scan.items] == [first.opportunity_id]
        assert [item.id for item in second_scan.items] == [second.opportunity_id]
        assert first_scan.summary.needs_decision == 1
        assert second_scan.summary.needs_decision == 1
        assert foreign_owner.items == []
        assert foreign_owner.summary.needs_decision == 0
        assert missing_scan.items == []
        assert missing_scan.summary.needs_decision == 0


def test_today_inbox_uses_latest_reliable_partition_snapshot(
    radar: tuple[Database, DataKeyring],
) -> None:
    database, keyring = radar
    with database.session() as session:
        first_source = session.get(OpportunityScanSource, "source-a1")
        assert first_source is not None
        first_source.observed_count = 4
        first_source.returned_count = 4
        old_only = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(
                source_job_id="old-only",
                url="https://jobs.acme.example/old-only",
                title="Old Snapshot Role",
            ),
            first_party_url_verified=True,
            now=NOW,
        )
        shared = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(
                source_job_id="shared",
                url="https://jobs.acme.example/shared",
                title="Current Snapshot Role",
            ),
            first_party_url_verified=True,
            now=NOW + timedelta(minutes=1),
        )
        dismissed = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(
                source_job_id="dismissed-history",
                url="https://jobs.acme.example/dismissed-history",
                title="Dismissed Historical Role",
            ),
            first_party_url_verified=True,
            now=NOW + timedelta(minutes=2),
        )
        closed = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(
                source_job_id="closed-history",
                url="https://jobs.acme.example/closed-history",
                title="Closed Historical Role",
            ),
            first_party_url_verified=True,
            now=NOW + timedelta(minutes=3),
        )
        _finish_scan_source(
            session,
            scan_id="scan-a1",
            source_id="source-a1",
            completed_at=NOW + timedelta(minutes=4),
        )

        _seed_scan_source(session, "owner-a", "search-a", "scan-a2", "source-a2")
        persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a2",
            role=_role(
                source_job_id="shared",
                url="https://jobs.acme.example/shared",
                title="Current Snapshot Role",
            ),
            first_party_url_verified=True,
            now=NOW + timedelta(minutes=5),
        )
        _finish_scan_source(
            session,
            scan_id="scan-a2",
            source_id="source-a2",
            completed_at=NOW + timedelta(minutes=6),
        )

        # A later empty success and an even later failure do not erase the last
        # non-empty successful snapshot for this search/company/source partition.
        _seed_scan_source(session, "owner-a", "search-a", "scan-a3", "source-a3")
        empty_source = session.get(OpportunityScanSource, "source-a3")
        assert empty_source is not None
        empty_source.observed_count = 0
        empty_source.returned_count = 0
        _finish_scan_source(
            session,
            scan_id="scan-a3",
            source_id="source-a3",
            completed_at=NOW + timedelta(minutes=7),
        )
        _seed_scan_source(session, "owner-a", "search-a", "scan-a4", "source-a4")
        failed_source = session.get(OpportunityScanSource, "source-a4")
        assert failed_source is not None
        failed_source.observed_count = 0
        failed_source.returned_count = 0
        _finish_scan_source(
            session,
            scan_id="scan-a4",
            source_id="source-a4",
            completed_at=NOW + timedelta(minutes=8),
            succeeded=False,
        )

        dismissed_row = session.get(OwnerOpportunity, dismissed.opportunity_id)
        assert dismissed_row is not None
        decide_owner_opportunity(
            session,
            owner_id="owner-a",
            opportunity_id=dismissed.opportunity_id,
            request=OpportunityDecisionRequest(
                action="dismiss",
                dismiss_reason="not_relevant",
            ),
            expected_version=dismissed_row.version,
            idempotency_key="dismiss-historical-role",
            keyring=keyring,
            now=NOW + timedelta(minutes=9),
        )
        closed_posting = session.get(JobPosting, closed.posting_id)
        assert closed_posting is not None
        closed_posting.lifecycle_state = "closed"
        closed_posting.closure_reason = "explicit"
        closed_posting.closed_at = NOW + timedelta(minutes=9)
        closed_posting.version += 1
        session.flush()

        current = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(),
            keyring=keyring,
            now=NOW + timedelta(minutes=10),
        )
        historical = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(scan_id="scan-a1", view="all"),
            keyring=keyring,
            now=NOW + timedelta(minutes=10),
        )

        assert [item.id for item in current.items] == [shared.opportunity_id]
        assert {item.id for item in historical.items} == {
            old_only.opportunity_id,
            shared.opportunity_id,
            dismissed.opportunity_id,
        }
        assert closed.opportunity_id not in {item.id for item in historical.items}

        old_row = session.get(OwnerOpportunity, old_only.opportunity_id)
        assert old_row is not None
        decide_owner_opportunity(
            session,
            owner_id="owner-a",
            opportunity_id=old_only.opportunity_id,
            request=OpportunityDecisionRequest(action="watch"),
            expected_version=old_row.version,
            idempotency_key="watch-historical-role",
            keyring=keyring,
            now=NOW + timedelta(minutes=11),
        )
        watching = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(view="watching"),
            keyring=keyring,
            now=NOW + timedelta(minutes=12),
        )
        dismissed_view = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(view="dismissed"),
            keyring=keyring,
            now=NOW + timedelta(minutes=12),
        )
        refreshed_current = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(),
            keyring=keyring,
            now=NOW + timedelta(minutes=12),
        )

        assert [item.id for item in watching.items] == [old_only.opportunity_id]
        assert [item.id for item in dismissed_view.items] == [dismissed.opportunity_id]
        assert refreshed_current.summary.needs_decision == 1
        assert refreshed_current.summary.watching == 1
        assert refreshed_current.summary.dismissed == 1

        # A healthy source can observe raw roles that the stricter central
        # country/title filters correctly reject. That post-filter empty result
        # advances the partition and removes old Inbox false positives, unlike
        # a transport-level empty response with zero observations.
        _seed_scan_source(session, "owner-a", "search-a", "scan-a5", "source-a5")
        filtered_source = session.get(OpportunityScanSource, "source-a5")
        assert filtered_source is not None
        filtered_source.observed_count = 3
        filtered_source.returned_count = 0
        _finish_scan_source(
            session,
            scan_id="scan-a5",
            source_id="source-a5",
            completed_at=NOW + timedelta(minutes=13),
        )
        filtered_current = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(),
            keyring=keyring,
            now=NOW + timedelta(minutes=14),
        )

        assert filtered_current.items == []
        assert filtered_current.summary.needs_decision == 0
        assert filtered_current.summary.watching == 1
        assert filtered_current.summary.dismissed == 1


def test_today_requires_a_snapshot_from_the_active_saved_search_version(
    radar: tuple[Database, DataKeyring],
) -> None:
    database, keyring = radar
    with database.session() as session:
        persisted = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(),
            first_party_url_verified=True,
            now=NOW,
        )
        _finish_scan_source(
            session,
            scan_id="scan-a1",
            source_id="source-a1",
            completed_at=NOW + timedelta(minutes=1),
        )
        before_edit = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(),
            keyring=keyring,
            now=NOW + timedelta(minutes=2),
        )
        assert [item.id for item in before_edit.items] == [persisted.opportunity_id]

        search = session.get(SavedSearch, "search-a")
        assert search is not None
        search.criteria = {**search.criteria, "role_keywords": ["platform"]}
        search.version += 1
        search.updated_at = NOW + timedelta(minutes=3)
        session.flush()

        after_edit = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(),
            keyring=keyring,
            now=NOW + timedelta(minutes=4),
        )

        assert after_edit.items == []
        assert after_edit.summary.needs_decision == 0


def test_today_currentness_predicate_compiles_for_postgresql_json() -> None:
    condition = opportunity_repository_module._current_inbox_snapshot_condition(
        owner_id="owner-a",
        snapshot_at=NOW,
    )
    statement = select(OwnerOpportunity.id).where(condition)
    compiled = str(statement.compile(dialect=postgresql.dialect()))

    assert "json_array_length" in compiled
    assert "warning_codes =" not in compiled


def test_today_round_robins_companies_and_paginates_without_loss(
    radar: tuple[Database, DataKeyring],
) -> None:
    database, keyring = radar
    with database.session() as session:
        _seed_scan_source(
            session,
            "owner-a",
            "search-a",
            "scan-amazon",
            "source-amazon",
            company_slug="amazon",
        )
        amazon_source = session.get(OpportunityScanSource, "source-amazon")
        assert amazon_source is not None
        amazon_source.observed_count = 25
        amazon_source.returned_count = 25
        expected_ids: set[str] = set()
        newest_amazon: tuple[str, datetime] | None = None
        for index in range(25):
            surfaced_at = NOW + timedelta(minutes=60 + index)
            persisted = persist_scan_source_role(
                session,
                owner_id="owner-a",
                scan_source_id="source-amazon",
                role=_role(
                    company="Amazon",
                    company_slug="amazon",
                    source_job_id=f"amazon-{index}",
                    url=f"https://jobs.amazon.example/{index}",
                    title=f"Software Development Engineer {index}",
                ),
                first_party_url_verified=True,
                now=surfaced_at,
            )
            expected_ids.add(persisted.opportunity_id)
            newest_amazon = (persisted.opportunity_id, surfaced_at)

        for company, minute in (("beta", 10), ("gamma", 8)):
            scan_id = f"scan-{company}"
            source_id = f"source-{company}"
            _seed_scan_source(
                session,
                "owner-a",
                "search-a",
                scan_id,
                source_id,
                company_slug=company,
            )
            company_source = session.get(OpportunityScanSource, source_id)
            assert company_source is not None
            company_source.observed_count = 2
            company_source.returned_count = 2
            for index in range(2):
                persisted = persist_scan_source_role(
                    session,
                    owner_id="owner-a",
                    scan_source_id=source_id,
                    role=_role(
                        company=company.title(),
                        company_slug=company,
                        source_job_id=f"{company}-{index}",
                        url=f"https://jobs.{company}.example/{index}",
                        title=f"Backend Engineer {index}",
                    ),
                    first_party_url_verified=True,
                    now=NOW + timedelta(minutes=minute - index),
                )
                expected_ids.add(persisted.opportunity_id)

        _seed_scan_source(
            session,
            "owner-b",
            "search-b",
            "scan-b-private",
            "source-b-private",
            company_slug="private-company",
        )
        foreign = persist_scan_source_role(
            session,
            owner_id="owner-b",
            scan_source_id="source-b-private",
            role=_role(
                company="Private Company",
                company_slug="private-company",
                source_job_id="private-role",
                url="https://jobs.private.example/private-role",
            ),
            first_party_url_verified=True,
            now=NOW + timedelta(minutes=100),
        )

        page_now = NOW + timedelta(hours=2)
        first = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(limit=7),
            keyring=keyring,
            now=page_now,
        )
        repeated_first = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(limit=7),
            keyring=keyring,
            now=page_now,
        )

        assert {item.posting.company_slug for item in first.items[:3]} == {
            "amazon",
            "beta",
            "gamma",
        }
        assert [item.id for item in repeated_first.items] == [
            item.id for item in first.items
        ]
        assert repeated_first.next_cursor == first.next_cursor
        assert foreign.opportunity_id not in {item.id for item in first.items}

        # A role arriving after page one belongs to the next fresh inbox load,
        # not in the cursor's stable snapshot.
        _seed_scan_source(
            session,
            "owner-a",
            "search-a",
            "scan-after-page",
            "source-after-page",
            company_slug="amazon",
        )
        later = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-after-page",
            role=_role(
                company="Amazon",
                company_slug="amazon",
                source_job_id="amazon-later",
                url="https://jobs.amazon.example/later",
            ),
            first_party_url_verified=True,
            now=NOW + timedelta(hours=3),
        )

        seen = [item.id for item in first.items]
        cursor = first.next_cursor
        page_count = 1
        while cursor is not None:
            page = list_today_opportunities(
                session,
                owner_id="owner-a",
                query=TodayQuery(limit=7, cursor=cursor),
                keyring=keyring,
                now=NOW + timedelta(hours=4),
            )
            seen.extend(item.id for item in page.items)
            cursor = page.next_cursor
            page_count += 1
            assert page_count <= 6

        assert len(seen) == len(expected_ids) == 29
        assert len(seen) == len(set(seen))
        assert set(seen) == expected_ids
        assert later.opportunity_id not in seen

        # Pre-deploy two-field cursors remain valid and keep the former recency
        # order rather than changing sort semantics halfway through traversal.
        assert newest_amazon is not None
        legacy_payload = json.dumps(
            [newest_amazon[1].isoformat(), newest_amazon[0]],
            separators=(",", ":"),
        ).encode("utf-8")
        legacy_cursor = (
            base64.urlsafe_b64encode(legacy_payload).decode("ascii").rstrip("=")
        )
        legacy_page = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(limit=3, cursor=legacy_cursor),
            keyring=keyring,
            now=NOW + timedelta(hours=4),
        )
        assert [item.posting.company_slug for item in legacy_page.items] == [
            "amazon",
            "amazon",
            "amazon",
        ]


def test_today_recommended_ranks_full_result_set_before_pagination(
    radar: tuple[Database, DataKeyring],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, keyring = radar
    with database.session() as session:
        _seed_candidate_profile(
            session,
            owner_id="owner-a",
            profile_id="profile-a",
            keyring=keyring,
        )
        _seed_approved_evidence(
            session,
            owner_id="owner-a",
            evidence_id="evidence-a",
            keyring=keyring,
        )
        expected = _seed_categorical_rank_roles(session)
        monkeypatch.setattr(
            opportunity_repository_module,
            "assess_opportunity",
            _categorical_rank_assessment,
        )

        first = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(limit=2),
            keyring=keyring,
            now=NOW + timedelta(hours=2),
        )

        assert [item.match.fit_band.value for item in first.items] == [
            "strong",
            "strong",
        ]
        assert {item.posting.company_slug for item in first.items} == {"alpha", "beta"}
        assert first.next_cursor is not None

        seen = list(first.items)
        cursor = first.next_cursor
        while cursor is not None:
            page = list_today_opportunities(
                session,
                owner_id="owner-a",
                query=TodayQuery(limit=2, cursor=cursor),
                keyring=keyring,
                now=NOW + timedelta(hours=3),
            )
            seen.extend(page.items)
            cursor = page.next_cursor

        assert [item.match.fit_band.value for item in seen] == [
            "strong",
            "strong",
            "strong",
            "promising",
            "stretch",
            "low",
        ]
        assert {item.id for item in seen} == set(expected.values())
        assert len({item.id for item in seen}) == len(seen)

        newest = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(sort="newest", limit=2),
            keyring=keyring,
            now=NOW + timedelta(hours=2),
        )
        assert newest.items[0].id == expected["low-epsilon"]
        assert newest.items[0].match.fit_band.value == "low"


@pytest.mark.parametrize("changed_input", ["profile", "evidence", "posting"])
def test_today_recommended_cursor_fails_closed_when_ranking_inputs_change(
    radar: tuple[Database, DataKeyring],
    monkeypatch: pytest.MonkeyPatch,
    changed_input: str,
) -> None:
    database, keyring = radar
    with database.session() as session:
        _seed_candidate_profile(
            session,
            owner_id="owner-a",
            profile_id="profile-a",
            keyring=keyring,
        )
        _seed_approved_evidence(
            session,
            owner_id="owner-a",
            evidence_id="evidence-a",
            keyring=keyring,
        )
        _seed_categorical_rank_roles(session)
        monkeypatch.setattr(
            opportunity_repository_module,
            "assess_opportunity",
            _categorical_rank_assessment,
        )
        first = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(limit=2),
            keyring=keyring,
            now=NOW + timedelta(hours=2),
        )
        assert first.next_cursor is not None

        if changed_input == "profile":
            profile = session.get(CandidateProfile, "profile-a")
            assert profile is not None
            profile.version += 1
        elif changed_input == "evidence":
            evidence = session.get(AchievementEvidence, "evidence-a")
            assert evidence is not None
            evidence.version += 1
        else:
            _seed_scan_source(
                session,
                "owner-a",
                "search-a",
                "scan-rank-update",
                "source-rank-update",
                company_slug="alpha",
            )
            persist_scan_source_role(
                session,
                owner_id="owner-a",
                scan_source_id="source-rank-update",
                role=_role(
                    company="Alpha",
                    company_slug="alpha",
                    title="Strong Alpha Newer",
                    source_job_id="strong-alpha-new",
                    url="https://jobs.alpha.example/strong-alpha-new",
                    raw_description="Build changed Python backend systems.",
                ),
                first_party_url_verified=True,
                now=NOW + timedelta(hours=2, minutes=1),
            )
        session.flush()

        with pytest.raises(ValueError, match="cursor is invalid"):
            list_today_opportunities(
                session,
                owner_id="owner-a",
                query=TodayQuery(limit=2, cursor=first.next_cursor),
                keyring=keyring,
                now=NOW + timedelta(hours=3),
            )


def test_today_recommended_bulk_reads_75_candidates_without_n_plus_one(
    radar: tuple[Database, DataKeyring],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, keyring = radar
    with database.session() as session:
        source = session.get(OpportunityScanSource, "source-a1")
        assert source is not None
        source.observed_count = 75
        source.returned_count = 75
        for index in range(75):
            persist_scan_source_role(
                session,
                owner_id="owner-a",
                scan_source_id="source-a1",
                role=_role(
                    source_job_id=f"perf-{index}",
                    url=f"https://jobs.acme.example/perf/{index}",
                    title=f"Strong Performance Role {index}",
                ),
                first_party_url_verified=True,
                now=NOW + timedelta(minutes=index),
            )
        _finish_scan_source(
            session,
            scan_id="scan-a1",
            source_id="source-a1",
            completed_at=NOW + timedelta(minutes=75),
        )

        assessment_calls = 0

        def counted_assessment(**kwargs):
            nonlocal assessment_calls
            assessment_calls += 1
            return _categorical_rank_assessment(**kwargs)

        monkeypatch.setattr(
            opportunity_repository_module,
            "assess_opportunity",
            counted_assessment,
        )
        statements = 0

        def count_statement(*_args, **_kwargs) -> None:
            nonlocal statements
            statements += 1

        event.listen(database.engine, "before_cursor_execute", count_statement)
        try:
            page = list_today_opportunities(
                session,
                owner_id="owner-a",
                query=TodayQuery(limit=10),
                keyring=keyring,
                now=NOW + timedelta(hours=2),
            )
        finally:
            event.remove(database.engine, "before_cursor_execute", count_statement)

        assert len(page.items) == 10
        assert page.next_cursor is not None
        assert assessment_calls == 75
        # Ranking and the rendered page both use bulk reads. Distinct saved-search
        # tracks/resumes add only personal-scale cache misses, never one query per
        # role in the 75-item corpus.
        assert statements <= 20


def test_late_lock_with_older_scan_time_keeps_posting_history_monotonic(
    radar: tuple[Database, DataKeyring],
) -> None:
    database, keyring = radar
    with database.session() as session:
        first = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(),
            first_party_url_verified=True,
            now=NOW + timedelta(hours=4),
        )
        _seed_scan_source(session, "owner-a", "search-a", "scan-a2", "source-a2")
        second = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a2",
            role=_role(
                title="Staff Backend Engineer",
                raw_description="A newer commit from an older captured scan time.",
            ),
            first_party_url_verified=True,
            now=NOW + timedelta(hours=1),
        )
        assert second.posting_id == first.posting_id

        posting = session.get(JobPosting, first.posting_id)
        versions = list(
            session.scalars(
                select(JobPostingVersion)
                .where(JobPostingVersion.job_posting_id == first.posting_id)
                .order_by(JobPostingVersion.version_number)
            )
        )
        assert posting is not None
        assert [version.version_number for version in versions] == [1, 2]
        assert versions[1].observed_at >= versions[0].observed_at
        assert posting.first_confirmed_at <= posting.last_changed_at
        assert posting.last_changed_at <= posting.last_confirmed_at

        today = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(),
            keyring=keyring,
            now=NOW + timedelta(hours=5),
        )
        assert today.items[0].posting.title == "Staff Backend Engineer"


def test_two_searches_and_two_owners_keep_correct_dedupe_boundaries(
    radar: tuple[Database, DataKeyring],
) -> None:
    database, _keyring = radar
    with database.session() as session:
        first = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(),
            first_party_url_verified=True,
            now=NOW,
        )
        base = session.get(SavedSearch, "search-a")
        assert base is not None
        session.add(
            SavedSearch(
                id="search-a2",
                owner_id="owner-a",
                career_track_id=base.career_track_id,
                resume_version_id=base.resume_version_id,
                name="Second search",
                criteria_schema_version=1,
                criteria=dict(base.criteria),
                pack=base.pack,
                use_self_rag=False,
                cadence="manual",
                schedule={"local_time": None, "days_of_week": []},
                timezone="UTC",
                active=True,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        _seed_scan_source(session, "owner-a", "search-a2", "scan-a4", "source-a4")
        second = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a4",
            role=_role(),
            first_party_url_verified=True,
            now=NOW + timedelta(hours=1),
        )
        assert second.opportunity_id == first.opportunity_id
        assert session.scalar(select(func.count(SavedSearchMatch.id))) == 2
        assert session.scalar(select(func.count(OwnerOpportunity.id))) == 1

        with pytest.raises(ValueError):
            persist_scan_source_role(
                session,
                owner_id="owner-b",
                scan_source_id="source-a4",
                role=_role(),
                first_party_url_verified=True,
                now=NOW,
            )


def test_failed_partial_refresh_never_hides_or_closes_last_good_posting(
    radar: tuple[Database, DataKeyring],
) -> None:
    database, keyring = radar
    with database.session() as session:
        persisted = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(),
            first_party_url_verified=True,
            now=NOW,
        )
        _seed_scan_source(session, "owner-a", "search-a", "scan-failed", "source-failed")
        scan = session.get(OpportunityScan, "scan-failed")
        source = session.get(OpportunityScanSource, "source-failed")
        assert scan is not None and source is not None
        source.status = "succeeded"
        source.completeness = "partial"
        source.error_code = None
        source.warning_codes = ["source_incomplete"]
        source.completed_at = NOW + timedelta(hours=1)
        scan.status = "partial"
        scan.stage = "complete"
        scan.finalized_at = NOW + timedelta(hours=1)

        today = list_today_opportunities(
            session,
            owner_id="owner-a",
            query=TodayQuery(),
            keyring=keyring,
            now=NOW + timedelta(hours=2),
        )
        posting = session.get(JobPosting, persisted.posting_id)
        assert posting is not None and posting.lifecycle_state == "open"
        assert [item.id for item in today.items] == [persisted.opportunity_id]
        assert today.scan_health.state.value == "degraded"
        assert today.scan_health.last_success_at == NOW + timedelta(hours=1)


def test_version_fenced_decisions_are_encrypted_append_only_and_restore_latest(
    radar: tuple[Database, DataKeyring],
) -> None:
    database, keyring = radar
    with database.session() as session:
        persisted = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id="source-a1",
            role=_role(),
            first_party_url_verified=True,
            now=NOW,
        )
        watched = decide_owner_opportunity(
            session,
            owner_id="owner-a",
            opportunity_id=persisted.opportunity_id,
            request=OpportunityDecisionRequest(action="watch", note="PRIVATE WATCH NOTE"),
            expected_version=1,
            idempotency_key="watch-1",
            keyring=keyring,
            now=NOW + timedelta(minutes=1),
        )
        assert watched.state.value == "watch"
        watched_row = session.get(OpportunityDecisionEvent, watched.event.id)
        assert watched_row is not None
        legacy_payload = {
            "action": "watch",
            "dismiss_reason": None,
            "note": "PRIVATE WATCH NOTE",
            "restore_decision_event_id": None,
        }
        legacy_request_hash = hashlib.sha256(
            json.dumps(
                legacy_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        assert watched_row.request_hash == legacy_request_hash
        replay = decide_owner_opportunity(
            session,
            owner_id="owner-a",
            opportunity_id=persisted.opportunity_id,
            request=OpportunityDecisionRequest(action="watch", note="PRIVATE WATCH NOTE"),
            expected_version=1,
            idempotency_key="watch-1",
            keyring=keyring,
            now=NOW + timedelta(minutes=2),
        )
        assert replay.event.id == watched.event.id

        dismissed = decide_owner_opportunity(
            session,
            owner_id="owner-a",
            opportunity_id=persisted.opportunity_id,
            request=OpportunityDecisionRequest(
                action="dismiss",
                dismiss_reason="not_a_better_move",
                note="PRIVATE DISMISS NOTE",
            ),
            expected_version=watched.opportunity_version,
            idempotency_key="dismiss-1",
            keyring=keyring,
            now=NOW + timedelta(minutes=3),
        )
        with pytest.raises(ResourceConflict):
            decide_owner_opportunity(
                session,
                owner_id="owner-a",
                opportunity_id=persisted.opportunity_id,
                request=OpportunityDecisionRequest(
                    action="restore_to_inbox",
                    restore_decision_event_id=watched.event.id,
                ),
                expected_version=dismissed.opportunity_version,
                idempotency_key="bad-restore",
                keyring=keyring,
            )
        restored = decide_owner_opportunity(
            session,
            owner_id="owner-a",
            opportunity_id=persisted.opportunity_id,
            request=OpportunityDecisionRequest(
                action="restore_to_inbox",
                restore_decision_event_id=dismissed.event.id,
            ),
            expected_version=dismissed.opportunity_version,
            idempotency_key="restore-1",
            keyring=keyring,
            now=NOW + timedelta(minutes=4),
        )
        assert restored.state.value == "inbox"
        assert session.scalar(select(func.count(OpportunityDecisionEvent.id))) == 3
        rows = list(session.scalars(select(OpportunityDecisionEvent)))
        assert all("PRIVATE" not in (row.encrypted_note or "") for row in rows)

        with pytest.raises(VersionConflict):
            decide_owner_opportunity(
                session,
                owner_id="owner-a",
                opportunity_id=persisted.opportunity_id,
                request=OpportunityDecisionRequest(action="watch"),
                expected_version=1,
                idempotency_key="stale-watch",
                keyring=keyring,
            )
        with pytest.raises(OpportunityNotFound):
            decide_owner_opportunity(
                session,
                owner_id="owner-b",
                opportunity_id=persisted.opportunity_id,
                request=OpportunityDecisionRequest(action="watch"),
                expected_version=restored.opportunity_version,
                idempotency_key="foreign-watch",
                keyring=keyring,
            )
