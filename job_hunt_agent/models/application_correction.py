"""Append-only corrections for coarse application milestone dates."""

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
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _uuid_hex() -> str:
    return uuid4().hex


class ApplicationMilestoneCorrection(Base):
    """One immutable replacement for a previously effective milestone date."""

    __tablename__ = "application_milestone_corrections"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "id",
            name="uq_application_milestone_corrections_owner_id_id",
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "activity_event_id",
            "id",
            name="uq_application_milestone_corrections_owner_event_id",
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "activity_event_id",
            "correction_number",
            name="uq_application_milestone_corrections_owner_number",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_application_milestone_corrections_owner_application",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "activity_event_id"],
            [
                "application_activity_events.owner_id",
                "application_activity_events.application_id",
                "application_activity_events.id",
            ],
            name="fk_application_milestone_corrections_owner_activity",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "application_id",
                "activity_event_id",
                "supersedes_correction_id",
            ],
            [
                "application_milestone_corrections.owner_id",
                "application_milestone_corrections.application_id",
                "application_milestone_corrections.activity_event_id",
                "application_milestone_corrections.id",
            ],
            name="fk_application_milestone_corrections_owner_supersedes",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "correction_number BETWEEN 1 AND 50", name="number_range"
        ),
        CheckConstraint(
            "(correction_number = 1 AND supersedes_correction_id IS NULL) OR "
            "(correction_number >= 2 AND supersedes_correction_id IS NOT NULL)",
            name="chain_shape",
        ),
        CheckConstraint(
            "previous_effective_on <> corrected_effective_on",
            name="date_changed",
        ),
        CheckConstraint("recording_method = 'manual'", name="recording_method"),
        Index(
            "uq_application_milestone_corrections_owner_supersedes",
            "owner_id",
            "application_id",
            "activity_event_id",
            "supersedes_correction_id",
            unique=True,
            sqlite_where=text("supersedes_correction_id IS NOT NULL"),
            postgresql_where=text("supersedes_correction_id IS NOT NULL"),
        ),
        Index(
            "ix_application_milestone_corrections_timeline",
            "owner_id",
            "application_id",
            "activity_event_id",
            "correction_number",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String(32), nullable=False)
    activity_event_id: Mapped[str] = mapped_column(String(32), nullable=False)
    correction_number: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_correction_id: Mapped[str | None] = mapped_column(String(32))
    previous_effective_on: Mapped[date] = mapped_column(Date, nullable=False)
    corrected_effective_on: Mapped[date] = mapped_column(Date, nullable=False)
    recording_method: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual"
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["ApplicationMilestoneCorrection"]
