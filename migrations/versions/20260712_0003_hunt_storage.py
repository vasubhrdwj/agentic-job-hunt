"""Add encrypted owner-scoped hunt runs and outcome logs.

Revision ID: 20260712_0003
Revises: 20260711_0002
Create Date: 2026-07-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260712_0003"
down_revision = "20260711_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hunt_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("background_job_id", sa.String(length=32), nullable=False),
        sa.Column("access_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("encrypted_request", sa.Text(), nullable=True),
        sa.Column("request_key_id", sa.String(length=32), nullable=True),
        sa.Column("request_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("encrypted_result", sa.Text(), nullable=True),
        sa.Column("result_key_id", sa.String(length=32), nullable=True),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(encrypted_request IS NULL AND request_key_id IS NULL) OR "
            "(encrypted_request IS NOT NULL AND request_key_id IS NOT NULL)",
            name=op.f("ck_hunt_runs_request_envelope_complete"),
        ),
        sa.CheckConstraint(
            "(encrypted_result IS NULL AND result_key_id IS NULL) OR "
            "(encrypted_result IS NOT NULL AND result_key_id IS NOT NULL)",
            name=op.f("ck_hunt_runs_result_envelope_complete"),
        ),
        sa.ForeignKeyConstraint(
            ["background_job_id"],
            ["background_jobs.id"],
            name=op.f("fk_hunt_runs_background_job_id_background_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_hunt_runs_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hunt_runs")),
        sa.UniqueConstraint(
            "background_job_id",
            name=op.f("uq_hunt_runs_background_job_id"),
        ),
        sa.UniqueConstraint(
            "owner_id",
            "idempotency_key_hash",
            name="uq_hunt_runs_owner_idempotency_key_hash",
        ),
    )
    op.create_index(
        "ix_hunt_runs_access_expiry", "hunt_runs", ["access_expires_at"]
    )
    op.create_index(
        "ix_hunt_runs_owner_created", "hunt_runs", ["owner_id", "created_at"]
    )
    op.create_index(
        "ix_hunt_runs_request_expiry", "hunt_runs", ["request_expires_at"]
    )

    op.create_table(
        "hunt_outcomes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("hunt_run_id", sa.String(length=32), nullable=False),
        sa.Column("draft_id", sa.String(length=128), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=32), nullable=False),
        sa.Column("logged_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["hunt_run_id"],
            ["hunt_runs.id"],
            name=op.f("fk_hunt_outcomes_hunt_run_id_hunt_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hunt_outcomes")),
    )
    op.create_index("ix_hunt_outcomes_draft", "hunt_outcomes", ["draft_id"])
    op.create_index(
        "ix_hunt_outcomes_run_logged",
        "hunt_outcomes",
        ["hunt_run_id", "logged_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_hunt_outcomes_run_logged", table_name="hunt_outcomes")
    op.drop_index("ix_hunt_outcomes_draft", table_name="hunt_outcomes")
    op.drop_table("hunt_outcomes")
    op.drop_index("ix_hunt_runs_request_expiry", table_name="hunt_runs")
    op.drop_index("ix_hunt_runs_owner_created", table_name="hunt_runs")
    op.drop_index("ix_hunt_runs_access_expiry", table_name="hunt_runs")
    op.drop_table("hunt_runs")
