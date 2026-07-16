"""End-to-end tests for the provider-free durable opportunity scan worker."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select

import job_hunt_agent.opportunity_scan_worker as scan_worker
import job_hunt_agent.run as hunt_run_module
from job_hunt_agent import worker
from job_hunt_agent.database import Database
from job_hunt_agent.embedded_scan_worker import EmbeddedScanWorker
from job_hunt_agent.job_queue import record_worker_heartbeat
from job_hunt_agent.models import (
    BackgroundJob,
    CareerTrack,
    HuntRun,
    JobObservation,
    JobPosting,
    JobPostingVersion,
    OpportunityScan,
    OpportunityScanSource,
    Owner,
    OwnerOpportunity,
    ResumeVersion,
    SavedSearch,
    SavedSearchMatch,
    WorkerHeartbeat,
)
from job_hunt_agent.opportunity_schemas import ScanCreateRequest
from job_hunt_agent.security import load_data_keyring
from job_hunt_agent.sources.registry import load_company_pack
from job_hunt_agent.sqlalchemy_opportunity_workspace import (
    SqlAlchemyOpportunityWorkspaceStore,
)


OWNER_ID = "owner-a"
SEARCH_ID = "search-a"
SEARCH_VERSION = 3
PACK = "backend_india"


@pytest.fixture
def scan_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Database, SqlAlchemyOpportunityWorkspaceStore]]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'scan-worker.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("ENABLE_PRACTICAL_MODE", "1")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.delenv("APP_VERSION", raising=False)
    command.upgrade(Config("alembic.ini"), "head")
    database = Database(database_url)
    with database.session() as session:
        _seed_saved_search(session)
        session.add(Owner(id="owner-b", display_name="Owner B", timezone="UTC"))
        record_worker_heartbeat(
            session,
            worker_id="role-scan-capability",
            supported_kinds={scan_worker.SCAN_JOB_KIND},
            now=datetime.now(timezone.utc),
        )
    store = SqlAlchemyOpportunityWorkspaceStore(
        database,
        load_data_keyring(production=False),
    )
    try:
        yield database, store
    finally:
        database.dispose()


def _seed_saved_search(session) -> None:
    session.add(
        Owner(
            id=OWNER_ID,
            display_name="Owner A",
            timezone="Asia/Kolkata",
        )
    )
    session.add(
        CareerTrack(
            id="track-a",
            owner_id=OWNER_ID,
            name="Backend growth",
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
            id="resume-a",
            owner_id=OWNER_ID,
            label="Base resume",
            encrypted_content="PRIVATE RESUME CIPHERTEXT MUST NOT BE LOADED",
            encryption_key_id="local-dev",
            content_hash="a" * 64,
            source="pasted",
            is_base=True,
            version=1,
        )
    )
    session.add(
        SavedSearch(
            id=SEARCH_ID,
            owner_id=OWNER_ID,
            career_track_id="track-a",
            resume_version_id="resume-a",
            name="Senior backend roles",
            criteria_schema_version=1,
            criteria={
                "role_keywords": ["backend", "platform"],
                "seniority": "senior",
                "location": ["Remote India"],
                "comp_min_lpa": 30,
                "comp_max_lpa": None,
                "employment_types": ["full_time"],
                "max_age_days": 45,
                "country": "in",
            },
            pack=PACK,
            use_self_rag=True,
            cadence="manual",
            schedule={"local_time": None, "days_of_week": []},
            timezone="Asia/Kolkata",
            active=True,
            next_scan_at=None,
            version=SEARCH_VERSION,
        )
    )


def _create_scan(
    store: SqlAlchemyOpportunityWorkspaceStore,
    *,
    idempotency_key: str,
):
    return store.create_scan(
        owner_id=OWNER_ID,
        saved_search_id=SEARCH_ID,
        expected_saved_search_version=SEARCH_VERSION,
        idempotency_key=idempotency_key,
        payload=ScanCreateRequest(),
    )


def _run_once(
    database: Database,
    *,
    worker_id: str = "scan-worker",
    job_kinds: set[str] | None = None,
):
    return worker.run_worker_once(
        worker_id=worker_id,
        lease_seconds=60,
        retry_delay_seconds=0,
        use_mocks=True,
        enable_tracing=False,
        durable_database=database,
        practical_mode=True,
        job_kinds=job_kinds,
    )


def _install_no_hunt_guards(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def forbidden(name: str) -> Callable[..., object]:
        def fail(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"search-only scan invoked {name}")

        return fail

    monkeypatch.setattr(worker, "run_hunt", forbidden("full hunt"))
    monkeypatch.setattr(
        hunt_run_module,
        "build_pipeline_tools",
        forbidden("referral/draft tools"),
    )
    monkeypatch.setattr(
        hunt_run_module.ResumeFitScorer,
        "rank_roles",
        forbidden("resume matching"),
    )
    monkeypatch.setattr(
        scan_worker.SourceResolver,
        "fetch_company_roles_result",
        forbidden("live source or model path"),
    )
    return calls


def test_free_tier_embedded_worker_completes_a_durable_scan(
    scan_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, store = scan_workspace
    monkeypatch.setenv("USE_MOCKS", "1")
    with database.session() as session:
        session.execute(delete(WorkerHeartbeat))

    embedded = EmbeddedScanWorker(
        worker_id="embedded-scan-e2e",
        idle_sleep_seconds=0.01,
        error_sleep_seconds=0.01,
    )
    embedded.start()
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with database.session() as session:
                heartbeat = session.get(WorkerHeartbeat, "embedded-scan-e2e")
                if heartbeat is not None:
                    break
            time.sleep(0.01)
        else:
            pytest.fail("embedded worker did not publish a readiness heartbeat")

        created = _create_scan(store, idempotency_key="embedded-free-tier")
        response = created
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = store.get_scan(owner_id=OWNER_ID, scan_id=created.id)
            assert response is not None
            if response.status.value in {"succeeded", "partial", "failed"}:
                break
            time.sleep(0.02)
        else:
            pytest.fail("embedded worker did not complete the queued scan")

        assert response.status.value in {"succeeded", "partial"}
        assert response.counts.sources_completed == response.counts.sources_total
        assert response.counts.new_opportunities > 0
        with database.session() as session:
            heartbeat = session.get(WorkerHeartbeat, "embedded-scan-e2e")
            assert heartbeat is not None
            assert heartbeat.supported_kinds == [scan_worker.SCAN_JOB_KIND]
    finally:
        embedded.stop(timeout_seconds=2)


def test_mock_scan_retries_idempotently_and_persists_only_public_job_facts(
    scan_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, store = scan_workspace
    forbidden_calls = _install_no_hunt_guards(monkeypatch)
    created = _create_scan(store, idempotency_key="retry-scan")
    expected_sources = len(load_company_pack(PACK).active_companies)

    real_persist = scan_worker._persist_source_result
    interruptions = 0

    def persist_then_interrupt(*args, **kwargs) -> None:
        nonlocal interruptions
        real_persist(*args, **kwargs)
        if interruptions == 0:
            interruptions += 1
            raise RuntimeError("simulated process interruption")

    monkeypatch.setattr(scan_worker, "_persist_source_result", persist_then_interrupt)
    scan_only = {scan_worker.SCAN_JOB_KIND}
    interrupted = _run_once(database, job_kinds=scan_only)
    assert interrupted.claimed is True
    assert interrupted.run_id == created.id
    assert interrupted.status == "queued"
    assert interrupted.stage == "retry_scheduled"

    monkeypatch.setattr(scan_worker, "_persist_source_result", real_persist)
    completed = _run_once(database, job_kinds=scan_only)
    assert completed.claimed is True
    assert completed.run_id == created.id
    assert completed.status == "succeeded"
    assert completed.stage == "succeeded"
    assert _run_once(database, job_kinds=scan_only).claimed is False
    assert forbidden_calls == []

    response = store.get_scan(owner_id=OWNER_ID, scan_id=created.id)
    assert response is not None
    assert response.status.value == "partial"
    assert response.stage.value == "complete"
    assert response.counts.sources_total == expected_sources
    assert response.counts.sources_completed == expected_sources
    assert response.counts.sources_succeeded == 0
    assert response.counts.sources_degraded == expected_sources
    assert response.counts.sources_failed == 0
    assert response.counts.observed_postings == 3
    assert response.counts.matched_postings == 3
    assert response.counts.new_opportunities == 3
    assert len(response.warnings) == expected_sources
    assert {warning.code.value for warning in response.warnings} == {
        "source_incomplete"
    }

    registry = load_company_pack(PACK)
    company_domains = {
        company.slug: set(company.careers_domains)
        for company in registry.active_companies
    }
    with database.session() as session:
        scan = session.get(OpportunityScan, created.id)
        assert scan is not None
        assert scan.status == "partial"
        assert scan.source_count == expected_sources
        assert scan.terminal_source_count == expected_sources
        assert scan.successful_source_count == expected_sources
        assert scan.failed_source_count == 0
        assert scan.observed_count == 3
        assert scan.new_posting_count == 3
        assert scan.new_opportunity_count == 3

        sources = list(
            session.scalars(
                select(OpportunityScanSource).where(
                    OpportunityScanSource.opportunity_scan_id == created.id
                )
            )
        )
        assert len(sources) == expected_sources
        assert {source.status for source in sources} == {"succeeded"}
        assert {source.fetch_scope for source in sources} == {"criteria_filtered"}
        assert {source.completeness for source in sources} == {"partial"}
        assert sum(source.observed_count for source in sources) == 3
        assert sum(source.returned_count for source in sources) == 3
        assert sum(source.persisted_count for source in sources) == 3

        postings = list(session.scalars(select(JobPosting)))
        versions = list(session.scalars(select(JobPostingVersion)))
        observations = list(session.scalars(select(JobObservation)))
        assert len(postings) == 3
        assert len(versions) == 3
        assert len(observations) == 3
        assert session.scalar(select(func.count(SavedSearchMatch.id))) == 3
        assert session.scalar(select(func.count(OwnerOpportunity.id))) == 3
        assert session.scalar(select(func.count(HuntRun.id))) == 0
        assert all(item.first_party_url_verified for item in observations)
        assert all(
            urlsplit(posting.canonical_url).hostname
            in company_domains[posting.company_slug]
            for posting in postings
        )
        assert all(version.source_facts == {} for version in versions)
        assert all("match_reason" not in version.__table__.columns for version in versions)
        assert all("fit_score" not in version.__table__.columns for version in versions)
        assert all(
            "Deterministic local fixture" not in str(version.__dict__)
            for version in versions
        )

        job = session.get(BackgroundJob, scan.background_job_id)
        assert job is not None
        assert job.status == "succeeded"
        assert job.attempt_count == 2
        heartbeat = session.get(WorkerHeartbeat, "scan-worker")
        assert heartbeat is not None
        assert set(heartbeat.supported_kinds) == {scan_worker.SCAN_JOB_KIND}
        assert set(worker.PRACTICAL_JOB_KINDS) == {
            "discover_contacts",
            "legacy_hunt",
            scan_worker.SCAN_JOB_KIND,
        }


def test_source_failure_preserves_previously_saved_opportunities(
    scan_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, store = scan_workspace
    forbidden_calls = _install_no_hunt_guards(monkeypatch)
    first = _create_scan(store, idempotency_key="initial-scan")
    assert _run_once(database).status == "succeeded"

    with database.session() as session:
        original_posting_ids = set(session.scalars(select(JobPosting.id)))
        original_opportunity_ids = set(session.scalars(select(OwnerOpportunity.id)))
    assert len(original_posting_ids) == len(original_opportunity_ids) == 3

    second = _create_scan(store, idempotency_key="failure-scan")
    real_fetch = scan_worker._fetch_company

    def fail_one_source(company, criteria, *, use_mocks: bool, mock_index: int):
        if company.slug == "amazon":
            raise RuntimeError("private upstream failure detail")
        return real_fetch(
            company,
            criteria,
            use_mocks=use_mocks,
            mock_index=mock_index,
        )

    monkeypatch.setattr(scan_worker, "_fetch_company", fail_one_source)
    result = _run_once(database)
    assert result.status == "succeeded"
    assert forbidden_calls == []

    response = store.get_scan(owner_id=OWNER_ID, scan_id=second.id)
    assert response is not None
    assert response.status.value == "partial"
    assert response.counts.sources_failed == 1
    assert response.counts.sources_degraded == len(load_company_pack(PACK).active_companies) - 1
    assert response.counts.observed_postings == 2
    amazon_warnings = [
        warning
        for warning in response.warnings
        if warning.company_slug == "amazon"
    ]
    assert [warning.code.value for warning in amazon_warnings] == [
        "source_unavailable"
    ]

    with database.session() as session:
        failed_source = session.scalar(
            select(OpportunityScanSource).where(
                OpportunityScanSource.opportunity_scan_id == second.id,
                OpportunityScanSource.company_slug == "amazon",
            )
        )
        assert failed_source is not None
        assert failed_source.status == "failed"
        assert failed_source.completeness == "unknown"
        assert failed_source.error_code == "source_fetch_failed"
        assert failed_source.warning_codes == ["source_fetch_failed"]
        assert "private upstream failure detail" not in str(failed_source.__dict__)

        postings = list(session.scalars(select(JobPosting)))
        assert {posting.id for posting in postings} == original_posting_ids
        assert {posting.lifecycle_state for posting in postings} == {"open"}
        assert set(session.scalars(select(OwnerOpportunity.id))) == original_opportunity_ids
        assert session.scalar(select(func.count(JobPostingVersion.id))) == 3
        assert session.scalar(select(func.count(SavedSearchMatch.id))) == 3
        assert session.scalar(select(func.count(JobObservation.id))) == 5
        first_scan = session.get(OpportunityScan, first.id)
        second_scan = session.get(OpportunityScan, second.id)
        assert first_scan is not None and first_scan.status == "partial"
        assert second_scan is not None and second_scan.failed_source_count == 1


def test_scan_drops_untrusted_alternate_apply_urls(
    scan_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, store = scan_workspace
    created = _create_scan(store, idempotency_key="unsafe-apply-url")
    real_fetch = scan_worker._fetch_company
    injected_company = sorted(
        company.slug for company in load_company_pack(PACK).active_companies
    )[0]

    def add_untrusted_apply_url(company, criteria, *, use_mocks: bool, mock_index: int):
        result = real_fetch(
            company,
            criteria,
            use_mocks=use_mocks,
            mock_index=mock_index,
        )
        if company.slug == injected_company and result.roles:
            role = result.roles[0]
            unsafe = role.model_copy(
                update={"apply_urls": [*role.apply_urls, "https://evil.example/apply"]}
            )
            return result.model_copy(update={"roles": [unsafe]})
        return result

    monkeypatch.setattr(scan_worker, "_fetch_company", add_untrusted_apply_url)
    assert _run_once(database).status == "succeeded"

    with database.session() as session:
        source = session.scalar(
            select(OpportunityScanSource).where(
                OpportunityScanSource.opportunity_scan_id == created.id,
                OpportunityScanSource.company_slug == injected_company,
            )
        )
        assert source is not None
        assert "untrusted_apply_url_skipped" in source.warning_codes
        observation = session.scalar(
            select(JobObservation).where(
                JobObservation.opportunity_scan_source_id == source.id
            )
        )
        assert observation is not None
        version = session.get(JobPostingVersion, observation.job_posting_version_id)
        assert version is not None
        assert "https://evil.example/apply" not in version.apply_urls
        assert all(urlsplit(url).hostname != "evil.example" for url in version.apply_urls)

    response = store.get_scan(owner_id=OWNER_ID, scan_id=created.id)
    assert response is not None
    assert any(
        warning.company_slug == injected_company
        and warning.code.value == "source_invalid_response"
        for warning in response.warnings
    )


def test_queued_scan_uses_its_pinned_company_pack(
    scan_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
) -> None:
    database, store = scan_workspace
    created = _create_scan(store, idempotency_key="pinned-pack")
    with database.session() as session:
        scan = session.get(OpportunityScan, created.id)
        search = session.get(SavedSearch, SEARCH_ID)
        assert scan is not None and search is not None
        assert scan.pack_snapshot == PACK
        search.pack = "pack-changed-after-queueing"
        search.version += 1

    result = _run_once(database, worker_id="pinned-pack-worker")
    assert result.status == "succeeded"
    response = store.get_scan(owner_id=OWNER_ID, scan_id=created.id)
    assert response is not None
    assert response.status.value == "partial"
    assert response.counts.observed_postings == 3


@pytest.mark.parametrize(
    ("cancel_requested", "expected_job", "expected_scan", "source_error"),
    [
        (False, "dead_letter", "failed", "scan_interrupted"),
        (True, "cancelled", "cancelled", None),
    ],
)
def test_stale_terminal_scan_job_reconciles_scan_and_sources(
    scan_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
    cancel_requested: bool,
    expected_job: str,
    expected_scan: str,
    source_error: str | None,
) -> None:
    database, store = scan_workspace
    created = _create_scan(
        store,
        idempotency_key=f"stale-{expected_job}",
    )
    with database.session() as session:
        scan = session.get(OpportunityScan, created.id)
        assert scan is not None and scan.background_job_id is not None
        expired = scan.created_at
        scan.status = "running"
        scan.stage = "fetching"
        scan.started_at = expired
        job = session.get(BackgroundJob, scan.background_job_id)
        assert job is not None
        job.status = "running"
        job.stage = "fetching"
        job.attempt_count = job.max_attempts
        job.lease_owner = "vanished-worker"
        job.lease_token = "expired-lease"
        job.lease_expires_at = expired
        job.started_at = expired
        if cancel_requested:
            job.cancel_requested_at = expired

    result = _run_once(database, worker_id=f"recovery-{expected_job}")
    assert result.claimed is False

    with database.session() as session:
        scan = session.get(OpportunityScan, created.id)
        assert scan is not None and scan.background_job_id is not None
        job = session.get(BackgroundJob, scan.background_job_id)
        assert job is not None
        assert job.status == expected_job
        assert scan.status == expected_scan
        assert scan.stage == "complete"
        assert scan.finalized_at is not None
        sources = list(
            session.scalars(
                select(OpportunityScanSource).where(
                    OpportunityScanSource.opportunity_scan_id == created.id
                )
            )
        )
        assert sources
        assert {source.status for source in sources} == {expected_scan}
        assert all(source.completed_at is not None for source in sources)
        assert all(source.error_code == source_error for source in sources)
        if expected_scan == "failed":
            assert all(source.started_at is not None for source in sources)

    response = store.get_scan(owner_id=OWNER_ID, scan_id=created.id)
    assert response is not None
    assert response.status.value == expected_scan
    assert response.stage.value == "complete"
    assert response.counts.sources_completed == response.counts.sources_total
    assert response.counts.sources_failed == response.counts.sources_total


def test_terminal_queue_anomaly_does_not_overwrite_a_completed_scan(
    scan_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
) -> None:
    database, store = scan_workspace
    created = _create_scan(store, idempotency_key="completed-scan-wins")
    assert _run_once(database).status == "succeeded"
    with database.session() as session:
        scan = session.get(OpportunityScan, created.id)
        assert scan is not None and scan.background_job_id is not None
        assert scan.status == "partial"
        job = session.get(BackgroundJob, scan.background_job_id)
        assert job is not None
        job.status = "dead_letter"
        job.stage = "dead_letter"
        job.last_error = "synthetic_queue_anomaly"
        job.dead_lettered_at = scan.finalized_at

    assert _run_once(database, worker_id="completed-scan-recovery").claimed is False
    with database.session() as session:
        scan = session.get(OpportunityScan, created.id)
        assert scan is not None
        assert scan.status == "partial"
        assert scan.observed_count == 3


@pytest.mark.parametrize("bad_reference", ["invalid", "foreign"])
def test_invalid_or_foreign_scan_claim_is_terminally_rejected(
    scan_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
    bad_reference: str,
) -> None:
    database, store = scan_workspace
    created = _create_scan(store, idempotency_key=f"{bad_reference}-claim")
    with database.session() as session:
        scan = session.get(OpportunityScan, created.id)
        assert scan is not None and scan.background_job_id is not None
        job = session.get(BackgroundJob, scan.background_job_id)
        assert job is not None
        if bad_reference == "invalid":
            job.payload = {"opportunity_scan_id": "missing-scan"}
        else:
            job.owner_id = "owner-b"
            job.dedupe_scope = "owner:owner-b"

    result = _run_once(database, worker_id=f"{bad_reference}-worker")
    assert result.claimed is True
    assert result.status == "dead_letter"
    assert result.stage == "dead_letter"

    with database.session() as session:
        scan = session.get(OpportunityScan, created.id)
        assert scan is not None
        job = session.get(BackgroundJob, scan.background_job_id)
        assert job is not None
        assert job.status == "dead_letter"
        assert job.last_error == "InvalidScanReference"
        assert job.attempt_count == 1
        assert scan.status == ("failed" if bad_reference == "invalid" else "queued")
        assert session.scalar(select(func.count(JobObservation.id))) == 0
        heartbeat = session.get(WorkerHeartbeat, f"{bad_reference}-worker")
        assert heartbeat is not None
        assert set(heartbeat.supported_kinds) == {
            "discover_contacts",
            "legacy_hunt",
            scan_worker.SCAN_JOB_KIND,
        }
