"""PostgreSQL concurrency gates for compensating an accidental pursuit.

These tests intentionally force writers to pause after their first row lock.
They exercise lock order and replay behavior that SQLite cannot model.
"""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from threading import Barrier, Event, local
from types import SimpleNamespace
from typing import Callable, TypeVar
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.engine import make_url

import job_hunt_agent.application_submission_repository as submission_repository
from job_hunt_agent.application_artifact_repository import (
    create_application_artifact_revision,
    record_application_artifact_event,
)
from job_hunt_agent.application_artifact_schemas import (
    ApplicationArtifactEventCreate,
    ApplicationArtifactRevisionCreate,
    ApplicationArtifactStatus,
)
from job_hunt_agent.application_pack_repository import (
    create_application_pack,
    create_application_pack_revision,
    record_application_pack_event,
)
from job_hunt_agent.application_pack_schemas import (
    ApplicationPackCreate,
    ApplicationPackEventCreate,
    ApplicationPackRevisionCreate,
    ApplicationPackStatus,
)
from job_hunt_agent.application_repository import (
    pursue_owner_opportunity,
    undo_application_pursuit,
)
from job_hunt_agent.application_submission_schemas import (
    ReadyToApplyTransitionCreate,
)
from job_hunt_agent.contact_search_repository import (
    CONTACT_SEARCH_JOB_KIND,
    create_contact_search,
)
from job_hunt_agent.contact_search_worker import reconcile_terminal_contact_plans
from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    Application,
    ApplicationArtifactEvent,
    ApplicationArtifactRevision,
    ApplicationContact,
    ApplicationPack,
    ApplicationPackEvent,
    ApplicationPackRevision,
    BackgroundJob,
    BackgroundJobEvent,
    Contact,
    ContactPlan,
    JobPosting,
    JobPostingVersion,
    OpportunityDecisionEvent,
    OutreachEvent,
    OutreachSequence,
    Owner,
    OwnerMutationReceipt,
    OwnerOpportunity,
)
from job_hunt_agent.mutation_receipts import MutationPending
from job_hunt_agent.opportunity_schemas import PursueOpportunityRequest
from job_hunt_agent.outreach_repository import (
    record_outreach_event,
    save_outreach_message,
    start_outreach_sequence,
)
from job_hunt_agent.outreach_schemas import (
    OutreachCopiedEventCreate,
    OutreachMarkedSentEventCreate,
    OutreachMessageCreate,
)
from job_hunt_agent.profile_repository import create_or_reuse_resume_version
from job_hunt_agent.repository_errors import ResourceConflict
from job_hunt_agent.security import DataKeyring


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set TEST_DATABASE_URL to the migrated disposable Postgres database",
)

NOW = datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc)
_T = TypeVar("_T")


@dataclass(frozen=True)
class _PursuitGraph:
    owner_id: str
    posting_id: str
    posting_version_id: str
    opportunity_id: str
    application_id: str
    canonical_url: str


@dataclass
class _PostgresWorkspace:
    database: Database
    keyring: DataKeyring
    owner_ids: list[str] = field(default_factory=list)


@pytest.fixture
def postgres_workspace() -> _PostgresWorkspace:
    if make_url(TEST_DATABASE_URL).get_backend_name() != "postgresql":
        pytest.skip("undo concurrency gates require PostgreSQL")
    database = Database(TEST_DATABASE_URL)
    if not database.migrations_current():
        database.dispose()
        pytest.fail("TEST_DATABASE_URL must be migrated to the current Alembic head")
    workspace = _PostgresWorkspace(
        database=database,
        keyring=DataKeyring(
            [("pg-undo-v1", Fernet.generate_key().decode("ascii"))]
        ),
    )
    try:
        yield workspace
    finally:
        with database.session() as session:
            session.execute(
                delete(Owner).where(Owner.id.in_(workspace.owner_ids))
            )
        database.dispose()


def _seed_pursuit(workspace: _PostgresWorkspace) -> _PursuitGraph:
    suffix = uuid4().hex[:12]
    owner_id = f"pg-undo-{suffix}"
    posting_id = f"posting-{suffix}"
    posting_version_id = f"post-ver-{suffix}"
    opportunity_id = f"opp-{suffix}"
    canonical_url = f"https://careers.example.com/jobs/{suffix}"
    workspace.owner_ids.append(owner_id)

    with workspace.database.session() as session:
        session.add(Owner(id=owner_id, display_name="PG Undo", timezone="UTC"))
        session.flush()
        session.add(
            JobPosting(
                id=posting_id,
                owner_id=owner_id,
                identity_kind="native",
                identity_key=f"source:example:{suffix}",
                identity_key_hash=_sha256(f"posting:{suffix}"),
                source="example",
                company_slug=f"example-{suffix}",
                source_job_id=suffix,
                canonical_url=canonical_url,
                lifecycle_state="open",
                consecutive_complete_omissions=0,
                first_confirmed_at=NOW,
                last_confirmed_at=NOW,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            JobPostingVersion(
                id=posting_version_id,
                owner_id=owner_id,
                job_posting_id=posting_id,
                version_number=1,
                content_hash=_sha256(f"posting-version:{suffix}"),
                source="example",
                source_job_id=suffix,
                company_name="Example",
                title="Backend Engineer",
                canonical_url=canonical_url,
                apply_urls=[f"{canonical_url}/apply"],
                location="Remote",
                summary="Build reliable services.",
                description=(
                    "Requirements:\n"
                    "- Experience building Python distributed systems is required."
                ),
                employment_type="full_time",
                source_facts={},
                source_confidence=1.0,
                observed_at=NOW,
                created_at=NOW,
            )
        )
        session.flush()
        session.add(
            OwnerOpportunity(
                id=opportunity_id,
                owner_id=owner_id,
                job_posting_id=posting_id,
                decision="inbox",
                first_surfaced_at=NOW,
                last_surfaced_at=NOW,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        pursued = pursue_owner_opportunity(
            session,
            owner_id,
            opportunity_id,
            PursueOpportunityRequest(),
            1,
            f"pursue-{suffix}",
            NOW,
        )
        assert pursued.pursuit is not None
        application_id = pursued.pursuit.application.id

    return _PursuitGraph(
        owner_id=owner_id,
        posting_id=posting_id,
        posting_version_id=posting_version_id,
        opportunity_id=opportunity_id,
        application_id=application_id,
        canonical_url=canonical_url,
    )


def _create_resume(
    workspace: _PostgresWorkspace,
    graph: _PursuitGraph,
    *,
    parent_id: str | None = None,
) -> str:
    with workspace.database.session() as session:
        created = create_or_reuse_resume_version(
            session,
            owner_id=graph.owner_id,
            label="Tailored resume" if parent_id else "Base resume",
            content=(
                "Tailored private resume for the concurrency gate."
                if parent_id
                else "Private resume with Python distributed systems experience."
            ),
            source="edited" if parent_id else "pasted",
            keyring=workspace.keyring,
            parent_id=parent_id,
            make_base=parent_id is None,
            now=NOW,
        )
        return created.resume.id


def _seed_reviewed_materials(
    workspace: _PostgresWorkspace,
    graph: _PursuitGraph,
) -> dict[str, str]:
    base_resume_id = _create_resume(workspace, graph)
    tailored_resume_id = _create_resume(
        workspace,
        graph,
        parent_id=base_resume_id,
    )
    suffix = graph.owner_id.removeprefix("pg-undo-")
    ids = {
        "pack": f"pack-{suffix}",
        "grounding": f"ground-{suffix}",
        "review": f"review-{suffix}",
        "artifact": f"artifact-{suffix}",
        "approval": f"approval-{suffix}",
        "tailored_resume": tailored_resume_id,
    }
    with workspace.database.session() as session:
        session.add(
            ApplicationPack(
                id=ids["pack"],
                owner_id=graph.owner_id,
                application_id=graph.application_id,
                job_posting_id=graph.posting_id,
                posting_version_id=graph.posting_version_id,
                base_resume_version_id=base_resume_id,
                version=3,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            ApplicationPackRevision(
                id=ids["grounding"],
                owner_id=graph.owner_id,
                application_id=graph.application_id,
                application_pack_id=ids["pack"],
                revision_number=1,
                source="extracted",
                encrypted_payload="test-grounding-ciphertext",
                encryption_key_id="pg-undo-v1",
                content_hash=_sha256(f"grounding:{suffix}"),
                created_at=NOW,
            )
        )
        session.flush()
        session.add(
            ApplicationPackEvent(
                id=ids["review"],
                owner_id=graph.owner_id,
                application_id=graph.application_id,
                application_pack_id=ids["pack"],
                revision_id=ids["grounding"],
                sequence_number=1,
                event_type="reviewed",
                occurred_at=NOW,
                idempotency_key_hash=_sha256(f"review:{suffix}"),
                created_at=NOW,
            )
        )
        session.add(
            ApplicationArtifactRevision(
                id=ids["artifact"],
                owner_id=graph.owner_id,
                application_id=graph.application_id,
                application_pack_id=ids["pack"],
                grounding_revision_id=ids["grounding"],
                revision_number=1,
                source="deterministic",
                generator_version="application-artifacts-deterministic-v1",
                encrypted_payload="test-artifact-ciphertext",
                encryption_key_id="pg-undo-v1",
                content_hash=_sha256(f"artifact:{suffix}"),
                created_at=NOW,
            )
        )
        session.flush()
        session.add(
            ApplicationArtifactEvent(
                id=ids["approval"],
                owner_id=graph.owner_id,
                application_id=graph.application_id,
                application_pack_id=ids["pack"],
                artifact_revision_id=ids["artifact"],
                sequence_number=1,
                event_type="approved",
                tailored_resume_version_id=tailored_resume_id,
                occurred_at=NOW,
                idempotency_key_hash=_sha256(f"approval:{suffix}"),
                created_at=NOW,
            )
        )
    return ids


def _unsupported_review_payload(current_revision) -> ApplicationPackRevisionCreate:
    return ApplicationPackRevisionCreate(
        parent_revision_id=current_revision.id,
        requirements=[
            {
                "id": item.id,
                "ordinal": item.ordinal,
                "importance": item.importance,
                "text": item.text,
                "source_start": item.source_start,
                "source_end": item.source_end,
                "coverage": "unsupported",
                "evidence_refs": [],
            }
            for item in current_revision.requirements
        ],
    )


def _seed_live_reviewed_pack(
    workspace: _PostgresWorkspace,
    graph: _PursuitGraph,
):
    resume_id = _create_resume(workspace, graph)
    suffix = graph.owner_id.removeprefix("pg-undo-")
    with workspace.database.session() as session:
        created = create_application_pack(
            session,
            owner_id=graph.owner_id,
            application_id=graph.application_id,
            payload=ApplicationPackCreate(base_resume_version_id=resume_id),
            expected_application_version=1,
            idempotency_key=f"live-pack-create-{suffix}",
            keyring=workspace.keyring,
            now=NOW + timedelta(minutes=1),
        )
    assert created is not None and created.pack is not None
    assert created.current_revision is not None
    assert created.current_revision.requirements

    with workspace.database.session() as session:
        revised = create_application_pack_revision(
            session,
            owner_id=graph.owner_id,
            application_id=graph.application_id,
            pack_id=created.pack.id,
            payload=_unsupported_review_payload(created.current_revision),
            expected_pack_version=1,
            idempotency_key=f"live-pack-revision-{suffix}",
            keyring=workspace.keyring,
            now=NOW + timedelta(minutes=2),
        )
    assert revised is not None and revised.pack is not None
    assert revised.current_revision is not None

    with workspace.database.session() as session:
        reviewed = record_application_pack_event(
            session,
            owner_id=graph.owner_id,
            application_id=graph.application_id,
            pack_id=revised.pack.id,
            payload=ApplicationPackEventCreate(
                event_type="reviewed",
                revision_id=revised.current_revision.id,
                confirm_requirements_reviewed=True,
            ),
            expected_pack_version=2,
            idempotency_key=f"live-pack-review-{suffix}",
            keyring=workspace.keyring,
            now=NOW + timedelta(minutes=3),
        )
    assert reviewed is not None and reviewed.pack is not None
    assert reviewed.current_revision is not None
    assert reviewed.status is ApplicationPackStatus.reviewed
    assert reviewed.pack.version == 3
    return reviewed


def _seed_outreach(
    workspace: _PostgresWorkspace,
    graph: _PursuitGraph,
) -> tuple[str, str, int]:
    suffix = graph.owner_id.removeprefix("pg-undo-")
    plan_id = f"plan-{suffix}"
    contact_id = f"contact-{suffix}"
    application_contact_id = f"app-contact-{suffix}"
    profile_url = f"https://www.linkedin.com/in/pg-undo-{suffix}"
    with workspace.database.session() as session:
        session.add(
            ContactPlan(
                id=plan_id,
                owner_id=graph.owner_id,
                application_id=graph.application_id,
                plan_number=1,
                status="completed",
                target_count=5,
                candidate_limit=12,
                confidence_floor=0.75,
                policy_version="contact-policy-v1",
                scoring_version="contact-score-v1",
                discovered_count=1,
                verified_count=1,
                selected_count=1,
                coverage_status="partial",
                exhausted=True,
                retryable=False,
                shortfall_reasons=[],
                version=1,
                started_at=NOW,
                finalized_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            Contact(
                id=contact_id,
                owner_id=graph.owner_id,
                identity_key=f"linkedin:pg-undo-{suffix}",
                identity_key_hash=_sha256(f"contact:{suffix}"),
                profile_url=profile_url,
                normalized_profile_url=profile_url,
                profile_source="linkedin",
                public_name="PG Contact",
                lifecycle="active",
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            ApplicationContact(
                id=application_contact_id,
                owner_id=graph.owner_id,
                application_id=graph.application_id,
                contact_plan_id=plan_id,
                contact_id=contact_id,
                discovery_provider="public_web",
                discovery_query="Example backend team",
                result_position=1,
                discovered_at=NOW,
                current_title="Staff Engineer",
                current_company="Example",
                category="team_peer",
                verification_status="verified",
                confidence=0.9,
                verified_at=NOW,
                employer_evidence_excerpt="Public profile lists Example.",
                employer_evidence_url=profile_url,
                employer_evidence_source="linkedin",
                employer_evidence_observed_at=NOW,
                why_relevant="Works on the target backend team.",
                relationship_status="unknown",
                team_proximity_status="inferred",
                score_total=900,
                score_components={"role_fit": 500, "evidence": 400},
                scoring_version="contact-score-v1",
                pool_rank=1,
                bench_rank=1,
                wave=1,
                bench_state="reserve",
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )

    with workspace.database.session() as session:
        started = start_outreach_sequence(
            session,
            owner_id=graph.owner_id,
            application_id=graph.application_id,
            expected_application_version=1,
            idempotency_key=f"outreach-start-{suffix}",
            keyring=workspace.keyring,
            now=NOW + timedelta(minutes=1),
        )
        assert started is not None and started.sequence is not None
        sequence_id = started.sequence.id
        sequence_version = started.sequence.version

    with workspace.database.session() as session:
        saved = save_outreach_message(
            session,
            owner_id=graph.owner_id,
            application_id=graph.application_id,
            sequence_id=sequence_id,
            payload=OutreachMessageCreate(
                application_contact_id=application_contact_id,
                kind="initial",
                body="Exact message for the PostgreSQL concurrency gate.",
            ),
            expected_sequence_version=sequence_version,
            idempotency_key=f"outreach-save-{suffix}",
            keyring=workspace.keyring,
            now=NOW + timedelta(minutes=2),
        )
        assert saved is not None and saved.sequence is not None
        message = saved.recipients[0].initial_message
        assert message is not None
        message_id = message.id
        sequence_version = saved.sequence.version

    with workspace.database.session() as session:
        copied = record_outreach_event(
            session,
            owner_id=graph.owner_id,
            application_id=graph.application_id,
            sequence_id=sequence_id,
            payload=OutreachCopiedEventCreate(
                event_type="copied",
                message_version_id=message_id,
            ),
            expected_sequence_version=sequence_version,
            idempotency_key=f"outreach-copy-{suffix}",
            keyring=workspace.keyring,
            now=NOW + timedelta(minutes=3),
        )
        assert copied is not None and copied.sequence is not None
        sequence_version = copied.sequence.version
    return sequence_id, message_id, sequence_version


class _SqlLockInterlock:
    """Pause one writer after locking a row and observe Undo waiting for it."""

    def __init__(self, table_name: str) -> None:
        self.table_name = table_name
        self.writer_locked = Event()
        self.undo_attempted_lock = Event()
        self.release_writer = Event()
        self._thread = local()

    def set_role(self, role: str) -> None:
        self._thread.role = role

    def clear_role(self) -> None:
        self._thread.role = None

    def before_cursor_execute(
        self,
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        if (
            getattr(self._thread, "role", None) == "undo"
            and _is_row_lock(statement, self.table_name)
        ):
            self.undo_attempted_lock.set()

    def after_cursor_execute(
        self,
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        if (
            getattr(self._thread, "role", None) == "writer"
            and not self.writer_locked.is_set()
            and _is_row_lock(statement, self.table_name)
        ):
            self.writer_locked.set()
            if not self.release_writer.wait(timeout=8):
                raise AssertionError("timed out waiting to release the locked writer")


class _ThreeWayCycleInterlock:
    """Force WriterRow -> Posting -> Opportunity -> WriterRow overlap."""

    def __init__(self, writer_table: str) -> None:
        self.writer_table = writer_table
        self.writer_first_locked = Event()
        self.scan_posting_locked = Event()
        self.undo_first_lock_attempted = Event()
        self.writer_posting_attempted = Event()
        self.scan_opportunity_attempted = Event()
        self.release_writer = Event()
        self.release_scan = Event()
        self._thread = local()

    def set_role(self, role: str) -> None:
        self._thread.role = role

    def clear_role(self) -> None:
        self._thread.role = None

    def before_cursor_execute(
        self,
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        role = getattr(self._thread, "role", None)
        if role == "undo" and _is_row_lock(statement, self.writer_table):
            self.undo_first_lock_attempted.set()
        elif role == "writer" and _is_row_lock(statement, "job_postings"):
            self.writer_posting_attempted.set()
        elif role == "scan" and _is_row_lock(statement, "owner_opportunities"):
            self.scan_opportunity_attempted.set()

    def after_cursor_execute(
        self,
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        role = getattr(self._thread, "role", None)
        if (
            role == "writer"
            and not self.writer_first_locked.is_set()
            and _is_row_lock(statement, self.writer_table)
        ):
            self.writer_first_locked.set()
            if not self.release_writer.wait(timeout=8):
                raise AssertionError("timed out releasing the first writer lock")
        elif (
            role == "scan"
            and not self.scan_posting_locked.is_set()
            and _is_row_lock(statement, "job_postings")
        ):
            self.scan_posting_locked.set()
            if not self.release_scan.wait(timeout=8):
                raise AssertionError("timed out releasing the Posting scan")


def _run_forced_overlap(
    database: Database,
    *,
    table_name: str,
    writer: Callable[[], _T],
    undo: Callable[[], object],
) -> tuple[_T, object]:
    interlock = _SqlLockInterlock(table_name)
    event.listen(
        database.engine,
        "before_cursor_execute",
        interlock.before_cursor_execute,
    )
    event.listen(
        database.engine,
        "after_cursor_execute",
        interlock.after_cursor_execute,
    )
    executor = ThreadPoolExecutor(max_workers=2)
    writer_future: Future[_T] | None = None
    undo_future: Future[object] | None = None
    try:
        writer_future = executor.submit(_run_as, interlock, "writer", writer)
        if not interlock.writer_locked.wait(timeout=8):
            pytest.fail(f"writer never locked {table_name}")
        undo_future = executor.submit(_run_as, interlock, "undo", undo)
        if not interlock.undo_attempted_lock.wait(timeout=8):
            pytest.fail(f"undo never attempted to lock {table_name}")
        interlock.release_writer.set()
        return writer_future.result(timeout=15), undo_future.result(timeout=15)
    finally:
        interlock.release_writer.set()
        executor.shutdown(wait=True, cancel_futures=True)
        event.remove(
            database.engine,
            "before_cursor_execute",
            interlock.before_cursor_execute,
        )
        event.remove(
            database.engine,
            "after_cursor_execute",
            interlock.after_cursor_execute,
        )


def _run_three_way_cycle(
    database: Database,
    *,
    interlock: _ThreeWayCycleInterlock,
    writer: Callable[[], _T],
    scan: Callable[[], object],
    undo: Callable[[], object],
) -> tuple[_T, object, object]:
    event.listen(
        database.engine,
        "before_cursor_execute",
        interlock.before_cursor_execute,
    )
    event.listen(
        database.engine,
        "after_cursor_execute",
        interlock.after_cursor_execute,
    )
    executor = ThreadPoolExecutor(max_workers=3)
    try:
        writer_future = executor.submit(_run_as, interlock, "writer", writer)
        if not interlock.writer_first_locked.wait(timeout=8):
            pytest.fail("writer never acquired its first row lock")
        scan_future = executor.submit(_run_as, interlock, "scan", scan)
        if not interlock.scan_posting_locked.wait(timeout=8):
            pytest.fail("scan never acquired its Posting row lock")
        undo_future = executor.submit(_run_as, interlock, "undo", undo)
        if not interlock.undo_first_lock_attempted.wait(timeout=8):
            pytest.fail("undo never attempted the contended row lock")

        undo_result = undo_future.result(timeout=8)
        interlock.release_writer.set()
        if not interlock.writer_posting_attempted.wait(timeout=8):
            pytest.fail("writer never attempted its Posting row lock")
        interlock.release_scan.set()
        if not interlock.scan_opportunity_attempted.wait(timeout=8):
            pytest.fail("scan never attempted its Opportunity row lock")
        scan_result = scan_future.result(timeout=12)
        writer_result = writer_future.result(timeout=12)
        return writer_result, scan_result, undo_result
    finally:
        interlock.release_writer.set()
        interlock.release_scan.set()
        executor.shutdown(wait=True, cancel_futures=True)
        event.remove(
            database.engine,
            "before_cursor_execute",
            interlock.before_cursor_execute,
        )
        event.remove(
            database.engine,
            "after_cursor_execute",
            interlock.after_cursor_execute,
        )


def _run_as(
    interlock: _SqlLockInterlock | _ThreeWayCycleInterlock,
    role: str,
    operation: Callable[[], _T],
) -> _T:
    interlock.set_role(role)
    try:
        return operation()
    finally:
        interlock.clear_role()


def _is_row_lock(statement: str, table_name: str) -> bool:
    normalized = " ".join(statement.lower().split())
    return f"from {table_name}" in normalized and "for update" in normalized


def _set_postgres_timeouts(session) -> None:
    session.execute(text("SET LOCAL lock_timeout = '6s'"))
    session.execute(text("SET LOCAL statement_timeout = '12s'"))


def test_pack_creation_and_undo_overlap_without_a_lock_order_deadlock(
    postgres_workspace: _PostgresWorkspace,
) -> None:
    graph = _seed_pursuit(postgres_workspace)
    resume_id = _create_resume(postgres_workspace, graph)
    suffix = graph.owner_id.removeprefix("pg-undo-")

    def create_pack():
        with postgres_workspace.database.session() as session:
            _set_postgres_timeouts(session)
            return create_application_pack(
                session,
                owner_id=graph.owner_id,
                application_id=graph.application_id,
                payload=ApplicationPackCreate(base_resume_version_id=resume_id),
                expected_application_version=1,
                idempotency_key=f"pack-create-race-{suffix}",
                keyring=postgres_workspace.keyring,
                now=NOW + timedelta(minutes=1),
            )

    def undo():
        try:
            with postgres_workspace.database.session() as session:
                _set_postgres_timeouts(session)
                return undo_application_pursuit(
                    session,
                    graph.owner_id,
                    graph.application_id,
                    1,
                    f"undo-pack-race-{suffix}",
                    NOW + timedelta(minutes=2),
                )
        except MutationPending as exc:
            return exc

    created, pending = _run_forced_overlap(
        postgres_workspace.database,
        table_name="applications",
        writer=create_pack,
        undo=undo,
    )

    assert created is not None and created.status is ApplicationPackStatus.draft
    assert isinstance(pending, MutationPending)
    assert str(pending) == "application is being updated; retry undo"
    with postgres_workspace.database.session() as session:
        restored = undo_application_pursuit(
            session,
            graph.owner_id,
            graph.application_id,
            1,
            f"undo-pack-race-{suffix}",
            NOW + timedelta(minutes=3),
        )
    assert restored is not None and restored.state.value == "inbox"
    with postgres_workspace.database.session() as session:
        assert session.get(Application, graph.application_id) is None
        assert (
            session.scalar(
                select(func.count(ApplicationPack.id)).where(
                    ApplicationPack.owner_id == graph.owner_id
                )
            )
            == 0
        )


def test_ready_to_apply_finishes_before_waiting_undo_and_undo_fails_safely(
    postgres_workspace: _PostgresWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _seed_pursuit(postgres_workspace)
    ids = _seed_reviewed_materials(postgres_workspace, graph)
    suffix = graph.owner_id.removeprefix("pg-undo-")

    def locked_posting_state(session, *, application, lock):
        statement = select(JobPosting).where(
            JobPosting.owner_id == application.owner_id,
            JobPosting.id == application.job_posting_id,
        )
        if lock:
            statement = statement.with_for_update()
        posting = session.scalar(statement)
        posting_version = session.scalar(
            select(JobPostingVersion).where(
                JobPostingVersion.owner_id == application.owner_id,
                JobPostingVersion.id == application.pursued_posting_version_id,
            )
        )
        assert posting is not None and posting_version is not None
        return posting, posting_version, [f"{graph.canonical_url}/apply"], True

    monkeypatch.setattr(
        submission_repository,
        "_posting_state",
        locked_posting_state,
    )
    monkeypatch.setattr(
        submission_repository,
        "load_application_pack",
        lambda *args, **kwargs: SimpleNamespace(
            status=ApplicationPackStatus.reviewed,
            pack=SimpleNamespace(id=ids["pack"]),
            current_revision=SimpleNamespace(id=ids["grounding"]),
            review_event=SimpleNamespace(id=ids["review"]),
            blockers=[],
        ),
    )
    monkeypatch.setattr(
        submission_repository,
        "load_application_artifacts",
        lambda *args, **kwargs: SimpleNamespace(
            status=ApplicationArtifactStatus.approved,
            pack=SimpleNamespace(id=ids["pack"]),
            current_revision=SimpleNamespace(id=ids["artifact"]),
            approved_revision=SimpleNamespace(id=ids["artifact"]),
            current_event=SimpleNamespace(id=ids["approval"]),
            approval_event=SimpleNamespace(id=ids["approval"]),
            tailored_resume_version=SimpleNamespace(id=ids["tailored_resume"]),
            blockers=[],
        ),
    )
    payload = ReadyToApplyTransitionCreate(
        to_stage="ready_to_apply",
        application_pack_id=ids["pack"],
        application_pack_revision_id=ids["grounding"],
        application_pack_review_event_id=ids["review"],
        application_artifact_revision_id=ids["artifact"],
        application_artifact_approval_event_id=ids["approval"],
        tailored_resume_version_id=ids["tailored_resume"],
        next_action_due_on=date(2026, 7, 19),
        confirm_ready=True,
    )

    def become_ready():
        with postgres_workspace.database.session() as session:
            _set_postgres_timeouts(session)
            return submission_repository.transition_application(
                session,
                owner_id=graph.owner_id,
                application_id=graph.application_id,
                payload=payload,
                expected_application_version=1,
                idempotency_key=f"ready-race-{suffix}",
                keyring=postgres_workspace.keyring,
                now=NOW + timedelta(minutes=1),
            )

    def undo():
        try:
            with postgres_workspace.database.session() as session:
                _set_postgres_timeouts(session)
                return undo_application_pursuit(
                    session,
                    graph.owner_id,
                    graph.application_id,
                    1,
                    f"undo-ready-race-{suffix}",
                    NOW + timedelta(minutes=2),
                )
        except MutationPending as exc:
            return exc

    transitioned, undo_result = _run_forced_overlap(
        postgres_workspace.database,
        table_name="applications",
        writer=become_ready,
        undo=undo,
    )

    assert transitioned is not None
    assert transitioned.application.stage.value == "ready_to_apply"
    assert isinstance(undo_result, MutationPending)
    assert str(undo_result) == "application is being updated; retry undo"
    with postgres_workspace.database.session() as session:
        application = session.get(Application, graph.application_id)
        assert application is not None
        assert application.stage == "ready_to_apply"
        assert application.version == 2
        restored = undo_application_pursuit(
            session,
            graph.owner_id,
            graph.application_id,
            2,
            f"undo-ready-race-{suffix}",
            NOW + timedelta(minutes=3),
        )
        assert restored is not None and restored.state.value == "inbox"


def test_three_transaction_cycle_is_broken_by_undo_application_nowait(
    postgres_workspace: _PostgresWorkspace,
) -> None:
    graph = _seed_pursuit(postgres_workspace)
    resume_id = _create_resume(postgres_workspace, graph)
    suffix = graph.owner_id.removeprefix("pg-undo-")
    interlock = _ThreeWayCycleInterlock("applications")

    def create_pack():
        with postgres_workspace.database.session() as session:
            _set_postgres_timeouts(session)
            return create_application_pack(
                session,
                owner_id=graph.owner_id,
                application_id=graph.application_id,
                payload=ApplicationPackCreate(base_resume_version_id=resume_id),
                expected_application_version=1,
                idempotency_key=f"pack-three-way-{suffix}",
                keyring=postgres_workspace.keyring,
                now=NOW + timedelta(minutes=1),
            )

    def scan_posting_then_opportunity() -> int:
        with postgres_workspace.database.session() as session:
            _set_postgres_timeouts(session)
            posting = session.scalar(
                select(JobPosting)
                .where(
                    JobPosting.owner_id == graph.owner_id,
                    JobPosting.id == graph.posting_id,
                )
                .with_for_update()
            )
            assert posting is not None
            opportunity = session.scalar(
                select(OwnerOpportunity)
                .where(
                    OwnerOpportunity.owner_id == graph.owner_id,
                    OwnerOpportunity.id == graph.opportunity_id,
                )
                .with_for_update()
            )
            assert opportunity is not None
            return opportunity.version

    def undo():
        try:
            with postgres_workspace.database.session() as session:
                _set_postgres_timeouts(session)
                return undo_application_pursuit(
                    session,
                    graph.owner_id,
                    graph.application_id,
                    1,
                    f"undo-three-way-{suffix}",
                    NOW + timedelta(minutes=2),
                )
        except MutationPending as exc:
            return exc

    created, scan_version, undo_result = _run_three_way_cycle(
        postgres_workspace.database,
        interlock=interlock,
        writer=create_pack,
        scan=scan_posting_then_opportunity,
        undo=undo,
    )

    # NOWAIT makes Undo release Opportunity instead of waiting on the
    # writer's Application lock and completing the three-row cycle.
    assert isinstance(undo_result, MutationPending)
    assert str(undo_result) == "application is being updated; retry undo"
    assert scan_version == 2

    assert created is not None and created.status is ApplicationPackStatus.draft
    with postgres_workspace.database.session() as session:
        restored = undo_application_pursuit(
            session,
            graph.owner_id,
            graph.application_id,
            1,
            f"undo-three-way-{suffix}",
            NOW + timedelta(minutes=3),
        )
    assert restored is not None and restored.state.value == "inbox"


def test_pack_revision_three_way_cycle_is_broken_by_pack_nowait(
    postgres_workspace: _PostgresWorkspace,
) -> None:
    graph = _seed_pursuit(postgres_workspace)
    reviewed = _seed_live_reviewed_pack(postgres_workspace, graph)
    assert reviewed.pack is not None and reviewed.current_revision is not None
    suffix = graph.owner_id.removeprefix("pg-undo-")
    interlock = _ThreeWayCycleInterlock("application_packs")
    next_revision = _unsupported_review_payload(reviewed.current_revision)

    def revise_pack():
        with postgres_workspace.database.session() as session:
            _set_postgres_timeouts(session)
            return create_application_pack_revision(
                session,
                owner_id=graph.owner_id,
                application_id=graph.application_id,
                pack_id=reviewed.pack.id,
                payload=next_revision,
                expected_pack_version=3,
                idempotency_key=f"pack-revision-three-way-{suffix}",
                keyring=postgres_workspace.keyring,
                now=NOW + timedelta(minutes=4),
            )

    def scan_posting_then_opportunity() -> int:
        with postgres_workspace.database.session() as session:
            _set_postgres_timeouts(session)
            posting = session.scalar(
                select(JobPosting)
                .where(
                    JobPosting.owner_id == graph.owner_id,
                    JobPosting.id == graph.posting_id,
                )
                .with_for_update()
            )
            assert posting is not None
            opportunity = session.scalar(
                select(OwnerOpportunity)
                .where(
                    OwnerOpportunity.owner_id == graph.owner_id,
                    OwnerOpportunity.id == graph.opportunity_id,
                )
                .with_for_update()
            )
            assert opportunity is not None
            return opportunity.version

    def undo():
        try:
            with postgres_workspace.database.session() as session:
                _set_postgres_timeouts(session)
                return undo_application_pursuit(
                    session,
                    graph.owner_id,
                    graph.application_id,
                    1,
                    f"undo-pack-revision-race-{suffix}",
                    NOW + timedelta(minutes=5),
                )
        except MutationPending as exc:
            return exc

    revised, scan_version, undo_result = _run_three_way_cycle(
        postgres_workspace.database,
        interlock=interlock,
        writer=revise_pack,
        scan=scan_posting_then_opportunity,
        undo=undo,
    )

    assert revised is not None and revised.pack is not None
    assert revised.current_revision is not None
    assert revised.pack.version == 4
    assert revised.current_revision.revision_number == 3
    assert scan_version == 2
    assert isinstance(undo_result, MutationPending)
    assert str(undo_result) == "application materials are being updated; retry undo"
    with postgres_workspace.database.session() as session:
        restored = undo_application_pursuit(
            session,
            graph.owner_id,
            graph.application_id,
            1,
            f"undo-pack-revision-race-{suffix}",
            NOW + timedelta(minutes=6),
        )
    assert restored is not None and restored.state.value == "inbox"


def test_artifact_event_overlap_returns_pending_then_retries_without_orphans(
    postgres_workspace: _PostgresWorkspace,
) -> None:
    graph = _seed_pursuit(postgres_workspace)
    reviewed = _seed_live_reviewed_pack(postgres_workspace, graph)
    assert reviewed.pack is not None and reviewed.current_revision is not None
    suffix = graph.owner_id.removeprefix("pg-undo-")

    with postgres_workspace.database.session() as session:
        generated = create_application_artifact_revision(
            session,
            owner_id=graph.owner_id,
            application_id=graph.application_id,
            pack_id=reviewed.pack.id,
            payload=ApplicationArtifactRevisionCreate(
                grounding_revision_id=reviewed.current_revision.id,
            ),
            expected_pack_version=3,
            idempotency_key=f"artifact-generate-race-{suffix}",
            keyring=postgres_workspace.keyring,
            now=NOW + timedelta(minutes=4),
        )
    assert generated is not None and generated.pack is not None
    assert generated.current_revision is not None
    artifact_revision_id = generated.current_revision.id

    def reject_artifact():
        with postgres_workspace.database.session() as session:
            _set_postgres_timeouts(session)
            return record_application_artifact_event(
                session,
                owner_id=graph.owner_id,
                application_id=graph.application_id,
                pack_id=reviewed.pack.id,
                payload=ApplicationArtifactEventCreate(
                    event_type="rejected",
                    artifact_revision_id=artifact_revision_id,
                ),
                expected_pack_version=4,
                idempotency_key=f"artifact-reject-race-{suffix}",
                keyring=postgres_workspace.keyring,
                now=NOW + timedelta(minutes=5),
            )

    def undo():
        try:
            with postgres_workspace.database.session() as session:
                _set_postgres_timeouts(session)
                return undo_application_pursuit(
                    session,
                    graph.owner_id,
                    graph.application_id,
                    1,
                    f"undo-artifact-race-{suffix}",
                    NOW + timedelta(minutes=6),
                )
        except MutationPending as exc:
            return exc

    rejected, undo_result = _run_forced_overlap(
        postgres_workspace.database,
        table_name="application_packs",
        writer=reject_artifact,
        undo=undo,
    )

    assert rejected is not None and rejected.pack is not None
    assert rejected.current_event is not None
    assert rejected.current_event.event_type == "rejected"
    assert rejected.pack.version == 5
    assert isinstance(undo_result, MutationPending)
    assert str(undo_result) == "application materials are being updated; retry undo"
    with postgres_workspace.database.session() as session:
        restored = undo_application_pursuit(
            session,
            graph.owner_id,
            graph.application_id,
            1,
            f"undo-artifact-race-{suffix}",
            NOW + timedelta(minutes=7),
        )
    assert restored is not None and restored.state.value == "inbox"
    with postgres_workspace.database.session() as session:
        assert session.get(Application, graph.application_id) is None
        assert (
            session.scalar(
                select(func.count(ApplicationArtifactRevision.id)).where(
                    ApplicationArtifactRevision.owner_id == graph.owner_id
                )
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count(ApplicationArtifactEvent.id)).where(
                    ApplicationArtifactEvent.owner_id == graph.owner_id
                )
            )
            == 0
        )


def test_two_same_key_undos_return_one_stable_replay(
    postgres_workspace: _PostgresWorkspace,
) -> None:
    graph = _seed_pursuit(postgres_workspace)
    suffix = graph.owner_id.removeprefix("pg-undo-")
    start = Barrier(2)

    def undo(_worker: int):
        with postgres_workspace.database.session() as session:
            _set_postgres_timeouts(session)
            start.wait(timeout=8)
            return undo_application_pursuit(
                session,
                graph.owner_id,
                graph.application_id,
                1,
                f"same-undo-key-{suffix}",
                NOW + timedelta(minutes=1),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(undo, (1, 2)))

    assert results[0] is not None and results[1] is not None
    assert results[0] == results[1]
    with postgres_workspace.database.session() as session:
        assert session.get(Application, graph.application_id) is None
        assert (
            session.scalar(
                select(func.count(OpportunityDecisionEvent.id)).where(
                    OpportunityDecisionEvent.owner_id == graph.owner_id,
                    OpportunityDecisionEvent.owner_opportunity_id
                    == graph.opportunity_id,
                )
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count(OwnerMutationReceipt.id)).where(
                    OwnerMutationReceipt.owner_id == graph.owner_id,
                    OwnerMutationReceipt.namespace
                    == f"application.undo_pursuit:{graph.application_id}",
                )
            )
            == 1
        )


def test_terminal_contact_reconciliation_and_undo_share_job_then_plan_order(
    postgres_workspace: _PostgresWorkspace,
) -> None:
    graph = _seed_pursuit(postgres_workspace)
    suffix = graph.owner_id.removeprefix("pg-undo-")
    with postgres_workspace.database.session() as session:
        queued = create_contact_search(
            session,
            owner_id=graph.owner_id,
            application_id=graph.application_id,
            expected_application_version=1,
            idempotency_key=f"contact-search-reconcile-{suffix}",
            now=NOW + timedelta(minutes=1),
        )
        assert queued is not None and queued.created is True
        plan_id = queued.plan.id
        job_id = queued.plan.background_job_id
        assert job_id is not None

    # Model lease recovery: the queue is terminal while its active-looking
    # plan still needs the reconciler to publish the matching terminal state.
    with postgres_workspace.database.session() as session:
        job = session.get(BackgroundJob, job_id)
        assert job is not None
        assert job.kind == CONTACT_SEARCH_JOB_KIND
        job.status = "dead_letter"
        job.stage = "dead_letter"
        job.last_error = "lease_expired"
        job.failed_at = NOW + timedelta(minutes=2)
        job.dead_lettered_at = NOW + timedelta(minutes=2)
        job.updated_at = NOW + timedelta(minutes=2)
        job.version += 1

    def reconcile() -> int:
        with postgres_workspace.database.session() as session:
            _set_postgres_timeouts(session)
            return reconcile_terminal_contact_plans(
                session,
                now=NOW + timedelta(minutes=3),
            )

    def undo():
        with postgres_workspace.database.session() as session:
            _set_postgres_timeouts(session)
            return undo_application_pursuit(
                session,
                graph.owner_id,
                graph.application_id,
                1,
                f"undo-reconcile-race-{suffix}",
                NOW + timedelta(minutes=4),
            )

    reconciled, restored = _run_forced_overlap(
        postgres_workspace.database,
        table_name="background_jobs",
        writer=reconcile,
        undo=undo,
    )

    assert reconciled == 1
    assert restored is not None and restored.state.value == "inbox"
    with postgres_workspace.database.session() as session:
        assert session.get(Application, graph.application_id) is None
        assert session.get(ContactPlan, plan_id) is None
        assert session.get(BackgroundJob, job_id) is None
        assert (
            session.scalar(
                select(func.count(BackgroundJobEvent.id)).where(
                    BackgroundJobEvent.job_id == job_id
                )
            )
            == 0
        )


def test_marked_sent_wins_sequence_lock_and_prevents_waiting_undo(
    postgres_workspace: _PostgresWorkspace,
) -> None:
    graph = _seed_pursuit(postgres_workspace)
    sequence_id, message_id, sequence_version = _seed_outreach(
        postgres_workspace,
        graph,
    )
    suffix = graph.owner_id.removeprefix("pg-undo-")

    def mark_sent():
        with postgres_workspace.database.session() as session:
            _set_postgres_timeouts(session)
            return record_outreach_event(
                session,
                owner_id=graph.owner_id,
                application_id=graph.application_id,
                sequence_id=sequence_id,
                payload=OutreachMarkedSentEventCreate(
                    event_type="marked_sent",
                    message_version_id=message_id,
                    channel="linkedin",
                    confirm_exact_version=True,
                ),
                expected_sequence_version=sequence_version,
                idempotency_key=f"outreach-sent-race-{suffix}",
                keyring=postgres_workspace.keyring,
                now=NOW + timedelta(minutes=4),
            )

    def undo():
        try:
            with postgres_workspace.database.session() as session:
                _set_postgres_timeouts(session)
                return undo_application_pursuit(
                    session,
                    graph.owner_id,
                    graph.application_id,
                    1,
                    f"undo-outreach-race-{suffix}",
                    NOW + timedelta(minutes=5),
                )
        except ResourceConflict as exc:
            return exc

    sent, undo_result = _run_forced_overlap(
        postgres_workspace.database,
        table_name="outreach_sequences",
        writer=mark_sent,
        undo=undo,
    )

    assert sent is not None and sent.sequence is not None
    assert isinstance(undo_result, ResourceConflict)
    assert "sent outreach" in str(undo_result)
    with postgres_workspace.database.session() as session:
        assert session.get(Application, graph.application_id) is not None
        assert session.get(OutreachSequence, sequence_id) is not None
        assert (
            session.scalar(
                select(func.count(OutreachEvent.id)).where(
                    OutreachEvent.owner_id == graph.owner_id,
                    OutreachEvent.application_id == graph.application_id,
                    OutreachEvent.event_type == "marked_sent",
                )
            )
            == 1
        )
        opportunity = session.get(OwnerOpportunity, graph.opportunity_id)
        assert opportunity is not None and opportunity.decision == "pursued"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
