"""Database projection tests for the verified application contact bench."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from job_hunt_agent.contact_repository import (
    ContactRepositoryError,
    load_application_contact_bench,
)
from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    Application,
    ApplicationContact,
    BackgroundJob,
    Base,
    Contact,
    ContactPlan,
    JobPosting,
    JobPostingVersion,
    Owner,
    OwnerOpportunity,
)


NOW = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def contact_database(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'contacts.db'}")
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        session.add_all(
            [
                Owner(id="owner-a", display_name="Owner A", timezone="Asia/Kolkata"),
                Owner(id="owner-b", display_name="Owner B", timezone="UTC"),
            ]
        )
        session.flush()
        session.add(
            JobPosting(
                id="posting1",
                owner_id="owner-a",
                identity_kind="native",
                identity_key="source:greenhouse:example:123",
                identity_key_hash="1" * 64,
                source="greenhouse",
                company_slug="example",
                source_job_id="123",
                canonical_url="https://careers.example.com/jobs/123",
                lifecycle_state="open",
                consecutive_complete_omissions=0,
                first_confirmed_at=NOW - timedelta(days=1),
                last_confirmed_at=NOW,
                version=1,
                created_at=NOW - timedelta(days=1),
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            JobPostingVersion(
                id="postingversion1",
                owner_id="owner-a",
                job_posting_id="posting1",
                version_number=1,
                content_hash="2" * 64,
                source="greenhouse",
                source_job_id="123",
                company_name="Example",
                title="Senior Backend Engineer",
                canonical_url="https://careers.example.com/jobs/123",
                apply_urls=["https://careers.example.com/jobs/123"],
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
                id="opportunity1",
                owner_id="owner-a",
                job_posting_id="posting1",
                decision="pursued",
                reviewed_posting_version_id="postingversion1",
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
                id="application1",
                owner_id="owner-a",
                owner_opportunity_id="opportunity1",
                job_posting_id="posting1",
                pursued_posting_version_id="postingversion1",
                stage="pursuing",
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    try:
        yield database
    finally:
        database.dispose()


def _add_completed_plan(
    database: Database,
    *,
    plan_id: str = "contactplan1",
    plan_number: int = 1,
    selected_count: int = 3,
    confidence: float = 0.9,
    confidence_floor: float = 0.75,
) -> None:
    with database.session() as session:
        plan = ContactPlan(
            id=plan_id,
            owner_id="owner-a",
            application_id="application1",
            plan_number=plan_number,
            status="completed",
            target_count=5,
            candidate_limit=12,
            confidence_floor=confidence_floor,
            policy_version="contact-policy-v1",
            scoring_version="contact-score-v1",
            discovered_count=7,
            verified_count=selected_count,
            selected_count=selected_count,
            coverage_status="partial",
            exhausted=True,
            retryable=False,
            shortfall_reasons=["insufficient_verified_profiles"],
            error_code=None,
            version=1,
            started_at=NOW - timedelta(minutes=2),
            finalized_at=NOW,
            created_at=NOW - timedelta(minutes=3),
            updated_at=NOW,
        )
        session.add(plan)
        session.flush()
        for rank in range(1, selected_count + 1):
            contact = Contact(
                id=f"contact{plan_number}{rank}",
                owner_id="owner-a",
                identity_key=f"linkedin:person-{plan_number}-{rank}",
                identity_key_hash=f"{plan_number}{rank}".ljust(64, "0"),
                profile_url=(
                    f"https://www.linkedin.com/in/person-{plan_number}-{rank}/"
                ),
                normalized_profile_url=(
                    f"https://www.linkedin.com/in/person-{plan_number}-{rank}"
                ),
                profile_source="linkedin",
                public_name=f"Person {rank}",
                lifecycle="active",
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
            session.add(contact)
            session.flush()
            session.add(
                ApplicationContact(
                    id=f"applicationcontact{plan_number}{rank}",
                    owner_id="owner-a",
                    application_id="application1",
                    contact_plan_id=plan_id,
                    contact_id=contact.id,
                    discovery_provider="public-search",
                    discovery_query="Example engineering team",
                    result_position=rank,
                    discovered_at=NOW - timedelta(minutes=2),
                    current_title="Staff Engineer",
                    current_company="Example",
                    category="team_peer",
                    verification_status="verified",
                    confidence=confidence,
                    verified_at=NOW - timedelta(minutes=1),
                    employer_evidence_excerpt=(
                        "Public profile lists a current Staff Engineer role at Example."
                    ),
                    employer_evidence_url=contact.normalized_profile_url,
                    employer_evidence_source="linkedin",
                    employer_evidence_observed_at=NOW - timedelta(minutes=1),
                    why_relevant="Works on the team adjacent to this role.",
                    relationship_status="unknown",
                    team_proximity_status="unknown",
                    score_total=800 - rank,
                    score_components={"employer": 400, "role": 399 - rank},
                    scoring_version="contact-score-v1",
                    pool_rank=rank,
                    bench_rank=rank,
                    wave=rank,
                    bench_state="reserve",
                    version=1,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )


def test_contact_bench_distinguishes_not_started_and_masks_other_owners(
    contact_database: Database,
) -> None:
    with contact_database.session() as session:
        own = load_application_contact_bench(session, "owner-a", "application1")
        foreign = load_application_contact_bench(session, "owner-b", "application1")
        missing = load_application_contact_bench(session, "owner-a", "missingapplication")

    assert own is not None
    assert own.status.value == "not_started"
    assert own.verified_count == 0
    assert own.current_search is None
    assert own.last_completed_result is None
    assert foreign is None
    assert missing is None


def test_completed_partial_bench_returns_exactly_the_verified_people(
    contact_database: Database,
) -> None:
    _add_completed_plan(contact_database)

    with contact_database.session() as session:
        response = load_application_contact_bench(session, "owner-a", "application1")

    assert response is not None
    assert response.status.value == "completed"
    assert response.verified_count == 3
    assert response.coverage_status.value == "partial"
    assert response.last_completed_result is not None
    assert [item.bench_rank for item in response.last_completed_result.contacts] == [1, 2, 3]
    assert all(
        item.confidence >= 0.75
        and item.employer_evidence.url.startswith("https://")
        for item in response.last_completed_result.contacts
    )
    assert response.last_completed_result.shortfall_reasons


def test_new_retry_preserves_the_last_completed_result(
    contact_database: Database,
) -> None:
    _add_completed_plan(contact_database)
    with contact_database.session() as session:
        session.add(
            BackgroundJob(
                id="contactjob2",
                kind="discover_contacts",
                owner_id="owner-a",
                dedupe_scope="owner:owner-a",
                subject_type="contact_plan",
                subject_id="contactplan2",
                payload={"contact_plan_id": "contactplan2"},
                dedupe_key="contacts:contactplan2",
                status="queued",
                priority=75,
                attempt_count=0,
                max_attempts=3,
                run_after=NOW + timedelta(minutes=1),
                stage="queued",
                version=1,
                created_at=NOW + timedelta(minutes=1),
                updated_at=NOW + timedelta(minutes=1),
            )
        )
        session.flush()
        session.add(
            ContactPlan(
                id="contactplan2",
                owner_id="owner-a",
                application_id="application1",
                plan_number=2,
                status="queued",
                target_count=5,
                candidate_limit=12,
                confidence_floor=0.75,
                policy_version="contact-policy-v1",
                scoring_version="contact-score-v1",
                background_job_id="contactjob2",
                discovered_count=0,
                verified_count=0,
                selected_count=0,
                coverage_status="pending",
                exhausted=False,
                retryable=False,
                shortfall_reasons=[],
                error_code=None,
                version=1,
                created_at=NOW + timedelta(minutes=1),
                updated_at=NOW + timedelta(minutes=1),
            )
        )

    with contact_database.session() as session:
        response = load_application_contact_bench(session, "owner-a", "application1")

    assert response is not None
    assert response.status.value == "queued"
    assert response.current_search is not None
    assert response.current_search.plan_number == 2
    assert response.last_completed_result is not None
    assert response.last_completed_result.plan_number == 1
    assert response.verified_count == 3

    with contact_database.session() as session:
        job = session.get(BackgroundJob, "contactjob2")
        assert job is not None
        job.status = "succeeded"
        job.completed_at = NOW + timedelta(minutes=2)
    with contact_database.session() as session:
        with pytest.raises(ContactRepositoryError, match="active queue job"):
            load_application_contact_bench(session, "owner-a", "application1")


def test_selected_contact_below_the_plan_floor_fails_closed(
    contact_database: Database,
) -> None:
    _add_completed_plan(
        contact_database,
        confidence=0.8,
        confidence_floor=0.9,
    )

    with contact_database.session() as session:
        with pytest.raises(ContactRepositoryError, match="evidence floor"):
            load_application_contact_bench(session, "owner-a", "application1")


@pytest.mark.parametrize(
    ("unsafe_state", "message"),
    [
        ("do_not_contact", "non-active contact"),
        ("future_cooldown", "cooldown has not elapsed"),
    ],
)
def test_read_never_presents_an_ineligible_contact_as_ready(
    contact_database: Database,
    unsafe_state: str,
    message: str,
) -> None:
    _add_completed_plan(contact_database)
    with contact_database.session() as session:
        row = session.get(ApplicationContact, "applicationcontact11")
        contact = session.get(Contact, "contact11")
        assert row is not None and contact is not None
        row.bench_state = "ready"
        row.unlocked_at = NOW
        if unsafe_state == "do_not_contact":
            contact.lifecycle = "do_not_contact"
            contact.do_not_contact_at = NOW
        else:
            row.cooldown_until = NOW + timedelta(hours=1)

    with contact_database.session() as session:
        with pytest.raises(ContactRepositoryError, match=message):
            load_application_contact_bench(
                session,
                "owner-a",
                "application1",
                now=NOW,
            )
