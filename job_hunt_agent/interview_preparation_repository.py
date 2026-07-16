"""Database-only, evidence-pinned interview preparation persistence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .application_pack_repository import (
    _load_revision_payload,
    _mapped_evidence_is_current,
)
from .application_pack_schemas import ApplicationPackEvidenceSnapshot
from .interview_preparation_schemas import (
    ApplicationInterviewPreparationResponse,
    InterviewPreparationBlocker,
    InterviewPreparationEvidenceGap,
    InterviewPreparationPrompt,
    InterviewPreparationPromptCategory,
    InterviewPreparationRequirement,
    InterviewPreparationRevisionCreate,
    InterviewPreparationRevisionSummary,
    InterviewPreparationRoleContext,
    InterviewPreparationStarDraft,
    InterviewPreparationStatus,
    InterviewPreparationTarget,
    InterviewPreparationTargetKind,
    MAX_PREPARATION_PROMPTS,
)
from .job_queue import utcnow
from .models import (
    Application,
    ApplicationInterviewPreparation,
    ApplicationInterviewPreparationRevision,
    ApplicationInterviewRound,
    ApplicationPackRevision,
    ApplicationSubmission,
    JobPostingVersion,
)
from .mutation_receipts import claim_owner_mutation, complete_owner_mutation
from .private_payloads import decrypt_private_payload, encrypt_private_payload
from .repository_errors import ResourceConflict, require_version
from .security import DataKeyring


_POST_SUBMISSION_STAGES = frozenset({"applied", "screening", "interviewing", "offer"})
_PAYLOAD_SCHEMA_VERSION = 1
_CATEGORY_ORDER = (
    InterviewPreparationPromptCategory.role_motivation,
    InterviewPreparationPromptCategory.key_requirement,
    InterviewPreparationPromptCategory.impact,
    InterviewPreparationPromptCategory.conflict_ambiguity,
    InterviewPreparationPromptCategory.failure_learning,
    InterviewPreparationPromptCategory.leadership_collaboration,
)


class InterviewPreparationRepositoryError(RuntimeError):
    """Persisted preparation data violated an evidence or version invariant."""


def load_application_interview_preparation(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    keyring: DataKeyring,
) -> ApplicationInterviewPreparationResponse | None:
    """Build a deterministic scaffold from the exact submitted application facts."""

    application = _owned_application(session, owner_id, application_id)
    if application is None:
        return None
    preparation = _owned_preparation(session, owner_id, application_id)
    return _projection(
        session,
        application=application,
        preparation=preparation,
        keyring=keyring,
    )


def create_interview_preparation_revision(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    payload: InterviewPreparationRevisionCreate,
    expected_version: int,
    idempotency_key: str,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> ApplicationInterviewPreparationResponse | None:
    """Append exact owner-authored STAR fields without generating factual prose."""

    current = _as_utc(now or utcnow())
    application = _owned_application(session, owner_id, application_id, lock=True)
    if application is None:
        return None
    preparation = _owned_preparation(
        session,
        owner_id,
        application_id,
        lock=True,
    )
    namespace = f"interview_preparation.revision:{application.id}"
    claim = claim_owner_mutation(
        session,
        owner_id=owner_id,
        namespace=namespace,
        idempotency_key=idempotency_key,
        request={
            "payload": payload.model_dump(mode="json"),
            "expected_version": expected_version,
        },
        now=current,
    )
    if claim.replay is not None:
        if claim.replay.resource_type != "interview_preparation_revision":
            raise InterviewPreparationRepositoryError(
                "interview-preparation replay type is inconsistent"
            )
        replayed = session.scalar(
            select(ApplicationInterviewPreparationRevision).where(
                ApplicationInterviewPreparationRevision.owner_id == owner_id,
                ApplicationInterviewPreparationRevision.application_id == application.id,
                ApplicationInterviewPreparationRevision.id == claim.replay.resource_id,
            )
        )
        if replayed is None:
            raise InterviewPreparationRepositoryError(
                "interview-preparation replay is unavailable"
            )
        saved = _owned_preparation(session, owner_id, application.id)
        if saved is None or saved.id != replayed.preparation_id:
            raise InterviewPreparationRepositoryError(
                "interview-preparation replay aggregate is unavailable"
            )
        return _projection(
            session,
            application=application,
            preparation=saved,
            keyring=keyring,
        )

    if preparation is None:
        require_version(
            "application",
            application.id,
            expected=expected_version,
            actual=application.version,
        )
        if payload.parent_revision_id is not None:
            raise ResourceConflict("a new preparation cannot name a parent revision")
    else:
        require_version(
            "interview preparation",
            preparation.id,
            expected=expected_version,
            actual=preparation.version,
        )

    projection = _projection(
        session,
        application=application,
        preparation=preparation,
        keyring=keyring,
    )
    if projection.source_fingerprint is None:
        raise ResourceConflict("complete the submitted application context before preparing")
    if payload.source_fingerprint != projection.source_fingerprint:
        raise ResourceConflict("interview preparation context changed; reload before saving")
    if application.stage == "closed":
        raise ResourceConflict("closed applications cannot update interview preparation")
    blocked_for_write = {
        InterviewPreparationBlocker.application_not_submitted,
        InterviewPreparationBlocker.reviewed_application_pack_missing,
        InterviewPreparationBlocker.approved_evidence_missing,
        InterviewPreparationBlocker.evidence_snapshot_changed,
        InterviewPreparationBlocker.required_requirement_evidence_missing,
        InterviewPreparationBlocker.required_prompt_capacity_exceeded,
    }
    if blocked_for_write.intersection(projection.blockers):
        raise ResourceConflict(
            "resolve the interview-preparation blockers before saving"
        )
    if not projection.prompts:
        raise ResourceConflict("no evidence-backed story prompts are available")
    submission = _submission(session, application)
    if submission is None:
        raise InterviewPreparationRepositoryError("submitted application has no submission")
    grounding = session.scalar(
        select(ApplicationPackRevision).where(
            ApplicationPackRevision.owner_id == owner_id,
            ApplicationPackRevision.application_id == application.id,
            ApplicationPackRevision.application_pack_id
            == submission.application_pack_id,
            ApplicationPackRevision.id == submission.application_pack_revision_id,
        )
    )
    if grounding is None:
        raise InterviewPreparationRepositoryError(
            "submitted application grounding is unavailable"
        )
    _source, _description, locked_requirements = _load_revision_payload(
        grounding,
        keyring=keyring,
    )
    if not _mapped_evidence_is_current(
        session,
        owner_id=owner_id,
        requirements=locked_requirements,
        keyring=keyring,
        lock=True,
    ):
        raise ResourceConflict(
            "approved evidence changed while preparing; reload before saving"
        )

    prompt_ids = [prompt.id for prompt in projection.prompts]
    payload_ids = [draft.prompt_id for draft in payload.prompt_drafts]
    if payload_ids != prompt_ids:
        raise ResourceConflict("save the complete current prompt set in displayed order")
    latest = _latest_revision(session, preparation) if preparation is not None else None
    if (latest.id if latest is not None else None) != payload.parent_revision_id:
        raise ResourceConflict("parent_revision_id must name the latest saved preparation")

    draft_by_id = {draft.prompt_id: draft for draft in payload.prompt_drafts}
    stored_prompts: list[InterviewPreparationPrompt] = []
    for prompt in projection.prompts:
        draft = draft_by_id[prompt.id]
        star = InterviewPreparationStarDraft(
            situation=draft.situation,
            task=draft.task,
            action=draft.action,
            result=draft.result,
        )
        stored_prompts.append(
            prompt.model_copy(
                update={
                    "draft": star,
                    "missing_sections": _missing_sections(star),
                }
            )
        )

    if preparation is None:
        preparation = ApplicationInterviewPreparation(
            id=uuid4().hex,
            owner_id=owner_id,
            application_id=application.id,
            version=1,
            created_at=current,
            updated_at=current,
        )
        session.add(preparation)
        session.flush()
        revision_number = 1
    else:
        preparation.version += 1
        preparation.updated_at = current
        revision_number = preparation.version

    target_round = _scheduled_round(session, application)
    target_kind = projection.target.kind.value
    private_payload = {
        "schema_version": _PAYLOAD_SCHEMA_VERSION,
        "source_fingerprint": projection.source_fingerprint,
        "pins": {
            "application_submission_id": submission.id,
            "application_pack_id": submission.application_pack_id,
            "grounding_revision_id": submission.application_pack_revision_id,
            "job_posting_id": application.job_posting_id,
            "posting_version_id": application.pursued_posting_version_id,
            "interview_round_id": (
                target_round.id if target_round is not None else None
            ),
            "interview_round_version": (
                target_round.version if target_round is not None else None
            ),
        },
        "role": projection.role.model_dump(mode="json"),
        "target": projection.target.model_dump(mode="json"),
        "requirements": [item.model_dump(mode="json") for item in projection.requirements],
        "prompts": [item.model_dump(mode="json") for item in stored_prompts],
    }
    revision_id = uuid4().hex
    envelope = encrypt_private_payload(
        keyring,
        record_kind="application_interview_preparation_revision",
        owner_id=owner_id,
        record_id=revision_id,
        payload=private_payload,
    )
    revision = ApplicationInterviewPreparationRevision(
        id=revision_id,
        owner_id=owner_id,
        application_id=application.id,
        preparation_id=preparation.id,
        parent_revision_id=latest.id if latest is not None else None,
        revision_number=revision_number,
        application_submission_id=submission.id,
        application_pack_id=submission.application_pack_id,
        grounding_revision_id=submission.application_pack_revision_id,
        job_posting_id=application.job_posting_id,
        posting_version_id=application.pursued_posting_version_id,
        target_kind=target_kind,
        interview_round_id=(target_round.id if target_round is not None else None),
        interview_round_version=(
            target_round.version if target_round is not None else None
        ),
        source_fingerprint=projection.source_fingerprint,
        recording_method="owner_authored",
        encrypted_payload=envelope.ciphertext,
        encryption_key_id=envelope.key_id,
        content_hash=_content_hash(owner_id, private_payload),
        created_at=current,
    )
    session.add(revision)
    session.flush()
    complete_owner_mutation(
        session,
        owner_id=owner_id,
        receipt_id=claim.receipt_id,
        resource_type="interview_preparation_revision",
        resource_id=revision.id,
        result_version=preparation.version,
        now=current,
    )
    return _projection(
        session,
        application=application,
        preparation=preparation,
        keyring=keyring,
    )


def _projection(
    session: Session,
    *,
    application: Application,
    preparation: ApplicationInterviewPreparation | None,
    keyring: DataKeyring,
) -> ApplicationInterviewPreparationResponse:
    posting = session.scalar(
        select(JobPostingVersion).where(
            JobPostingVersion.owner_id == application.owner_id,
            JobPostingVersion.job_posting_id == application.job_posting_id,
            JobPostingVersion.id == application.pursued_posting_version_id,
        )
    )
    if posting is None:
        raise InterviewPreparationRepositoryError(
            "application posting version is unavailable"
        )
    role = InterviewPreparationRoleContext(
        job_posting_id=application.job_posting_id,
        posting_version_id=posting.id,
        company=posting.company_name,
        title=posting.title,
        summary=posting.summary,
    )
    scheduled_round = _scheduled_round(session, application)
    target = (
        InterviewPreparationTarget(
            kind=InterviewPreparationTargetKind.interview_round,
            label=scheduled_round.title,
            interview_round_id=scheduled_round.id,
            interview_round_version=scheduled_round.version,
            interview_round_kind=scheduled_round.kind,
            scheduled_start_at=_as_utc(scheduled_round.scheduled_start_at),
            scheduled_timezone=scheduled_round.scheduled_timezone,
        )
        if scheduled_round is not None
        else InterviewPreparationTarget(
            kind=InterviewPreparationTargetKind.recruiter_screen,
            label="Recruiter screen or next recruiter conversation",
        )
    )
    submission = _submission(session, application)
    blockers: list[InterviewPreparationBlocker] = []
    next_steps: list[str] = []
    if application.stage == "closed":
        blockers.append(InterviewPreparationBlocker.application_closed)
        next_steps.append("This application is closed; keep prior preparation read-only.")
    if application.stage not in _POST_SUBMISSION_STAGES or submission is None:
        blockers.append(InterviewPreparationBlocker.application_not_submitted)
        next_steps.append(
            "Record the exact submitted materials in Application submission first."
        )

    grounding: ApplicationPackRevision | None = None
    raw_requirements = []
    if submission is not None:
        grounding = session.scalar(
            select(ApplicationPackRevision).where(
                ApplicationPackRevision.owner_id == application.owner_id,
                ApplicationPackRevision.application_id == application.id,
                ApplicationPackRevision.application_pack_id
                == submission.application_pack_id,
                ApplicationPackRevision.id == submission.application_pack_revision_id,
            )
        )
        if grounding is None:
            blockers.append(
                InterviewPreparationBlocker.reviewed_application_pack_missing
            )
            next_steps.append(
                "Review role requirements and approved evidence in Application requirements."
            )
        else:
            _source, _description, raw_requirements = _load_revision_payload(
                grounding,
                keyring=keyring,
            )

    requirements = [
        InterviewPreparationRequirement(
            id=item.id,
            ordinal=item.ordinal,
            importance=item.importance.value,
            text=item.text,
            coverage=item.coverage.value,
            evidence=item.evidence,
        )
        for item in raw_requirements
    ]
    evidence = _unique_evidence(requirements)
    required_evidence_backed_count = sum(
        requirement.importance == "required" and bool(requirement.evidence)
        for requirement in requirements
    )
    if required_evidence_backed_count > MAX_PREPARATION_PROMPTS:
        blockers.append(
            InterviewPreparationBlocker.required_prompt_capacity_exceeded
        )
        next_steps.append(
            f"{required_evidence_backed_count} required evidence-backed requirements "
            f"exceed the {MAX_PREPARATION_PROMPTS}-prompt safety limit. Review and "
            "narrow the exact required "
            "requirement set; prompts will not be silently grouped or omitted."
        )
    evidence_current = True
    if raw_requirements:
        evidence_current = _mapped_evidence_is_current(
            session,
            owner_id=application.owner_id,
            requirements=raw_requirements,
            keyring=keyring,
        )
    if grounding is not None and not evidence:
        blockers.append(InterviewPreparationBlocker.approved_evidence_missing)
        next_steps.append(
            "Approve achievement evidence in Profile, then review and map it to role requirements."
        )
    if grounding is not None and not evidence_current:
        blockers.append(InterviewPreparationBlocker.evidence_snapshot_changed)
        next_steps.append(
            "Mapped evidence changed or was retired; review the application evidence before using it."
        )

    gaps: list[InterviewPreparationEvidenceGap] = []
    for requirement in requirements:
        reason: str | None = None
        if not requirement.evidence:
            reason = "no_approved_evidence"
        elif not evidence_current:
            reason = "evidence_changed"
        if reason is not None:
            gaps.append(
                InterviewPreparationEvidenceGap(
                    requirement_id=requirement.id,
                    importance=requirement.importance,
                    requirement_text=requirement.text,
                    reason=reason,
                )
            )
    if any(gap.importance == "required" for gap in gaps):
        blockers.append(
            InterviewPreparationBlocker.required_requirement_evidence_missing
        )
        next_steps.append(
            "Add or approve evidence for every required gap; the tool will not invent an answer."
        )

    source_fingerprint = (
        _source_fingerprint(
            application=application,
            submission=submission,
            grounding=grounding,
            target=target,
            requirements=requirements,
        )
        if submission is not None and grounding is not None
        else None
    )
    latest = _latest_revision(session, preparation) if preparation is not None else None
    saved_drafts: dict[str, InterviewPreparationStarDraft] = {}
    previous_prompts: list[InterviewPreparationPrompt] = []
    if latest is not None and latest.source_fingerprint == source_fingerprint:
        stored_prompts = _load_revision_prompts(latest, keyring=keyring)
        saved_drafts = {prompt.id: prompt.draft for prompt in stored_prompts}
    elif latest is not None:
        previous_prompts = _load_revision_prompts(latest, keyring=keyring)
    prompts = _build_prompts(
        role=role,
        target=target,
        requirements=requirements,
        source_fingerprint=source_fingerprint,
        saved_drafts=saved_drafts,
    )

    unique_blockers = list(dict.fromkeys(blockers))
    if unique_blockers:
        preparation_status = InterviewPreparationStatus.blocked
    elif latest is None or latest.source_fingerprint != source_fingerprint:
        preparation_status = InterviewPreparationStatus.not_started
    elif prompts and all(not prompt.missing_sections for prompt in prompts):
        preparation_status = InterviewPreparationStatus.ready
    else:
        preparation_status = InterviewPreparationStatus.in_progress

    return ApplicationInterviewPreparationResponse(
        application_id=application.id,
        application_version=application.version,
        application_submission_id=submission.id if submission is not None else None,
        preparation_id=preparation.id if preparation is not None else None,
        preparation_version=preparation.version if preparation is not None else None,
        write_version_scope="preparation" if preparation is not None else "application",
        write_version=preparation.version if preparation is not None else application.version,
        status=preparation_status,
        source_fingerprint=source_fingerprint,
        role=role,
        target=target,
        grounding_revision_id=grounding.id if grounding is not None else None,
        latest_revision=_revision_summary(latest) if latest is not None else None,
        requirements=requirements,
        required_evidence_backed_count=required_evidence_backed_count,
        prompt_capacity=MAX_PREPARATION_PROMPTS,
        evidence_gaps=gaps,
        prompts=prompts,
        previous_context_stale=bool(previous_prompts),
        previous_prompts=previous_prompts,
        blockers=unique_blockers,
        next_steps=list(dict.fromkeys(next_steps)),
    )


def _build_prompts(
    *,
    role: InterviewPreparationRoleContext,
    target: InterviewPreparationTarget,
    requirements: list[InterviewPreparationRequirement],
    source_fingerprint: str | None,
    saved_drafts: dict[str, InterviewPreparationStarDraft],
) -> list[InterviewPreparationPrompt]:
    candidates = [item for item in requirements if item.evidence]
    if not candidates or source_fingerprint is None:
        return []
    ordered = sorted(
        candidates,
        key=lambda item: (item.importance != "required", item.ordinal, item.id),
    )
    prompts: list[InterviewPreparationPrompt] = []
    required = [item for item in ordered if item.importance == "required"]
    plan: list[
        tuple[InterviewPreparationPromptCategory, InterviewPreparationRequirement, str]
    ] = [
        (InterviewPreparationPromptCategory.key_requirement, requirement, "required")
        for requirement in required[:MAX_PREPARATION_PROMPTS]
    ]
    optional_categories = [
        category
        for category in _CATEGORY_ORDER
        if category is not InterviewPreparationPromptCategory.key_requirement
    ]
    for index, category in enumerate(optional_categories):
        if len(plan) >= MAX_PREPARATION_PROMPTS:
            break
        plan.append((category, ordered[index % len(ordered)], f"category-{index}"))
    if not required and len(plan) < MAX_PREPARATION_PROMPTS:
        plan.insert(
            0,
            (
                InterviewPreparationPromptCategory.key_requirement,
                ordered[0],
                "key-requirement",
            ),
        )
    for category, requirement, discriminator in plan:
        prompt_id = hashlib.sha256(
            (
                f"{source_fingerprint}\0{category.value}\0{requirement.id}"
                f"\0{discriminator}"
            ).encode("utf-8")
        ).hexdigest()[:32]
        draft = saved_drafts.get(prompt_id, InterviewPreparationStarDraft())
        prompt = InterviewPreparationPrompt(
            id=prompt_id,
            category=category,
            question=_question(
                category,
                company=role.company,
                title=role.title,
                target=target.label,
                requirement=requirement.text,
            ),
            requirement_id=requirement.id,
            requirement_text=requirement.text,
            evidence=requirement.evidence,
            draft=draft,
            missing_sections=_missing_sections(draft),
        )
        prompts.append(prompt)
    return prompts


def _question(
    category: InterviewPreparationPromptCategory,
    *,
    company: str,
    title: str,
    target: str,
    requirement: str,
) -> str:
    if category is InterviewPreparationPromptCategory.role_motivation:
        return (
            f"For {target}, connect your own motivation for the {title} role at "
            f"{company} to a verified example relevant to: {requirement}"
        )
    if category is InterviewPreparationPromptCategory.key_requirement:
        return f"Prepare one concrete example that demonstrates: {requirement}"
    if category is InterviewPreparationPromptCategory.impact:
        return (
            "Use the approved evidence below to explain the outcome you influenced, "
            "how it was measured, and your exact contribution."
        )
    if category is InterviewPreparationPromptCategory.conflict_ambiguity:
        return (
            "Can the approved evidence below support a truthful example of resolving "
            "conflict or ambiguity? Leave it blank if it cannot."
        )
    if category is InterviewPreparationPromptCategory.failure_learning:
        return (
            "Can the approved evidence below support a truthful example of a setback, "
            "what you learned, and what changed? Leave it blank if it cannot."
        )
    return (
        "Use the approved evidence below to show how you led, collaborated, or helped "
        "others succeed without overstating your role."
    )


def _load_revision_prompts(
    revision: ApplicationInterviewPreparationRevision,
    *,
    keyring: DataKeyring,
) -> list[InterviewPreparationPrompt]:
    private = decrypt_private_payload(
        keyring,
        record_kind="application_interview_preparation_revision",
        owner_id=revision.owner_id,
        record_id=revision.id,
        encryption_key_id=revision.encryption_key_id,
        ciphertext=revision.encrypted_payload,
    )
    if _content_hash(revision.owner_id, private) != revision.content_hash:
        raise InterviewPreparationRepositoryError(
            "interview-preparation content hash is invalid"
        )
    if private.get("schema_version") != _PAYLOAD_SCHEMA_VERSION:
        raise InterviewPreparationRepositoryError(
            "interview-preparation payload version is invalid"
        )
    if private.get("source_fingerprint") != revision.source_fingerprint:
        raise InterviewPreparationRepositoryError(
            "interview-preparation source fingerprint is invalid"
        )
    expected_pins = {
        "application_submission_id": revision.application_submission_id,
        "application_pack_id": revision.application_pack_id,
        "grounding_revision_id": revision.grounding_revision_id,
        "job_posting_id": revision.job_posting_id,
        "posting_version_id": revision.posting_version_id,
        "interview_round_id": revision.interview_round_id,
        "interview_round_version": revision.interview_round_version,
    }
    if private.get("pins") != expected_pins:
        raise InterviewPreparationRepositoryError(
            "interview-preparation pinned source references are invalid"
        )
    raw_prompts = private.get("prompts")
    if not isinstance(raw_prompts, list):
        raise InterviewPreparationRepositoryError(
            "interview-preparation prompts are invalid"
        )
    try:
        return [InterviewPreparationPrompt.model_validate(item) for item in raw_prompts]
    except (TypeError, ValueError) as exc:
        raise InterviewPreparationRepositoryError(
            "interview-preparation prompt payload is invalid"
        ) from exc


def _source_fingerprint(
    *,
    application: Application,
    submission: ApplicationSubmission,
    grounding: ApplicationPackRevision,
    target: InterviewPreparationTarget,
    requirements: list[InterviewPreparationRequirement],
) -> str:
    value = {
        "application_id": application.id,
        "submission_id": submission.id,
        "job_posting_id": application.job_posting_id,
        "posting_version_id": application.pursued_posting_version_id,
        "grounding_revision_id": grounding.id,
        "target": target.model_dump(mode="json"),
        "evidence": sorted(
            (evidence.id, evidence.version)
            for requirement in requirements
            for evidence in requirement.evidence
        ),
    }
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _unique_evidence(
    requirements: list[InterviewPreparationRequirement],
) -> list[ApplicationPackEvidenceSnapshot]:
    values: dict[tuple[str, int], ApplicationPackEvidenceSnapshot] = {}
    for requirement in requirements:
        for evidence in requirement.evidence:
            values.setdefault((evidence.id, evidence.version), evidence)
    return [values[key] for key in sorted(values)]


def _missing_sections(
    draft: InterviewPreparationStarDraft,
) -> list[str]:
    return [
        field
        for field in ("situation", "task", "action", "result")
        if not getattr(draft, field).strip()
    ]


def _revision_summary(
    revision: ApplicationInterviewPreparationRevision,
) -> InterviewPreparationRevisionSummary:
    return InterviewPreparationRevisionSummary(
        id=revision.id,
        revision_number=revision.revision_number,
        parent_revision_id=revision.parent_revision_id,
        source_fingerprint=revision.source_fingerprint,
        recording_method="owner_authored",
        created_at=_as_utc(revision.created_at),
    )


def _owned_application(
    session: Session,
    owner_id: str,
    application_id: str,
    *,
    lock: bool = False,
) -> Application | None:
    statement = select(Application).where(
        Application.owner_id == owner_id,
        Application.id == application_id,
    )
    if lock:
        statement = statement.with_for_update()
    return session.scalar(statement)


def _owned_preparation(
    session: Session,
    owner_id: str,
    application_id: str,
    *,
    lock: bool = False,
) -> ApplicationInterviewPreparation | None:
    statement = select(ApplicationInterviewPreparation).where(
        ApplicationInterviewPreparation.owner_id == owner_id,
        ApplicationInterviewPreparation.application_id == application_id,
    )
    if lock:
        statement = statement.with_for_update()
    return session.scalar(statement)


def _submission(
    session: Session,
    application: Application,
) -> ApplicationSubmission | None:
    return session.scalar(
        select(ApplicationSubmission).where(
            ApplicationSubmission.owner_id == application.owner_id,
            ApplicationSubmission.application_id == application.id,
        )
    )


def _scheduled_round(
    session: Session,
    application: Application,
) -> ApplicationInterviewRound | None:
    """Choose the earliest appointment deterministically if storage is malformed."""

    return session.scalar(
        select(ApplicationInterviewRound)
        .where(
            ApplicationInterviewRound.owner_id == application.owner_id,
            ApplicationInterviewRound.application_id == application.id,
            ApplicationInterviewRound.status == "scheduled",
        )
        .order_by(
            ApplicationInterviewRound.scheduled_start_at,
            ApplicationInterviewRound.created_at,
            ApplicationInterviewRound.id,
        )
        .limit(1)
    )


def _latest_revision(
    session: Session,
    preparation: ApplicationInterviewPreparation | None,
) -> ApplicationInterviewPreparationRevision | None:
    if preparation is None:
        return None
    return session.scalar(
        select(ApplicationInterviewPreparationRevision)
        .where(
            ApplicationInterviewPreparationRevision.owner_id == preparation.owner_id,
            ApplicationInterviewPreparationRevision.preparation_id == preparation.id,
        )
        .order_by(
            ApplicationInterviewPreparationRevision.revision_number.desc(),
            ApplicationInterviewPreparationRevision.id.desc(),
        )
        .limit(1)
    )


def _content_hash(owner_id: str, payload: dict[str, object]) -> str:
    return hashlib.sha256(
        f"{owner_id}\0{_canonical_json(payload)}".encode("utf-8")
    ).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "InterviewPreparationRepositoryError",
    "create_interview_preparation_revision",
    "load_application_interview_preparation",
]
