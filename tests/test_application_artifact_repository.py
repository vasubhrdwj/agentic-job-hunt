"""Repository tests for deterministic, encrypted application artifacts."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from job_hunt_agent.application_artifact_repository import (
    create_application_artifact_revision,
    load_application_artifacts,
    record_application_artifact_event,
)
from job_hunt_agent.application_artifact_schemas import (
    ApplicationArtifactEventCreate,
    ApplicationArtifactRevisionCreate,
)
from job_hunt_agent.application_pack_repository import (
    create_application_pack_revision,
    record_application_pack_event,
)
from job_hunt_agent.application_pack_schemas import ApplicationPackEventCreate
from job_hunt_agent.models import (
    ApplicationArtifactEvent,
    ApplicationArtifactRevision,
    OwnerMutationReceipt,
    ResumeVersion,
)
from job_hunt_agent.profile_repository import delete_resume_version
from job_hunt_agent.repository_errors import ResourceConflict, ResourceInUse
from tests.test_application_pack_repository import (
    NOW,
    _create,
    _review_payload,
    pack_workspace,
)


def _reviewed_pack(database, keyring, resume_id):
    created = _create(database, keyring, resume_id)
    assert created is not None and created.pack is not None
    with database.session() as session:
        revised = create_application_pack_revision(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=created.pack.id,
            payload=_review_payload(created),
            expected_pack_version=1,
            idempotency_key="artifact-grounding-revision",
            keyring=keyring,
            now=NOW + timedelta(minutes=3),
        )
    assert revised is not None and revised.pack is not None
    assert revised.current_revision is not None
    with database.session() as session:
        reviewed = record_application_pack_event(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=revised.pack.id,
            payload=ApplicationPackEventCreate.model_validate(
                {
                    "event_type": "reviewed",
                    "revision_id": revised.current_revision.id,
                    "confirm_requirements_reviewed": True,
                }
            ),
            expected_pack_version=2,
            idempotency_key="artifact-grounding-reviewed",
            keyring=keyring,
            now=NOW + timedelta(minutes=4),
        )
    assert reviewed is not None and reviewed.pack is not None
    assert reviewed.current_revision is not None and reviewed.review_event is not None
    return reviewed


def test_deterministic_generation_is_grounded_exact_encrypted_and_approvable(
    pack_workspace,
) -> None:
    database, keyring, resume_id = pack_workspace
    reviewed = _reviewed_pack(database, keyring, resume_id)
    with database.session() as session:
        before = load_application_artifacts(
            session,
            owner_id="owner-a",
            application_id="application1",
            keyring=keyring,
        )
    assert before is not None and before.status.value == "not_started"
    assert before.source_catalog is not None
    assert before.source_catalog.reviewed_grounding_event_id == reviewed.review_event.id
    evidence = before.source_catalog.evidence[0]

    request = ApplicationArtifactRevisionCreate.model_validate(
        {
            "grounding_revision_id": reviewed.current_revision.id,
            "selected_evidence_refs": [
                {"id": evidence.id, "version": evidence.version}
            ],
            "questions": [
                {
                    "id": "python_impact",
                    "text": "Describe your Python impact.",
                    "evidence_refs": [
                        {"id": evidence.id, "version": evidence.version}
                    ],
                }
            ],
        }
    )
    with database.session() as session:
        generated = create_application_artifact_revision(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=reviewed.pack.id,
            payload=request,
            expected_pack_version=3,
            idempotency_key="artifact-generate-1",
            keyring=keyring,
            now=NOW + timedelta(minutes=5),
        )
    assert generated is not None and generated.pack is not None
    assert generated.pack.version == 4 and generated.status.value == "draft"
    revision = generated.current_revision
    assert revision is not None
    assert revision.grounding_review_event_id == reviewed.review_event.id
    assert revision.questions[0].text == "Describe your Python impact."
    assert revision.answers[0].status.value == "answered"
    assert evidence.statement in revision.tailored_resume.text
    assert evidence.statement in revision.company_note.text
    assert evidence.statement in revision.answers[0].text
    assert revision.diff.base_content_hash != revision.diff.tailored_content_hash
    assert all(claim.derivation == "verbatim" for claim in revision.tailored_resume.claims)

    with database.session() as session:
        row = session.scalar(select(ApplicationArtifactRevision))
        assert row is not None
        assert "PRIVATE RESUME" not in row.encrypted_payload
        assert "PRIVATE EVIDENCE" not in row.encrypted_payload
        assert "Describe your Python impact" not in row.encrypted_payload
        receipts = list(
            session.scalars(
                select(OwnerMutationReceipt).where(
                    OwnerMutationReceipt.namespace.like("application_artifact.%")
                )
            )
        )
        assert receipts and all("PRIVATE" not in item.request_hash for item in receipts)

    with database.session() as session:
        approved = record_application_artifact_event(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=reviewed.pack.id,
            payload=ApplicationArtifactEventCreate.model_validate(
                {
                    "event_type": "approved",
                    "artifact_revision_id": revision.id,
                    "confirm_artifacts_reviewed": True,
                }
            ),
            expected_pack_version=4,
            idempotency_key="artifact-approve-1",
            keyring=keyring,
            now=NOW + timedelta(minutes=6),
        )
    assert approved is not None and approved.status.value == "approved"
    assert approved.approval_event is not None
    assert approved.tailored_resume_version is not None
    assert approved.tailored_resume_version.parent_resume_version_id == resume_id
    assert approved.tailored_resume_version.is_base is False
    with database.session() as session:
        tailored = session.get(ResumeVersion, approved.tailored_resume_version.id)
        assert tailored is not None and tailored.parent_id == resume_id
        assert session.scalar(select(func.count(ApplicationArtifactEvent.id))) == 1
    with pytest.raises(ResourceInUse, match="artifact approval"):
        with database.session() as session:
            delete_resume_version(
                session,
                owner_id="owner-a",
                resume_version_id=approved.tailored_resume_version.id,
                expected_version=1,
            )

    with database.session() as session:
        replayed = record_application_artifact_event(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=reviewed.pack.id,
            payload=ApplicationArtifactEventCreate.model_validate(
                {
                    "event_type": "approved",
                    "artifact_revision_id": revision.id,
                    "confirm_artifacts_reviewed": True,
                }
            ),
            expected_pack_version=4,
            idempotency_key="artifact-approve-1",
            keyring=keyring,
        )
    assert replayed is not None and replayed.approval_event is not None
    assert replayed.approval_event.id == approved.approval_event.id


def test_unanswerable_questions_persist_as_blocked_draft_and_can_be_rejected(
    pack_workspace,
) -> None:
    database, keyring, resume_id = pack_workspace
    reviewed = _reviewed_pack(database, keyring, resume_id)
    assert reviewed.current_revision is not None and reviewed.pack is not None
    request = ApplicationArtifactRevisionCreate.model_validate(
        {
            "grounding_revision_id": reviewed.current_revision.id,
            "questions": [
                {
                    "id": "authorization",
                    "text": "Are you legally authorized to work here?",
                }
            ],
        }
    )
    with database.session() as session:
        generated = create_application_artifact_revision(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=reviewed.pack.id,
            payload=request,
            expected_pack_version=3,
            idempotency_key="artifact-unanswerable",
            keyring=keyring,
        )
    assert generated is not None and generated.current_revision is not None
    assert generated.current_revision.answers[0].status.value == "needs_owner_input"
    assert "questions_need_owner_input" in [item.value for item in generated.blockers]

    with pytest.raises(ResourceConflict, match="answer every"):
        with database.session() as session:
            record_application_artifact_event(
                session,
                owner_id="owner-a",
                application_id="application1",
                pack_id=reviewed.pack.id,
                payload=ApplicationArtifactEventCreate.model_validate(
                    {
                        "event_type": "approved",
                        "artifact_revision_id": generated.current_revision.id,
                        "confirm_artifacts_reviewed": True,
                    }
                ),
                expected_pack_version=4,
                idempotency_key="artifact-invalid-approval",
                keyring=keyring,
            )

    with database.session() as session:
        rejected = record_application_artifact_event(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=reviewed.pack.id,
            payload=ApplicationArtifactEventCreate.model_validate(
                {
                    "event_type": "rejected",
                    "artifact_revision_id": generated.current_revision.id,
                }
            ),
            expected_pack_version=4,
            idempotency_key="artifact-reject-1",
            keyring=keyring,
        )
    assert rejected is not None and rejected.current_event is not None
    assert rejected.current_event.event_type == "rejected"
    assert "current_revision_rejected" in [item.value for item in rejected.blockers]
