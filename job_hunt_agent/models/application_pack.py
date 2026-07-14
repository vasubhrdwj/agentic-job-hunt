"""Durable, owner-scoped grounding reviews for one application."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _uuid_hex() -> str:
    return uuid4().hex


class ApplicationPack(Base):
    """One grounding aggregate pinned to an application's pursued posting."""

    __tablename__ = "application_packs"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_application_packs_owner_id_id"),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "id",
            name="uq_application_packs_owner_application_id",
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            name="uq_application_packs_owner_application",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_application_packs_owner_application",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "job_posting_id", "posting_version_id"],
            [
                "job_posting_versions.owner_id",
                "job_posting_versions.job_posting_id",
                "job_posting_versions.id",
            ],
            name="fk_application_packs_owner_posting_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "base_resume_version_id"],
            ["resume_versions.owner_id", "resume_versions.id"],
            name="fk_application_packs_owner_base_resume",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_application_packs_owner_updated",
            "owner_id",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String(32), nullable=False)
    job_posting_id: Mapped[str] = mapped_column(String(32), nullable=False)
    posting_version_id: Mapped[str] = mapped_column(String(32), nullable=False)
    base_resume_version_id: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ApplicationPackRevision(Base):
    """An immutable encrypted requirement/evidence review snapshot."""

    __tablename__ = "application_pack_revisions"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "id", name="uq_application_pack_revisions_owner_id_id"
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "application_pack_id",
            "id",
            name="uq_application_pack_revisions_event_ref",
        ),
        UniqueConstraint(
            "owner_id",
            "application_pack_id",
            "revision_number",
            name="uq_application_pack_revisions_owner_number",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "application_pack_id"],
            [
                "application_packs.owner_id",
                "application_packs.application_id",
                "application_packs.id",
            ],
            name="fk_application_pack_revisions_owner_pack",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "application_pack_id", "parent_revision_id"],
            [
                "application_pack_revisions.owner_id",
                "application_pack_revisions.application_id",
                "application_pack_revisions.application_pack_id",
                "application_pack_revisions.id",
            ],
            name="fk_application_pack_revisions_owner_parent",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "parent_revision_id IS NULL OR parent_revision_id <> id",
            name="parent_not_self",
        ),
        CheckConstraint("revision_number >= 1", name="revision_number_positive"),
        CheckConstraint("source IN ('extracted', 'edited')", name="source"),
        CheckConstraint(
            "length(trim(encrypted_payload)) >= 1 "
            "AND length(trim(encryption_key_id)) BETWEEN 1 AND 32",
            name="encrypted_payload_envelope",
        ),
        CheckConstraint("length(content_hash) = 64", name="content_hash"),
        Index(
            "ix_application_pack_revisions_pack_created",
            "owner_id",
            "application_pack_id",
            "revision_number",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String(32), nullable=False)
    application_pack_id: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_revision_id: Mapped[str | None] = mapped_column(String(32))
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ApplicationPackEvent(Base):
    """Append-only confirmation of the exact reviewed grounding revision."""

    __tablename__ = "application_pack_events"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "id", name="uq_application_pack_events_owner_id_id"
        ),
        UniqueConstraint(
            "owner_id",
            "application_pack_id",
            "sequence_number",
            name="uq_application_pack_events_owner_sequence",
        ),
        UniqueConstraint(
            "owner_id",
            "application_pack_id",
            "revision_id",
            "event_type",
            name="uq_application_pack_events_owner_reviewed",
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "application_pack_id",
            "revision_id",
            "id",
            name="uq_application_pack_events_submission_ref",
        ),
        UniqueConstraint(
            "owner_id",
            "application_pack_id",
            "idempotency_key_hash",
            name="uq_application_pack_events_owner_mutation",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "application_pack_id"],
            [
                "application_packs.owner_id",
                "application_packs.application_id",
                "application_packs.id",
            ],
            name="fk_application_pack_events_owner_pack",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "application_pack_id", "revision_id"],
            [
                "application_pack_revisions.owner_id",
                "application_pack_revisions.application_id",
                "application_pack_revisions.application_pack_id",
                "application_pack_revisions.id",
            ],
            name="fk_application_pack_events_owner_revision",
            ondelete="RESTRICT",
        ),
        CheckConstraint("sequence_number >= 1", name="sequence_number_positive"),
        CheckConstraint("event_type = 'reviewed'", name="event_type"),
        CheckConstraint("length(idempotency_key_hash) = 64", name="mutation_hash"),
        Index(
            "ix_application_pack_events_timeline",
            "owner_id",
            "application_pack_id",
            "occurred_at",
            "sequence_number",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String(32), nullable=False)
    application_pack_id: Mapped[str] = mapped_column(String(32), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False, default="reviewed")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["ApplicationPack", "ApplicationPackEvent", "ApplicationPackRevision"]
