from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

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
from job_hunt_agent.opportunity_repository import (
    OpportunityNotFound,
    canonicalize_posting_url,
    decide_owner_opportunity,
    list_today_opportunities,
    load_opportunity_detail,
    persist_scan_source_role,
    posting_identity,
)
from job_hunt_agent.opportunity_schemas import OpportunityDecisionRequest, TodayQuery
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
            _seed_owner_search(session, owner_id, f"search-{owner_id[-1]}")
        _seed_scan_source(session, "owner-a", "search-a", "scan-a1", "source-a1")
    try:
        yield database, keyring
    finally:
        database.dispose()


def _seed_owner_search(session, owner_id: str, search_id: str) -> None:
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
    session.add(
        ResumeVersion(
            id=resume_id,
            owner_id=owner_id,
            label="Resume",
            encrypted_content="ciphertext",
            encryption_key_id="test-v1",
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
