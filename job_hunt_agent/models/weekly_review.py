"""Immutable attribution and manual weekly-review history."""

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
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _uuid_hex() -> str:
    return uuid4().hex


class ApplicationMetricSnapshot(Base):
    """One immutable attribution snapshot captured when pursuit begins."""

    __tablename__ = "application_metric_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "id", name="uq_application_metric_snapshots_owner_id_id"
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            name="uq_application_metric_snapshots_owner_application",
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "application_id",
                "job_posting_id",
                "pursued_posting_version_id",
            ],
            [
                "applications.owner_id",
                "applications.id",
                "applications.job_posting_id",
                "applications.pursued_posting_version_id",
            ],
            name="fk_application_metric_snapshots_owner_application",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "job_posting_id", "pursued_posting_version_id"],
            [
                "job_posting_versions.owner_id",
                "job_posting_versions.job_posting_id",
                "job_posting_versions.id",
            ],
            name="fk_application_metric_snapshots_owner_posting_version",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["owner_id", "saved_search_id"],
            ["saved_searches.owner_id", "saved_searches.id"],
            name="fk_application_metric_snapshots_owner_saved_search",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "career_track_id"],
            ["career_tracks.owner_id", "career_tracks.id"],
            name="fk_application_metric_snapshots_owner_career_track",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "acquisition_source IN ('job_hunt_search', 'referral', "
            "'recruiter_inbound', 'direct_company', 'job_board', 'other')",
            name="acquisition_source",
        ),
        CheckConstraint(
            "attribution_status IN ('captured', 'attribution_missing')",
            name="attribution_status",
        ),
        CheckConstraint(
            "saved_search_version IS NULL OR saved_search_version >= 1",
            name="saved_search_version_positive",
        ),
        CheckConstraint(
            "career_track_version IS NULL OR career_track_version >= 1",
            name="career_track_version_positive",
        ),
        CheckConstraint(
            "(attribution_status = 'attribution_missing' "
            "AND saved_search_id IS NULL AND saved_search_version IS NULL "
            "AND saved_search_name IS NULL AND career_track_id IS NULL "
            "AND career_track_version IS NULL AND career_track_name IS NULL) OR "
            "(attribution_status = 'captured' "
            "AND acquisition_source = 'job_hunt_search' "
            "AND saved_search_id IS NOT NULL AND saved_search_version IS NOT NULL "
            "AND saved_search_name IS NOT NULL AND career_track_id IS NOT NULL "
            "AND career_track_version IS NOT NULL AND career_track_name IS NOT NULL) OR "
            "(attribution_status = 'captured' "
            "AND acquisition_source <> 'job_hunt_search' "
            "AND saved_search_id IS NULL AND saved_search_version IS NULL "
            "AND saved_search_name IS NULL AND career_track_id IS NULL "
            "AND career_track_version IS NULL AND career_track_name IS NULL)",
            name="attribution_shape",
        ),
        CheckConstraint(
            "assessment_state IN ('assessed', 'not_assessed')",
            name="assessment_state",
        ),
        CheckConstraint(
            "assessment_band IS NULL OR assessment_band IN ('strong', 'core', 'stretch')",
            name="assessment_band",
        ),
        CheckConstraint(
            "assessment_reason IS NULL OR assessment_reason IN ("
            "'assessment_pending', 'resume_unavailable', "
            "'description_unavailable', 'not_requested')",
            name="assessment_reason",
        ),
        CheckConstraint(
            "(assessment_state = 'assessed' AND assessment_band IS NOT NULL "
            "AND assessment_algorithm_version IS NOT NULL "
            "AND assessment_reason IS NULL) OR "
            "(assessment_state = 'not_assessed' AND assessment_band IS NULL "
            "AND assessment_algorithm_version IS NULL "
            "AND assessment_reason IS NOT NULL)",
            name="assessment_shape",
        ),
        Index(
            "ix_application_metric_snapshots_owner_recorded",
            "owner_id",
            "recorded_at",
            "application_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String(32), nullable=False)
    job_posting_id: Mapped[str] = mapped_column(String(32), nullable=False)
    pursued_posting_version_id: Mapped[str] = mapped_column(String(32), nullable=False)
    acquisition_source: Mapped[str] = mapped_column(String(32), nullable=False)
    attribution_status: Mapped[str] = mapped_column(String(32), nullable=False)
    saved_search_id: Mapped[str | None] = mapped_column(String(32))
    saved_search_version: Mapped[int | None] = mapped_column(Integer)
    saved_search_name: Mapped[str | None] = mapped_column(String(120))
    career_track_id: Mapped[str | None] = mapped_column(String(32))
    career_track_version: Mapped[int | None] = mapped_column(Integer)
    career_track_name: Mapped[str | None] = mapped_column(String(120))
    assessment_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="not_assessed"
    )
    assessment_band: Mapped[str | None] = mapped_column(String(20))
    assessment_algorithm_version: Mapped[str | None] = mapped_column(String(64))
    assessment_reason: Mapped[str | None] = mapped_column(String(32))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ApplicationActionReview(Base):
    """Append-only record of one explicit weekly action decision."""

    __tablename__ = "application_action_reviews"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "id", name="uq_application_action_reviews_owner_id_id"
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "idempotency_key_hash",
            name="uq_application_action_reviews_owner_application_mutation",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_application_action_reviews_owner_application",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "action_item_id"],
            ["action_items.owner_id", "action_items.application_id", "action_items.id"],
            name="fk_application_action_reviews_owner_action",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("decision IN ('continue', 'waiting')", name="decision"),
        CheckConstraint("new_due_on > prior_due_on", name="due_date_progresses"),
        CheckConstraint(
            "new_action_version = prior_action_version + 1",
            name="action_version_progresses",
        ),
        CheckConstraint(
            "new_application_version = prior_application_version + 1",
            name="application_version_progresses",
        ),
        CheckConstraint(
            "prior_action_version >= 1 AND prior_application_version >= 1",
            name="prior_versions_positive",
        ),
        CheckConstraint("recording_method = 'manual'", name="recording_method"),
        CheckConstraint(
            "length(idempotency_key_hash) = 64", name="mutation_hash"
        ),
        Index(
            "ix_application_action_reviews_timeline",
            "owner_id",
            "application_id",
            "recorded_at",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String(32), nullable=False)
    action_item_id: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    prior_due_on: Mapped[date] = mapped_column(Date, nullable=False)
    new_due_on: Mapped[date] = mapped_column(Date, nullable=False)
    prior_action_version: Mapped[int] = mapped_column(Integer, nullable=False)
    new_action_version: Mapped[int] = mapped_column(Integer, nullable=False)
    prior_application_version: Mapped[int] = mapped_column(Integer, nullable=False)
    new_application_version: Mapped[int] = mapped_column(Integer, nullable=False)
    recording_method: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual"
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["ApplicationActionReview", "ApplicationMetricSnapshot"]
