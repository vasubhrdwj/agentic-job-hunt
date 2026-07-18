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
    undo_application_pursuit,
)
from job_hunt_agent.contact_search_repository import (
    CONTACT_SEARCH_JOB_KIND,
    create_contact_search,
)
from job_hunt_agent.database import Database
from job_hunt_agent.job_queue import claim_next_job, complete_job
from job_hunt_agent.models import (
    ActionItem,
    Application,
    ApplicationActivityEvent,
    ApplicationArtifactEvent,
    ApplicationArtifactRevision,
    ApplicationContact,
    ApplicationMetricSnapshot,
    ApplicationOutcome,
    ApplicationPack,
    ApplicationPackEvent,
    ApplicationPackRevision,
    ApplicationSubmission,
    Base,
    BackgroundJob,
    BackgroundJobEvent,
    JobPosting,
    JobPostingVersion,
    OpportunityDecisionEvent,
    OwnerMutationReceipt,
    Owner,
    OwnerOpportunity,
    ResumeVersion,
    Contact,
    ContactPlan,
    OutreachEvent,
    OutreachMessageVersion,
    OutreachSequence,
)
from job_hunt_agent.opportunity_repository import (
    OpportunityNotFound,
    _decision_event_response,
)
from job_hunt_agent.opportunity_schemas import PursueOpportunityRequest
from job_hunt_agent.repository_errors import ResourceConflict, VersionConflict
from job_hunt_agent.security import DataKeyring
from tests.test_contact_models import _application_contact, _contact, _plan
from tests.test_application_submission_models import submission_db
from tests.test_outreach_models import _event, _message, _sequence


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


def _seed_outreach_draft(
    session,
    *,
    application_id: str,
    marked_sent: bool,
) -> None:
    plan = _plan(
        discovered_count=1,
        verified_count=1,
        selected_count=1,
        coverage_status="partial",
        exhausted=True,
    )
    plan.application_id = application_id
    contact = _contact(contact_id="contact-undo", identity_hash="c" * 64)
    session.add_all([plan, contact])
    session.flush()
    application_contact = _application_contact(
        row_id="application-contact-undo",
        contact_id=contact.id,
        pool_rank=1,
        verification_status="verified",
        confidence=0.9,
        bench_rank=1,
        wave=1,
        bench_state="reserve",
    )
    application_contact.application_id = application_id
    session.add(application_contact)
    session.flush()
    sequence = _sequence()
    sequence.application_id = application_id
    sequence.contact_plan_id = plan.id
    session.add(sequence)
    session.flush()
    message = _message()
    message.application_id = application_id
    message.application_contact_id = application_contact.id
    session.add(message)
    session.flush()
    events = [
        _event(
            event_id="event-started-undo",
            sequence_number=1,
            event_type="sequence_started",
            hash_character="1",
            wave=1,
        ),
        _event(
            event_id="event-saved-undo",
            sequence_number=2,
            event_type="message_saved",
            hash_character="2",
            application_contact_id=application_contact.id,
            message_version_id=message.id,
            kind="initial",
        ),
        _event(
            event_id="event-copied-undo",
            sequence_number=3,
            event_type="copied",
            hash_character="3",
            application_contact_id=application_contact.id,
            message_version_id=message.id,
            kind="initial",
        ),
    ]
    if marked_sent:
        events.append(
            _event(
                event_id="event-sent-undo",
                sequence_number=4,
                event_type="marked_sent",
                hash_character="4",
                application_contact_id=application_contact.id,
                message_version_id=message.id,
                kind="initial",
                channel="linkedin",
                follow_up_due_at=NOW + timedelta(days=5),
            )
        )
    for event in events:
        event.application_id = application_id
        event.outreach_sequence_id = sequence.id
    session.add_all(events)


def _seed_reviewed_application_materials(
    session,
    *,
    application_id: str,
) -> None:
    session.add_all(
        [
            ResumeVersion(
                id="resume-base-undo",
                owner_id="owner-a",
                label="Base resume",
                encrypted_content="base-ciphertext",
                encryption_key_id="v1",
                content_hash="a" * 64,
                source="pasted",
                is_base=True,
                version=1,
            ),
            ResumeVersion(
                id="resume-tailored-undo",
                owner_id="owner-a",
                parent_id="resume-base-undo",
                label="Tailored resume",
                encrypted_content="tailored-ciphertext",
                encryption_key_id="v1",
                content_hash="b" * 64,
                source="edited",
                is_base=False,
                version=1,
            ),
        ]
    )
    session.flush()
    session.add(
        ApplicationPack(
            id="pack-undo",
            owner_id="owner-a",
            application_id=application_id,
            job_posting_id="posting-a",
            posting_version_id="posting-version-2",
            base_resume_version_id="resume-base-undo",
            version=4,
        )
    )
    session.flush()
    session.add(
        ApplicationPackRevision(
            id="pack-revision-1-undo",
            owner_id="owner-a",
            application_id=application_id,
            application_pack_id="pack-undo",
            revision_number=1,
            source="extracted",
            encrypted_payload="grounding-one",
            encryption_key_id="v1",
            content_hash="c" * 64,
        )
    )
    session.flush()
    session.add(
        ApplicationPackRevision(
            id="pack-revision-2-undo",
            owner_id="owner-a",
            application_id=application_id,
            application_pack_id="pack-undo",
            parent_revision_id="pack-revision-1-undo",
            revision_number=2,
            source="edited",
            encrypted_payload="grounding-two",
            encryption_key_id="v1",
            content_hash="d" * 64,
        )
    )
    session.flush()
    session.add(
        ApplicationPackEvent(
            id="pack-review-undo",
            owner_id="owner-a",
            application_id=application_id,
            application_pack_id="pack-undo",
            revision_id="pack-revision-2-undo",
            sequence_number=1,
            event_type="reviewed",
            occurred_at=NOW,
            idempotency_key_hash="e" * 64,
        )
    )
    session.add(
        ApplicationArtifactRevision(
            id="artifact-revision-1-undo",
            owner_id="owner-a",
            application_id=application_id,
            application_pack_id="pack-undo",
            grounding_revision_id="pack-revision-2-undo",
            revision_number=1,
            source="deterministic",
            generator_version="application-artifacts-deterministic-v1",
            encrypted_payload="artifact-one",
            encryption_key_id="v1",
            content_hash="f" * 64,
        )
    )
    session.flush()
    session.add(
        ApplicationArtifactRevision(
            id="artifact-revision-2-undo",
            owner_id="owner-a",
            application_id=application_id,
            application_pack_id="pack-undo",
            grounding_revision_id="pack-revision-2-undo",
            parent_artifact_revision_id="artifact-revision-1-undo",
            revision_number=2,
            source="deterministic",
            generator_version="application-artifacts-deterministic-v1",
            encrypted_payload="artifact-two",
            encryption_key_id="v1",
            content_hash="0" * 64,
        )
    )
    session.flush()
    session.add(
        ApplicationArtifactEvent(
            id="artifact-approval-undo",
            owner_id="owner-a",
            application_id=application_id,
            application_pack_id="pack-undo",
            artifact_revision_id="artifact-revision-2-undo",
            sequence_number=1,
            event_type="approved",
            tailored_resume_version_id="resume-tailored-undo",
            occurred_at=NOW + timedelta(minutes=1),
            idempotency_key_hash="1" * 64,
        )
    )


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


def test_undo_pursuit_deletes_only_the_application_graph_and_replays(
    application_repository_db: Database,
) -> None:
    with application_repository_db.session() as session:
        pursued = pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            PursueOpportunityRequest(),
            1,
            "pursue-before-undo",
            NOW,
        )
        assert pursued.pursuit is not None
        application_id = pursued.pursuit.application.id
        pursued_event_id = pursued.event.id
        _seed_outreach_draft(
            session,
            application_id=application_id,
            marked_sent=False,
        )
        contact_search = create_contact_search(
            session,
            owner_id="owner-a",
            application_id=application_id,
            expected_application_version=1,
            idempotency_key="queued-contact-search-before-undo",
            now=NOW,
        )
        assert contact_search is not None and contact_search.created is True
        assert contact_search.plan.status == "queued"
        assert contact_search.plan.background_job_id is not None

    with application_repository_db.session() as session:
        restored = undo_application_pursuit(
            session,
            "owner-a",
            application_id,
            1,
            "undo-once",
            NOW + timedelta(hours=1),
        )
        assert restored is not None
        assert restored.state.value == "inbox"
        assert restored.opportunity_version == 3
        assert restored.event.action.value == "restore_to_inbox"
        assert restored.event.previous_state.value == "pursued"
        assert restored.event.restores_event_id == pursued_event_id

        replay = undo_application_pursuit(
            session,
            "owner-a",
            application_id,
            1,
            "undo-once",
            NOW + timedelta(hours=2),
        )
        assert replay is not None
        assert replay == restored
        opportunity = session.get(OwnerOpportunity, "opportunity-a")
        assert opportunity is not None
        opportunity.last_surfaced_at = NOW + timedelta(hours=3)
        opportunity.updated_at = NOW + timedelta(hours=3)
        opportunity.version += 1
        session.flush()
        replay_after_scan_update = undo_application_pursuit(
            session,
            "owner-a",
            application_id,
            1,
            "undo-once",
            NOW + timedelta(hours=3),
        )
        assert replay_after_scan_update is not None
        assert replay_after_scan_update.opportunity_version == 4
        assert replay_after_scan_update.state == restored.state
        assert replay_after_scan_update.event == restored.event
        with pytest.raises(ResourceConflict, match="pursuit was undone"):
            pursue_owner_opportunity(
                session,
                "owner-a",
                "opportunity-a",
                PursueOpportunityRequest(),
                1,
                "pursue-before-undo",
                NOW + timedelta(hours=2),
            )
        assert (
            undo_application_pursuit(
                session,
                "owner-a",
                application_id,
                1,
                "different-undo-key",
                NOW + timedelta(hours=2),
            )
            is None
        )

        assert session.get(Application, application_id) is None
        assert _count(session, ActionItem) == 0
        assert _count(session, ApplicationActivityEvent) == 0
        assert _count(session, ApplicationMetricSnapshot) == 0
        assert _count(session, ContactPlan) == 0
        assert _count(session, ApplicationContact) == 0
        assert _count(session, OutreachSequence) == 0
        assert _count(session, OutreachMessageVersion) == 0
        assert _count(session, OutreachEvent) == 0
        assert _count(session, BackgroundJob) == 0
        assert _count(session, BackgroundJobEvent) == 0
        # Canonical public identities are owner-level, not application-owned.
        assert _count(session, Contact) == 1
        assert _count(session, JobPosting) == 1
        assert _count(session, JobPostingVersion) == 2
        assert _count(session, OwnerOpportunity) == 1
        assert _count(session, OpportunityDecisionEvent) == 2
        opportunity = session.get(OwnerOpportunity, "opportunity-a")
        assert opportunity is not None
        assert opportunity.decision == "inbox"

        with pytest.raises(ResourceConflict, match="different mutation request"):
            undo_application_pursuit(
                session,
                "owner-a",
                application_id,
                2,
                "undo-once",
                NOW + timedelta(hours=3),
            )


def test_undo_pursuit_deletes_reviewed_pack_and_artifact_graph_in_fk_order(
    application_repository_db: Database,
) -> None:
    with application_repository_db.session() as session:
        pursued = pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            PursueOpportunityRequest(),
            1,
            "pursue-before-reviewed-materials",
            NOW,
        )
        assert pursued.pursuit is not None
        application_id = pursued.pursuit.application.id
        _seed_reviewed_application_materials(
            session,
            application_id=application_id,
        )

    with application_repository_db.session() as session:
        restored = undo_application_pursuit(
            session,
            "owner-a",
            application_id,
            1,
            "undo-reviewed-materials",
            NOW + timedelta(hours=1),
        )
        assert restored is not None and restored.state.value == "inbox"
        assert session.get(Application, application_id) is None
        assert _count(session, ApplicationPack) == 0
        assert _count(session, ApplicationPackRevision) == 0
        assert _count(session, ApplicationPackEvent) == 0
        assert _count(session, ApplicationArtifactRevision) == 0
        assert _count(session, ApplicationArtifactEvent) == 0
        # Resume versions are owner-owned source material, not disposable
        # application history.
        assert _count(session, ResumeVersion) == 2


def test_undo_pursuit_rejects_a_running_contact_search_without_orphans(
    application_repository_db: Database,
) -> None:
    with application_repository_db.session() as session:
        pursued = pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            PursueOpportunityRequest(),
            1,
            "pursue-before-running-search",
            NOW,
        )
        assert pursued.pursuit is not None
        application_id = pursued.pursuit.application.id
        search = create_contact_search(
            session,
            owner_id="owner-a",
            application_id=application_id,
            expected_application_version=1,
            idempotency_key="running-search-before-undo",
            now=NOW,
        )
        assert search is not None and search.created is True
        plan_id = search.plan.id
        job_id = search.plan.background_job_id
        assert job_id is not None
        claimed = claim_next_job(
            session,
            worker_id="test-contact-worker",
            lease_token="running-contact-lease",
            kinds={CONTACT_SEARCH_JOB_KIND},
            now=NOW,
        )
        assert claimed is not None and claimed.id == job_id
        assert claimed.status == "running"

    with pytest.raises(ResourceConflict, match="currently running"):
        with application_repository_db.session() as session:
            undo_application_pursuit(
                session,
                "owner-a",
                application_id,
                1,
                "undo-running-search",
                NOW + timedelta(minutes=1),
            )

    with application_repository_db.session() as session:
        assert session.get(Application, application_id) is not None
        assert session.get(ContactPlan, plan_id) is not None
        job = session.get(BackgroundJob, job_id)
        assert job is not None and job.status == "running"
        assert _count(session, BackgroundJobEvent) == 2
        opportunity = session.get(OwnerOpportunity, "opportunity-a")
        assert opportunity is not None and opportunity.decision == "pursued"


def test_undo_pursuit_deletes_a_completed_contact_search_job_and_history(
    application_repository_db: Database,
) -> None:
    with application_repository_db.session() as session:
        pursued = pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            PursueOpportunityRequest(),
            1,
            "pursue-before-completed-search",
            NOW,
        )
        assert pursued.pursuit is not None
        application_id = pursued.pursuit.application.id
        search = create_contact_search(
            session,
            owner_id="owner-a",
            application_id=application_id,
            expected_application_version=1,
            idempotency_key="completed-search-before-undo",
            now=NOW,
        )
        assert search is not None and search.created is True
        plan_id = search.plan.id
        job_id = search.plan.background_job_id
        assert job_id is not None
        claimed = claim_next_job(
            session,
            worker_id="test-contact-worker",
            lease_token="completed-contact-lease",
            kinds={CONTACT_SEARCH_JOB_KIND},
            now=NOW,
        )
        assert claimed is not None and claimed.id == job_id
        plan = session.get(ContactPlan, plan_id)
        assert plan is not None
        plan.status = "completed"
        plan.coverage_status = "partial"
        plan.exhausted = True
        plan.started_at = NOW
        plan.finalized_at = NOW + timedelta(minutes=1)
        plan.updated_at = NOW + timedelta(minutes=1)
        plan.version += 1
        completed = complete_job(
            session,
            job_id,
            worker_id="test-contact-worker",
            lease_token="completed-contact-lease",
            now=NOW + timedelta(minutes=1),
        )
        assert completed is not None and completed.status == "succeeded"

    with application_repository_db.session() as session:
        restored = undo_application_pursuit(
            session,
            "owner-a",
            application_id,
            1,
            "undo-completed-search",
            NOW + timedelta(hours=1),
        )
        assert restored is not None and restored.state.value == "inbox"
        assert session.get(Application, application_id) is None
        assert session.get(ContactPlan, plan_id) is None
        assert session.get(BackgroundJob, job_id) is None
        assert _count(session, BackgroundJobEvent) == 0


def test_pursue_undo_repursue_cycle_keeps_each_replay_bound_to_its_cycle(
    application_repository_db: Database,
) -> None:
    with application_repository_db.session() as session:
        first = pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            PursueOpportunityRequest(),
            1,
            "pursue-cycle-a",
            NOW,
        )
        assert first.pursuit is not None
        first_application_id = first.pursuit.application.id
        first_event_id = first.event.id
        restored = undo_application_pursuit(
            session,
            "owner-a",
            first_application_id,
            1,
            "undo-cycle-a",
            NOW + timedelta(minutes=1),
        )
        assert restored is not None

        second = pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            PursueOpportunityRequest(),
            restored.opportunity_version,
            "pursue-cycle-b",
            NOW + timedelta(minutes=2),
        )
        assert second.pursuit is not None
        second_application_id = second.pursuit.application.id
        second_event_id = second.event.id
        assert second_application_id != first_application_id
        assert second_event_id != first_event_id
        assert second.pursuit.application_created is True

        with pytest.raises(ResourceConflict, match="undone or superseded"):
            pursue_owner_opportunity(
                session,
                "owner-a",
                "opportunity-a",
                PursueOpportunityRequest(),
                1,
                "pursue-cycle-a",
                NOW + timedelta(minutes=3),
            )
        replayed_second = pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            PursueOpportunityRequest(),
            restored.opportunity_version,
            "pursue-cycle-b",
            NOW + timedelta(minutes=3),
        )
        assert replayed_second == second

        current = pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            PursueOpportunityRequest(),
            second.opportunity_version,
            "pursue-cycle-c",
            NOW + timedelta(minutes=4),
        )
        assert current.pursuit is not None
        assert current.pursuit.application.id == second_application_id
        assert current.event.id == second_event_id
        assert current.pursuit.application_created is False
        with pytest.raises(ResourceConflict, match="superseded"):
            undo_application_pursuit(
                session,
                "owner-a",
                first_application_id,
                1,
                "undo-cycle-a",
                NOW + timedelta(minutes=5),
            )


def test_undo_pursuit_masks_foreign_ids_and_fails_closed_after_external_actions(
    application_repository_db: Database,
) -> None:
    with application_repository_db.session() as session:
        pursued = pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            PursueOpportunityRequest(),
            1,
            "pursue-before-send",
            NOW,
        )
        assert pursued.pursuit is not None
        application_id = pursued.pursuit.application.id
        _seed_outreach_draft(
            session,
            application_id=application_id,
            marked_sent=True,
        )

    with application_repository_db.session() as session:
        assert (
            undo_application_pursuit(
                session,
                "owner-b",
                application_id,
                1,
                "foreign-undo",
                NOW + timedelta(hours=1),
            )
            is None
        )

    with pytest.raises(ResourceConflict, match="sent outreach"):
        with application_repository_db.session() as session:
            undo_application_pursuit(
                session,
                "owner-a",
                application_id,
                1,
                "blocked-undo",
                NOW + timedelta(hours=1),
            )

    with application_repository_db.session() as session:
        assert session.get(Application, application_id) is not None
        opportunity = session.get(OwnerOpportunity, "opportunity-a")
        assert opportunity is not None and opportunity.decision == "pursued"
        assert _count(session, OpportunityDecisionEvent) == 1
        # The failed mutation rolled its pending receipt back atomically.
        receipt_namespaces = list(
            session.scalars(select(OwnerMutationReceipt.namespace))
        )
        assert receipt_namespaces == ["opportunity.pursue:opportunity-a"]


def test_undo_pursuit_rejects_recorded_outcomes_and_hiring_progress(
    application_repository_db: Database,
) -> None:
    with application_repository_db.session() as session:
        pursued = pursue_owner_opportunity(
            session,
            "owner-a",
            "opportunity-a",
            PursueOpportunityRequest(),
            1,
            "pursue-before-outcome",
            NOW,
        )
        assert pursued.pursuit is not None
        application_id = pursued.pursuit.application.id
        session.add(
            ApplicationOutcome(
                id="outcome-before-undo",
                owner_id="owner-a",
                application_id=application_id,
                application_submission_id=None,
                stage_at_outcome="pursuing",
                outcome="withdrawn",
                outcome_on=LOCAL_TODAY,
                recording_method="manual",
                recorded_at=NOW,
                created_at=NOW,
            )
        )

    with pytest.raises(ResourceConflict, match="recorded outcome"):
        with application_repository_db.session() as session:
            undo_application_pursuit(
                session,
                "owner-a",
                application_id,
                1,
                "outcome-undo",
                NOW + timedelta(hours=1),
            )

    with application_repository_db.session() as session:
        outcome = session.get(ApplicationOutcome, "outcome-before-undo")
        assert outcome is not None
        session.delete(outcome)
        application = session.get(Application, application_id)
        assert application is not None
        application.stage = "applied"

    with pytest.raises(ResourceConflict, match="hiring progress"):
        with application_repository_db.session() as session:
            undo_application_pursuit(
                session,
                "owner-a",
                application_id,
                1,
                "progress-undo",
                NOW + timedelta(hours=2),
            )


def test_undo_pursuit_rejects_a_recorded_submission(
    submission_db: Database,
) -> None:
    with pytest.raises(ResourceConflict, match="submitted applications"):
        with submission_db.session() as session:
            undo_application_pursuit(
                session,
                "owner1",
                "application1",
                3,
                "cannot-undo-submission",
                NOW,
            )

    with submission_db.session() as session:
        assert session.get(Application, "application1") is not None
        assert session.get(ApplicationSubmission, "submission1") is not None


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
