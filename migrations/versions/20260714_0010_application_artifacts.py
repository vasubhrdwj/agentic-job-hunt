"""Add deterministic grounded application artifacts.

Revision ID: 20260714_0010
Revises: 20260714_0009
Create Date: 2026-07-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260714_0010"
down_revision = "20260714_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_artifact_revisions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.String(length=32), nullable=False),
        sa.Column("application_pack_id", sa.String(length=32), nullable=False),
        sa.Column("grounding_revision_id", sa.String(length=32), nullable=False),
        sa.Column("parent_artifact_revision_id", sa.String(length=32), nullable=True),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("generator_version", sa.String(length=64), nullable=False),
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
            "parent_artifact_revision_id IS NULL OR parent_artifact_revision_id <> id",
            name=op.f("ck_application_artifact_revisions_parent_not_self"),
        ),
        sa.CheckConstraint(
            "revision_number >= 1",
            name=op.f("ck_application_artifact_revisions_revision_number_positive"),
        ),
        sa.CheckConstraint(
            "source = 'deterministic'",
            name=op.f("ck_application_artifact_revisions_source"),
        ),
        sa.CheckConstraint(
            "generator_version = 'application-artifacts-deterministic-v1'",
            name=op.f("ck_application_artifact_revisions_generator_version"),
        ),
        sa.CheckConstraint(
            "length(trim(encrypted_payload)) >= 1 "
            "AND length(trim(encryption_key_id)) BETWEEN 1 AND 32",
            name=op.f("ck_application_artifact_revisions_encrypted_payload_envelope"),
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64",
            name=op.f("ck_application_artifact_revisions_content_hash"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "application_pack_id"],
            [
                "application_packs.owner_id",
                "application_packs.application_id",
                "application_packs.id",
            ],
            name="fk_application_artifact_revisions_owner_pack",
            ondelete="CASCADE",
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
            name="fk_application_artifact_revisions_owner_grounding",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "owner_id",
                "application_id",
                "application_pack_id",
                "parent_artifact_revision_id",
            ],
            [
                "application_artifact_revisions.owner_id",
                "application_artifact_revisions.application_id",
                "application_artifact_revisions.application_pack_id",
                "application_artifact_revisions.id",
            ],
            name="fk_application_artifact_revisions_owner_parent",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_application_artifact_revisions_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_application_artifact_revisions")
        ),
        sa.UniqueConstraint(
            "owner_id",
            "id",
            name="uq_application_artifact_revisions_owner_id_id",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            "application_pack_id",
            "id",
            name="uq_application_artifact_revisions_event_ref",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_pack_id",
            "revision_number",
            name="uq_application_artifact_revisions_owner_number",
        ),
    )
    op.create_index(
        "ix_application_artifact_revisions_pack_created",
        "application_artifact_revisions",
        ["owner_id", "application_pack_id", "revision_number"],
    )

    op.create_table(
        "application_artifact_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.String(length=32), nullable=False),
        sa.Column("application_pack_id", sa.String(length=32), nullable=False),
        sa.Column("artifact_revision_id", sa.String(length=32), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("tailored_resume_version_id", sa.String(length=32), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sequence_number >= 1",
            name=op.f("ck_application_artifact_events_sequence_number_positive"),
        ),
        sa.CheckConstraint(
            "event_type IN ('approved', 'rejected')",
            name=op.f("ck_application_artifact_events_event_type"),
        ),
        sa.CheckConstraint(
            "(event_type = 'approved' AND tailored_resume_version_id IS NOT NULL) OR "
            "(event_type = 'rejected' AND tailored_resume_version_id IS NULL)",
            name=op.f("ck_application_artifact_events_event_resume_shape"),
        ),
        sa.CheckConstraint(
            "length(idempotency_key_hash) = 64",
            name=op.f("ck_application_artifact_events_mutation_hash"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "application_pack_id"],
            [
                "application_packs.owner_id",
                "application_packs.application_id",
                "application_packs.id",
            ],
            name="fk_application_artifact_events_owner_pack",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            [
                "owner_id",
                "application_id",
                "application_pack_id",
                "artifact_revision_id",
            ],
            [
                "application_artifact_revisions.owner_id",
                "application_artifact_revisions.application_id",
                "application_artifact_revisions.application_pack_id",
                "application_artifact_revisions.id",
            ],
            name="fk_application_artifact_events_owner_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "tailored_resume_version_id"],
            ["resume_versions.owner_id", "resume_versions.id"],
            name="fk_application_artifact_events_owner_resume",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_application_artifact_events_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_artifact_events")),
        sa.UniqueConstraint(
            "owner_id", "id", name="uq_application_artifact_events_owner_id_id"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_pack_id",
            "sequence_number",
            name="uq_application_artifact_events_owner_sequence",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_pack_id",
            "artifact_revision_id",
            name="uq_application_artifact_events_owner_terminal",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_pack_id",
            "idempotency_key_hash",
            name="uq_application_artifact_events_owner_mutation",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            "application_pack_id",
            "artifact_revision_id",
            "id",
            name="uq_application_artifact_events_submission_ref",
        ),
    )
    op.create_index(
        "ix_application_artifact_events_timeline",
        "application_artifact_events",
        ["owner_id", "application_pack_id", "occurred_at", "sequence_number"],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM owner_mutation_receipts "
        "WHERE namespace LIKE 'application_artifact.%'"
    )
    op.drop_index(
        "ix_application_artifact_events_timeline",
        table_name="application_artifact_events",
    )
    op.drop_table("application_artifact_events")
    op.drop_index(
        "ix_application_artifact_revisions_pack_created",
        table_name="application_artifact_revisions",
    )
    op.drop_table("application_artifact_revisions")
