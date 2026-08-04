"""Owner isolation and transactional guarantees for privacy controls."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from job_hunt_agent.auth import create_owner_session, hash_password
from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    BackgroundJob,
    BackgroundJobEvent,
    Base,
    CandidateProfile,
    HuntRun,
    JobPosting,
    JobPostingVersion,
    OpportunityFitEvaluation,
    Owner,
    OwnerCredential,
    OwnerPrivacySetting,
    OwnerSession,
    PrivacyDeletionReceipt,
    ResumeImport,
    WorkerHeartbeat,
)
from job_hunt_agent.private_payloads import encrypt_private_payload
from job_hunt_agent.privacy_repository import (
    PrivacyConflict,
    delete_owner_workspace,
    external_data_limits,
    export_owner_workspace,
    get_owner_hunt_retention_days,
    preview_owner_deletion,
    update_retention_setting,
)
from job_hunt_agent.resume_ingestion import ParsedResume
from job_hunt_agent.security import load_data_keyring
from job_hunt_agent.sqlalchemy_owner_workspace import SqlAlchemyOwnerWorkspaceStore


NOW = datetime(2026, 7, 15, 10, 30, tzinfo=timezone.utc)
RECEIPT_SECRET = "privacy-test-receipt-secret-with-more-than-32-characters"


@pytest.fixture
def privacy_db(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'privacy.db'}")
    Base.metadata.create_all(database.engine)
    try:
        yield database
    finally:
        database.dispose()


def test_external_data_limits_cover_every_production_provider() -> None:
    limits = external_data_limits()
    providers = {limit.provider for limit in limits}
    summaries = {limit.provider: limit.summary for limit in limits}

    assert providers == {"Google Gemini API", "SerpAPI", "Arize Phoenix"}
    assert all(limit.source_url.startswith("https://") for limit in limits)
    assert "Local workspace deletion" in summaries["SerpAPI"]
    assert "local workspace" in summaries["Arize Phoenix"]


def _seed_owner(database: Database, owner_id: str) -> None:
    with database.session() as session:
        session.add(Owner(id=owner_id, display_name=owner_id.title(), timezone="UTC"))


def _seed_profile(database: Database, owner_id: str, marker: str) -> None:
    keyring = load_data_keyring(production=False)
    record_id = f"profile-{owner_id}"
    envelope = encrypt_private_payload(
        keyring,
        record_kind="candidate_profile",
        owner_id=owner_id,
        record_id=record_id,
        payload={
            "full_name": marker,
            "authorization_token": "must-never-export",
        },
    )
    with database.session() as session:
        session.add(
            CandidateProfile(
                id=record_id,
                owner_id=owner_id,
                encrypted_payload=envelope.ciphertext,
                encryption_key_id=envelope.key_id,
                onboarding_state="profile",
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )


def _seed_hunt(database: Database, owner_id: str, suffix: str, created_at: datetime) -> str:
    job_id = f"job-{suffix}"
    hunt_id = f"hunt-{suffix}"
    with database.session() as session:
        session.add(
            BackgroundJob(
                id=job_id,
                kind="legacy_hunt",
                owner_id=owner_id,
                dedupe_scope=f"owner:{owner_id}",
                subject_type="hunt_run",
                subject_id=hunt_id,
                payload={"hunt_run_id": hunt_id},
                dedupe_key=f"hunt-run:{hunt_id}",
                status="queued",
                priority=100,
                attempt_count=0,
                max_attempts=3,
                run_after=created_at,
                stage="queued",
                version=1,
                created_at=created_at,
                updated_at=created_at,
            )
        )
        session.flush()
        session.add(
            HuntRun(
                id=hunt_id,
                owner_id=owner_id,
                background_job_id=job_id,
                access_hash="a" * 64,
                request_hash="b" * 64,
                request_expires_at=created_at + timedelta(hours=24),
                access_expires_at=created_at + timedelta(days=30),
                created_at=created_at,
                updated_at=created_at,
            )
        )
    return job_id


def test_export_is_owner_scoped_decrypts_plaintext_and_never_emits_secrets(
    privacy_db: Database,
) -> None:
    _seed_owner(privacy_db, "owner-a")
    _seed_owner(privacy_db, "owner-b")
    _seed_profile(privacy_db, "owner-a", "OWNER-A-PRIVATE-MARKER")
    _seed_profile(privacy_db, "owner-b", "OWNER-B-PRIVATE-MARKER")
    with privacy_db.session() as session:
        create_owner_session(session, "owner-a", now=NOW)
        session.add(
            OwnerCredential(
                owner_id="owner-a",
                normalized_email="owner-a@example.com",
                password_hash=hash_password("correct-horse-battery-staple"),
            )
        )

    keyring = load_data_keyring(production=False)
    with privacy_db.session() as session:
        exported = export_owner_workspace(
            session,
            owner_id="owner-a",
            keyring=keyring,
            now=NOW,
        )

    profile = exported.tables["candidate_profiles"][0]
    assert profile["private_payload"] == {"full_name": "OWNER-A-PRIVATE-MARKER"}
    assert exported.counts["candidate_profiles"] == 1
    assert exported.schema_name == "job_hunt_workspace_export"
    assert exported.schema_version == 1
    serialized = exported.model_dump_json()
    assert "OWNER-B-PRIVATE-MARKER" not in serialized
    assert "must-never-export" not in serialized
    assert "encrypted_payload" not in serialized
    assert "encryption_key_id" not in serialized
    assert "token_hash" not in serialized
    assert "correct-horse-battery-staple" not in serialized
    assert "$argon2" not in serialized
    assert "owner_sessions" not in exported.tables
    credential_export = exported.tables["owner_credentials"][0]
    assert credential_export["owner_id"] == "owner-a"
    assert credential_export["normalized_email"] == "owner-a@example.com"
    assert "password_hash" not in credential_export
    assert any(
        omission.table == "owner_sessions"
        and omission.reason == "security_metadata"
        and omission.row_count == 1
        for omission in exported.omissions
    )
    assert any(
        omission.table == "owner_credentials"
        and omission.field == "password_hash"
        and omission.reason == "security_metadata"
        and omission.row_count == 1
        for omission in exported.omissions
    )


def test_export_redacts_undecryptable_records_without_leaking_ciphertext(
    privacy_db: Database,
) -> None:
    _seed_owner(privacy_db, "owner-a")
    with privacy_db.session() as session:
        session.add(
            CandidateProfile(
                id="profile-owner-a",
                owner_id="owner-a",
                encrypted_payload="distinctive-invalid-ciphertext",
                encryption_key_id="local-dev",
                onboarding_state="profile",
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )

    with privacy_db.session() as session:
        exported = export_owner_workspace(
            session,
            owner_id="owner-a",
            keyring=load_data_keyring(production=False),
            now=NOW,
        )

    serialized = exported.model_dump_json()
    assert "distinctive-invalid-ciphertext" not in serialized
    assert any(
        omission.table == "candidate_profiles"
        and omission.reason == "decryption_failed"
        for omission in exported.omissions
    )


def test_resume_import_snapshot_is_exported_and_cascade_deleted_per_owner(
    privacy_db: Database,
) -> None:
    _seed_owner(privacy_db, "owner-a")
    _seed_owner(privacy_db, "owner-b")
    keyring = load_data_keyring(production=False)
    store = SqlAlchemyOwnerWorkspaceStore(privacy_db, keyring)
    parsed_a = ParsedResume(
        content="OWNER-A-RAW-RESUME\nSoftware Engineer\nBuilt reliable services.",
        sections=("experience",),
        current_title="Software Engineer",
        current_location=None,
        years_of_experience=2.5,
        evidence=(),
        skills=("Python",),
        warnings=(),
        media_type="application/pdf",
        page_count=2,
        parser_version="privacy-test-parser",
    )
    parsed_b = ParsedResume(
        content="OWNER-B-RAW-RESUME\nBackend Engineer\nBuilt event pipelines.",
        sections=("experience",),
        current_title="Backend Engineer",
        current_location=None,
        years_of_experience=3.0,
        evidence=(),
        skills=("Kafka",),
        warnings=(),
        media_type="text/plain",
        page_count=None,
        parser_version="privacy-test-parser",
    )
    report_a = store.upload_resume_version(
        owner_id="owner-a",
        parsed=parsed_a,
        label="Owner A Resume",
        set_as_base=True,
        idempotency_key="owner-a-resume-upload",
    )
    store.upload_resume_version(
        owner_id="owner-b",
        parsed=parsed_b,
        label="Owner B Resume",
        set_as_base=True,
        idempotency_key="owner-b-resume-upload",
    )

    with privacy_db.session() as session:
        owner_a_import = session.scalar(
            select(ResumeImport).where(ResumeImport.owner_id == "owner-a")
        )
        owner_b_import = session.scalar(
            select(ResumeImport).where(ResumeImport.owner_id == "owner-b")
        )
        assert owner_a_import is not None
        assert owner_b_import is not None
        owner_a_ciphertext = owner_a_import.encrypted_payload
        owner_b_import_id = owner_b_import.id
        exported = export_owner_workspace(
            session,
            owner_id="owner-a",
            keyring=keyring,
            now=NOW,
        )

    assert exported.counts["resume_imports"] == 1
    exported_import = exported.tables["resume_imports"][0]
    assert exported_import["id"] == owner_a_import.id
    assert exported_import["resume_version_id"] == report_a.resume_version.id
    assert exported_import["parser_version"] == "privacy-test-parser"
    assert exported_import["media_type"] == "application/pdf"
    assert exported_import["page_count"] == 2
    private_payload = exported_import["private_payload"]
    assert private_payload["resume_version_id"] == report_a.resume_version.id
    assert private_payload["parser_version"] == "privacy-test-parser"
    assert private_payload["media_type"] == "application/pdf"
    assert private_payload["page_count"] == 2
    assert private_payload["report"] == report_a.model_dump(
        mode="json"
    )
    serialized_import = json.dumps(exported_import, sort_keys=True, default=str)
    assert "OWNER-A-RAW-RESUME" not in serialized_import
    assert "OWNER-B-RAW-RESUME" not in serialized_import
    assert owner_a_ciphertext not in serialized_import
    assert owner_b_import_id not in exported.model_dump_json()
    assert "encrypted_payload" not in serialized_import
    assert "encryption_key_id" not in serialized_import

    with privacy_db.session() as session:
        preview = preview_owner_deletion(session, owner_id="owner-a", now=NOW)
        assert preview.row_counts["resume_imports"] == 1
        delete_owner_workspace(
            session,
            owner_id="owner-a",
            confirmation="DELETE WORKSPACE owner-a",
            idempotency_key="delete-owner-a-resume-import",
            receipt_secret=RECEIPT_SECRET,
            now=NOW,
        )

    with privacy_db.session() as session:
        assert session.scalar(
            select(func.count())
            .select_from(ResumeImport)
            .where(ResumeImport.owner_id == "owner-a")
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(ResumeImport)
            .where(ResumeImport.owner_id == "owner-b")
        ) == 1


def test_fit_evaluation_is_exported_decrypted_and_cascade_deleted_per_owner(
    privacy_db: Database,
) -> None:
    _seed_owner(privacy_db, "owner-a")
    _seed_owner(privacy_db, "owner-b")
    keyring = load_data_keyring(production=False)
    verdicts = {
        "owner-a": {
            "band": "strong",
            "reasons": ["Direct backend experience"],
            "gaps": [],
            "evidence_ids": ["evidence-a"],
        },
        "owner-b": {
            "band": "promising",
            "reasons": ["Adjacent systems experience"],
            "gaps": ["No listed Go evidence"],
            "evidence_ids": ["evidence-b"],
        },
    }
    with privacy_db.session() as session:
        for suffix in ("a", "b"):
            owner_id = f"owner-{suffix}"
            posting_id = f"posting-{suffix}"
            version_id = f"posting-version-{suffix}"
            evaluation_id = f"fit-{suffix}"
            session.add(
                JobPosting(
                    id=posting_id,
                    owner_id=owner_id,
                    identity_kind="native",
                    identity_key=f"source:example:{suffix}",
                    identity_key_hash=suffix * 64,
                    source="example",
                    company_slug=f"example-{suffix}",
                    source_job_id=suffix,
                    canonical_url=f"https://careers.example.com/jobs/{suffix}",
                    lifecycle_state="open",
                    consecutive_complete_omissions=0,
                    first_confirmed_at=NOW,
                    last_confirmed_at=NOW,
                    version=1,
                )
            )
            session.flush()
            session.add(
                JobPostingVersion(
                    id=version_id,
                    owner_id=owner_id,
                    job_posting_id=posting_id,
                    version_number=1,
                    content_hash=("1" if suffix == "a" else "2") * 64,
                    source="example",
                    source_job_id=suffix,
                    company_name=f"Example {suffix.upper()}",
                    title="Backend Engineer",
                    canonical_url=f"https://careers.example.com/jobs/{suffix}",
                    apply_urls=[f"https://careers.example.com/jobs/{suffix}"],
                    location="Remote",
                    summary="Build reliable backend systems.",
                    description="Design APIs and event-driven services.",
                    employment_type="full_time",
                    source_facts={},
                    source_confidence=1.0,
                    observed_at=NOW,
                )
            )
            session.flush()
            envelope = encrypt_private_payload(
                keyring,
                record_kind="opportunity_fit_evaluation",
                owner_id=owner_id,
                record_id=evaluation_id,
                payload=verdicts[owner_id],
            )
            session.add(
                OpportunityFitEvaluation(
                    id=evaluation_id,
                    owner_id=owner_id,
                    job_posting_id=posting_id,
                    posting_version_id=version_id,
                    posting_hash=("3" if suffix == "a" else "4") * 64,
                    profile_input_fingerprint=("5" if suffix == "a" else "6") * 64,
                    input_fingerprint=("7" if suffix == "a" else "8") * 64,
                    evaluator_version="fit-policy-v1",
                    provider="google-gemini",
                    model="gemini-2.5-flash",
                    result_schema_version=1,
                    encrypted_payload=envelope.ciphertext,
                    encryption_key_id=envelope.key_id,
                    version=1,
                    created_at=NOW,
                )
            )

    with privacy_db.session() as session:
        owner_b_evaluation = session.scalar(
            select(OpportunityFitEvaluation).where(
                OpportunityFitEvaluation.owner_id == "owner-b"
            )
        )
        assert owner_b_evaluation is not None
        exported = export_owner_workspace(
            session,
            owner_id="owner-a",
            keyring=keyring,
            now=NOW,
        )

    assert exported.counts["opportunity_fit_evaluations"] == 1
    exported_evaluation = exported.tables["opportunity_fit_evaluations"][0]
    assert exported_evaluation["id"] == "fit-a"
    assert exported_evaluation["provider"] == "google-gemini"
    assert exported_evaluation["model"] == "gemini-2.5-flash"
    assert exported_evaluation["private_payload"] == verdicts["owner-a"]
    serialized = json.dumps(exported_evaluation, sort_keys=True, default=str)
    assert "encrypted_payload" not in serialized
    assert "encryption_key_id" not in serialized
    assert "input_fingerprint" not in serialized
    assert "profile_input_fingerprint" not in serialized
    assert "posting_hash" not in serialized
    assert owner_b_evaluation.id not in exported.model_dump_json()

    with privacy_db.session() as session:
        preview = preview_owner_deletion(session, owner_id="owner-a", now=NOW)
        assert preview.row_counts["opportunity_fit_evaluations"] == 1
        delete_owner_workspace(
            session,
            owner_id="owner-a",
            confirmation="DELETE WORKSPACE owner-a",
            idempotency_key="delete-owner-a-fit-cache",
            receipt_secret=RECEIPT_SECRET,
            now=NOW,
        )

    with privacy_db.session() as session:
        assert session.scalar(
            select(func.count())
            .select_from(OpportunityFitEvaluation)
            .where(OpportunityFitEvaluation.owner_id == "owner-a")
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(OpportunityFitEvaluation)
            .where(OpportunityFitEvaluation.owner_id == "owner-b")
        ) == 1


def test_deletion_preview_counts_indirect_rows_and_delete_is_atomic_and_isolated(
    privacy_db: Database,
) -> None:
    _seed_owner(privacy_db, "owner-a")
    _seed_owner(privacy_db, "owner-b")
    _seed_profile(privacy_db, "owner-a", "private-a")
    job_id = _seed_hunt(privacy_db, "owner-a", "a", NOW - timedelta(days=3))
    _seed_hunt(privacy_db, "owner-b", "b", NOW - timedelta(days=3))
    with privacy_db.session() as session:
        create_owner_session(session, "owner-a", now=NOW)
        session.add(
            BackgroundJobEvent(
                job_id=job_id,
                from_status=None,
                to_status="queued",
                actor="api",
                created_at=NOW,
            )
        )
        session.add(
            WorkerHeartbeat(
                worker_id="worker-system",
                supported_kinds=["legacy_hunt"],
                current_job_id=job_id,
                started_at=NOW,
                last_seen_at=NOW,
            )
        )

    with privacy_db.session() as session:
        preview = preview_owner_deletion(session, owner_id="owner-a", now=NOW)
    assert preview.row_counts["background_job_events"] == 1
    assert preview.active_sessions == 1
    assert preview.confirmation_phrase == "DELETE WORKSPACE owner-a"

    with privacy_db.session() as session:
        receipt = delete_owner_workspace(
            session,
            owner_id="owner-a",
            confirmation=preview.confirmation_phrase,
            idempotency_key="delete-owner-a-v1",
            receipt_secret=RECEIPT_SECRET,
            now=NOW,
        )
    assert receipt.replayed is False

    with privacy_db.session() as session:
        assert session.get(Owner, "owner-a") is None
        assert session.get(Owner, "owner-b") is not None
        assert session.scalar(select(func.count()).select_from(OwnerSession)) == 0
        assert session.scalar(select(func.count()).select_from(PrivacyDeletionReceipt)) == 1
        heartbeat = session.get(WorkerHeartbeat, "worker-system")
        assert heartbeat is not None
        assert heartbeat.current_job_id is None


def test_deletion_confirmation_failure_rolls_back_and_replay_never_deletes_recreated_data(
    privacy_db: Database,
) -> None:
    _seed_owner(privacy_db, "owner-a")
    with pytest.raises(PrivacyConflict, match="confirmation"):
        with privacy_db.session() as session:
            delete_owner_workspace(
                session,
                owner_id="owner-a",
                confirmation="DELETE WORKSPACE someone-else",
                idempotency_key="delete-owner-a-v1",
                receipt_secret=RECEIPT_SECRET,
                now=NOW,
            )
    with privacy_db.session() as session:
        assert session.get(Owner, "owner-a") is not None
        assert session.scalar(select(func.count()).select_from(PrivacyDeletionReceipt)) == 0

    with privacy_db.session() as session:
        first = delete_owner_workspace(
            session,
            owner_id="owner-a",
            confirmation="DELETE WORKSPACE owner-a",
            idempotency_key="delete-owner-a-v1",
            receipt_secret=RECEIPT_SECRET,
            now=NOW,
        )
    _seed_owner(privacy_db, "owner-a")
    _seed_profile(privacy_db, "owner-a", "new-data-after-delete")
    with privacy_db.session() as session:
        replay = delete_owner_workspace(
            session,
            owner_id="owner-a",
            confirmation="DELETE WORKSPACE owner-a",
            idempotency_key="delete-owner-a-v1",
            receipt_secret=RECEIPT_SECRET,
            now=NOW + timedelta(minutes=1),
        )
    assert replay.replayed is True
    assert replay.deletion_id == first.deletion_id
    with privacy_db.session() as session:
        assert session.get(Owner, "owner-a") is not None
        assert session.scalar(
            select(func.count())
            .select_from(CandidateProfile)
            .where(CandidateProfile.owner_id == "owner-a")
        ) == 1


def test_shorter_retention_purges_only_eligible_owner_hunts_and_never_extends_policy(
    privacy_db: Database,
) -> None:
    _seed_owner(privacy_db, "owner-a")
    _seed_owner(privacy_db, "owner-b")
    _seed_hunt(privacy_db, "owner-a", "old-a", NOW - timedelta(days=8))
    _seed_hunt(privacy_db, "owner-a", "new-a", NOW - timedelta(days=2))
    _seed_hunt(privacy_db, "owner-b", "old-b", NOW - timedelta(days=8))

    with privacy_db.session() as session:
        assert get_owner_hunt_retention_days(session, owner_id="owner-a") == 30
        report = update_retention_setting(
            session,
            owner_id="owner-a",
            hunt_run_retention_days=7,
            expected_version=1,
            now=NOW,
        )
    assert report.version == 2
    assert report.purged_hunt_runs == 1
    assert report.retained_hunt_runs == 1
    assert report.eligible_hunt_runs == 0

    with privacy_db.session() as session:
        assert get_owner_hunt_retention_days(session, owner_id="owner-a") == 7
        owner_a_hunts = list(
            session.scalars(select(HuntRun).where(HuntRun.owner_id == "owner-a"))
        )
        owner_b_hunts = list(
            session.scalars(select(HuntRun).where(HuntRun.owner_id == "owner-b"))
        )
        assert [row.id for row in owner_a_hunts] == ["hunt-new-a"]
        assert [row.id for row in owner_b_hunts] == ["hunt-old-b"]
        setting = session.get(OwnerPrivacySetting, "owner-a")
        assert setting is not None and setting.hunt_run_retention_days == 7


def test_export_json_is_deterministic_for_equal_state(privacy_db: Database) -> None:
    _seed_owner(privacy_db, "owner-a")
    _seed_profile(privacy_db, "owner-a", "stable")
    keyring = load_data_keyring(production=False)
    with privacy_db.session() as session:
        first = export_owner_workspace(
            session, owner_id="owner-a", keyring=keyring, now=NOW
        )
        second = export_owner_workspace(
            session, owner_id="owner-a", keyring=keyring, now=NOW
        )
    assert json.dumps(first.model_dump(mode="json"), sort_keys=True) == json.dumps(
        second.model_dump(mode="json"), sort_keys=True
    )
    assert hashlib.sha256(first.model_dump_json().encode()).hexdigest() == hashlib.sha256(
        second.model_dump_json().encode()
    ).hexdigest()
