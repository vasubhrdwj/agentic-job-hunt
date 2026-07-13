"""Focused invariants for the durable opportunity-radar model graph."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    Base,
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


NOW = datetime(2026, 7, 13, 8, 30, tzinfo=timezone.utc)


@pytest.fixture
def radar_db(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'opportunity.db'}")
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        _seed_owner_searches(session)
    try:
        yield database
    finally:
        database.dispose()


def _seed_owner_searches(session: Session) -> None:
    for owner_id in ("owner-a", "owner-b"):
        session.add(
            Owner(id=owner_id, display_name=owner_id, timezone="Asia/Kolkata")
        )
        session.add(
            CareerTrack(
                id=f"track-{owner_id}",
                owner_id=owner_id,
                name="Backend",
                role_families=["Backend Engineer"],
                seniority_levels=["senior"],
                target_locations=["Remote India"],
                priorities={},
                active=True,
                version=1,
            )
        )
        session.add(
            ResumeVersion(
                id=f"resume-{owner_id}",
                owner_id=owner_id,
                label="Base resume",
                encrypted_content="encrypted",
                encryption_key_id="key-v1",
                content_hash=("a" if owner_id == "owner-a" else "b") * 64,
                source="pasted",
                is_base=True,
                version=1,
            )
        )
        session.add(
            SavedSearch(
                id=f"search-{owner_id}",
                owner_id=owner_id,
                career_track_id=f"track-{owner_id}",
                resume_version_id=f"resume-{owner_id}",
                name="Daily backend roles",
                criteria_schema_version=1,
                criteria=_criteria(),
                pack="backend_india",
                use_self_rag=True,
                cadence="manual",
                schedule={"local_time": None, "days_of_week": []},
                timezone="Asia/Kolkata",
                active=False,
                next_scan_at=None,
                version=1,
            )
        )
    session.add(
        SavedSearch(
            id="search-owner-a-second",
            owner_id="owner-a",
            career_track_id="track-owner-a",
            resume_version_id="resume-owner-a",
            name="Platform roles",
            criteria_schema_version=1,
            criteria={**_criteria(), "role_keywords": ["platform"]},
            pack="backend_india",
            use_self_rag=True,
            cadence="manual",
            schedule={"local_time": None, "days_of_week": []},
            timezone="Asia/Kolkata",
            active=False,
            next_scan_at=None,
            version=1,
        )
    )


def _criteria() -> dict[str, object]:
    return {
        "role_keywords": ["backend"],
        "seniority": "senior",
        "location": ["Remote India"],
        "comp_min_lpa": None,
        "comp_max_lpa": None,
        "employment_types": ["full_time"],
        "max_age_days": 45,
        "country": "in",
    }


def _scan(search_id: str, scan_id: str) -> OpportunityScan:
    return OpportunityScan(
        id=scan_id,
        owner_id="owner-a",
        saved_search_id=search_id,
        saved_search_version=1,
        criteria_schema_version=1,
        criteria_snapshot=_criteria(),
        pack_snapshot="backend_india",
        trigger="manual",
        scheduled_for=NOW,
        dedupe_key=f"manual:{scan_id}",
        idempotency_key_hash=("c" if scan_id.endswith("one") else "d") * 64,
        request_hash="e" * 64,
        status="running",
        stage="fetching",
        source_count=1,
        terminal_source_count=1,
        successful_source_count=1,
        failed_source_count=0,
        observed_count=1,
        started_at=NOW,
        version=1,
    )


def _posting() -> JobPosting:
    return JobPosting(
        id="posting-a",
        owner_id="owner-a",
        identity_kind="native",
        identity_key="source:greenhouse:acme:123",
        identity_key_hash="f" * 64,
        source="greenhouse",
        company_slug="acme",
        source_job_id="123",
        canonical_url="https://boards.greenhouse.io/acme/jobs/123",
        lifecycle_state="open",
        consecutive_complete_omissions=0,
        first_confirmed_at=NOW,
        last_confirmed_at=NOW,
        version=1,
    )


def _posting_version(
    *, version_id: str = "posting-version-a", version_number: int = 1, content: str = "1"
) -> JobPostingVersion:
    return JobPostingVersion(
        id=version_id,
        owner_id="owner-a",
        job_posting_id="posting-a",
        version_number=version_number,
        content_hash=content * 64,
        source="greenhouse",
        source_job_id="123",
        company_name="Acme",
        title="Senior Backend Engineer",
        canonical_url="https://boards.greenhouse.io/acme/jobs/123",
        apply_urls=["https://boards.greenhouse.io/acme/jobs/123"],
        location="Remote India",
        summary="Build reliable backend systems.",
        description=None,
        employment_type="unknown",
        posted_at_text=None,
        source_updated_at_text=None,
        source_facts={"compensation": {"status": "unknown"}},
        source_confidence=1.0,
        observed_at=NOW,
    )


def _add_graph(session: Session) -> None:
    scans = [
        _scan("search-owner-a", "scan-one"),
        _scan("search-owner-a-second", "scan-two"),
    ]
    sources = [
        OpportunityScanSource(
            id=f"source-{index}",
            owner_id="owner-a",
            opportunity_scan_id=scan.id,
            company_slug="acme",
            source="greenhouse",
            status="succeeded",
            fetch_scope="criteria_filtered",
            completeness="partial",
            observed_count=1,
            returned_count=1,
            persisted_count=1,
            warning_codes=["criteria_filtered"],
            started_at=NOW,
            completed_at=NOW,
            version=1,
        )
        for index, scan in enumerate(scans, start=1)
    ]
    posting = _posting()
    alias = JobPostingAlias(
        id="alias-a",
        owner_id="owner-a",
        job_posting_id=posting.id,
        alias_kind="native",
        alias_key=posting.identity_key,
        alias_key_hash=posting.identity_key_hash,
        source="greenhouse",
        company_slug="acme",
        source_job_id="123",
        normalized_url=None,
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    version = _posting_version()
    observations = [
        JobObservation(
            id=f"observation-{index}",
            owner_id="owner-a",
            opportunity_scan_id=scan.id,
            opportunity_scan_source_id=source.id,
            job_posting_id=posting.id,
            job_posting_version_id=version.id,
            job_posting_alias_id=alias.id,
            first_party_url_verified=True,
            observed_at=NOW,
        )
        for index, (scan, source) in enumerate(zip(scans, sources, strict=True), start=1)
    ]
    matches = [
        SavedSearchMatch(
            id=f"match-{index}",
            owner_id="owner-a",
            saved_search_id=scan.saved_search_id,
            job_posting_id=posting.id,
            first_scan_id=scan.id,
            last_scan_id=scan.id,
            last_posting_version_id=version.id,
            match_count=1,
            first_matched_at=NOW,
            last_matched_at=NOW,
        )
        for index, scan in enumerate(scans, start=1)
    ]
    opportunity = OwnerOpportunity(
        id="opportunity-a",
        owner_id="owner-a",
        job_posting_id=posting.id,
        decision="inbox",
        first_surfaced_at=NOW,
        last_surfaced_at=NOW,
        version=1,
    )
    # The production repositories persist each dependency layer explicitly as
    # they resolve identities and versions. Mirror those flush boundaries here
    # rather than relying on ORM relationships that these storage-only models
    # deliberately do not define.
    session.add_all([*scans, posting])
    session.flush()
    session.add_all([*sources, alias, version, opportunity])
    session.flush()
    session.add_all([*observations, *matches])


def test_two_searches_share_one_owner_opportunity_and_keep_both_provenances(
    radar_db: Database,
) -> None:
    with radar_db.session() as session:
        _add_graph(session)

    with radar_db.session() as session:
        assert session.scalar(select(func.count(OwnerOpportunity.id))) == 1
        assert session.scalar(select(func.count(SavedSearchMatch.id))) == 2
        assert session.scalar(select(func.count(JobPostingVersion.id))) == 1
        version = session.get(JobPostingVersion, "posting-version-a")
        assert version is not None
        assert version.description is None
        assert version.employment_type == "unknown"
        assert version.source_facts["compensation"]["status"] == "unknown"

    with pytest.raises(IntegrityError):
        with radar_db.session() as session:
            session.add(
                OwnerOpportunity(
                    owner_id="owner-a",
                    job_posting_id="posting-a",
                    decision="inbox",
                    first_surfaced_at=NOW,
                    last_surfaced_at=NOW,
                    version=1,
                )
            )


def test_composite_foreign_keys_reject_cross_owner_graph_edges(
    radar_db: Database,
) -> None:
    with radar_db.session() as session:
        _add_graph(session)

    with pytest.raises(IntegrityError):
        with radar_db.session() as session:
            foreign_version = _posting_version(version_id="foreign-version", content="2")
            foreign_version.owner_id = "owner-b"
            session.add(foreign_version)

    with pytest.raises(IntegrityError):
        with radar_db.session() as session:
            session.add(
                OwnerOpportunity(
                    owner_id="owner-b",
                    job_posting_id="posting-a",
                    decision="inbox",
                    first_surfaced_at=NOW,
                    last_surfaced_at=NOW,
                    version=1,
                )
            )


def test_version_number_is_unique_and_historical_content_can_repeat(
    radar_db: Database,
) -> None:
    with radar_db.session() as session:
        session.add(_posting())
        session.add(_posting_version())

    with radar_db.session() as session:
        repeated = _posting_version(
            version_id="same-content", version_number=2, content="1"
        )
        session.add(repeated)

    with pytest.raises(IntegrityError):
        with radar_db.session() as session:
            session.add(
                _posting_version(
                    version_id="duplicate-number", version_number=2, content="2"
                )
            )

    with radar_db.session() as session:
        changed = _posting_version(
            version_id="posting-version-b", version_number=3, content="2"
        )
        changed.summary = "Lead a changed backend platform charter."
        session.add(changed)

    with radar_db.session() as session:
        latest = session.scalar(
            select(JobPostingVersion)
            .where(JobPostingVersion.job_posting_id == "posting-a")
            .order_by(JobPostingVersion.version_number.desc())
            .limit(1)
        )
        assert latest is not None and latest.id == "posting-version-b"


def test_incomplete_fetch_cannot_claim_complete_inventory(radar_db: Database) -> None:
    with radar_db.session() as session:
        session.add(_scan("search-owner-a", "scan-one"))

    with pytest.raises(IntegrityError):
        with radar_db.session() as session:
            session.add(
                OpportunityScanSource(
                    owner_id="owner-a",
                    opportunity_scan_id="scan-one",
                    company_slug="acme",
                    source="greenhouse",
                    status="succeeded",
                    fetch_scope="criteria_filtered",
                    completeness="complete",
                    observed_count=0,
                    returned_count=0,
                    persisted_count=0,
                    warning_codes=[],
                    started_at=NOW,
                    completed_at=NOW,
                    version=1,
                )
            )


def test_decision_reason_note_and_compensation_invariants(radar_db: Database) -> None:
    with radar_db.session() as session:
        session.add(_posting())
        session.flush()
        session.add(_posting_version())
        session.add(
            OwnerOpportunity(
                id="opportunity-a",
                owner_id="owner-a",
                job_posting_id="posting-a",
                decision="watch",
                first_surfaced_at=NOW,
                last_surfaced_at=NOW,
                version=2,
            )
        )
        session.flush()
        session.add(
            OpportunityDecisionEvent(
                id="event-watch",
                owner_id="owner-a",
                owner_opportunity_id="opportunity-a",
                job_posting_id="posting-a",
                posting_version_id="posting-version-a",
                previous_decision="inbox",
                new_decision="watch",
                idempotency_key_hash="3" * 64,
                request_hash="4" * 64,
                occurred_at=NOW,
            )
        )

    with pytest.raises(IntegrityError):
        with radar_db.session() as session:
            session.add(
                OpportunityDecisionEvent(
                    owner_id="owner-a",
                    owner_opportunity_id="opportunity-a",
                    job_posting_id="posting-a",
                    posting_version_id="posting-version-a",
                    previous_decision="watch",
                    new_decision="dismiss",
                    reason_code=None,
                    idempotency_key_hash="5" * 64,
                    request_hash="6" * 64,
                    occurred_at=NOW,
                )
            )

    with radar_db.session() as session:
        session.add(
            OpportunityDecisionEvent(
                id="event-dismiss",
                owner_id="owner-a",
                owner_opportunity_id="opportunity-a",
                job_posting_id="posting-a",
                posting_version_id="posting-version-a",
                previous_decision="watch",
                new_decision="dismiss",
                reason_code="not_a_career_upgrade",
                encrypted_note="encrypted-decision-note",
                note_key_id="key-v1",
                idempotency_key_hash="7" * 64,
                request_hash="8" * 64,
                occurred_at=NOW,
            )
        )
        session.add(
            OpportunityDecisionEvent(
                id="event-restore",
                owner_id="owner-a",
                owner_opportunity_id="opportunity-a",
                job_posting_id="posting-a",
                posting_version_id="posting-version-a",
                previous_decision="dismiss",
                new_decision="inbox",
                compensates_event_id="event-dismiss",
                idempotency_key_hash="9" * 64,
                request_hash="0" * 64,
                occurred_at=NOW,
            )
        )

    with radar_db.session() as session:
        events = list(
            session.scalars(
                select(OpportunityDecisionEvent).order_by(
                    OpportunityDecisionEvent.created_at,
                    OpportunityDecisionEvent.id,
                )
            )
        )
        assert {event.id for event in events} == {
            "event-watch",
            "event-dismiss",
            "event-restore",
        }


def test_lifecycle_omission_closure_requires_two_complete_omissions(
    radar_db: Database,
) -> None:
    invalid = _posting()
    invalid.id = "posting-invalid"
    invalid.identity_key_hash = "9" * 64
    invalid.lifecycle_state = "closed"
    invalid.closure_reason = "two_complete_omissions"
    invalid.consecutive_complete_omissions = 1
    invalid.closed_at = NOW
    with pytest.raises(IntegrityError):
        with radar_db.session() as session:
            session.add(invalid)
