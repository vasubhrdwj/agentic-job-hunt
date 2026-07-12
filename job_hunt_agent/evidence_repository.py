"""Encrypted evidence repository with an explicit human approval gate."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .job_queue import utcnow
from .models import AchievementEvidence, ResumeVersion
from .private_payloads import (
    PrivatePayloadBindingError,
    decrypt_private_payload,
    encrypt_private_payload,
)
from .profile_repository import load_resume_version
from .profile_schemas import (
    AchievementEvidenceCreate,
    AchievementEvidencePatch,
    AchievementEvidenceResponse,
)
from .repository_errors import ResourceConflict, require_version
from .security import DataKeyring


def create_achievement_evidence(
    session: Session,
    *,
    owner_id: str,
    payload: AchievementEvidenceCreate,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> AchievementEvidenceResponse:
    current = now or utcnow()
    _validate_source_evidence(session, owner_id, payload, keyring)
    record_id = uuid4().hex
    envelope = encrypt_private_payload(
        keyring,
        record_kind="achievement_evidence",
        owner_id=owner_id,
        record_id=record_id,
        payload={
            "statement": payload.statement,
            "source_excerpt": payload.source_excerpt,
        },
    )
    row = AchievementEvidence(
        id=record_id,
        owner_id=owner_id,
        source_resume_version_id=payload.source_resume_version_id,
        encrypted_payload=envelope.ciphertext,
        encryption_key_id=envelope.key_id,
        skills=list(payload.skills),
        origin=payload.origin,
        approval_state="pending",
        version=1,
        created_at=current,
        updated_at=current,
    )
    session.add(row)
    session.flush()
    return _evidence_response(row, keyring)


def list_achievement_evidence(
    session: Session,
    *,
    owner_id: str,
    keyring: DataKeyring,
    approval_state: str | None = None,
) -> list[AchievementEvidenceResponse]:
    statement = select(AchievementEvidence).where(
        AchievementEvidence.owner_id == owner_id
    )
    if approval_state is not None:
        statement = statement.where(AchievementEvidence.approval_state == approval_state)
    rows = session.scalars(
        statement.order_by(AchievementEvidence.created_at, AchievementEvidence.id)
    )
    return [_evidence_response(row, keyring) for row in rows]


def list_approved_evidence_for_use(
    session: Session,
    *,
    owner_id: str,
    keyring: DataKeyring,
) -> list[AchievementEvidenceResponse]:
    """The only downstream evidence loader; pending/rejected/retired never enter."""

    return list_achievement_evidence(
        session,
        owner_id=owner_id,
        keyring=keyring,
        approval_state="approved",
    )


def update_achievement_evidence(
    session: Session,
    *,
    owner_id: str,
    evidence_id: str,
    patch: AchievementEvidencePatch,
    expected_version: int,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> AchievementEvidenceResponse | None:
    current = now or utcnow()
    row = session.scalar(
        select(AchievementEvidence)
        .where(
            AchievementEvidence.owner_id == owner_id,
            AchievementEvidence.id == evidence_id,
        )
        .with_for_update()
    )
    if row is None:
        return None
    require_version("achievement_evidence", row.id, expected=expected_version, actual=row.version)
    private = _private_evidence(row, keyring)
    content_changed = any(
        field in patch.model_fields_set for field in ("statement", "source_excerpt", "skills")
    )
    if row.approval_state == "retired" and content_changed:
        raise ResourceConflict("retired evidence cannot be edited")
    if content_changed and patch.approval_state not in (None, "pending"):
        raise ResourceConflict("edited evidence must return to pending review")

    if content_changed:
        statement = patch.statement if patch.statement is not None else private["statement"]
        source_excerpt = (
            patch.source_excerpt
            if "source_excerpt" in patch.model_fields_set
            else private.get("source_excerpt")
        )
        if source_excerpt is not None:
            if row.source_resume_version_id is None:
                raise ValueError("source_excerpt requires source_resume_version_id")
            source_resume = load_resume_version(
                session,
                owner_id=owner_id,
                resume_version_id=row.source_resume_version_id,
                keyring=keyring,
            )
            if source_resume is None or source_excerpt not in source_resume.content:
                raise ValueError("source_excerpt must be verbatim resume content")
        skills = patch.skills if patch.skills is not None else row.skills
        envelope = encrypt_private_payload(
            keyring,
            record_kind="achievement_evidence",
            owner_id=owner_id,
            record_id=row.id,
            payload={"statement": statement, "source_excerpt": source_excerpt},
        )
        row.encrypted_payload = envelope.ciphertext
        row.encryption_key_id = envelope.key_id
        row.skills = list(skills)
        row.approval_state = "pending"
        row.approved_at = None
        row.rejected_at = None
        row.retired_at = None
    elif patch.approval_state is not None:
        _apply_approval_transition(row, patch.approval_state, current)

    row.version += 1
    row.updated_at = current
    session.flush()
    return _evidence_response(row, keyring)


def _apply_approval_transition(
    row: AchievementEvidence, target: str, now: datetime
) -> None:
    current = row.approval_state
    if current == target:
        raise ResourceConflict("evidence is already in the requested approval state")
    allowed = {
        ("pending", "approved"),
        ("pending", "rejected"),
        ("approved", "retired"),
    }
    if (current, target) not in allowed:
        raise ResourceConflict(f"evidence cannot transition from {current} to {target}")
    row.approval_state = target
    row.approved_at = now if target == "approved" else row.approved_at
    row.rejected_at = now if target == "rejected" else None
    row.retired_at = now if target == "retired" else None


def _validate_source_evidence(
    session: Session,
    owner_id: str,
    payload: AchievementEvidenceCreate,
    keyring: DataKeyring,
) -> None:
    if payload.origin == "resume_suggestion" and payload.source_resume_version_id is None:
        raise ValueError("resume suggestions require source_resume_version_id")
    if payload.source_excerpt is not None and payload.source_resume_version_id is None:
        raise ValueError("source_excerpt requires source_resume_version_id")
    if payload.source_resume_version_id is None:
        return
    resume = load_resume_version(
        session,
        owner_id=owner_id,
        resume_version_id=payload.source_resume_version_id,
        keyring=keyring,
    )
    if resume is None:
        raise ValueError("source resume version does not exist for owner")
    if payload.source_excerpt is not None and payload.source_excerpt not in resume.content:
        raise ValueError("source_excerpt must be verbatim resume content")


def _evidence_response(
    row: AchievementEvidence, keyring: DataKeyring
) -> AchievementEvidenceResponse:
    private = _private_evidence(row, keyring)
    try:
        return AchievementEvidenceResponse(
            id=row.id,
            statement=private["statement"],
            source_resume_version_id=row.source_resume_version_id,
            source_excerpt=private.get("source_excerpt"),
            skills=row.skills,
            origin=row.origin,
            approval_state=row.approval_state,
            approved_at=_optional_utc(row.approved_at),
            rejected_at=_optional_utc(row.rejected_at),
            retired_at=_optional_utc(row.retired_at),
            version=row.version,
            created_at=_as_utc(row.created_at),
            updated_at=_as_utc(row.updated_at),
        )
    except ValidationError as exc:
        raise PrivatePayloadBindingError("achievement evidence payload is invalid") from exc


def _private_evidence(row: AchievementEvidence, keyring: DataKeyring) -> dict[str, str | None]:
    payload = decrypt_private_payload(
        keyring,
        record_kind="achievement_evidence",
        owner_id=row.owner_id,
        record_id=row.id,
        encryption_key_id=row.encryption_key_id,
        ciphertext=row.encrypted_payload,
    )
    if not isinstance(payload.get("statement"), str):
        raise ValueError("achievement evidence private payload is invalid")
    excerpt = payload.get("source_excerpt")
    if excerpt is not None and not isinstance(excerpt, str):
        raise ValueError("achievement evidence private payload is invalid")
    return payload


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_utc(value: datetime | None) -> datetime | None:
    return _as_utc(value) if value is not None else None


__all__ = [
    "create_achievement_evidence",
    "list_achievement_evidence",
    "list_approved_evidence_for_use",
    "update_achievement_evidence",
]
