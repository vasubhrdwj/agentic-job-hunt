"""Phase-0 durable owner, session, queue, and worker models."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _uuid_hex() -> str:
    return uuid4().hex


class Owner(Base):
    """Private workspace owner and root of every user-owned record."""

    __tablename__ = "owners"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, default="Owner")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    sessions: Mapped[list["OwnerSession"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
    credential: Mapped["OwnerCredential | None"] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    hunt_runs: Mapped[list["HuntRun"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan", passive_deletes=True
    )


class OwnerSession(Base):
    """Opaque browser session; only the SHA-256 token hash is persisted."""

    __tablename__ = "owner_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    owner: Mapped[Owner] = relationship(back_populates="sessions")


class OwnerCredential(Base):
    """Email/password login attached one-to-one to an owner workspace."""

    __tablename__ = "owner_credentials"

    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), primary_key=True
    )
    normalized_email: Mapped[str] = mapped_column(
        String(254), nullable=False, unique=True
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped[Owner] = relationship(back_populates="credential")


class AuthThrottleBucket(Base):
    """Fixed-cardinality, keyed login throttle with no raw identifier data."""

    __tablename__ = "auth_throttle_buckets"
    __table_args__ = (
        CheckConstraint("length(bucket_id) = 3", name="bucket_id_length"),
        CheckConstraint("failure_count >= 0", name="failure_count_nonnegative"),
    )

    bucket_id: Mapped[str] = mapped_column(String(3), primary_key=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class BackgroundJob(Base):
    """Durable, lease-based work item for scans, enrichment, and legacy runs."""

    __tablename__ = "background_jobs"
    __table_args__ = (
        UniqueConstraint(
            "dedupe_scope",
            "kind",
            "dedupe_key",
            name="uq_background_jobs_scope_kind_dedupe",
        ),
        CheckConstraint(
            "(owner_id IS NULL AND dedupe_scope = 'system') OR "
            "(owner_id IS NOT NULL AND dedupe_scope = 'owner:' || owner_id)",
            name="dedupe_scope_matches_owner",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', "
            "'cancelled', 'dead_letter')",
            name="status",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
        Index("ix_background_jobs_claim", "status", "run_after", "priority"),
        Index("ix_background_jobs_owner", "owner_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_id: Mapped[str | None] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=True
    )
    dedupe_scope: Mapped[str] = mapped_column(
        String(72), nullable=False, default="system", server_default="system"
    )
    subject_type: Mapped[str | None] = mapped_column(String(64))
    subject_id: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stage: Mapped[str] = mapped_column(String(100), nullable=False, default="queued")
    stage_checkpoint: Mapped[str | None] = mapped_column(String(200))
    last_error: Mapped[str | None] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    events: Mapped[list["BackgroundJobEvent"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class BackgroundJobEvent(Base):
    """Append-only transition history for a background job."""

    __tablename__ = "background_job_events"
    __table_args__ = (Index("ix_background_job_events_job_created", "job_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    job: Mapped[BackgroundJob] = relationship(back_populates="events")


class WorkerHeartbeat(Base):
    """Latest liveness and capability report for one worker process."""

    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    supported_kinds: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    current_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="SET NULL")
    )
    build_version: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HuntRun(Base):
    """Private durable state for one practical hunt.

    The generic queue owns execution state. This row owns only the private
    request/result envelopes, capability metadata, retention, and owner scope.
    Plain resume, result, and outreach text must never be assigned here.
    """

    __tablename__ = "hunt_runs"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "idempotency_key_hash",
            name="uq_hunt_runs_owner_idempotency_key_hash",
        ),
        CheckConstraint(
            "(encrypted_request IS NULL AND request_key_id IS NULL) OR "
            "(encrypted_request IS NOT NULL AND request_key_id IS NOT NULL)",
            name="request_envelope_complete",
        ),
        CheckConstraint(
            "(encrypted_result IS NULL AND result_key_id IS NULL) OR "
            "(encrypted_result IS NOT NULL AND result_key_id IS NOT NULL)",
            name="result_envelope_complete",
        ),
        Index("ix_hunt_runs_owner_created", "owner_id", "created_at"),
        Index("ix_hunt_runs_request_expiry", "request_expires_at"),
        Index("ix_hunt_runs_access_expiry", "access_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    background_job_id: Mapped[str] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    access_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key_hash: Mapped[str | None] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_request: Mapped[str | None] = mapped_column(Text)
    request_key_id: Mapped[str | None] = mapped_column(String(32))
    request_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    encrypted_result: Mapped[str | None] = mapped_column(Text)
    result_key_id: Mapped[str | None] = mapped_column(String(32))
    access_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    request_cleared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped[Owner] = relationship(back_populates="hunt_runs")
    background_job: Mapped[BackgroundJob] = relationship()
    outcomes: Mapped[list["HuntOutcome"]] = relationship(
        back_populates="hunt_run", cascade="all, delete-orphan", passive_deletes=True
    )


class HuntOutcome(Base):
    """Encrypted append-only outcome entry for one outreach draft."""

    __tablename__ = "hunt_outcomes"
    __table_args__ = (
        Index("ix_hunt_outcomes_run_logged", "hunt_run_id", "logged_at"),
        Index("ix_hunt_outcomes_draft", "draft_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hunt_run_id: Mapped[str] = mapped_column(
        ForeignKey("hunt_runs.id", ondelete="CASCADE"), nullable=False
    )
    draft_id: Mapped[str] = mapped_column(String(128), nullable=False)
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    hunt_run: Mapped[HuntRun] = relationship(back_populates="outcomes")
