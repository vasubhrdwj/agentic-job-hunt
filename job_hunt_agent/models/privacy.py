"""Owner privacy preferences and payload-free deletion receipts."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _uuid_hex() -> str:
    return uuid4().hex


class OwnerPrivacySetting(Base):
    """Versioned retention choice for legacy encrypted hunt runs.

    Applications, resumes, opportunities, contacts, and outreach are retained
    until the owner explicitly deletes the workspace. Automatic retention is
    deliberately limited to legacy hunt runs, whose private payloads already
    have a bounded lifecycle.
    """

    __tablename__ = "owner_privacy_settings"
    __table_args__ = (
        CheckConstraint(
            "hunt_run_retention_days BETWEEN 1 AND 30",
            name="hunt_run_retention_days",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), primary_key=True
    )
    hunt_run_retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default="30"
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PrivacyDeletionReceipt(Base):
    """Durable idempotency tombstone that contains no owner data or raw keys."""

    __tablename__ = "privacy_deletion_receipts"
    __table_args__ = (
        UniqueConstraint(
            "owner_id_hash",
            "idempotency_key_hash",
            name="uq_privacy_deletion_receipts_owner_key",
        ),
        CheckConstraint("length(owner_id_hash) = 64", name="owner_hash"),
        CheckConstraint("length(idempotency_key_hash) = 64", name="idempotency_hash"),
        CheckConstraint("length(request_hash) = 64", name="request_hash"),
        Index("ix_privacy_deletion_receipts_deleted", "deleted_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["OwnerPrivacySetting", "PrivacyDeletionReceipt"]
