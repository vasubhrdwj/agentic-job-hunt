"""Repository-level policy tests for manual staged outreach."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    Application,
    ApplicationContact,
    Base,
    Contact,
    ContactPlan,
    JobPosting,
    JobPostingVersion,
    OutreachEvent,
    OutreachMessageVersion,
    Owner,
    OwnerOpportunity,
)
from job_hunt_agent.outreach_repository import (
    add_business_days,
    load_application_outreach,
    record_outreach_event,
    save_outreach_message,
    start_outreach_sequence,
)
from job_hunt_agent.outreach_schemas import (
    ApplicationOutreachResponse,
    OutreachCopiedEventCreate,
    OutreachMarkedSentEventCreate,
    OutreachMessageCreate,
    OutreachOutcomeEventCreate,
    OutreachPauseEventCreate,
    OutreachResumeEventCreate,
    OutreachStopEventCreate,
)
from job_hunt_agent.repository_errors import ResourceConflict
from job_hunt_agent.security import DataKeyring


# Monday morning in India makes the five-business-day deadline unambiguous:
# the following Monday at the same wall-clock time.
NOW = datetime(2026, 7, 13, 4, 30, tzinfo=timezone.utc)
APPLICATION_ID = "application-a"
PLAN_ID = "contact-plan-a"
OWNER_ID = "owner-a"


@pytest.fixture
def keyring() -> DataKeyring:
    return DataKeyring([("test-v1", Fernet.generate_key().decode("ascii"))])


@pytest.fixture
def outreach_db(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'outreach-repository.db'}")
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        session.add_all(
            [
                Owner(id=OWNER_ID, display_name="Owner A", timezone="Asia/Kolkata"),
                Owner(id="owner-b", display_name="Owner B", timezone="UTC"),
            ]
        )
        session.flush()
        _seed_application_graph(
            session,
            suffix="a",
            application_id=APPLICATION_ID,
            opportunity_id="opportunity-a",
            posting_id="posting-a",
            posting_version_id="posting-version-a",
            source_job_id="123",
        )
        session.flush()
        session.add(
            ContactPlan(
                id=PLAN_ID,
                owner_id=OWNER_ID,
                application_id=APPLICATION_ID,
                plan_number=1,
                status="completed",
                target_count=5,
                candidate_limit=12,
                confidence_floor=0.75,
                policy_version="contact-policy-v1",
                scoring_version="contact-score-v1",
                discovered_count=5,
                verified_count=5,
                selected_count=5,
                coverage_status="met",
                exhausted=True,
                retryable=False,
                shortfall_reasons=[],
                version=1,
                started_at=NOW - timedelta(minutes=5),
                finalized_at=NOW - timedelta(minutes=1),
                created_at=NOW - timedelta(minutes=5),
                updated_at=NOW - timedelta(minutes=1),
            )
        )
        session.flush()
        categories = [
            "team_peer",
            "team_leader",
            "adjacent_peer",
            "recruiter",
            "team_peer",
        ]
        for rank, category in enumerate(categories, start=1):
            contact_id = f"contact-{rank}"
            profile_url = f"https://www.linkedin.com/in/person-{rank}"
            session.add(
                Contact(
                    id=contact_id,
                    owner_id=OWNER_ID,
                    identity_key=f"linkedin:person-{rank}",
                    identity_key_hash=f"{rank:x}" * 64,
                    profile_url=profile_url,
                    normalized_profile_url=profile_url,
                    profile_source="linkedin",
                    public_name=f"Person {rank}",
                    lifecycle="active",
                    version=1,
                    created_at=NOW - timedelta(minutes=4),
                    updated_at=NOW - timedelta(minutes=4),
                )
            )
            session.flush()
            session.add(
                _application_contact(
                    application_id=APPLICATION_ID,
                    plan_id=PLAN_ID,
                    row_id=f"application-contact-{rank}",
                    contact_id=contact_id,
                    rank=rank,
                    category=category,
                )
            )
    try:
        yield database
    finally:
        database.dispose()


def _seed_application_graph(
    session,
    *,
    suffix: str,
    application_id: str,
    opportunity_id: str,
    posting_id: str,
    posting_version_id: str,
    source_job_id: str,
) -> None:
    canonical_url = f"https://boards.greenhouse.io/acme/jobs/{source_job_id}"
    session.add(
        JobPosting(
            id=posting_id,
            owner_id=OWNER_ID,
            identity_kind="native",
            identity_key=f"source:greenhouse:acme:{source_job_id}",
            identity_key_hash=suffix * 64,
            source="greenhouse",
            company_slug="acme",
            source_job_id=source_job_id,
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
            owner_id=OWNER_ID,
            job_posting_id=posting_id,
            version_number=1,
            content_hash=("f" if suffix == "a" else "e") * 64,
            source="greenhouse",
            source_job_id=source_job_id,
            company_name="Acme",
            title="Senior Backend Engineer",
            canonical_url=canonical_url,
            apply_urls=[canonical_url],
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
            id=opportunity_id,
            owner_id=OWNER_ID,
            job_posting_id=posting_id,
            decision="pursued",
            reviewed_posting_version_id=posting_version_id,
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
            id=application_id,
            owner_id=OWNER_ID,
            owner_opportunity_id=opportunity_id,
            job_posting_id=posting_id,
            pursued_posting_version_id=posting_version_id,
            stage="pursuing",
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def _application_contact(
    *,
    application_id: str,
    plan_id: str,
    row_id: str,
    contact_id: str,
    rank: int,
    category: str,
) -> ApplicationContact:
    profile_url = f"https://www.linkedin.com/in/{contact_id}"
    return ApplicationContact(
        id=row_id,
        owner_id=OWNER_ID,
        application_id=application_id,
        contact_plan_id=plan_id,
        contact_id=contact_id,
        discovery_provider="public_web",
        discovery_query="Acme backend engineering team",
        result_position=rank,
        discovered_at=NOW - timedelta(minutes=4),
        current_title=("Technical Recruiter" if category == "recruiter" else "Staff Engineer"),
        current_company="Acme",
        category=category,
        verification_status="verified",
        confidence=0.92,
        verified_at=NOW - timedelta(minutes=3),
        employer_evidence_excerpt="Public profile lists a current role at Acme.",
        employer_evidence_url=profile_url,
        employer_evidence_source="linkedin",
        employer_evidence_observed_at=NOW - timedelta(minutes=3),
        why_relevant="Works with the team or recruiting function for this role.",
        relationship_status="unknown",
        team_proximity_status="inferred",
        score_total=900 - rank,
        score_components={"role_fit": 500, "evidence": 300},
        scoring_version="contact-score-v1",
        pool_rank=rank,
        bench_rank=rank,
        wave=rank,
        bench_state="reserve",
        unlocked_at=None,
        version=1,
        created_at=NOW - timedelta(minutes=2),
        updated_at=NOW - timedelta(minutes=2),
    )


def _sequence(response: ApplicationOutreachResponse):
    assert response.sequence is not None
    return response.sequence


def _recipient(response: ApplicationOutreachResponse, application_contact_id: str):
    return next(
        item
        for item in response.recipients
        if item.application_contact_id == application_contact_id
    )


def _start(
    database: Database,
    keyring: DataKeyring,
    *,
    key: str = "start-sequence",
    now: datetime = NOW,
    application_id: str = APPLICATION_ID,
) -> ApplicationOutreachResponse:
    with database.session() as session:
        result = start_outreach_sequence(
            session,
            owner_id=OWNER_ID,
            application_id=application_id,
            expected_application_version=1,
            idempotency_key=key,
            keyring=keyring,
            now=now,
        )
    assert result is not None
    return result


def _save(
    database: Database,
    keyring: DataKeyring,
    response: ApplicationOutreachResponse,
    *,
    recipient_id: str,
    kind: str,
    body: str,
    key: str,
    now: datetime,
) -> ApplicationOutreachResponse:
    sequence = _sequence(response)
    with database.session() as session:
        result = save_outreach_message(
            session,
            owner_id=OWNER_ID,
            application_id=APPLICATION_ID,
            sequence_id=sequence.id,
            payload=OutreachMessageCreate(
                application_contact_id=recipient_id,
                kind=kind,
                body=body,
            ),
            expected_sequence_version=sequence.version,
            idempotency_key=key,
            keyring=keyring,
            now=now,
        )
    assert result is not None
    return result


def _record(
    database: Database,
    keyring: DataKeyring,
    response: ApplicationOutreachResponse,
    *,
    payload,
    key: str,
    now: datetime,
) -> ApplicationOutreachResponse:
    sequence = _sequence(response)
    with database.session() as session:
        result = record_outreach_event(
            session,
            owner_id=OWNER_ID,
            application_id=APPLICATION_ID,
            sequence_id=sequence.id,
            payload=payload,
            expected_sequence_version=sequence.version,
            idempotency_key=key,
            keyring=keyring,
            now=now,
        )
    assert result is not None
    return result


def _save_copy_send_initial(
    database: Database,
    keyring: DataKeyring,
    response: ApplicationOutreachResponse,
    *,
    recipient_id: str = "application-contact-1",
    prefix: str = "initial",
    sent_at: datetime = NOW + timedelta(minutes=3),
) -> ApplicationOutreachResponse:
    response = _save(
        database,
        keyring,
        response,
        recipient_id=recipient_id,
        kind="initial",
        body=f"Exact {prefix} message",
        key=f"{prefix}-save",
        now=sent_at - timedelta(minutes=2),
    )
    version = _recipient(response, recipient_id).initial_message
    assert version is not None
    response = _record(
        database,
        keyring,
        response,
        payload=OutreachCopiedEventCreate(
            event_type="copied",
            message_version_id=version.id,
        ),
        key=f"{prefix}-copy",
        now=sent_at - timedelta(minutes=1),
    )
    return _record(
        database,
        keyring,
        response,
        payload=OutreachMarkedSentEventCreate(
            event_type="marked_sent",
            message_version_id=version.id,
            channel="linkedin",
            confirm_exact_version=True,
        ),
        key=f"{prefix}-sent",
        now=sent_at,
    )


def test_not_started_is_database_only_and_owner_isolated(
    outreach_db: Database,
    keyring: DataKeyring,
) -> None:
    with outreach_db.session() as session:
        own = load_application_outreach(
            session,
            owner_id=OWNER_ID,
            application_id=APPLICATION_ID,
            keyring=keyring,
        )
        foreign = load_application_outreach(
            session,
            owner_id="owner-b",
            application_id=APPLICATION_ID,
            keyring=keyring,
        )
        missing = load_application_outreach(
            session,
            owner_id=OWNER_ID,
            application_id="missing-application",
            keyring=keyring,
        )

    assert own is not None and own.status.value == "not_started"
    assert own.data_source == "database"
    assert foreign is None
    assert missing is None


def test_start_unlocks_strongest_non_recruiter_and_top_recruiter_then_replays(
    outreach_db: Database,
    keyring: DataKeyring,
) -> None:
    first = _start(outreach_db, keyring)

    assert _sequence(first).active_wave == 1
    assert [
        (item.application_contact_id, item.wave, item.bench_state.value)
        for item in first.recipients
    ] == [
        ("application-contact-1", 1, "ready"),
        ("application-contact-4", 1, "ready"),
        ("application-contact-2", 2, "reserve"),
        ("application-contact-3", 3, "reserve"),
        ("application-contact-5", 4, "reserve"),
    ]

    replay = _start(outreach_db, keyring, now=NOW + timedelta(minutes=1))
    assert _sequence(replay).id == _sequence(first).id
    assert _sequence(replay).version == 1
    with outreach_db.session() as session:
        assert session.scalar(select(func.count(OutreachEvent.id))) == 1


def test_exact_v1_v2_are_encrypted_and_latest_body_survives_fresh_reload(
    outreach_db: Database,
    keyring: DataKeyring,
) -> None:
    response = _start(outreach_db, keyring)
    body_v1 = "  PRIVATE exact v1\nwith deliberate spacing  "
    body_v2 = "PRIVATE exact v2 — revised"
    response = _save(
        outreach_db,
        keyring,
        response,
        recipient_id="application-contact-1",
        kind="initial",
        body=body_v1,
        key="save-v1",
        now=NOW + timedelta(minutes=1),
    )
    assert _recipient(response, "application-contact-1").initial_message.body == body_v1
    response = _save(
        outreach_db,
        keyring,
        response,
        recipient_id="application-contact-1",
        kind="initial",
        body=body_v2,
        key="save-v2",
        now=NOW + timedelta(minutes=2),
    )
    latest = _recipient(response, "application-contact-1").initial_message
    assert latest is not None
    assert (latest.version_number, latest.body) == (2, body_v2)

    with outreach_db.session() as session:
        rows = list(
            session.scalars(
                select(OutreachMessageVersion).order_by(
                    OutreachMessageVersion.version_number
                )
            )
        )
        fresh = load_application_outreach(
            session,
            owner_id=OWNER_ID,
            application_id=APPLICATION_ID,
            keyring=keyring,
        )
    assert [row.version_number for row in rows] == [1, 2]
    assert all(body_v1 not in row.encrypted_body for row in rows)
    assert all(body_v2 not in row.encrypted_body for row in rows)
    assert fresh is not None
    assert _recipient(fresh, "application-contact-1").initial_message.body == body_v2


def test_reserve_cannot_be_drafted_and_copy_is_not_a_send(
    outreach_db: Database,
    keyring: DataKeyring,
) -> None:
    response = _start(outreach_db, keyring)
    with pytest.raises(ResourceConflict):
        _save(
            outreach_db,
            keyring,
            response,
            recipient_id="application-contact-2",
            kind="initial",
            body="This reserve must stay locked.",
            key="reserve-save",
            now=NOW + timedelta(minutes=1),
        )

    response = _save(
        outreach_db,
        keyring,
        response,
        recipient_id="application-contact-1",
        kind="initial",
        body="Copy does not imply send.",
        key="ready-save",
        now=NOW + timedelta(minutes=1),
    )
    message = _recipient(response, "application-contact-1").initial_message
    assert message is not None
    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachCopiedEventCreate(
            event_type="copied",
            message_version_id=message.id,
        ),
        key="ready-copy",
        now=NOW + timedelta(minutes=2),
    )
    projected = _recipient(response, "application-contact-1").initial_message
    assert projected is not None and projected.copied_at is not None
    assert projected.sent_at is None
    with outreach_db.session() as session:
        assert session.scalar(
            select(func.count(OutreachEvent.id)).where(
                OutreachEvent.event_type == "marked_sent"
            )
        ) == 0


def test_send_requires_copied_exact_latest_version_and_persists_five_business_days(
    outreach_db: Database,
    keyring: DataKeyring,
) -> None:
    response = _start(outreach_db, keyring)
    response = _save(
        outreach_db,
        keyring,
        response,
        recipient_id="application-contact-1",
        kind="initial",
        body="Version one",
        key="exact-v1-save",
        now=NOW + timedelta(minutes=1),
    )
    v1 = _recipient(response, "application-contact-1").initial_message
    assert v1 is not None
    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachCopiedEventCreate(
            event_type="copied",
            message_version_id=v1.id,
        ),
        key="exact-v1-copy",
        now=NOW + timedelta(minutes=2),
    )
    response = _save(
        outreach_db,
        keyring,
        response,
        recipient_id="application-contact-1",
        kind="initial",
        body="Version two",
        key="exact-v2-save",
        now=NOW + timedelta(minutes=3),
    )
    v2 = _recipient(response, "application-contact-1").initial_message
    assert v2 is not None

    for message_id, key in ((v1.id, "stale-send"), (v2.id, "uncopied-send")):
        with pytest.raises(ResourceConflict):
            _record(
                outreach_db,
                keyring,
                response,
                payload=OutreachMarkedSentEventCreate(
                    event_type="marked_sent",
                    message_version_id=message_id,
                    channel="linkedin",
                    confirm_exact_version=True,
                ),
                key=key,
                now=NOW + timedelta(minutes=4),
            )

    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachCopiedEventCreate(
            event_type="copied",
            message_version_id=v2.id,
        ),
        key="exact-v2-copy",
        now=NOW + timedelta(minutes=4),
    )
    sent_at = NOW + timedelta(minutes=5)
    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachMarkedSentEventCreate(
            event_type="marked_sent",
            message_version_id=v2.id,
            channel="linkedin",
            confirm_exact_version=True,
        ),
        key="exact-v2-send",
        now=sent_at,
    )
    recipient = _recipient(response, "application-contact-1")
    assert recipient.initial_message is not None
    assert recipient.initial_message.sent_at == sent_at
    assert recipient.follow_up_due_at == datetime(
        2026, 7, 20, 4, 35, tzinfo=timezone.utc
    )
    with outreach_db.session() as session:
        sent = session.scalar(
            select(OutreachEvent).where(OutreachEvent.event_type == "marked_sent")
        )
    assert sent is not None
    assert _utc(sent.follow_up_due_at) == recipient.follow_up_due_at


def test_follow_up_cannot_send_early_and_no_reply_obeys_manual_cadence(
    outreach_db: Database,
    keyring: DataKeyring,
) -> None:
    sent_at = NOW + timedelta(minutes=3)
    response = _save_copy_send_initial(
        outreach_db,
        keyring,
        _start(outreach_db, keyring),
        sent_at=sent_at,
    )
    due = _recipient(response, "application-contact-1").follow_up_due_at
    assert due is not None
    response = _save(
        outreach_db,
        keyring,
        response,
        recipient_id="application-contact-1",
        kind="follow_up",
        body="One and only follow-up.",
        key="follow-save",
        now=sent_at + timedelta(days=1),
    )
    follow_up = _recipient(response, "application-contact-1").follow_up_message
    assert follow_up is not None
    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachCopiedEventCreate(
            event_type="copied",
            message_version_id=follow_up.id,
        ),
        key="follow-copy",
        now=sent_at + timedelta(days=1, minutes=1),
    )
    with pytest.raises(ResourceConflict):
        _record(
            outreach_db,
            keyring,
            response,
            payload=OutreachMarkedSentEventCreate(
                event_type="marked_sent",
                message_version_id=follow_up.id,
                channel="linkedin",
                confirm_exact_version=True,
            ),
            key="follow-early-send",
            now=due - timedelta(seconds=1),
        )
    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachMarkedSentEventCreate(
            event_type="marked_sent",
            message_version_id=follow_up.id,
            channel="linkedin",
            confirm_exact_version=True,
        ),
        key="follow-due-send",
        now=due,
    )
    no_reply_at = add_business_days(due, 5, timezone_name="Asia/Kolkata")
    with pytest.raises(ResourceConflict):
        _record(
            outreach_db,
            keyring,
            response,
            payload=OutreachOutcomeEventCreate(
                event_type="outcome",
                application_contact_id="application-contact-1",
                outcome="no_reply",
            ),
            key="no-reply-early",
            now=no_reply_at - timedelta(seconds=1),
        )
    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachOutcomeEventCreate(
            event_type="outcome",
            application_contact_id="application-contact-1",
            outcome="no_reply",
        ),
        key="no-reply-due",
        now=no_reply_at,
    )
    assert _recipient(response, "application-contact-1").outcome.value == "no_reply"


@pytest.mark.parametrize("terminal_outcome", ["introduced", "referred"])
def test_useful_reply_pauses_and_introduction_or_referral_stops(
    outreach_db: Database,
    keyring: DataKeyring,
    terminal_outcome: str,
) -> None:
    response = _save_copy_send_initial(
        outreach_db,
        keyring,
        _start(outreach_db, keyring),
    )
    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachOutcomeEventCreate(
            event_type="outcome",
            application_contact_id="application-contact-1",
            outcome="useful_reply",
        ),
        key="useful-reply",
        now=NOW + timedelta(hours=1),
    )
    assert response.status.value == "paused"
    assert _sequence(response).reason == "useful_reply"
    assert {
        item.bench_state.value for item in response.recipients if item.wave == 1
    } == {"paused"}

    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachOutcomeEventCreate(
            event_type="outcome",
            application_contact_id="application-contact-1",
            outcome=terminal_outcome,
        ),
        key=f"{terminal_outcome}-stop",
        now=NOW + timedelta(hours=2),
    )
    assert response.status.value == "stopped"
    assert _sequence(response).reason == terminal_outcome
    assert {item.bench_state.value for item in response.recipients} == {"stopped"}


def test_unreachable_and_declined_advance_only_after_both_wave_one_purposes_resolve(
    outreach_db: Database,
    keyring: DataKeyring,
) -> None:
    response = _start(outreach_db, keyring)
    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachOutcomeEventCreate(
            event_type="outcome",
            application_contact_id="application-contact-1",
            outcome="unreachable",
        ),
        key="primary-unreachable",
        now=NOW + timedelta(minutes=1),
    )
    assert _sequence(response).active_wave == 1
    assert _recipient(response, "application-contact-2").bench_state.value == "reserve"

    response = _save_copy_send_initial(
        outreach_db,
        keyring,
        response,
        recipient_id="application-contact-4",
        prefix="recruiter",
        sent_at=NOW + timedelta(minutes=5),
    )
    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachOutcomeEventCreate(
            event_type="outcome",
            application_contact_id="application-contact-4",
            outcome="declined",
        ),
        key="recruiter-declined",
        now=NOW + timedelta(minutes=6),
    )
    assert _sequence(response).active_wave == 2
    assert _recipient(response, "application-contact-2").bench_state.value == "ready"


def test_person_cooldown_blocks_same_contact_on_another_application(
    outreach_db: Database,
    keyring: DataKeyring,
) -> None:
    sent_at = NOW + timedelta(minutes=3)
    _save_copy_send_initial(
        outreach_db,
        keyring,
        _start(outreach_db, keyring),
        sent_at=sent_at,
    )
    with outreach_db.session() as session:
        _seed_application_graph(
            session,
            suffix="b",
            application_id="application-b",
            opportunity_id="opportunity-b",
            posting_id="posting-b",
            posting_version_id="posting-version-b",
            source_job_id="456",
        )
        session.flush()
        session.add(
            ContactPlan(
                id="contact-plan-b",
                owner_id=OWNER_ID,
                application_id="application-b",
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
                shortfall_reasons=[
                    {
                        "code": "verified_contacts_shortfall",
                        "count": 4,
                        "detail": "Only one verified person was available.",
                    }
                ],
                version=1,
                finalized_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            _application_contact(
                application_id="application-b",
                plan_id="contact-plan-b",
                row_id="application-contact-b1",
                contact_id="contact-1",
                rank=1,
                category="team_peer",
            )
        )

    with pytest.raises(ResourceConflict, match="cooldown"):
        _start(
            outreach_db,
            keyring,
            key="start-second-application",
            now=sent_at + timedelta(days=1),
            application_id="application-b",
        )


def test_timeline_projects_every_persisted_event_with_exact_discriminators(
    outreach_db: Database,
    keyring: DataKeyring,
) -> None:
    response = _save_copy_send_initial(
        outreach_db,
        keyring,
        _start(outreach_db, keyring),
    )
    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachOutcomeEventCreate(
            event_type="outcome",
            application_contact_id="application-contact-1",
            outcome="useful_reply",
        ),
        key="timeline-useful-reply",
        now=NOW + timedelta(hours=1),
    )
    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachOutcomeEventCreate(
            event_type="outcome",
            application_contact_id="application-contact-1",
            outcome="introduced",
        ),
        key="timeline-introduced",
        now=NOW + timedelta(hours=2),
    )

    assert [event.event_type for event in response.timeline] == [
        "sequence_started",
        "message_saved",
        "copied",
        "marked_sent",
        "outcome_recorded",
        "paused",
        "outcome_recorded",
        "stopped",
    ]


@pytest.mark.parametrize("action", ["copy", "send"])
def test_presaved_follow_up_cannot_be_used_after_useful_reply_and_resume(
    outreach_db: Database,
    keyring: DataKeyring,
    action: str,
) -> None:
    response = _save_copy_send_initial(
        outreach_db,
        keyring,
        _start(outreach_db, keyring),
    )
    due = _recipient(response, "application-contact-1").follow_up_due_at
    assert due is not None
    response = _save(
        outreach_db,
        keyring,
        response,
        recipient_id="application-contact-1",
        kind="follow_up",
        body="This draft must become unusable after a reply.",
        key=f"presaved-follow-up-{action}",
        now=NOW + timedelta(days=1),
    )
    follow_up = _recipient(response, "application-contact-1").follow_up_message
    assert follow_up is not None
    if action == "send":
        response = _record(
            outreach_db,
            keyring,
            response,
            payload=OutreachCopiedEventCreate(
                event_type="copied",
                message_version_id=follow_up.id,
            ),
            key="presaved-follow-up-copy-before-reply",
            now=NOW + timedelta(days=1, minutes=1),
        )
    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachOutcomeEventCreate(
            event_type="outcome",
            application_contact_id="application-contact-1",
            outcome="useful_reply",
        ),
        key=f"presaved-useful-reply-{action}",
        now=NOW + timedelta(days=2),
    )
    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachResumeEventCreate(
            event_type="resume",
            reason="The owner reviewed the reply and resumed the sequence.",
        ),
        key=f"presaved-resume-{action}",
        now=NOW + timedelta(days=2, minutes=1),
    )

    payload = (
        OutreachCopiedEventCreate(
            event_type="copied",
            message_version_id=follow_up.id,
        )
        if action == "copy"
        else OutreachMarkedSentEventCreate(
            event_type="marked_sent",
            message_version_id=follow_up.id,
            channel="linkedin",
            confirm_exact_version=True,
        )
    )
    with pytest.raises(ResourceConflict, match="after an outcome"):
        _record(
            outreach_db,
            keyring,
            response,
            payload=payload,
            key=f"presaved-blocked-{action}",
            now=max(due, NOW + timedelta(days=2, minutes=2)),
        )


def test_manual_pause_resume_stop_reasons_are_encrypted_and_reload_exactly(
    outreach_db: Database,
    keyring: DataKeyring,
) -> None:
    reasons = {
        "paused": "Pause while I verify this conversation",
        "resumed": "Resume after my manual review",
        "stopped": "Stop because I chose another path",
    }
    response = _start(outreach_db, keyring)
    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachPauseEventCreate(
            event_type="pause",
            reason=reasons["paused"],
        ),
        key="manual-reason-pause",
        now=NOW + timedelta(minutes=1),
    )
    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachResumeEventCreate(
            event_type="resume",
            reason=reasons["resumed"],
        ),
        key="manual-reason-resume",
        now=NOW + timedelta(minutes=2),
    )
    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachStopEventCreate(
            event_type="stop",
            reason=reasons["stopped"],
        ),
        key="manual-reason-stop",
        now=NOW + timedelta(minutes=3),
    )

    projected_reasons = {
        event.event_type: event.reason
        for event in response.timeline
        if event.event_type in {"paused", "resumed", "stopped"}
    }
    assert projected_reasons == reasons
    with outreach_db.session() as session:
        rows = list(
            session.scalars(
                select(OutreachEvent).where(
                    OutreachEvent.event_type.in_(("paused", "resumed", "stopped"))
                )
            )
        )
        fresh = load_application_outreach(
            session,
            owner_id=OWNER_ID,
            application_id=APPLICATION_ID,
            keyring=keyring,
        )
    assert all(row.encrypted_note and row.note_key_id for row in rows)
    assert all(
        reason not in row.encrypted_note
        for row in rows
        for reason in reasons.values()
    )
    assert fresh is not None
    assert {
        event.event_type: event.reason
        for event in fresh.timeline
        if event.event_type in {"paused", "resumed", "stopped"}
    } == reasons


def test_posting_closure_on_next_mutation_atomically_stops_without_saving_message(
    outreach_db: Database,
    keyring: DataKeyring,
) -> None:
    response = _start(outreach_db, keyring)
    with outreach_db.session() as session:
        posting = session.get(JobPosting, "posting-a")
        assert posting is not None
        posting.lifecycle_state = "closed"
        posting.closure_reason = "explicit"
        posting.closed_at = NOW + timedelta(minutes=1)
        posting.version += 1
        posting.updated_at = NOW + timedelta(minutes=1)

    response = _save(
        outreach_db,
        keyring,
        response,
        recipient_id="application-contact-1",
        kind="initial",
        body="This body must never be persisted after closure.",
        key="closed-posting-save-attempt",
        now=NOW + timedelta(minutes=2),
    )

    assert response.status.value == "stopped"
    assert _sequence(response).reason == "posting_closed"
    assert {recipient.bench_state.value for recipient in response.recipients} == {
        "stopped"
    }
    assert response.timeline[-1].event_type == "stopped"
    assert response.timeline[-1].reason == "posting closed"
    with outreach_db.session() as session:
        assert session.scalar(select(func.count(OutreachMessageVersion.id))) == 0
        assert list(
            session.scalars(
                select(OutreachEvent.event_type).order_by(
                    OutreachEvent.sequence_number
                )
            )
        ) == ["sequence_started", "stopped"]


def test_fourth_cold_employee_send_at_same_company_within_seven_days_is_blocked(
    outreach_db: Database,
    keyring: DataKeyring,
) -> None:
    response = _start(outreach_db, keyring)
    response = _save_copy_send_initial(
        outreach_db,
        keyring,
        response,
        recipient_id="application-contact-1",
        prefix="cold-one",
        sent_at=NOW + timedelta(minutes=3),
    )
    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachOutcomeEventCreate(
            event_type="outcome",
            application_contact_id="application-contact-1",
            outcome="declined",
        ),
        key="cold-one-declined",
        now=NOW + timedelta(minutes=4),
    )
    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachOutcomeEventCreate(
            event_type="outcome",
            application_contact_id="application-contact-4",
            outcome="unreachable",
        ),
        key="recruiter-unreachable-for-cap",
        now=NOW + timedelta(minutes=5),
    )
    assert _sequence(response).active_wave == 2

    for index, recipient_id in enumerate(
        ("application-contact-2", "application-contact-3"),
        start=2,
    ):
        sent_at = NOW + timedelta(minutes=index * 10)
        response = _save_copy_send_initial(
            outreach_db,
            keyring,
            response,
            recipient_id=recipient_id,
            prefix=f"cold-{index}",
            sent_at=sent_at,
        )
        response = _record(
            outreach_db,
            keyring,
            response,
            payload=OutreachOutcomeEventCreate(
                event_type="outcome",
                application_contact_id=recipient_id,
                outcome="declined",
            ),
            key=f"cold-{index}-declined",
            now=sent_at + timedelta(minutes=1),
        )

    assert _sequence(response).active_wave == 4
    response = _save(
        outreach_db,
        keyring,
        response,
        recipient_id="application-contact-5",
        kind="initial",
        body="The fourth cold employee message must be blocked at send time.",
        key="cold-four-save",
        now=NOW + timedelta(minutes=40),
    )
    fourth = _recipient(response, "application-contact-5").initial_message
    assert fourth is not None
    response = _record(
        outreach_db,
        keyring,
        response,
        payload=OutreachCopiedEventCreate(
            event_type="copied",
            message_version_id=fourth.id,
        ),
        key="cold-four-copy",
        now=NOW + timedelta(minutes=41),
    )
    with pytest.raises(ResourceConflict, match="three cold employee contacts"):
        _record(
            outreach_db,
            keyring,
            response,
            payload=OutreachMarkedSentEventCreate(
                event_type="marked_sent",
                message_version_id=fourth.id,
                channel="linkedin",
                confirm_exact_version=True,
            ),
            key="cold-four-send",
            now=NOW + timedelta(minutes=42),
        )
    with outreach_db.session() as session:
        assert session.scalar(
            select(func.count(OutreachEvent.id)).where(
                OutreachEvent.event_type == "marked_sent",
                OutreachEvent.kind == "initial",
            )
        ) == 3


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
