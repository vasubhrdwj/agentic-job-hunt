"""Repository coverage for evidence-pinned, owner-authored interview preparation."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

import job_hunt_agent.interview_preparation_repository as preparation_repository
from job_hunt_agent.application_pack_repository import (
    create_application_pack_revision,
    record_application_pack_event,
)
from job_hunt_agent.application_pack_schemas import (
    ApplicationPackEventCreate,
    ApplicationPackRequirementResponse,
)
from job_hunt_agent.interview_preparation_repository import (
    create_interview_preparation_revision,
    load_application_interview_preparation,
)
from job_hunt_agent.interview_preparation_schemas import (
    InterviewPreparationRevisionCreate,
)
from job_hunt_agent.models import (
    AchievementEvidence,
    Application,
    ApplicationArtifactEvent,
    ApplicationArtifactRevision,
    ApplicationInterviewPreparation,
    ApplicationInterviewPreparationRevision,
    ApplicationInterviewRound,
    ApplicationSubmission,
    Owner,
    PrivacyDeletionReceipt,
)
from job_hunt_agent.mutation_receipts import MutationIdempotencyConflict
from job_hunt_agent.privacy_repository import delete_owner_workspace
from job_hunt_agent.repository_errors import ResourceConflict, VersionConflict
from tests.test_application_pack_repository import (
    NOW,
    _create,
    _review_payload,
    pack_workspace,
)


@pytest.fixture
def submitted_workspace(pack_workspace):
    database, keyring, resume_id = pack_workspace
    created = _create(database, keyring, resume_id)
    assert created is not None and created.pack is not None
    with database.session() as session:
        reviewed = create_application_pack_revision(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=created.pack.id,
            payload=_review_payload(created),
            expected_pack_version=1,
            idempotency_key="prep-grounding-revision",
            keyring=keyring,
            now=NOW + timedelta(minutes=3),
        )
    assert reviewed is not None and reviewed.pack is not None
    assert reviewed.current_revision is not None
    with database.session() as session:
        confirmed = record_application_pack_event(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=reviewed.pack.id,
            payload=ApplicationPackEventCreate.model_validate(
                {
                    "event_type": "reviewed",
                    "revision_id": reviewed.current_revision.id,
                    "confirm_requirements_reviewed": True,
                }
            ),
            expected_pack_version=2,
            idempotency_key="prep-grounding-reviewed",
            keyring=keyring,
            now=NOW + timedelta(minutes=4),
        )
    assert confirmed is not None and confirmed.review_event is not None
    assert confirmed.pack is not None and confirmed.reviewed_revision is not None
    with database.session() as session:
        session.add(
            ApplicationArtifactRevision(
                id="prep-artifact",
                owner_id="owner-a",
                application_id="application1",
                application_pack_id=confirmed.pack.id,
                grounding_revision_id=confirmed.reviewed_revision.id,
                revision_number=1,
                source="deterministic",
                generator_version="application-artifacts-deterministic-v1",
                encrypted_payload="unused-encrypted-artifact",
                encryption_key_id="v1",
                content_hash="a" * 64,
                created_at=NOW + timedelta(minutes=5),
            )
        )
        session.flush()
        session.add(
            ApplicationArtifactEvent(
                id="prep-artifact-approved",
                owner_id="owner-a",
                application_id="application1",
                application_pack_id=confirmed.pack.id,
                artifact_revision_id="prep-artifact",
                sequence_number=1,
                event_type="approved",
                tailored_resume_version_id=resume_id,
                occurred_at=NOW + timedelta(minutes=6),
                idempotency_key_hash="b" * 64,
            )
        )
        session.flush()
        session.add(
            ApplicationSubmission(
                id="prep-submission",
                owner_id="owner-a",
                application_id="application1",
                application_pack_id=confirmed.pack.id,
                application_pack_revision_id=confirmed.reviewed_revision.id,
                application_pack_review_event_id=confirmed.review_event.id,
                application_artifact_revision_id="prep-artifact",
                application_artifact_approval_event_id="prep-artifact-approved",
                tailored_resume_version_id=resume_id,
                destination_url="https://careers.example.com/jobs/1",
                applied_on=date(2026, 7, 14),
                submission_method="manual",
                recorded_at=NOW + timedelta(minutes=7),
                created_at=NOW + timedelta(minutes=7),
            )
        )
        application = session.get(Application, "application1")
        assert application is not None
        application.stage = "applied"
        application.version = 2
        application.updated_at = NOW + timedelta(minutes=7)
    return database, keyring


def _payload(projection, *, complete: bool = True):
    value = "Owner-authored fact" if complete else ""
    return InterviewPreparationRevisionCreate(
        source_fingerprint=projection.source_fingerprint,
        parent_revision_id=(
            projection.latest_revision.id if projection.latest_revision else None
        ),
        prompt_drafts=[
            {
                "prompt_id": prompt.id,
                "situation": value,
                "task": value,
                "action": value,
                "result": value,
            }
            for prompt in projection.prompts
        ],
        confirm_owner_authored=True,
    )


def test_load_and_save_are_evidence_pinned_encrypted_and_idempotent(
    submitted_workspace,
) -> None:
    database, keyring = submitted_workspace
    with database.session() as session:
        before = load_application_interview_preparation(
            session,
            owner_id="owner-a",
            application_id="application1",
            keyring=keyring,
        )
    assert before is not None
    assert before.status.value == "not_started"
    assert before.target.kind.value == "recruiter_screen"
    assert before.write_version_scope == "application"
    assert {prompt.category.value for prompt in before.prompts} == {
        "role_motivation",
        "key_requirement",
        "impact",
        "conflict_ambiguity",
        "failure_learning",
        "leadership_collaboration",
    }
    assert all(prompt.evidence for prompt in before.prompts)
    assert all(prompt.draft.situation == "" for prompt in before.prompts)
    evidence = before.prompts[0].evidence[0]
    assert evidence.statement.startswith("PRIVATE EVIDENCE")
    assert evidence.version >= 2
    assert evidence.source_resume_version_id is None

    payload = _payload(before)
    with database.session() as session:
        saved = create_interview_preparation_revision(
            session,
            owner_id="owner-a",
            application_id="application1",
            payload=payload,
            expected_version=before.write_version,
            idempotency_key="save-preparation-1",
            keyring=keyring,
            now=NOW + timedelta(hours=1),
        )
    assert saved is not None and saved.status.value == "ready"
    assert saved.preparation_version == 1
    assert saved.latest_revision is not None
    assert all(not prompt.missing_sections for prompt in saved.prompts)

    with database.session() as session:
        row = session.scalar(select(ApplicationInterviewPreparationRevision))
        assert row is not None
        assert "Owner-authored fact" not in row.encrypted_payload
        assert "PRIVATE EVIDENCE" not in row.encrypted_payload
        replay = create_interview_preparation_revision(
            session,
            owner_id="owner-a",
            application_id="application1",
            payload=payload,
            expected_version=before.write_version,
            idempotency_key="save-preparation-1",
            keyring=keyring,
            now=NOW + timedelta(hours=2),
        )
        assert replay is not None and replay.preparation_version == 1

    with pytest.raises(MutationIdempotencyConflict):
        with database.session() as session:
            create_interview_preparation_revision(
                session,
                owner_id="owner-a",
                application_id="application1",
                payload=payload.model_copy(
                    update={"confirm_owner_authored": True, "parent_revision_id": "changed"}
                ),
                expected_version=1,
                idempotency_key="save-preparation-1",
                keyring=keyring,
            )


def test_workspace_deletion_removes_multi_revision_preparation_and_keeps_other_owner(
    submitted_workspace,
) -> None:
    database, keyring = submitted_workspace
    with database.session() as session:
        initial = load_application_interview_preparation(
            session,
            owner_id="owner-a",
            application_id="application1",
            keyring=keyring,
        )
        assert initial is not None
        first = create_interview_preparation_revision(
            session,
            owner_id="owner-a",
            application_id="application1",
            payload=_payload(initial),
            expected_version=initial.write_version,
            idempotency_key="save-before-workspace-delete-1",
            keyring=keyring,
            now=NOW + timedelta(hours=1),
        )
        assert first is not None
    with database.session() as session:
        second = create_interview_preparation_revision(
            session,
            owner_id="owner-a",
            application_id="application1",
            payload=_payload(first),
            expected_version=first.write_version,
            idempotency_key="save-before-workspace-delete-2",
            keyring=keyring,
            now=NOW + timedelta(hours=2),
        )
        assert second is not None and second.preparation_version == 2

    with database.session() as session:
        receipt = delete_owner_workspace(
            session,
            owner_id="owner-a",
            confirmation="DELETE WORKSPACE owner-a",
            idempotency_key="delete-owner-with-preparation",
            receipt_secret="privacy-test-receipt-secret-with-more-than-32-characters",
            now=NOW + timedelta(hours=3),
        )
    assert receipt.replayed is False

    with database.session() as session:
        assert session.get(Owner, "owner-a") is None
        assert session.get(Owner, "owner-b") is not None
        assert session.scalar(
            select(func.count()).select_from(ApplicationInterviewPreparation)
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(ApplicationInterviewPreparationRevision)
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(PrivacyDeletionReceipt)
        ) == 1


def test_round_change_keeps_prior_owner_text_read_only_and_requires_reload(
    submitted_workspace,
) -> None:
    database, keyring = submitted_workspace
    with database.session() as session:
        initial = load_application_interview_preparation(
            session,
            owner_id="owner-a",
            application_id="application1",
            keyring=keyring,
        )
        assert initial is not None
        saved = create_interview_preparation_revision(
            session,
            owner_id="owner-a",
            application_id="application1",
            payload=_payload(initial),
            expected_version=initial.write_version,
            idempotency_key="save-before-round",
            keyring=keyring,
            now=NOW + timedelta(hours=1),
        )
        assert saved is not None
    with database.session() as session:
        session.add(
            ApplicationInterviewRound(
                id="scheduled-round",
                owner_id="owner-a",
                application_id="application1",
                application_submission_id="prep-submission",
                round_number=1,
                kind="technical",
                title="Technical interview",
                status="scheduled",
                scheduled_start_at=NOW + timedelta(days=2),
                scheduled_timezone="Asia/Kolkata",
                duration_minutes=60,
                meeting_format="video",
                version=3,
                created_at=NOW + timedelta(hours=2),
                updated_at=NOW + timedelta(hours=2),
            )
        )
    with database.session() as session:
        changed = load_application_interview_preparation(
            session,
            owner_id="owner-a",
            application_id="application1",
            keyring=keyring,
        )
    assert changed is not None
    assert changed.target.kind.value == "interview_round"
    assert changed.target.interview_round_version == 3
    assert changed.previous_context_stale is True
    assert all(
        prompt.draft.situation == "Owner-authored fact"
        for prompt in changed.previous_prompts
    )
    assert all(prompt.draft.situation == "" for prompt in changed.prompts)
    with pytest.raises(ResourceConflict, match="context changed"):
        with database.session() as session:
            create_interview_preparation_revision(
                session,
                owner_id="owner-a",
                application_id="application1",
                payload=_payload(saved),
                expected_version=1,
                idempotency_key="stale-round-save",
                keyring=keyring,
            )


def test_changed_or_retired_evidence_blocks_new_writes(submitted_workspace) -> None:
    database, keyring = submitted_workspace
    with database.session() as session:
        before = load_application_interview_preparation(
            session,
            owner_id="owner-a",
            application_id="application1",
            keyring=keyring,
        )
        assert before is not None
        evidence = session.scalar(select(AchievementEvidence))
        assert evidence is not None
        evidence.approval_state = "retired"
        evidence.retired_at = NOW + timedelta(hours=1)
        evidence.version += 1
    with database.session() as session:
        blocked = load_application_interview_preparation(
            session,
            owner_id="owner-a",
            application_id="application1",
            keyring=keyring,
        )
    assert blocked is not None and blocked.status.value == "blocked"
    assert "evidence_snapshot_changed" in {
        blocker.value for blocker in blocked.blockers
    }
    with pytest.raises(ResourceConflict, match="interview-preparation blockers"):
        with database.session() as session:
            create_interview_preparation_revision(
                session,
                owner_id="owner-a",
                application_id="application1",
                payload=_payload(blocked),
                expected_version=blocked.write_version,
                idempotency_key="retired-evidence-save",
                keyring=keyring,
            )


def test_version_conflict_is_detected_before_append(submitted_workspace) -> None:
    database, keyring = submitted_workspace
    with database.session() as session:
        before = load_application_interview_preparation(
            session,
            owner_id="owner-a",
            application_id="application1",
            keyring=keyring,
        )
        assert before is not None
    with pytest.raises(VersionConflict):
        with database.session() as session:
            create_interview_preparation_revision(
                session,
                owner_id="owner-a",
                application_id="application1",
                payload=_payload(before),
                expected_version=999,
                idempotency_key="wrong-version",
                keyring=keyring,
            )
    with database.session() as session:
        assert session.scalar(select(ApplicationInterviewPreparation)) is None


def test_malformed_multiple_scheduled_rounds_choose_earliest_deterministically(
    submitted_workspace,
) -> None:
    database, keyring = submitted_workspace
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP INDEX uq_application_interview_rounds_owner_scheduled"
        )
    with database.session() as session:
        session.add_all(
            [
                ApplicationInterviewRound(
                    id="later-round",
                    owner_id="owner-a",
                    application_id="application1",
                    application_submission_id="prep-submission",
                    round_number=1,
                    kind="panel",
                    title="Later panel",
                    status="scheduled",
                    scheduled_start_at=NOW + timedelta(days=4),
                    scheduled_timezone="Asia/Kolkata",
                    duration_minutes=60,
                    meeting_format="video",
                    version=1,
                    created_at=NOW + timedelta(hours=1),
                    updated_at=NOW + timedelta(hours=1),
                ),
                ApplicationInterviewRound(
                    id="earlier-round",
                    owner_id="owner-a",
                    application_id="application1",
                    application_submission_id="prep-submission",
                    round_number=2,
                    kind="technical",
                    title="Earlier technical interview",
                    status="scheduled",
                    scheduled_start_at=NOW + timedelta(days=2),
                    scheduled_timezone="Asia/Kolkata",
                    duration_minutes=60,
                    meeting_format="video",
                    version=2,
                    created_at=NOW + timedelta(hours=2),
                    updated_at=NOW + timedelta(hours=2),
                ),
            ]
        )
    with database.session() as session:
        projection = load_application_interview_preparation(
            session,
            owner_id="owner-a",
            application_id="application1",
            keyring=keyring,
        )
    assert projection is not None
    assert projection.target.interview_round_id == "earlier-round"
    assert projection.target.interview_round_version == 2


def test_more_than_twelve_required_grounded_requirements_block_and_reject_writes(
    submitted_workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, keyring = submitted_workspace
    with database.session() as session:
        ordinary = load_application_interview_preparation(
            session,
            owner_id="owner-a",
            application_id="application1",
            keyring=keyring,
        )
    assert ordinary is not None and ordinary.requirements[0].evidence
    evidence = ordinary.requirements[0].evidence[0]
    requirements = [
        ApplicationPackRequirementResponse(
            id=f"required-{index:02d}",
            ordinal=index + 1,
            importance="required",
            text=f"Required capability {index + 1}",
            source_start=index * 2,
            source_end=index * 2 + 1,
            coverage="supported",
            evidence=[evidence],
        )
        for index in range(13)
    ]
    monkeypatch.setattr(
        preparation_repository,
        "_load_revision_payload",
        lambda *args, **kwargs: (
            "persisted_description",
            "x" * 100,
            requirements,
        ),
    )
    with database.session() as session:
        blocked = load_application_interview_preparation(
            session,
            owner_id="owner-a",
            application_id="application1",
            keyring=keyring,
        )
    assert blocked is not None and blocked.status.value == "blocked"
    assert blocked.required_evidence_backed_count == 13
    assert blocked.prompt_capacity == 12
    assert len(blocked.prompts) == 12
    assert "required_prompt_capacity_exceeded" in {
        blocker.value for blocker in blocked.blockers
    }
    assert any("13 required" in step for step in blocked.next_steps)
    with pytest.raises(ResourceConflict, match="interview-preparation blockers"):
        with database.session() as session:
            create_interview_preparation_revision(
                session,
                owner_id="owner-a",
                application_id="application1",
                payload=_payload(blocked),
                expected_version=blocked.write_version,
                idempotency_key="capacity-blocked-save",
                keyring=keyring,
            )
