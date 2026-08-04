"""Add encrypted, owner-scoped opportunity-fit evaluation cache.

Revision ID: 20260804_0021
Revises: 20260721_0020
Create Date: 2026-08-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260804_0021"
down_revision = "20260721_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "opportunity_fit_evaluations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("job_posting_id", sa.String(length=32), nullable=False),
        sa.Column("posting_version_id", sa.String(length=32), nullable=False),
        sa.Column("posting_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "profile_input_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("evaluator_version", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("result_schema_version", sa.Integer(), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(posting_hash) = 64",
            name=op.f("ck_opportunity_fit_evaluations_posting_hash_length"),
        ),
        sa.CheckConstraint(
            "length(profile_input_fingerprint) = 64",
            name=op.f(
                "ck_opportunity_fit_evaluations_profile_input_fingerprint_length"
            ),
        ),
        sa.CheckConstraint(
            "length(input_fingerprint) = 64",
            name=op.f("ck_opportunity_fit_evaluations_input_fingerprint_length"),
        ),
        sa.CheckConstraint(
            "length(trim(evaluator_version)) BETWEEN 1 AND 64",
            name=op.f("ck_opportunity_fit_evaluations_evaluator_version"),
        ),
        sa.CheckConstraint(
            "length(trim(provider)) BETWEEN 1 AND 64",
            name=op.f("ck_opportunity_fit_evaluations_provider"),
        ),
        sa.CheckConstraint(
            "length(trim(model)) BETWEEN 1 AND 120",
            name=op.f("ck_opportunity_fit_evaluations_model"),
        ),
        sa.CheckConstraint(
            "result_schema_version = 1",
            name=op.f("ck_opportunity_fit_evaluations_result_schema_version"),
        ),
        sa.CheckConstraint(
            "length(trim(encrypted_payload)) >= 1 "
            "AND length(trim(encryption_key_id)) BETWEEN 1 AND 32",
            name=op.f(
                "ck_opportunity_fit_evaluations_encrypted_payload_envelope"
            ),
        ),
        sa.CheckConstraint(
            "version = 1",
            name=op.f("ck_opportunity_fit_evaluations_immutable_version"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "job_posting_id", "posting_version_id"],
            [
                "job_posting_versions.owner_id",
                "job_posting_versions.job_posting_id",
                "job_posting_versions.id",
            ],
            name="fk_opportunity_fit_evaluations_owner_posting_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_opportunity_fit_evaluations_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_opportunity_fit_evaluations"),
        ),
        sa.UniqueConstraint(
            "owner_id",
            "id",
            name="uq_opportunity_fit_evaluations_owner_id_id",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "input_fingerprint",
            name="uq_opportunity_fit_evaluations_owner_input_fingerprint",
        ),
    )
    op.create_index(
        "ix_opportunity_fit_evaluations_owner_posting",
        "opportunity_fit_evaluations",
        ["owner_id", "job_posting_id", "created_at"],
    )
    op.create_index(
        "ix_opportunity_fit_evaluations_owner_created",
        "opportunity_fit_evaluations",
        ["owner_id", "created_at"],
    )


def downgrade() -> None:
    # These rows are a derived cache, not owner source data. It is safe to
    # discard them on rollback; the deterministic scorer remains available.
    op.drop_index(
        "ix_opportunity_fit_evaluations_owner_created",
        table_name="opportunity_fit_evaluations",
    )
    op.drop_index(
        "ix_opportunity_fit_evaluations_owner_posting",
        table_name="opportunity_fit_evaluations",
    )
    op.drop_table("opportunity_fit_evaluations")
