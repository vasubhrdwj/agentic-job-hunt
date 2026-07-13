"""Focused tests for provider-free contact-search creation."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from job_hunt_agent.contact_repository import load_application_contact_bench
from job_hunt_agent.contact_search_repository import (
    ContactSearchRepositoryError,
    create_contact_search,
)
from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    ActionItem,
    Application,
    ApplicationActivityEvent,
    BackgroundJob,
    Base,
    ContactPlan,
    JobPosting,
    JobPostingVersion,
    Owner,
    OwnerMutationReceipt,
    OwnerOpportunity,
)
from job_hunt_agent.repository_errors import ResourceConflict, VersionConflict


NOW = datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def contact_search_db(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'contact-search.db'}")
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        session.add_all(
            [
                Owner(id="owner-a", display_name="Owner A", timezone="UTC"),
                Owner(id="owner-b", display_name="Owner B", timezone="UTC"),
            ]
        )
        session.flush()
        session.add(
            JobPosting(
                id="posting-a",
                owner_id="owner-a",
                identity_kind="native",
                identity_key="source:greenhouse:acme:123",
                identity_key_hash="1" * 64,
                source="greenhouse",
                company_slug="acme",
                source_job_id="123",
                canonical_url="https://boards.greenhouse.io/acme/jobs/123",
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
                id="version-a",
                owner_id="owner-a",
                job_posting_id="posting-a",
                version_number=1,
                content_hash="2" * 64,
                source="greenhouse",
                source_job_id="123",
                company_name="Acme",
                title="Staff Backend Engineer",
                canonical_url="https://boards.greenhouse.io/acme/jobs/123",
                apply_urls=["https://boards.greenhouse.io/acme/jobs/123"],
                location="Remote India",
                summary="Build reliable backend systems.",
                description="Design and operate reliable backend systems.",
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
                id="opportunity-a",
                owner_id="owner-a",
                job_posting_id="posting-a",
                decision="pursued",
                reviewed_posting_version_id="version-a",
                decision_updated_at=NOW,
                first_surfaced_at=NOW,
                last_surfaced_at=NOW,
                version=2,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            Application(
                id="application-a",
                owner_id="owner-a",
                owner_opportunity_id="opportunity-a",
                job_posting_id="posting-a",
                pursued_posting_version_id="version-a",
                stage="pursuing",
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            ActionItem(
                id="action-a",
                owner_id="owner-a",
                application_id="application-a",
                kind="review_and_prepare_application",
                title="Review role and prepare application",
                status="open",
                due_on=date(2026, 7, 14),
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            ApplicationActivityEvent(
                id="activity-a",
                owner_id="owner-a",
                application_id="application-a",
                sequence_number=1,
                event_type="application_created",
                from_stage=None,
                to_stage="pursuing",
                action_item_id="action-a",
                occurred_at=NOW,
                created_at=NOW,
            )
        )
    try:
        yield database
    finally:
        database.dispose()


def _count(session, model: type) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def test_contact_search_is_queued_once_and_all_active_retries_reuse_it(
    contact_search_db: Database,
) -> None:
    with contact_search_db.session() as session:
        first = create_contact_search(
            session,
            owner_id="owner-a",
            application_id="application-a",
            expected_application_version=1,
            idempotency_key="search-once",
            now=NOW,
        )
        replay = create_contact_search(
            session,
            owner_id="owner-a",
            application_id="application-a",
            expected_application_version=1,
            idempotency_key="search-once",
            now=NOW,
        )
        second_key = create_contact_search(
            session,
            owner_id="owner-a",
            application_id="application-a",
            expected_application_version=99,
            idempotency_key="ambiguous-client-retry",
            now=NOW,
        )

        assert first is not None and first.created is True
        assert replay is not None and replay.created is False
        assert second_key is not None and second_key.created is False
        assert first.plan.id == replay.plan.id == second_key.plan.id
        assert first.plan.plan_number == 1
        assert first.plan.target_count == 5
        assert first.plan.candidate_limit == 12
        assert first.plan.background_job_id is not None
        assert _count(session, ContactPlan) == 1
        assert _count(session, BackgroundJob) == 1
        assert _count(session, OwnerMutationReceipt) == 2
        job = session.get(BackgroundJob, first.plan.background_job_id)
        assert job is not None
        assert job.kind == "discover_contacts"
        assert job.subject_type == "contact_plan"
        assert job.subject_id == first.plan.id
        assert job.run_after.replace(tzinfo=job.run_after.tzinfo or timezone.utc) == NOW
        assert job.payload == {
            "contact_plan_id": first.plan.id,
            "candidate_limit": 12,
            "target_count": 5,
        }


def test_completed_search_allows_a_new_versioned_attempt(
    contact_search_db: Database,
) -> None:
    with contact_search_db.session() as session:
        first = create_contact_search(
            session,
            owner_id="owner-a",
            application_id="application-a",
            expected_application_version=1,
            idempotency_key="first-search",
            now=NOW,
        )
        assert first is not None
        first.plan.status = "completed"
        first.plan.coverage_status = "partial"
        first.plan.exhausted = True
        first.plan.retryable = False
        first.plan.shortfall_reasons = [
            {
                "code": "insufficient_profiles",
                "count": 5,
                "detail": "No public profiles passed the evidence threshold.",
            }
        ]
        first.plan.finalized_at = NOW
        first.plan.version += 1

    with contact_search_db.session() as session:
        second = create_contact_search(
            session,
            owner_id="owner-a",
            application_id="application-a",
            expected_application_version=1,
            idempotency_key="second-search",
            now=NOW,
        )
        assert second is not None and second.created is True
        assert second.plan.plan_number == 2
        assert second.plan.id != first.plan.id
        assert _count(session, ContactPlan) == 2
        assert _count(session, BackgroundJob) == 2


def test_terminal_job_repair_persists_before_a_stale_request_can_retry(
    contact_search_db: Database,
) -> None:
    with contact_search_db.session() as session:
        first = create_contact_search(
            session,
            owner_id="owner-a",
            application_id="application-a",
            expected_application_version=1,
            idempotency_key="active-job",
            now=NOW,
        )
        assert first is not None and first.plan.background_job_id is not None
        first_job_id = first.plan.background_job_id

    with pytest.raises(IntegrityError):
        with contact_search_db.session() as session:
            job = session.get(BackgroundJob, first_job_id)
            assert job is not None
            session.delete(job)

    with contact_search_db.session() as session:
        job = session.get(BackgroundJob, first_job_id)
        assert job is not None
        job.status = "dead_letter"
        job.stage = "dead_letter"
        job.dead_lettered_at = NOW

    with contact_search_db.session() as session:
        repaired = create_contact_search(
            session,
            owner_id="owner-a",
            application_id="application-a",
            expected_application_version=99,
            idempotency_key="repair-dead-job",
            now=NOW,
        )
        assert repaired is not None and repaired.created is False
        assert repaired.plan.id == first.plan.id
        assert repaired.plan.plan_number == 1
        assert repaired.plan.status == "failed"
        assert repaired.plan.error_code == "search_job_unavailable"
        assert repaired.plan.retryable is True

    with contact_search_db.session() as session:
        readable = load_application_contact_bench(
            session,
            owner_id="owner-a",
            application_id="application-a",
            now=NOW,
        )
        assert readable is not None
        assert readable.status.value == "failed"
        assert readable.current_search is not None
        assert readable.current_search.error_code == "search_job_unavailable"
        assert readable.current_search.retryable is True

    with contact_search_db.session() as session:
        retried = create_contact_search(
            session,
            owner_id="owner-a",
            application_id="application-a",
            expected_application_version=1,
            idempotency_key="replace-dead-job",
            now=NOW,
        )
        assert retried is not None and retried.created is True
        assert retried.plan.plan_number == 2
        old_plan = session.get(ContactPlan, first.plan.id)
        assert old_plan is not None
        assert old_plan.status == "failed"
        assert old_plan.error_code == "search_job_unavailable"
        assert old_plan.retryable is True


def test_terminal_job_repair_remains_readable_while_posting_is_closed(
    contact_search_db: Database,
) -> None:
    with contact_search_db.session() as session:
        first = create_contact_search(
            session,
            owner_id="owner-a",
            application_id="application-a",
            expected_application_version=1,
            idempotency_key="active-before-close",
            now=NOW,
        )
        assert first is not None and first.plan.background_job_id is not None
        first_job_id = first.plan.background_job_id

    with contact_search_db.session() as session:
        job = session.get(BackgroundJob, first_job_id)
        posting = session.get(JobPosting, "posting-a")
        assert job is not None and posting is not None
        job.status = "dead_letter"
        job.stage = "dead_letter"
        job.dead_lettered_at = NOW
        posting.lifecycle_state = "closed"
        posting.closure_reason = "explicit"
        posting.closed_at = NOW

    with contact_search_db.session() as session:
        repaired = create_contact_search(
            session,
            owner_id="owner-a",
            application_id="application-a",
            expected_application_version=1,
            idempotency_key="repair-after-close",
            now=NOW,
        )
        assert repaired is not None and repaired.created is False
        assert repaired.plan.status == "failed"

    with contact_search_db.session() as session:
        readable = load_application_contact_bench(
            session,
            owner_id="owner-a",
            application_id="application-a",
            now=NOW,
        )
        assert readable is not None
        assert readable.status.value == "failed"
        assert readable.current_search is not None
        assert readable.current_search.retryable is True

    with pytest.raises(ResourceConflict, match="closed postings"):
        with contact_search_db.session() as session:
            create_contact_search(
                session,
                owner_id="owner-a",
                application_id="application-a",
                expected_application_version=1,
                idempotency_key="retry-after-close",
                now=NOW,
            )

    with contact_search_db.session() as session:
        posting = session.get(JobPosting, "posting-a")
        assert posting is not None
        posting.lifecycle_state = "open"
        posting.closure_reason = None
        posting.closed_at = None

    with contact_search_db.session() as session:
        retried = create_contact_search(
            session,
            owner_id="owner-a",
            application_id="application-a",
            expected_application_version=1,
            idempotency_key="retry-after-reopen",
            now=NOW,
        )
        assert retried is not None and retried.created is True
        assert retried.plan.plan_number == 2


@pytest.mark.parametrize(
    ("deleted", "result_version"),
    [
        (True, 1),
        (False, None),
        (False, 0),
        (False, 2),
    ],
)
def test_contact_search_replay_rejects_inconsistent_receipt_metadata(
    contact_search_db: Database,
    deleted: bool,
    result_version: int | None,
) -> None:
    with contact_search_db.session() as session:
        created = create_contact_search(
            session,
            owner_id="owner-a",
            application_id="application-a",
            expected_application_version=1,
            idempotency_key="receipt-metadata",
            now=NOW,
        )
        assert created is not None

    with contact_search_db.session() as session:
        receipt = session.scalar(
            select(OwnerMutationReceipt).where(
                OwnerMutationReceipt.owner_id == "owner-a",
                OwnerMutationReceipt.namespace == "contact_search.create:application-a",
            )
        )
        assert receipt is not None
        receipt.deleted = deleted
        receipt.result_version = result_version

    with contact_search_db.session() as session:
        with pytest.raises(ContactSearchRepositoryError, match="result metadata|ahead"):
            create_contact_search(
                session,
                owner_id="owner-a",
                application_id="application-a",
                expected_application_version=1,
                idempotency_key="receipt-metadata",
                now=NOW,
            )


def test_contact_search_replay_accepts_an_older_valid_result_version(
    contact_search_db: Database,
) -> None:
    with contact_search_db.session() as session:
        created = create_contact_search(
            session,
            owner_id="owner-a",
            application_id="application-a",
            expected_application_version=1,
            idempotency_key="receipt-version",
            now=NOW,
        )
        assert created is not None
        created.plan.version += 1

    with contact_search_db.session() as session:
        replayed = create_contact_search(
            session,
            owner_id="owner-a",
            application_id="application-a",
            expected_application_version=99,
            idempotency_key="receipt-version",
            now=NOW,
        )
        assert replayed is not None and replayed.created is False
        assert replayed.plan.version == 2


def test_contact_search_masks_foreign_applications_and_checks_new_attempts(
    contact_search_db: Database,
) -> None:
    with contact_search_db.session() as session:
        assert (
            create_contact_search(
                session,
                owner_id="owner-b",
                application_id="application-a",
                expected_application_version=1,
                idempotency_key="foreign",
                now=NOW,
            )
            is None
        )
    with pytest.raises(VersionConflict):
        with contact_search_db.session() as session:
            create_contact_search(
                session,
                owner_id="owner-a",
                application_id="application-a",
                expected_application_version=2,
                idempotency_key="stale",
                now=NOW,
            )

    # The failed mutation transaction is rolled back by the context manager;
    # close the posting in a fresh unit of work and verify the provider job is
    # never queued.
    with contact_search_db.session() as session:
        posting = session.get(JobPosting, "posting-a")
        assert posting is not None
        posting.lifecycle_state = "closed"
        posting.closure_reason = "explicit"
        posting.closed_at = NOW

    with pytest.raises(ResourceConflict, match="closed postings"):
        with contact_search_db.session() as session:
            create_contact_search(
                session,
                owner_id="owner-a",
                application_id="application-a",
                expected_application_version=1,
                idempotency_key="closed",
                now=NOW,
            )

    with contact_search_db.session() as session:
        assert _count(session, ContactPlan) == 0
        assert _count(session, BackgroundJob) == 0
