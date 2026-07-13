"""Database-only integration tests for the application workspace adapter."""

from __future__ import annotations

import socket
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import job_hunt_agent.sqlalchemy_application_workspace as workspace_module
from job_hunt_agent.application_repository import ApplicationRepositoryError
from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    ActionItem,
    Application,
    ApplicationActivityEvent,
    Base,
    JobPosting,
    JobPostingVersion,
    Owner,
    OwnerOpportunity,
)
from job_hunt_agent.owner_workspace import WorkspaceUnavailable
from job_hunt_agent.sqlalchemy_application_workspace import (
    SqlAlchemyApplicationWorkspaceStore,
)


NOW = datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def application_workspace(
    tmp_path: Path,
) -> tuple[Database, SqlAlchemyApplicationWorkspaceStore]:
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
        session.flush()
        session.add(
            ActionItem(
                id="action1",
                owner_id="owner-a",
                application_id="application1",
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
                id="activity1",
                owner_id="owner-a",
                application_id="application1",
                sequence_number=1,
                event_type="application_created",
                from_stage=None,
                to_stage="pursuing",
                action_item_id="action1",
                occurred_at=NOW,
                created_at=NOW,
            )
        )
    try:
        yield database, SqlAlchemyApplicationWorkspaceStore(database)
    finally:
        database.dispose()


def test_application_reads_are_owner_scoped_and_never_open_a_network_connection(
    application_workspace: tuple[Database, SqlAlchemyApplicationWorkspaceStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database, store = application_workspace

    def fail_network(*_args, **_kwargs):
        raise AssertionError("application reads must not invoke a live provider")

    monkeypatch.setattr(socket, "create_connection", fail_network)

    listed = store.list_applications(owner_id="owner-a", limit=1)
    detail = store.get_application(
        owner_id="owner-a",
        application_id="application1",
    )
    activity = store.list_activity(
        owner_id="owner-a",
        application_id="application1",
    )
    contacts = store.get_application_contacts(
        owner_id="owner-a",
        application_id="application1",
    )

    assert listed.data_source == "database"
    assert listed.total == 1
    assert [application.id for application in listed.items] == ["application1"]
    assert listed.next_cursor is None
    assert detail is not None and detail.application.id == "application1"
    assert activity is not None
    assert [event.id for event in activity.items] == ["activity1"]
    assert contacts is not None
    assert contacts.status.value == "not_started"
    assert contacts.verified_count == 0

    assert store.list_applications(owner_id="owner-b").total == 0
    assert store.get_application(
        owner_id="owner-b",
        application_id="application1",
    ) is None
    assert store.list_activity(
        owner_id="owner-b",
        application_id="application1",
    ) is None
    assert store.get_application_contacts(
        owner_id="owner-b",
        application_id="application1",
    ) is None


def test_application_repository_invariant_failures_map_to_safe_unavailability(
    application_workspace: tuple[Database, SqlAlchemyApplicationWorkspaceStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database, store = application_workspace

    def fail_projection(*_args, **_kwargs):
        raise ApplicationRepositoryError("PRIVATE_CORRUPTED_APPLICATION_PAYLOAD")

    monkeypatch.setattr(workspace_module, "list_applications", fail_projection)
    with pytest.raises(WorkspaceUnavailable) as caught:
        store.list_applications(owner_id="owner-a")

    assert str(caught.value) == "application data is inconsistent"
    assert "PRIVATE_CORRUPTED_APPLICATION_PAYLOAD" not in str(caught.value)
