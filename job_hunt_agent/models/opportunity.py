"""Durable owner-scoped opportunity radar records.

These models persist search-only scan inputs and public job-source facts. They
deliberately contain no resume content, match prose, application state, or
provider-generated material.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _uuid_hex() -> str:
    return uuid4().hex


class OpportunityScan(Base):
    """One idempotent scan of a versioned saved-search criteria snapshot."""

    __tablename__ = "opportunity_scans"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_opportunity_scans_owner_id_id"),
        UniqueConstraint(
            "owner_id",
            "saved_search_id",
            "id",
            name="uq_opportunity_scans_owner_search_id",
        ),
        UniqueConstraint(
            "owner_id",
            "saved_search_id",
            "dedupe_key",
            name="uq_opportunity_scans_owner_search_dedupe",
        ),
        UniqueConstraint(
            "owner_id",
            "idempotency_key_hash",
            name="uq_opportunity_scans_owner_idempotency",
        ),
        ForeignKeyConstraint(
            ["owner_id", "saved_search_id"],
            ["saved_searches.owner_id", "saved_searches.id"],
            name="fk_opportunity_scans_owner_search",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "trigger IN ('manual', 'scheduled')", name="trigger"
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', "
            "'partial', 'failed', 'cancelled')",
            name="status",
        ),
        CheckConstraint("stage <> ''", name="stage_nonempty"),
        CheckConstraint("saved_search_version >= 1", name="search_version_positive"),
        CheckConstraint("criteria_schema_version >= 1", name="criteria_version_positive"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "source_count >= 0 AND terminal_source_count >= 0 AND "
            "successful_source_count >= 0 AND failed_source_count >= 0 AND "
            "observed_count >= 0 AND new_posting_count >= 0 AND "
            "changed_posting_count >= 0 AND new_opportunity_count >= 0",
            name="counts_nonnegative",
        ),
        CheckConstraint(
            "terminal_source_count <= source_count AND "
            "successful_source_count <= terminal_source_count AND "
            "failed_source_count <= terminal_source_count AND "
            "successful_source_count + failed_source_count <= terminal_source_count",
            name="source_counts_ordered",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'partial', 'failed', 'cancelled') "
            "AND finalized_at IS NOT NULL) OR "
            "(status IN ('queued', 'running') AND finalized_at IS NULL)",
            name="finalized_timestamp",
        ),
        CheckConstraint(
            "status NOT IN ('running', 'succeeded', 'partial', 'failed') "
            "OR started_at IS NOT NULL",
            name="started_timestamp",
        ),
        Index("ix_opportunity_scans_owner_status", "owner_id", "status", "created_at"),
        Index(
            "ix_opportunity_scans_search_scheduled",
            "saved_search_id",
            "scheduled_for",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    saved_search_id: Mapped[str] = mapped_column(String(32), nullable=False)
    saved_search_version: Mapped[int] = mapped_column(Integer, nullable=False)
    criteria_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    criteria_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    pack_snapshot: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key_hash: Mapped[str | None] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    background_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="SET NULL"), unique=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    stage: Mapped[str] = mapped_column(String(100), nullable=False, default="queued")
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    terminal_source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_posting_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    changed_posting_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_opportunity_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OpportunityScanSource(Base):
    """One source/company result with honest scope and completeness metadata."""

    __tablename__ = "opportunity_scan_sources"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "id", name="uq_opportunity_scan_sources_owner_id_id"
        ),
        UniqueConstraint(
            "owner_id",
            "opportunity_scan_id",
            "id",
            name="uq_opportunity_scan_sources_owner_scan_id",
        ),
        UniqueConstraint(
            "owner_id",
            "opportunity_scan_id",
            "company_slug",
            "source",
            name="uq_opportunity_scan_sources_partition",
        ),
        ForeignKeyConstraint(
            ["owner_id", "opportunity_scan_id"],
            ["opportunity_scans.owner_id", "opportunity_scans.id"],
            name="fk_opportunity_scan_sources_owner_scan",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="status",
        ),
        CheckConstraint(
            "fetch_scope IN ('criteria_filtered', 'board_snapshot')", name="fetch_scope"
        ),
        CheckConstraint(
            "completeness IN ('unknown', 'partial', 'complete')", name="completeness"
        ),
        CheckConstraint(
            "completeness <> 'complete' OR "
            "(status = 'succeeded' AND fetch_scope = 'board_snapshot')",
            name="complete_board_only",
        ),
        CheckConstraint(
            "observed_count >= 0 AND returned_count >= 0 AND persisted_count >= 0 AND "
            "returned_count <= observed_count AND persisted_count <= returned_count",
            name="counts_ordered",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "(status = 'pending' AND started_at IS NULL AND completed_at IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status IN ('succeeded', 'failed') AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL) OR "
            "(status = 'cancelled' AND completed_at IS NOT NULL)",
            name="timestamps_match_status",
        ),
        CheckConstraint(
            "(status = 'failed' AND error_code IS NOT NULL) OR "
            "(status <> 'failed' AND error_code IS NULL)",
            name="failure_code",
        ),
        Index(
            "ix_opportunity_scan_sources_scan_status",
            "opportunity_scan_id",
            "status",
        ),
        Index(
            "ix_opportunity_scan_sources_health",
            "owner_id",
            "source",
            "completed_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    opportunity_scan_id: Mapped[str] = mapped_column(String(32), nullable=False)
    company_slug: Mapped[str] = mapped_column(String(120), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    fetch_scope: Mapped[str] = mapped_column(
        String(24), nullable=False, default="criteria_filtered"
    )
    completeness: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown"
    )
    observed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    returned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    persisted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    error_code: Mapped[str | None] = mapped_column(String(100))
    used_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class JobPosting(Base):
    """Owner-scoped stable posting identity and conservative lifecycle state."""

    __tablename__ = "job_postings"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_job_postings_owner_id_id"),
        UniqueConstraint(
            "owner_id", "identity_key_hash", name="uq_job_postings_owner_identity_hash"
        ),
        CheckConstraint("identity_kind IN ('native', 'url')", name="identity_kind"),
        CheckConstraint(
            "(identity_kind = 'native' AND source_job_id IS NOT NULL) OR "
            "(identity_kind = 'url' AND source_job_id IS NULL)",
            name="identity_shape",
        ),
        CheckConstraint("lifecycle_state IN ('open', 'closed')", name="lifecycle_state"),
        CheckConstraint(
            "closure_reason IS NULL OR "
            "closure_reason IN ('explicit', 'two_complete_omissions')",
            name="closure_reason",
        ),
        CheckConstraint(
            "(lifecycle_state = 'open' AND closed_at IS NULL AND closure_reason IS NULL) OR "
            "(lifecycle_state = 'closed' AND closed_at IS NOT NULL "
            "AND closure_reason IS NOT NULL)",
            name="lifecycle_timestamps",
        ),
        CheckConstraint(
            "closure_reason <> 'two_complete_omissions' OR "
            "consecutive_complete_omissions >= 2",
            name="omission_closure_threshold",
        ),
        CheckConstraint(
            "consecutive_complete_omissions >= 0", name="omissions_nonnegative"
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "last_confirmed_at >= first_confirmed_at", name="confirmed_order"
        ),
        Index("ix_job_postings_owner_lifecycle", "owner_id", "lifecycle_state"),
        Index(
            "ix_job_postings_owner_company",
            "owner_id",
            "company_slug",
            "last_confirmed_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    identity_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    identity_key: Mapped[str] = mapped_column(Text, nullable=False)
    identity_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    company_slug: Mapped[str] = mapped_column(String(120), nullable=False)
    source_job_id: Mapped[str | None] = mapped_column(String(512))
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    closure_reason: Mapped[str | None] = mapped_column(String(32))
    consecutive_complete_omissions: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    first_confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_lifecycle_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class JobPostingAlias(Base):
    """An additional native identity or normalized URL for one posting."""

    __tablename__ = "job_posting_aliases"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "job_posting_id", "id", name="uq_job_posting_aliases_owner_posting_id"
        ),
        UniqueConstraint(
            "owner_id", "alias_key_hash", name="uq_job_posting_aliases_owner_hash"
        ),
        ForeignKeyConstraint(
            ["owner_id", "job_posting_id"],
            ["job_postings.owner_id", "job_postings.id"],
            name="fk_job_posting_aliases_owner_posting",
            ondelete="CASCADE",
        ),
        CheckConstraint("alias_kind IN ('native', 'url')", name="alias_kind"),
        CheckConstraint(
            "(alias_kind = 'native' AND source_job_id IS NOT NULL "
            "AND normalized_url IS NULL) OR "
            "(alias_kind = 'url' AND source_job_id IS NULL "
            "AND normalized_url IS NOT NULL)",
            name="alias_shape",
        ),
        CheckConstraint("last_seen_at >= first_seen_at", name="seen_order"),
        Index("ix_job_posting_aliases_posting", "job_posting_id", "last_seen_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    job_posting_id: Mapped[str] = mapped_column(String(32), nullable=False)
    alias_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    alias_key: Mapped[str] = mapped_column(Text, nullable=False)
    alias_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    company_slug: Mapped[str] = mapped_column(String(120), nullable=False)
    source_job_id: Mapped[str | None] = mapped_column(String(512))
    normalized_url: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class JobPostingVersion(Base):
    """Immutable normalized public facts observed for a posting."""

    __tablename__ = "job_posting_versions"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "job_posting_id",
            "id",
            name="uq_job_posting_versions_owner_posting_id",
        ),
        UniqueConstraint(
            "owner_id",
            "job_posting_id",
            "version_number",
            name="uq_job_posting_versions_owner_number",
        ),
        ForeignKeyConstraint(
            ["owner_id", "job_posting_id"],
            ["job_postings.owner_id", "job_postings.id"],
            name="fk_job_posting_versions_owner_posting",
            ondelete="CASCADE",
        ),
        CheckConstraint("version_number >= 1", name="number_positive"),
        CheckConstraint(
            "employment_type IN ('full_time', 'contract', 'intern', 'unknown')",
            name="employment_type",
        ),
        CheckConstraint(
            "source_confidence >= 0 AND source_confidence <= 1",
            name="confidence_range",
        ),
        Index(
            "ix_job_posting_versions_posting_observed",
            "job_posting_id",
            "observed_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    job_posting_id: Mapped[str] = mapped_column(String(32), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_job_id: Mapped[str | None] = mapped_column(String(512))
    company_name: Mapped[str] = mapped_column(String(240), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    apply_urls: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    location: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    employment_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown"
    )
    posted_at_text: Mapped[str | None] = mapped_column(String(100))
    source_updated_at_text: Mapped[str | None] = mapped_column(String(100))
    source_facts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    source_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class JobObservation(Base):
    """Append-only link from a source run to the exact posting version seen."""

    __tablename__ = "job_observations"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_job_observations_owner_id_id"),
        UniqueConstraint(
            "owner_id",
            "opportunity_scan_source_id",
            "job_posting_id",
            name="uq_job_observations_source_posting",
        ),
        ForeignKeyConstraint(
            ["owner_id", "opportunity_scan_id", "opportunity_scan_source_id"],
            [
                "opportunity_scan_sources.owner_id",
                "opportunity_scan_sources.opportunity_scan_id",
                "opportunity_scan_sources.id",
            ],
            name="fk_job_observations_owner_scan_source",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "job_posting_id", "job_posting_version_id"],
            [
                "job_posting_versions.owner_id",
                "job_posting_versions.job_posting_id",
                "job_posting_versions.id",
            ],
            name="fk_job_observations_owner_posting_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "job_posting_id", "job_posting_alias_id"],
            [
                "job_posting_aliases.owner_id",
                "job_posting_aliases.job_posting_id",
                "job_posting_aliases.id",
            ],
            name="fk_job_observations_owner_posting_alias",
            ondelete="RESTRICT",
        ),
        Index("ix_job_observations_scan", "opportunity_scan_id", "observed_at"),
        Index("ix_job_observations_posting", "job_posting_id", "observed_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    opportunity_scan_id: Mapped[str] = mapped_column(String(32), nullable=False)
    opportunity_scan_source_id: Mapped[str] = mapped_column(String(32), nullable=False)
    job_posting_id: Mapped[str] = mapped_column(String(32), nullable=False)
    job_posting_version_id: Mapped[str] = mapped_column(String(32), nullable=False)
    job_posting_alias_id: Mapped[str] = mapped_column(String(32), nullable=False)
    first_party_url_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SavedSearchMatch(Base):
    """One cumulative provenance edge for one search and stable posting."""

    __tablename__ = "saved_search_matches"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "id", name="uq_saved_search_matches_owner_id_id"
        ),
        UniqueConstraint(
            "owner_id",
            "saved_search_id",
            "job_posting_id",
            name="uq_saved_search_matches_owner_search_posting",
        ),
        ForeignKeyConstraint(
            ["owner_id", "saved_search_id"],
            ["saved_searches.owner_id", "saved_searches.id"],
            name="fk_saved_search_matches_owner_search",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "job_posting_id"],
            ["job_postings.owner_id", "job_postings.id"],
            name="fk_saved_search_matches_owner_posting",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "saved_search_id", "first_scan_id"],
            [
                "opportunity_scans.owner_id",
                "opportunity_scans.saved_search_id",
                "opportunity_scans.id",
            ],
            name="fk_saved_search_matches_owner_first_scan",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "saved_search_id", "last_scan_id"],
            [
                "opportunity_scans.owner_id",
                "opportunity_scans.saved_search_id",
                "opportunity_scans.id",
            ],
            name="fk_saved_search_matches_owner_last_scan",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "job_posting_id", "last_posting_version_id"],
            [
                "job_posting_versions.owner_id",
                "job_posting_versions.job_posting_id",
                "job_posting_versions.id",
            ],
            name="fk_saved_search_matches_owner_last_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint("match_count >= 1", name="match_count_positive"),
        CheckConstraint("last_matched_at >= first_matched_at", name="matched_order"),
        Index(
            "ix_saved_search_matches_owner_recent",
            "owner_id",
            "last_matched_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    saved_search_id: Mapped[str] = mapped_column(String(32), nullable=False)
    job_posting_id: Mapped[str] = mapped_column(String(32), nullable=False)
    first_scan_id: Mapped[str] = mapped_column(String(32), nullable=False)
    last_scan_id: Mapped[str] = mapped_column(String(32), nullable=False)
    last_posting_version_id: Mapped[str] = mapped_column(String(32), nullable=False)
    match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OwnerOpportunity(Base):
    """One deduplicated Today decision record per owner and posting."""

    __tablename__ = "owner_opportunities"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "id", name="uq_owner_opportunities_owner_id_id"
        ),
        UniqueConstraint(
            "owner_id",
            "id",
            "job_posting_id",
            name="uq_owner_opportunities_owner_id_posting",
        ),
        UniqueConstraint(
            "owner_id", "job_posting_id", name="uq_owner_opportunities_owner_posting"
        ),
        ForeignKeyConstraint(
            ["owner_id", "job_posting_id"],
            ["job_postings.owner_id", "job_postings.id"],
            name="fk_owner_opportunities_owner_posting",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "job_posting_id", "reviewed_posting_version_id"],
            [
                "job_posting_versions.owner_id",
                "job_posting_versions.job_posting_id",
                "job_posting_versions.id",
            ],
            name="fk_owner_opportunities_owner_reviewed_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "decision IN ('inbox', 'watch', 'dismiss', 'pursued')",
            name="decision",
        ),
        CheckConstraint(
            "(decision = 'dismiss' AND decision_reason_code IS NOT NULL) OR "
            "(decision IN ('inbox', 'watch', 'pursued') "
            "AND decision_reason_code IS NULL)",
            name="decision_reason",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("last_surfaced_at >= first_surfaced_at", name="surfaced_order"),
        Index(
            "ix_owner_opportunities_today",
            "owner_id",
            "decision",
            "last_surfaced_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    job_posting_id: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False, default="inbox")
    decision_reason_code: Mapped[str | None] = mapped_column(String(64))
    reviewed_posting_version_id: Mapped[str | None] = mapped_column(String(32))
    decision_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_surfaced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_surfaced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OpportunityDecisionEvent(Base):
    """Append-only, idempotent owner decision transition with an encrypted note."""

    __tablename__ = "opportunity_decision_events"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "id", name="uq_opportunity_decision_events_owner_id_id"
        ),
        UniqueConstraint(
            "owner_id",
            "owner_opportunity_id",
            "id",
            name="uq_opportunity_decision_events_owner_opportunity_id",
        ),
        UniqueConstraint(
            "owner_id",
            "owner_opportunity_id",
            "idempotency_key_hash",
            name="uq_opportunity_decision_events_idempotency",
        ),
        ForeignKeyConstraint(
            ["owner_id", "owner_opportunity_id", "job_posting_id"],
            [
                "owner_opportunities.owner_id",
                "owner_opportunities.id",
                "owner_opportunities.job_posting_id",
            ],
            name="fk_opportunity_decision_events_owner_opportunity",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "job_posting_id", "posting_version_id"],
            [
                "job_posting_versions.owner_id",
                "job_posting_versions.job_posting_id",
                "job_posting_versions.id",
            ],
            name="fk_opportunity_decision_events_owner_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "owner_opportunity_id", "compensates_event_id"],
            [
                "opportunity_decision_events.owner_id",
                "opportunity_decision_events.owner_opportunity_id",
                "opportunity_decision_events.id",
            ],
            name="fk_opportunity_decision_events_compensates",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "previous_decision IN ('inbox', 'watch', 'dismiss', 'pursued') AND "
            "new_decision IN ('inbox', 'watch', 'dismiss', 'pursued')",
            name="decision_values",
        ),
        CheckConstraint("previous_decision <> new_decision", name="decision_changed"),
        CheckConstraint(
            "(new_decision = 'dismiss' AND reason_code IS NOT NULL) OR "
            "(new_decision IN ('inbox', 'watch', 'pursued') "
            "AND reason_code IS NULL)",
            name="decision_reason",
        ),
        CheckConstraint(
            "(encrypted_note IS NULL AND note_key_id IS NULL) OR "
            "(encrypted_note IS NOT NULL AND note_key_id IS NOT NULL)",
            name="note_envelope_complete",
        ),
        CheckConstraint(
            "compensates_event_id IS NULL OR compensates_event_id <> id",
            name="not_self_compensating",
        ),
        Index(
            "ix_opportunity_decision_events_timeline",
            "owner_opportunity_id",
            "occurred_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    owner_opportunity_id: Mapped[str] = mapped_column(String(32), nullable=False)
    job_posting_id: Mapped[str] = mapped_column(String(32), nullable=False)
    posting_version_id: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_decision: Mapped[str] = mapped_column(String(16), nullable=False)
    new_decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    encrypted_note: Mapped[str | None] = mapped_column(Text)
    note_key_id: Mapped[str | None] = mapped_column(String(32))
    compensates_event_id: Mapped[str | None] = mapped_column(String(32))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "JobObservation",
    "JobPosting",
    "JobPostingAlias",
    "JobPostingVersion",
    "OpportunityDecisionEvent",
    "OpportunityScan",
    "OpportunityScanSource",
    "OwnerOpportunity",
    "SavedSearchMatch",
]
