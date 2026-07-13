"""Focused persistence invariants for the atomic pursuit boundary."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    ActionItem,
    Application,
    ApplicationActivityEvent,
    Base,
    JobPosting,
    JobPostingVersion,
    OpportunityDecisionEvent,
    Owner,
    OwnerOpportunity,
)


NOW = datetime(2026, 7, 13, 8, 30, tzinfo=timezone.utc)
DUE_ON = date(2026, 7, 14)


@pytest.fixture
def application_db(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'applications.db'}")
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        session.add_all(
            [
                Owner(id="owner-a", display_name="Owner A", timezone="Asia/Kolkata"),
                Owner(id="owner-b", display_name="Owner B", timezone="UTC"),
            ]
        )
        session.flush()
        session.add(_posting())
        session.flush()
        session.add(_posting_version())
        session.add(
            OwnerOpportunity(
                id="opportunity-a",
                owner_id="owner-a",
                job_posting_id="posting-a",
                decision="inbox",
                first_surfaced_at=NOW,
                last_surfaced_at=NOW,
                version=1,
            )
        )
    try:
        yield database
    finally:
        database.dispose()


def _posting() -> JobPosting:
    return JobPosting(
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
    )


def _posting_version() -> JobPostingVersion:
    return JobPostingVersion(
        id="posting-version-a",
        owner_id="owner-a",
        job_posting_id="posting-a",
        version_number=1,
        content_hash="2" * 64,
        source="greenhouse",
        source_job_id="123",
        company_name="Acme",
        title="Senior Backend Engineer",
        canonical_url="https://boards.greenhouse.io/acme/jobs/123",
        apply_urls=["https://boards.greenhouse.io/acme/jobs/123"],
        location="Remote India",
        summary="Build reliable backend systems.",
        description="Design and operate reliable backend systems.",
        employment_type="full_time",
        source_facts={},
        source_confidence=1.0,
        observed_at=NOW,
    )


def _application(*, owner_id: str = "owner-a", application_id: str = "application-a") -> Application:
    return Application(
        id=application_id,
        owner_id=owner_id,
        owner_opportunity_id="opportunity-a",
        job_posting_id="posting-a",
        pursued_posting_version_id="posting-version-a",
        stage="pursuing",
        version=1,
    )


def _action(
    *,
    action_id: str = "action-a",
    owner_id: str = "owner-a",
    application_id: str = "application-a",
    status: str = "open",
) -> ActionItem:
    terminal_at = NOW if status != "open" else None
    return ActionItem(
        id=action_id,
        owner_id=owner_id,
        application_id=application_id,
        kind="review_and_prepare_application",
        title="Review the role and prepare the application",
        status=status,
        due_on=DUE_ON,
        completed_at=terminal_at if status == "completed" else None,
        cancelled_at=terminal_at if status == "cancelled" else None,
        version=1,
    )


def _event(
    *, event_id: str = "activity-a", action_id: str = "action-a"
) -> ApplicationActivityEvent:
    return ApplicationActivityEvent(
        id=event_id,
        owner_id="owner-a",
        application_id="application-a",
        sequence_number=1,
        event_type="application_created",
        from_stage=None,
        to_stage="pursuing",
        action_item_id=action_id,
        occurred_at=NOW,
    )


def _add_pursuit_graph(session) -> None:
    session.add(_application())
    session.flush()
    session.add(_action())
    session.flush()
    session.add(_event())


def test_application_action_and_activity_form_one_owner_scoped_graph(
    application_db: Database,
) -> None:
    with application_db.session() as session:
        _add_pursuit_graph(session)

    with application_db.session() as session:
        assert session.scalar(select(func.count(Application.id))) == 1
        assert session.scalar(select(func.count(ActionItem.id))) == 1
        assert session.scalar(select(func.count(ApplicationActivityEvent.id))) == 1
        action = session.get(ActionItem, "action-a")
        assert action is not None
        assert action.due_on == DUE_ON
        assert action.title == "Review the role and prepare the application"
        event = session.get(ApplicationActivityEvent, "activity-a")
        assert event is not None and event.action_item_id == action.id
        assert "updated_at" not in ApplicationActivityEvent.__table__.columns
        assert "version" not in ApplicationActivityEvent.__table__.columns


def test_application_delete_cascades_graph_but_direct_action_delete_is_restricted(
    application_db: Database,
) -> None:
    action_edge = next(
        constraint
        for constraint in ApplicationActivityEvent.__table__.foreign_key_constraints
        if constraint.name == "fk_application_activity_events_owner_action"
    )
    assert action_edge.deferrable is True
    assert action_edge.initially == "DEFERRED"
    version_edge = next(
        constraint
        for constraint in Application.__table__.foreign_key_constraints
        if constraint.name == "fk_applications_owner_posting_version"
    )
    assert version_edge.deferrable is True
    assert version_edge.initially == "DEFERRED"

    with application_db.session() as session:
        _add_pursuit_graph(session)

    with pytest.raises(IntegrityError):
        with application_db.session() as session:
            action = session.get(ActionItem, "action-a")
            assert action is not None
            session.delete(action)

    with pytest.raises(IntegrityError):
        with application_db.session() as session:
            version = session.get(JobPostingVersion, "posting-version-a")
            assert version is not None
            session.delete(version)

    with application_db.session() as session:
        application = session.get(Application, "application-a")
        assert application is not None
        session.delete(application)

    with application_db.session() as session:
        assert session.scalar(select(func.count(Application.id))) == 0
        assert session.scalar(select(func.count(ActionItem.id))) == 0
        assert session.scalar(select(func.count(ApplicationActivityEvent.id))) == 0


def test_one_application_one_open_action_and_one_creation_event_are_enforced(
    application_db: Database,
) -> None:
    with application_db.session() as session:
        _add_pursuit_graph(session)

    with pytest.raises(IntegrityError):
        with application_db.session() as session:
            session.add(_application(application_id="application-duplicate"))

    with pytest.raises(IntegrityError):
        with application_db.session() as session:
            session.add(_action(action_id="action-second-open"))

    with pytest.raises(IntegrityError):
        with application_db.session() as session:
            session.add(_event(event_id="activity-second"))

    with application_db.session() as session:
        session.add(
            _action(action_id="action-completed", status="completed")
        )


def test_cross_owner_edges_and_invalid_action_shapes_are_rejected(
    application_db: Database,
) -> None:
    with pytest.raises(IntegrityError):
        with application_db.session() as session:
            session.add(_application(owner_id="owner-b"))

    with application_db.session() as session:
        session.add(_application())

    with pytest.raises(IntegrityError):
        with application_db.session() as session:
            session.add(_action(owner_id="owner-b"))

    for invalid_title in ("   ", "x" * 241):
        with pytest.raises(IntegrityError):
            with application_db.session() as session:
                action = _action(action_id=f"bad-title-{len(invalid_title)}")
                action.title = invalid_title
                session.add(action)

    with pytest.raises(IntegrityError):
        with application_db.session() as session:
            action = _action(action_id="bad-completed")
            action.completed_at = NOW
            session.add(action)


def test_creation_event_must_be_first_and_link_its_own_application_action(
    application_db: Database,
) -> None:
    with application_db.session() as session:
        session.add(_application())
        session.flush()
        session.add(_action())

    with pytest.raises(IntegrityError):
        with application_db.session() as session:
            event = _event()
            event.sequence_number = 2
            session.add(event)

    with pytest.raises(IntegrityError):
        with application_db.session() as session:
            event = _event(event_id="bad-stage")
            event.to_stage = "applied"
            session.add(event)

    with pytest.raises(IntegrityError):
        with application_db.session() as session:
            session.add(_event(event_id="missing-action", action_id="missing"))


def test_pursued_opportunity_and_decision_event_accept_no_reason(
    application_db: Database,
) -> None:
    with application_db.session() as session:
        opportunity = session.get(OwnerOpportunity, "opportunity-a")
        assert opportunity is not None
        opportunity.decision = "pursued"
        opportunity.version += 1
        session.add(
            OpportunityDecisionEvent(
                id="decision-pursue",
                owner_id="owner-a",
                owner_opportunity_id="opportunity-a",
                job_posting_id="posting-a",
                posting_version_id="posting-version-a",
                previous_decision="inbox",
                new_decision="pursued",
                reason_code=None,
                idempotency_key_hash="3" * 64,
                request_hash="4" * 64,
                occurred_at=NOW,
            )
        )

    with pytest.raises(IntegrityError):
        with application_db.session() as session:
            session.add(
                OpportunityDecisionEvent(
                    owner_id="owner-a",
                    owner_opportunity_id="opportunity-a",
                    job_posting_id="posting-a",
                    posting_version_id="posting-version-a",
                    previous_decision="watch",
                    new_decision="pursued",
                    reason_code="not_allowed",
                    idempotency_key_hash="5" * 64,
                    request_hash="6" * 64,
                    occurred_at=NOW,
                )
            )
