"""Focused tests for one-preview, one-approval application preparation."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from job_hunt_agent.application_dossier_repository import (
    approve_application_dossier,
    preview_application_dossier,
)
from job_hunt_agent.application_dossier_schemas import (
    ApplicationDossierApproveCreate,
    ApplicationDossierPreviewCreate,
)
from job_hunt_agent.models import (
    ApplicationArtifactEvent,
    ApplicationArtifactRevision,
    ApplicationPackEvent,
    ApplicationPackRevision,
    OwnerMutationReceipt,
    ResumeVersion,
)
from job_hunt_agent.repository_errors import ResourceConflict
from tests.test_application_pack_repository import (
    NOW,
    _create,
    _review_payload,
    pack_workspace,
)


def test_preview_is_read_only_and_one_approval_persists_the_exact_package(
    pack_workspace,
) -> None:
    database, keyring, resume_id = pack_workspace
    created = _create(database, keyring, resume_id)
    assert created is not None and created.pack is not None
    reviewed = _review_payload(created)
    evidence = created.current_approved_evidence[0]
    request = ApplicationDossierPreviewCreate(
        grounding_parent_revision_id=reviewed.parent_revision_id,
        requirements=reviewed.requirements,
        selected_evidence_refs=[{"id": evidence.id, "version": evidence.version}],
        questions=[
            {
                "id": "python_impact",
                "text": "Describe your Python impact.",
                "evidence_refs": [{"id": evidence.id, "version": evidence.version}],
            }
        ],
    )

    with database.session() as session:
        preview = preview_application_dossier(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=created.pack.id,
            payload=request,
            expected_pack_version=1,
            keyring=keyring,
            now=NOW + timedelta(minutes=3),
        )
        assert preview is not None and preview.blockers == []
        assert preview.materials.answers[0].status.value == "answered"
        assert evidence.statement in preview.materials.company_note.text
        assert session.scalar(select(func.count(ApplicationPackRevision.id))) == 1
        assert session.scalar(select(func.count(ApplicationPackEvent.id))) == 0
        assert session.scalar(select(func.count(ApplicationArtifactRevision.id))) == 0
        assert session.scalar(select(func.count(ApplicationArtifactEvent.id))) == 0

    approval_request = ApplicationDossierApproveCreate(
        **request.model_dump(),
        preview_fingerprint=preview.preview_fingerprint,
        confirm_dossier_reviewed=True,
    )
    with database.session() as session:
        approved = approve_application_dossier(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=created.pack.id,
            payload=approval_request,
            expected_pack_version=1,
            idempotency_key="approve-complete-dossier",
            keyring=keyring,
            now=NOW + timedelta(minutes=4),
        )
    assert approved is not None
    assert approved.pack.status.value == "reviewed"
    assert approved.artifacts.status.value == "approved"
    assert approved.artifacts.current_revision is not None
    assert approved.artifacts.current_revision.tailored_resume.text == (
        preview.materials.tailored_resume.text
    )
    assert approved.artifacts.current_revision.company_note.text == (
        preview.materials.company_note.text
    )
    assert approved.artifacts.current_revision.answers[0].text == (
        preview.materials.answers[0].text
    )
    assert approved.artifacts.approval_event is not None
    first_event_id = approved.artifacts.approval_event.id

    with database.session() as session:
        replayed = approve_application_dossier(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=created.pack.id,
            payload=approval_request,
            expected_pack_version=1,
            idempotency_key="approve-complete-dossier",
            keyring=keyring,
            now=NOW + timedelta(minutes=5),
        )
        assert replayed is not None and replayed.artifacts.approval_event is not None
        assert replayed.artifacts.approval_event.id == first_event_id
        assert session.scalar(select(func.count(ApplicationPackRevision.id))) == 2
        assert session.scalar(select(func.count(ApplicationPackEvent.id))) == 1
        assert session.scalar(select(func.count(ApplicationArtifactRevision.id))) == 1
        assert session.scalar(select(func.count(ApplicationArtifactEvent.id))) == 1
        assert session.scalar(select(func.count(ResumeVersion.id))) == 2


def test_stale_preview_fingerprint_persists_nothing(
    pack_workspace,
) -> None:
    database, keyring, resume_id = pack_workspace
    created = _create(database, keyring, resume_id)
    assert created is not None and created.pack is not None
    reviewed = _review_payload(created)
    evidence = created.current_approved_evidence[0]
    request = ApplicationDossierPreviewCreate(
        grounding_parent_revision_id=reviewed.parent_revision_id,
        requirements=reviewed.requirements,
        selected_evidence_refs=[{"id": evidence.id, "version": evidence.version}],
        questions=[],
    )
    with database.session() as session:
        preview = preview_application_dossier(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=created.pack.id,
            payload=request,
            expected_pack_version=1,
            keyring=keyring,
        )
    assert preview is not None
    stale = ApplicationDossierApproveCreate(
        **request.model_dump(),
        preview_fingerprint="0" * 64,
        confirm_dossier_reviewed=True,
    )

    with pytest.raises(ResourceConflict, match="preview changed"):
        with database.session() as session:
            approve_application_dossier(
                session,
                owner_id="owner-a",
                application_id="application1",
                pack_id=created.pack.id,
                payload=stale,
                expected_pack_version=1,
                idempotency_key="stale-dossier",
                keyring=keyring,
            )

    with database.session() as session:
        assert session.scalar(select(func.count(ApplicationPackRevision.id))) == 1
        assert session.scalar(select(func.count(ApplicationPackEvent.id))) == 0
        assert session.scalar(select(func.count(ApplicationArtifactRevision.id))) == 0
        assert session.scalar(select(func.count(ApplicationArtifactEvent.id))) == 0
        assert session.scalar(
            select(func.count(OwnerMutationReceipt.id)).where(
                OwnerMutationReceipt.namespace.like("application_dossier.%")
            )
        ) == 0


def test_generated_blocker_rolls_back_outer_and_child_receipts(
    pack_workspace,
) -> None:
    database, keyring, resume_id = pack_workspace
    created = _create(database, keyring, resume_id)
    assert created is not None and created.pack is not None
    reviewed = _review_payload(created)
    evidence = created.current_approved_evidence[0]
    request = ApplicationDossierPreviewCreate(
        grounding_parent_revision_id=reviewed.parent_revision_id,
        requirements=reviewed.requirements,
        selected_evidence_refs=[{"id": evidence.id, "version": evidence.version}],
        questions=[
            {
                "id": "rust_story",
                "text": "Explain advanced Rust compiler internals.",
                "evidence_refs": [],
            }
        ],
    )
    with database.session() as session:
        preview = preview_application_dossier(
            session,
            owner_id="owner-a",
            application_id="application1",
            pack_id=created.pack.id,
            payload=request,
            expected_pack_version=1,
            keyring=keyring,
        )
    assert preview is not None
    assert [item.value for item in preview.blockers] == ["questions_need_owner_input"]
    approval = ApplicationDossierApproveCreate(
        **request.model_dump(),
        preview_fingerprint=preview.preview_fingerprint,
        confirm_dossier_reviewed=True,
    )

    with pytest.raises(ResourceConflict, match="blocker"):
        with database.session() as session:
            approve_application_dossier(
                session,
                owner_id="owner-a",
                application_id="application1",
                pack_id=created.pack.id,
                payload=approval,
                expected_pack_version=1,
                idempotency_key="blocked-dossier",
                keyring=keyring,
            )

    with database.session() as session:
        assert session.scalar(select(func.count(ApplicationPackRevision.id))) == 1
        assert session.scalar(select(func.count(ApplicationPackEvent.id))) == 0
        assert session.scalar(select(func.count(ApplicationArtifactRevision.id))) == 0
        assert session.scalar(select(func.count(ApplicationArtifactEvent.id))) == 0
        assert session.scalar(
            select(func.count(OwnerMutationReceipt.id)).where(
                OwnerMutationReceipt.namespace.like("application_dossier.%")
                | OwnerMutationReceipt.namespace.like("application_artifact.%")
                | OwnerMutationReceipt.namespace.like("application_pack.revision:%")
            )
        ) == 0


def test_dossier_preview_is_owner_scoped(
    pack_workspace,
) -> None:
    database, keyring, resume_id = pack_workspace
    created = _create(database, keyring, resume_id)
    assert created is not None and created.pack is not None
    reviewed = _review_payload(created)
    evidence = created.current_approved_evidence[0]
    request = ApplicationDossierPreviewCreate(
        grounding_parent_revision_id=reviewed.parent_revision_id,
        requirements=reviewed.requirements,
        selected_evidence_refs=[{"id": evidence.id, "version": evidence.version}],
        questions=[],
    )
    with database.session() as session:
        assert preview_application_dossier(
            session,
            owner_id="owner-b",
            application_id="application1",
            pack_id=created.pack.id,
            payload=request,
            expected_pack_version=1,
            keyring=keyring,
        ) is None
