"""Owner-scoped profile, resume, evidence, and saved-search records."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
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


class CandidateProfile(Base):
    """One encrypted private career profile per owner workspace."""

    __tablename__ = "candidate_profiles"
    __table_args__ = (
        CheckConstraint(
            "onboarding_state IN ('profile', 'resume', 'career_track', 'evidence', "
            "'saved_search', 'complete')",
            name="onboarding_state",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_candidate_profiles_owner_updated", "owner_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    onboarding_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="profile"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CareerTrack(Base):
    """Versioned target role family and ranking priorities."""

    __tablename__ = "career_tracks"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_career_tracks_owner_id_id"),
        UniqueConstraint("owner_id", "name", name="uq_career_tracks_owner_name"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_career_tracks_owner_active", "owner_id", "active"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role_families: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    seniority_levels: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    target_locations: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    priorities: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ResumeVersion(Base):
    """Immutable encrypted resume content with mutable base designation."""

    __tablename__ = "resume_versions"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_resume_versions_owner_id_id"),
        UniqueConstraint(
            "owner_id", "content_hash", name="uq_resume_versions_owner_content_hash"
        ),
        ForeignKeyConstraint(
            ["owner_id", "parent_id"],
            ["resume_versions.owner_id", "resume_versions.id"],
            name="fk_resume_versions_owner_parent",
            ondelete="RESTRICT",
        ),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="parent_not_self"),
        CheckConstraint(
            "source IN ('pasted', 'uploaded', 'imported', 'edited')", name="source"
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_resume_versions_owner_base",
            "owner_id",
            unique=True,
            sqlite_where=text("is_base = 1"),
            postgresql_where=text("is_base"),
        ),
        Index("ix_resume_versions_owner_created", "owner_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[str | None] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    encrypted_content: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    is_base: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ResumeImport(Base):
    """Immutable encrypted result of one owner resume-upload mutation."""

    __tablename__ = "resume_imports"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_resume_imports_owner_id_id"),
        ForeignKeyConstraint(
            ["owner_id", "resume_version_id"],
            ["resume_versions.owner_id", "resume_versions.id"],
            name="fk_resume_imports_owner_resume",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "length(trim(parser_version)) BETWEEN 1 AND 64",
            name="parser_version",
        ),
        CheckConstraint(
            "length(trim(media_type)) BETWEEN 1 AND 120",
            name="media_type",
        ),
        CheckConstraint(
            "page_count IS NULL OR (page_count >= 1 AND page_count <= 1000)",
            name="page_count",
        ),
        CheckConstraint(
            "length(trim(encrypted_payload)) >= 1 "
            "AND length(trim(encryption_key_id)) BETWEEN 1 AND 32",
            name="encrypted_payload_envelope",
        ),
        CheckConstraint("version = 1", name="immutable_version"),
        Index("ix_resume_imports_owner_created", "owner_id", "created_at"),
        Index("ix_resume_imports_owner_resume", "owner_id", "resume_version_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    resume_version_id: Mapped[str] = mapped_column(String(32), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AchievementEvidence(Base):
    """Encrypted manual or suggested claim gated by explicit approval."""

    __tablename__ = "achievement_evidence"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_achievement_evidence_owner_id_id"),
        ForeignKeyConstraint(
            ["owner_id", "source_resume_version_id"],
            ["resume_versions.owner_id", "resume_versions.id"],
            name="fk_achievement_evidence_owner_resume",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "origin IN ('owner_entered', 'resume_suggestion')", name="origin"
        ),
        CheckConstraint(
            "approval_state IN ('pending', 'approved', 'rejected', 'retired')",
            name="approval_state",
        ),
        CheckConstraint(
            "(approval_state = 'pending' AND approved_at IS NULL "
            "AND rejected_at IS NULL AND retired_at IS NULL) OR "
            "(approval_state = 'approved' AND approved_at IS NOT NULL "
            "AND rejected_at IS NULL AND retired_at IS NULL) OR "
            "(approval_state = 'rejected' AND rejected_at IS NOT NULL "
            "AND approved_at IS NULL AND retired_at IS NULL) OR "
            "(approval_state = 'retired' AND approved_at IS NOT NULL "
            "AND rejected_at IS NULL AND retired_at IS NOT NULL)",
            name="approval_timestamps",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_achievement_evidence_owner_state", "owner_id", "approval_state"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    source_resume_version_id: Mapped[str | None] = mapped_column(String(32))
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    approval_state: Mapped[str] = mapped_column(String(20), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SavedSearch(Base):
    """Versioned lossless hunt criteria plus a timezone-aware local schedule."""

    __tablename__ = "saved_searches"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_saved_searches_owner_id_id"),
        UniqueConstraint("owner_id", "name", name="uq_saved_searches_owner_name"),
        ForeignKeyConstraint(
            ["owner_id", "career_track_id"],
            ["career_tracks.owner_id", "career_tracks.id"],
            name="fk_saved_searches_owner_track",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "resume_version_id"],
            ["resume_versions.owner_id", "resume_versions.id"],
            name="fk_saved_searches_owner_resume",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "cadence IN ('manual', 'daily', 'weekdays', 'weekly')", name="cadence"
        ),
        CheckConstraint(
            "((cadence = 'manual' OR NOT active) AND next_scan_at IS NULL) OR "
            "(cadence <> 'manual' AND active AND next_scan_at IS NOT NULL)",
            name="schedule_next_scan",
        ),
        CheckConstraint("criteria_schema_version >= 1", name="criteria_schema_version"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_saved_searches_owner_track", "owner_id", "career_track_id"),
        Index("ix_saved_searches_due", "active", "next_scan_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    career_track_id: Mapped[str] = mapped_column(String(32), nullable=False)
    resume_version_id: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    criteria_schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    criteria: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    pack: Mapped[str] = mapped_column(String(64), nullable=False)
    use_self_rag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cadence: Mapped[str] = mapped_column(String(20), nullable=False)
    schedule: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OwnerMutationReceipt(Base):
    """Payload-free owner mutation claim and replay result."""

    __tablename__ = "owner_mutation_receipts"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "namespace",
            "idempotency_key_hash",
            name="uq_owner_mutation_receipts_owner_namespace_key",
        ),
        CheckConstraint("status IN ('pending', 'completed')", name="status"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "(status = 'pending' AND completed_at IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL "
            "AND resource_type IS NOT NULL AND resource_id IS NOT NULL)",
            name="completion",
        ),
        Index("ix_owner_mutation_receipts_owner_created", "owner_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    namespace: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    result_version: Mapped[int | None] = mapped_column(Integer)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
