"""Owner-scoped interview appointments and append-only round history."""

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


class ApplicationInterviewRound(Base):
    """One stable interview round; only its current lifecycle projection mutates."""

    __tablename__ = "application_interview_rounds"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "id", name="uq_application_interview_rounds_owner_id_id"
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "id",
            name="uq_application_interview_rounds_owner_application_id",
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "round_number",
            name="uq_application_interview_rounds_owner_number",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_application_interview_rounds_owner_application",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "application_submission_id"],
            [
                "application_submissions.owner_id",
                "application_submissions.application_id",
                "application_submissions.id",
            ],
            name="fk_application_interview_rounds_owner_submission",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("round_number >= 1", name="round_number_positive"),
        CheckConstraint(
            "kind IN ('hiring_manager', 'technical', 'system_design', "
            "'behavioral', 'case_study', 'panel', 'final', 'other')",
            name="kind",
        ),
        CheckConstraint(
            "length(trim(title)) BETWEEN 1 AND 160", name="title_length"
        ),
        CheckConstraint(
            "status IN ('scheduled', 'completed', 'cancelled')", name="status"
        ),
        CheckConstraint(
            "duration_minutes BETWEEN 15 AND 480", name="duration_minutes"
        ),
        CheckConstraint(
            "length(trim(scheduled_timezone)) BETWEEN 1 AND 64",
            name="scheduled_timezone_length",
        ),
        CheckConstraint(
            "meeting_format IN ('video', 'phone', 'onsite', 'unspecified')",
            name="meeting_format",
        ),
        CheckConstraint(
            "cancelled_by IS NULL OR cancelled_by IN "
            "('employer', 'candidate', 'mutual', 'unknown')",
            name="cancelled_by",
        ),
        CheckConstraint(
            "(status = 'scheduled' AND completed_on IS NULL "
            "AND cancelled_on IS NULL AND cancelled_by IS NULL) OR "
            "(status = 'completed' AND completed_on IS NOT NULL "
            "AND cancelled_on IS NULL AND cancelled_by IS NULL) OR "
            "(status = 'cancelled' AND cancelled_on IS NOT NULL "
            "AND cancelled_by IS NOT NULL AND completed_on IS NULL)",
            name="status_shape",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_application_interview_rounds_owner_scheduled",
            "owner_id",
            "application_id",
            unique=True,
            sqlite_where=text("status = 'scheduled'"),
            postgresql_where=text("status = 'scheduled'"),
        ),
        Index(
            "ix_application_interview_rounds_owner_schedule",
            "owner_id",
            "status",
            "scheduled_start_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String(32), nullable=False)
    application_submission_id: Mapped[str] = mapped_column(String(32), nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")
    scheduled_start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    scheduled_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    meeting_format: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unspecified"
    )
    completed_on: Mapped[date | None] = mapped_column(Date)
    cancelled_on: Mapped[date | None] = mapped_column(Date)
    cancelled_by: Mapped[str | None] = mapped_column(String(20))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ApplicationInterviewRoundEvent(Base):
    """Immutable schedule, reschedule, completion, or cancellation fact."""

    __tablename__ = "application_interview_round_events"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "id",
            name="uq_application_interview_round_events_owner_id_id",
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "interview_round_id",
            "sequence_number",
            name="uq_application_interview_round_events_owner_sequence",
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "idempotency_key_hash",
            name="uq_application_interview_round_events_owner_mutation",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "interview_round_id"],
            [
                "application_interview_rounds.owner_id",
                "application_interview_rounds.application_id",
                "application_interview_rounds.id",
            ],
            name="fk_application_interview_round_events_owner_round",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "previous_action_item_id"],
            ["action_items.owner_id", "action_items.application_id", "action_items.id"],
            name="fk_application_interview_round_events_owner_previous_action",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "action_item_id"],
            ["action_items.owner_id", "action_items.application_id", "action_items.id"],
            name="fk_application_interview_round_events_owner_action",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("sequence_number >= 1", name="sequence_positive"),
        CheckConstraint(
            "event_type IN ('scheduled', 'rescheduled', 'completed', 'cancelled')",
            name="event_type",
        ),
        CheckConstraint(
            "from_status IS NULL OR from_status = 'scheduled'", name="from_status"
        ),
        CheckConstraint(
            "to_status IN ('scheduled', 'completed', 'cancelled')", name="to_status"
        ),
        CheckConstraint(
            "duration_minutes BETWEEN 15 AND 480", name="duration_minutes"
        ),
        CheckConstraint(
            "length(trim(scheduled_timezone)) BETWEEN 1 AND 64",
            name="scheduled_timezone_length",
        ),
        CheckConstraint(
            "meeting_format IN ('video', 'phone', 'onsite', 'unspecified')",
            name="meeting_format",
        ),
        CheckConstraint(
            "cancelled_by IS NULL OR cancelled_by IN "
            "('employer', 'candidate', 'mutual', 'unknown')",
            name="cancelled_by",
        ),
        CheckConstraint(
            "(event_type = 'scheduled' AND sequence_number = 1 "
            "AND from_status IS NULL AND to_status = 'scheduled' "
            "AND effective_on IS NULL AND cancelled_by IS NULL) OR "
            "(event_type = 'rescheduled' AND sequence_number >= 2 "
            "AND from_status = 'scheduled' AND to_status = 'scheduled' "
            "AND effective_on IS NULL AND cancelled_by IS NULL) OR "
            "(event_type = 'completed' AND sequence_number >= 2 "
            "AND from_status = 'scheduled' AND to_status = 'completed' "
            "AND effective_on IS NOT NULL AND cancelled_by IS NULL) OR "
            "(event_type = 'cancelled' AND sequence_number >= 2 "
            "AND from_status = 'scheduled' AND to_status = 'cancelled' "
            "AND effective_on IS NOT NULL AND cancelled_by IS NOT NULL)",
            name="event_shape",
        ),
        CheckConstraint(
            "previous_action_item_id <> action_item_id", name="action_replaced"
        ),
        CheckConstraint("recording_method = 'manual'", name="recording_method"),
        CheckConstraint("length(idempotency_key_hash) = 64", name="mutation_hash"),
        Index(
            "uq_application_interview_round_events_owner_terminal",
            "owner_id",
            "application_id",
            "interview_round_id",
            unique=True,
            sqlite_where=text("event_type IN ('completed', 'cancelled')"),
            postgresql_where=text("event_type IN ('completed', 'cancelled')"),
        ),
        Index(
            "ix_application_interview_round_events_timeline",
            "owner_id",
            "application_id",
            "interview_round_id",
            "sequence_number",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String(32), nullable=False)
    interview_round_id: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    scheduled_start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    scheduled_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    meeting_format: Mapped[str] = mapped_column(String(20), nullable=False)
    effective_on: Mapped[date | None] = mapped_column(Date)
    cancelled_by: Mapped[str | None] = mapped_column(String(20))
    previous_action_item_id: Mapped[str] = mapped_column(String(32), nullable=False)
    action_item_id: Mapped[str] = mapped_column(String(32), nullable=False)
    recording_method: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual"
    )
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["ApplicationInterviewRound", "ApplicationInterviewRoundEvent"]
