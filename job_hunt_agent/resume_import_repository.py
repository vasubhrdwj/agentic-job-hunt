"""Immutable, owner-bound resume-upload result snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .job_queue import utcnow
from .models import ResumeImport, ResumeVersion
from .private_payloads import (
    PrivatePayloadBindingError,
    decrypt_private_payload,
    encrypt_private_payload,
)
from .profile_schemas import ResumeUploadReport
from .security import DataKeyring


@dataclass(frozen=True)
class ResumeImportSnapshot:
    id: str
    owner_id: str
    resume_version_id: str
    parser_version: str
    media_type: str
    page_count: int | None
    report: ResumeUploadReport
    version: int
    created_at: datetime


def create_resume_import(
    session: Session,
    *,
    owner_id: str,
    resume_version_id: str,
    parser_version: str,
    media_type: str,
    page_count: int | None,
    report: ResumeUploadReport,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> ResumeImportSnapshot:
    """Persist one immutable report without retaining the original file bytes."""

    normalized_parser_version = parser_version.strip()
    normalized_media_type = media_type.strip().casefold()
    if not 1 <= len(normalized_parser_version) <= 64:
        raise ValueError("resume parser version must be 1-64 characters")
    if not 1 <= len(normalized_media_type) <= 120:
        raise ValueError("resume media type must be 1-120 characters")
    if page_count is not None and not 1 <= page_count <= 1000:
        raise ValueError("resume page count must be between 1 and 1000")
    if report.resume_version.id != resume_version_id:
        raise ValueError("resume import report does not match its resume version")
    if session.scalar(
        select(ResumeVersion.id).where(
            ResumeVersion.owner_id == owner_id,
            ResumeVersion.id == resume_version_id,
        )
    ) is None:
        raise ValueError("resume version does not exist for owner")

    record_id = uuid4().hex
    envelope = encrypt_private_payload(
        keyring,
        record_kind="resume_import",
        owner_id=owner_id,
        record_id=record_id,
        payload={
            "resume_version_id": resume_version_id,
            "parser_version": normalized_parser_version,
            "media_type": normalized_media_type,
            "page_count": page_count,
            "report": report.model_dump(mode="json"),
        },
    )
    row = ResumeImport(
        id=record_id,
        owner_id=owner_id,
        resume_version_id=resume_version_id,
        parser_version=normalized_parser_version,
        media_type=normalized_media_type,
        page_count=page_count,
        encrypted_payload=envelope.ciphertext,
        encryption_key_id=envelope.key_id,
        version=1,
        created_at=now or utcnow(),
    )
    session.add(row)
    session.flush()
    return _snapshot(row, keyring)


def load_resume_import(
    session: Session,
    *,
    owner_id: str,
    resume_import_id: str,
    keyring: DataKeyring,
) -> ResumeImportSnapshot | None:
    row = session.scalar(
        select(ResumeImport).where(
            ResumeImport.owner_id == owner_id,
            ResumeImport.id == resume_import_id,
        )
    )
    return _snapshot(row, keyring) if row is not None else None


def _snapshot(row: ResumeImport, keyring: DataKeyring) -> ResumeImportSnapshot:
    payload = decrypt_private_payload(
        keyring,
        record_kind="resume_import",
        owner_id=row.owner_id,
        record_id=row.id,
        encryption_key_id=row.encryption_key_id,
        ciphertext=row.encrypted_payload,
    )
    expected_fields = {
        "resume_version_id",
        "parser_version",
        "media_type",
        "page_count",
        "report",
    }
    if set(payload) != expected_fields or not isinstance(payload.get("report"), dict):
        raise PrivatePayloadBindingError("resume import payload is invalid")
    try:
        report = ResumeUploadReport.model_validate(payload["report"])
    except ValidationError as exc:
        raise PrivatePayloadBindingError("resume import payload is invalid") from exc
    if (
        payload["resume_version_id"] != row.resume_version_id
        or payload["parser_version"] != row.parser_version
        or payload["media_type"] != row.media_type
        or payload["page_count"] != row.page_count
        or report.resume_version.id != row.resume_version_id
        or row.version != 1
    ):
        raise PrivatePayloadBindingError("resume import payload does not match record")
    return ResumeImportSnapshot(
        id=row.id,
        owner_id=row.owner_id,
        resume_version_id=row.resume_version_id,
        parser_version=row.parser_version,
        media_type=row.media_type,
        page_count=row.page_count,
        report=report,
        version=row.version,
        created_at=_as_utc(row.created_at),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "ResumeImportSnapshot",
    "create_resume_import",
    "load_resume_import",
]
