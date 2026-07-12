"""Create the Phase-0 owner, session, queue, and worker foundation.

Revision ID: 20260711_0001
Revises:
Create Date: 2026-07-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260711_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owners",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_owners")),
    )
    op.create_table(
        "owner_sessions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["owners.id"], name=op.f("fk_owner_sessions_owner_id_owners"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_owner_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_owner_sessions_token_hash")),
    )
    op.create_index(op.f("ix_owner_sessions_owner_id"), "owner_sessions", ["owner_id"])

    op.create_table(
        "background_jobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=True),
        sa.Column("subject_type", sa.String(length=64), nullable=True),
        sa.Column("subject_id", sa.String(length=128), nullable=True),
        sa.Column("payload", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("run_after", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stage", sa.String(length=100), server_default="queued", nullable=False),
        sa.Column("stage_checkpoint", sa.String(length=200), nullable=True),
        sa.Column("last_error", sa.String(length=200), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt_count >= 0", name=op.f("ck_background_jobs_attempt_count_nonnegative")),
        sa.CheckConstraint("max_attempts > 0", name=op.f("ck_background_jobs_max_attempts_positive")),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'dead_letter')",
            name=op.f("ck_background_jobs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["owners.id"], name=op.f("fk_background_jobs_owner_id_owners"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_background_jobs")),
        sa.UniqueConstraint("kind", "dedupe_key", name="uq_background_jobs_kind_dedupe"),
    )
    op.create_index("ix_background_jobs_claim", "background_jobs", ["status", "run_after", "priority"])
    op.create_index("ix_background_jobs_owner", "background_jobs", ["owner_id", "created_at"])

    op.create_table(
        "background_job_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"], ["background_jobs.id"], name=op.f("fk_background_job_events_job_id_background_jobs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_background_job_events")),
    )
    op.create_index(
        "ix_background_job_events_job_created", "background_job_events", ["job_id", "created_at"]
    )

    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(length=200), nullable=False),
        sa.Column("supported_kinds", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("current_job_id", sa.String(length=32), nullable=True),
        sa.Column("build_version", sa.String(length=100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["current_job_id"], ["background_jobs.id"], name=op.f("fk_worker_heartbeats_current_job_id_background_jobs"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("worker_id", name=op.f("pk_worker_heartbeats")),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
    op.drop_index("ix_background_job_events_job_created", table_name="background_job_events")
    op.drop_table("background_job_events")
    op.drop_index("ix_background_jobs_owner", table_name="background_jobs")
    op.drop_index("ix_background_jobs_claim", table_name="background_jobs")
    op.drop_table("background_jobs")
    op.drop_index(op.f("ix_owner_sessions_owner_id"), table_name="owner_sessions")
    op.drop_table("owner_sessions")
    op.drop_table("owners")
