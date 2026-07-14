"""Database invariants for exact manual application submissions."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    ActionItem,
    Application,
    ApplicationActivityEvent,
    ApplicationArtifactEvent,
    ApplicationArtifactRevision,
    ApplicationPack,
    ApplicationPackEvent,
    ApplicationPackRevision,
    Base,
    JobPosting,
    JobPostingVersion,
    Owner,
    OwnerOpportunity,
    ResumeVersion,
)
from job_hunt_agent.models.application_submission import ApplicationSubmission


NOW = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def submission_db(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'submission-models.db'}")
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        session.add(Owner(id="owner1", display_name="Owner", timezone="Asia/Kolkata"))
        session.flush()
        session.add(
            JobPosting(
                id="posting1",
                owner_id="owner1",
                identity_kind="native",
                identity_key="source:example:1",
                identity_key_hash="1" * 64,
                source="example",
                company_slug="example",
                source_job_id="1",
                canonical_url="https://careers.example.com/jobs/1",
                lifecycle_state="open",
                consecutive_complete_omissions=0,
                first_confirmed_at=NOW,
                last_confirmed_at=NOW,
                version=1,
            )
        )
        session.flush()
        session.add(
            JobPostingVersion(
                id="postingversion1",
                owner_id="owner1",
                job_posting_id="posting1",
                version_number=1,
                content_hash="2" * 64,
                source="example",
                source_job_id="1",
                company_name="Example",
                title="Engineer",
                canonical_url="https://careers.example.com/jobs/1",
                apply_urls=["https://careers.example.com/jobs/1/apply"],
                location="Remote",
                summary="Build systems.",
                description="Requirements:\n- Python experience required.",
                employment_type="full_time",
                source_facts={},
                source_confidence=1.0,
                observed_at=NOW,
            )
        )
        session.flush()
        session.add(
            OwnerOpportunity(
                id="opportunity1",
                owner_id="owner1",
                job_posting_id="posting1",
                decision="pursued",
                reviewed_posting_version_id="postingversion1",
                decision_updated_at=NOW,
                first_surfaced_at=NOW,
                last_surfaced_at=NOW,
                version=2,
            )
        )
        session.flush()
        session.add(
            Application(
                id="application1",
                owner_id="owner1",
                owner_opportunity_id="opportunity1",
                job_posting_id="posting1",
                pursued_posting_version_id="postingversion1",
                stage="applied",
                version=3,
                created_at=NOW,
                updated_at=NOW + timedelta(hours=2),
            )
        )
        session.flush()
        session.add_all(
            [
                ResumeVersion(
                    id="resume1",
                    owner_id="owner1",
                    label="Base",
                    encrypted_content="ciphertext",
                    encryption_key_id="v1",
                    content_hash="3" * 64,
                    source="pasted",
                    is_base=True,
                    version=1,
                ),
                ResumeVersion(
                    id="resume2",
                    owner_id="owner1",
                    parent_id="resume1",
                    label="Tailored",
                    encrypted_content="ciphertext2",
                    encryption_key_id="v1",
                    content_hash="4" * 64,
                    source="edited",
                    is_base=False,
                    version=1,
                ),
            ]
        )
        session.flush()
        session.add(
            ApplicationPack(
                id="pack1",
                owner_id="owner1",
                application_id="application1",
                job_posting_id="posting1",
                posting_version_id="postingversion1",
                base_resume_version_id="resume1",
                version=3,
            )
        )
        session.flush()
        session.add(
            ApplicationPackRevision(
                id="grounding1",
                owner_id="owner1",
                application_id="application1",
                application_pack_id="pack1",
                revision_number=1,
                source="extracted",
                encrypted_payload="ciphertext",
                encryption_key_id="v1",
                content_hash="5" * 64,
            )
        )
        session.flush()
        session.add(
            ApplicationPackEvent(
                id="groundingreview1",
                owner_id="owner1",
                application_id="application1",
                application_pack_id="pack1",
                revision_id="grounding1",
                sequence_number=1,
                event_type="reviewed",
                occurred_at=NOW,
                idempotency_key_hash="6" * 64,
            )
        )
        session.add(
            ApplicationArtifactRevision(
                id="artifact1",
                owner_id="owner1",
                application_id="application1",
                application_pack_id="pack1",
                grounding_revision_id="grounding1",
                revision_number=1,
                source="deterministic",
                generator_version="application-artifacts-deterministic-v1",
                encrypted_payload="ciphertext",
                encryption_key_id="v1",
                content_hash="7" * 64,
            )
        )
        session.flush()
        session.add(
            ApplicationArtifactEvent(
                id="artifactapproval1",
                owner_id="owner1",
                application_id="application1",
                application_pack_id="pack1",
                artifact_revision_id="artifact1",
                sequence_number=1,
                event_type="approved",
                tailored_resume_version_id="resume2",
                occurred_at=NOW + timedelta(hours=1),
                idempotency_key_hash="8" * 64,
            )
        )
        session.flush()
        session.add_all(
            [
                _action("action1", "review_and_prepare_application", "completed", NOW),
                _action("action2", "submit_application", "completed", NOW + timedelta(hours=1)),
                _action("action3", "follow_up_application", "open", None),
            ]
        )
        session.flush()
        session.add(
            ApplicationSubmission(
                id="submission1",
                owner_id="owner1",
                application_id="application1",
                application_pack_id="pack1",
                application_pack_revision_id="grounding1",
                application_pack_review_event_id="groundingreview1",
                application_artifact_revision_id="artifact1",
                application_artifact_approval_event_id="artifactapproval1",
                tailored_resume_version_id="resume2",
                destination_url="https://careers.example.com/jobs/1/apply",
                applied_on=date(2026, 7, 14),
                submission_method="manual",
                recorded_at=NOW + timedelta(hours=2),
            )
        )
        session.flush()
        session.add_all(
            [
                _activity(
                    "activity1",
                    1,
                    "application_created",
                    None,
                    "pursuing",
                    "action1",
                ),
                _activity(
                    "activity2",
                    2,
                    "application_ready_to_apply",
                    "pursuing",
                    "ready_to_apply",
                    "action2",
                    previous_action_id="action1",
                ),
                _activity(
                    "activity3",
                    3,
                    "application_applied",
                    "ready_to_apply",
                    "applied",
                    "action3",
                    previous_action_id="action2",
                    submission_id="submission1",
                ),
            ]
        )
    try:
        yield database
    finally:
        database.dispose()


def _action(
    action_id: str,
    kind: str,
    status: str,
    completed_at: datetime | None,
) -> ActionItem:
    return ActionItem(
        id=action_id,
        owner_id="owner1",
        application_id="application1",
        kind=kind,
        title=kind.replace("_", " "),
        status=status,
        due_on=date(2026, 7, 21),
        completed_at=completed_at,
        version=2 if completed_at else 1,
        created_at=NOW,
        updated_at=completed_at or NOW,
    )


def _activity(
    event_id: str,
    sequence: int,
    event_type: str,
    from_stage: str | None,
    to_stage: str,
    action_id: str,
    *,
    previous_action_id: str | None = None,
    submission_id: str | None = None,
) -> ApplicationActivityEvent:
    return ApplicationActivityEvent(
        id=event_id,
        owner_id="owner1",
        application_id="application1",
        sequence_number=sequence,
        event_type=event_type,
        from_stage=from_stage,
        to_stage=to_stage,
        action_item_id=action_id,
        previous_action_item_id=previous_action_id,
        submission_id=submission_id,
        occurred_at=NOW + timedelta(hours=sequence - 1),
    )


def test_submission_and_activity_pin_one_exact_owner_scoped_graph(
    submission_db: Database,
) -> None:
    with submission_db.session() as session:
        submission = session.get(ApplicationSubmission, "submission1")
        assert submission is not None
        assert submission.application_pack_revision_id == "grounding1"
        assert submission.application_artifact_approval_event_id == "artifactapproval1"
        assert submission.tailored_resume_version_id == "resume2"
        assert session.scalar(select(func.count(ApplicationActivityEvent.id))) == 3
        open_actions = list(
            session.scalars(select(ActionItem).where(ActionItem.status == "open"))
        )
        assert [(item.id, item.kind) for item in open_actions] == [
            ("action3", "follow_up_application")
        ]


def test_submission_uniqueness_and_applied_event_shape_are_enforced(
    submission_db: Database,
) -> None:
    with pytest.raises(IntegrityError):
        with submission_db.session() as session:
            original = session.get(ApplicationSubmission, "submission1")
            assert original is not None
            values = {
                column.name: getattr(original, column.name)
                for column in ApplicationSubmission.__table__.columns
                if column.name not in {"id", "created_at"}
            }
            session.add(ApplicationSubmission(id="submission2", **values))

    with pytest.raises(IntegrityError):
        with submission_db.session() as session:
            session.add(
                _activity(
                    "activitybad",
                    4,
                    "application_applied",
                    "ready_to_apply",
                    "applied",
                    "action3",
                    previous_action_id="action2",
                    submission_id="submission1",
                )
            )
