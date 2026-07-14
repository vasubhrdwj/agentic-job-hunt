"""Focused repository tests for atomic, owner-scoped opportunity pursuit."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import func, select

from job_hunt_agent.application_repository import (
    list_application_activity,
    list_applications,
    load_application_detail,
    pursue_owner_opportunity,
)
from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    ActionItem,
    Application,
    ApplicationActivityEvent,
    Base,
    JobPosting,
    JobPostingVersion,
    OpportunityDecisionEvent,
    OwnerMutationReceipt,
    Owner,
    OwnerOpportunity,
)
from job_hunt_agent.opportunity_repository import (
    OpportunityNotFound,
    _decision_event_response,
)
from job_hunt_agent.opportunity_schemas import PursueOpportunityRequest
from job_hunt_agent.repository_errors import ResourceConflict, VersionConflict
from job_hunt_agent.security import DataKeyring


NOW = datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc)
LOCAL_TODAY = date(2026, 7, 14)


@pytest.fixture
def application_repository_db(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'application-repository.db'}")
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        session.add_all(
            [
                Owner(
                    id="owner-a",
                    display_name="Owner A",
                    timezone="Asia/Kolkata",
                    created_at=NOW,
                    updated_at=NOW,
                ),
                Owner(
                    id="owner-b",
                    display_name="Owner B",
                    timezone="UTC",
                    created_at=NOW,
                    updated_at=NOW,
                ),
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
                first_confirmed_at=NOW - timedelta(days=2),
                last_confirmed_at=NOW,
                version=1,
                created_at=NOW - timedelta(days=2),
                updated_at=NOW,
            )
        )
        session.flush()
        session.add_all(
            [
                _posting_version(
                    version_id="posting-version-1",
                    version_number=1,
                    title="Senior Backend Engineer",
                    observed_at=NOW - timedelta(days=1),
                ),
                _posting_version(
                    version_id="posting-version-2",
                    version_number=2,
                    title="Staff Backend Engineer",
                    observed_at=NOW,
                ),
            ]
        )
        session.flush()
        session.add(
            OwnerOpportunity(
                id="opportunity-a",
                owner_id="owner-a",
                job_posting_id="posting-a",
                decision="inbox",
                first_surfaced_at=NOW - timedelta(days=1),
                last_surfaced_at=NOW,
                version=1,
                created_at=NOW - timedelta(days=1),
                updated_at=NOW,
            )
        )
    try:
        yield database
    finally:
        database.dispose()


def _posting_version(
    *,
    version_id: str,
    version_number: int,
    title: str,
    observed_at: datetime,
) -> JobPostingVersion:
    return JobPostingVersion(
        id=version_id,
        owner_id="owner-a",
        job_posting_id="posting-a",
        version_number=version_number,
        content_hash=str(version_number + 1) * 64,
        source="greenhouse",
        source_job_id="123",
        company_name="Acme",
        title=title,
        canonical_url="https://boards.greenhouse.io/acme/jobs/123",
        apply_urls=["https://boards.greenhouse.io/acme/jobs/123"],
        location="Remote India",
        summary="Build reliable backend systems.",
        description="Design and operate reliable backend systems.",
        employment_type="full_time",
        posted_at_text="2026-07-12",
        source_facts={},
        source_confidence=1.0,
        observed_at=observed_at,
        created_at=observed_at,
    )


def _count(session, model: type) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def test_pursuit_atomically_creates_and_projects_the_application_graph(
    application_repository_db: Database,
) -> None:
    with application_repository_db.session() as session:
        response = pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            PursueOpportunityRequest(),
            1,
            "pursue-once",
            NOW,
        )

        assert response.state.value == "pursued"
        assert response.opportunity_version == 2
        assert response.event.previous_state.value == "inbox"
        assert response.pursuit is not None
        assert response.pursuit.application_created is True
        application = response.pursuit.application
        assert application.version == 1
        assert application.stage.value == "pursuing"
        assert application.pursued_posting_version_id == "posting-version-2"
        assert application.posting.title == "Staff Backend Engineer"
        assert application.posting.first_party is False
        assert application.current_action.status.value == "open"
        assert application.current_action.due_on == date(2026, 7, 15)
        assert response.pursuit.activity.sequence_number == 1
        assert response.pursuit.activity.application_id == application.id
        assert (
            response.pursuit.activity.action_item_id
            == application.current_action.id
        )
        assert response.pursuit.activity.occurred_at == application.created_at

        assert _count(session, Application) == 1
        assert _count(session, ActionItem) == 1
        assert _count(session, ApplicationActivityEvent) == 1
        assert _count(session, OpportunityDecisionEvent) == 1
        assert _count(session, OwnerMutationReceipt) == 1

        opportunity = session.get(OwnerOpportunity, "opportunity-a")
        assert opportunity is not None
        assert opportunity.decision == "pursued"
        assert opportunity.version == 2
        assert opportunity.reviewed_posting_version_id == "posting-version-2"

        listed = list_applications(session, "owner-a")
        assert listed.data_source == "database"
        assert listed.total == 1
        assert [item.id for item in listed.items] == [application.id]
        assert listed.next_cursor is None
        with pytest.raises(ValueError, match="cursor"):
            list_applications(session, "owner-a", cursor="bm90LWpzb24")

        detail = load_application_detail(session, "owner-a", application.id)
        assert detail is not None
        assert detail.application == application
        assert [item.id for item in detail.activity] == [response.pursuit.activity.id]

        activity = list_application_activity(session, "owner-a", application.id)
        assert activity is not None
        assert [item.id for item in activity.items] == [response.pursuit.activity.id]

        pursuit_event = session.scalar(
            select(OpportunityDecisionEvent).where(
                OpportunityDecisionEvent.owner_id == "owner-a",
                OpportunityDecisionEvent.owner_opportunity_id == "opportunity-a",
            )
        )
        assert pursuit_event is not None
        projected_event = _decision_event_response(
            pursuit_event,
            keyring=DataKeyring(
                [("test-v1", Fernet.generate_key().decode("ascii"))]
            ),
        )
        assert projected_event.action.value == "pursue"

        assert list_applications(session, "owner-b").total == 0
        assert load_application_detail(session, "owner-b", application.id) is None
        assert list_application_activity(session, "owner-b", application.id) is None


def test_pursuit_replays_without_mutation_and_rejects_changed_same_key(
    application_repository_db: Database,
) -> None:
    original_request = PursueOpportunityRequest(initial_action_due_on=LOCAL_TODAY)
    changed_request = PursueOpportunityRequest(
        initial_action_due_on=LOCAL_TODAY + timedelta(days=1)
    )

    with application_repository_db.session() as session:
        first = pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            original_request,
            1,
            "stable-key",
            NOW,
        )
        same_key = pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            original_request,
            1,
            "stable-key",
            NOW + timedelta(hours=1),
        )
        different_key = pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            changed_request,
            1,
            "another-key",
            NOW + timedelta(hours=2),
        )

        assert first.pursuit is not None
        assert same_key.pursuit is not None
        assert different_key.pursuit is not None
        assert first.pursuit.application_created is True
        assert same_key.pursuit.application_created is True
        assert different_key.pursuit.application_created is False
        assert same_key.event.id == first.event.id == different_key.event.id
        assert (
            same_key.pursuit.application.id
            == first.pursuit.application.id
            == different_key.pursuit.application.id
        )
        assert first.opportunity_version == 2
        assert same_key.opportunity_version == 2
        assert different_key.opportunity_version == 2

        with pytest.raises(ResourceConflict, match="idempotency key"):
            pursue_owner_opportunity(
                session,
                "owner-a",
                "opportunity-a",
                changed_request,
                2,
                "stable-key",
                NOW + timedelta(hours=3),
            )
        with pytest.raises(ResourceConflict, match="idempotency key"):
            pursue_owner_opportunity(
                session,
                "owner-a",
                "opportunity-a",
                PursueOpportunityRequest(
                    initial_action_due_on=LOCAL_TODAY + timedelta(days=2)
                ),
                2,
                "another-key",
                NOW + timedelta(hours=4),
            )

        assert _count(session, Application) == 1
        assert _count(session, ActionItem) == 1
        assert _count(session, ApplicationActivityEvent) == 1
        assert _count(session, OpportunityDecisionEvent) == 1
        assert _count(session, OwnerMutationReceipt) == 2
        opportunity = session.get(OwnerOpportunity, "opportunity-a")
        assert opportunity is not None
        assert opportunity.version == 2


def test_application_list_batches_one_page_without_n_plus_one_queries(
    application_repository_db: Database,
) -> None:
    with application_repository_db.session() as session:
        pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            PursueOpportunityRequest(),
            1,
            "batch-page",
            NOW,
        )

    statements: list[str] = []

    def record_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    sqlalchemy_event.listen(
        application_repository_db.engine,
        "before_cursor_execute",
        record_statement,
    )
    try:
        with application_repository_db.session() as session:
            page = list_applications(session, "owner-a")
    finally:
        sqlalchemy_event.remove(
            application_repository_db.engine,
            "before_cursor_execute",
            record_statement,
        )

    assert len(page.items) == 1
    # Applications, actions, outcomes, scheduled rounds, postings/versions,
    # and first-party observations are each loaded once for the page.
    assert len(statements) == 6


def test_pursuit_enforces_owner_version_and_local_due_date_bounds(
    application_repository_db: Database,
) -> None:
    with application_repository_db.session() as session:
        with pytest.raises(OpportunityNotFound):
            pursue_owner_opportunity(
                session,
                "owner-b",
                "opportunity-a",
                PursueOpportunityRequest(),
                1,
                "wrong-owner",
                NOW,
            )

        with pytest.raises(VersionConflict):
            pursue_owner_opportunity(
                session,
                "owner-a",
                "opportunity-a",
                PursueOpportunityRequest(),
                2,
                "stale-version",
                NOW,
            )

        with pytest.raises(ValueError, match="local today through 365 days ahead"):
            pursue_owner_opportunity(
                session,
                "owner-a",
                "opportunity-a",
                PursueOpportunityRequest(
                    initial_action_due_on=LOCAL_TODAY - timedelta(days=1)
                ),
                1,
                "past-due",
                NOW,
            )

        with pytest.raises(ValueError, match="local today through 365 days ahead"):
            pursue_owner_opportunity(
                session,
                "owner-a",
                "opportunity-a",
                PursueOpportunityRequest(
                    initial_action_due_on=LOCAL_TODAY + timedelta(days=366)
                ),
                1,
                "too-far",
                NOW,
            )

        response = pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            PursueOpportunityRequest(initial_action_due_on=LOCAL_TODAY),
            1,
            "local-today-is-valid",
            NOW,
        )
        assert response.pursuit is not None
        assert response.pursuit.application.current_action.due_on == LOCAL_TODAY


def test_dismissed_opportunity_can_be_pursued(
    application_repository_db: Database,
) -> None:
    with application_repository_db.session() as session:
        opportunity = session.get(OwnerOpportunity, "opportunity-a")
        assert opportunity is not None
        opportunity.decision = "dismiss"
        opportunity.decision_reason_code = "not_now"

    with application_repository_db.session() as session:
        response = pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            PursueOpportunityRequest(),
            1,
            "reconsider-dismissed",
            NOW,
        )

        assert response.event.previous_state.value == "dismiss"
        assert response.state.value == "pursued"
        assert response.pursuit is not None
        opportunity = session.get(OwnerOpportunity, "opportunity-a")
        assert opportunity is not None
        assert opportunity.decision == "pursued"
        assert opportunity.decision_reason_code is None


def test_closed_posting_cannot_be_pursued(
    application_repository_db: Database,
) -> None:
    with application_repository_db.session() as session:
        posting = session.get(JobPosting, "posting-a")
        assert posting is not None
        posting.lifecycle_state = "closed"
        posting.closure_reason = "explicit"
        posting.closed_at = NOW

    with application_repository_db.session() as session:
        with pytest.raises(ResourceConflict, match="closed postings"):
            pursue_owner_opportunity(
                session,
                "owner-a",
                "opportunity-a",
                PursueOpportunityRequest(),
                1,
                "closed-posting",
                NOW,
            )

    with application_repository_db.session() as session:
        assert _count(session, Application) == 0
        assert _count(session, ActionItem) == 0
        assert _count(session, ApplicationActivityEvent) == 0
        assert _count(session, OpportunityDecisionEvent) == 0
        opportunity = session.get(OwnerOpportunity, "opportunity-a")
        assert opportunity is not None
        assert opportunity.decision == "inbox"
        assert opportunity.version == 1
