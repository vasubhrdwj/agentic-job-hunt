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
    ResumeImport,
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
from job_hunt_agent.resume_ingestion import ParsedEvidence, ParsedResume
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


def test_legacy_profile_update_cannot_silently_erase_new_skills_field(
    workspace: tuple[Database, SqlAlchemyOwnerWorkspaceStore],
) -> None:
    _database, store = workspace
    created = store.put_profile(
        owner_id="owner-a",
        payload=CandidateProfileWrite(
            current_title="Backend Engineer",
            skills=["Python", "Kafka"],
        ),
        expected_version=0,
    )

    legacy_payload = CandidateProfileWrite.model_validate(
        {"current_title": "Platform Engineer"}
    )
    assert "skills" not in legacy_payload.model_fields_set
    preserved = store.put_profile(
        owner_id="owner-a",
        payload=legacy_payload,
        expected_version=created.version,
    )
    assert preserved.skills == ["Python", "Kafka"]

    cleared = store.put_profile(
        owner_id="owner-a",
        payload=CandidateProfileWrite(
            current_title="Platform Engineer",
            skills=[],
        ),
        expected_version=preserved.version,
    )
    assert cleared.skills == []


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


def _parsed_resume(
    *,
    content: str = (
        "PROFESSIONAL EXPERIENCE\n"
        "Software Engineer\n"
        "Jan 2024 - Present\n"
        "• Built a reliable event pipeline with retries and dead-letter handling."
    ),
) -> ParsedResume:
    excerpt = "• Built a reliable event pipeline with retries and dead-letter handling."
    return ParsedResume(
        content=content,
        sections=("experience", "skills"),
        current_title="Software Engineer",
        current_location=None,
        years_of_experience=2.5,
        evidence=(
            ParsedEvidence(
                statement=excerpt,
                source_excerpt=excerpt,
                skills=("Python",),
            ),
        )
        if excerpt in content
        else (),
        skills=("Python",),
        warnings=("Review the experience estimate.",),
        media_type="text/plain",
        page_count=None,
    )


def test_resume_upload_atomically_imports_profile_and_approved_grounding(
    workspace: tuple[Database, SqlAlchemyOwnerWorkspaceStore],
) -> None:
    database, store = workspace
    parsed = _parsed_resume()

    report = store.upload_resume_version(
        owner_id="owner-a",
        parsed=parsed,
        label="Vasu Backend Resume",
        set_as_base=True,
        idempotency_key="upload-resume-1",
    )

    assert report.resume_version.source == "uploaded"
    assert report.resume_version.is_base
    assert report.imported_profile_fields == [
        "current_title",
        "years_of_experience",
        "skills",
    ]
    assert report.missing_profile_fields == ["current_location"]
    assert report.achievement_suggestions_created == 1
    profile = store.get_profile(owner_id="owner-a")
    assert profile is not None
    assert profile.current_title == "Software Engineer"
    assert profile.years_of_experience == 2.5
    assert profile.skills == ["Python"]
    assert profile.onboarding_step == "career_track"
    evidence = store.list_evidence(owner_id="owner-a", approval_state="approved").items
    assert len(evidence) == 1
    assert evidence[0].statement == parsed.evidence[0].source_excerpt
    assert evidence[0].source_resume_version_id == report.resume_version.id

    replay = store.upload_resume_version(
        owner_id="owner-a",
        parsed=parsed,
        label="Vasu Backend Resume",
        set_as_base=True,
        idempotency_key="upload-resume-1",
    )
    assert replay.model_dump() == report.model_dump()
    assert len(store.list_evidence(owner_id="owner-a", approval_state=None).items) == 1

    with pytest.raises(WorkspaceConflict) as conflict:
        store.upload_resume_version(
            owner_id="owner-a",
            parsed=_parsed_resume(content="A different normalized resume body."),
            label="Vasu Backend Resume",
            set_as_base=True,
            idempotency_key="upload-resume-1",
        )
    assert conflict.value.code == "idempotency_conflict"

    other = store.upload_resume_version(
        owner_id="owner-b",
        parsed=parsed,
        label="Vasu Backend Resume",
        set_as_base=True,
        idempotency_key="upload-resume-1",
    )
    assert other.resume_version.id != report.resume_version.id

    with database.session() as session:
        raw_resume = session.get(ResumeVersion, report.resume_version.id)
        raw_profile = session.scalar(
            select(CandidateProfile).where(CandidateProfile.owner_id == "owner-a")
        )
        receipt = session.scalar(
            select(OwnerMutationReceipt).where(
                OwnerMutationReceipt.owner_id == "owner-a",
                OwnerMutationReceipt.namespace == "resume_version.upload",
            )
        )
        assert raw_resume is not None and raw_profile is not None and receipt is not None
        assert parsed.content not in raw_resume.encrypted_content
        assert "Software Engineer" not in raw_profile.encrypted_payload
        assert len(receipt.request_hash) == 64


def test_resume_upload_preserves_conflicting_profile_and_rolls_back_atomically(
    workspace: tuple[Database, SqlAlchemyOwnerWorkspaceStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, store = workspace
    store.put_profile(
        owner_id="owner-a",
        payload=_profile(),
        expected_version=0,
    )
    report = store.upload_resume_version(
        owner_id="owner-a",
        parsed=_parsed_resume(),
        label="Safe merge",
        set_as_base=True,
        idempotency_key="safe-profile-merge",
    )
    profile = store.get_profile(owner_id="owner-a")
    assert profile is not None
    assert profile.current_title == "Backend Engineer"
    assert profile.current_location == "Hyderabad"
    assert profile.years_of_experience == 2.5
    assert report.imported_profile_fields == ["years_of_experience", "skills"]
    assert any("current title" in warning for warning in report.warnings)

    import job_hunt_agent.sqlalchemy_owner_workspace as workspace_module

    def fail_evidence(*args: object, **kwargs: object) -> object:
        raise RuntimeError("simulated evidence write failure")

    monkeypatch.setattr(
        workspace_module,
        "create_approved_resume_evidence",
        fail_evidence,
    )
    with pytest.raises(RuntimeError, match="simulated evidence"):
        store.upload_resume_version(
            owner_id="owner-b",
            parsed=_parsed_resume(),
            label="Must roll back",
            set_as_base=True,
            idempotency_key="atomic-failure",
        )
    with database.session() as session:
        assert session.scalar(
            select(func.count(ResumeVersion.id)).where(ResumeVersion.owner_id == "owner-b")
        ) == 0
        assert session.scalar(
            select(func.count(CandidateProfile.id)).where(
                CandidateProfile.owner_id == "owner-b"
            )
        ) == 0
        assert session.scalar(
            select(func.count(OwnerMutationReceipt.id)).where(
                OwnerMutationReceipt.owner_id == "owner-b",
                OwnerMutationReceipt.namespace == "resume_version.upload",
            )
        ) == 0


def test_resume_upload_replay_returns_original_encrypted_snapshot_after_later_changes(
    workspace: tuple[Database, SqlAlchemyOwnerWorkspaceStore],
) -> None:
    database, store = workspace
    parsed = _parsed_resume()
    original = store.upload_resume_version(
        owner_id="owner-a",
        parsed=parsed,
        label="Replay-stable resume",
        set_as_base=True,
        idempotency_key="stable-upload",
    )

    profile = store.get_profile(owner_id="owner-a")
    assert profile is not None
    store.put_profile(
        owner_id="owner-a",
        payload=CandidateProfileWrite(
            current_title="Owner-corrected title",
            years_of_experience=profile.years_of_experience,
            employment_types=profile.employment_types,
            onboarding_step=profile.onboarding_step,
        ),
        expected_version=profile.version,
    )
    evidence = store.list_evidence(owner_id="owner-a", approval_state="approved").items
    assert len(evidence) == 1
    store.patch_evidence(
        owner_id="owner-a",
        evidence_id=evidence[0].id,
        payload=AchievementEvidencePatch(approval_state="retired"),
        expected_version=evidence[0].version,
    )
    store.create_resume_version(
        owner_id="owner-a",
        payload=ResumeVersionCreate(
            label="New base",
            content="A different base resume with enough useful text to retain.",
            source="pasted",
            set_as_base=True,
        ),
        idempotency_key="new-base-after-upload",
    )

    replay = store.upload_resume_version(
        owner_id="owner-a",
        parsed=parsed,
        label="Replay-stable resume",
        set_as_base=True,
        idempotency_key="stable-upload",
    )

    assert replay.model_dump() == original.model_dump()
    assert replay.resume_version.version == 1
    assert replay.resume_version.is_base is True
    assert replay.achievement_suggestions_created == 1
    with database.session() as session:
        imports = list(
            session.scalars(
                select(ResumeImport).where(ResumeImport.owner_id == "owner-a")
            )
        )
        assert len(imports) == 1
        stored = imports[0]
        assert stored.resume_version_id == original.resume_version.id
        assert stored.parser_version == parsed.parser_version
        assert stored.media_type == parsed.media_type
        assert stored.page_count == parsed.page_count
        assert "Replay-stable resume" not in stored.encrypted_payload
        assert parsed.content not in stored.encrypted_payload
        receipt = session.scalar(
            select(OwnerMutationReceipt).where(
                OwnerMutationReceipt.owner_id == "owner-a",
                OwnerMutationReceipt.namespace == "resume_version.upload",
            )
        )
        assert receipt is not None
        assert receipt.resource_type == "resume_import"
        assert receipt.resource_id == stored.id
        assert receipt.result_version == original.resume_version.version


def test_resume_import_envelope_cannot_be_replayed_for_another_owner_or_record(
    workspace: tuple[Database, SqlAlchemyOwnerWorkspaceStore],
) -> None:
    database, store = workspace
    parsed = _parsed_resume()
    store.upload_resume_version(
        owner_id="owner-a",
        parsed=parsed,
        label="Owner A resume",
        set_as_base=True,
        idempotency_key="owner-a-upload",
    )
    owner_b_original = store.upload_resume_version(
        owner_id="owner-b",
        parsed=parsed,
        label="Owner B resume",
        set_as_base=True,
        idempotency_key="owner-b-upload",
    )
    with database.session() as session:
        owner_a_import = session.scalar(
            select(ResumeImport).where(ResumeImport.owner_id == "owner-a")
        )
        owner_b_import = session.scalar(
            select(ResumeImport).where(ResumeImport.owner_id == "owner-b")
        )
        assert owner_a_import is not None and owner_b_import is not None
        owner_a_import.encrypted_payload = owner_b_import.encrypted_payload
        owner_a_import.encryption_key_id = owner_b_import.encryption_key_id

    with pytest.raises(WorkspaceUnavailable, match="could not be decrypted"):
        store.upload_resume_version(
            owner_id="owner-a",
            parsed=parsed,
            label="Owner A resume",
            set_as_base=True,
            idempotency_key="owner-a-upload",
        )
    owner_b_replay = store.upload_resume_version(
        owner_id="owner-b",
        parsed=parsed,
        label="Owner B resume",
        set_as_base=True,
        idempotency_key="owner-b-upload",
    )
    assert owner_b_replay.model_dump() == owner_b_original.model_dump()


def test_resume_import_envelope_binds_parser_provenance_metadata(
    workspace: tuple[Database, SqlAlchemyOwnerWorkspaceStore],
) -> None:
    database, store = workspace
    parsed = _parsed_resume()
    store.upload_resume_version(
        owner_id="owner-a",
        parsed=parsed,
        label="Bound provenance resume",
        set_as_base=True,
        idempotency_key="bound-provenance-upload",
    )
    with database.session() as session:
        imported = session.scalar(
            select(ResumeImport).where(ResumeImport.owner_id == "owner-a")
        )
        assert imported is not None
        imported.parser_version = "tampered-parser-version"

    with pytest.raises(WorkspaceUnavailable, match="could not be decrypted"):
        store.upload_resume_version(
            owner_id="owner-a",
            parsed=parsed,
            label="Bound provenance resume",
            set_as_base=True,
            idempotency_key="bound-provenance-upload",
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
