"""Focused truthfulness gates for the in-app daily digest."""

from __future__ import annotations

from types import SimpleNamespace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet

from job_hunt_agent.daily_digest_repository import (
    _digest_headline,
    _worth_your_time,
    build_daily_digest,
)
from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    Base,
    AchievementEvidence,
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
from job_hunt_agent.opportunity_schemas import (
    MatchAssessmentState,
    OpportunityDecisionState,
    OpportunityEligibility,
    OpportunityFitBand,
    PostingState,
)
from job_hunt_agent.private_payloads import encrypt_private_payload
from job_hunt_agent.security import DataKeyring


def _item(*, decision: OpportunityDecisionState, posting: PostingState = PostingState.open):
    return SimpleNamespace(
        state=decision,
        posting=SimpleNamespace(state=posting),
        match=SimpleNamespace(
            state=MatchAssessmentState.assessed,
            eligibility=OpportunityEligibility.eligible,
            fit_band=OpportunityFitBand.strong,
        ),
    )


def test_dismissed_or_closed_roles_are_not_worth_your_time() -> None:
    assert _worth_your_time(_item(decision=OpportunityDecisionState.inbox)) is True
    assert _worth_your_time(_item(decision=OpportunityDecisionState.watch)) is True
    assert _worth_your_time(_item(decision=OpportunityDecisionState.pursued)) is True
    assert _worth_your_time(_item(decision=OpportunityDecisionState.dismiss)) is False
    assert _worth_your_time(
        _item(decision=OpportunityDecisionState.inbox, posting=PostingState.closed)
    ) is False


def test_digest_headline_is_explicit_when_assessment_is_bounded() -> None:
    assert _digest_headline(
        new_count=6,
        worth_count=2,
        assessment_complete=True,
    ) == "6 new roles, 2 worth your time"
    assert _digest_headline(
        new_count=2_001,
        worth_count=20,
        assessment_complete=False,
    ) == "2001 new roles, at least 20 worth your time"


def test_digest_real_rows_are_owner_local_and_exclude_dismissed(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'digest.db'}")
    Base.metadata.create_all(database.engine)
    keyring = DataKeyring([("test-v1", Fernet.generate_key().decode("ascii"))])
    current = datetime(2026, 8, 5, 6, 0, tzinfo=timezone.utc)
    india_day_start = datetime(2026, 8, 4, 18, 30, tzinfo=timezone.utc)
    with database.session() as session:
        _seed_owner_search(
            session,
            owner_id="owner-a",
            timezone_name="Asia/Kolkata",
            keyring=keyring,
        )
        _seed_owner_search(session, owner_id="owner-b", timezone_name="UTC", keyring=keyring)
        session.flush()
        _seed_role_graph(
            session,
            owner_id="owner-a",
            suffix="inbox",
            created_at=india_day_start + timedelta(minutes=30),
            decision="inbox",
        )
        _seed_role_graph(
            session,
            owner_id="owner-a",
            suffix="dismiss",
            created_at=india_day_start + timedelta(hours=2),
            decision="dismiss",
        )
        _seed_role_graph(
            session,
            owner_id="owner-a",
            suffix="before",
            created_at=india_day_start - timedelta(seconds=1),
            decision="inbox",
        )
        _seed_role_graph(
            session,
            owner_id="owner-b",
            suffix="foreign",
            created_at=india_day_start + timedelta(hours=1),
            decision="inbox",
        )

    with database.session() as session:
        digest = build_daily_digest(
            session,
            owner_id="owner-a",
            owner_timezone="Asia/Kolkata",
            owner_local_date=date(2026, 8, 5),
            keyring=keyring,
            now=current,
        )

    assert digest.period_started_at == india_day_start
    assert digest.new_opportunities == 2
    assert digest.evaluated_opportunities == 2
    assert digest.worth_your_time == 1
    assert [item.opportunity_id for item in digest.highlights] == ["op-inbox"]
    assert digest.headline == "2 new roles, 1 worth your time"
    assert "op-dismiss" not in {item.opportunity_id for item in digest.highlights}
    assert "op-before" not in {item.opportunity_id for item in digest.highlights}
    assert "op-foreign" not in {item.opportunity_id for item in digest.highlights}
    database.dispose()


def _seed_owner_search(
    session,
    *,
    owner_id: str,
    timezone_name: str,
    keyring: DataKeyring,
) -> None:
    track_id = f"track-{owner_id}"
    resume_id = f"resume-{owner_id}"
    search_id = f"search-{owner_id}"
    session.add(Owner(id=owner_id, display_name=owner_id, timezone=timezone_name))
    profile_id = f"profile-{owner_id}"
    profile_envelope = encrypt_private_payload(
        keyring,
        record_kind="candidate_profile",
        owner_id=owner_id,
        record_id=profile_id,
        payload={
            "current_title": "Backend Engineer",
            "current_location": "Gurugram, India",
            "years_of_experience": 5,
            "skills": ["Python", "AWS", "PostgreSQL", "Docker"],
            "work_authorizations": [{"country_code": "IN", "status": "citizen"}],
            "work_modes": ["remote", "hybrid"],
            "employment_types": ["full_time"],
        },
    )
    session.add(
        CandidateProfile(
            id=profile_id,
            owner_id=owner_id,
            encrypted_payload=profile_envelope.ciphertext,
            encryption_key_id=profile_envelope.key_id,
            onboarding_state="complete",
            version=1,
        )
    )
    evidence_id = f"evidence-{owner_id}"
    evidence_envelope = encrypt_private_payload(
        keyring,
        record_kind="achievement_evidence",
        owner_id=owner_id,
        record_id=evidence_id,
        payload={
            "statement": "Owned reliable Python and AWS backend systems in production.",
            "source_excerpt": None,
        },
    )
    session.add(
        AchievementEvidence(
            id=evidence_id,
            owner_id=owner_id,
            source_resume_version_id=None,
            encrypted_payload=evidence_envelope.ciphertext,
            encryption_key_id=evidence_envelope.key_id,
            skills=["Python", "AWS", "backend", "distributed systems"],
            origin="owner_entered",
            approval_state="approved",
            approved_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            version=1,
        )
    )
    session.add(
        CareerTrack(
            id=track_id,
            owner_id=owner_id,
            name="Backend roles",
            role_families=["Backend Engineer"],
            seniority_levels=["senior"],
            target_locations=["Remote India"],
            priorities={},
            active=True,
            version=1,
        )
    )
    envelope = encrypt_private_payload(
        keyring,
        record_kind="resume_version",
        owner_id=owner_id,
        record_id=resume_id,
        payload={
            "content": (
                "Senior backend engineer with Python, AWS, REST APIs, PostgreSQL, "
                "Docker, distributed systems, and production reliability ownership."
            )
        },
    )
    session.add(
        ResumeVersion(
            id=resume_id,
            owner_id=owner_id,
            label="Base resume",
            encrypted_content=envelope.ciphertext,
            encryption_key_id=envelope.key_id,
            content_hash=("a" if owner_id == "owner-a" else "b") * 64,
            source="pasted",
            is_base=True,
            version=1,
        )
    )
    session.add(
        SavedSearch(
            id=search_id,
            owner_id=owner_id,
            career_track_id=track_id,
            resume_version_id=resume_id,
            name="Senior backend",
            criteria_schema_version=1,
            criteria={
                "role_keywords": ["backend", "platform"],
                "seniority": "senior",
                "location": ["Remote India"],
                "comp_min_lpa": None,
                "comp_max_lpa": None,
                "employment_types": ["full_time"],
                "max_age_days": 45,
                "country": "in",
            },
            pack="backend_india",
            use_self_rag=False,
            cadence="manual",
            schedule={"local_time": None, "days_of_week": []},
            timezone=timezone_name,
            active=True,
            next_scan_at=None,
            version=1,
        )
    )


def _seed_role_graph(
    session,
    *,
    owner_id: str,
    suffix: str,
    created_at: datetime,
    decision: str,
) -> None:
    scan_id = f"scan-{suffix}"
    source_id = f"source-{suffix}"
    posting_id = f"post-{suffix}"
    version_id = f"ver-{suffix}"
    alias_id = f"alias-{suffix}"
    opportunity_id = f"op-{suffix}"
    session.add(
        OpportunityScan(
            id=scan_id,
            owner_id=owner_id,
            saved_search_id=f"search-{owner_id}",
            saved_search_version=1,
            criteria_schema_version=1,
            criteria_snapshot={
                "role_keywords": ["backend"],
                "seniority": "senior",
                "location": ["Remote India"],
                "employment_types": ["full_time"],
                "max_age_days": 45,
                "country": "in",
            },
            pack_snapshot="backend_india",
            trigger="manual",
            scheduled_for=created_at,
            dedupe_key=f"manual-{suffix}",
            request_hash=(suffix[0] if suffix else "c") * 64,
            status="queued",
            stage="queued",
            source_count=1,
            terminal_source_count=0,
            successful_source_count=0,
            failed_source_count=0,
            observed_count=1,
            new_posting_count=1,
            changed_posting_count=0,
            new_opportunity_count=1,
            version=1,
            created_at=created_at,
            updated_at=created_at,
        )
    )
    session.add(
        OpportunityScanSource(
            id=source_id,
            owner_id=owner_id,
            opportunity_scan_id=scan_id,
            company_slug=f"company-{suffix}",
            source="greenhouse",
            status="pending",
            fetch_scope="criteria_filtered",
            completeness="unknown",
            observed_count=1,
            returned_count=1,
            persisted_count=1,
            warning_codes=[],
            used_fallback=False,
            cache_hit=False,
            version=1,
            created_at=created_at,
            updated_at=created_at,
        )
    )
    session.add(
        JobPosting(
            id=posting_id,
            owner_id=owner_id,
            identity_kind="native",
            identity_key=f"greenhouse:{owner_id}:{suffix}",
            identity_key_hash=("1" if owner_id == "owner-a" else "2") * 60
            + suffix[:4].ljust(4, "0"),
            source="greenhouse",
            company_slug=f"company-{suffix}",
            source_job_id=f"job-{suffix}",
            canonical_url=f"https://company-{suffix}.example/jobs/{suffix}",
            lifecycle_state="open",
            consecutive_complete_omissions=0,
            first_confirmed_at=created_at,
            last_confirmed_at=created_at,
            version=1,
            created_at=created_at,
            updated_at=created_at,
        )
    )
    session.add(
        JobPostingVersion(
            id=version_id,
            owner_id=owner_id,
            job_posting_id=posting_id,
            version_number=1,
            content_hash=("3" if owner_id == "owner-a" else "4") * 60 + suffix[:4].ljust(4, "0"),
            source="greenhouse",
            source_job_id=f"job-{suffix}",
            company_name=f"Company {suffix.title()}",
            title="Senior Backend Engineer",
            canonical_url=f"https://company-{suffix}.example/jobs/{suffix}",
            apply_urls=[f"https://company-{suffix}.example/jobs/{suffix}"],
            location="Remote India",
            summary="Build reliable backend services.",
            description=(
                "Required qualifications: 5+ years building production backend "
                "services with Python and AWS. You must design and operate REST "
                "APIs and reliable distributed systems. Strong PostgreSQL and "
                "Docker experience is required. You will own service reliability, "
                "review designs, debug production incidents, improve observability, "
                "and collaborate with product and infrastructure engineers. The "
                "team values tested code, clear operational ownership, and pragmatic "
                "delivery of secure customer-facing systems."
            ),
            employment_type="full_time",
            posted_at_text=created_at.date().isoformat(),
            source_facts={},
            source_confidence=1.0,
            observed_at=created_at,
            created_at=created_at,
        )
    )
    session.add(
        JobPostingAlias(
            id=alias_id,
            owner_id=owner_id,
            job_posting_id=posting_id,
            alias_kind="native",
            alias_key=f"greenhouse:{owner_id}:{suffix}",
            alias_key_hash=("5" if owner_id == "owner-a" else "6") * 60 + suffix[:4].ljust(4, "0"),
            source="greenhouse",
            company_slug=f"company-{suffix}",
            source_job_id=f"job-{suffix}",
            normalized_url=None,
            first_seen_at=created_at,
            last_seen_at=created_at,
            created_at=created_at,
        )
    )
    session.flush()
    session.add(
        JobObservation(
            id=f"obs-{suffix}",
            owner_id=owner_id,
            opportunity_scan_id=scan_id,
            opportunity_scan_source_id=source_id,
            job_posting_id=posting_id,
            job_posting_version_id=version_id,
            job_posting_alias_id=alias_id,
            first_party_url_verified=True,
            observed_at=created_at,
            created_at=created_at,
        )
    )
    session.add(
        SavedSearchMatch(
            id=f"match-{suffix}",
            owner_id=owner_id,
            saved_search_id=f"search-{owner_id}",
            job_posting_id=posting_id,
            first_scan_id=scan_id,
            last_scan_id=scan_id,
            last_posting_version_id=version_id,
            match_count=1,
            first_matched_at=created_at,
            last_matched_at=created_at,
            created_at=created_at,
            updated_at=created_at,
        )
    )
    session.add(
        OwnerOpportunity(
            id=opportunity_id,
            owner_id=owner_id,
            job_posting_id=posting_id,
            decision=decision,
            decision_reason_code="not_relevant" if decision == "dismiss" else None,
            decision_updated_at=created_at if decision == "dismiss" else None,
            first_surfaced_at=created_at,
            last_surfaced_at=created_at,
            version=2 if decision == "dismiss" else 1,
            created_at=created_at,
            updated_at=created_at,
        )
    )
    session.flush()
    if decision == "dismiss":
        session.add(
            OpportunityDecisionEvent(
                id=f"event-{suffix}",
                owner_id=owner_id,
                owner_opportunity_id=opportunity_id,
                job_posting_id=posting_id,
                posting_version_id=version_id,
                previous_decision="inbox",
                new_decision="dismiss",
                reason_code="not_relevant",
                idempotency_key_hash="7" * 64,
                request_hash="8" * 64,
                occurred_at=created_at,
                created_at=created_at,
            )
        )
