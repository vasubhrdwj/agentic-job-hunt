"""Immutable, owner-scoped application artifacts built from reviewed grounding."""

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


class ApplicationArtifactRevision(Base):
    """One immutable deterministic application-material snapshot."""

    __tablename__ = "application_artifact_revisions"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "id", name="uq_application_artifact_revisions_owner_id_id"
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "application_pack_id",
            "id",
            name="uq_application_artifact_revisions_event_ref",
        ),
        UniqueConstraint(
            "owner_id",
            "application_pack_id",
            "revision_number",
            name="uq_application_artifact_revisions_owner_number",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "application_pack_id"],
            [
                "application_packs.owner_id",
                "application_packs.application_id",
                "application_packs.id",
            ],
            name="fk_application_artifact_revisions_owner_pack",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "application_id",
                "application_pack_id",
                "grounding_revision_id",
            ],
            [
                "application_pack_revisions.owner_id",
                "application_pack_revisions.application_id",
                "application_pack_revisions.application_pack_id",
                "application_pack_revisions.id",
            ],
            name="fk_application_artifact_revisions_owner_grounding",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "application_id",
                "application_pack_id",
                "parent_artifact_revision_id",
            ],
            [
                "application_artifact_revisions.owner_id",
                "application_artifact_revisions.application_id",
                "application_artifact_revisions.application_pack_id",
                "application_artifact_revisions.id",
            ],
            name="fk_application_artifact_revisions_owner_parent",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "parent_artifact_revision_id IS NULL OR parent_artifact_revision_id <> id",
            name="parent_not_self",
        ),
        CheckConstraint("revision_number >= 1", name="revision_number_positive"),
        CheckConstraint("source = 'deterministic'", name="source"),
        CheckConstraint(
            "generator_version = 'application-artifacts-deterministic-v1'",
            name="generator_version",
        ),
        CheckConstraint(
            "length(trim(encrypted_payload)) >= 1 "
            "AND length(trim(encryption_key_id)) BETWEEN 1 AND 32",
            name="encrypted_payload_envelope",
        ),
        CheckConstraint("length(content_hash) = 64", name="content_hash"),
        Index(
            "ix_application_artifact_revisions_pack_created",
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
    grounding_revision_id: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_artifact_revision_id: Mapped[str | None] = mapped_column(String(32))
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    generator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ApplicationArtifactEvent(Base):
    """Append-only approval or rejection of one exact artifact revision."""

    __tablename__ = "application_artifact_events"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "id", name="uq_application_artifact_events_owner_id_id"
        ),
        UniqueConstraint(
            "owner_id",
            "application_pack_id",
            "sequence_number",
            name="uq_application_artifact_events_owner_sequence",
        ),
        UniqueConstraint(
            "owner_id",
            "application_pack_id",
            "artifact_revision_id",
            name="uq_application_artifact_events_owner_terminal",
        ),
        UniqueConstraint(
            "owner_id",
            "application_pack_id",
            "idempotency_key_hash",
            name="uq_application_artifact_events_owner_mutation",
        ),
        # Phase 5C uses this exact event as a composite submission reference.
        UniqueConstraint(
            "owner_id",
            "application_id",
            "application_pack_id",
            "artifact_revision_id",
            "id",
            name="uq_application_artifact_events_submission_ref",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "application_pack_id"],
            [
                "application_packs.owner_id",
                "application_packs.application_id",
                "application_packs.id",
            ],
            name="fk_application_artifact_events_owner_pack",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "application_id",
                "application_pack_id",
                "artifact_revision_id",
            ],
            [
                "application_artifact_revisions.owner_id",
                "application_artifact_revisions.application_id",
                "application_artifact_revisions.application_pack_id",
                "application_artifact_revisions.id",
            ],
            name="fk_application_artifact_events_owner_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "tailored_resume_version_id"],
            ["resume_versions.owner_id", "resume_versions.id"],
            name="fk_application_artifact_events_owner_resume",
            ondelete="RESTRICT",
        ),
        CheckConstraint("sequence_number >= 1", name="sequence_number_positive"),
        CheckConstraint("event_type IN ('approved', 'rejected')", name="event_type"),
        CheckConstraint(
            "(event_type = 'approved' AND tailored_resume_version_id IS NOT NULL) OR "
            "(event_type = 'rejected' AND tailored_resume_version_id IS NULL)",
            name="event_resume_shape",
        ),
        CheckConstraint("length(idempotency_key_hash) = 64", name="mutation_hash"),
        Index(
            "ix_application_artifact_events_timeline",
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
    artifact_revision_id: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    tailored_resume_version_id: Mapped[str | None] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["ApplicationArtifactEvent", "ApplicationArtifactRevision"]
