"""Deterministic dossier preview and one-action transactional approval."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy.orm import Session

from .application_artifact_repository import (
    _artifact_response,
    _generate_payload,
    _latest_artifact_revision,
    _mapped_evidence,
    _posting_version,
    _select_evidence,
    _validate_question_evidence,
    create_application_artifact_revision,
    record_application_artifact_event,
)
from .application_artifact_schemas import (
    APPLICATION_ARTIFACT_GENERATOR_VERSION,
    ApplicationArtifactAnswerStatus,
    ApplicationArtifactBlocker,
    ApplicationArtifactEventCreate,
    ApplicationArtifactRevisionCreate,
    ApplicationArtifactRevisionResponse,
)
from .application_dossier_schemas import (
    ApplicationDossierApprovalResponse,
    ApplicationDossierApproveCreate,
    ApplicationDossierPreparedInputs,
    ApplicationDossierPreviewResponse,
)
from .application_pack_repository import (
    _latest_revision,
    _load_revision_payload,
    _mapped_evidence_is_current,
    _owned_application,
    _owned_pack_by_id,
    _pack_response,
    _require_editable_application,
    _require_open_posting,
    _require_reviewable_requirements,
    _reviewed_requirements,
    create_application_pack_revision,
)
from .application_pack_schemas import ApplicationPackRevisionCreate
from .job_queue import utcnow
from .mutation_receipts import claim_owner_mutation, complete_owner_mutation
from .profile_repository import load_resume_version
from .repository_errors import ResourceConflict, require_version
from .security import DataKeyring


def preview_application_dossier(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    pack_id: str,
    payload: ApplicationDossierPreparedInputs,
    expected_pack_version: int,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> ApplicationDossierPreviewResponse | None:
    """Build the exact deterministic package without persisting an approval."""

    current = _as_utc(now or utcnow())
    application = _owned_application(session, owner_id, application_id)
    pack = _owned_pack_by_id(
        session,
        owner_id=owner_id,
        application_id=application_id,
        pack_id=pack_id,
    )
    if application is None or pack is None:
        return None
    require_version(
        "application_pack",
        pack.id,
        expected=expected_pack_version,
        actual=pack.version,
    )
    _require_editable_application(application)
    _require_open_posting(session, application)
    parent = _latest_revision(session, pack)
    if parent is None or parent.id != payload.grounding_parent_revision_id:
        raise ResourceConflict("grounding_parent_revision_id must name the current revision")
    _, description, _ = _load_revision_payload(parent, keyring=keyring)
    grounding_payload = ApplicationPackRevisionCreate(
        parent_revision_id=parent.id,
        requirements=payload.requirements,
        confirm_requirements_reviewed=True,
    )
    requirements = _reviewed_requirements(
        session,
        owner_id=owner_id,
        payload=grounding_payload,
        description=description,
        keyring=keyring,
    )
    _require_reviewable_requirements(requirements)
    if not _mapped_evidence_is_current(
        session,
        owner_id=owner_id,
        requirements=requirements,
        keyring=keyring,
    ):
        raise ResourceConflict("mapped evidence changed; reload approved evidence")

    mapped = _mapped_evidence(requirements)
    selected = _select_evidence(mapped, payload.selected_evidence_refs)
    questions = _validate_question_evidence(payload.questions, mapped)
    if not selected:
        raise ResourceConflict("select grounded evidence before preparing a dossier")
    base = load_resume_version(
        session,
        owner_id=owner_id,
        resume_version_id=pack.base_resume_version_id,
        keyring=keyring,
    )
    if base is None:
        raise ResourceConflict("the pinned base resume is unavailable")
    posting = _posting_version(session, application)
    input_fingerprint = _preview_input_fingerprint(
        application_id=application.id,
        pack_id=pack.id,
        pack_version=pack.version,
        payload=payload,
    )
    grounding_id = f"ground_{input_fingerprint[:25]}"
    grounding_event_id = f"review_{input_fingerprint[:25]}"
    generated = _generate_payload(
        grounding=SimpleNamespace(id=grounding_id),
        grounding_event=SimpleNamespace(id=grounding_event_id),
        description=description,
        requirements=requirements,
        posting=posting,
        base_content=base.content,
        selected=selected,
        mapped=mapped,
        questions=questions,
    )
    fingerprint = _generated_preview_fingerprint(
        input_fingerprint=input_fingerprint,
        generated=generated,
    )
    latest_artifact = _latest_artifact_revision(session, pack)
    materials = ApplicationArtifactRevisionResponse(
        id=f"preview_{fingerprint[:24]}",
        application_pack_id=pack.id,
        grounding_revision_id=grounding_id,
        grounding_review_event_id=grounding_event_id,
        parent_artifact_revision_id=(
            latest_artifact.id if latest_artifact is not None else None
        ),
        revision_number=(
            latest_artifact.revision_number + 1 if latest_artifact is not None else 1
        ),
        source="deterministic",
        generator_version=APPLICATION_ARTIFACT_GENERATOR_VERSION,
        selected_evidence=generated["selected_evidence"],
        questions=generated["questions"],
        tailored_resume=generated["tailored_resume"],
        company_note=generated["company_note"],
        answers=generated["answers"],
        diff=generated["diff"],
        created_at=current,
    )
    blockers: list[ApplicationArtifactBlocker] = []
    if any(
        answer.status is ApplicationArtifactAnswerStatus.needs_owner_input
        for answer in materials.answers
    ):
        blockers.append(ApplicationArtifactBlocker.questions_need_owner_input)
    if materials.diff.base_content_hash == materials.diff.tailored_content_hash:
        blockers.append(ApplicationArtifactBlocker.tailored_resume_unchanged)
    return ApplicationDossierPreviewResponse(
        application_id=application.id,
        pack_id=pack.id,
        pack_version=pack.version,
        preview_fingerprint=fingerprint,
        materials=materials,
        blockers=blockers,
    )


def approve_application_dossier(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    pack_id: str,
    payload: ApplicationDossierApproveCreate,
    expected_pack_version: int,
    idempotency_key: str,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> ApplicationDossierApprovalResponse | None:
    """Review grounding, generate materials, and approve them in one transaction."""

    current = _as_utc(now or utcnow())
    application = _owned_application(session, owner_id, application_id)
    pack = _owned_pack_by_id(
        session,
        owner_id=owner_id,
        application_id=application_id,
        pack_id=pack_id,
        for_update=True,
    )
    if application is None or pack is None:
        return None
    claim = claim_owner_mutation(
        session,
        owner_id=owner_id,
        namespace=f"application_dossier.approval:{pack.id}",
        idempotency_key=idempotency_key,
        request={
            "payload": payload.model_dump(mode="json"),
            "expected_pack_version": expected_pack_version,
        },
        now=current,
    )
    if claim.replay is not None:
        if (
            claim.replay.resource_type != "application_dossier"
            or claim.replay.resource_id != pack.id
        ):
            raise ResourceConflict("application-dossier replay is unavailable")
        return ApplicationDossierApprovalResponse(
            application_id=application.id,
            pack=_pack_response(
                session,
                application=application,
                pack=pack,
                keyring=keyring,
            ),
            artifacts=_artifact_response(
                session,
                application=application,
                pack=pack,
                keyring=keyring,
            ),
        )
    prepared = ApplicationDossierPreparedInputs.model_validate(
        payload.model_dump(
            exclude={"preview_fingerprint", "confirm_dossier_reviewed"},
        )
    )
    preview = preview_application_dossier(
        session,
        owner_id=owner_id,
        application_id=application_id,
        pack_id=pack_id,
        payload=prepared,
        expected_pack_version=expected_pack_version,
        keyring=keyring,
        now=current,
    )
    if preview is None:
        raise RuntimeError("dossier preview disappeared after its approval claim")
    if preview.preview_fingerprint != payload.preview_fingerprint:
        raise ResourceConflict("the dossier preview changed; review the refreshed package")
    if preview.blockers:
        raise ResourceConflict("resolve every dossier blocker before approval")

    grounding = create_application_pack_revision(
        session,
        owner_id=owner_id,
        application_id=application_id,
        pack_id=pack_id,
        payload=ApplicationPackRevisionCreate(
            parent_revision_id=payload.grounding_parent_revision_id,
            requirements=payload.requirements,
            confirm_requirements_reviewed=True,
        ),
        expected_pack_version=expected_pack_version,
        idempotency_key=_child_key(idempotency_key, "grounding"),
        keyring=keyring,
        now=current,
    )
    if grounding is None or grounding.pack is None or grounding.current_revision is None:
        raise RuntimeError("grounding approval did not produce its required projection")
    generated = create_application_artifact_revision(
        session,
        owner_id=owner_id,
        application_id=application_id,
        pack_id=pack_id,
        payload=ApplicationArtifactRevisionCreate(
            grounding_revision_id=grounding.current_revision.id,
            parent_artifact_revision_id=preview.materials.parent_artifact_revision_id,
            selected_evidence_refs=payload.selected_evidence_refs,
            questions=payload.questions,
        ),
        expected_pack_version=grounding.pack.version,
        idempotency_key=_child_key(idempotency_key, "materials"),
        keyring=keyring,
        now=current,
    )
    if generated is None or generated.pack is None or generated.current_revision is None:
        raise RuntimeError("dossier generation did not produce its required projection")
    approved = record_application_artifact_event(
        session,
        owner_id=owner_id,
        application_id=application_id,
        pack_id=pack_id,
        payload=ApplicationArtifactEventCreate(
            event_type="approved",
            artifact_revision_id=generated.current_revision.id,
            confirm_artifacts_reviewed=True,
        ),
        expected_pack_version=generated.pack.version,
        idempotency_key=_child_key(idempotency_key, "approval"),
        keyring=keyring,
        now=current,
    )
    if approved is None:
        raise RuntimeError("dossier approval did not produce its required projection")
    application = _owned_application(session, owner_id, application_id)
    pack = _owned_pack_by_id(
        session,
        owner_id=owner_id,
        application_id=application_id,
        pack_id=pack_id,
    )
    if application is None or pack is None:
        raise RuntimeError("approved dossier resources disappeared before completion")
    complete_owner_mutation(
        session,
        owner_id=owner_id,
        receipt_id=claim.receipt_id,
        resource_type="application_dossier",
        resource_id=pack.id,
        result_version=pack.version,
        now=current,
    )
    return ApplicationDossierApprovalResponse(
        application_id=application.id,
        pack=_pack_response(
            session,
            application=application,
            pack=pack,
            keyring=keyring,
        ),
        artifacts=_artifact_response(
            session,
            application=application,
            pack=pack,
            keyring=keyring,
        ),
    )


def _preview_input_fingerprint(
    *,
    application_id: str,
    pack_id: str,
    pack_version: int,
    payload: ApplicationDossierPreparedInputs,
) -> str:
    encoded = json.dumps(
        {
            "generator_version": APPLICATION_ARTIFACT_GENERATOR_VERSION,
            "application_id": application_id,
            "pack_id": pack_id,
            "pack_version": pack_version,
            "payload": payload.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _generated_preview_fingerprint(
    *,
    input_fingerprint: str,
    generated: dict[str, object],
) -> str:
    encoded = json.dumps(
        {
            "input_fingerprint": input_fingerprint,
            "generator_version": APPLICATION_ARTIFACT_GENERATOR_VERSION,
            "generated": generated,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _child_key(root: str, step: str) -> str:
    return hashlib.sha256(f"{root.strip()}:{step}".encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = ["approve_application_dossier", "preview_application_dossier"]
