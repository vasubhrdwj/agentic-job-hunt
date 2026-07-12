"""Add owner profile, targets, encrypted resumes/evidence, and saved searches.

Revision ID: 20260713_0004
Revises: 20260712_0003
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260713_0004"
down_revision = "20260712_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_profiles",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=32), nullable=False),
        sa.Column("onboarding_state", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "onboarding_state IN ('profile', 'resume', 'career_track', 'evidence', "
            "'saved_search', 'complete')",
            name=op.f("ck_candidate_profiles_onboarding_state"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_candidate_profiles_version_positive")),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["owners.id"], name=op.f("fk_candidate_profiles_owner_id_owners"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_candidate_profiles")),
        sa.UniqueConstraint("owner_id", name=op.f("uq_candidate_profiles_owner_id")),
    )
    op.create_index(
        "ix_candidate_profiles_owner_updated", "candidate_profiles", ["owner_id", "updated_at"]
    )

    op.create_table(
        "career_tracks",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("role_families", sa.JSON(), nullable=False),
        sa.Column("seniority_levels", sa.JSON(), nullable=False),
        sa.Column("target_locations", sa.JSON(), nullable=False),
        sa.Column("priorities", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version >= 1", name=op.f("ck_career_tracks_version_positive")),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["owners.id"], name=op.f("fk_career_tracks_owner_id_owners"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_career_tracks")),
        sa.UniqueConstraint("owner_id", "id", name="uq_career_tracks_owner_id_id"),
        sa.UniqueConstraint("owner_id", "name", name="uq_career_tracks_owner_name"),
    )
    op.create_index("ix_career_tracks_owner_active", "career_tracks", ["owner_id", "active"])

    op.create_table(
        "resume_versions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("parent_id", sa.String(length=32), nullable=True),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("encrypted_content", sa.Text(), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("is_base", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("parent_id IS NULL OR parent_id <> id", name=op.f("ck_resume_versions_parent_not_self")),
        sa.CheckConstraint(
            "source IN ('pasted', 'uploaded', 'imported', 'edited')",
            name=op.f("ck_resume_versions_source"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_resume_versions_version_positive")),
        sa.ForeignKeyConstraint(
            ["owner_id", "parent_id"],
            ["resume_versions.owner_id", "resume_versions.id"],
            name="fk_resume_versions_owner_parent",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["owners.id"], name=op.f("fk_resume_versions_owner_id_owners"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resume_versions")),
        sa.UniqueConstraint("owner_id", "content_hash", name="uq_resume_versions_owner_content_hash"),
        sa.UniqueConstraint("owner_id", "id", name="uq_resume_versions_owner_id_id"),
    )
    op.create_index("ix_resume_versions_owner_created", "resume_versions", ["owner_id", "created_at"])
    op.create_index(
        "uq_resume_versions_owner_base",
        "resume_versions",
        ["owner_id"],
        unique=True,
        postgresql_where=sa.text("is_base"),
        sqlite_where=sa.text("is_base = 1"),
    )

    op.create_table(
        "achievement_evidence",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("source_resume_version_id", sa.String(length=32), nullable=True),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=32), nullable=False),
        sa.Column("skills", sa.JSON(), nullable=False),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("approval_state", sa.String(length=20), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "origin IN ('owner_entered', 'resume_suggestion')",
            name=op.f("ck_achievement_evidence_origin"),
        ),
        sa.CheckConstraint(
            "approval_state IN ('pending', 'approved', 'rejected', 'retired')",
            name=op.f("ck_achievement_evidence_approval_state"),
        ),
        sa.CheckConstraint(
            "(approval_state = 'pending' AND approved_at IS NULL AND rejected_at IS NULL AND retired_at IS NULL) OR "
            "(approval_state = 'approved' AND approved_at IS NOT NULL AND rejected_at IS NULL AND retired_at IS NULL) OR "
            "(approval_state = 'rejected' AND rejected_at IS NOT NULL AND approved_at IS NULL AND retired_at IS NULL) OR "
            "(approval_state = 'retired' AND approved_at IS NOT NULL AND rejected_at IS NULL AND retired_at IS NOT NULL)",
            name=op.f("ck_achievement_evidence_approval_timestamps"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_achievement_evidence_version_positive")),
        sa.ForeignKeyConstraint(
            ["owner_id", "source_resume_version_id"],
            ["resume_versions.owner_id", "resume_versions.id"],
            name="fk_achievement_evidence_owner_resume",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["owners.id"], name=op.f("fk_achievement_evidence_owner_id_owners"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_achievement_evidence")),
        sa.UniqueConstraint("owner_id", "id", name="uq_achievement_evidence_owner_id_id"),
    )
    op.create_index(
        "ix_achievement_evidence_owner_state", "achievement_evidence", ["owner_id", "approval_state"]
    )

    op.create_table(
        "saved_searches",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("career_track_id", sa.String(length=32), nullable=False),
        sa.Column("resume_version_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("criteria_schema_version", sa.Integer(), nullable=False),
        sa.Column("criteria", sa.JSON(), nullable=False),
        sa.Column("pack", sa.String(length=64), nullable=False),
        sa.Column("use_self_rag", sa.Boolean(), nullable=False),
        sa.Column("cadence", sa.String(length=20), nullable=False),
        sa.Column("schedule", sa.JSON(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "cadence IN ('manual', 'daily', 'weekdays', 'weekly')",
            name=op.f("ck_saved_searches_cadence"),
        ),
        sa.CheckConstraint(
            "((cadence = 'manual' OR NOT active) AND next_scan_at IS NULL) OR "
            "(cadence <> 'manual' AND active AND next_scan_at IS NOT NULL)",
            name=op.f("ck_saved_searches_schedule_next_scan"),
        ),
        sa.CheckConstraint(
            "criteria_schema_version >= 1", name=op.f("ck_saved_searches_criteria_schema_version")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_saved_searches_version_positive")),
        sa.ForeignKeyConstraint(
            ["owner_id", "career_track_id"],
            ["career_tracks.owner_id", "career_tracks.id"],
            name="fk_saved_searches_owner_track",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "resume_version_id"],
            ["resume_versions.owner_id", "resume_versions.id"],
            name="fk_saved_searches_owner_resume",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["owners.id"], name=op.f("fk_saved_searches_owner_id_owners"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_searches")),
        sa.UniqueConstraint("owner_id", "id", name="uq_saved_searches_owner_id_id"),
        sa.UniqueConstraint("owner_id", "name", name="uq_saved_searches_owner_name"),
    )
    op.create_index("ix_saved_searches_due", "saved_searches", ["active", "next_scan_at"])
    op.create_index(
        "ix_saved_searches_owner_track", "saved_searches", ["owner_id", "career_track_id"]
    )

    op.create_table(
        "owner_mutation_receipts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("namespace", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("result_version", sa.Integer(), nullable=True),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'completed')", name=op.f("ck_owner_mutation_receipts_status")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_owner_mutation_receipts_version_positive")),
        sa.CheckConstraint(
            "(status = 'pending' AND completed_at IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL AND resource_type IS NOT NULL AND resource_id IS NOT NULL)",
            name=op.f("ck_owner_mutation_receipts_completion"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["owners.id"], name=op.f("fk_owner_mutation_receipts_owner_id_owners"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_owner_mutation_receipts")),
        sa.UniqueConstraint(
            "owner_id", "namespace", "idempotency_key_hash",
            name="uq_owner_mutation_receipts_owner_namespace_key",
        ),
    )
    op.create_index(
        "ix_owner_mutation_receipts_owner_created",
        "owner_mutation_receipts",
        ["owner_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_owner_mutation_receipts_owner_created", table_name="owner_mutation_receipts")
    op.drop_table("owner_mutation_receipts")
    op.drop_index("ix_saved_searches_owner_track", table_name="saved_searches")
    op.drop_index("ix_saved_searches_due", table_name="saved_searches")
    op.drop_table("saved_searches")
    op.drop_index("ix_achievement_evidence_owner_state", table_name="achievement_evidence")
    op.drop_table("achievement_evidence")
    op.drop_index("uq_resume_versions_owner_base", table_name="resume_versions")
    op.drop_index("ix_resume_versions_owner_created", table_name="resume_versions")
    op.drop_table("resume_versions")
    op.drop_index("ix_career_tracks_owner_active", table_name="career_tracks")
    op.drop_table("career_tracks")
    op.drop_index("ix_candidate_profiles_owner_updated", table_name="candidate_profiles")
    op.drop_table("candidate_profiles")
