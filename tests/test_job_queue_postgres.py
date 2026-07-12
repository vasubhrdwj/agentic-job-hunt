"""Real PostgreSQL concurrency gates for the generic Phase-0 queue."""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete
from sqlalchemy.engine import make_url

from job_hunt_agent.auth import create_owner_session, load_owner_session, revoke_owner_session
from job_hunt_agent.database import Database
from job_hunt_agent.hunt_repository import create_or_reuse_hunt
from job_hunt_agent.job_queue import claim_next_job, enqueue_job
from job_hunt_agent.models import BackgroundJob, HuntRun, Owner
from job_hunt_agent.security import DataKeyring


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set TEST_DATABASE_URL to the migrated disposable Postgres database",
)


@pytest.fixture
def postgres_db() -> Database:
    if make_url(TEST_DATABASE_URL).get_backend_name() != "postgresql":
        pytest.skip("queue concurrency gates require PostgreSQL")
    database = Database(TEST_DATABASE_URL)
    if not database.migrations_current():
        database.dispose()
        pytest.fail("TEST_DATABASE_URL must be migrated to the current Alembic head")
    try:
        yield database
    finally:
        with database.session() as session:
            session.execute(delete(BackgroundJob).where(BackgroundJob.kind.like("pg_%")))
            session.execute(delete(Owner).where(Owner.id.like("pg-owner-%")))
        database.dispose()


def test_two_workers_skip_locked_and_claim_different_jobs(postgres_db: Database) -> None:
    suffix = uuid4().hex
    kind = f"pg_claim_{suffix}"
    with postgres_db.session() as session:
        enqueue_job(session, kind=kind, dedupe_key=f"{suffix}:one")
        enqueue_job(session, kind=kind, dedupe_key=f"{suffix}:two")

    claimed_barrier = Barrier(2)

    def claim(worker_id: str) -> str:
        with postgres_db.session() as session:
            job = claim_next_job(
                session,
                worker_id=worker_id,
                lease_token=f"lease-{worker_id}",
                kinds={kind},
            )
            assert job is not None
            claimed_barrier.wait(timeout=5)
            return job.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(claim, ("worker-a", "worker-b")))

    assert len(set(ids)) == 2


def test_concurrent_enqueue_keeps_one_deduplicated_row(postgres_db: Database) -> None:
    suffix = uuid4().hex
    kind = f"pg_dedupe_{suffix}"
    dedupe_key = f"same-slot:{suffix}"
    start_barrier = Barrier(2)

    def enqueue(_worker_id: str) -> tuple[str, bool]:
        start_barrier.wait(timeout=5)
        with postgres_db.session() as session:
            result = enqueue_job(
                session,
                kind=kind,
                dedupe_key=dedupe_key,
                payload={"scheduled_slot_id": suffix},
            )
            return result.job.id, result.created

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(enqueue, ("scheduler-a", "scheduler-b")))

    assert len({job_id for job_id, _created in results}) == 1
    assert sorted(created for _job_id, created in results) == [False, True]


def test_same_dedupe_key_is_isolated_between_owners(postgres_db: Database) -> None:
    suffix = uuid4().hex
    owner_a = f"pg-owner-a-{suffix}"
    owner_b = f"pg-owner-b-{suffix}"
    kind = f"pg_owner_scope_{suffix}"
    dedupe_key = f"same-slot:{suffix}"
    with postgres_db.session() as session:
        session.add_all(
            [
                Owner(id=owner_a, display_name="Owner A", timezone="UTC"),
                Owner(id=owner_b, display_name="Owner B", timezone="UTC"),
            ]
        )
        session.flush()
        first = enqueue_job(
            session,
            kind=kind,
            dedupe_key=dedupe_key,
            owner_id=owner_a,
        )
        second = enqueue_job(
            session,
            kind=kind,
            dedupe_key=dedupe_key,
            owner_id=owner_b,
        )

        assert first.created is True
        assert second.created is True
        assert first.job.id != second.job.id
        assert first.job.dedupe_scope == f"owner:{owner_a}"
        assert second.job.dedupe_scope == f"owner:{owner_b}"


def test_concurrent_same_owner_hunt_replay_creates_one_run(
    postgres_db: Database,
) -> None:
    suffix = uuid4().hex
    owner_id = f"pg-owner-hunt-{suffix}"
    marker = f"PRIVATE POSTGRES RESUME {suffix}"
    request_json = json.dumps({"resume_text": marker}, separators=(",", ":"))
    request_hash = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
    idempotency_hash = hashlib.sha256(f"idem-{suffix}".encode()).hexdigest()
    keyring = DataKeyring([("pg-v1", Fernet.generate_key().decode("ascii"))])
    now = datetime.now(timezone.utc)
    start = Barrier(2)

    with postgres_db.session() as session:
        session.add(Owner(id=owner_id, display_name="PG Hunt", timezone="UTC"))

    def create(index: int) -> tuple[str, bool]:
        start.wait(timeout=5)
        with postgres_db.session() as session:
            result = create_or_reuse_hunt(
                session,
                owner_id=owner_id,
                request_json=request_json,
                request_hash=request_hash,
                access_hash=hashlib.sha256(f"access-{index}".encode()).hexdigest(),
                keyring=keyring,
                request_expires_at=now + timedelta(hours=1),
                access_expires_at=now + timedelta(hours=2),
                idempotency_key_hash=idempotency_hash,
                now=now,
            )
            return result.state.run_id, result.created

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(create, (1, 2)))

        assert len({run_id for run_id, _created in results}) == 1
        assert sorted(created for _run_id, created in results) == [False, True]
        with postgres_db.session() as session:
            runs = list(
                session.query(HuntRun).filter(HuntRun.owner_id == owner_id).all()
            )
            assert len(runs) == 1
            assert marker not in (runs[0].encrypted_request or "")
    finally:
        with postgres_db.session() as session:
            session.execute(delete(HuntRun).where(HuntRun.owner_id == owner_id))
            session.execute(delete(BackgroundJob).where(BackgroundJob.owner_id == owner_id))
            session.execute(delete(Owner).where(Owner.id == owner_id))


def test_owner_session_round_trip_uses_real_postgres(postgres_db: Database) -> None:
    owner_id = f"pg-owner-{uuid4().hex}"
    with postgres_db.session() as session:
        grant = create_owner_session(session, owner_id, ttl_days=1)
        assert load_owner_session(session, grant.token) is not None
    with postgres_db.session() as session:
        assert load_owner_session(session, grant.token) is not None
        assert revoke_owner_session(session, grant.token)
    with postgres_db.session() as session:
        assert load_owner_session(session, grant.token) is None
