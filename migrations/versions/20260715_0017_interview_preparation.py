"""Add encrypted evidence-pinned interview preparation.

Revision ID: 20260715_0017
Revises: 20260715_0016
Create Date: 2026-07-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260715_0017"
down_revision = "20260715_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_interview_preparations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
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
            "version >= 1",
            name=op.f("ck_application_interview_preparations_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_interview_preps_owner_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_application_interview_preparations_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_application_interview_preparations")
        ),
        sa.UniqueConstraint(
            "owner_id", "id", name="uq_interview_preps_owner_id"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            "id",
            name="uq_interview_preps_owner_application_id",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            name="uq_interview_preps_owner_application",
        ),
    )
    op.create_index(
        "ix_interview_preps_owner_updated",
        "application_interview_preparations",
        ["owner_id", "updated_at"],
    )

    op.create_table(
        "application_interview_preparation_revisions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.String(length=32), nullable=False),
        sa.Column("preparation_id", sa.String(length=32), nullable=False),
        sa.Column("parent_revision_id", sa.String(length=32), nullable=True),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("application_submission_id", sa.String(length=32), nullable=False),
        sa.Column("application_pack_id", sa.String(length=32), nullable=False),
        sa.Column("grounding_revision_id", sa.String(length=32), nullable=False),
        sa.Column("job_posting_id", sa.String(length=32), nullable=False),
        sa.Column("posting_version_id", sa.String(length=32), nullable=False),
        sa.Column("target_kind", sa.String(length=24), nullable=False),
        sa.Column("interview_round_id", sa.String(length=32), nullable=True),
        sa.Column("interview_round_version", sa.Integer(), nullable=True),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("recording_method", sa.String(length=20), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "parent_revision_id IS NULL OR parent_revision_id <> id",
            name=op.f("ck_application_interview_preparation_revisions_parent"),
        ),
        sa.CheckConstraint(
            "revision_number >= 1",
            name=op.f("ck_application_interview_preparation_revisions_revision_positive"),
        ),
        sa.CheckConstraint(
            "target_kind IN ('recruiter_screen', 'interview_round')",
            name=op.f("ck_application_interview_preparation_revisions_target"),
        ),
        sa.CheckConstraint(
            "(target_kind = 'recruiter_screen' AND interview_round_id IS NULL) OR "
            "(target_kind = 'interview_round' AND interview_round_id IS NOT NULL)",
            name=op.f("ck_application_interview_preparation_revisions_target_shape"),
        ),
        sa.CheckConstraint(
            "(target_kind = 'recruiter_screen' AND interview_round_version IS NULL) OR "
            "(target_kind = 'interview_round' AND interview_round_version >= 1)",
            name=op.f("ck_application_interview_preparation_revisions_target_version"),
        ),
        sa.CheckConstraint(
            "recording_method = 'owner_authored'",
            name=op.f("ck_application_interview_preparation_revisions_recording"),
        ),
        sa.CheckConstraint(
            "length(trim(encrypted_payload)) >= 1 "
            "AND length(trim(encryption_key_id)) BETWEEN 1 AND 32",
            name=op.f("ck_application_interview_preparation_revisions_envelope"),
        ),
        sa.CheckConstraint(
            "length(source_fingerprint) = 64",
            name=op.f("ck_application_interview_preparation_revisions_source_hash"),
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64",
            name=op.f("ck_application_interview_preparation_revisions_content_hash"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "preparation_id"],
            [
                "application_interview_preparations.owner_id",
                "application_interview_preparations.application_id",
                "application_interview_preparations.id",
            ],
            name="fk_interview_prep_revisions_owner_prep",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "preparation_id", "parent_revision_id"],
            [
                "application_interview_preparation_revisions.owner_id",
                "application_interview_preparation_revisions.application_id",
                "application_interview_preparation_revisions.preparation_id",
                "application_interview_preparation_revisions.id",
            ],
            name="fk_interview_prep_revisions_owner_parent",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "application_submission_id"],
            [
                "application_submissions.owner_id",
                "application_submissions.application_id",
                "application_submissions.id",
            ],
            name="fk_interview_prep_revisions_owner_submission",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "owner_id",
                "application_id",
                "application_pack_id",
                "grounding_revision_id",
            ],
            [
                "application_pack_revisions.owner_id",
                "application_pack_revisions.application_id",
                "application_pack_revisions.application_pack_id",
                "application_pack_revisions.id",
            ],
            name="fk_interview_prep_revisions_owner_grounding",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "job_posting_id", "posting_version_id"],
            [
                "job_posting_versions.owner_id",
                "job_posting_versions.job_posting_id",
                "job_posting_versions.id",
            ],
            name="fk_interview_prep_revisions_owner_posting_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "interview_round_id"],
            [
                "application_interview_rounds.owner_id",
                "application_interview_rounds.application_id",
                "application_interview_rounds.id",
            ],
            name="fk_interview_prep_revisions_owner_round",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f(
                "fk_application_interview_preparation_revisions_owner_id_owners"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_application_interview_preparation_revisions")
        ),
        sa.UniqueConstraint(
            "owner_id", "id", name="uq_interview_prep_revisions_owner_id"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            "preparation_id",
            "id",
            name="uq_interview_prep_revisions_event_ref",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "preparation_id",
            "revision_number",
            name="uq_interview_prep_revisions_owner_number",
        ),
    )
    op.create_index(
        "ix_interview_prep_revisions_timeline",
        "application_interview_preparation_revisions",
        ["owner_id", "application_id", "preparation_id", "revision_number"],
    )


def downgrade() -> None:
    _assert_downgrade_is_lossless()
    op.execute(
        "DELETE FROM owner_mutation_receipts "
        "WHERE namespace LIKE 'interview_preparation.revision:%'"
    )
    op.drop_index(
        "ix_interview_prep_revisions_timeline",
        table_name="application_interview_preparation_revisions",
    )
    op.drop_table("application_interview_preparation_revisions")
    op.drop_index(
        "ix_interview_preps_owner_updated",
        table_name="application_interview_preparations",
    )
    op.drop_table("application_interview_preparations")


def _assert_downgrade_is_lossless() -> None:
    connection = op.get_bind()
    if connection.execute(
        sa.text(
            "SELECT 1 FROM application_interview_preparation_revisions LIMIT 1"
        )
    ).first() is not None:
        raise RuntimeError(
            "Cannot downgrade 20260715_0017 without losing encrypted, owner-authored "
            "interview preparation revisions."
        )
    if connection.execute(
        sa.text("SELECT 1 FROM application_interview_preparations LIMIT 1")
    ).first() is not None:
        raise RuntimeError(
            "Cannot downgrade 20260715_0017 without losing interview preparation state."
        )
