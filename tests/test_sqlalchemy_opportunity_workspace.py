"""Transaction and privacy tests for the concrete opportunity workspace."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, func, select

import job_hunt_agent.sqlalchemy_opportunity_workspace as workspace_module
from job_hunt_agent.database import Database
from job_hunt_agent.job_queue import record_worker_heartbeat
from job_hunt_agent.models import (
    ActionItem,
    Application,
    ApplicationActivityEvent,
    BackgroundJob,
    Base,
    CareerTrack,
    OpportunityScan,
    OpportunityScanSource,
    OpportunityDecisionEvent,
    Owner,
    OwnerMutationReceipt,
    ResumeVersion,
    SavedSearch,
    WorkerHeartbeat,
)
from job_hunt_agent.application_repository import ApplicationRepositoryError
from job_hunt_agent.opportunity_repository import (
    OpportunityNotFound,
    persist_scan_source_role,
)
from job_hunt_agent.opportunity_schemas import (
    OpportunityDecisionRequest,
    PursueOpportunityRequest,
    ScanCreateRequest,
    TodayQuery,
)
from job_hunt_agent.private_payloads import encrypt_private_payload
from job_hunt_agent.owner_workspace import (
    WorkspaceCapabilityUnavailable,
    WorkspaceConflict,
    WorkspaceInputError,
    WorkspaceNotFound,
    WorkspaceUnavailable,
)
from job_hunt_agent.repository_errors import ResourceConflict, VersionConflict
from job_hunt_agent.schemas import Company, CompanySource, EmploymentType, Role
from job_hunt_agent.security import DataKeyring
from job_hunt_agent.sources.registry import CompanyRegistry, RegistryError
from job_hunt_agent.sqlalchemy_opportunity_workspace import (
    SqlAlchemyOpportunityWorkspaceStore,
)


NOW = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def opportunity_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Database, SqlAlchemyOpportunityWorkspaceStore]:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'workspace.db'}")
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.delenv("APP_VERSION", raising=False)
    Base.metadata.create_all(database.engine)
    keyring = DataKeyring([("test-v1", Fernet.generate_key().decode("ascii"))])
    with database.session() as session:
        _seed_search(
            session,
            owner_id="owner-a",
            search_id="search-a",
            version=3,
            keyring=keyring,
        )
        session.add(Owner(id="owner-b", display_name="Owner B", timezone="UTC"))
        record_worker_heartbeat(
            session,
            worker_id="role-scan-worker",
            supported_kinds={"scan_saved_search"},
            now=datetime.now(timezone.utc),
        )
    registry = CompanyRegistry(
        [
            _company("acme", CompanySource.greenhouse),
            _company("beta", CompanySource.lever),
        ],
        name="test-pack",
    )
    monkeypatch.setattr(
        workspace_module,
        "load_company_pack",
        lambda pack: registry if pack == "backend_india" else None,
    )
    try:
        yield database, SqlAlchemyOpportunityWorkspaceStore(database, keyring)
    finally:
        database.dispose()


def _company(slug: str, source: CompanySource) -> Company:
    return Company(
        name=slug.title(),
        slug=slug,
        source=source,
        source_token=slug,
        careers_domains=[f"{slug}.example"],
        hire_locations=["India"],
        tags=["backend"],
        active=True,
    )


def _seed_search(
    session,
    *,
    owner_id: str,
    search_id: str,
    version: int,
    keyring: DataKeyring,
) -> None:
    session.add(Owner(id=owner_id, display_name="Owner A", timezone="Asia/Kolkata"))
    session.add(
        CareerTrack(
            id=f"track-{owner_id}",
            owner_id=owner_id,
            name="Backend growth",
            role_families=["Backend Engineer"],
            seniority_levels=["senior"],
            target_locations=["Remote India"],
            priorities={},
            active=True,
            version=1,
        )
    )
    resume_id = f"resume-{owner_id}"
    resume_envelope = encrypt_private_payload(
        keyring,
        record_kind="resume_version",
        owner_id=owner_id,
        record_id=resume_id,
        payload={
            "content": (
                "Backend software engineer building reliable distributed systems, "
                "Python services, REST APIs, AWS, Docker, and PostgreSQL."
            )
        },
    )
    session.add(
        ResumeVersion(
            id=resume_id,
            owner_id=owner_id,
            label="Base resume",
            encrypted_content=resume_envelope.ciphertext,
            encryption_key_id=resume_envelope.key_id,
            content_hash="a" * 64,
            source="pasted",
            is_base=True,
            version=1,
        )
    )
    session.add(
        SavedSearch(
            id=search_id,
            owner_id=owner_id,
            career_track_id=f"track-{owner_id}",
            resume_version_id=resume_id,
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
            pack="backend_india",
            use_self_rag=True,
            cadence="manual",
            schedule={"local_time": None, "days_of_week": []},
            timezone="Asia/Kolkata",
            active=True,
            next_scan_at=None,
            version=version,
        )
    )


def _create_scan(store: SqlAlchemyOpportunityWorkspaceStore, *, key: str = "scan-1"):
    return store.create_scan(
        owner_id="owner-a",
        saved_search_id="search-a",
        expected_saved_search_version=3,
        idempotency_key=key,
        payload=ScanCreateRequest(),
    )


def _persist_opportunity(
    database: Database,
    store: SqlAlchemyOpportunityWorkspaceStore,
) -> tuple[str, str]:
    scan = _create_scan(store)
    with database.session() as session:
        source = session.scalar(
            select(OpportunityScanSource).where(
                OpportunityScanSource.opportunity_scan_id == scan.id,
                OpportunityScanSource.company_slug == "acme",
            )
        )
        assert source is not None
        source.status = "running"
        source.started_at = NOW
        source.observed_count = 1
        source.returned_count = 1
        session.flush()
        result = persist_scan_source_role(
            session,
            owner_id="owner-a",
            scan_source_id=source.id,
            role=Role(
                company="Acme",
                company_slug="acme",
                source_job_id="GH-123",
                title="Senior Backend Engineer",
                url="https://acme.example/jobs/GH-123",
                location="Remote India",
                summary="Build reliable backend systems.",
                match_reason="PRIVATE MATCH PROSE FROM RESUME",
                source=CompanySource.greenhouse,
                apply_urls=["https://acme.example/jobs/GH-123"],
                posted_at=None,
                employment_type=EmploymentType.unknown,
                raw_description="Design and operate reliable backend systems.",
            ),
            first_party_url_verified=True,
            now=NOW,
        )
        return result.opportunity_id, scan.id


def test_create_scan_snapshots_criteria_partitions_pack_and_enqueues_ids_only(
    opportunity_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, store = opportunity_workspace
    monkeypatch.setattr(workspace_module, "utcnow", lambda: NOW)

    created = _create_scan(store)

    assert created.status.value == "queued"
    assert created.stage.value == "queued"
    assert created.saved_search_version == 3
    assert created.counts.sources_total == 2
    assert created.counts.sources_completed == 0
    assert created.warnings == []

    with database.session() as session:
        scan = session.get(OpportunityScan, created.id)
        assert scan is not None
        assert scan.criteria_snapshot["role_keywords"] == ["backend", "platform"]
        assert scan.background_job_id is not None
        sources = list(
            session.scalars(
                select(OpportunityScanSource)
                .where(OpportunityScanSource.opportunity_scan_id == scan.id)
                .order_by(OpportunityScanSource.company_slug)
            )
        )
        assert [(row.company_slug, row.source, row.status) for row in sources] == [
            ("acme", "greenhouse", "pending"),
            ("beta", "lever", "pending"),
        ]
        assert all(
            row.fetch_scope == "criteria_filtered" and row.completeness == "unknown"
            for row in sources
        )
        job = session.get(BackgroundJob, scan.background_job_id)
        assert job is not None
        assert job.kind == "scan_saved_search"
        assert job.owner_id == "owner-a"
        assert job.subject_type == "opportunity_scan"
        assert job.subject_id == scan.id
        assert job.payload == {
            "opportunity_scan_id": scan.id,
            "saved_search_id": "search-a",
            "saved_search_version": 3,
        }
        durable_scan = json.dumps(
            {
                "criteria": scan.criteria_snapshot,
                "dedupe_key": scan.dedupe_key,
                "request_hash": scan.request_hash,
                "payload": job.payload,
            },
            sort_keys=True,
        )
        assert "PRIVATE RESUME" not in durable_scan
        assert "resume_text" not in durable_scan
        assert session.scalar(select(func.count(OwnerMutationReceipt.id))) == 1


def test_create_scan_replays_once_and_changed_request_or_stale_version_conflicts(
    opportunity_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
) -> None:
    database, store = opportunity_workspace
    created = _create_scan(store)
    replay = _create_scan(store)
    assert replay.id == created.id

    with database.session() as session:
        assert session.scalar(select(func.count(OpportunityScan.id))) == 1
        assert session.scalar(select(func.count(OpportunityScanSource.id))) == 2
        assert session.scalar(select(func.count(BackgroundJob.id))) == 1

    with pytest.raises(WorkspaceConflict) as changed:
        store.create_scan(
            owner_id="owner-a",
            saved_search_id="search-a",
            expected_saved_search_version=4,
            idempotency_key="scan-1",
            payload=ScanCreateRequest(),
        )
    assert changed.value.code == "idempotency_conflict"

    with pytest.raises(WorkspaceConflict) as stale:
        store.create_scan(
            owner_id="owner-a",
            saved_search_id="search-a",
            expected_saved_search_version=4,
            idempotency_key="scan-stale",
            payload=ScanCreateRequest(),
        )
    assert stale.value.code == "version_conflict"


def test_create_scan_rejects_missing_worker_before_any_mutation(
    opportunity_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
) -> None:
    database, store = opportunity_workspace
    with database.session() as session:
        session.execute(delete(WorkerHeartbeat))

    with pytest.raises(WorkspaceCapabilityUnavailable) as unavailable:
        _create_scan(store, key="no-worker")

    assert unavailable.value.capability == "role_scan"
    assert unavailable.value.reason == "no_fresh_worker"
    with database.session() as session:
        assert session.scalar(select(func.count(OpportunityScan.id))) == 0
        assert session.scalar(select(func.count(OpportunityScanSource.id))) == 0
        assert session.scalar(select(func.count(BackgroundJob.id))) == 0
        assert session.scalar(select(func.count(OwnerMutationReceipt.id))) == 0


@pytest.mark.parametrize(
    ("supported_kinds", "last_seen_delta", "expected_reason"),
    [
        (["legacy_hunt"], timedelta(seconds=0), "unsupported_kind"),
        (["scan_saved_search"], timedelta(seconds=-120), "no_fresh_worker"),
    ],
)
def test_create_scan_rejects_incapable_or_stale_workers(
    opportunity_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
    supported_kinds: list[str],
    last_seen_delta: timedelta,
    expected_reason: str,
) -> None:
    database, store = opportunity_workspace
    with database.session() as session:
        heartbeat = session.get(WorkerHeartbeat, "role-scan-worker")
        assert heartbeat is not None
        heartbeat.supported_kinds = supported_kinds
        heartbeat.last_seen_at = datetime.now(timezone.utc) + last_seen_delta

    with pytest.raises(WorkspaceCapabilityUnavailable) as unavailable:
        _create_scan(store, key=f"unavailable-{expected_reason}")
    assert unavailable.value.reason == expected_reason


def test_create_scan_requires_a_worker_from_the_current_build(
    opportunity_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, store = opportunity_workspace
    monkeypatch.setenv("APP_VERSION", "web-build")
    with database.session() as session:
        heartbeat = session.get(WorkerHeartbeat, "role-scan-worker")
        assert heartbeat is not None
        heartbeat.build_version = "older-worker-build"

    with pytest.raises(WorkspaceCapabilityUnavailable) as unavailable:
        _create_scan(store, key="wrong-build")
    assert unavailable.value.reason == "incompatible_build"

    with database.session() as session:
        heartbeat = session.get(WorkerHeartbeat, "role-scan-worker")
        assert heartbeat is not None
        heartbeat.build_version = "web-build"
        heartbeat.last_seen_at = datetime.now(timezone.utc)

    created = _create_scan(store, key="matching-build")
    assert created.status.value == "queued"


def test_completed_scan_replay_does_not_require_a_live_worker(
    opportunity_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
) -> None:
    database, store = opportunity_workspace
    created = _create_scan(store, key="replay-without-worker")
    with database.session() as session:
        session.execute(delete(WorkerHeartbeat))

    replay = _create_scan(store, key="replay-without-worker")
    assert replay.id == created.id
    with database.session() as session:
        assert session.scalar(select(func.count(OpportunityScan.id))) == 1
        assert session.scalar(select(func.count(BackgroundJob.id))) == 1


def test_scan_creation_requires_owned_active_search_and_masks_pack_failure(
    opportunity_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, store = opportunity_workspace
    with pytest.raises(WorkspaceNotFound):
        store.create_scan(
            owner_id="owner-b",
            saved_search_id="search-a",
            expected_saved_search_version=3,
            idempotency_key="foreign",
            payload=ScanCreateRequest(),
        )

    with database.session() as session:
        search = session.get(SavedSearch, "search-a")
        assert search is not None
        search.active = False
        search.version += 1
    with pytest.raises(WorkspaceConflict) as inactive:
        store.create_scan(
            owner_id="owner-a",
            saved_search_id="search-a",
            expected_saved_search_version=4,
            idempotency_key="inactive",
            payload=ScanCreateRequest(),
        )
    assert inactive.value.code == "inactive_saved_search"

    with database.session() as session:
        search = session.get(SavedSearch, "search-a")
        assert search is not None
        search.active = True
        search.version += 1
    monkeypatch.setattr(
        workspace_module,
        "load_company_pack",
        lambda _pack: (_ for _ in ()).throw(
            RegistryError("PRIVATE_PATH /secret/config.yaml")
        ),
    )
    with pytest.raises(WorkspaceInputError) as unavailable:
        store.create_scan(
            owner_id="owner-a",
            saved_search_id="search-a",
            expected_saved_search_version=5,
            idempotency_key="invalid-pack",
            payload=ScanCreateRequest(),
        )
    assert unavailable.value.field == "pack"
    assert "PRIVATE_PATH" not in str(unavailable.value)

    with database.session() as session:
        assert session.scalar(select(func.count(OpportunityScan.id))) == 0
        assert session.scalar(select(func.count(BackgroundJob.id))) == 0
        assert session.scalar(select(func.count(OwnerMutationReceipt.id))) == 0


def test_get_scan_projects_fixed_safe_warnings_without_raw_source_errors(
    opportunity_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
) -> None:
    database, store = opportunity_workspace
    created = _create_scan(store)
    with database.session() as session:
        scan = session.get(OpportunityScan, created.id)
        assert scan is not None
        completed_at = scan.created_at
        scan.status = "failed"
        scan.stage = "PRIVATE provider stage"
        scan.started_at = completed_at
        scan.finalized_at = completed_at
        scan.terminal_source_count = 2
        scan.failed_source_count = 2
        scan.version += 1
        for index, source in enumerate(
            session.scalars(
                select(OpportunityScanSource).where(
                    OpportunityScanSource.opportunity_scan_id == scan.id
                )
            )
        ):
            source.status = "failed"
            source.started_at = completed_at
            source.completed_at = completed_at
            source.error_code = (
                "source_timeout" if index == 0 else "PRIVATE_TOKEN raw provider body"
            )
            source.warning_codes = ["PRIVATE_INTERNAL_WARNING"]
            source.version += 1

    response = store.get_scan(owner_id="owner-a", scan_id=created.id)
    assert response is not None
    assert response.status.value == "failed"
    assert response.stage.value == "complete"
    assert response.counts.sources_completed == 2
    assert response.counts.sources_failed == 2
    assert response.counts.sources_degraded == 0
    assert {warning.code.value for warning in response.warnings} >= {
        "source_timeout",
        "source_unavailable",
        "source_incomplete",
    }
    serialized = response.model_dump_json()
    assert "PRIVATE_TOKEN" not in serialized
    assert "PRIVATE_INTERNAL_WARNING" not in serialized
    assert "raw provider body" not in serialized
    assert "provider stage" not in serialized

    assert store.get_scan(owner_id="owner-b", scan_id=created.id) is None
    assert store.get_scan(owner_id="owner-a", scan_id="missing-scan") is None


def test_today_and_detail_delegate_to_database_only_repository_projections(
    opportunity_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, store = opportunity_workspace
    opportunity_id, scan_id = _persist_opportunity(database, store)
    monkeypatch.setattr(
        workspace_module,
        "load_company_pack",
        lambda _pack: (_ for _ in ()).throw(AssertionError("provider path called")),
    )

    today = store.list_today(owner_id="owner-a", query=TodayQuery())
    detail = store.get_opportunity(
        owner_id="owner-a", opportunity_id=opportunity_id
    )
    scan = store.get_scan(owner_id="owner-a", scan_id=scan_id)

    assert today.data_source == "database"
    assert [item.id for item in today.items] == [opportunity_id]
    assert today.items[0].posting.summary == "Build reliable backend systems."
    assert today.items[0].match.state.value == "assessed"
    assert today.items[0].match.assessment_saved_search_id == "search-a"
    assert today.items[0].match.resume_version_id == "resume-owner-a"
    assert detail is not None and detail.data_source == "database"
    assert detail.match == today.items[0].match
    assert detail.description == "Design and operate reliable backend systems."
    assert detail.apply_urls == ["https://acme.example/jobs/GH-123"]
    assert "PRIVATE MATCH PROSE" not in detail.model_dump_json()
    assert scan is not None
    assert scan.counts.observed_postings == 1
    assert scan.counts.matched_postings == 1

    foreign_today = store.list_today(owner_id="owner-b", query=TodayQuery())
    assert foreign_today.items == []
    assert store.get_opportunity(
        owner_id="owner-b", opportunity_id=opportunity_id
    ) is None


def test_decision_delegation_maps_not_found_versions_and_idempotency_and_encrypts_note(
    opportunity_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
) -> None:
    database, store = opportunity_workspace
    opportunity_id, _scan_id = _persist_opportunity(database, store)

    watched = store.decide_opportunity(
        owner_id="owner-a",
        opportunity_id=opportunity_id,
        expected_version=1,
        idempotency_key="watch-1",
        payload=OpportunityDecisionRequest(action="watch"),
    )
    assert watched.state.value == "watch"
    assert watched.opportunity_version == 2
    replay = store.decide_opportunity(
        owner_id="owner-a",
        opportunity_id=opportunity_id,
        expected_version=1,
        idempotency_key="watch-1",
        payload=OpportunityDecisionRequest(action="watch"),
    )
    assert replay == watched

    with pytest.raises(WorkspaceConflict) as reused:
        store.decide_opportunity(
            owner_id="owner-a",
            opportunity_id=opportunity_id,
            expected_version=2,
            idempotency_key="watch-1",
            payload=OpportunityDecisionRequest(
                action="dismiss",
                dismiss_reason="not_relevant",
            ),
        )
    assert reused.value.code == "idempotency_conflict"

    with pytest.raises(WorkspaceConflict) as stale:
        store.decide_opportunity(
            owner_id="owner-a",
            opportunity_id=opportunity_id,
            expected_version=1,
            idempotency_key="stale-decision",
            payload=OpportunityDecisionRequest(
                action="dismiss",
                dismiss_reason="not_relevant",
            ),
        )
    assert stale.value.code == "version_conflict"

    dismissed = store.decide_opportunity(
        owner_id="owner-a",
        opportunity_id=opportunity_id,
        expected_version=2,
        idempotency_key="dismiss-1",
        payload=OpportunityDecisionRequest(
            action="dismiss",
            dismiss_reason="other",
            note="PRIVATE DECISION NOTE",
        ),
    )
    assert dismissed.state.value == "dismiss"
    assert dismissed.event.note == "PRIVATE DECISION NOTE"
    with database.session() as session:
        row = session.scalar(
            select(OpportunityDecisionEvent).where(
                OpportunityDecisionEvent.id == dismissed.event.id
            )
        )
        assert row is not None and row.encrypted_note is not None
        assert "PRIVATE DECISION NOTE" not in row.encrypted_note
        row.encrypted_note = "invalid-encrypted-note"

    with pytest.raises(WorkspaceUnavailable) as corrupted:
        store.get_opportunity(
            owner_id="owner-a",
            opportunity_id=opportunity_id,
        )
    assert "PRIVATE DECISION NOTE" not in str(corrupted.value)

    with pytest.raises(WorkspaceNotFound):
        store.decide_opportunity(
            owner_id="owner-b",
            opportunity_id=opportunity_id,
            expected_version=3,
            idempotency_key="foreign-decision",
            payload=OpportunityDecisionRequest(action="watch"),
        )


def test_decision_dispatches_pursuit_to_the_atomic_application_boundary_only(
    opportunity_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database, store = opportunity_workspace
    pursue_result = object()
    ordinary_result = object()
    calls: list[tuple[str, object]] = []

    def fake_pursue(session, **kwargs):
        calls.append(("pursue", session))
        assert kwargs["owner_id"] == "owner-a"
        assert kwargs["opportunity_id"] == "opportunity-a"
        assert kwargs["expected_version"] == 4
        assert kwargs["idempotency_key"] == "pursue-1"
        assert kwargs["request"] == PursueOpportunityRequest(
            initial_action_due_on=date(2026, 7, 15),
            acquisition_source="job_hunt_search",
            selected_saved_search_id="search-a",
        )
        return pursue_result

    def fake_ordinary(session, **kwargs):
        calls.append(("ordinary", session))
        assert kwargs["owner_id"] == "owner-a"
        assert kwargs["opportunity_id"] == "opportunity-a"
        assert kwargs["expected_version"] == 4
        assert kwargs["idempotency_key"] == "watch-1"
        assert kwargs["request"] == OpportunityDecisionRequest(action="watch")
        assert kwargs["keyring"] is store.keyring
        return ordinary_result

    monkeypatch.setattr(workspace_module, "pursue_owner_opportunity", fake_pursue)
    monkeypatch.setattr(workspace_module, "decide_owner_opportunity", fake_ordinary)

    pursued = store.decide_opportunity(
        owner_id="owner-a",
        opportunity_id="opportunity-a",
        expected_version=4,
        idempotency_key="pursue-1",
        payload=OpportunityDecisionRequest(
            action="pursue",
            initial_action_due_on=date(2026, 7, 15),
            acquisition_source="job_hunt_search",
            selected_saved_search_id="search-a",
        ),
    )
    watched = store.decide_opportunity(
        owner_id="owner-a",
        opportunity_id="opportunity-a",
        expected_version=4,
        idempotency_key="watch-1",
        payload=OpportunityDecisionRequest(action="watch"),
    )

    assert pursued is pursue_result
    assert watched is ordinary_result
    assert [kind for kind, _session in calls] == ["pursue", "ordinary"]
    assert calls[0][1] is not calls[1][1]


def test_pursuit_dispatch_atomically_persists_one_graph_and_maps_key_conflicts(
    opportunity_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
) -> None:
    database, store = opportunity_workspace
    opportunity_id, _scan_id = _persist_opportunity(database, store)
    local_today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    due_on = local_today + timedelta(days=1)

    pursued = store.decide_opportunity(
        owner_id="owner-a",
        opportunity_id=opportunity_id,
        expected_version=1,
        idempotency_key="pursue-atomic",
        payload=OpportunityDecisionRequest(
            action="pursue",
            initial_action_due_on=due_on,
        ),
    )

    assert pursued.state.value == "pursued"
    assert pursued.opportunity_version == 2
    assert pursued.pursuit is not None
    assert pursued.pursuit.application_created is True
    assert pursued.pursuit.application.current_action.due_on == due_on
    with database.session() as session:
        assert session.scalar(select(func.count(Application.id))) == 1
        assert session.scalar(select(func.count(ActionItem.id))) == 1
        assert session.scalar(select(func.count(ApplicationActivityEvent.id))) == 1
        assert session.scalar(select(func.count(OpportunityDecisionEvent.id))) == 1

    with pytest.raises(WorkspaceConflict) as changed_request:
        store.decide_opportunity(
            owner_id="owner-a",
            opportunity_id=opportunity_id,
            expected_version=2,
            idempotency_key="pursue-atomic",
            payload=OpportunityDecisionRequest(
                action="pursue",
                initial_action_due_on=due_on + timedelta(days=1),
            ),
        )
    assert changed_request.value.code == "idempotency_conflict"


def test_pursuit_repository_errors_map_to_workspace_contracts(
    opportunity_workspace: tuple[Database, SqlAlchemyOpportunityWorkspaceStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database, store = opportunity_workspace
    payload = OpportunityDecisionRequest(action="pursue")

    def pursue_with(error: Exception):
        def fail(*_args, **_kwargs):
            raise error

        monkeypatch.setattr(workspace_module, "pursue_owner_opportunity", fail)
        return lambda: store.decide_opportunity(
            owner_id="owner-a",
            opportunity_id="opportunity-a",
            expected_version=4,
            idempotency_key="pursue-1",
            payload=payload,
        )

    with pytest.raises(WorkspaceNotFound) as missing:
        pursue_with(OpportunityNotFound("PRIVATE_FOREIGN_OPPORTUNITY"))()
    assert str(missing.value) == "opportunity not found"

    with pytest.raises(WorkspaceConflict) as stale:
        pursue_with(VersionConflict("opportunity", "opportunity-a", 4, 5))()
    assert stale.value.code == "version_conflict"

    with pytest.raises(WorkspaceConflict) as conflict:
        pursue_with(ResourceConflict("closed postings cannot be pursued"))()
    assert conflict.value.code == "resource_conflict"

    with pytest.raises(WorkspaceInputError):
        pursue_with(ValueError("initial action due date is out of range"))()

    with pytest.raises(WorkspaceUnavailable) as inconsistent:
        pursue_with(ApplicationRepositoryError("PRIVATE_BROKEN_GRAPH"))()
    assert str(inconsistent.value) == "application data is inconsistent"
