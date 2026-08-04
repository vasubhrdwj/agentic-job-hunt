"""Durability and snapshot tests for automatic saved-search scan scheduling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

import job_hunt_agent.scheduled_scan_repository as scheduled_repository
from job_hunt_agent.database import Database
from job_hunt_agent.cadence_service import run_cadence_tick
from job_hunt_agent.models import (
    BackgroundJob,
    BackgroundJobEvent,
    Base,
    CareerTrack,
    OpportunityScan,
    OpportunityScanSource,
    Owner,
    ResumeVersion,
    SavedSearch,
)
from job_hunt_agent.scheduled_scan_repository import (
    due_saved_search_statement,
    enqueue_due_saved_search_scans,
)
from job_hunt_agent.saved_search_repository import list_saved_searches
from job_hunt_agent.schemas import Company, CompanySource
from job_hunt_agent.sources.registry import CompanyRegistry, RegistryError


NOW = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
DUE = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def scheduled_db(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'scheduled-scans.db'}")
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        session.add(Owner(id="owner-a", display_name="Owner A", timezone="UTC"))
        session.add(
            CareerTrack(
                id="track-a",
                owner_id="owner-a",
                name="Backend roles",
                role_families=["Backend Engineer"],
                seniority_levels=["mid"],
                target_locations=["India"],
                priorities={},
                active=True,
                version=1,
            )
        )
        session.add(
            ResumeVersion(
                id="resume-a",
                owner_id="owner-a",
                label="Base resume",
                encrypted_content="test-ciphertext",
                encryption_key_id="test-v1",
                content_hash="a" * 64,
                source="pasted",
                is_base=True,
                version=1,
            )
        )
        _add_search(session, search_id="due-a", next_scan_at=DUE)
    try:
        yield database
    finally:
        database.dispose()


def _company(
    slug: str,
    source: CompanySource,
    *,
    active: bool = True,
) -> Company:
    return Company(
        name=slug.title(),
        slug=slug,
        source=source,
        source_token=slug if active else None,
        careers_domains=[f"{slug}.example"] if active else [],
        hire_locations=["India"],
        tags=["backend"],
        active=active,
    )


def _registry(_pack: str) -> CompanyRegistry:
    return CompanyRegistry(
        [
            _company("acme", CompanySource.greenhouse),
            _company("beta", CompanySource.lever),
            _company("paused", CompanySource.ashby, active=False),
        ],
        name="test-pack",
    )


def _add_search(
    session,
    *,
    search_id: str,
    next_scan_at: datetime | None,
    cadence: str = "daily",
    active: bool = True,
    pack: str = "backend_india",
    version: int = 7,
) -> SavedSearch:
    row = SavedSearch(
        id=search_id,
        owner_id="owner-a",
        career_track_id="track-a",
        resume_version_id="resume-a",
        name=f"Search {search_id}",
        criteria_schema_version=1,
        criteria={
            "role_keywords": ["backend", "platform"],
            "seniority": "mid",
            "location": ["India", "Remote India"],
            "comp_min_lpa": None,
            "comp_max_lpa": None,
            "employment_types": ["full_time"],
            "max_age_days": 45,
            "country": "in",
        },
        pack=pack,
        use_self_rag=False,
        cadence=cadence,
        schedule={
            "local_time": "09:00" if cadence != "manual" else None,
            "days_of_week": [],
        },
        timezone="UTC",
        active=active,
        next_scan_at=next_scan_at,
        version=version,
        created_at=NOW - timedelta(days=3),
        updated_at=NOW - timedelta(days=3),
    )
    session.add(row)
    return row


def test_due_tick_pins_exact_inputs_sources_and_job_then_advances_atomically(
    scheduled_db: Database,
) -> None:
    with scheduled_db.session() as session:
        batch = enqueue_due_saved_search_scans(
            session,
            now=NOW,
            registry_loader=_registry,
        )
        assert batch.invalid_search_count == 0
        assert batch.created_count == 1
        assert len(batch.items) == 1

    with scheduled_db.session() as session:
        search = session.get(SavedSearch, "due-a")
        scan = session.scalar(select(OpportunityScan))
        job = session.scalar(select(BackgroundJob))
        sources = list(
            session.scalars(
                select(OpportunityScanSource).order_by(
                    OpportunityScanSource.company_slug
                )
            )
        )
        event = session.scalar(select(BackgroundJobEvent))

        assert search is not None and scan is not None and job is not None
        assert scan.trigger == "scheduled"
        assert scan.scheduled_for.replace(tzinfo=timezone.utc) == DUE
        assert scan.saved_search_version == search.version == 7
        assert scan.criteria_snapshot == search.criteria
        assert scan.pack_snapshot == "backend_india"
        assert scan.source_count == 2
        assert [(row.company_slug, row.source) for row in sources] == [
            ("acme", "greenhouse"),
            ("beta", "lever"),
        ]
        assert job.id == scan.background_job_id == batch.items[0].job_id
        assert job.subject_type == "opportunity_scan"
        assert job.subject_id == scan.id
        assert job.payload == {
            "opportunity_scan_id": scan.id,
            "saved_search_id": search.id,
            "saved_search_version": 7,
        }
        assert event is not None and event.actor == "scheduler"
        assert search.last_scan_at is None
        assert search.next_scan_at is not None
        assert search.next_scan_at.replace(tzinfo=timezone.utc) == datetime(
            2026, 7, 21, 9, 0, tzinfo=timezone.utc
        )

        original_snapshot = dict(scan.criteria_snapshot)
        search.criteria = {**search.criteria, "role_keywords": ["api"]}
        session.flush()
        assert scan.criteria_snapshot == original_snapshot


def test_tick_excludes_manual_inactive_and_future_searches(
    scheduled_db: Database,
) -> None:
    with scheduled_db.session() as session:
        _add_search(
            session,
            search_id="manual-a",
            cadence="manual",
            next_scan_at=None,
        )
        _add_search(
            session,
            search_id="inactive-a",
            active=False,
            next_scan_at=None,
        )
        _add_search(
            session,
            search_id="future-a",
            next_scan_at=NOW + timedelta(hours=1),
        )

    with scheduled_db.session() as session:
        batch = enqueue_due_saved_search_scans(
            session,
            now=NOW,
            registry_loader=_registry,
        )
        assert [item.saved_search_id for item in batch.items] == ["due-a"]

    with scheduled_db.session() as session:
        assert session.scalar(select(func.count(OpportunityScan.id))) == 1
        assert session.scalar(select(func.count(BackgroundJob.id))) == 1


def test_slot_dedupe_survives_a_restart_style_stale_due_pointer(
    scheduled_db: Database,
) -> None:
    with scheduled_db.session() as session:
        first = enqueue_due_saved_search_scans(
            session,
            now=NOW,
            registry_loader=_registry,
        )
        assert first.created_count == 1

    # A stale operational pointer cannot create a second scan or queue row for
    # the same search version and wall-clock slot; durable unique keys replay it.
    with scheduled_db.session() as session:
        search = session.get(SavedSearch, "due-a")
        assert search is not None
        search.next_scan_at = DUE

    with scheduled_db.session() as session:
        replay = enqueue_due_saved_search_scans(
            session,
            now=NOW,
            registry_loader=_registry,
        )
        assert len(replay.items) == 1
        assert replay.items[0].created is False
        assert replay.items[0].scan_id == first.items[0].scan_id
        assert replay.items[0].job_id == first.items[0].job_id

    with scheduled_db.session() as session:
        assert session.scalar(select(func.count(OpportunityScan.id))) == 1
        assert session.scalar(select(func.count(OpportunityScanSource.id))) == 2
        assert session.scalar(select(func.count(BackgroundJob.id))) == 1


def test_external_cadence_tick_replay_keeps_one_scan_and_job(
    scheduled_db: Database,
) -> None:
    first = run_cadence_tick(
        scheduled_db,
        now=NOW,
        registry_loader=_registry,
    )
    assert first.created_scans == 1
    assert first.replayed_scans == 0

    # Simulate an overlapping wake that read an old operational pointer. The
    # slot identity, scan constraint, and queue dedupe must still converge.
    with scheduled_db.session() as session:
        search = session.get(SavedSearch, "due-a")
        assert search is not None
        search.next_scan_at = DUE

    replay = run_cadence_tick(
        scheduled_db,
        now=NOW,
        registry_loader=_registry,
    )
    assert replay.created_scans == 0
    assert replay.replayed_scans == 1

    with scheduled_db.session() as session:
        assert session.scalar(select(func.count(OpportunityScan.id))) == 1
        assert session.scalar(select(func.count(BackgroundJob.id))) == 1


def test_queue_failure_rolls_back_scan_sources_and_schedule_advance(
    scheduled_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("simulated queue failure")

    monkeypatch.setattr(scheduled_repository, "enqueue_job", fail_enqueue)
    with pytest.raises(RuntimeError, match="simulated queue failure"):
        with scheduled_db.session() as session:
            enqueue_due_saved_search_scans(
                session,
                now=NOW,
                registry_loader=_registry,
            )

    with scheduled_db.session() as session:
        search = session.get(SavedSearch, "due-a")
        assert search is not None and search.next_scan_at is not None
        assert search.next_scan_at.replace(tzinfo=timezone.utc) == DUE
        assert search.version == 7
        assert session.scalar(select(func.count(OpportunityScan.id))) == 0
        assert session.scalar(select(func.count(OpportunityScanSource.id))) == 0
        assert session.scalar(select(func.count(BackgroundJob.id))) == 0


def test_tick_is_bounded_oldest_first_and_invalid_pack_does_not_starve_others(
    scheduled_db: Database,
) -> None:
    with scheduled_db.session() as session:
        base = session.get(SavedSearch, "due-a")
        assert base is not None
        base.next_scan_at = DUE + timedelta(minutes=2)
        _add_search(
            session,
            search_id="invalid-a",
            next_scan_at=DUE,
            pack="missing_pack",
        )
        _add_search(
            session,
            search_id="due-b",
            next_scan_at=DUE + timedelta(minutes=1),
        )

    def load_registry(pack: str) -> CompanyRegistry:
        if pack == "missing_pack":
            raise RegistryError("missing test pack")
        return _registry(pack)

    with scheduled_db.session() as session:
        first_batch = enqueue_due_saved_search_scans(
            session,
            now=NOW,
            limit=1,
            registry_loader=load_registry,
        )
        assert first_batch.invalid_search_count == 1
        assert first_batch.items == ()

    with scheduled_db.session() as session:
        second_batch = enqueue_due_saved_search_scans(
            session,
            now=NOW,
            limit=1,
            registry_loader=load_registry,
        )
        assert second_batch.invalid_search_count == 0
        assert [item.saved_search_id for item in second_batch.items] == ["due-b"]

    with scheduled_db.session() as session:
        invalid = session.get(SavedSearch, "invalid-a")
        untouched = session.get(SavedSearch, "due-a")
        assert invalid is not None and invalid.next_scan_at is None
        assert invalid.active is False
        assert invalid.version == 7
        assert untouched is not None and untouched.next_scan_at is not None
        assert untouched.next_scan_at.replace(tzinfo=timezone.utc) == DUE + timedelta(
            minutes=2
        )


def test_malformed_schedule_container_is_paused_without_starving_later_searches(
    scheduled_db: Database,
) -> None:
    with scheduled_db.session() as session:
        malformed = session.get(SavedSearch, "due-a")
        assert malformed is not None
        malformed.schedule = None  # type: ignore[assignment] - legacy corrupted JSON.
        _add_search(
            session,
            search_id="due-b",
            next_scan_at=DUE + timedelta(minutes=1),
        )

    with scheduled_db.session() as session:
        batch = enqueue_due_saved_search_scans(
            session,
            now=NOW,
            registry_loader=_registry,
        )
        assert batch.invalid_search_count == 1
        assert [item.saved_search_id for item in batch.items] == ["due-b"]

    with scheduled_db.session() as session:
        malformed = session.get(SavedSearch, "due-a")
        assert malformed is not None
        assert malformed.active is False
        assert malformed.next_scan_at is None
        listed = {item.id: item for item in list_saved_searches(session, owner_id="owner-a")}
        assert listed["due-a"].active is False
        assert listed["due-a"].schedule.cadence == "manual"


def test_invalid_legacy_search_is_durably_paused_for_owner_review(
    scheduled_db: Database,
) -> None:
    with scheduled_db.session() as session:
        search = session.get(SavedSearch, "due-a")
        assert search is not None
        search.criteria = {**search.criteria, "role_keywords": []}
        search.schedule = {"local_time": 900, "days_of_week": []}

    with scheduled_db.session() as session:
        batch = enqueue_due_saved_search_scans(
            session,
            now=NOW,
            registry_loader=_registry,
        )
        assert batch.items == ()
        assert batch.invalid_search_count == 1

    with scheduled_db.session() as session:
        search = session.get(SavedSearch, "due-a")
        assert search is not None and search.next_scan_at is None
        assert search.active is False
        assert search.version == 7
        assert session.scalar(select(func.count(OpportunityScan.id))) == 0


def test_due_claim_compiles_to_postgres_skip_locked() -> None:
    statement = due_saved_search_statement(now=NOW, limit=10)
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    normalized = " ".join(sql.upper().split())
    assert "FOR UPDATE SKIP LOCKED" in normalized
    assert "SAVED_SEARCHES.ACTIVE IS TRUE" in normalized
    assert "SAVED_SEARCHES.NEXT_SCAN_AT <=" in normalized
