"""Data-layer tests for the first practical owner-workspace vertical slice."""

from __future__ import annotations

from datetime import datetime, time, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    AchievementEvidence,
    BackgroundJob,
    Base,
    CandidateProfile,
    CareerTrack,
    Owner,
    OwnerMutationReceipt,
    ResumeVersion,
    SavedSearch,
)
from job_hunt_agent.owner_workspace import (
    WorkspaceConflict,
    WorkspaceInputError,
    WorkspaceUnavailable,
)
from job_hunt_agent.private_payloads import encrypt_private_payload
from job_hunt_agent.profile_schemas import (
    AchievementEvidenceCreate,
    AchievementEvidencePatch,
    CandidateProfileWrite,
    CareerTrackCreate,
    ResumeVersionCreate,
    SavedSearchCreate,
    SavedSearchCriteria,
    SavedSearchPatch,
    SavedSearchSchedule,
    WorkAuthorization,
)
from job_hunt_agent.saved_search_repository import calculate_next_scan_at
from job_hunt_agent.security import DataKeyring
from job_hunt_agent.sqlalchemy_owner_workspace import SqlAlchemyOwnerWorkspaceStore


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Database, SqlAlchemyOwnerWorkspaceStore]:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'workspace.db'}")
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        session.add_all(
            [
                Owner(id="owner-a", display_name="Owner A", timezone="Asia/Kolkata"),
                Owner(id="owner-b", display_name="Owner B", timezone="UTC"),
            ]
        )
    keyring = DataKeyring([("test-v1", Fernet.generate_key().decode("ascii"))])
    try:
        yield database, SqlAlchemyOwnerWorkspaceStore(database, keyring)
    finally:
        database.dispose()


def _profile(marker: str = "PRIVATE CAREER THESIS") -> CandidateProfileWrite:
    return CandidateProfileWrite(
        career_thesis=marker,
        current_title="Backend Engineer",
        current_location="Hyderabad",
        work_authorizations=[WorkAuthorization(country_code="IN", status="citizen")],
        work_modes=["remote", "hybrid"],
        employment_types=["full_time"],
        notice_period_days=30,
        onboarding_step="profile",
    )


def _track() -> CareerTrackCreate:
    return CareerTrackCreate(
        name="Backend Growth",
        role_families=["Backend Engineer", "Platform Engineer"],
        seniority_levels=["senior", "staff"],
        target_locations=["Hyderabad", "Remote India"],
    )


def _criteria() -> SavedSearchCriteria:
    return SavedSearchCriteria(
        role_keywords=["backend", "platform"],
        seniority="senior",
        location=["Hyderabad", "Remote India"],
        comp_min_lpa=30,
        comp_max_lpa=60,
        employment_types=["full_time"],
        max_age_days=45,
        country="in",
    )


def _daily_schedule() -> SavedSearchSchedule:
    return SavedSearchSchedule(
        cadence="daily",
        timezone="Asia/Kolkata",
        local_time=time(8, 30),
    )


def _build_core(
    store: SqlAlchemyOwnerWorkspaceStore,
) -> tuple[object, object, object]:
    resume = store.create_resume_version(
        owner_id="owner-a",
        payload=ResumeVersionCreate(
            label="Base Resume",
            content="PRIVATE RESUME: Built distributed backend systems at scale.",
            source="pasted",
        ),
        idempotency_key="resume-create-1",
    )
    track = store.create_career_track(
        owner_id="owner-a",
        payload=_track(),
        idempotency_key="track-create-1",
    )
    search = store.create_saved_search(
        owner_id="owner-a",
        payload=SavedSearchCreate(
            name="Daily backend roles",
            career_track_id=track.id,
            resume_version_id=None,
            criteria=_criteria(),
            schedule=_daily_schedule(),
            pack="backend_india",
            use_self_rag=True,
            active=True,
        ),
        idempotency_key="search-create-1",
    )
    return resume, track, search


def test_practical_profile_resume_track_search_vertical_path(
    workspace: tuple[Database, SqlAlchemyOwnerWorkspaceStore],
) -> None:
    database, store = workspace
    profile = store.put_profile(owner_id="owner-a", payload=_profile(), expected_version=0)
    resume, track, search = _build_core(store)

    assert resume.is_base
    assert search.resume_version_id == resume.id
    assert search.next_scan_at is not None
    loaded_profile = store.get_profile(owner_id="owner-a")
    assert loaded_profile is not None and loaded_profile.base_resume is not None
    assert loaded_profile.career_thesis == "PRIVATE CAREER THESIS"
    assert store.get_profile(owner_id="owner-b") is None
    assert store.get_resume_version(
        owner_id="owner-b", resume_version_id=resume.id
    ) is None
    assert store.get_career_track(
        owner_id="owner-b", career_track_id=track.id
    ) is None
    assert store.get_saved_search(
        owner_id="owner-b", saved_search_id=search.id
    ) is None

    hunt_input = store.build_hunt_input(owner_id="owner-a", saved_search_id=search.id)
    assert hunt_input is not None and hunt_input.ready
    assert hunt_input.input is not None
    assert "PRIVATE RESUME" in hunt_input.input.resume_text
    assert hunt_input.input.criteria.comp_min_lpa == 30

    renamed = store.patch_saved_search(
        owner_id="owner-a",
        saved_search_id=search.id,
        payload=SavedSearchPatch(name="Renamed backend roles"),
        expected_version=search.version,
    )
    assert renamed.next_scan_at == search.next_scan_at

    with database.session() as session:
        assert session.scalar(select(func.count(BackgroundJob.id))) == 0
        raw_profile = session.get(CandidateProfile, profile.id)
        raw_resume = session.get(ResumeVersion, resume.id)
        assert raw_profile is not None
        assert "PRIVATE CAREER THESIS" not in raw_profile.encrypted_payload
        assert raw_resume is not None
        assert "PRIVATE RESUME" not in raw_resume.encrypted_content
        assert session.scalar(select(func.count(OwnerMutationReceipt.id))) == 3


def test_legacy_blank_profile_remains_readable_after_write_validation_tightens(
    workspace: tuple[Database, SqlAlchemyOwnerWorkspaceStore],
) -> None:
    database, store = workspace
    profile_id = "legacyblankprofile"
    envelope = encrypt_private_payload(
        store.keyring,
        record_kind="candidate_profile",
        owner_id="owner-a",
        record_id=profile_id,
        payload={},
    )
    with database.session() as session:
        session.add(
            CandidateProfile(
                id=profile_id,
                owner_id="owner-a",
                encrypted_payload=envelope.ciphertext,
                encryption_key_id=envelope.key_id,
                onboarding_state="profile",
                version=1,
            )
        )

    loaded = store.get_profile(owner_id="owner-a")
    assert loaded is not None
    assert loaded.id == profile_id
    assert loaded.current_title is None
    assert loaded.employment_types == ["full_time"]


def test_idempotency_replay_conflict_versions_and_restrict_delete(
    workspace: tuple[Database, SqlAlchemyOwnerWorkspaceStore],
) -> None:
    _database, store = workspace
    store.put_profile(owner_id="owner-a", payload=_profile(), expected_version=0)
    resume, track, search = _build_core(store)

    replay = store.create_resume_version(
        owner_id="owner-a",
        payload=ResumeVersionCreate(
            label="Base Resume",
            content="PRIVATE RESUME: Built distributed backend systems at scale.",
            source="pasted",
        ),
        idempotency_key="resume-create-1",
    )
    assert replay.id == resume.id
    with pytest.raises(WorkspaceConflict) as conflict:
        store.create_resume_version(
            owner_id="owner-a",
            payload=ResumeVersionCreate(
                label="Changed",
                content="A different private resume",
                source="pasted",
            ),
            idempotency_key="resume-create-1",
        )
    assert conflict.value.code == "idempotency_conflict"

    with pytest.raises(WorkspaceConflict) as stale:
        store.patch_saved_search(
            owner_id="owner-a",
            saved_search_id=search.id,
            payload=SavedSearchPatch(name="stale update"),
            expected_version=search.version + 9,
        )
    assert stale.value.code == "version_conflict"
    with pytest.raises(WorkspaceConflict) as in_use:
        store.delete_career_track(
            owner_id="owner-a",
            career_track_id=track.id,
            expected_version=track.version,
        )
    assert in_use.value.code == "resource_in_use"


def test_evidence_requires_approval_and_material_edit_resets_it(
    workspace: tuple[Database, SqlAlchemyOwnerWorkspaceStore],
) -> None:
    _database, store = workspace
    resume, _track_record, _search = _build_core(store)
    evidence = store.create_evidence(
        owner_id="owner-a",
        payload=AchievementEvidenceCreate(
            statement="Built distributed backend systems",
            source_resume_version_id=resume.id,
            source_excerpt="Built distributed backend systems",
            skills=["Python", "Distributed systems"],
            origin="resume_suggestion",
        ),
        idempotency_key="evidence-create-1",
    )
    assert evidence.approval_state == "pending"
    approved = store.patch_evidence(
        owner_id="owner-a",
        evidence_id=evidence.id,
        payload=AchievementEvidencePatch(approval_state="approved"),
        expected_version=evidence.version,
    )
    assert approved.approval_state == "approved" and approved.approved_at is not None
    assert len(store.list_evidence(owner_id="owner-a", approval_state="approved").items) == 1

    edited = store.patch_evidence(
        owner_id="owner-a",
        evidence_id=evidence.id,
        payload=AchievementEvidencePatch(statement="Built resilient distributed systems"),
        expected_version=approved.version,
    )
    assert edited.approval_state == "pending"
    assert edited.approved_at is None and edited.retired_at is None
    assert store.list_evidence(owner_id="owner-a", approval_state="approved").items == []
    with pytest.raises(WorkspaceConflict):
        store.patch_evidence(
            owner_id="owner-a",
            evidence_id=evidence.id,
            payload=AchievementEvidencePatch(
                statement="Unreviewed material change",
                approval_state="approved",
            ),
            expected_version=edited.version,
        )
    reapproved = store.patch_evidence(
        owner_id="owner-a",
        evidence_id=evidence.id,
        payload=AchievementEvidencePatch(approval_state="approved"),
        expected_version=edited.version,
    )
    retired = store.patch_evidence(
        owner_id="owner-a",
        evidence_id=evidence.id,
        payload=AchievementEvidencePatch(approval_state="retired"),
        expected_version=reapproved.version,
    )
    assert retired.approval_state == "retired" and retired.retired_at is not None
    assert store.list_evidence(owner_id="owner-a", approval_state="approved").items == []
    with pytest.raises(WorkspaceInputError):
        store.create_evidence(
            owner_id="owner-a",
            payload=AchievementEvidenceCreate(
                statement="Invented claim",
                source_resume_version_id=resume.id,
                source_excerpt="not in the resume",
                origin="resume_suggestion",
            ),
            idempotency_key="bad-evidence",
        )


def test_bound_ciphertext_swap_fails_closed(
    workspace: tuple[Database, SqlAlchemyOwnerWorkspaceStore],
) -> None:
    database, store = workspace
    first = store.create_resume_version(
        owner_id="owner-a",
        payload=ResumeVersionCreate(label="One", content="private resume one"),
        idempotency_key="one",
    )
    second = store.create_resume_version(
        owner_id="owner-a",
        payload=ResumeVersionCreate(label="Two", content="private resume two"),
        idempotency_key="two",
    )
    with database.session() as session:
        one = session.get(ResumeVersion, first.id)
        two = session.get(ResumeVersion, second.id)
        assert one is not None and two is not None
        one.encrypted_content, two.encrypted_content = (
            two.encrypted_content,
            one.encrypted_content,
        )
        one.encryption_key_id, two.encryption_key_id = (
            two.encryption_key_id,
            one.encryption_key_id,
        )
    with pytest.raises(WorkspaceUnavailable):
        store.get_resume_version(owner_id="owner-a", resume_version_id=first.id)


def test_sqlite_enforces_composite_owner_foreign_keys(
    workspace: tuple[Database, SqlAlchemyOwnerWorkspaceStore],
) -> None:
    database, store = workspace
    resume, track, _search = _build_core(store)
    with pytest.raises(IntegrityError):
        with database.session() as session:
            session.add(
                SavedSearch(
                    owner_id="owner-b",
                    career_track_id=track.id,
                    resume_version_id=resume.id,
                    name="Cross-owner corruption",
                    criteria_schema_version=1,
                    criteria=_criteria().model_dump(mode="json"),
                    pack="backend_india",
                    use_self_rag=True,
                    cadence="manual",
                    schedule={"local_time": None, "days_of_week": []},
                    timezone="UTC",
                    active=False,
                    next_scan_at=None,
                    version=1,
                )
            )


def test_schedule_handles_dst_gaps_and_ambiguous_slots_once() -> None:
    spring = SavedSearchSchedule(
        cadence="daily",
        timezone="America/New_York",
        local_time=time(2, 30),
    )
    spring_next = calculate_next_scan_at(
        spring,
        after=datetime(2026, 3, 8, 5, 0, tzinfo=timezone.utc),
    )
    assert spring_next == datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc)

    fall = SavedSearchSchedule(
        cadence="daily",
        timezone="America/New_York",
        local_time=time(1, 30),
    )
    first_fold = calculate_next_scan_at(
        fall,
        after=datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc),
    )
    assert first_fold == datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)
    after_first = calculate_next_scan_at(fall, after=first_fold)
    assert after_first == datetime(2026, 11, 2, 6, 30, tzinfo=timezone.utc)


def test_owner_delete_cascades_complete_workspace_graph(
    workspace: tuple[Database, SqlAlchemyOwnerWorkspaceStore],
) -> None:
    database, store = workspace
    store.put_profile(owner_id="owner-a", payload=_profile(), expected_version=0)
    resume, _track_record, _search = _build_core(store)
    store.create_evidence(
        owner_id="owner-a",
        payload=AchievementEvidenceCreate(
            statement="Built distributed backend systems",
            source_resume_version_id=resume.id,
            source_excerpt="Built distributed backend systems",
            origin="resume_suggestion",
        ),
        idempotency_key="cascade-evidence",
    )
    with database.session() as session:
        owner = session.get(Owner, "owner-a")
        assert owner is not None
        session.delete(owner)
    with database.session() as session:
        for model in (
            CandidateProfile,
            CareerTrack,
            ResumeVersion,
            AchievementEvidence,
            SavedSearch,
            OwnerMutationReceipt,
        ):
            assert session.scalar(select(func.count(model.id))) == 0
        assert session.get(Owner, "owner-b") is not None
