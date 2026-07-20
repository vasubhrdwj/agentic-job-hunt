"""Encrypted deterministic application artifacts from reviewed grounding snapshots."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .application_artifact_schemas import (
    APPLICATION_ARTIFACT_GENERATOR_VERSION,
    APPLICATION_ARTIFACT_SCHEMA_VERSION,
    MAX_APPLICATION_ARTIFACT_NOTE_CHARS,
    ApplicationArtifactAnswer,
    ApplicationArtifactAnswerStatus,
    ApplicationArtifactBlocker,
    ApplicationArtifactClaim,
    ApplicationArtifactDiff,
    ApplicationArtifactDiffLine,
    ApplicationArtifactDocument,
    ApplicationArtifactEventCreate,
    ApplicationArtifactEventResponse,
    ApplicationArtifactEvidenceClaimSource,
    ApplicationArtifactJobDescriptionClaimSource,
    ApplicationArtifactPostingFieldClaimSource,
    ApplicationArtifactRevisionCreate,
    ApplicationArtifactRevisionResponse,
    ApplicationArtifactSourceCatalog,
    ApplicationArtifactStatus,
    ApplicationArtifactsResponse,
)
from .application_pack_repository import (
    _load_revision_payload,
    _mapped_evidence_is_current,
    _pack_summary,
)
from .application_pack_schemas import (
    ApplicationPackEvidenceSnapshot,
    ApplicationPackRequirementCoverage,
    ApplicationPackRequirementImportance,
    ApplicationPackRequirementResponse,
)
from .job_queue import utcnow
from .models import (
    Application,
    ApplicationArtifactEvent,
    ApplicationArtifactRevision,
    ApplicationPack,
    ApplicationPackEvent,
    ApplicationPackRevision,
    JobPosting,
    JobPostingVersion,
    ResumeVersion,
)
from .mutation_receipts import claim_owner_mutation, complete_owner_mutation
from .private_payloads import decrypt_private_payload, encrypt_private_payload
from .profile_repository import create_or_reuse_resume_version, load_resume_version
from .profile_schemas import ResumeVersionSummary
from .repository_errors import ResourceConflict, require_version
from .resume_docx import (
    ApprovedResumeDocx,
    ResumeDocxError,
    build_resume_docx,
    safe_resume_filename,
)
from .security import DataKeyring, MAX_RESUME_CHARS


_TOKEN_RE = re.compile(r"[a-z][a-z0-9+#.\-]{2,}")
_STOP_TOKENS = {
    "and",
    "are",
    "for",
    "from",
    "have",
    "how",
    "our",
    "that",
    "the",
    "this",
    "what",
    "with",
    "will",
    "you",
    "your",
}


class ApplicationArtifactRepositoryError(RuntimeError):
    """Persisted artifact state failed a private-data invariant."""


def load_application_artifacts(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    keyring: DataKeyring,
) -> ApplicationArtifactsResponse | None:
    """Return the database-only application artifact projection."""

    application = _owned_application(session, owner_id, application_id)
    if application is None:
        return None
    pack = _owned_pack(session, owner_id, application_id)
    return _artifact_response(
        session,
        application=application,
        pack=pack,
        keyring=keyring,
    )


def load_approved_tailored_resume_docx(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    keyring: DataKeyring,
) -> ApprovedResumeDocx | None:
    """Export only the latest revision when that exact revision is approved."""

    application = _owned_application(session, owner_id, application_id)
    if application is None:
        return None
    # Serialize the approval/current-revision decision with the same pack-row
    # lock used by artifact writers. Without this boundary, a concurrent draft
    # could supersede the approval between our latest-revision reads.
    pack = _owned_pack(
        session,
        owner_id,
        application_id,
        for_update=True,
    )
    if pack is None:
        raise ResourceConflict("approve the current materials before downloading a resume")
    revision = _latest_artifact_revision(session, pack)
    event = _artifact_event(session, pack, revision) if revision is not None else None
    if revision is None or event is None or event.event_type != "approved":
        raise ResourceConflict("approve the current materials before downloading a resume")
    if event.tailored_resume_version_id is None:
        raise ApplicationArtifactRepositoryError(
            "approved artifact does not name its tailored resume"
        )

    stored = _load_artifact_payload(revision, keyring=keyring)
    approved_resume = load_resume_version(
        session,
        owner_id=owner_id,
        resume_version_id=event.tailored_resume_version_id,
        keyring=keyring,
    )
    if approved_resume is None or approved_resume.content != stored.tailored_resume.text:
        raise ApplicationArtifactRepositoryError(
            "approved artifact and tailored resume do not match"
        )
    posting = _posting_version(session, application)
    try:
        content = build_resume_docx(stored.tailored_resume.text)
    except ResumeDocxError as exc:
        raise ApplicationArtifactRepositoryError(
            "approved tailored resume cannot be exported"
        ) from exc
    return ApprovedResumeDocx(
        content=content,
        filename=safe_resume_filename(
            company_name=posting.company_name,
            role_title=posting.title,
            revision_number=revision.revision_number,
        ),
        artifact_revision_id=revision.id,
        content_hash=stored.tailored_resume.content_hash,
    )


def create_application_artifact_revision(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    pack_id: str,
    payload: ApplicationArtifactRevisionCreate,
    expected_pack_version: int,
    idempotency_key: str,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> ApplicationArtifactsResponse | None:
    """Append one deterministic revision without invoking a provider."""

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
        namespace=f"application_artifact.revision:{pack.id}",
        idempotency_key=idempotency_key,
        request={
            "payload": payload.model_dump(mode="json"),
            "expected_pack_version": expected_pack_version,
        },
        now=current,
    )
    if claim.replay is not None:
        _require_replay_type(claim.replay.resource_type, "application_artifact_revision")
        replayed = session.scalar(
            select(ApplicationArtifactRevision).where(
                ApplicationArtifactRevision.owner_id == owner_id,
                ApplicationArtifactRevision.application_pack_id == pack.id,
                ApplicationArtifactRevision.id == claim.replay.resource_id,
            )
        )
        if replayed is None:
            raise ApplicationArtifactRepositoryError("artifact revision replay is unavailable")
        return _artifact_response(
            session,
            application=application,
            pack=pack,
            keyring=keyring,
        )

    require_version(
        "application_pack",
        pack.id,
        expected=expected_pack_version,
        actual=pack.version,
    )
    _require_mutable_application(application)
    _require_open_posting(session, application)
    grounding = _latest_grounding_revision(session, pack, for_update=True)
    if grounding is None or grounding.id != payload.grounding_revision_id:
        raise ResourceConflict("grounding_revision_id must name the current pack revision")
    grounding_event = _review_event(session, pack, grounding)
    if grounding_event is None:
        raise ResourceConflict("the current grounding revision must be reviewed")

    description_source, description, requirements = _load_revision_payload(
        grounding,
        keyring=keyring,
    )
    del description_source
    if not _mapped_evidence_is_current(
        session,
        owner_id=owner_id,
        requirements=requirements,
        keyring=keyring,
        lock=True,
    ):
        raise ResourceConflict("reviewed grounding evidence changed; review it again")

    latest_artifact = _latest_artifact_revision(session, pack, for_update=True)
    if latest_artifact is None:
        if payload.parent_artifact_revision_id is not None:
            raise ResourceConflict("the first artifact revision cannot name a parent")
        revision_number = 1
    else:
        if payload.parent_artifact_revision_id != latest_artifact.id:
            raise ResourceConflict("parent_artifact_revision_id must name the current revision")
        revision_number = latest_artifact.revision_number + 1

    mapped = _mapped_evidence(requirements)
    selected = _select_evidence(mapped, payload.selected_evidence_refs)
    questions = _validate_question_evidence(payload.questions, mapped)
    base = load_resume_version(
        session,
        owner_id=owner_id,
        resume_version_id=pack.base_resume_version_id,
        keyring=keyring,
    )
    if base is None:
        raise ApplicationArtifactRepositoryError("pinned base resume is unavailable")
    posting = _posting_version(session, application)
    private_payload = _generate_payload(
        grounding=grounding,
        grounding_event=grounding_event,
        description=description,
        requirements=requirements,
        posting=posting,
        base_content=base.content,
        selected=selected,
        mapped=mapped,
        questions=questions,
    )
    revision_id = uuid4().hex
    envelope = encrypt_private_payload(
        keyring,
        record_kind="application_artifact_revision",
        owner_id=owner_id,
        record_id=revision_id,
        payload=private_payload,
    )
    revision = ApplicationArtifactRevision(
        id=revision_id,
        owner_id=owner_id,
        application_id=application.id,
        application_pack_id=pack.id,
        grounding_revision_id=grounding.id,
        parent_artifact_revision_id=payload.parent_artifact_revision_id,
        revision_number=revision_number,
        source="deterministic",
        generator_version=APPLICATION_ARTIFACT_GENERATOR_VERSION,
        encrypted_payload=envelope.ciphertext,
        encryption_key_id=envelope.key_id,
        content_hash=_private_content_hash(owner_id, private_payload),
        created_at=current,
    )
    session.add(revision)
    pack.version += 1
    pack.updated_at = current
    session.flush()
    complete_owner_mutation(
        session,
        owner_id=owner_id,
        receipt_id=claim.receipt_id,
        resource_type="application_artifact_revision",
        resource_id=revision.id,
        result_version=pack.version,
        now=current,
    )
    return _artifact_response(
        session,
        application=application,
        pack=pack,
        keyring=keyring,
    )


def record_application_artifact_event(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    pack_id: str,
    payload: ApplicationArtifactEventCreate,
    expected_pack_version: int,
    idempotency_key: str,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> ApplicationArtifactsResponse | None:
    """Approve or reject one exact current artifact revision atomically."""

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
        namespace=f"application_artifact.event:{pack.id}",
        idempotency_key=idempotency_key,
        request={
            "payload": payload.model_dump(mode="json"),
            "expected_pack_version": expected_pack_version,
        },
        now=current,
    )
    if claim.replay is not None:
        _require_replay_type(claim.replay.resource_type, "application_artifact_event")
        replayed = session.scalar(
            select(ApplicationArtifactEvent).where(
                ApplicationArtifactEvent.owner_id == owner_id,
                ApplicationArtifactEvent.application_pack_id == pack.id,
                ApplicationArtifactEvent.id == claim.replay.resource_id,
            )
        )
        if replayed is None:
            raise ApplicationArtifactRepositoryError("artifact event replay is unavailable")
        return _artifact_response(
            session,
            application=application,
            pack=pack,
            keyring=keyring,
        )

    require_version(
        "application_pack",
        pack.id,
        expected=expected_pack_version,
        actual=pack.version,
    )
    _require_mutable_application(application)
    _require_open_posting(session, application)
    revision = _latest_artifact_revision(session, pack, for_update=True)
    if revision is None or revision.id != payload.artifact_revision_id:
        raise ResourceConflict("artifact_revision_id must name the current revision")
    if _artifact_event(session, pack, revision) is not None:
        raise ResourceConflict("this artifact revision already has a terminal event")

    grounding = _latest_grounding_revision(session, pack, for_update=True)
    if grounding is None or grounding.id != revision.grounding_revision_id:
        raise ResourceConflict("review the current grounding revision before continuing")
    grounding_event = _review_event(session, pack, grounding)
    if grounding_event is None:
        raise ResourceConflict("the artifact grounding revision is not reviewed")
    _, description, requirements = _load_revision_payload(grounding, keyring=keyring)
    if not _mapped_evidence_is_current(
        session,
        owner_id=owner_id,
        requirements=requirements,
        keyring=keyring,
        lock=True,
    ):
        raise ResourceConflict("reviewed grounding evidence changed; review it again")

    stored = _load_artifact_payload(revision, keyring=keyring)
    base = load_resume_version(
        session,
        owner_id=owner_id,
        resume_version_id=pack.base_resume_version_id,
        keyring=keyring,
    )
    if base is None:
        raise ApplicationArtifactRepositoryError("pinned base resume is unavailable")
    posting = _posting_version(session, application)
    _validate_stored_provenance(
        stored,
        grounding=grounding,
        grounding_event=grounding_event,
        description=description,
        requirements=requirements,
        posting=posting,
        base_content=base.content,
    )
    tailored_resume_version_id: str | None = None
    if payload.event_type == "approved":
        if any(
            item.status is ApplicationArtifactAnswerStatus.needs_owner_input
            for item in stored.answers
        ):
            raise ResourceConflict("answer every application question before approval")
        if not stored.selected_evidence:
            raise ResourceConflict("select grounded evidence before approval")
        if stored.diff.base_content_hash == stored.diff.tailored_content_hash:
            raise ResourceConflict("the tailored resume must differ from its base")
        label = _tailored_resume_label(posting)
        result = create_or_reuse_resume_version(
            session,
            owner_id=owner_id,
            label=label,
            content=stored.tailored_resume.text,
            source="edited",
            keyring=keyring,
            parent_id=pack.base_resume_version_id,
            make_base=False,
            now=current,
        )
        if result.resume.parent_id != pack.base_resume_version_id:
            raise ResourceConflict("identical resume content has a different parent")
        if result.resume.is_base:
            raise ApplicationArtifactRepositoryError("tailored resume became the base resume")
        tailored_resume_version_id = result.resume.id

    sequence_number = int(
        session.scalar(
            select(func.max(ApplicationArtifactEvent.sequence_number)).where(
                ApplicationArtifactEvent.owner_id == owner_id,
                ApplicationArtifactEvent.application_pack_id == pack.id,
            )
        )
        or 0
    ) + 1
    event = ApplicationArtifactEvent(
        id=uuid4().hex,
        owner_id=owner_id,
        application_id=application.id,
        application_pack_id=pack.id,
        artifact_revision_id=revision.id,
        sequence_number=sequence_number,
        event_type=payload.event_type,
        tailored_resume_version_id=tailored_resume_version_id,
        occurred_at=current,
        idempotency_key_hash=_sha256(idempotency_key.strip()),
        created_at=current,
    )
    session.add(event)
    pack.version += 1
    pack.updated_at = current
    session.flush()
    complete_owner_mutation(
        session,
        owner_id=owner_id,
        receipt_id=claim.receipt_id,
        resource_type="application_artifact_event",
        resource_id=event.id,
        result_version=pack.version,
        now=current,
    )
    return _artifact_response(
        session,
        application=application,
        pack=pack,
        keyring=keyring,
    )


def _artifact_response(
    session: Session,
    *,
    application: Application,
    pack: ApplicationPack | None,
    keyring: DataKeyring,
) -> ApplicationArtifactsResponse:
    posting = session.scalar(
        select(JobPosting).where(
            JobPosting.owner_id == application.owner_id,
            JobPosting.id == application.job_posting_id,
        )
    )
    if posting is None:
        raise ApplicationArtifactRepositoryError("application posting is unavailable")
    if pack is None:
        blockers = [ApplicationArtifactBlocker.application_pack_missing]
        if posting.lifecycle_state != "open":
            blockers.append(ApplicationArtifactBlocker.posting_closed)
        return ApplicationArtifactsResponse(
            application_id=application.id,
            status=ApplicationArtifactStatus.not_started,
            blockers=blockers,
        )

    grounding = _latest_grounding_revision(session, pack)
    grounding_event = _review_event(session, pack, grounding) if grounding is not None else None
    source_catalog: ApplicationArtifactSourceCatalog | None = None
    grounding_requirements: list[ApplicationPackRequirementResponse] = []
    grounding_current = False
    if grounding is not None and grounding_event is not None:
        _, _, grounding_requirements = _load_revision_payload(grounding, keyring=keyring)
        grounding_current = _mapped_evidence_is_current(
            session,
            owner_id=application.owner_id,
            requirements=grounding_requirements,
            keyring=keyring,
        )
        source_catalog = _source_catalog(
            grounding,
            grounding_event,
            grounding_requirements,
        )

    current = _latest_artifact_revision(session, pack)
    current_response = (
        _revision_response(current, keyring=keyring) if current is not None else None
    )
    current_event = _artifact_event(session, pack, current) if current is not None else None
    current_event_response = _event_response(current_event) if current_event is not None else None
    approval_event = session.scalar(
        select(ApplicationArtifactEvent)
        .where(
            ApplicationArtifactEvent.owner_id == application.owner_id,
            ApplicationArtifactEvent.application_pack_id == pack.id,
            ApplicationArtifactEvent.event_type == "approved",
        )
        .order_by(
            ApplicationArtifactEvent.sequence_number.desc(),
            ApplicationArtifactEvent.id.desc(),
        )
        .limit(1)
    )
    approved_revision = (
        session.scalar(
            select(ApplicationArtifactRevision).where(
                ApplicationArtifactRevision.owner_id == application.owner_id,
                ApplicationArtifactRevision.application_pack_id == pack.id,
                ApplicationArtifactRevision.id == approval_event.artifact_revision_id,
            )
        )
        if approval_event is not None
        else None
    )
    approved_response = (
        _revision_response(approved_revision, keyring=keyring)
        if approved_revision is not None
        else None
    )
    resume_summary = (
        _resume_summary(
            session,
            owner_id=application.owner_id,
            resume_id=approval_event.tailored_resume_version_id,
            keyring=keyring,
        )
        if approval_event is not None and approval_event.tailored_resume_version_id is not None
        else None
    )

    status = ApplicationArtifactStatus.not_started
    if current is not None:
        status = (
            ApplicationArtifactStatus.approved
            if current_event is not None and current_event.event_type == "approved"
            else ApplicationArtifactStatus.draft
        )
    blockers: list[ApplicationArtifactBlocker] = []
    if grounding is None or grounding_event is None:
        blockers.append(ApplicationArtifactBlocker.grounding_review_required)
    elif not grounding_current:
        blockers.append(ApplicationArtifactBlocker.grounding_evidence_changed)
    if posting.lifecycle_state != "open":
        blockers.append(ApplicationArtifactBlocker.posting_closed)
    if current_response is not None:
        if not current_response.selected_evidence:
            blockers.append(ApplicationArtifactBlocker.grounded_evidence_missing)
        if any(
            item.status is ApplicationArtifactAnswerStatus.needs_owner_input
            for item in current_response.answers
        ):
            blockers.append(ApplicationArtifactBlocker.questions_need_owner_input)
        if (
            current_response.diff.base_content_hash
            == current_response.diff.tailored_content_hash
        ):
            blockers.append(ApplicationArtifactBlocker.tailored_resume_unchanged)
        if current_event is not None and current_event.event_type == "rejected":
            blockers.append(ApplicationArtifactBlocker.current_revision_rejected)
    return ApplicationArtifactsResponse(
        application_id=application.id,
        status=status,
        pack=_pack_summary(pack),
        source_catalog=source_catalog,
        current_revision=current_response,
        current_event=current_event_response,
        approved_revision=approved_response,
        approval_event=_event_response(approval_event) if approval_event is not None else None,
        tailored_resume_version=resume_summary,
        blockers=blockers,
    )


def _generate_payload(
    *,
    grounding: ApplicationPackRevision,
    grounding_event: ApplicationPackEvent,
    description: str,
    requirements: list[ApplicationPackRequirementResponse],
    posting: JobPostingVersion,
    base_content: str,
    selected: list[ApplicationPackEvidenceSnapshot],
    mapped: list[ApplicationPackEvidenceSnapshot],
    questions: list,
) -> dict[str, object]:
    tailored_resume = _tailored_resume(base_content, selected)
    company_note = _company_note(
        posting=posting,
        grounding=grounding,
        description=description,
        requirements=requirements,
        selected=selected,
    )
    answers = [
        _answer_question(question, mapped=mapped)
        for question in questions
    ]
    diff = _line_diff(base_content, tailored_resume.text)
    return {
        "schema_version": APPLICATION_ARTIFACT_SCHEMA_VERSION,
        "grounding_review_event_id": grounding_event.id,
        "selected_evidence": [item.model_dump(mode="json") for item in selected],
        "questions": [item.model_dump(mode="json") for item in questions],
        "tailored_resume": tailored_resume.model_dump(mode="json"),
        "company_note": company_note.model_dump(mode="json"),
        "answers": [item.model_dump(mode="json") for item in answers],
        "diff": diff.model_dump(mode="json"),
    }


def _validate_stored_provenance(
    stored: ApplicationArtifactRevisionResponse,
    *,
    grounding: ApplicationPackRevision,
    grounding_event: ApplicationPackEvent,
    description: str,
    requirements: list[ApplicationPackRequirementResponse],
    posting: JobPostingVersion,
    base_content: str,
) -> None:
    if stored.grounding_revision_id != grounding.id:
        raise ApplicationArtifactRepositoryError("artifact grounding revision is invalid")
    if stored.grounding_review_event_id != grounding_event.id:
        raise ResourceConflict("artifact does not name the current grounding review event")
    mapped = {item.id: item for item in _mapped_evidence(requirements)}
    for evidence in stored.selected_evidence:
        expected = mapped.get(evidence.id)
        if expected is None or expected != evidence:
            raise ResourceConflict("artifact selected evidence is not an exact reviewed snapshot")
    for question in stored.questions:
        for reference in question.evidence_refs:
            expected = mapped.get(reference.id)
            if expected is None or expected.version != reference.version:
                raise ResourceConflict(
                    "artifact question evidence is not an exact reviewed snapshot"
                )
    for question, answer in zip(stored.questions, stored.answers, strict=True):
        if question.character_limit is not None and len(answer.text) > question.character_limit:
            raise ResourceConflict("artifact answer exceeds its exact character limit")
    if stored.diff.base_content_hash != _sha256(base_content):
        raise ResourceConflict("artifact diff does not name the pinned base resume")

    documents = [stored.tailored_resume, stored.company_note, *stored.answers]
    for document in documents:
        for claim in document.claims:
            exact_source = False
            for source in claim.sources:
                if isinstance(source, ApplicationArtifactEvidenceClaimSource):
                    evidence = mapped.get(source.evidence_id)
                    if (
                        evidence is None
                        or evidence.version != source.evidence_version
                        or evidence.statement != source.quote
                    ):
                        raise ResourceConflict(
                            "artifact claim evidence is not an exact reviewed snapshot"
                        )
                    exact_source = exact_source or claim.text == source.quote
                elif isinstance(source, ApplicationArtifactJobDescriptionClaimSource):
                    if (
                        source.grounding_revision_id != grounding.id
                        or description[source.source_start : source.source_end] != source.quote
                    ):
                        raise ResourceConflict("artifact claim job-description span is invalid")
                    exact_source = exact_source or claim.text == source.quote
                elif isinstance(source, ApplicationArtifactPostingFieldClaimSource):
                    expected_value = getattr(posting, source.field)
                    if (
                        source.posting_version_id != posting.id
                        or source.value != expected_value
                    ):
                        raise ResourceConflict("artifact claim posting source is invalid")
                    exact_source = exact_source or claim.text == source.value
                else:  # pragma: no cover - strict schema union rejects this first.
                    raise ApplicationArtifactRepositoryError("artifact claim source is invalid")
            if not exact_source:
                raise ResourceConflict("deterministic artifact claims must copy an exact source")


def _tailored_resume(
    base_content: str,
    selected: list[ApplicationPackEvidenceSnapshot],
) -> ApplicationArtifactDocument:
    if not selected:
        return _document(base_content, [])
    builder = _DocumentBuilder("resume")
    header_boundary = _resume_header_boundary(base_content)
    if header_boundary is None:
        builder.append(base_content)
        builder.append("\n" if base_content.endswith("\n") else "\n\n")
    else:
        builder.append(base_content[:header_boundary])
    builder.append("RELEVANT HIGHLIGHTS\n")
    for evidence in selected:
        builder.append("- ")
        builder.claim(evidence.statement, _evidence_source(evidence))
        builder.append("\n")
    if header_boundary is not None:
        builder.append("\n")
        builder.append(base_content[header_boundary:])
    if len(builder.text) > MAX_RESUME_CHARS:
        raise ValueError("grounded highlights exceed the supported resume size")
    return builder.document()


def _resume_header_boundary(base_content: str) -> int | None:
    """Return the end of the first blank line after a resume header."""

    boundary = re.search(r"\n[ \t]*\n", base_content)
    return boundary.end() if boundary is not None else None


def _company_note(
    *,
    posting: JobPostingVersion,
    grounding: ApplicationPackRevision,
    description: str,
    requirements: list[ApplicationPackRequirementResponse],
    selected: list[ApplicationPackEvidenceSnapshot],
) -> ApplicationArtifactDocument:
    builder = _DocumentBuilder("note")
    builder.append("Application note for ")
    builder.claim(
        posting.title,
        ApplicationArtifactPostingFieldClaimSource(
            posting_version_id=posting.id,
            field="title",
            value=posting.title,
        ),
    )
    builder.append(" at ")
    builder.claim(
        posting.company_name,
        ApplicationArtifactPostingFieldClaimSource(
            posting_version_id=posting.id,
            field="company_name",
            value=posting.company_name,
        ),
    )
    builder.append(".\n")
    source_requirement = next(
        (
            item
            for item in requirements
            if item.coverage
            in {
                ApplicationPackRequirementCoverage.supported,
                ApplicationPackRequirementCoverage.partial,
            }
        ),
        requirements[0] if requirements else None,
    )
    if source_requirement is not None:
        if description[
            source_requirement.source_start : source_requirement.source_end
        ] != source_requirement.text:
            raise ApplicationArtifactRepositoryError("grounding source span is invalid")
        builder.append("The role emphasizes: ")
        builder.claim(
            source_requirement.text,
            ApplicationArtifactJobDescriptionClaimSource(
                grounding_revision_id=grounding.id,
                source_start=source_requirement.source_start,
                source_end=source_requirement.source_end,
                quote=source_requirement.text,
            ),
        )
        builder.append("\n")
    if selected:
        builder.append("Relevant evidence:\n")
        for evidence in selected:
            if (
                len(builder.text) + len(evidence.statement) + len("- \n")
                > MAX_APPLICATION_ARTIFACT_NOTE_CHARS
            ):
                break
            builder.append("- ")
            builder.claim(evidence.statement, _evidence_source(evidence))
            builder.append("\n")
    return builder.document()


def _answer_question(question, *, mapped: list[ApplicationPackEvidenceSnapshot]):
    if question.evidence_refs:
        by_id = {item.id: item for item in mapped}
        selected = [by_id[item.id] for item in question.evidence_refs]
    else:
        selected = _question_evidence(question.text, mapped)
    if not selected:
        return ApplicationArtifactAnswer(
            id=_stable_id("answer", question.id),
            question_id=question.id,
            status=ApplicationArtifactAnswerStatus.needs_owner_input,
            text="",
            content_hash=_sha256(""),
            claims=[],
        )
    builder = _DocumentBuilder(f"answer_{question.id[:10]}")
    builder.append("Relevant experience: ")
    for index, evidence in enumerate(selected):
        if index:
            builder.append(" ")
        builder.claim(evidence.statement, _evidence_source(evidence))
    document = builder.document()
    limit = min(question.character_limit or 4_000, 4_000)
    if len(document.text) > limit:
        return ApplicationArtifactAnswer(
            id=_stable_id("answer", question.id),
            question_id=question.id,
            status=ApplicationArtifactAnswerStatus.needs_owner_input,
            text="",
            content_hash=_sha256(""),
            claims=[],
        )
    return ApplicationArtifactAnswer(
        id=_stable_id("answer", question.id),
        question_id=question.id,
        status=ApplicationArtifactAnswerStatus.answered,
        **document.model_dump(),
    )


def _line_diff(base: str, tailored: str) -> ApplicationArtifactDiff:
    base_lines = base.splitlines(keepends=True)
    tailored_lines = tailored.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(a=base_lines, b=tailored_lines, autojunk=False)
    lines: list[ApplicationArtifactDiffLine] = []
    for operation, i1, i2, j1, j2 in matcher.get_opcodes():
        if operation == "equal":
            for offset, text in enumerate(base_lines[i1:i2]):
                lines.append(
                    ApplicationArtifactDiffLine(
                        operation="equal",
                        text=text,
                        base_line_number=i1 + offset + 1,
                        tailored_line_number=j1 + offset + 1,
                    )
                )
        if operation in {"delete", "replace"}:
            for offset, text in enumerate(base_lines[i1:i2]):
                lines.append(
                    ApplicationArtifactDiffLine(
                        operation="delete",
                        text=text,
                        base_line_number=i1 + offset + 1,
                    )
                )
        if operation in {"insert", "replace"}:
            for offset, text in enumerate(tailored_lines[j1:j2]):
                lines.append(
                    ApplicationArtifactDiffLine(
                        operation="insert",
                        text=text,
                        tailored_line_number=j1 + offset + 1,
                    )
                )
    return ApplicationArtifactDiff(
        base_content_hash=_sha256(base),
        tailored_content_hash=_sha256(tailored),
        lines=lines,
    )


class _DocumentBuilder:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.parts: list[str] = []
        self.claims: list[ApplicationArtifactClaim] = []
        self.length = 0

    @property
    def text(self) -> str:
        return "".join(self.parts)

    def append(self, value: str) -> None:
        self.parts.append(value)
        self.length += len(value)

    def claim(self, value: str, source) -> None:
        start = self.length
        self.append(value)
        self.claims.append(
            ApplicationArtifactClaim(
                id=_stable_id(self.prefix, f"{len(self.claims)}:{start}:{value}"),
                start=start,
                end=self.length,
                text=value,
                sources=[source],
            )
        )

    def document(self) -> ApplicationArtifactDocument:
        return _document(self.text, self.claims)


def _document(
    text: str,
    claims: list[ApplicationArtifactClaim],
) -> ApplicationArtifactDocument:
    return ApplicationArtifactDocument(
        text=text,
        content_hash=_sha256(text),
        claims=claims,
    )


def _source_catalog(
    grounding: ApplicationPackRevision,
    event: ApplicationPackEvent,
    requirements: list[ApplicationPackRequirementResponse],
) -> ApplicationArtifactSourceCatalog:
    return ApplicationArtifactSourceCatalog(
        reviewed_grounding_revision_id=grounding.id,
        reviewed_grounding_revision_number=grounding.revision_number,
        reviewed_grounding_event_id=event.id,
        evidence=_mapped_evidence(requirements),
        unsupported_requirements=[
            item
            for item in requirements
            if item.coverage is ApplicationPackRequirementCoverage.unsupported
        ],
    )


def _mapped_evidence(
    requirements: list[ApplicationPackRequirementResponse],
) -> list[ApplicationPackEvidenceSnapshot]:
    ordered_requirements = sorted(
        requirements,
        key=lambda item: (
            0
            if item.importance is ApplicationPackRequirementImportance.required
            else 1,
            item.ordinal,
        ),
    )
    results: list[ApplicationPackEvidenceSnapshot] = []
    seen: set[str] = set()
    for requirement in ordered_requirements:
        if requirement.coverage not in {
            ApplicationPackRequirementCoverage.supported,
            ApplicationPackRequirementCoverage.partial,
        }:
            continue
        for evidence in requirement.evidence:
            if evidence.id not in seen:
                results.append(evidence)
                seen.add(evidence.id)
    return results


def _select_evidence(
    mapped: list[ApplicationPackEvidenceSnapshot],
    requested,
) -> list[ApplicationPackEvidenceSnapshot]:
    if requested is None:
        return mapped[:5]
    by_id = {item.id: item for item in mapped}
    selected: list[ApplicationPackEvidenceSnapshot] = []
    for reference in requested:
        evidence = by_id.get(reference.id)
        if evidence is None or evidence.version != reference.version:
            raise ValueError("selected_evidence_refs must name exact reviewed snapshots")
        selected.append(evidence)
    return selected


def _validate_question_evidence(questions, mapped):
    by_id = {item.id: item for item in mapped}
    for question in questions:
        for reference in question.evidence_refs:
            evidence = by_id.get(reference.id)
            if evidence is None or evidence.version != reference.version:
                raise ValueError("question evidence_refs must name exact reviewed snapshots")
    return questions


def _question_evidence(
    question: str,
    mapped: list[ApplicationPackEvidenceSnapshot],
) -> list[ApplicationPackEvidenceSnapshot]:
    question_tokens = _tokens(question)
    ranked: list[tuple[int, int, ApplicationPackEvidenceSnapshot]] = []
    for index, evidence in enumerate(mapped):
        candidate = _tokens(" ".join([evidence.statement, *evidence.skills]))
        overlap = len(question_tokens & candidate)
        if overlap:
            ranked.append((overlap, index, evidence))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2].id))
    return [ranked[0][2]] if ranked else []


def _evidence_source(
    evidence: ApplicationPackEvidenceSnapshot,
) -> ApplicationArtifactEvidenceClaimSource:
    return ApplicationArtifactEvidenceClaimSource(
        evidence_id=evidence.id,
        evidence_version=evidence.version,
        quote=evidence.statement,
    )


def _load_artifact_payload(
    revision: ApplicationArtifactRevision,
    *,
    keyring: DataKeyring,
) -> ApplicationArtifactRevisionResponse:
    private = decrypt_private_payload(
        keyring,
        record_kind="application_artifact_revision",
        owner_id=revision.owner_id,
        record_id=revision.id,
        encryption_key_id=revision.encryption_key_id,
        ciphertext=revision.encrypted_payload,
    )
    if _private_content_hash(revision.owner_id, private) != revision.content_hash:
        raise ApplicationArtifactRepositoryError("artifact content hash is invalid")
    if set(private) != {
        "schema_version",
        "grounding_review_event_id",
        "selected_evidence",
        "questions",
        "tailored_resume",
        "company_note",
        "answers",
        "diff",
    } or private.get("schema_version") != APPLICATION_ARTIFACT_SCHEMA_VERSION:
        raise ApplicationArtifactRepositoryError("artifact private payload shape is invalid")
    try:
        return ApplicationArtifactRevisionResponse(
            id=revision.id,
            application_pack_id=revision.application_pack_id,
            grounding_revision_id=revision.grounding_revision_id,
            grounding_review_event_id=private["grounding_review_event_id"],
            parent_artifact_revision_id=revision.parent_artifact_revision_id,
            revision_number=revision.revision_number,
            source=revision.source,
            generator_version=revision.generator_version,
            selected_evidence=private["selected_evidence"],
            questions=private["questions"],
            tailored_resume=private["tailored_resume"],
            company_note=private["company_note"],
            answers=private["answers"],
            diff=private["diff"],
            created_at=_as_utc(revision.created_at),
        )
    except (TypeError, ValueError) as exc:
        raise ApplicationArtifactRepositoryError("artifact private payload is invalid") from exc


def _revision_response(
    revision: ApplicationArtifactRevision,
    *,
    keyring: DataKeyring,
) -> ApplicationArtifactRevisionResponse:
    return _load_artifact_payload(revision, keyring=keyring)


def _event_response(event: ApplicationArtifactEvent) -> ApplicationArtifactEventResponse:
    return ApplicationArtifactEventResponse(
        id=event.id,
        application_pack_id=event.application_pack_id,
        artifact_revision_id=event.artifact_revision_id,
        sequence_number=event.sequence_number,
        event_type=event.event_type,
        tailored_resume_version_id=event.tailored_resume_version_id,
        occurred_at=_as_utc(event.occurred_at),
    )


def _resume_summary(
    session: Session,
    *,
    owner_id: str,
    resume_id: str,
    keyring: DataKeyring,
) -> ResumeVersionSummary:
    detail = load_resume_version(
        session,
        owner_id=owner_id,
        resume_version_id=resume_id,
        keyring=keyring,
    )
    if detail is None:
        raise ApplicationArtifactRepositoryError("approved tailored resume is unavailable")
    metadata = detail.metadata
    return ResumeVersionSummary(
        id=metadata.id,
        label=metadata.label,
        source=metadata.source,
        parent_resume_version_id=metadata.parent_id,
        is_base=metadata.is_base,
        character_count=len(detail.content),
        version=metadata.version,
        created_at=_as_utc(metadata.created_at),
        updated_at=_as_utc(metadata.updated_at),
    )


def _owned_application(
    session: Session,
    owner_id: str,
    application_id: str,
) -> Application | None:
    return session.scalar(
        select(Application).where(
            Application.owner_id == owner_id,
            Application.id == application_id,
        )
    )


def _owned_pack(
    session: Session,
    owner_id: str,
    application_id: str,
    *,
    for_update: bool = False,
) -> ApplicationPack | None:
    statement = select(ApplicationPack).where(
        ApplicationPack.owner_id == owner_id,
        ApplicationPack.application_id == application_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return session.scalar(statement)


def _owned_pack_by_id(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    pack_id: str,
    for_update: bool = False,
) -> ApplicationPack | None:
    statement = select(ApplicationPack).where(
        ApplicationPack.owner_id == owner_id,
        ApplicationPack.application_id == application_id,
        ApplicationPack.id == pack_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return session.scalar(statement)


def _latest_grounding_revision(
    session: Session,
    pack: ApplicationPack,
    *,
    for_update: bool = False,
) -> ApplicationPackRevision | None:
    statement = (
        select(ApplicationPackRevision)
        .where(
            ApplicationPackRevision.owner_id == pack.owner_id,
            ApplicationPackRevision.application_pack_id == pack.id,
        )
        .order_by(ApplicationPackRevision.revision_number.desc())
        .limit(1)
    )
    if for_update:
        statement = statement.with_for_update()
    return session.scalar(statement)


def _review_event(
    session: Session,
    pack: ApplicationPack,
    revision: ApplicationPackRevision,
) -> ApplicationPackEvent | None:
    return session.scalar(
        select(ApplicationPackEvent).where(
            ApplicationPackEvent.owner_id == pack.owner_id,
            ApplicationPackEvent.application_pack_id == pack.id,
            ApplicationPackEvent.revision_id == revision.id,
            ApplicationPackEvent.event_type == "reviewed",
        )
    )


def _latest_artifact_revision(
    session: Session,
    pack: ApplicationPack,
    *,
    for_update: bool = False,
) -> ApplicationArtifactRevision | None:
    statement = (
        select(ApplicationArtifactRevision)
        .where(
            ApplicationArtifactRevision.owner_id == pack.owner_id,
            ApplicationArtifactRevision.application_pack_id == pack.id,
        )
        .order_by(ApplicationArtifactRevision.revision_number.desc())
        .limit(1)
    )
    if for_update:
        statement = statement.with_for_update()
    return session.scalar(statement)


def _artifact_event(
    session: Session,
    pack: ApplicationPack,
    revision: ApplicationArtifactRevision,
) -> ApplicationArtifactEvent | None:
    return session.scalar(
        select(ApplicationArtifactEvent).where(
            ApplicationArtifactEvent.owner_id == pack.owner_id,
            ApplicationArtifactEvent.application_pack_id == pack.id,
            ApplicationArtifactEvent.artifact_revision_id == revision.id,
        )
    )


def _posting_version(session: Session, application: Application) -> JobPostingVersion:
    posting = session.scalar(
        select(JobPostingVersion).where(
            JobPostingVersion.owner_id == application.owner_id,
            JobPostingVersion.job_posting_id == application.job_posting_id,
            JobPostingVersion.id == application.pursued_posting_version_id,
        )
    )
    if posting is None:
        raise ApplicationArtifactRepositoryError("pinned posting version is unavailable")
    return posting


def _require_open_posting(session: Session, application: Application) -> None:
    posting = session.scalar(
        select(JobPosting).where(
            JobPosting.owner_id == application.owner_id,
            JobPosting.id == application.job_posting_id,
        )
    )
    if posting is None:
        raise ApplicationArtifactRepositoryError("application posting is unavailable")
    if posting.lifecycle_state != "open":
        raise ResourceConflict("closed postings do not accept artifact mutations")


def _require_mutable_application(application: Application) -> None:
    if application.stage != "pursuing":
        raise ResourceConflict("application stage does not accept artifact mutations")


def _tailored_resume_label(posting: JobPostingVersion) -> str:
    value = " ".join(f"{posting.company_name} {posting.title} tailored".split())
    return value[:120]


def _private_content_hash(owner_id: str, payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return _sha256(f"{owner_id}\0{canonical}")


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix[:8]}_{_sha256(value)[:20]}"


def _tokens(value: str) -> set[str]:
    return {
        item
        for item in _TOKEN_RE.findall(value.casefold())
        if item not in _STOP_TOKENS
    }


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _require_replay_type(actual: str | None, expected: str) -> None:
    if actual != expected:
        raise ApplicationArtifactRepositoryError("artifact mutation replay is unavailable")


__all__ = [
    "ApplicationArtifactRepositoryError",
    "create_application_artifact_revision",
    "load_approved_tailored_resume_docx",
    "load_application_artifacts",
    "record_application_artifact_event",
]
