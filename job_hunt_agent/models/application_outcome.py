"""Immutable, owner-scoped terminal application outcomes."""

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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _uuid_hex() -> str:
    return uuid4().hex


class ApplicationOutcome(Base):
    """One manually recorded terminal result for an application."""

    __tablename__ = "application_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "id", name="uq_application_outcomes_owner_id_id"
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "id",
            name="uq_application_outcomes_owner_application_id",
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            name="uq_application_outcomes_owner_application",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_application_outcomes_owner_application",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "application_submission_id"],
            [
                "application_submissions.owner_id",
                "application_submissions.application_id",
                "application_submissions.id",
            ],
            name="fk_application_outcomes_owner_submission",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "stage_at_outcome IN ('pursuing', 'ready_to_apply', 'applied', "
            "'screening', 'interviewing', 'offer')",
            name="stage_at_outcome",
        ),
        CheckConstraint(
            "outcome IN ('rejected', 'withdrawn', 'offer_accepted', "
            "'offer_declined', 'no_response', 'posting_closed')",
            name="outcome",
        ),
        CheckConstraint("recording_method = 'manual'", name="recording_method"),
        CheckConstraint(
            "((stage_at_outcome IN ('pursuing', 'ready_to_apply') "
            "AND outcome IN ('withdrawn', 'posting_closed') "
            "AND application_submission_id IS NULL) OR "
            "(stage_at_outcome IN ('applied', 'screening', 'interviewing', 'offer') "
            "AND application_submission_id IS NOT NULL))",
            name="submission_shape",
        ),
        CheckConstraint(
            "(outcome IN ('offer_accepted', 'offer_declined') "
            "AND stage_at_outcome = 'offer') OR "
            "outcome NOT IN ('offer_accepted', 'offer_declined')",
            name="offer_outcome_stage",
        ),
        Index(
            "ix_application_outcomes_owner_metrics",
            "owner_id",
            "outcome",
            "outcome_on",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String(32), nullable=False)
    application_submission_id: Mapped[str | None] = mapped_column(String(32))
    stage_at_outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome_on: Mapped[date] = mapped_column(Date, nullable=False)
    recording_method: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual"
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["ApplicationOutcome"]
