"""Immutable owner-scoped records of exact manual job applications."""

from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _uuid_hex() -> str:
    return uuid4().hex


class ApplicationSubmission(Base):
    """One immutable assertion of the exact materials manually submitted."""

    __tablename__ = "application_submissions"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "id", name="uq_application_submissions_owner_id_id"
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "id",
            name="uq_application_submissions_owner_application_id",
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            name="uq_application_submissions_owner_application",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_application_submissions_owner_application",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "application_pack_id"],
            [
                "application_packs.owner_id",
                "application_packs.application_id",
                "application_packs.id",
            ],
            name="fk_application_submissions_owner_pack",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "application_id",
                "application_pack_id",
                "application_pack_revision_id",
            ],
            [
                "application_pack_revisions.owner_id",
                "application_pack_revisions.application_id",
                "application_pack_revisions.application_pack_id",
                "application_pack_revisions.id",
            ],
            name="fk_application_submissions_owner_pack_revision",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "application_id",
                "application_pack_id",
                "application_pack_revision_id",
                "application_pack_review_event_id",
            ],
            [
                "application_pack_events.owner_id",
                "application_pack_events.application_id",
                "application_pack_events.application_pack_id",
                "application_pack_events.revision_id",
                "application_pack_events.id",
            ],
            name="fk_application_submissions_owner_pack_review",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "application_id",
                "application_pack_id",
                "application_artifact_revision_id",
            ],
            [
                "application_artifact_revisions.owner_id",
                "application_artifact_revisions.application_id",
                "application_artifact_revisions.application_pack_id",
                "application_artifact_revisions.id",
            ],
            name="fk_application_submissions_owner_artifact_revision",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "application_id",
                "application_pack_id",
                "application_artifact_revision_id",
                "application_artifact_approval_event_id",
            ],
            [
                "application_artifact_events.owner_id",
                "application_artifact_events.application_id",
                "application_artifact_events.application_pack_id",
                "application_artifact_events.artifact_revision_id",
                "application_artifact_events.id",
            ],
            name="fk_application_submissions_owner_artifact_approval",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["owner_id", "tailored_resume_version_id"],
            ["resume_versions.owner_id", "resume_versions.id"],
            name="fk_application_submissions_owner_tailored_resume",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("submission_method = 'manual'", name="submission_method"),
        CheckConstraint(
            "length(destination_url) BETWEEN 9 AND 2048 "
            "AND destination_url LIKE 'https://%'",
            name="destination_url",
        ),
        Index(
            "ix_application_submissions_owner_applied",
            "owner_id",
            "applied_on",
            "recorded_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String(32), nullable=False)
    application_pack_id: Mapped[str] = mapped_column(String(32), nullable=False)
    application_pack_revision_id: Mapped[str] = mapped_column(String(32), nullable=False)
    application_pack_review_event_id: Mapped[str] = mapped_column(String(32), nullable=False)
    application_artifact_revision_id: Mapped[str] = mapped_column(String(32), nullable=False)
    application_artifact_approval_event_id: Mapped[str] = mapped_column(
        String(32), nullable=False
    )
    tailored_resume_version_id: Mapped[str] = mapped_column(String(32), nullable=False)
    destination_url: Mapped[str] = mapped_column(Text, nullable=False)
    applied_on: Mapped[date] = mapped_column(Date, nullable=False)
    submission_method: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual"
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["ApplicationSubmission"]
