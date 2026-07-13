"""Focused tests for provider-free contact-search creation."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from job_hunt_agent.contact_search_repository import create_contact_search
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
        first.plan.shortfall_reasons = ["insufficient_profiles"]
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


def test_active_plan_cannot_lose_its_job_and_recovers_a_terminal_job(
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
        recovered = create_contact_search(
            session,
            owner_id="owner-a",
            application_id="application-a",
            expected_application_version=1,
            idempotency_key="replace-dead-job",
            now=NOW,
        )
        assert recovered is not None and recovered.created is True
        assert recovered.plan.plan_number == 2
        old_plan = session.get(ContactPlan, first.plan.id)
        assert old_plan is not None
        assert old_plan.status == "failed"
        assert old_plan.error_code == "search_job_unavailable"
        assert old_plan.retryable is True


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

    with contact_search_db.session() as session:
        with pytest.raises(ResourceConflict, match="closed postings"):
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
