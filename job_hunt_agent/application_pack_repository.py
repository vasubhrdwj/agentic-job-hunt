"""Owner-scoped persistence for provider-free application grounding reviews."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .application_pack_schemas import (
    APPLICATION_PACK_EXTRACTION_VERSION,
    ApplicationPackBlocker,
    ApplicationPackCreate,
    ApplicationPackDescriptionSource,
    ApplicationPackEventCreate,
    ApplicationPackEventResponse,
    ApplicationPackEvidenceSnapshot,
    ApplicationPackRequirementCoverage,
    ApplicationPackRequirementImportance,
    ApplicationPackRequirementResponse,
    ApplicationPackRevisionCreate,
    ApplicationPackRevisionResponse,
    ApplicationPackResponse,
    ApplicationPackStatus,
    ApplicationPackSummary,
    MAX_APPLICATION_PACK_CURRENT_EVIDENCE,
    MAX_APPLICATION_PACK_JOB_DESCRIPTION_CHARS,
    MAX_APPLICATION_PACK_REQUIREMENTS,
)
from .evidence_repository import list_approved_evidence_for_use
from .job_queue import utcnow
from .models import (
    AchievementEvidence,
    Application,
    ApplicationPack,
    ApplicationPackEvent,
    ApplicationPackRevision,
    JobPosting,
    JobPostingVersion,
    ResumeVersion,
)
from .mutation_receipts import claim_owner_mutation, complete_owner_mutation
from .private_payloads import decrypt_private_payload, encrypt_private_payload
from .profile_repository import load_resume_version
from .profile_schemas import AchievementEvidenceResponse
from .repository_errors import ResourceConflict, require_version
from .security import DataKeyring


_REQUIREMENT_HEADING_RE = re.compile(
    r"^(?:minimum|required|basic|preferred|desired|bonus|nice[- ]to[- ]have)?\s*"
    r"(?:requirements|qualifications|experience|skills|what you(?:'|’)ll bring|"
    r"what we(?:'|’)re looking for)\s*:?$",
    re.IGNORECASE,
)
_NON_REQUIREMENT_HEADING_RE = re.compile(
    r"^(?:about(?: us| the company| the role)?|company|the role|role overview|"
    r"what you(?:'|’)ll do|what you will do|responsibilities|your impact|"
    r"benefits|perks|what we offer|why join us|compensation|location|"
    r"equal opportunity|how to apply)\s*:?$",
    re.IGNORECASE,
)
_PREFERRED_RE = re.compile(
    r"\b(?:preferred|ideally|nice[- ]to[- ]have|bonus|a plus|desirable)\b",
    re.IGNORECASE,
)
_REQUIRED_RE = re.compile(
    r"\b(?:required|must|minimum|need(?:ed)?|at least|years? of experience|"
    r"proficien(?:t|cy)|strong (?:knowledge|experience)|hands[- ]on|"
    r"demonstrated|ability to|experience (?:with|in)|knowledge of|familiarity with|"
    r"degree in)\b",
    re.IGNORECASE,
)
_BULLET_PREFIX_RE = re.compile(r"(?:[-*•‣▪◦]+|\d+[.)])\s*")
_TOKEN_RE = re.compile(r"[a-z][a-z0-9+#.\-]{2,}")
_STOP_TOKENS = {
    "and",
    "are",
    "for",
    "from",
    "have",
    "our",
    "that",
    "the",
    "this",
    "with",
    "will",
    "you",
    "your",
}


class ApplicationPackRepositoryError(RuntimeError):
    """A persisted application-pack invariant failed."""


def load_application_pack(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    keyring: DataKeyring,
) -> ApplicationPackResponse | None:
    """Project the database-only grounding workspace for one application."""

    application = _owned_application(session, owner_id, application_id)
    if application is None:
        return None
    pack = _owned_pack(session, owner_id, application_id)
    return _pack_response(
        session,
        application=application,
        pack=pack,
        keyring=keyring,
    )


def create_application_pack(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    payload: ApplicationPackCreate,
    expected_application_version: int,
    idempotency_key: str,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> ApplicationPackResponse | None:
    """Create one pinned pack and its deterministic extracted revision."""

    current = _as_utc(now or utcnow())
    application = _owned_application(session, owner_id, application_id, for_update=True)
    if application is None:
        return None
    claim = claim_owner_mutation(
        session,
        owner_id=owner_id,
        namespace=f"application_pack.create:{application.id}",
        idempotency_key=idempotency_key,
        request={
            "payload": payload.model_dump(mode="json"),
            "expected_application_version": expected_application_version,
        },
        now=current,
    )
    if claim.replay is not None:
        _require_replay_type(claim.replay.resource_type, "application_pack")
        replayed = _owned_pack(session, owner_id, application.id)
        if replayed is None or replayed.id != claim.replay.resource_id:
            raise ApplicationPackRepositoryError("application-pack replay is unavailable")
        return _pack_response(
            session,
            application=application,
            pack=replayed,
            keyring=keyring,
        )

    require_version(
        "application",
        application.id,
        expected=expected_application_version,
        actual=application.version,
    )
    _require_editable_application(application)
    if _owned_pack(session, owner_id, application.id, for_update=True) is not None:
        raise ResourceConflict("an application pack already exists for this application")
    _require_open_posting(session, application)

    posting_version = session.scalar(
        select(JobPostingVersion).where(
            JobPostingVersion.owner_id == owner_id,
            JobPostingVersion.job_posting_id == application.job_posting_id,
            JobPostingVersion.id == application.pursued_posting_version_id,
        )
    )
    if posting_version is None:
        raise ApplicationPackRepositoryError("application posting version is unavailable")
    resume = load_resume_version(
        session,
        owner_id=owner_id,
        resume_version_id=payload.base_resume_version_id,
        keyring=keyring,
    )
    if resume is None:
        raise ValueError("base_resume_version_id does not exist for owner")

    description, description_source = _select_job_description(
        posting_version,
        owner_job_description=payload.owner_job_description,
    )
    approved = list_approved_evidence_for_use(
        session,
        owner_id=owner_id,
        keyring=keyring,
    )[:MAX_APPLICATION_PACK_CURRENT_EVIDENCE]
    requirements = _extract_requirements(description, approved)

    pack = ApplicationPack(
        id=uuid4().hex,
        owner_id=owner_id,
        application_id=application.id,
        job_posting_id=application.job_posting_id,
        posting_version_id=application.pursued_posting_version_id,
        base_resume_version_id=resume.metadata.id,
        version=1,
        created_at=current,
        updated_at=current,
    )
    session.add(pack)
    session.flush()
    _add_revision(
        session,
        pack=pack,
        revision_id=uuid4().hex,
        revision_number=1,
        parent_revision_id=None,
        source="extracted",
        description=description,
        description_source=description_source,
        requirements=requirements,
        keyring=keyring,
        now=current,
    )
    complete_owner_mutation(
        session,
        owner_id=owner_id,
        receipt_id=claim.receipt_id,
        resource_type="application_pack",
        resource_id=pack.id,
        result_version=pack.version,
        now=current,
    )
    return _pack_response(
        session,
        application=application,
        pack=pack,
        keyring=keyring,
    )


def create_application_pack_revision(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    pack_id: str,
    payload: ApplicationPackRevisionCreate,
    expected_pack_version: int,
    idempotency_key: str,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> ApplicationPackResponse | None:
    """Append one complete, encrypted requirement review revision."""

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
        namespace=f"application_pack.revision:{pack.id}",
        idempotency_key=idempotency_key,
        request={
            "payload": payload.model_dump(mode="json"),
            "expected_pack_version": expected_pack_version,
        },
        now=current,
    )
    if claim.replay is not None:
        _require_replay_type(claim.replay.resource_type, "application_pack_revision")
        replayed = session.scalar(
            select(ApplicationPackRevision).where(
                ApplicationPackRevision.owner_id == owner_id,
                ApplicationPackRevision.application_pack_id == pack.id,
                ApplicationPackRevision.id == claim.replay.resource_id,
            )
        )
        if replayed is None:
            raise ApplicationPackRepositoryError("application-pack revision replay is unavailable")
        return _pack_response(
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
    _require_editable_application(application)
    _require_open_posting(session, application)
    parent = _latest_revision(session, pack, for_update=True)
    if parent is None:
        raise ApplicationPackRepositoryError("application pack has no extracted revision")
    if parent.id != payload.parent_revision_id:
        raise ResourceConflict("parent_revision_id must name the current revision")
    stored = _load_revision_payload(parent, keyring=keyring)
    requirements = _reviewed_requirements(
        session,
        owner_id=owner_id,
        payload=payload,
        description=stored[1],
        keyring=keyring,
    )
    revision_id = uuid4().hex
    revision = _add_revision(
        session,
        pack=pack,
        revision_id=revision_id,
        revision_number=parent.revision_number + 1,
        parent_revision_id=parent.id,
        source="edited",
        description=stored[1],
        description_source=stored[0],
        requirements=requirements,
        keyring=keyring,
        now=current,
    )
    pack.version += 1
    pack.updated_at = current
    session.flush()
    complete_owner_mutation(
        session,
        owner_id=owner_id,
        receipt_id=claim.receipt_id,
        resource_type="application_pack_revision",
        resource_id=revision.id,
        result_version=pack.version,
        now=current,
    )
    return _pack_response(
        session,
        application=application,
        pack=pack,
        keyring=keyring,
    )


def record_application_pack_event(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
    pack_id: str,
    payload: ApplicationPackEventCreate,
    expected_pack_version: int,
    idempotency_key: str,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> ApplicationPackResponse | None:
    """Confirm that the owner reviewed every requirement in one exact revision."""

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
        namespace=f"application_pack.event:{pack.id}",
        idempotency_key=idempotency_key,
        request={
            "payload": payload.model_dump(mode="json"),
            "expected_pack_version": expected_pack_version,
        },
        now=current,
    )
    if claim.replay is not None:
        _require_replay_type(claim.replay.resource_type, "application_pack")
        if claim.replay.resource_id != pack.id:
            raise ApplicationPackRepositoryError("application-pack event replay is unavailable")
        return _pack_response(
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
    _require_editable_application(application)
    _require_open_posting(session, application)
    revision = _latest_revision(session, pack, for_update=True)
    if revision is None or revision.id != payload.revision_id:
        raise ResourceConflict("review the current application-pack revision")
    if session.scalar(
        select(ApplicationPackEvent.id).where(
            ApplicationPackEvent.owner_id == owner_id,
            ApplicationPackEvent.application_pack_id == pack.id,
            ApplicationPackEvent.revision_id == revision.id,
            ApplicationPackEvent.event_type == "reviewed",
        )
    ) is not None:
        raise ResourceConflict("this application-pack revision is already reviewed")
    stored = _load_revision_payload(revision, keyring=keyring)
    requirements = stored[2]
    if not requirements:
        raise ResourceConflict("at least one requirement must be reviewed")
    if any(
        item.coverage is ApplicationPackRequirementCoverage.needs_review
        for item in requirements
    ):
        raise ResourceConflict("every requirement must be reviewed before confirmation")
    if not _mapped_evidence_is_current(
        session,
        owner_id=owner_id,
        requirements=requirements,
        keyring=keyring,
        lock=True,
    ):
        raise ResourceConflict("mapped evidence changed; create a fresh review revision")

    next_sequence = int(
        session.scalar(
            select(func.max(ApplicationPackEvent.sequence_number)).where(
                ApplicationPackEvent.owner_id == owner_id,
                ApplicationPackEvent.application_pack_id == pack.id,
            )
        )
        or 0
    ) + 1
    event = ApplicationPackEvent(
        id=uuid4().hex,
        owner_id=owner_id,
        application_id=application_id,
        application_pack_id=pack.id,
        revision_id=revision.id,
        sequence_number=next_sequence,
        event_type="reviewed",
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
        resource_type="application_pack",
        resource_id=pack.id,
        result_version=pack.version,
        now=current,
    )
    return _pack_response(
        session,
        application=application,
        pack=pack,
        keyring=keyring,
    )


def _pack_response(
    session: Session,
    *,
    application: Application,
    pack: ApplicationPack | None,
    keyring: DataKeyring,
) -> ApplicationPackResponse:
    approved = list_approved_evidence_for_use(
        session,
        owner_id=application.owner_id,
        keyring=keyring,
    )[:MAX_APPLICATION_PACK_CURRENT_EVIDENCE]
    posting = session.scalar(
        select(JobPosting).where(
            JobPosting.owner_id == application.owner_id,
            JobPosting.id == application.job_posting_id,
        )
    )
    if posting is None:
        raise ApplicationPackRepositoryError("application posting is unavailable")
    if pack is None:
        blockers: list[ApplicationPackBlocker] = []
        if session.scalar(
            select(ResumeVersion.id).where(
                ResumeVersion.owner_id == application.owner_id,
                ResumeVersion.is_base.is_(True),
            )
        ) is None:
            blockers.append(ApplicationPackBlocker.base_resume_missing)
        if not approved:
            blockers.append(ApplicationPackBlocker.approved_evidence_missing)
        posting_version = session.scalar(
            select(JobPostingVersion).where(
                JobPostingVersion.owner_id == application.owner_id,
                JobPostingVersion.job_posting_id == application.job_posting_id,
                JobPostingVersion.id == application.pursued_posting_version_id,
            )
        )
        if posting_version is None:
            raise ApplicationPackRepositoryError("application posting version is unavailable")
        if not (posting_version.description or "").strip():
            blockers.append(ApplicationPackBlocker.owner_job_description_required)
        if posting.lifecycle_state != "open":
            blockers.append(ApplicationPackBlocker.posting_closed)
        return ApplicationPackResponse(
            application_id=application.id,
            status=ApplicationPackStatus.not_started,
            current_approved_evidence=approved,
            blockers=blockers,
        )

    if (
        pack.job_posting_id != application.job_posting_id
        or pack.posting_version_id != application.pursued_posting_version_id
    ):
        raise ApplicationPackRepositoryError("application pack is pinned to the wrong posting")
    current = _latest_revision(session, pack)
    if current is None:
        raise ApplicationPackRepositoryError("application pack has no revision")
    latest_review = _latest_review_event(session, pack)
    reviewed = (
        session.scalar(
            select(ApplicationPackRevision).where(
                ApplicationPackRevision.owner_id == pack.owner_id,
                ApplicationPackRevision.application_pack_id == pack.id,
                ApplicationPackRevision.id == latest_review.revision_id,
            )
        )
        if latest_review is not None
        else None
    )
    if latest_review is not None and reviewed is None:
        raise ApplicationPackRepositoryError("review event has no revision")
    current_response = _revision_response(current, keyring=keyring)
    reviewed_response = (
        _revision_response(reviewed, keyring=keyring) if reviewed is not None else None
    )
    status = (
        ApplicationPackStatus.reviewed
        if latest_review is not None and latest_review.revision_id == current.id
        else ApplicationPackStatus.draft
    )
    blockers = []
    if posting.lifecycle_state != "open":
        blockers.append(ApplicationPackBlocker.posting_closed)
    if not current_response.requirements:
        blockers.append(ApplicationPackBlocker.no_requirements_extracted)
    if any(
        requirement.coverage is ApplicationPackRequirementCoverage.needs_review
        for requirement in current_response.requirements
    ):
        blockers.append(ApplicationPackBlocker.requirements_need_review)
    if not _mapped_evidence_is_current(
        session,
        owner_id=pack.owner_id,
        requirements=current_response.requirements,
        keyring=keyring,
    ):
        blockers.append(ApplicationPackBlocker.mapped_evidence_changed)
    return ApplicationPackResponse(
        application_id=application.id,
        status=status,
        pack=_pack_summary(pack),
        current_revision=current_response,
        reviewed_revision=reviewed_response,
        review_event=(
            _event_response(latest_review) if latest_review is not None else None
        ),
        current_approved_evidence=approved,
        blockers=blockers,
    )


def _select_job_description(
    posting: JobPostingVersion,
    *,
    owner_job_description: str | None,
) -> tuple[str, ApplicationPackDescriptionSource]:
    persisted = posting.description or ""
    if persisted.strip():
        if owner_job_description is not None:
            raise ValueError(
                "owner_job_description is accepted only when persisted description is missing"
            )
        description = persisted
        source = ApplicationPackDescriptionSource.persisted_description
    elif owner_job_description is not None:
        description = owner_job_description
        source = ApplicationPackDescriptionSource.owner_supplied
    else:
        raise ValueError(
            "owner_job_description is required because persisted description is missing"
        )
    if len(description) > MAX_APPLICATION_PACK_JOB_DESCRIPTION_CHARS:
        raise ValueError("job description exceeds the supported size")
    return description, source


def _extract_requirements(
    description: str,
    approved: list[AchievementEvidenceResponse],
) -> list[ApplicationPackRequirementResponse]:
    candidates: list[
        tuple[int, int, str, ApplicationPackRequirementImportance]
    ] = []
    section_importance: ApplicationPackRequirementImportance | None = None
    for line_match in re.finditer(r"[^\r\n]+", description):
        raw = line_match.group(0)
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw.rstrip())
        if trailing <= leading:
            continue
        line_start = line_match.start() + leading
        line_end = line_match.start() + trailing
        line = description[line_start:line_end]
        heading_candidate = line.strip().rstrip(":").strip()
        if _REQUIREMENT_HEADING_RE.fullmatch(heading_candidate):
            section_importance = (
                ApplicationPackRequirementImportance.preferred
                if _PREFERRED_RE.search(heading_candidate)
                else ApplicationPackRequirementImportance.required
            )
            continue
        if _NON_REQUIREMENT_HEADING_RE.fullmatch(heading_candidate):
            section_importance = None
            continue
        bullet = _BULLET_PREFIX_RE.match(line)
        if bullet is not None:
            line_start += bullet.end()
            line = description[line_start:line_end].strip()
            line_start = description.find(line, line_start, line_end + 1)
            line_end = line_start + len(line)
        elif line.endswith(":"):
            section_importance = None
            continue

        segments = [(line_start, line_end)]
        if bullet is None and len(line) > 300:
            segments = []
            for sentence in re.finditer(r"[^.!?]+(?:[.!?]+|$)", description[line_start:line_end]):
                start = line_start + sentence.start()
                end = line_start + sentence.end()
                exact = description[start:end]
                left = len(exact) - len(exact.lstrip())
                right = len(exact.rstrip())
                if right > left:
                    segments.append((start + left, start + right))
        for start, end in segments:
            text = description[start:end]
            if not 8 <= len(text) <= 2_000:
                continue
            signalled = _REQUIRED_RE.search(text) or _PREFERRED_RE.search(text)
            if not signalled and (section_importance is None or bullet is None):
                continue
            importance = (
                ApplicationPackRequirementImportance.preferred
                if _PREFERRED_RE.search(text)
                else section_importance
                or ApplicationPackRequirementImportance.required
            )
            candidates.append((start, end, text, importance))

    requirements: list[ApplicationPackRequirementResponse] = []
    seen: set[str] = set()
    for start, end, text, importance in sorted(
        candidates,
        key=lambda item: (item[0], item[1]),
    ):
        normalized = " ".join(text.split()).casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        suggestions = _suggest_evidence(text, approved)
        requirements.append(
            ApplicationPackRequirementResponse(
                id=f"requirement_{len(requirements) + 1:02d}",
                ordinal=len(requirements) + 1,
                importance=importance,
                text=text,
                source_start=start,
                source_end=end,
                coverage=ApplicationPackRequirementCoverage.needs_review,
                evidence=suggestions,
            )
        )
        if len(requirements) == MAX_APPLICATION_PACK_REQUIREMENTS:
            break
    return requirements


def _suggest_evidence(
    requirement: str,
    approved: list[AchievementEvidenceResponse],
) -> list[ApplicationPackEvidenceSnapshot]:
    requirement_tokens = _tokens(requirement)
    ranked: list[tuple[int, datetime, str, AchievementEvidenceResponse]] = []
    for evidence in approved:
        candidate_tokens = _tokens(" ".join([evidence.statement, *evidence.skills]))
        overlap = len(requirement_tokens & candidate_tokens)
        if overlap:
            ranked.append(
                (
                    overlap,
                    evidence.approved_at or evidence.updated_at,
                    evidence.id,
                    evidence,
                )
            )
    ranked.sort(key=lambda item: (-item[0], -item[1].timestamp(), item[2]))
    return [_evidence_snapshot(item[3]) for item in ranked[:3]]


def _reviewed_requirements(
    session: Session,
    *,
    owner_id: str,
    payload: ApplicationPackRevisionCreate,
    description: str,
    keyring: DataKeyring,
) -> list[ApplicationPackRequirementResponse]:
    requested_versions: dict[str, int] = {}
    for requirement in payload.requirements:
        if description[requirement.source_start : requirement.source_end] != requirement.text:
            raise ValueError("requirement text must match its exact pinned source span")
        for reference in requirement.evidence_refs:
            previous = requested_versions.setdefault(reference.id, reference.version)
            if previous != reference.version:
                raise ValueError("one evidence id cannot be referenced at multiple versions")
    snapshots = _locked_evidence_snapshots(
        session,
        owner_id=owner_id,
        requested_versions=requested_versions,
        keyring=keyring,
    )
    return [
        ApplicationPackRequirementResponse(
            id=requirement.id,
            ordinal=requirement.ordinal,
            importance=requirement.importance,
            text=requirement.text,
            source_start=requirement.source_start,
            source_end=requirement.source_end,
            coverage=requirement.coverage,
            evidence=[snapshots[reference.id] for reference in requirement.evidence_refs],
        )
        for requirement in sorted(payload.requirements, key=lambda item: item.ordinal)
    ]


def _locked_evidence_snapshots(
    session: Session,
    *,
    owner_id: str,
    requested_versions: dict[str, int],
    keyring: DataKeyring,
) -> dict[str, ApplicationPackEvidenceSnapshot]:
    if not requested_versions:
        return {}
    rows = list(
        session.scalars(
            select(AchievementEvidence)
            .where(
                AchievementEvidence.owner_id == owner_id,
                AchievementEvidence.id.in_(sorted(requested_versions)),
            )
            .order_by(AchievementEvidence.id)
            .with_for_update()
        )
    )
    if len(rows) != len(requested_versions):
        raise ValueError("one or more evidence refs do not exist for owner")
    snapshots: dict[str, ApplicationPackEvidenceSnapshot] = {}
    for row in rows:
        if row.version != requested_versions[row.id]:
            raise ResourceConflict("mapped evidence changed; reload approved evidence")
        if row.approval_state != "approved" or row.approved_at is None:
            raise ResourceConflict("only currently approved evidence may be mapped")
        snapshots[row.id] = _evidence_snapshot_from_row(row, keyring=keyring)
    return snapshots


def _mapped_evidence_is_current(
    session: Session,
    *,
    owner_id: str,
    requirements: list[ApplicationPackRequirementResponse],
    keyring: DataKeyring,
    lock: bool = False,
) -> bool:
    snapshots = {
        evidence.id: evidence
        for requirement in requirements
        for evidence in requirement.evidence
    }
    if not snapshots:
        return True
    statement = select(AchievementEvidence).where(
        AchievementEvidence.owner_id == owner_id,
        AchievementEvidence.id.in_(sorted(snapshots)),
    ).order_by(AchievementEvidence.id)
    if lock:
        statement = statement.with_for_update()
    rows = list(session.scalars(statement))
    if len(rows) != len(snapshots):
        return False
    for row in rows:
        expected = snapshots[row.id]
        if (
            row.version != expected.version
            or row.approval_state != "approved"
            or row.approved_at is None
            or _as_utc(row.approved_at) != expected.approved_at
        ):
            return False
        try:
            current = _evidence_snapshot_from_row(row, keyring=keyring)
        except (ValueError, ApplicationPackRepositoryError):
            return False
        if current != expected:
            return False
    return True


def _evidence_snapshot(
    evidence: AchievementEvidenceResponse,
) -> ApplicationPackEvidenceSnapshot:
    if evidence.approved_at is None:
        raise ApplicationPackRepositoryError("approved evidence has no approval timestamp")
    return ApplicationPackEvidenceSnapshot(
        id=evidence.id,
        version=evidence.version,
        statement=evidence.statement,
        source_resume_version_id=evidence.source_resume_version_id,
        source_excerpt=evidence.source_excerpt,
        skills=evidence.skills,
        approved_at=evidence.approved_at,
    )


def _evidence_snapshot_from_row(
    row: AchievementEvidence,
    *,
    keyring: DataKeyring,
) -> ApplicationPackEvidenceSnapshot:
    if row.approved_at is None:
        raise ApplicationPackRepositoryError("approved evidence has no approval timestamp")
    private = decrypt_private_payload(
        keyring,
        record_kind="achievement_evidence",
        owner_id=row.owner_id,
        record_id=row.id,
        encryption_key_id=row.encryption_key_id,
        ciphertext=row.encrypted_payload,
    )
    statement = private.get("statement")
    excerpt = private.get("source_excerpt")
    if not isinstance(statement, str) or (excerpt is not None and not isinstance(excerpt, str)):
        raise ApplicationPackRepositoryError("achievement evidence payload is invalid")
    return ApplicationPackEvidenceSnapshot(
        id=row.id,
        version=row.version,
        statement=statement,
        source_resume_version_id=row.source_resume_version_id,
        source_excerpt=excerpt,
        skills=row.skills,
        approved_at=_as_utc(row.approved_at),
    )


def _add_revision(
    session: Session,
    *,
    pack: ApplicationPack,
    revision_id: str,
    revision_number: int,
    parent_revision_id: str | None,
    source: str,
    description: str,
    description_source: ApplicationPackDescriptionSource,
    requirements: list[ApplicationPackRequirementResponse],
    keyring: DataKeyring,
    now: datetime,
) -> ApplicationPackRevision:
    private_payload = {
        "extraction_version": APPLICATION_PACK_EXTRACTION_VERSION,
        "job_description_source": description_source.value,
        "job_description": description,
        "requirements": [item.model_dump(mode="json") for item in requirements],
    }
    envelope = encrypt_private_payload(
        keyring,
        record_kind="application_pack_revision",
        owner_id=pack.owner_id,
        record_id=revision_id,
        payload=private_payload,
    )
    revision = ApplicationPackRevision(
        id=revision_id,
        owner_id=pack.owner_id,
        application_id=pack.application_id,
        application_pack_id=pack.id,
        parent_revision_id=parent_revision_id,
        revision_number=revision_number,
        source=source,
        encrypted_payload=envelope.ciphertext,
        encryption_key_id=envelope.key_id,
        content_hash=_content_hash(pack.owner_id, private_payload),
        created_at=now,
    )
    session.add(revision)
    session.flush()
    return revision


def _load_revision_payload(
    revision: ApplicationPackRevision,
    *,
    keyring: DataKeyring,
) -> tuple[
    ApplicationPackDescriptionSource,
    str,
    list[ApplicationPackRequirementResponse],
]:
    private = decrypt_private_payload(
        keyring,
        record_kind="application_pack_revision",
        owner_id=revision.owner_id,
        record_id=revision.id,
        encryption_key_id=revision.encryption_key_id,
        ciphertext=revision.encrypted_payload,
    )
    if set(private) != {
        "extraction_version",
        "job_description_source",
        "job_description",
        "requirements",
    }:
        raise ApplicationPackRepositoryError("application-pack payload shape is invalid")
    if private.get("extraction_version") != APPLICATION_PACK_EXTRACTION_VERSION:
        raise ApplicationPackRepositoryError("application-pack extraction version is invalid")
    try:
        source = ApplicationPackDescriptionSource(private["job_description_source"])
    except (TypeError, ValueError) as exc:
        raise ApplicationPackRepositoryError(
            "application-pack description source is invalid"
        ) from exc
    description = private.get("job_description")
    raw_requirements = private.get("requirements")
    if (
        not isinstance(description, str)
        or not description.strip()
        or len(description) > MAX_APPLICATION_PACK_JOB_DESCRIPTION_CHARS
        or not isinstance(raw_requirements, list)
        or len(raw_requirements) > MAX_APPLICATION_PACK_REQUIREMENTS
    ):
        raise ApplicationPackRepositoryError("application-pack private payload is invalid")
    try:
        requirements = [
            ApplicationPackRequirementResponse.model_validate(item)
            for item in raw_requirements
        ]
    except (TypeError, ValueError) as exc:
        raise ApplicationPackRepositoryError("application-pack requirements are invalid") from exc
    ids = [item.id for item in requirements]
    ordinals = [item.ordinal for item in requirements]
    if (
        len(ids) != len(set(ids))
        or ordinals != sorted(ordinals)
        or len(ordinals) != len(set(ordinals))
    ):
        raise ApplicationPackRepositoryError("application-pack requirements are unordered")
    for requirement in requirements:
        if description[requirement.source_start : requirement.source_end] != requirement.text:
            raise ApplicationPackRepositoryError("application-pack source span is invalid")
    if _content_hash(revision.owner_id, private) != revision.content_hash:
        raise ApplicationPackRepositoryError("application-pack content hash is invalid")
    return source, description, requirements


def _revision_response(
    revision: ApplicationPackRevision,
    *,
    keyring: DataKeyring,
) -> ApplicationPackRevisionResponse:
    source, description, requirements = _load_revision_payload(revision, keyring=keyring)
    return ApplicationPackRevisionResponse(
        id=revision.id,
        application_pack_id=revision.application_pack_id,
        parent_revision_id=revision.parent_revision_id,
        revision_number=revision.revision_number,
        source=revision.source,
        extraction_version=APPLICATION_PACK_EXTRACTION_VERSION,
        job_description_source=source,
        job_description=description,
        requirements=requirements,
        created_at=_as_utc(revision.created_at),
    )


def _pack_summary(pack: ApplicationPack) -> ApplicationPackSummary:
    return ApplicationPackSummary(
        id=pack.id,
        version=pack.version,
        application_id=pack.application_id,
        posting_version_id=pack.posting_version_id,
        base_resume_version_id=pack.base_resume_version_id,
        created_at=_as_utc(pack.created_at),
        updated_at=_as_utc(pack.updated_at),
    )


def _event_response(event: ApplicationPackEvent) -> ApplicationPackEventResponse:
    return ApplicationPackEventResponse(
        id=event.id,
        application_pack_id=event.application_pack_id,
        revision_id=event.revision_id,
        sequence_number=event.sequence_number,
        event_type="reviewed",
        occurred_at=_as_utc(event.occurred_at),
    )


def _owned_application(
    session: Session,
    owner_id: str,
    application_id: str,
    *,
    for_update: bool = False,
) -> Application | None:
    statement = select(Application).where(
        Application.owner_id == owner_id,
        Application.id == application_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return session.scalar(statement)


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


def _latest_revision(
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
        .order_by(
            ApplicationPackRevision.revision_number.desc(),
            ApplicationPackRevision.id.desc(),
        )
        .limit(1)
    )
    if for_update:
        statement = statement.with_for_update()
    return session.scalar(statement)


def _latest_review_event(
    session: Session,
    pack: ApplicationPack,
) -> ApplicationPackEvent | None:
    return session.scalar(
        select(ApplicationPackEvent)
        .where(
            ApplicationPackEvent.owner_id == pack.owner_id,
            ApplicationPackEvent.application_pack_id == pack.id,
            ApplicationPackEvent.event_type == "reviewed",
        )
        .order_by(
            ApplicationPackEvent.sequence_number.desc(),
            ApplicationPackEvent.occurred_at.desc(),
            ApplicationPackEvent.id.desc(),
        )
        .limit(1)
    )


def _require_open_posting(session: Session, application: Application) -> None:
    posting = session.scalar(
        select(JobPosting)
        .where(
            JobPosting.owner_id == application.owner_id,
            JobPosting.id == application.job_posting_id,
        )
        .with_for_update()
    )
    if posting is None:
        raise ApplicationPackRepositoryError("application posting is unavailable")
    if posting.lifecycle_state != "open":
        raise ResourceConflict("closed postings cannot mutate application packs")


def _require_editable_application(application: Application) -> None:
    if application.stage != "pursuing":
        raise ResourceConflict(
            "application packs are frozen after the application is ready to apply"
        )


def _content_hash(owner_id: str, payload: dict[str, object]) -> str:
    return _sha256(f"{owner_id}\0{_canonical_json(payload)}")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _tokens(value: str) -> set[str]:
    return {
        token.strip(".-")
        for token in _TOKEN_RE.findall(value.casefold())
        if token.strip(".-") not in _STOP_TOKENS
    }


def _require_replay_type(actual: str, expected: str) -> None:
    if actual != expected:
        raise ApplicationPackRepositoryError("application-pack replay type is inconsistent")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "ApplicationPackRepositoryError",
    "create_application_pack",
    "create_application_pack_revision",
    "load_application_pack",
    "record_application_pack_event",
]
