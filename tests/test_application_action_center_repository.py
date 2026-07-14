"""Repository and adapter tests for owner-local Today application actions."""

from __future__ import annotations

import hashlib
import socket
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import job_hunt_agent.application_repository as repository_module
import job_hunt_agent.sqlalchemy_application_workspace as workspace_module
from job_hunt_agent.application_repository import (
    ApplicationRepositoryError,
    list_today_application_actions,
)
from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    ActionItem,
    Application,
    Base,
    JobPosting,
    JobPostingVersion,
    Owner,
    OwnerOpportunity,
)
from job_hunt_agent.owner_workspace import WorkspaceUnavailable
from job_hunt_agent.security import load_data_keyring
from job_hunt_agent.sqlalchemy_application_workspace import (
    SqlAlchemyApplicationWorkspaceStore,
)


NOW = datetime(2026, 7, 15, 18, 45, tzinfo=timezone.utc)
KOLKATA_TODAY = date(2026, 7, 16)
UTC_TODAY = date(2026, 7, 15)


@pytest.fixture
def action_center_db(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'application-actions.db'}")
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
    try:
        yield database
    finally:
        database.dispose()


def _add_application(
    session,
    *,
    owner_id: str,
    suffix: str,
    due_on: date,
    stage: str = "pursuing",
    kind: str = "review_and_prepare_application",
    action_created_at: datetime = NOW,
    include_open_action: bool = True,
    pinned_title: str | None = None,
    latest_title: str | None = None,
) -> tuple[str, str]:
    posting_id = f"post-{suffix}"
    pinned_version_id = f"ver-{suffix}-1"
    opportunity_id = f"opp-{suffix}"
    application_id = f"app-{suffix}"
    action_id = f"act-{suffix}"
    url = f"https://careers.example.com/jobs/{suffix}"
    pinned = pinned_title or f"Pinned role {suffix}"
    session.add(
        JobPosting(
            id=posting_id,
            owner_id=owner_id,
            identity_kind="native",
            identity_key=f"source:example:{owner_id}:{suffix}",
            identity_key_hash=hashlib.sha256(
                f"{owner_id}:{suffix}".encode("utf-8")
            ).hexdigest(),
            source="greenhouse",
            company_slug=f"company-{suffix}",
            source_job_id=suffix,
            canonical_url=url,
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
    session.add(
        JobPostingVersion(
            id=pinned_version_id,
            owner_id=owner_id,
            job_posting_id=posting_id,
            version_number=1,
            content_hash=hashlib.sha256(
                f"{owner_id}:{suffix}:v1".encode("utf-8")
            ).hexdigest(),
            source="greenhouse",
            source_job_id=suffix,
            company_name=f"Company {suffix}",
            title=pinned,
            canonical_url=url,
            apply_urls=[url],
            location="Remote India",
            summary="Build reliable systems.",
            description="Build reliable systems for customers.",
            source_facts={},
            source_confidence=1.0,
            observed_at=NOW - timedelta(days=1),
            created_at=NOW - timedelta(days=1),
        )
    )
    if latest_title is not None:
        session.add(
            JobPostingVersion(
                id=f"ver-{suffix}-2",
                owner_id=owner_id,
                job_posting_id=posting_id,
                version_number=2,
                content_hash=hashlib.sha256(
                    f"{owner_id}:{suffix}:v2".encode("utf-8")
                ).hexdigest(),
                source="greenhouse",
                source_job_id=suffix,
                company_name=f"Company {suffix}",
                title=latest_title,
                canonical_url=url,
                apply_urls=[url],
                location="Remote India",
                summary="A later posting title.",
                description="A later posting title and description.",
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
            decision="pursued",
            reviewed_posting_version_id=pinned_version_id,
            decision_updated_at=NOW,
            first_surfaced_at=NOW - timedelta(days=1),
            last_surfaced_at=NOW,
            version=2,
            created_at=NOW - timedelta(days=1),
            updated_at=NOW,
        )
    )
    session.flush()
    session.add(
        Application(
            id=application_id,
            owner_id=owner_id,
            owner_opportunity_id=opportunity_id,
            job_posting_id=posting_id,
            pursued_posting_version_id=pinned_version_id,
            stage=stage,
            version=2,
            created_at=NOW - timedelta(days=1),
            updated_at=NOW,
        )
    )
    session.flush()
    if include_open_action:
        session.add(
            ActionItem(
                id=action_id,
                owner_id=owner_id,
                application_id=application_id,
                kind=kind,
                title=f"Next action {suffix}",
                status="open",
                due_on=due_on,
                version=1,
                created_at=action_created_at,
                updated_at=action_created_at,
            )
        )
        session.flush()
    return application_id, action_id


def test_owner_timezone_controls_the_same_due_date_at_a_utc_midnight_boundary(
    action_center_db: Database,
) -> None:
    with action_center_db.session() as session:
        _add_application(
            session,
            owner_id="owner-a",
            suffix="kolkata",
            due_on=UTC_TODAY,
        )
        _add_application(
            session,
            owner_id="owner-b",
            suffix="utc",
            due_on=UTC_TODAY,
        )

        kolkata = list_today_application_actions(
            session,
            owner_id="owner-a",
            now=NOW,
        )
        utc = list_today_application_actions(
            session,
            owner_id="owner-b",
            now=NOW,
        )

    assert (kolkata.owner_timezone, kolkata.owner_local_date) == (
        "Asia/Kolkata",
        KOLKATA_TODAY,
    )
    assert kolkata.overdue.total == 1
    assert kolkata.today.total == 0
    assert (utc.owner_timezone, utc.owner_local_date) == ("UTC", UTC_TODAY)
    assert utc.overdue.total == 0
    assert utc.today.total == 1
    assert kolkata.as_of == utc.as_of == NOW


def test_next_seven_days_is_inclusive_and_day_eight_is_not_projected(
    action_center_db: Database,
) -> None:
    with action_center_db.session() as session:
        _add_application(
            session,
            owner_id="owner-a",
            suffix="day7",
            due_on=KOLKATA_TODAY + timedelta(days=7),
        )
        _add_application(
            session,
            owner_id="owner-a",
            suffix="day8",
            due_on=KOLKATA_TODAY + timedelta(days=8),
        )
        response = list_today_application_actions(
            session,
            owner_id="owner-a",
            now=NOW,
        )

    assert response.window_ends_on == KOLKATA_TODAY + timedelta(days=7)
    assert response.next_7_days.total == 1
    assert [item.application.id for item in response.next_7_days.items] == [
        "app-day7"
    ]


def test_every_active_stage_is_included_but_historical_action_statuses_are_not(
    action_center_db: Database,
) -> None:
    stage_kinds = [
        ("pursuing", "review_and_prepare_application"),
        ("ready_to_apply", "submit_application"),
        ("applied", "follow_up_application"),
        ("screening", "prepare_recruiter_screen"),
        ("interviewing", "prepare_interview"),
        ("offer", "review_offer"),
    ]
    with action_center_db.session() as session:
        first_application_id: str | None = None
        for index, (stage, kind) in enumerate(stage_kinds):
            application_id, _action_id = _add_application(
                session,
                owner_id="owner-a",
                suffix=f"stage{index}",
                due_on=KOLKATA_TODAY,
                stage=stage,
                kind=kind,
            )
            if first_application_id is None:
                first_application_id = application_id
        assert first_application_id is not None
        session.add_all(
            [
                ActionItem(
                    id="act-history-completed",
                    owner_id="owner-a",
                    application_id=first_application_id,
                    kind="review_and_prepare_application",
                    title="Completed history",
                    status="completed",
                    due_on=KOLKATA_TODAY - timedelta(days=2),
                    completed_at=NOW - timedelta(hours=2),
                    version=2,
                    created_at=NOW - timedelta(days=2),
                    updated_at=NOW - timedelta(hours=2),
                ),
                ActionItem(
                    id="act-history-cancelled",
                    owner_id="owner-a",
                    application_id=first_application_id,
                    kind="review_and_prepare_application",
                    title="Cancelled history",
                    status="cancelled",
                    due_on=KOLKATA_TODAY - timedelta(days=1),
                    cancelled_at=NOW - timedelta(hours=1),
                    version=2,
                    created_at=NOW - timedelta(days=1),
                    updated_at=NOW - timedelta(hours=1),
                ),
            ]
        )
        response = list_today_application_actions(
            session,
            owner_id="owner-a",
            limit=20,
            now=NOW,
        )

    assert response.today.total == 6
    assert {item.application.stage.value for item in response.today.items} == {
        stage for stage, _kind in stage_kinds
    }
    assert {item.action.kind.value for item in response.today.items} == {
        kind for _stage, kind in stage_kinds
    }
    assert {item.action.status.value for item in response.today.items} == {"open"}
    assert response.overdue.total == 0


def test_limit_applies_per_bucket_without_starving_later_buckets_and_keeps_totals(
    action_center_db: Database,
) -> None:
    with action_center_db.session() as session:
        for suffix, due_on in [
            ("over1", KOLKATA_TODAY - timedelta(days=2)),
            ("over2", KOLKATA_TODAY - timedelta(days=1)),
            ("today1", KOLKATA_TODAY),
            ("today2", KOLKATA_TODAY),
            ("next1", KOLKATA_TODAY + timedelta(days=1)),
            ("next2", KOLKATA_TODAY + timedelta(days=2)),
        ]:
            _add_application(
                session,
                owner_id="owner-a",
                suffix=suffix,
                due_on=due_on,
            )
        response = list_today_application_actions(
            session,
            owner_id="owner-a",
            limit=1,
            now=NOW,
        )

    assert [response.overdue.total, response.today.total, response.next_7_days.total] == [
        2,
        2,
        2,
    ]
    assert [len(response.overdue.items), len(response.today.items), len(response.next_7_days.items)] == [
        1,
        1,
        1,
    ]
    assert response.overdue.items[0].application.id == "app-over1"
    assert response.next_7_days.items[0].application.id == "app-next1"


def test_projection_is_stably_ordered_and_uses_the_pursued_pinned_version(
    action_center_db: Database,
) -> None:
    with action_center_db.session() as session:
        _add_application(
            session,
            owner_id="owner-a",
            suffix="late",
            due_on=KOLKATA_TODAY,
            action_created_at=NOW + timedelta(minutes=1),
        )
        _add_application(
            session,
            owner_id="owner-a",
            suffix="b",
            due_on=KOLKATA_TODAY,
            action_created_at=NOW,
        )
        _add_application(
            session,
            owner_id="owner-a",
            suffix="a",
            due_on=KOLKATA_TODAY,
            action_created_at=NOW,
            pinned_title="Pinned application title",
            latest_title="Later posting title",
        )
        response = list_today_application_actions(
            session,
            owner_id="owner-a",
            now=NOW,
        )

    assert [item.action.id for item in response.today.items] == [
        "act-a",
        "act-b",
        "act-late",
    ]
    pinned = response.today.items[0]
    assert pinned.application.pursued_posting_version_id == "ver-a-1"
    assert pinned.posting.title == "Pinned application title"


def test_foreign_owner_actions_never_cross_the_projection_boundary(
    action_center_db: Database,
) -> None:
    with action_center_db.session() as session:
        _add_application(
            session,
            owner_id="owner-a",
            suffix="owned",
            due_on=KOLKATA_TODAY,
        )
        _add_application(
            session,
            owner_id="owner-b",
            suffix="foreign",
            due_on=UTC_TODAY,
        )
        response = list_today_application_actions(
            session,
            owner_id="owner-a",
            now=NOW,
        )

    assert response.today.total == 1
    assert response.today.items[0].application.id == "app-owned"
    assert "foreign" not in response.model_dump_json()


@pytest.mark.parametrize("corruption", ["missing-action", "wrong-kind"])
def test_malformed_active_application_graph_fails_closed(
    action_center_db: Database,
    corruption: str,
) -> None:
    with action_center_db.session() as session:
        _add_application(
            session,
            owner_id="owner-a",
            suffix="corrupt",
            due_on=KOLKATA_TODAY,
            stage="offer" if corruption == "wrong-kind" else "pursuing",
            kind=(
                "prepare_interview"
                if corruption == "wrong-kind"
                else "review_and_prepare_application"
            ),
            include_open_action=corruption != "missing-action",
        )

        with pytest.raises(ApplicationRepositoryError):
            list_today_application_actions(
                session,
                owner_id="owner-a",
                now=NOW,
            )


def test_workspace_adapter_is_provider_free_owner_scoped_and_sanitizes_corruption(
    action_center_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with action_center_db.session() as session:
        _add_application(
            session,
            owner_id="owner-a",
            suffix="adapter",
            due_on=KOLKATA_TODAY,
        )

    def fail_network(*_args, **_kwargs):
        raise AssertionError("Today application actions must be database-only")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(repository_module, "utcnow", lambda: NOW)
    store = SqlAlchemyApplicationWorkspaceStore(
        action_center_db,
        load_data_keyring(production=False),
    )
    owned = store.list_today_application_actions(owner_id="owner-a", limit=20)
    foreign = store.list_today_application_actions(owner_id="owner-b", limit=20)
    assert owned.data_source == "database"
    assert owned.today.total + owned.overdue.total + owned.next_7_days.total == 1
    assert foreign.today.total + foreign.overdue.total + foreign.next_7_days.total == 0

    def corrupt(*_args, **_kwargs):
        raise ApplicationRepositoryError("PRIVATE_CORRUPTED_ACTION_GRAPH")

    monkeypatch.setattr(workspace_module, "list_today_application_actions", corrupt)
    with pytest.raises(WorkspaceUnavailable) as caught:
        store.list_today_application_actions(owner_id="owner-a", limit=20)
    assert str(caught.value) == "application data is inconsistent"
    assert "PRIVATE_CORRUPTED_ACTION_GRAPH" not in str(caught.value)
