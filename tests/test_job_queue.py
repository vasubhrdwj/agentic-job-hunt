"""Generic job state-machine tests; Postgres concurrency is a release gate."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from job_hunt_agent.database import Database
from job_hunt_agent.job_queue import (
    cancel_job,
    claim_next_job,
    complete_job,
    enqueue_job,
    fail_job_attempt,
    heartbeat_job,
    queue_counts,
    record_worker_heartbeat,
    recover_stale_jobs,
    update_job_stage,
)
from job_hunt_agent.models import BackgroundJob, BackgroundJobEvent, Base, Owner, WorkerHeartbeat
from job_hunt_agent.scheduler import ScheduledJobSpec, run_scheduler_tick


@pytest.fixture
def foundation_db(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'queue.db'}")
    Base.metadata.create_all(database.engine)
    try:
        yield database
    finally:
        database.dispose()


def test_enqueue_same_dedupe_key_returns_same_job(foundation_db: Database) -> None:
    with foundation_db.session() as session:
        first = enqueue_job(
            session,
            kind="scan_company",
            dedupe_key="scan-1:acme",
            payload={"scan_id": "scan-1", "company_id": "acme"},
        )
        second = enqueue_job(
            session,
            kind="scan_company",
            dedupe_key="scan-1:acme",
            payload={"scan_id": "scan-1", "company_id": "acme"},
        )

        assert first.created is True
        assert second.created is False
        assert second.job.id == first.job.id
        assert first.job.dedupe_scope == "system"


def test_dedupe_key_is_scoped_to_owner(foundation_db: Database) -> None:
    with foundation_db.session() as session:
        session.add_all(
            [
                Owner(id="owner-a", display_name="A", timezone="UTC"),
                Owner(id="owner-b", display_name="B", timezone="UTC"),
                Owner(id="system", display_name="System-named owner", timezone="UTC"),
            ]
        )
        session.flush()
        owner_a = enqueue_job(
            session,
            kind="scan_company",
            dedupe_key="same-slot",
            owner_id="owner-a",
            payload={"company_id": "acme"},
        )
        owner_a_again = enqueue_job(
            session,
            kind="scan_company",
            dedupe_key="same-slot",
            owner_id="owner-a",
            payload={"company_id": "acme"},
        )
        owner_b = enqueue_job(
            session,
            kind="scan_company",
            dedupe_key="same-slot",
            owner_id="owner-b",
            payload={"company_id": "acme"},
        )
        system = enqueue_job(
            session,
            kind="scan_company",
            dedupe_key="same-slot",
            payload={"company_id": "acme"},
        )
        system_named_owner = enqueue_job(
            session,
            kind="scan_company",
            dedupe_key="same-slot",
            owner_id="system",
            payload={"company_id": "acme"},
        )

        assert owner_a_again.created is False
        assert owner_a_again.job.id == owner_a.job.id
        assert owner_a.job.dedupe_scope == "owner:owner-a"
        assert owner_b.created is True
        assert owner_b.job.id != owner_a.job.id
        assert owner_b.job.dedupe_scope == "owner:owner-b"
        assert system.created is True
        assert system.job.id not in {owner_a.job.id, owner_b.job.id}
        assert system.job.dedupe_scope == "system"
        assert system_named_owner.created is True
        assert system_named_owner.job.id != system.job.id
        assert system_named_owner.job.dedupe_scope == "owner:system"


def test_payload_rejects_private_free_text(foundation_db: Database) -> None:
    with foundation_db.session() as session:
        unsafe_payloads = [
            {"resume_text": "distinctive private resume"},
            {"text": "distinctive private resume"},
            {"data": {"resume": "distinctive private resume"}},
            {"scan_id": {"text": "distinctive private resume"}},
        ]
        for index, payload in enumerate(unsafe_payloads):
            with pytest.raises(ValueError):
                enqueue_job(
                    session,
                    kind="legacy_hunt",
                    dedupe_key=f"unsafe-{index}",
                    payload=payload,
                )

        safe = enqueue_job(
            session,
            kind="assess_opportunity",
            dedupe_key="safe",
            payload={
                "resume_version_id": "rv-1",
                "opportunity_id": "opp-1",
                "source": "greenhouse",
                "target_count": 5,
                "full_refresh": False,
            },
        )
        assert safe.created
        serialized_payloads = json.dumps(
            list(session.scalars(select(BackgroundJob.payload))), sort_keys=True
        )
        assert "distinctive private resume" not in serialized_payloads


def test_payload_rejects_invalid_id_and_unbounded_config(foundation_db: Database) -> None:
    with foundation_db.session() as session:
        with pytest.raises(ValueError, match="opaque id"):
            enqueue_job(
                session,
                kind="scan_company",
                dedupe_key="bad-id",
                payload={"company_id": "private resume content"},
            )
        with pytest.raises(ValueError, match="short token"):
            enqueue_job(
                session,
                kind="scan_company",
                dedupe_key="bad-source",
                payload={"source": "private resume content"},
            )


def test_workers_claim_different_jobs(foundation_db: Database) -> None:
    with foundation_db.session() as session:
        enqueue_job(session, kind="scan_company", dedupe_key="one")
        enqueue_job(session, kind="scan_company", dedupe_key="two")

    with foundation_db.session() as session:
        first = claim_next_job(
            session, worker_id="worker-a", lease_token="lease-a", kinds={"scan_company"}
        )
        assert first is not None
        first_id = first.id

    with foundation_db.session() as session:
        second = claim_next_job(
            session, worker_id="worker-b", lease_token="lease-b", kinds={"scan_company"}
        )
        assert second is not None
        assert second.id != first_id


def test_stale_worker_cannot_complete_reclaimed_job(foundation_db: Database) -> None:
    now = datetime.now(timezone.utc)
    with foundation_db.session() as session:
        job_id = enqueue_job(
            session,
            kind="scan_company",
            dedupe_key="stale",
            run_after=now,
        ).job.id
    with foundation_db.session() as session:
        claimed = claim_next_job(
            session,
            worker_id="worker-a",
            lease_token="old-lease",
            lease_seconds=1,
            now=now,
        )
        assert claimed is not None
    with foundation_db.session() as session:
        assert recover_stale_jobs(session, now=now + timedelta(seconds=2)) == 1
    with foundation_db.session() as session:
        reclaimed = claim_next_job(
            session,
            worker_id="worker-b",
            lease_token="new-lease",
            now=now + timedelta(seconds=2),
        )
        assert reclaimed is not None
        assert reclaimed.id == job_id
    with foundation_db.session() as session:
        assert (
            complete_job(
                session,
                job_id,
                worker_id="worker-a",
                lease_token="old-lease",
                now=now + timedelta(seconds=3),
            )
            is None
        )
        assert heartbeat_job(
            session,
            job_id,
            worker_id="worker-b",
            lease_token="new-lease",
            now=now + timedelta(seconds=3),
        )


def test_expired_lease_cannot_mutate_running_job(foundation_db: Database) -> None:
    now = datetime.now(timezone.utc)
    with foundation_db.session() as session:
        job_id = enqueue_job(
            session,
            kind="scan_company",
            dedupe_key="expired-mutations",
            run_after=now,
        ).job.id
        claimed = claim_next_job(
            session,
            worker_id="worker-a",
            lease_token="expired-lease",
            lease_seconds=1,
            now=now,
        )
        assert claimed is not None

    after_expiry = now + timedelta(seconds=1)
    with foundation_db.session() as session:
        assert not heartbeat_job(
            session,
            job_id,
            worker_id="worker-a",
            lease_token="expired-lease",
            now=after_expiry,
        )
        assert not update_job_stage(
            session,
            job_id,
            worker_id="worker-a",
            lease_token="expired-lease",
            stage="should-not-persist",
            now=after_expiry,
        )
        assert (
            complete_job(
                session,
                job_id,
                worker_id="worker-a",
                lease_token="expired-lease",
                now=after_expiry,
            )
            is None
        )
        assert (
            fail_job_attempt(
                session,
                job_id,
                worker_id="worker-a",
                lease_token="expired-lease",
                error_code="ShouldNotPersist",
                now=after_expiry,
            )
            is None
        )
        unchanged = session.get(BackgroundJob, job_id)
        assert unchanged is not None
        assert unchanged.status == "running"
        assert unchanged.stage == "claimed"
        assert unchanged.last_error is None


def test_failed_attempt_retries_then_dead_letters(foundation_db: Database) -> None:
    now = datetime.now(timezone.utc)
    with foundation_db.session() as session:
        job_id = enqueue_job(
            session, kind="scan_company", dedupe_key="retry", max_attempts=2
        ).job.id

    for attempt in (1, 2):
        with foundation_db.session() as session:
            job = claim_next_job(
                session,
                worker_id="worker",
                lease_token=f"lease-{attempt}",
                now=now + timedelta(seconds=attempt),
            )
            assert job is not None
            failed = fail_job_attempt(
                session,
                job.id,
                worker_id="worker",
                lease_token=f"lease-{attempt}",
                error_code="ProviderTimeout: secret details",
                now=now + timedelta(seconds=attempt),
            )
            assert failed is not None
            assert failed.status == ("queued" if attempt == 1 else "dead_letter")
            assert failed.last_error == "ProviderTimeout"

    with foundation_db.session() as session:
        assert claim_next_job(session, worker_id="other") is None
        assert queue_counts(session)["dead_letter"] == 1
        events = list(
            session.scalars(
                select(BackgroundJobEvent).where(BackgroundJobEvent.job_id == job_id)
            )
        )
        assert all("secret details" not in (event.reason or "") for event in events)


def test_cancelled_job_cannot_be_claimed(foundation_db: Database) -> None:
    with foundation_db.session() as session:
        job_id = enqueue_job(session, kind="scan_company", dedupe_key="cancel").job.id
        cancelled = cancel_job(session, job_id, actor="owner", reason="not needed")
        assert cancelled is not None
        assert cancelled.status == "cancelled"
    with foundation_db.session() as session:
        assert claim_next_job(session, worker_id="worker") is None


def test_cancel_request_wins_when_worker_completes(foundation_db: Database) -> None:
    now = datetime.now(timezone.utc)
    with foundation_db.session() as session:
        job_id = enqueue_job(
            session, kind="scan_company", dedupe_key="cancel-complete", run_after=now
        ).job.id
        assert claim_next_job(
            session,
            worker_id="worker",
            lease_token="lease",
            now=now,
        )
        requested = cancel_job(
            session,
            job_id,
            actor="owner",
            reason="user changed plans",
            now=now + timedelta(seconds=1),
        )
        assert requested is not None
        assert requested.status == "running"
        assert requested.cancel_requested_at is not None
        completed = complete_job(
            session,
            job_id,
            worker_id="worker",
            lease_token="lease",
            now=now + timedelta(seconds=2),
        )
        assert completed is not None
        assert completed.status == "cancelled"
        assert completed.cancelled_at is not None
        assert completed.completed_at is None

        events = list(
            session.scalars(
                select(BackgroundJobEvent)
                .where(BackgroundJobEvent.job_id == job_id)
                .order_by(BackgroundJobEvent.id)
            )
        )
        assert [event.to_status for event in events] == [
            "queued",
            "running",
            "running",
            "cancelled",
        ]
        assert [event.reason for event in events[-2:]] == [
            "cancel_requested",
            "cancel_requested",
        ]


def test_cancel_request_wins_when_worker_fails(foundation_db: Database) -> None:
    now = datetime.now(timezone.utc)
    with foundation_db.session() as session:
        job_id = enqueue_job(
            session, kind="scan_company", dedupe_key="cancel-fail", run_after=now
        ).job.id
        assert claim_next_job(
            session,
            worker_id="worker",
            lease_token="lease",
            now=now,
        )
        cancel_job(
            session,
            job_id,
            actor="owner",
            now=now + timedelta(seconds=1),
        )
        failed = fail_job_attempt(
            session,
            job_id,
            worker_id="worker",
            lease_token="lease",
            error_code="ProviderTimeout",
            now=now + timedelta(seconds=2),
        )
        assert failed is not None
        assert failed.status == "cancelled"
        assert failed.failed_at is None
        assert failed.last_error is None


def test_cancel_request_wins_during_stale_lease_recovery(foundation_db: Database) -> None:
    now = datetime.now(timezone.utc)
    with foundation_db.session() as session:
        job_id = enqueue_job(
            session, kind="scan_company", dedupe_key="cancel-recover", run_after=now
        ).job.id
        assert claim_next_job(
            session,
            worker_id="worker",
            lease_token="lease",
            lease_seconds=1,
            now=now,
        )
        cancel_job(
            session,
            job_id,
            actor="owner",
            now=now + timedelta(milliseconds=500),
        )

    with foundation_db.session() as session:
        assert recover_stale_jobs(session, now=now + timedelta(seconds=2)) == 1
        recovered = session.get(BackgroundJob, job_id)
        assert recovered is not None
        assert recovered.status == "cancelled"
        assert recovered.cancelled_at is not None
        assert recovered.last_error is None
        assert claim_next_job(
            session,
            worker_id="other-worker",
            now=now + timedelta(seconds=2),
        ) is None


def test_two_scheduler_ticks_create_one_slot_job(foundation_db: Database) -> None:
    scheduled_for = datetime(2026, 7, 11, 8, 30, tzinfo=timezone.utc)

    def producer(_now: datetime):
        return [
            ScheduledJobSpec(
                kind="scan_saved_search",
                subject_type="saved_search",
                subject_id="search-1",
                scheduled_for=scheduled_for,
                payload={"saved_search_id": "search-1"},
            )
        ]

    with foundation_db.session() as session:
        first = run_scheduler_tick(session, producers=(producer,), now=scheduled_for)
        second = run_scheduler_tick(session, producers=(producer,), now=scheduled_for)
        assert first[0].created is True
        assert second[0].created is False
        assert first[0].job.id == second[0].job.id


def test_worker_heartbeat_and_events_are_durable(foundation_db: Database) -> None:
    now = datetime.now(timezone.utc)
    with foundation_db.session() as session:
        job = enqueue_job(
            session,
            kind="scan_company",
            dedupe_key="events",
            run_after=now,
        ).job
        claimed = claim_next_job(
            session,
            worker_id="worker-a",
            lease_token="lease",
            now=now,
        )
        assert claimed is not None
        record_worker_heartbeat(
            session,
            worker_id="worker-a",
            supported_kinds={"scan_company"},
            current_job_id=job.id,
            build_version="test",
            now=now,
        )
        completed = complete_job(
            session,
            job.id,
            worker_id="worker-a",
            lease_token="lease",
            now=now,
        )
        assert completed is not None

    with foundation_db.session() as session:
        heartbeat = session.get(WorkerHeartbeat, "worker-a")
        assert heartbeat is not None
        assert heartbeat.supported_kinds == ["scan_company"]
        events = list(
            session.scalars(
                select(BackgroundJobEvent)
                .where(BackgroundJobEvent.job_id == job.id)
                .order_by(BackgroundJobEvent.id)
            )
        )
        assert [event.to_status for event in events] == ["queued", "running", "succeeded"]
