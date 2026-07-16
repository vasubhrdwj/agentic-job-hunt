"""Encrypted, owner-authored interview preparation revisions."""

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


class ApplicationInterviewPreparation(Base):
    """One versioned preparation notebook per application."""

    __tablename__ = "application_interview_preparations"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "id", name="uq_interview_preps_owner_id"
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "id",
            name="uq_interview_preps_owner_application_id",
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            name="uq_interview_preps_owner_application",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_interview_preps_owner_application",
            ondelete="CASCADE",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_interview_preps_owner_updated",
            "owner_id",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ApplicationInterviewPreparationRevision(Base):
    """Immutable encrypted STAR drafts tied to exact reviewed application facts."""

    __tablename__ = "application_interview_preparation_revisions"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "id",
            name="uq_interview_prep_revisions_owner_id",
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "preparation_id",
            "id",
            name="uq_interview_prep_revisions_event_ref",
        ),
        UniqueConstraint(
            "owner_id",
            "preparation_id",
            "revision_number",
            name="uq_interview_prep_revisions_owner_number",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "preparation_id"],
            [
                "application_interview_preparations.owner_id",
                "application_interview_preparations.application_id",
                "application_interview_preparations.id",
            ],
            name="fk_interview_prep_revisions_owner_prep",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "preparation_id", "parent_revision_id"],
            [
                "application_interview_preparation_revisions.owner_id",
                "application_interview_preparation_revisions.application_id",
                "application_interview_preparation_revisions.preparation_id",
                "application_interview_preparation_revisions.id",
            ],
            name="fk_interview_prep_revisions_owner_parent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "application_submission_id"],
            [
                "application_submissions.owner_id",
                "application_submissions.application_id",
                "application_submissions.id",
            ],
            name="fk_interview_prep_revisions_owner_submission",
            ondelete="RESTRICT",
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
            name="fk_interview_prep_revisions_owner_grounding",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "job_posting_id", "posting_version_id"],
            [
                "job_posting_versions.owner_id",
                "job_posting_versions.job_posting_id",
                "job_posting_versions.id",
            ],
            name="fk_interview_prep_revisions_owner_posting_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "interview_round_id"],
            [
                "application_interview_rounds.owner_id",
                "application_interview_rounds.application_id",
                "application_interview_rounds.id",
            ],
            name="fk_interview_prep_revisions_owner_round",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "parent_revision_id IS NULL OR parent_revision_id <> id",
            name="parent",
        ),
        CheckConstraint("revision_number >= 1", name="revision_positive"),
        CheckConstraint(
            "target_kind IN ('recruiter_screen', 'interview_round')",
            name="target",
        ),
        CheckConstraint(
            "(target_kind = 'recruiter_screen' AND interview_round_id IS NULL) OR "
            "(target_kind = 'interview_round' AND interview_round_id IS NOT NULL)",
            name="target_shape",
        ),
        CheckConstraint(
            "(target_kind = 'recruiter_screen' AND interview_round_version IS NULL) OR "
            "(target_kind = 'interview_round' AND interview_round_version >= 1)",
            name="target_version",
        ),
        CheckConstraint("recording_method = 'owner_authored'", name="recording"),
        CheckConstraint(
            "length(trim(encrypted_payload)) >= 1 "
            "AND length(trim(encryption_key_id)) BETWEEN 1 AND 32",
            name="envelope",
        ),
        CheckConstraint("length(source_fingerprint) = 64", name="source_hash"),
        CheckConstraint("length(content_hash) = 64", name="content_hash"),
        Index(
            "ix_interview_prep_revisions_timeline",
            "owner_id",
            "application_id",
            "preparation_id",
            "revision_number",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String(32), nullable=False)
    preparation_id: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_revision_id: Mapped[str | None] = mapped_column(String(32))
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    application_submission_id: Mapped[str] = mapped_column(String(32), nullable=False)
    application_pack_id: Mapped[str] = mapped_column(String(32), nullable=False)
    grounding_revision_id: Mapped[str] = mapped_column(String(32), nullable=False)
    job_posting_id: Mapped[str] = mapped_column(String(32), nullable=False)
    posting_version_id: Mapped[str] = mapped_column(String(32), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    interview_round_id: Mapped[str | None] = mapped_column(String(32))
    interview_round_version: Mapped[int | None] = mapped_column(Integer)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    recording_method: Mapped[str] = mapped_column(
        String(20), nullable=False, default="owner_authored"
    )
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "ApplicationInterviewPreparation",
    "ApplicationInterviewPreparationRevision",
]
