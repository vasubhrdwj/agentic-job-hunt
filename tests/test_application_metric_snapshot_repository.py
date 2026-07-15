"""Pursuit-time acquisition and segmentation snapshot behavior."""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import func, select

from job_hunt_agent.application_repository import pursue_owner_opportunity
from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    Application,
    ApplicationMetricSnapshot,
    CareerTrack,
    OpportunityScan,
    ResumeVersion,
    SavedSearch,
    SavedSearchMatch,
)
from job_hunt_agent.opportunity_schemas import PursueOpportunityRequest
from job_hunt_agent.repository_errors import ResourceConflict
from tests.test_application_repository import NOW, application_repository_db


def _seed_search_match(
    database: Database,
    *,
    suffix: str,
    owner_id: str = "owner-a",
    search_version: int = 1,
    track_version: int = 1,
) -> None:
    with database.session() as session:
        track_id = f"track-{suffix}"
        resume_id = f"resume-{suffix}"
        search_id = f"search-{suffix}"
        scan_id = f"scan-{suffix}"
        session.add(
            CareerTrack(
                id=track_id,
                owner_id=owner_id,
                name=f"Track {suffix}",
                role_families=["Backend Engineer"],
                seniority_levels=["senior"],
                target_locations=["India"],
                priorities={},
                active=True,
                version=track_version,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            ResumeVersion(
                id=resume_id,
                owner_id=owner_id,
                label=f"Resume {suffix}",
                encrypted_content="ciphertext",
                encryption_key_id="test-v1",
                content_hash=hashlib.sha256(
                    f"{owner_id}:{suffix}".encode("utf-8")
                ).hexdigest(),
                source="pasted",
                is_base=False,
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
                name=f"Search {suffix}",
                criteria_schema_version=1,
                criteria={"role_keywords": ["backend"]},
                pack="backend_india",
                use_self_rag=False,
                cadence="manual",
                schedule={"local_time": None, "days_of_week": []},
                timezone="UTC",
                active=True,
                version=search_version,
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
                saved_search_version=search_version,
                criteria_schema_version=1,
                criteria_snapshot={"role_keywords": ["backend"]},
                pack_snapshot="backend_india",
                trigger="manual",
                scheduled_for=NOW,
                dedupe_key=scan_id,
                request_hash=hashlib.sha256(scan_id.encode("utf-8")).hexdigest(),
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
            SavedSearchMatch(
                id=f"match-{suffix}",
                owner_id=owner_id,
                saved_search_id=search_id,
                job_posting_id="posting-a",
                first_scan_id=scan_id,
                last_scan_id=scan_id,
                last_posting_version_id="posting-version-2",
                match_count=1,
                first_matched_at=NOW,
                last_matched_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )


def _snapshot(database: Database) -> ApplicationMetricSnapshot:
    with database.session() as session:
        snapshot = session.scalar(select(ApplicationMetricSnapshot))
        assert snapshot is not None
        session.expunge(snapshot)
        return snapshot


def test_pursuit_without_search_provenance_records_explicit_missing_attribution(
    application_repository_db: Database,
) -> None:
    with application_repository_db.session() as session:
        pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            PursueOpportunityRequest(),
            1,
            "missing-attribution",
            NOW,
        )

    snapshot = _snapshot(application_repository_db)
    assert snapshot.acquisition_source == "job_hunt_search"
    assert snapshot.attribution_status == "attribution_missing"
    assert snapshot.saved_search_id is None
    assert snapshot.career_track_id is None
    assert snapshot.assessment_state == "not_assessed"
    assert snapshot.assessment_reason == "not_requested"


def test_exactly_one_matching_search_is_captured_and_remains_immutable(
    application_repository_db: Database,
) -> None:
    _seed_search_match(
        application_repository_db,
        suffix="one",
        search_version=3,
        track_version=2,
    )
    with application_repository_db.session() as session:
        pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            PursueOpportunityRequest(),
            1,
            "one-search",
            NOW,
        )

    snapshot = _snapshot(application_repository_db)
    assert snapshot.attribution_status == "captured"
    assert (
        snapshot.saved_search_id,
        snapshot.saved_search_version,
        snapshot.saved_search_name,
    ) == ("search-one", 3, "Search one")
    assert (
        snapshot.career_track_id,
        snapshot.career_track_version,
        snapshot.career_track_name,
    ) == ("track-one", 2, "Track one")

    with application_repository_db.session() as session:
        search = session.get(SavedSearch, "search-one")
        track = session.get(CareerTrack, "track-one")
        assert search is not None and track is not None
        search.name = "Renamed mutable search"
        search.version += 1
        track.name = "Renamed mutable track"
        track.version += 1

    frozen = _snapshot(application_repository_db)
    assert (frozen.saved_search_name, frozen.saved_search_version) == (
        "Search one",
        3,
    )
    assert (frozen.career_track_name, frozen.career_track_version) == (
        "Track one",
        2,
    )


def test_multiple_matching_searches_require_an_explicit_valid_selection(
    application_repository_db: Database,
) -> None:
    _seed_search_match(application_repository_db, suffix="first")
    _seed_search_match(application_repository_db, suffix="second")

    with pytest.raises(ResourceConflict, match="multiple saved searches"):
        with application_repository_db.session() as session:
            pursue_owner_opportunity(
                session,
                "owner-a",
                "opportunity-a",
                PursueOpportunityRequest(),
                1,
                "ambiguous-search",
                NOW,
            )

    with application_repository_db.session() as session:
        assert session.scalar(select(func.count(Application.id))) == 0
        assert session.scalar(select(func.count(ApplicationMetricSnapshot.id))) == 0
        pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            PursueOpportunityRequest(selected_saved_search_id="search-second"),
            1,
            "selected-search",
            NOW,
        )

    snapshot = _snapshot(application_repository_db)
    assert snapshot.saved_search_id == "search-second"
    assert snapshot.saved_search_name == "Search second"


def test_selected_search_must_have_produced_this_owner_opportunity(
    application_repository_db: Database,
) -> None:
    _seed_search_match(application_repository_db, suffix="owner")
    with pytest.raises(ResourceConflict, match="did not produce"):
        with application_repository_db.session() as session:
            pursue_owner_opportunity(
                session,
                "owner-a",
                "opportunity-a",
                PursueOpportunityRequest(
                    selected_saved_search_id="search-not-a-match"
                ),
                1,
                "bad-selected-search",
                NOW,
            )


def test_non_search_acquisition_is_captured_without_search_or_track(
    application_repository_db: Database,
) -> None:
    _seed_search_match(application_repository_db, suffix="ignored")
    with application_repository_db.session() as session:
        pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            PursueOpportunityRequest(acquisition_source="referral"),
            1,
            "referral-source",
            NOW,
        )

    snapshot = _snapshot(application_repository_db)
    assert snapshot.acquisition_source == "referral"
    assert snapshot.attribution_status == "captured"
    assert snapshot.saved_search_id is None
    assert snapshot.career_track_id is None


def test_same_key_replay_creates_exactly_one_metric_snapshot(
    application_repository_db: Database,
) -> None:
    _seed_search_match(application_repository_db, suffix="replay")
    request = PursueOpportunityRequest(selected_saved_search_id="search-replay")
    with application_repository_db.session() as session:
        first = pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            request,
            1,
            "snapshot-replay",
            NOW,
        )
        replay = pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            request,
            1,
            "snapshot-replay",
            NOW,
        )
        assert first.pursuit is not None and replay.pursuit is not None
        assert replay.pursuit.application.id == first.pursuit.application.id
        assert session.scalar(
            select(func.count(ApplicationMetricSnapshot.id))
        ) == 1
