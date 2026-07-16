"""Focused tests for encrypted owner-scoped durable hunt storage."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from job_hunt_agent.database import Database
from job_hunt_agent.hunt_repository import (
    HuntRepositoryError,
    IdempotencyConflict,
    append_hunt_outcomes,
    authorize_hunt,
    cancel_hunt,
    complete_existing_hunt_result_for_worker,
    create_or_reuse_hunt,
    delete_hunt,
    load_hunt_outcomes,
    load_hunt_request_for_worker,
    load_hunt_result,
    load_hunt_state,
    purge_expired_hunts,
    requeue_hunt_dead_letter,
    store_hunt_success,
)
from job_hunt_agent.job_queue import claim_next_job, fail_job_attempt
from job_hunt_agent.models import BackgroundJob, Base, HuntOutcome, HuntRun, Owner
from job_hunt_agent.privacy_repository import export_owner_workspace
from job_hunt_agent.schemas import (
    CompanySource,
    HuntResult,
    OutcomeLog,
    OutreachDraft,
    Person,
    Role,
)
from job_hunt_agent.security import DataKeyring, DecryptionError


@pytest.fixture
def hunt_db(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'hunts.db'}")
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        session.add_all(
            [
                Owner(id="owner-a", display_name="Owner A", timezone="UTC"),
                Owner(id="owner-b", display_name="Owner B", timezone="UTC"),
            ]
        )
    try:
        yield database
    finally:
        database.dispose()


@pytest.fixture
def keyring() -> DataKeyring:
    return DataKeyring([("test-v1", Fernet.generate_key().decode("ascii"))])


def _request(marker: str) -> tuple[str, str]:
    payload = json.dumps(
        {"resume_text": marker, "criteria": {"role_keywords": ["backend"]}},
        sort_keys=True,
        separators=(",", ":"),
    )
    return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _create(
    session,
    keyring: DataKeyring,
    *,
    marker: str,
    owner_id: str = "owner-a",
    idempotency: str | None = None,
    access: str = "access-one",
    run_id: str | None = None,
    max_attempts: int = 3,
    now: datetime | None = None,
    request_hours: int = 1,
    access_hours: int = 2,
):
    current = now or datetime.now(timezone.utc)
    request_json, request_hash = _request(marker)
    return create_or_reuse_hunt(
        session,
        owner_id=owner_id,
        request_json=request_json,
        request_hash=request_hash,
        access_hash=_digest(access),
        keyring=keyring,
        request_expires_at=current + timedelta(hours=request_hours),
        access_expires_at=current + timedelta(hours=access_hours),
        idempotency_key_hash=_digest(idempotency) if idempotency else None,
        max_attempts=max_attempts,
        run_id=run_id,
        now=current,
    )


def _result(run_id: str, marker: str = "PRIVATE RESULT MESSAGE") -> HuntResult:
    role = Role(
        company="Acme",
        title="Backend Engineer",
        url="https://example.com/jobs/1",
        location="Remote",
        summary="Backend systems role",
        match_reason="Matches backend experience",
        source=CompanySource.greenhouse,
    )
    person = Person(
        name="Pat Example",
        title="Engineering Manager",
        company="Acme",
        profile_url="https://example.com/pat",
        source="company_page",
        why_relevant="Leads the hiring team",
    )
    return HuntResult(
        run_id=run_id,
        roles=[role],
        outreach=[
            OutreachDraft(
                draft_id="draft-1",
                role=role,
                person=person,
                message=marker,
            )
        ],
    )


def test_create_reuse_owner_isolation_and_encryption(
    hunt_db: Database,
    keyring: DataKeyring,
) -> None:
    request_marker = "DISTINCTIVE PRIVATE RESUME owner-a"
    now = datetime.now(timezone.utc)
    with hunt_db.session() as session:
        first = _create(
            session,
            keyring,
            marker=request_marker,
            idempotency="same-key",
            now=now,
        )
        original_expiry = first.state.access_expires_at
        reused = _create(
            session,
            keyring,
            marker=request_marker,
            idempotency="same-key",
            access="access-two",
            now=now + timedelta(seconds=1),
            access_hours=24,
        )
        other_owner = _create(
            session,
            keyring,
            marker=request_marker,
            owner_id="owner-b",
            idempotency="same-key",
            now=now,
        )

        assert first.created is True
        assert reused.reused is True
        assert reused.state.run_id == first.state.run_id
        assert reused.state.access_expires_at == original_expiry
        assert other_owner.state.run_id != first.state.run_id
        assert authorize_hunt(
            session,
            owner_id="owner-a",
            hunt_run_id=first.state.run_id,
            access_hash=_digest("access-two"),
            now=now + timedelta(seconds=2),
        )
        assert not authorize_hunt(
            session,
            owner_id="owner-a",
            hunt_run_id=first.state.run_id,
            access_hash=_digest("access-one"),
            now=now + timedelta(seconds=2),
        )
        assert load_hunt_state(
            session,
            owner_id="owner-b",
            hunt_run_id=first.state.run_id,
        ) is None

        run = session.get(HuntRun, first.state.run_id)
        job = session.get(BackgroundJob, first.state.background_job_id)
        assert run is not None and request_marker not in (run.encrypted_request or "")
        assert job is not None and job.payload == {"hunt_run_id": first.state.run_id}

        different_json, different_hash = _request("DIFFERENT PRIVATE RESUME")
        with pytest.raises(IdempotencyConflict):
            create_or_reuse_hunt(
                session,
                owner_id="owner-a",
                request_json=different_json,
                request_hash=different_hash,
                access_hash=_digest("access-three"),
                keyring=keyring,
                request_expires_at=now + timedelta(hours=1),
                access_expires_at=now + timedelta(hours=2),
                idempotency_key_hash=_digest("same-key"),
                now=now,
            )


def test_success_outcomes_and_delete_lifecycle(
    hunt_db: Database,
    keyring: DataKeyring,
) -> None:
    now = datetime.now(timezone.utc)
    with hunt_db.session() as session:
        created = _create(
            session,
            keyring,
            marker="PRIVATE RESUME LIFECYCLE",
            run_id="lifecycle-run",
            now=now,
        )
        claimed = claim_next_job(
            session,
            worker_id="worker-a",
            lease_token="lease-a",
            kinds={"legacy_hunt"},
            now=now + timedelta(seconds=1),
        )
        assert claimed is not None
        request_json = load_hunt_request_for_worker(
            session,
            hunt_run_id=created.state.run_id,
            worker_id="worker-a",
            lease_token="lease-a",
            keyring=keyring,
            now=now + timedelta(seconds=2),
        )
        assert request_json is not None and "PRIVATE RESUME LIFECYCLE" in request_json
        state = store_hunt_success(
            session,
            hunt_result=_result(created.state.run_id),
            worker_id="worker-a",
            lease_token="lease-a",
            keyring=keyring,
            now=now + timedelta(seconds=3),
        )
        assert state is not None and state.status == "succeeded"
        assert state.result_available and not state.request_available

    with hunt_db.session() as session:
        result = load_hunt_result(
            session,
            owner_id="owner-a",
            hunt_run_id=created.state.run_id,
            keyring=keyring,
        )
        assert result is not None
        assert result.outreach[0].message == "PRIVATE RESULT MESSAGE"
        stamped = append_hunt_outcomes(
            session,
            owner_id="owner-a",
            hunt_run_id=created.state.run_id,
            outcomes=[
                OutcomeLog(
                    draft_id="draft-1",
                    outcome="replied",
                    notes="PRIVATE OUTCOME NOTE",
                )
            ],
            keyring=keyring,
            now=now + timedelta(seconds=4),
        )
        assert stamped[0].logged_at == now + timedelta(seconds=4)
        loaded = load_hunt_outcomes(
            session,
            owner_id="owner-a",
            hunt_run_id=created.state.run_id,
            keyring=keyring,
        )
        assert loaded[0].notes == "PRIVATE OUTCOME NOTE"
        run = session.get(HuntRun, created.state.run_id)
        outcome = session.scalar(select(HuntOutcome))
        assert run is not None and "PRIVATE RESULT MESSAGE" not in (run.encrypted_result or "")
        assert outcome is not None and "PRIVATE OUTCOME NOTE" not in outcome.encrypted_payload

    with hunt_db.session() as session:
        assert delete_hunt(
            session,
            owner_id="owner-a",
            hunt_run_id=created.state.run_id,
        )
        assert session.scalar(select(func.count(HuntRun.id))) == 0
        assert session.scalar(select(func.count(HuntOutcome.id))) == 0
        assert session.get(BackgroundJob, created.state.background_job_id) is None


def test_cancel_requeue_recovery_and_linkage_guards(
    hunt_db: Database,
    keyring: DataKeyring,
) -> None:
    now = datetime.now(timezone.utc)
    with hunt_db.session() as session:
        queued = _create(
            session,
            keyring,
            marker="CANCEL QUEUED RESUME",
            run_id="cancel-queued",
            now=now,
        )
        cancelled = cancel_hunt(
            session,
            owner_id="owner-a",
            hunt_run_id=queued.state.run_id,
            now=now + timedelta(seconds=1),
        )
        assert cancelled is not None and cancelled.status == "cancelled"
        assert not cancelled.request_available

        dead = _create(
            session,
            keyring,
            marker="RETRY PRIVATE RESUME",
            run_id="dead-letter-run",
            max_attempts=1,
            now=now,
        )
        claimed = claim_next_job(
            session,
            worker_id="worker-dead",
            lease_token="lease-dead",
            kinds={"legacy_hunt"},
            now=now + timedelta(seconds=2),
        )
        assert claimed is not None and claimed.id == dead.state.background_job_id
        failed = fail_job_attempt(
            session,
            claimed.id,
            worker_id="worker-dead",
            lease_token="lease-dead",
            error_code="ProviderTimeout",
            terminal=True,
            now=now + timedelta(seconds=3),
        )
        assert failed is not None and failed.status == "dead_letter"
        requeued = requeue_hunt_dead_letter(
            session,
            owner_id="owner-a",
            hunt_run_id=dead.state.run_id,
            actor="operator",
            now=now + timedelta(seconds=4),
        )
        assert requeued is not None and requeued.status == "queued"
        assert requeued.attempt_count == 0 and requeued.request_available

        job = session.get(BackgroundJob, dead.state.background_job_id)
        assert job is not None
        job.payload = {"hunt_run_id": "wrong-run"}
        session.flush()
        claimed = claim_next_job(
            session,
            worker_id="worker-link",
            lease_token="lease-link",
            kinds={"legacy_hunt"},
            now=now + timedelta(seconds=5),
        )
        assert claimed is not None
        assert load_hunt_request_for_worker(
            session,
            hunt_run_id=dead.state.run_id,
            worker_id="worker-link",
            lease_token="lease-link",
            keyring=keyring,
            now=now + timedelta(seconds=6),
        ) is None
        with pytest.raises(HuntRepositoryError):
            store_hunt_success(
                session,
                hunt_result=_result(dead.state.run_id),
                worker_id="worker-link",
                lease_token="lease-link",
                keyring=keyring,
                now=now + timedelta(seconds=6),
            )


def test_existing_result_recovery_and_retention_purge(
    hunt_db: Database,
    keyring: DataKeyring,
) -> None:
    now = datetime.now(timezone.utc)
    with hunt_db.session() as session:
        recovery = _create(
            session,
            keyring,
            marker="RECOVERY RESUME",
            run_id="recovery-run",
            now=now,
            access_hours=4,
        )
        claimed = claim_next_job(
            session,
            worker_id="worker-recovery",
            lease_token="lease-recovery",
            kinds={"legacy_hunt"},
            now=now + timedelta(seconds=1),
        )
        assert claimed is not None
        envelope = keyring.encrypt(_result(recovery.state.run_id).model_dump_json())
        run = session.get(HuntRun, recovery.state.run_id)
        assert run is not None
        run.encrypted_result = envelope.ciphertext
        run.result_key_id = envelope.key_id
        recovered = complete_existing_hunt_result_for_worker(
            session,
            hunt_run_id=recovery.state.run_id,
            worker_id="worker-recovery",
            lease_token="lease-recovery",
            keyring=keyring,
            now=now + timedelta(seconds=2),
        )
        assert recovered is not None and recovered.status == "succeeded"
        assert recovered.result_available and not recovered.request_available

        request_expired = _create(
            session,
            keyring,
            marker="EXPIRED REQUEST RESUME",
            run_id="request-expired",
            now=now,
            request_hours=1,
            access_hours=3,
        )
        access_expired = _create(
            session,
            keyring,
            marker="EXPIRED ACCESS RESUME",
            run_id="access-expired",
            now=now,
            request_hours=1,
            access_hours=1,
        )
        purge = purge_expired_hunts(session, now=now + timedelta(hours=2))
        assert purge.requests_cleared == 1
        assert purge.runs_deleted == 1
        remaining = session.get(HuntRun, request_expired.state.run_id)
        assert remaining is not None and remaining.encrypted_request is None
        assert session.get(HuntRun, access_expired.state.run_id) is None


def test_request_ciphertext_is_bound_to_owner_run_and_stored_digest(
    hunt_db: Database,
    keyring: DataKeyring,
) -> None:
    now = datetime.now(timezone.utc)
    with hunt_db.session() as session:
        owner_a = _create(
            session,
            keyring,
            marker="SAME PRIVATE REQUEST",
            owner_id="owner-a",
            run_id="request-owner-a",
            now=now,
        )
        claimed = claim_next_job(
            session,
            worker_id="worker-request",
            lease_token="lease-request",
            kinds={"legacy_hunt"},
            now=now + timedelta(seconds=1),
        )
        assert claimed is not None
        owner_b = _create(
            session,
            keyring,
            marker="SAME PRIVATE REQUEST",
            owner_id="owner-b",
            run_id="request-owner-b",
            now=now + timedelta(seconds=2),
        )
        row_a = session.get(HuntRun, owner_a.state.run_id)
        row_b = session.get(HuntRun, owner_b.state.run_id)
        assert row_a is not None and row_b is not None
        row_a.encrypted_request = row_b.encrypted_request
        row_a.request_key_id = row_b.request_key_id

        with pytest.raises(DecryptionError, match="binding"):
            load_hunt_request_for_worker(
                session,
                hunt_run_id=row_a.id,
                worker_id="worker-request",
                lease_token="lease-request",
                keyring=keyring,
                now=now + timedelta(seconds=3),
            )

        legacy_json, legacy_hash = _request("LEGITIMATE LEGACY REQUEST")
        legacy_envelope = keyring.encrypt(legacy_json)
        row_a.encrypted_request = legacy_envelope.ciphertext
        row_a.request_key_id = legacy_envelope.key_id
        row_a.request_hash = legacy_hash
        assert (
            load_hunt_request_for_worker(
                session,
                hunt_run_id=row_a.id,
                worker_id="worker-request",
                lease_token="lease-request",
                keyring=keyring,
                now=now + timedelta(seconds=4),
            )
            == legacy_json
        )
        row_a.request_hash = "0" * 64
        with pytest.raises(DecryptionError, match="digest"):
            load_hunt_request_for_worker(
                session,
                hunt_run_id=row_a.id,
                worker_id="worker-request",
                lease_token="lease-request",
                keyring=keyring,
                now=now + timedelta(seconds=5),
            )


def test_result_and_outcome_ciphertexts_are_row_bound_and_export_fails_closed(
    hunt_db: Database,
    keyring: DataKeyring,
) -> None:
    now = datetime.now(timezone.utc)
    with hunt_db.session() as session:
        created_runs = []
        for index, owner_id in ((1, "owner-a"), (2, "owner-b")):
            created = _create(
                session,
                keyring,
                marker=f"PRIVATE ROW {index}",
                owner_id=owner_id,
                run_id=f"bound-run-{index}",
                now=now + timedelta(seconds=index - 1),
            )
            claimed = claim_next_job(
                session,
                worker_id=f"worker-{index}",
                lease_token=f"lease-{index}",
                kinds={"legacy_hunt"},
                now=now + timedelta(seconds=index * 3),
            )
            assert claimed is not None
            stored = store_hunt_success(
                session,
                hunt_result=_result(created.state.run_id, f"RESULT {index}"),
                worker_id=f"worker-{index}",
                lease_token=f"lease-{index}",
                keyring=keyring,
                now=now + timedelta(seconds=index * 3 + 1),
            )
            assert stored is not None
            append_hunt_outcomes(
                session,
                owner_id=owner_id,
                hunt_run_id=created.state.run_id,
                outcomes=[
                    OutcomeLog(
                        draft_id="draft-1",
                        outcome="replied",
                        notes=f"OUTCOME {index}",
                    )
                ],
                keyring=keyring,
                now=now + timedelta(seconds=index * 3 + 2),
            )
            created_runs.append(created.state.run_id)

        run_a = session.get(HuntRun, created_runs[0])
        run_b = session.get(HuntRun, created_runs[1])
        assert run_a is not None and run_b is not None
        original_a_result = (run_a.encrypted_result, run_a.result_key_id)
        run_a.encrypted_result, run_a.result_key_id = (
            run_b.encrypted_result,
            run_b.result_key_id,
        )
        run_b.encrypted_result, run_b.result_key_id = original_a_result

        outcome_a = session.scalar(
            select(HuntOutcome).where(HuntOutcome.hunt_run_id == run_a.id)
        )
        outcome_b = session.scalar(
            select(HuntOutcome).where(HuntOutcome.hunt_run_id == run_b.id)
        )
        assert outcome_a is not None and outcome_b is not None
        original_a_outcome = (
            outcome_a.encrypted_payload,
            outcome_a.encryption_key_id,
        )
        outcome_a.encrypted_payload, outcome_a.encryption_key_id = (
            outcome_b.encrypted_payload,
            outcome_b.encryption_key_id,
        )
        outcome_b.encrypted_payload, outcome_b.encryption_key_id = original_a_outcome

        with pytest.raises(DecryptionError, match="binding"):
            load_hunt_result(
                session,
                owner_id="owner-a",
                hunt_run_id=run_a.id,
                keyring=keyring,
            )
        with pytest.raises(DecryptionError, match="binding"):
            load_hunt_outcomes(
                session,
                owner_id="owner-a",
                hunt_run_id=run_a.id,
                keyring=keyring,
            )

        exported = export_owner_workspace(
            session,
            owner_id="owner-a",
            keyring=keyring,
            now=now + timedelta(minutes=1),
        )
        assert all(
            "result_payload" not in row for row in exported.tables["hunt_runs"]
        )
        assert all("outcome" not in row for row in exported.tables["hunt_outcomes"])
        assert any(
            omission.table == "hunt_runs"
            and omission.field == "encrypted_result"
            and omission.reason == "decryption_failed"
            for omission in exported.omissions
        )
        assert any(
            omission.table == "hunt_outcomes"
            and omission.field == "encrypted_payload"
            and omission.reason == "decryption_failed"
            for omission in exported.omissions
        )

        legacy_cross_owner = keyring.encrypt(
            OutcomeLog(
                draft_id="draft-1",
                outcome="replied",
                notes="UNBOUND LEGACY OWNER-B OUTCOME",
                logged_at=now,
            ).model_dump_json()
        )
        outcome_a.encrypted_payload = legacy_cross_owner.ciphertext
        outcome_a.encryption_key_id = legacy_cross_owner.key_id
        with pytest.raises(DecryptionError, match="unbound legacy"):
            load_hunt_outcomes(
                session,
                owner_id="owner-a",
                hunt_run_id=run_a.id,
                keyring=keyring,
            )

        wrong_result = keyring.encrypt(_result("wrong-run").model_dump_json())
        run_a.encrypted_result = wrong_result.ciphertext
        run_a.result_key_id = wrong_result.key_id
        with pytest.raises(DecryptionError, match="result binding"):
            load_hunt_result(
                session,
                owner_id="owner-a",
                hunt_run_id=run_a.id,
                keyring=keyring,
            )
