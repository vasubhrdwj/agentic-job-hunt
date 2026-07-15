"""Add immutable weekly-review attribution and action history.

Revision ID: 20260715_0016
Revises: 20260715_0015
Create Date: 2026-07-15
"""

from __future__ import annotations

from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = "20260715_0016"
down_revision = "20260715_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_applications_metric_snapshot_target",
        "applications",
        ["owner_id", "id", "job_posting_id", "pursued_posting_version_id"],
        unique=True,
    )
    op.create_table(
        "application_metric_snapshots",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.String(length=32), nullable=False),
        sa.Column("job_posting_id", sa.String(length=32), nullable=False),
        sa.Column("pursued_posting_version_id", sa.String(length=32), nullable=False),
        sa.Column("acquisition_source", sa.String(length=32), nullable=False),
        sa.Column("attribution_status", sa.String(length=32), nullable=False),
        sa.Column("saved_search_id", sa.String(length=32), nullable=True),
        sa.Column("saved_search_version", sa.Integer(), nullable=True),
        sa.Column("saved_search_name", sa.String(length=120), nullable=True),
        sa.Column("career_track_id", sa.String(length=32), nullable=True),
        sa.Column("career_track_version", sa.Integer(), nullable=True),
        sa.Column("career_track_name", sa.String(length=120), nullable=True),
        sa.Column("assessment_state", sa.String(length=20), nullable=False),
        sa.Column("assessment_band", sa.String(length=20), nullable=True),
        sa.Column(
            "assessment_algorithm_version", sa.String(length=64), nullable=True
        ),
        sa.Column("assessment_reason", sa.String(length=32), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "acquisition_source IN ('job_hunt_search', 'referral', "
            "'recruiter_inbound', 'direct_company', 'job_board', 'other')",
            name=op.f("ck_application_metric_snapshots_acquisition_source"),
        ),
        sa.CheckConstraint(
            "attribution_status IN ('captured', 'attribution_missing')",
            name=op.f("ck_application_metric_snapshots_attribution_status"),
        ),
        sa.CheckConstraint(
            "saved_search_version IS NULL OR saved_search_version >= 1",
            name=op.f("ck_application_metric_snapshots_saved_search_version_positive"),
        ),
        sa.CheckConstraint(
            "career_track_version IS NULL OR career_track_version >= 1",
            name=op.f("ck_application_metric_snapshots_career_track_version_positive"),
        ),
        sa.CheckConstraint(
            "(attribution_status = 'attribution_missing' "
            "AND saved_search_id IS NULL AND saved_search_version IS NULL "
            "AND saved_search_name IS NULL AND career_track_id IS NULL "
            "AND career_track_version IS NULL AND career_track_name IS NULL) OR "
            "(attribution_status = 'captured' "
            "AND acquisition_source = 'job_hunt_search' "
            "AND saved_search_id IS NOT NULL AND saved_search_version IS NOT NULL "
            "AND saved_search_name IS NOT NULL AND career_track_id IS NOT NULL "
            "AND career_track_version IS NOT NULL AND career_track_name IS NOT NULL) OR "
            "(attribution_status = 'captured' "
            "AND acquisition_source <> 'job_hunt_search' "
            "AND saved_search_id IS NULL AND saved_search_version IS NULL "
            "AND saved_search_name IS NULL AND career_track_id IS NULL "
            "AND career_track_version IS NULL AND career_track_name IS NULL)",
            name=op.f("ck_application_metric_snapshots_attribution_shape"),
        ),
        sa.CheckConstraint(
            "assessment_state IN ('assessed', 'not_assessed')",
            name=op.f("ck_application_metric_snapshots_assessment_state"),
        ),
        sa.CheckConstraint(
            "assessment_band IS NULL OR "
            "assessment_band IN ('strong', 'core', 'stretch')",
            name=op.f("ck_application_metric_snapshots_assessment_band"),
        ),
        sa.CheckConstraint(
            "assessment_reason IS NULL OR assessment_reason IN ("
            "'assessment_pending', 'resume_unavailable', "
            "'description_unavailable', 'not_requested')",
            name=op.f("ck_application_metric_snapshots_assessment_reason"),
        ),
        sa.CheckConstraint(
            "(assessment_state = 'assessed' AND assessment_band IS NOT NULL "
            "AND assessment_algorithm_version IS NOT NULL "
            "AND assessment_reason IS NULL) OR "
            "(assessment_state = 'not_assessed' AND assessment_band IS NULL "
            "AND assessment_algorithm_version IS NULL "
            "AND assessment_reason IS NOT NULL)",
            name=op.f("ck_application_metric_snapshots_assessment_shape"),
        ),
        sa.ForeignKeyConstraint(
            [
                "owner_id",
                "application_id",
                "job_posting_id",
                "pursued_posting_version_id",
            ],
            [
                "applications.owner_id",
                "applications.id",
                "applications.job_posting_id",
                "applications.pursued_posting_version_id",
            ],
            name="fk_application_metric_snapshots_owner_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "job_posting_id", "pursued_posting_version_id"],
            [
                "job_posting_versions.owner_id",
                "job_posting_versions.job_posting_id",
                "job_posting_versions.id",
            ],
            name="fk_application_metric_snapshots_owner_posting_version",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "saved_search_id"],
            ["saved_searches.owner_id", "saved_searches.id"],
            name="fk_application_metric_snapshots_owner_saved_search",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "career_track_id"],
            ["career_tracks.owner_id", "career_tracks.id"],
            name="fk_application_metric_snapshots_owner_career_track",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_application_metric_snapshots_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_metric_snapshots")),
        sa.UniqueConstraint(
            "owner_id", "id", name="uq_application_metric_snapshots_owner_id_id"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            name="uq_application_metric_snapshots_owner_application",
        ),
    )
    op.create_index(
        "ix_application_metric_snapshots_owner_recorded",
        "application_metric_snapshots",
        ["owner_id", "recorded_at", "application_id"],
    )

    op.create_table(
        "application_action_reviews",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.String(length=32), nullable=False),
        sa.Column("action_item_id", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("prior_due_on", sa.Date(), nullable=False),
        sa.Column("new_due_on", sa.Date(), nullable=False),
        sa.Column("prior_action_version", sa.Integer(), nullable=False),
        sa.Column("new_action_version", sa.Integer(), nullable=False),
        sa.Column("prior_application_version", sa.Integer(), nullable=False),
        sa.Column("new_application_version", sa.Integer(), nullable=False),
        sa.Column("recording_method", sa.String(length=16), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('continue', 'waiting')",
            name=op.f("ck_application_action_reviews_decision"),
        ),
        sa.CheckConstraint(
            "new_due_on > prior_due_on",
            name=op.f("ck_application_action_reviews_due_date_progresses"),
        ),
        sa.CheckConstraint(
            "new_action_version = prior_action_version + 1",
            name=op.f("ck_application_action_reviews_action_version_progresses"),
        ),
        sa.CheckConstraint(
            "new_application_version = prior_application_version + 1",
            name=op.f("ck_application_action_reviews_application_version_progresses"),
        ),
        sa.CheckConstraint(
            "prior_action_version >= 1 AND prior_application_version >= 1",
            name=op.f("ck_application_action_reviews_prior_versions_positive"),
        ),
        sa.CheckConstraint(
            "recording_method = 'manual'",
            name=op.f("ck_application_action_reviews_recording_method"),
        ),
        sa.CheckConstraint(
            "length(idempotency_key_hash) = 64",
            name=op.f("ck_application_action_reviews_mutation_hash"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_application_action_reviews_owner_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "action_item_id"],
            ["action_items.owner_id", "action_items.application_id", "action_items.id"],
            name="fk_application_action_reviews_owner_action",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_application_action_reviews_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_action_reviews")),
        sa.UniqueConstraint(
            "owner_id", "id", name="uq_application_action_reviews_owner_id_id"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            "idempotency_key_hash",
            name="uq_application_action_reviews_owner_application_mutation",
        ),
    )
    op.create_index(
        "ix_application_action_reviews_timeline",
        "application_action_reviews",
        ["owner_id", "application_id", "recorded_at", "id"],
    )

    _backfill_explicit_missing_attribution()


def downgrade() -> None:
    _assert_downgrade_is_lossless()
    op.execute(
        "DELETE FROM owner_mutation_receipts "
        "WHERE namespace LIKE 'application.action_review:%'"
    )
    op.drop_index(
        "ix_application_action_reviews_timeline",
        table_name="application_action_reviews",
    )
    op.drop_table("application_action_reviews")
    op.drop_index(
        "ix_application_metric_snapshots_owner_recorded",
        table_name="application_metric_snapshots",
    )
    op.drop_table("application_metric_snapshots")
    op.drop_index(
        "uq_applications_metric_snapshot_target", table_name="applications"
    )


def _backfill_explicit_missing_attribution() -> None:
    connection = op.get_bind()
    applications = connection.execute(
        sa.text(
            "SELECT owner_id, id, job_posting_id, pursued_posting_version_id, "
            "created_at FROM applications"
        )
    ).mappings()
    for application in applications:
        connection.execute(
            sa.text(
                "INSERT INTO application_metric_snapshots ("
                "id, owner_id, application_id, job_posting_id, "
                "pursued_posting_version_id, acquisition_source, attribution_status, "
                "assessment_state, assessment_reason, recorded_at"
                ") VALUES ("
                ":id, :owner_id, :application_id, :job_posting_id, "
                ":pursued_posting_version_id, 'job_hunt_search', "
                "'attribution_missing', 'not_assessed', 'not_requested', :recorded_at"
                ")"
            ),
            {
                "id": uuid4().hex,
                "owner_id": application["owner_id"],
                "application_id": application["id"],
                "job_posting_id": application["job_posting_id"],
                "pursued_posting_version_id": application[
                    "pursued_posting_version_id"
                ],
                "recorded_at": application["created_at"],
            },
        )


def _assert_downgrade_is_lossless() -> None:
    connection = op.get_bind()
    if connection.execute(
        sa.text("SELECT 1 FROM application_action_reviews LIMIT 1")
    ).first() is not None:
        raise RuntimeError(
            "Cannot downgrade 20260715_0016 without losing application action "
            "review history: a review exists."
        )
    if connection.execute(
        sa.text(
            "SELECT 1 FROM application_metric_snapshots "
            "WHERE attribution_status = 'captured' LIMIT 1"
        )
    ).first() is not None:
        raise RuntimeError(
            "Cannot downgrade 20260715_0016 without losing captured application "
            "attribution: a captured snapshot exists."
        )
