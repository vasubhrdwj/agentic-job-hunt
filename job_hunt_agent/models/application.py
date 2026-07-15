"""Owner-scoped application pursuit and next-action records."""

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


class Application(Base):
    """One controlled pursuit per owner opportunity."""

    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_applications_owner_id_id"),
        UniqueConstraint(
            "owner_id",
            "owner_opportunity_id",
            name="uq_applications_owner_opportunity",
        ),
        ForeignKeyConstraint(
            ["owner_id", "owner_opportunity_id", "job_posting_id"],
            [
                "owner_opportunities.owner_id",
                "owner_opportunities.id",
                "owner_opportunities.job_posting_id",
            ],
            name="fk_applications_owner_opportunity",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "job_posting_id", "pursued_posting_version_id"],
            [
                "job_posting_versions.owner_id",
                "job_posting_versions.job_posting_id",
                "job_posting_versions.id",
            ],
            name="fk_applications_owner_posting_version",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["owner_id", "id", "outcome_id"],
            [
                "application_outcomes.owner_id",
                "application_outcomes.application_id",
                "application_outcomes.id",
            ],
            name="fk_applications_owner_outcome",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        CheckConstraint(
            "stage IN ('pursuing', 'ready_to_apply', 'applied', 'screening', "
            "'interviewing', 'offer', 'closed')",
            name="stage",
        ),
        CheckConstraint(
            "(stage = 'closed' AND outcome_id IS NOT NULL) OR "
            "(stage <> 'closed' AND outcome_id IS NULL)",
            name="outcome_shape",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_applications_owner_stage", "owner_id", "stage", "updated_at"),
        Index(
            "uq_applications_metric_snapshot_target",
            "owner_id",
            "id",
            "job_posting_id",
            "pursued_posting_version_id",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    owner_opportunity_id: Mapped[str] = mapped_column(String(32), nullable=False)
    job_posting_id: Mapped[str] = mapped_column(String(32), nullable=False)
    pursued_posting_version_id: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome_id: Mapped[str | None] = mapped_column(String(32))
    stage: Mapped[str] = mapped_column(String(24), nullable=False, default="pursuing")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ActionItem(Base):
    """A dated, mutable next action for one application."""

    __tablename__ = "action_items"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_action_items_owner_id_id"),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "id",
            name="uq_action_items_owner_application_id",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_action_items_owner_application",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "interview_round_id"],
            [
                "application_interview_rounds.owner_id",
                "application_interview_rounds.application_id",
                "application_interview_rounds.id",
            ],
            name="fk_action_items_owner_interview_round",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        CheckConstraint(
            "kind IN ('review_and_prepare_application', 'submit_application', "
            "'follow_up_application', 'prepare_recruiter_screen', "
            "'prepare_interview', 'review_offer')",
            name="kind",
        ),
        CheckConstraint(
            "length(trim(title)) BETWEEN 1 AND 240", name="title_length"
        ),
        CheckConstraint(
            "status IN ('open', 'completed', 'cancelled')", name="status"
        ),
        CheckConstraint(
            "(status = 'open' AND completed_at IS NULL AND cancelled_at IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL "
            "AND cancelled_at IS NULL) OR "
            "(status = 'cancelled' AND cancelled_at IS NOT NULL "
            "AND completed_at IS NULL)",
            name="status_timestamps",
        ),
        CheckConstraint(
            "interview_round_id IS NULL OR kind = 'prepare_interview'",
            name="interview_round_kind",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_action_items_owner_application_open",
            "owner_id",
            "application_id",
            unique=True,
            sqlite_where=text("status = 'open'"),
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_action_items_owner_due", "owner_id", "status", "due_on"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String(32), nullable=False)
    interview_round_id: Mapped[str | None] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(
        String(64), nullable=False, default="review_and_prepare_application"
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ApplicationActivityEvent(Base):
    """Append-only application timeline event."""

    __tablename__ = "application_activity_events"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "id", name="uq_application_activity_events_owner_id_id"
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "id",
            name="uq_application_activity_events_owner_application_id",
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "sequence_number",
            name="uq_application_activity_events_owner_sequence",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_application_activity_events_owner_application",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "action_item_id"],
            ["action_items.owner_id", "action_items.application_id", "action_items.id"],
            name="fk_application_activity_events_owner_action",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "previous_action_item_id"],
            ["action_items.owner_id", "action_items.application_id", "action_items.id"],
            name="fk_application_activity_events_owner_previous_action",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "submission_id"],
            [
                "application_submissions.owner_id",
                "application_submissions.application_id",
                "application_submissions.id",
            ],
            name="fk_application_activity_events_owner_submission",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "outcome_id"],
            [
                "application_outcomes.owner_id",
                "application_outcomes.application_id",
                "application_outcomes.id",
            ],
            name="fk_application_activity_events_owner_outcome",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "interview_round_id"],
            [
                "application_interview_rounds.owner_id",
                "application_interview_rounds.application_id",
                "application_interview_rounds.id",
            ],
            name="fk_application_activity_events_owner_interview_round",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        CheckConstraint("sequence_number >= 1", name="sequence_positive"),
        CheckConstraint(
            "event_type IN ('application_created', 'application_ready_to_apply', "
            "'application_applied', 'application_screening', "
            "'application_interviewing', 'application_offer', "
            "'application_closed')",
            name="event_type",
        ),
        CheckConstraint(
            "(event_type = 'application_created' AND sequence_number = 1 "
            "AND from_stage IS NULL AND to_stage = 'pursuing' "
            "AND action_item_id IS NOT NULL "
            "AND previous_action_item_id IS NULL AND submission_id IS NULL "
            "AND effective_on IS NULL AND outcome_id IS NULL "
            "AND interview_round_id IS NULL) OR "
            "(event_type = 'application_ready_to_apply' AND sequence_number = 2 "
            "AND from_stage = 'pursuing' AND to_stage = 'ready_to_apply' "
            "AND action_item_id IS NOT NULL "
            "AND previous_action_item_id IS NOT NULL "
            "AND previous_action_item_id <> action_item_id "
            "AND submission_id IS NULL AND effective_on IS NULL "
            "AND outcome_id IS NULL AND interview_round_id IS NULL) OR "
            "(event_type = 'application_applied' AND sequence_number = 3 "
            "AND from_stage = 'ready_to_apply' AND to_stage = 'applied' "
            "AND action_item_id IS NOT NULL "
            "AND previous_action_item_id IS NOT NULL "
            "AND previous_action_item_id <> action_item_id "
            "AND submission_id IS NOT NULL AND effective_on IS NULL "
            "AND outcome_id IS NULL AND interview_round_id IS NULL) OR "
            "(event_type = 'application_screening' AND sequence_number >= 4 "
            "AND from_stage = 'applied' AND to_stage = 'screening' "
            "AND action_item_id IS NOT NULL "
            "AND previous_action_item_id IS NOT NULL "
            "AND previous_action_item_id <> action_item_id "
            "AND submission_id IS NULL AND effective_on IS NOT NULL "
            "AND outcome_id IS NULL AND interview_round_id IS NULL) OR "
            "(event_type = 'application_interviewing' AND sequence_number >= 4 "
            "AND from_stage IN ('applied', 'screening') "
            "AND to_stage = 'interviewing' AND action_item_id IS NOT NULL "
            "AND previous_action_item_id IS NOT NULL "
            "AND previous_action_item_id <> action_item_id "
            "AND submission_id IS NULL AND effective_on IS NOT NULL "
            "AND outcome_id IS NULL) OR "
            "(event_type = 'application_offer' AND sequence_number >= 4 "
            "AND from_stage IN ('applied', 'screening', 'interviewing') "
            "AND to_stage = 'offer' AND action_item_id IS NOT NULL "
            "AND previous_action_item_id IS NOT NULL "
            "AND previous_action_item_id <> action_item_id "
            "AND submission_id IS NULL AND effective_on IS NOT NULL "
            "AND outcome_id IS NULL AND interview_round_id IS NULL) OR "
            "(event_type = 'application_closed' AND sequence_number >= 2 "
            "AND from_stage IN ('pursuing', 'ready_to_apply', 'applied', "
            "'screening', 'interviewing', 'offer') AND to_stage = 'closed' "
            "AND action_item_id IS NULL AND previous_action_item_id IS NOT NULL "
            "AND submission_id IS NULL AND effective_on IS NOT NULL "
            "AND outcome_id IS NOT NULL AND interview_round_id IS NULL)",
            name="event_shape",
        ),
        Index(
            "uq_application_activity_events_owner_created",
            "owner_id",
            "application_id",
            unique=True,
            sqlite_where=text("event_type = 'application_created'"),
            postgresql_where=text("event_type = 'application_created'"),
        ),
        Index(
            "uq_application_activity_events_owner_ready",
            "owner_id",
            "application_id",
            unique=True,
            sqlite_where=text("event_type = 'application_ready_to_apply'"),
            postgresql_where=text("event_type = 'application_ready_to_apply'"),
        ),
        Index(
            "uq_application_activity_events_owner_applied",
            "owner_id",
            "application_id",
            unique=True,
            sqlite_where=text("event_type = 'application_applied'"),
            postgresql_where=text("event_type = 'application_applied'"),
        ),
        Index(
            "uq_application_activity_events_owner_submission",
            "owner_id",
            "submission_id",
            unique=True,
            sqlite_where=text("submission_id IS NOT NULL"),
            postgresql_where=text("submission_id IS NOT NULL"),
        ),
        Index(
            "uq_application_activity_events_owner_screening",
            "owner_id",
            "application_id",
            unique=True,
            sqlite_where=text("event_type = 'application_screening'"),
            postgresql_where=text("event_type = 'application_screening'"),
        ),
        Index(
            "uq_application_activity_events_owner_interviewing",
            "owner_id",
            "application_id",
            unique=True,
            sqlite_where=text("event_type = 'application_interviewing'"),
            postgresql_where=text("event_type = 'application_interviewing'"),
        ),
        Index(
            "uq_application_activity_events_owner_offer",
            "owner_id",
            "application_id",
            unique=True,
            sqlite_where=text("event_type = 'application_offer'"),
            postgresql_where=text("event_type = 'application_offer'"),
        ),
        Index(
            "uq_application_activity_events_owner_closed",
            "owner_id",
            "application_id",
            unique=True,
            sqlite_where=text("event_type = 'application_closed'"),
            postgresql_where=text("event_type = 'application_closed'"),
        ),
        Index(
            "uq_application_activity_events_owner_outcome",
            "owner_id",
            "outcome_id",
            unique=True,
            sqlite_where=text("outcome_id IS NOT NULL"),
            postgresql_where=text("outcome_id IS NOT NULL"),
        ),
        Index(
            "ix_application_activity_events_timeline",
            "application_id",
            "occurred_at",
            "sequence_number",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    from_stage: Mapped[str | None] = mapped_column(String(24))
    to_stage: Mapped[str] = mapped_column(String(24), nullable=False)
    action_item_id: Mapped[str | None] = mapped_column(String(32))
    previous_action_item_id: Mapped[str | None] = mapped_column(String(32))
    submission_id: Mapped[str | None] = mapped_column(String(32))
    effective_on: Mapped[date | None] = mapped_column(Date)
    outcome_id: Mapped[str | None] = mapped_column(String(32))
    interview_round_id: Mapped[str | None] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["ActionItem", "Application", "ApplicationActivityEvent"]
