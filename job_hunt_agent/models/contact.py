"""Durable, owner-scoped contact plans and evidence snapshots."""

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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _uuid_hex() -> str:
    return uuid4().hex


class ContactPlan(Base):
    """One versioned discovery/selection attempt for an application."""

    __tablename__ = "contact_plans"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_contact_plans_owner_id_id"),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "id",
            name="uq_contact_plans_owner_application_id",
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "plan_number",
            name="uq_contact_plans_owner_application_number",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_contact_plans_owner_application",
            ondelete="CASCADE",
        ),
        CheckConstraint("plan_number >= 1", name="plan_number_positive"),
        CheckConstraint("target_count = 5", name="target_count_five"),
        CheckConstraint(
            "candidate_limit BETWEEN target_count AND 12",
            name="candidate_limit_bounded",
        ),
        CheckConstraint(
            "confidence_floor >= 0.75 AND confidence_floor <= 1.0",
            name="confidence_floor",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="status",
        ),
        CheckConstraint(
            "coverage_status IN ('pending', 'met', 'partial')",
            name="coverage_status",
        ),
        CheckConstraint(
            "discovered_count >= 0 AND verified_count >= 0 AND selected_count >= 0 "
            "AND selected_count <= verified_count "
            "AND verified_count <= discovered_count "
            "AND discovered_count <= candidate_limit",
            name="counts_ordered",
        ),
        CheckConstraint(
            "(status = 'queued' AND started_at IS NULL AND finalized_at IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND finalized_at IS NULL) OR "
            "(status IN ('completed', 'failed', 'cancelled') "
            "AND finalized_at IS NOT NULL)",
            name="status_timestamps",
        ),
        CheckConstraint(
            "(status = 'completed' AND coverage_status IN ('met', 'partial')) OR "
            "(status <> 'completed' AND coverage_status = 'pending')",
            name="completion_coverage",
        ),
        CheckConstraint(
            "(coverage_status = 'met' AND selected_count = target_count) OR "
            "(coverage_status = 'partial' AND selected_count < target_count) OR "
            "coverage_status = 'pending'",
            name="coverage_counts",
        ),
        CheckConstraint(
            "exhausted = false OR status = 'completed'",
            name="exhausted_only_when_completed",
        ),
        CheckConstraint(
            "(status = 'failed' AND error_code IS NOT NULL) OR "
            "(status <> 'failed' AND error_code IS NULL)",
            name="failure_code",
        ),
        CheckConstraint(
            "status NOT IN ('queued', 'running') OR background_job_id IS NOT NULL",
            name="active_job_required",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_contact_plans_owner_application_active",
            "owner_id",
            "application_id",
            unique=True,
            sqlite_where=text("status IN ('queued', 'running')"),
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index(
            "ix_contact_plans_owner_status",
            "owner_id",
            "status",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    target_count: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    candidate_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    confidence_floor: Mapped[float] = mapped_column(Float, nullable=False, default=0.75)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    scoring_version: Mapped[str] = mapped_column(String(64), nullable=False)
    background_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="SET NULL"), unique=True
    )
    discovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verified_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coverage_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
    exhausted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    shortfall_reasons: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    error_code: Mapped[str | None] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Contact(Base):
    """An owner-scoped canonical public profile identity."""

    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_contacts_owner_id_id"),
        UniqueConstraint(
            "owner_id", "identity_key_hash", name="uq_contacts_owner_identity_hash"
        ),
        UniqueConstraint(
            "owner_id",
            "normalized_profile_url",
            name="uq_contacts_owner_normalized_profile_url",
        ),
        CheckConstraint(
            "profile_source IN ('linkedin', 'github', 'company_page', 'other')",
            name="profile_source",
        ),
        CheckConstraint(
            "lifecycle IN ('active', 'do_not_contact', 'retired')",
            name="lifecycle",
        ),
        CheckConstraint(
            "(lifecycle = 'do_not_contact' AND do_not_contact_at IS NOT NULL) OR "
            "(lifecycle <> 'do_not_contact' AND do_not_contact_at IS NULL)",
            name="do_not_contact_timestamp",
        ),
        CheckConstraint("length(trim(public_name)) BETWEEN 1 AND 200", name="name"),
        CheckConstraint(
            "length(normalized_profile_url) BETWEEN 9 AND 2048 "
            "AND normalized_profile_url LIKE 'https://%'",
            name="normalized_profile_url",
        ),
        CheckConstraint(
            "length(profile_url) BETWEEN 9 AND 2048 "
            "AND profile_url LIKE 'https://%'",
            name="profile_url",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_contacts_owner_lifecycle", "owner_id", "lifecycle", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    identity_key: Mapped[str] = mapped_column(Text, nullable=False)
    identity_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_profile_url: Mapped[str] = mapped_column(Text, nullable=False)
    profile_source: Mapped[str] = mapped_column(String(24), nullable=False)
    public_name: Mapped[str] = mapped_column(String(200), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    do_not_contact_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ApplicationContact(Base):
    """Role-specific evidence and ranking for one discovered public profile."""

    __tablename__ = "application_contacts"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "id", name="uq_application_contacts_owner_id_id"
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "id",
            name="uq_application_contacts_owner_application_id",
        ),
        UniqueConstraint(
            "owner_id",
            "contact_plan_id",
            "contact_id",
            name="uq_application_contacts_owner_plan_contact",
        ),
        UniqueConstraint(
            "owner_id",
            "contact_plan_id",
            "pool_rank",
            name="uq_application_contacts_owner_plan_pool_rank",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_application_contacts_owner_application",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "contact_plan_id"],
            ["contact_plans.owner_id", "contact_plans.application_id", "contact_plans.id"],
            name="fk_application_contacts_owner_plan",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "contact_id"],
            ["contacts.owner_id", "contacts.id"],
            name="fk_application_contacts_owner_contact",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("result_position >= 1", name="result_position_positive"),
        CheckConstraint("pool_rank BETWEEN 1 AND 12", name="pool_rank"),
        CheckConstraint("bench_rank IS NULL OR bench_rank BETWEEN 1 AND 5", name="bench_rank"),
        CheckConstraint("wave IS NULL OR wave BETWEEN 1 AND 5", name="wave"),
        CheckConstraint(
            "category IN ('warm_path', 'team_peer', 'adjacent_peer', "
            "'team_leader', 'recruiter', 'other')",
            name="category",
        ),
        CheckConstraint(
            "verification_status IN ('unverified', 'verified', 'rejected', 'stale')",
            name="verification_status",
        ),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="confidence"),
        CheckConstraint(
            "relationship_status IN ('verified', 'inferred', 'unknown')",
            name="relationship_status",
        ),
        CheckConstraint(
            "team_proximity_status IN ('verified', 'inferred', 'unknown')",
            name="team_proximity_status",
        ),
        CheckConstraint(
            "bench_state IN ('candidate', 'excluded', 'overflow', 'ready', "
            "'reserve', 'paused', 'stopped')",
            name="bench_state",
        ),
        CheckConstraint("score_total >= 0 AND score_total <= 1000", name="score_total"),
        CheckConstraint(
            "length(trim(current_title)) BETWEEN 1 AND 300",
            name="current_title",
        ),
        CheckConstraint(
            "length(trim(current_company)) BETWEEN 1 AND 200",
            name="current_company",
        ),
        CheckConstraint(
            "length(trim(why_relevant)) BETWEEN 1 AND 2000",
            name="why_relevant",
        ),
        CheckConstraint(
            "(verification_status = 'verified' AND confidence >= 0.75 "
            "AND verified_at IS NOT NULL "
            "AND employer_evidence_excerpt IS NOT NULL "
            "AND employer_evidence_url IS NOT NULL "
            "AND employer_evidence_source IS NOT NULL "
            "AND employer_evidence_observed_at IS NOT NULL) OR "
            "(verification_status <> 'verified' AND bench_rank IS NULL)",
            name="verified_evidence",
        ),
        CheckConstraint(
            "employer_evidence_excerpt IS NULL OR "
            "length(trim(employer_evidence_excerpt)) BETWEEN 1 AND 1000",
            name="employer_evidence_excerpt",
        ),
        CheckConstraint(
            "employer_evidence_url IS NULL OR "
            "(length(employer_evidence_url) BETWEEN 9 AND 2048 "
            "AND employer_evidence_url LIKE 'https://%')",
            name="employer_evidence_url",
        ),
        CheckConstraint(
            "employer_evidence_source IS NULL OR "
            "length(trim(employer_evidence_source)) BETWEEN 1 AND 64",
            name="employer_evidence_source",
        ),
        CheckConstraint(
            "(bench_rank IS NULL AND wave IS NULL "
            "AND bench_state IN ('candidate', 'excluded', 'overflow')) OR "
            "(bench_rank IS NOT NULL AND wave IS NOT NULL "
            "AND verification_status = 'verified' AND exclusion_reason IS NULL "
            "AND bench_state IN ('ready', 'reserve', 'paused', 'stopped'))",
            name="bench_selection",
        ),
        CheckConstraint(
            "(bench_state = 'ready' AND unlocked_at IS NOT NULL) OR "
            "(bench_state = 'reserve' AND unlocked_at IS NULL) OR "
            "bench_state IN ('candidate', 'excluded', 'overflow', 'paused', 'stopped')",
            name="bench_unlock",
        ),
        CheckConstraint(
            "category <> 'warm_path' OR relationship_status = 'verified'",
            name="warm_path_verified",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_application_contacts_owner_plan_bench_rank",
            "owner_id",
            "contact_plan_id",
            "bench_rank",
            unique=True,
            sqlite_where=text("bench_rank IS NOT NULL"),
            postgresql_where=text("bench_rank IS NOT NULL"),
        ),
        Index(
            "ix_application_contacts_owner_application",
            "owner_id",
            "application_id",
            "bench_rank",
        ),
        Index(
            "ix_application_contacts_owner_contact",
            "owner_id",
            "contact_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String(32), nullable=False)
    contact_plan_id: Mapped[str] = mapped_column(String(32), nullable=False)
    contact_id: Mapped[str] = mapped_column(String(32), nullable=False)
    discovery_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    discovery_query: Mapped[str] = mapped_column(Text, nullable=False)
    result_position: Mapped[int] = mapped_column(Integer, nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_title: Mapped[str] = mapped_column(String(300), nullable=False)
    current_company: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    employer_evidence_excerpt: Mapped[str | None] = mapped_column(String(1_000))
    employer_evidence_url: Mapped[str | None] = mapped_column(Text)
    employer_evidence_source: Mapped[str | None] = mapped_column(String(64))
    employer_evidence_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    why_relevant: Mapped[str] = mapped_column(String(2_000), nullable=False)
    relationship_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown"
    )
    relationship_evidence_summary: Mapped[str | None] = mapped_column(Text)
    relationship_evidence_url: Mapped[str | None] = mapped_column(Text)
    team_proximity_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown"
    )
    team_evidence_summary: Mapped[str | None] = mapped_column(Text)
    team_evidence_url: Mapped[str | None] = mapped_column(Text)
    score_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    score_components: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    scoring_version: Mapped[str] = mapped_column(String(64), nullable=False)
    pool_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    bench_rank: Mapped[int | None] = mapped_column(Integer)
    wave: Mapped[int | None] = mapped_column(Integer)
    bench_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="candidate"
    )
    exclusion_reason: Mapped[str | None] = mapped_column(String(100))
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unlocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


__all__ = ["ApplicationContact", "Contact", "ContactPlan"]
