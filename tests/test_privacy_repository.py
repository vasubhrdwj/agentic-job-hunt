"""Owner isolation and transactional guarantees for privacy controls."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from job_hunt_agent.auth import create_owner_session
from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    BackgroundJob,
    BackgroundJobEvent,
    Base,
    CandidateProfile,
    HuntRun,
    Owner,
    OwnerPrivacySetting,
    OwnerSession,
    PrivacyDeletionReceipt,
    WorkerHeartbeat,
)
from job_hunt_agent.private_payloads import encrypt_private_payload
from job_hunt_agent.privacy_repository import (
    PrivacyConflict,
    delete_owner_workspace,
    export_owner_workspace,
    get_owner_hunt_retention_days,
    preview_owner_deletion,
    update_retention_setting,
)
from job_hunt_agent.security import load_data_keyring


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
    assert "owner_sessions" not in exported.tables
    assert any(
        omission.table == "owner_sessions"
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
