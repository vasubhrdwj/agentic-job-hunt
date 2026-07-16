"""Add owner retention controls and payload-free deletion receipts.

Revision ID: 20260715_0018
Revises: 20260715_0017
Create Date: 2026-07-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260715_0018"
down_revision = "20260715_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owner_privacy_settings",
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column(
            "hunt_run_retention_days",
            sa.Integer(),
            server_default="30",
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
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
            "hunt_run_retention_days BETWEEN 1 AND 30",
            name=op.f("ck_owner_privacy_settings_hunt_run_retention_days"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_owner_privacy_settings_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_owner_privacy_settings_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "owner_id", name=op.f("pk_owner_privacy_settings")
        ),
    )

    op.create_table(
        "privacy_deletion_receipts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(owner_id_hash) = 64",
            name=op.f("ck_privacy_deletion_receipts_owner_hash"),
        ),
        sa.CheckConstraint(
            "length(idempotency_key_hash) = 64",
            name=op.f("ck_privacy_deletion_receipts_idempotency_hash"),
        ),
        sa.CheckConstraint(
            "length(request_hash) = 64",
            name=op.f("ck_privacy_deletion_receipts_request_hash"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_privacy_deletion_receipts")),
        sa.UniqueConstraint(
            "owner_id_hash",
            "idempotency_key_hash",
            name="uq_privacy_deletion_receipts_owner_key",
        ),
    )
    op.create_index(
        "ix_privacy_deletion_receipts_deleted",
        "privacy_deletion_receipts",
        ["deleted_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    settings = int(
        bind.execute(sa.text("SELECT COUNT(*) FROM owner_privacy_settings")).scalar_one()
    )
    receipts = int(
        bind.execute(sa.text("SELECT COUNT(*) FROM privacy_deletion_receipts")).scalar_one()
    )
    if settings or receipts:
        raise RuntimeError(
            "refusing privacy-controls downgrade while retention settings or "
            "deletion receipts exist; preserve/export policy state before an "
            "operator-authorized cleanup"
        )
    op.drop_index(
        "ix_privacy_deletion_receipts_deleted",
        table_name="privacy_deletion_receipts",
    )
    op.drop_table("privacy_deletion_receipts")
    op.drop_table("owner_privacy_settings")
