"""Caller-transaction-owned repositories for profile, tracks, and resumes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .job_queue import utcnow
from .models import (
    AchievementEvidence,
    ApplicationPack,
    CandidateProfile,
    CareerTrack,
    Owner,
    ResumeVersion,
    SavedSearch,
)
from .private_payloads import (
    PrivatePayloadBindingError,
    decrypt_private_payload,
    encrypt_private_payload,
)
from .profile_schemas import CandidateProfileWrite, CareerTrackCreate
from .repository_errors import ResourceConflict, ResourceInUse, require_version
from .security import DataKeyring, MAX_RESUME_CHARS


ResumeSource = Literal["pasted", "uploaded", "imported", "edited"]
CandidateProfileInput = CandidateProfileWrite
CareerTrackInput = CareerTrackCreate


@dataclass(frozen=True)
class CandidateProfileRecord:
    id: str
    owner_id: str
    data: CandidateProfileInput
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CareerTrackRecord:
    id: str
    owner_id: str
    data: CareerTrackInput
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ResumeVersionMetadata:
    id: str
    owner_id: str
    parent_id: str | None
    label: str
    content_hash: str
    source: str
    is_base: bool
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ResumeVersionContent:
    metadata: ResumeVersionMetadata
    content: str


@dataclass(frozen=True)
class CreateResumeResult:
    resume: ResumeVersionMetadata
    created: bool


def upsert_candidate_profile(
    session: Session,
    *,
    owner_id: str,
    data: CandidateProfileInput,
    keyring: DataKeyring,
    expected_version: int | None = None,
    now: datetime | None = None,
) -> CandidateProfileRecord:
    """Create the owner's profile or replace it under optimistic concurrency."""

    _require_owner(session, owner_id)
    current = now or utcnow()
    row = session.scalar(
        select(CandidateProfile)
        .where(CandidateProfile.owner_id == owner_id)
        .with_for_update()
    )
    if row is None:
        if expected_version not in (None, 0):
            raise ResourceConflict("candidate profile does not exist at expected version")
        record_id = uuid4().hex
        envelope = encrypt_private_payload(
            keyring,
            record_kind="candidate_profile",
            owner_id=owner_id,
            record_id=record_id,
            payload=data.model_dump(mode="json", exclude={"onboarding_step"}),
        )
        row = CandidateProfile(
            id=record_id,
            owner_id=owner_id,
            encrypted_payload=envelope.ciphertext,
            encryption_key_id=envelope.key_id,
            onboarding_state=data.onboarding_step,
            version=1,
            created_at=current,
            updated_at=current,
        )
        session.add(row)
    else:
        if expected_version is None:
            raise ResourceConflict("expected_version is required to update candidate profile")
        require_version("candidate_profile", row.id, expected=expected_version, actual=row.version)
        envelope = encrypt_private_payload(
            keyring,
            record_kind="candidate_profile",
            owner_id=owner_id,
            record_id=row.id,
            payload=data.model_dump(mode="json", exclude={"onboarding_step"}),
        )
        row.encrypted_payload = envelope.ciphertext
        row.encryption_key_id = envelope.key_id
        row.onboarding_state = data.onboarding_step
        row.version += 1
        row.updated_at = current
    session.flush()
    return _profile_record(row, keyring)


def load_candidate_profile(
    session: Session,
    *,
    owner_id: str,
    keyring: DataKeyring,
) -> CandidateProfileRecord | None:
    row = session.scalar(select(CandidateProfile).where(CandidateProfile.owner_id == owner_id))
    return _profile_record(row, keyring) if row is not None else None


def create_career_track(
    session: Session,
    *,
    owner_id: str,
    data: CareerTrackInput,
    now: datetime | None = None,
) -> CareerTrackRecord:
    _require_owner(session, owner_id)
    if session.scalar(
        select(CareerTrack.id).where(
            CareerTrack.owner_id == owner_id, CareerTrack.name == data.name
        )
    ) is not None:
        raise ResourceConflict("career track name is already in use")
    current = now or utcnow()
    row = CareerTrack(
        id=uuid4().hex,
        owner_id=owner_id,
        name=data.name,
        role_families=list(data.role_families),
        seniority_levels=list(data.seniority_levels),
        target_locations=list(data.target_locations),
        priorities=data.priorities.model_dump(mode="json"),
        active=data.active,
        version=1,
        created_at=current,
        updated_at=current,
    )
    session.add(row)
    session.flush()
    return _track_record(row)


def list_career_tracks(session: Session, *, owner_id: str) -> list[CareerTrackRecord]:
    rows = session.scalars(
        select(CareerTrack)
        .where(CareerTrack.owner_id == owner_id)
        .order_by(CareerTrack.created_at, CareerTrack.id)
    )
    return [_track_record(row) for row in rows]


def load_career_track(
    session: Session, *, owner_id: str, career_track_id: str
) -> CareerTrackRecord | None:
    row = _owner_track(session, owner_id, career_track_id)
    return _track_record(row) if row is not None else None


def update_career_track(
    session: Session,
    *,
    owner_id: str,
    career_track_id: str,
    data: CareerTrackInput,
    expected_version: int,
    now: datetime | None = None,
) -> CareerTrackRecord | None:
    row = session.scalar(
        select(CareerTrack)
        .where(CareerTrack.owner_id == owner_id, CareerTrack.id == career_track_id)
        .with_for_update()
    )
    if row is None:
        return None
    require_version("career_track", row.id, expected=expected_version, actual=row.version)
    _guard_active_search_seniority(session, row, set(data.seniority_levels))
    duplicate = session.scalar(
        select(CareerTrack.id).where(
            CareerTrack.owner_id == owner_id,
            CareerTrack.name == data.name,
            CareerTrack.id != row.id,
        )
    )
    if duplicate is not None:
        raise ResourceConflict("career track name is already in use")
    row.name = data.name
    row.role_families = list(data.role_families)
    row.seniority_levels = list(data.seniority_levels)
    row.target_locations = list(data.target_locations)
    row.priorities = data.priorities.model_dump(mode="json")
    row.active = data.active
    row.version += 1
    row.updated_at = now or utcnow()
    session.flush()
    return _track_record(row)


def delete_career_track(
    session: Session,
    *,
    owner_id: str,
    career_track_id: str,
    expected_version: int,
) -> bool:
    row = session.scalar(
        select(CareerTrack)
        .where(CareerTrack.owner_id == owner_id, CareerTrack.id == career_track_id)
        .with_for_update()
    )
    if row is None:
        return False
    require_version("career_track", row.id, expected=expected_version, actual=row.version)
    if session.scalar(
        select(SavedSearch.id).where(
            SavedSearch.owner_id == owner_id,
            SavedSearch.career_track_id == row.id,
        ).limit(1)
    ) is not None:
        raise ResourceInUse("career track is referenced by saved searches")
    session.delete(row)
    session.flush()
    return True


def create_or_reuse_resume_version(
    session: Session,
    *,
    owner_id: str,
    label: str,
    content: str,
    source: ResumeSource,
    keyring: DataKeyring,
    parent_id: str | None = None,
    make_base: bool = True,
    now: datetime | None = None,
) -> CreateResumeResult:
    _require_owner(session, owner_id)
    normalized_label = " ".join(label.split())
    if not normalized_label or len(normalized_label) > 120:
        raise ValueError("resume label must be 1-120 characters")
    if not content.strip() or len(content) > MAX_RESUME_CHARS:
        raise ValueError(f"resume content must be 1-{MAX_RESUME_CHARS} characters")
    if parent_id is not None and _owner_resume(session, owner_id, parent_id) is None:
        raise ValueError("parent resume version does not exist for owner")
    content_hash = _resume_content_hash(owner_id, content)
    existing = session.scalar(
        select(ResumeVersion)
        .where(
            ResumeVersion.owner_id == owner_id,
            ResumeVersion.content_hash == content_hash,
        )
        .with_for_update()
    )
    if existing is not None:
        if make_base and not existing.is_base:
            _make_base_locked(session, existing, now=now or utcnow())
        return CreateResumeResult(resume=_resume_metadata(existing), created=False)

    current = now or utcnow()
    record_id = uuid4().hex
    envelope = encrypt_private_payload(
        keyring,
        record_kind="resume_version",
        owner_id=owner_id,
        record_id=record_id,
        payload={"content": content},
    )
    owner_resumes = list(
        session.scalars(
            select(ResumeVersion)
            .where(ResumeVersion.owner_id == owner_id)
            .with_for_update()
        )
    )
    should_be_base = make_base or not owner_resumes
    if should_be_base:
        for resume in owner_resumes:
            if resume.is_base:
                resume.is_base = False
                resume.version += 1
                resume.updated_at = current
    row = ResumeVersion(
        id=record_id,
        owner_id=owner_id,
        parent_id=parent_id,
        label=normalized_label,
        encrypted_content=envelope.ciphertext,
        encryption_key_id=envelope.key_id,
        content_hash=content_hash,
        source=source,
        is_base=should_be_base,
        version=1,
        created_at=current,
        updated_at=current,
    )
    session.add(row)
    session.flush()
    return CreateResumeResult(resume=_resume_metadata(row), created=True)


def list_resume_versions(
    session: Session, *, owner_id: str
) -> list[ResumeVersionMetadata]:
    rows = session.scalars(
        select(ResumeVersion)
        .where(ResumeVersion.owner_id == owner_id)
        .order_by(ResumeVersion.created_at.desc(), ResumeVersion.id.desc())
    )
    return [_resume_metadata(row) for row in rows]


def load_resume_version(
    session: Session,
    *,
    owner_id: str,
    resume_version_id: str,
    keyring: DataKeyring,
) -> ResumeVersionContent | None:
    row = _owner_resume(session, owner_id, resume_version_id)
    if row is None:
        return None
    payload = decrypt_private_payload(
        keyring,
        record_kind="resume_version",
        owner_id=owner_id,
        record_id=row.id,
        encryption_key_id=row.encryption_key_id,
        ciphertext=row.encrypted_content,
    )
    content = payload.get("content")
    if not isinstance(content, str):
        raise ValueError("resume private payload is invalid")
    return ResumeVersionContent(metadata=_resume_metadata(row), content=content)


def set_base_resume_version(
    session: Session,
    *,
    owner_id: str,
    resume_version_id: str,
    expected_version: int,
    now: datetime | None = None,
) -> ResumeVersionMetadata | None:
    row = session.scalar(
        select(ResumeVersion)
        .where(ResumeVersion.owner_id == owner_id, ResumeVersion.id == resume_version_id)
        .with_for_update()
    )
    if row is None:
        return None
    require_version("resume_version", row.id, expected=expected_version, actual=row.version)
    _make_base_locked(session, row, now=now or utcnow())
    session.flush()
    return _resume_metadata(row)


def delete_resume_version(
    session: Session,
    *,
    owner_id: str,
    resume_version_id: str,
    expected_version: int,
) -> bool:
    row = session.scalar(
        select(ResumeVersion)
        .where(ResumeVersion.owner_id == owner_id, ResumeVersion.id == resume_version_id)
        .with_for_update()
    )
    if row is None:
        return False
    require_version("resume_version", row.id, expected=expected_version, actual=row.version)
    if session.scalar(
        select(SavedSearch.id).where(
            SavedSearch.owner_id == owner_id,
            SavedSearch.resume_version_id == row.id,
        ).limit(1)
    ) is not None:
        raise ResourceInUse("resume version is referenced by saved searches")
    if session.scalar(
        select(ApplicationPack.id).where(
            ApplicationPack.owner_id == owner_id,
            ApplicationPack.base_resume_version_id == row.id,
        ).limit(1)
    ) is not None:
        raise ResourceInUse("resume version is referenced by an application pack")
    if row.is_base and session.scalar(
        select(ResumeVersion.id).where(
            ResumeVersion.owner_id == owner_id,
            ResumeVersion.id != row.id,
        ).limit(1)
    ) is not None:
        raise ResourceInUse("select another base resume before deleting this version")
    if session.scalar(
        select(ResumeVersion.id).where(
            ResumeVersion.owner_id == owner_id,
            ResumeVersion.parent_id == row.id,
        ).limit(1)
    ) is not None:
        raise ResourceInUse("resume version is the immutable parent of another version")
    if session.scalar(
        select(AchievementEvidence.id).where(
            AchievementEvidence.owner_id == owner_id,
            AchievementEvidence.source_resume_version_id == row.id,
        ).limit(1)
    ) is not None:
        raise ResourceInUse("resume version is referenced by achievement evidence")
    session.delete(row)
    session.flush()
    return True


def _profile_record(row: CandidateProfile, keyring: DataKeyring) -> CandidateProfileRecord:
    payload = decrypt_private_payload(
        keyring,
        record_kind="candidate_profile",
        owner_id=row.owner_id,
        record_id=row.id,
        encryption_key_id=row.encryption_key_id,
        ciphertext=row.encrypted_payload,
    )
    try:
        data = CandidateProfileWrite.model_validate(
            {**payload, "onboarding_step": row.onboarding_state}
        )
    except ValidationError as exc:
        raise PrivatePayloadBindingError("candidate profile payload is invalid") from exc
    return CandidateProfileRecord(
        id=row.id,
        owner_id=row.owner_id,
        data=data,
        version=row.version,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
    )


def _track_record(row: CareerTrack) -> CareerTrackRecord:
    return CareerTrackRecord(
        id=row.id,
        owner_id=row.owner_id,
        data=CareerTrackCreate(
            name=row.name,
            role_families=row.role_families,
            seniority_levels=row.seniority_levels,
            target_locations=row.target_locations,
            priorities=row.priorities,
            active=row.active,
        ),
        version=row.version,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
    )


def _resume_metadata(row: ResumeVersion) -> ResumeVersionMetadata:
    return ResumeVersionMetadata(
        id=row.id,
        owner_id=row.owner_id,
        parent_id=row.parent_id,
        label=row.label,
        content_hash=row.content_hash,
        source=row.source,
        is_base=row.is_base,
        version=row.version,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
    )


def _make_base_locked(session: Session, target: ResumeVersion, *, now: datetime) -> None:
    rows = list(
        session.scalars(
            select(ResumeVersion)
            .where(ResumeVersion.owner_id == target.owner_id)
            .with_for_update()
        )
    )
    for row in rows:
        desired = row.id == target.id
        if row.is_base != desired:
            row.is_base = desired
            row.version += 1
            row.updated_at = now


def _owner_track(session: Session, owner_id: str, track_id: str) -> CareerTrack | None:
    return session.scalar(
        select(CareerTrack).where(CareerTrack.owner_id == owner_id, CareerTrack.id == track_id)
    )


def _owner_resume(session: Session, owner_id: str, resume_id: str) -> ResumeVersion | None:
    return session.scalar(
        select(ResumeVersion).where(
            ResumeVersion.owner_id == owner_id, ResumeVersion.id == resume_id
        )
    )


def _guard_active_search_seniority(
    session: Session, track: CareerTrack, allowed_levels: set[str]
) -> None:
    searches = session.scalars(
        select(SavedSearch).where(
            SavedSearch.owner_id == track.owner_id,
            SavedSearch.career_track_id == track.id,
            SavedSearch.active.is_(True),
        )
    )
    if any(str(search.criteria.get("seniority")) not in allowed_levels for search in searches):
        raise ResourceInUse("active saved search uses a removed seniority level")


def _resume_content_hash(owner_id: str, content: str) -> str:
    """Owner-scoped equality fingerprint; never expose it as a public content digest."""

    return hashlib.sha256(f"{owner_id}\0{content}".encode("utf-8")).hexdigest()


def _normalized_tokens(values: list[str], *, field: str, limit: int) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        token = " ".join(raw.split())
        if not token or len(token) > limit:
            raise ValueError(f"{field} values must be 1-{limit} characters")
        key = token.casefold()
        if key not in seen:
            normalized.append(token)
            seen.add(key)
    return normalized


def _require_owner(session: Session, owner_id: str) -> None:
    if session.get(Owner, owner_id) is None:
        raise ValueError("owner_id does not exist")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "CandidateProfileInput",
    "CandidateProfileRecord",
    "CareerTrackInput",
    "CareerTrackRecord",
    "CreateResumeResult",
    "ResumeVersionContent",
    "ResumeVersionMetadata",
    "create_career_track",
    "create_or_reuse_resume_version",
    "delete_career_track",
    "delete_resume_version",
    "list_career_tracks",
    "list_resume_versions",
    "load_candidate_profile",
    "load_career_track",
    "load_resume_version",
    "set_base_resume_version",
    "update_career_track",
    "upsert_candidate_profile",
]
