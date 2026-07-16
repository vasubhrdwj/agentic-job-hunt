"""Add exact manual application transitions and submission records.

Revision ID: 20260714_0011
Revises: 20260714_0010
Create Date: 2026-07-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260714_0011"
down_revision = "20260714_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _add_submission_reference_keys()
    _allow_application_stages()
    _allow_action_kinds()
    _create_application_submissions()
    _allow_transition_activity()


def downgrade() -> None:
    # The old schema cannot represent readiness or an exact submission. Restore
    # the original review action before putting every application back in its
    # one representable stage.
    op.execute(
        "DELETE FROM owner_mutation_receipts "
        "WHERE namespace LIKE 'application.transition:%'"
    )
    op.execute(
        "DELETE FROM application_activity_events "
        "WHERE event_type IN ('application_ready_to_apply', 'application_applied')"
    )
    op.execute("DELETE FROM application_submissions")
    op.execute(
        "DELETE FROM action_items "
        "WHERE kind IN ('submit_application', 'follow_up_application')"
    )
    op.execute(
        "UPDATE action_items SET status = 'open', completed_at = NULL, "
        "cancelled_at = NULL, version = version + 1, updated_at = CURRENT_TIMESTAMP "
        "WHERE kind = 'review_and_prepare_application' "
        "AND application_id IN ("
        "SELECT id FROM applications WHERE stage IN ('ready_to_apply', 'applied')"
        ")"
    )
    op.execute(
        "UPDATE applications SET stage = 'pursuing', version = version + 1, "
        "updated_at = CURRENT_TIMESTAMP "
        "WHERE stage IN ('ready_to_apply', 'applied')"
    )

    op.drop_index(
        "uq_application_activity_events_owner_submission",
        table_name="application_activity_events",
    )
    op.drop_index(
        "uq_application_activity_events_owner_applied",
        table_name="application_activity_events",
    )
    op.drop_index(
        "uq_application_activity_events_owner_ready",
        table_name="application_activity_events",
    )
    with op.batch_alter_table(
        "application_activity_events", recreate=_batch_recreate_mode()
    ) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_application_activity_events_event_shape"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_application_activity_events_event_type"), type_="check"
        )
        batch_op.drop_constraint(
            "fk_application_activity_events_owner_submission", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_application_activity_events_owner_previous_action", type_="foreignkey"
        )
        batch_op.create_check_constraint(
            op.f("ck_application_activity_events_event_type"),
            "event_type = 'application_created'",
        )
        batch_op.create_check_constraint(
            op.f("ck_application_activity_events_creation_shape"),
            "sequence_number = 1 AND from_stage IS NULL "
            "AND to_stage = 'pursuing' AND action_item_id IS NOT NULL",
        )
        batch_op.drop_column("submission_id")
        batch_op.drop_column("previous_action_item_id")

    op.drop_index(
        "ix_application_submissions_owner_applied",
        table_name="application_submissions",
    )
    op.drop_table("application_submissions")
    _restore_action_kind()
    _restore_application_stage()
    _drop_submission_reference_keys()


def _add_submission_reference_keys() -> None:
    with op.batch_alter_table(
        "application_pack_events", recreate=_batch_recreate_mode()
    ) as batch_op:
        batch_op.create_unique_constraint(
            "uq_application_pack_events_submission_ref",
            [
                "owner_id",
                "application_id",
                "application_pack_id",
                "revision_id",
                "id",
            ],
        )


def _drop_submission_reference_keys() -> None:
    with op.batch_alter_table(
        "application_pack_events", recreate=_batch_recreate_mode()
    ) as batch_op:
        batch_op.drop_constraint(
            "uq_application_pack_events_submission_ref", type_="unique"
        )


def _allow_application_stages() -> None:
    with op.batch_alter_table(
        "applications", recreate=_batch_recreate_mode()
    ) as batch_op:
        batch_op.drop_constraint(op.f("ck_applications_stage"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_applications_stage"),
            "stage IN ('pursuing', 'ready_to_apply', 'applied')",
        )


def _restore_application_stage() -> None:
    with op.batch_alter_table(
        "applications", recreate=_batch_recreate_mode()
    ) as batch_op:
        batch_op.drop_constraint(op.f("ck_applications_stage"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_applications_stage"), "stage = 'pursuing'"
        )


def _allow_action_kinds() -> None:
    with op.batch_alter_table(
        "action_items", recreate=_batch_recreate_mode()
    ) as batch_op:
        batch_op.drop_constraint(op.f("ck_action_items_kind"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_action_items_kind"),
            "kind IN ('review_and_prepare_application', 'submit_application', "
            "'follow_up_application')",
        )


def _restore_action_kind() -> None:
    with op.batch_alter_table(
        "action_items", recreate=_batch_recreate_mode()
    ) as batch_op:
        batch_op.drop_constraint(op.f("ck_action_items_kind"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_action_items_kind"),
            "kind = 'review_and_prepare_application'",
        )


def _create_application_submissions() -> None:
    op.create_table(
        "application_submissions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.String(length=32), nullable=False),
        sa.Column("application_pack_id", sa.String(length=32), nullable=False),
        sa.Column(
            "application_pack_revision_id", sa.String(length=32), nullable=False
        ),
        sa.Column(
            "application_pack_review_event_id", sa.String(length=32), nullable=False
        ),
        sa.Column(
            "application_artifact_revision_id", sa.String(length=32), nullable=False
        ),
        sa.Column(
            "application_artifact_approval_event_id",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "tailored_resume_version_id", sa.String(length=32), nullable=False
        ),
        sa.Column("destination_url", sa.Text(), nullable=False),
        sa.Column("applied_on", sa.Date(), nullable=False),
        sa.Column("submission_method", sa.String(length=16), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "submission_method = 'manual'",
            name=op.f("ck_application_submissions_submission_method"),
        ),
        sa.CheckConstraint(
            "length(destination_url) BETWEEN 9 AND 2048 "
            "AND destination_url LIKE 'https://%'",
            name=op.f("ck_application_submissions_destination_url"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_application_submissions_owner_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "application_pack_id"],
            [
                "application_packs.owner_id",
                "application_packs.application_id",
                "application_packs.id",
            ],
            name="fk_application_submissions_owner_pack",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            [
                "owner_id",
                "application_id",
                "application_pack_id",
                "application_pack_revision_id",
            ],
            [
                "application_pack_revisions.owner_id",
                "application_pack_revisions.application_id",
                "application_pack_revisions.application_pack_id",
                "application_pack_revisions.id",
            ],
            name="fk_application_submissions_owner_pack_revision",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            [
                "owner_id",
                "application_id",
                "application_pack_id",
                "application_pack_revision_id",
                "application_pack_review_event_id",
            ],
            [
                "application_pack_events.owner_id",
                "application_pack_events.application_id",
                "application_pack_events.application_pack_id",
                "application_pack_events.revision_id",
                "application_pack_events.id",
            ],
            name="fk_application_submissions_owner_pack_review",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            [
                "owner_id",
                "application_id",
                "application_pack_id",
                "application_artifact_revision_id",
            ],
            [
                "application_artifact_revisions.owner_id",
                "application_artifact_revisions.application_id",
                "application_artifact_revisions.application_pack_id",
                "application_artifact_revisions.id",
            ],
            name="fk_application_submissions_owner_artifact_revision",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            [
                "owner_id",
                "application_id",
                "application_pack_id",
                "application_artifact_revision_id",
                "application_artifact_approval_event_id",
            ],
            [
                "application_artifact_events.owner_id",
                "application_artifact_events.application_id",
                "application_artifact_events.application_pack_id",
                "application_artifact_events.artifact_revision_id",
                "application_artifact_events.id",
            ],
            name="fk_application_submissions_owner_artifact_approval",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "tailored_resume_version_id"],
            ["resume_versions.owner_id", "resume_versions.id"],
            name="fk_application_submissions_owner_tailored_resume",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_application_submissions_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_submissions")),
        sa.UniqueConstraint(
            "owner_id", "id", name="uq_application_submissions_owner_id_id"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            "id",
            name="uq_application_submissions_owner_application_id",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            name="uq_application_submissions_owner_application",
        ),
    )
    op.create_index(
        "ix_application_submissions_owner_applied",
        "application_submissions",
        ["owner_id", "applied_on", "recorded_at"],
    )


def _allow_transition_activity() -> None:
    with op.batch_alter_table(
        "application_activity_events", recreate=_batch_recreate_mode()
    ) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_application_activity_events_creation_shape"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_application_activity_events_event_type"), type_="check"
        )
        batch_op.add_column(
            sa.Column("previous_action_item_id", sa.String(length=32), nullable=True)
        )
        batch_op.add_column(
            sa.Column("submission_id", sa.String(length=32), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_application_activity_events_owner_previous_action",
            "action_items",
            ["owner_id", "application_id", "previous_action_item_id"],
            ["owner_id", "application_id", "id"],
            deferrable=True,
            initially="DEFERRED",
        )
        batch_op.create_foreign_key(
            "fk_application_activity_events_owner_submission",
            "application_submissions",
            ["owner_id", "application_id", "submission_id"],
            ["owner_id", "application_id", "id"],
            deferrable=True,
            initially="DEFERRED",
        )
        batch_op.create_check_constraint(
            op.f("ck_application_activity_events_event_type"),
            "event_type IN ('application_created', 'application_ready_to_apply', "
            "'application_applied')",
        )
        batch_op.create_check_constraint(
            op.f("ck_application_activity_events_event_shape"),
            "(event_type = 'application_created' AND sequence_number = 1 "
            "AND from_stage IS NULL AND to_stage = 'pursuing' "
            "AND previous_action_item_id IS NULL AND submission_id IS NULL) OR "
            "(event_type = 'application_ready_to_apply' AND sequence_number = 2 "
            "AND from_stage = 'pursuing' AND to_stage = 'ready_to_apply' "
            "AND previous_action_item_id IS NOT NULL "
            "AND previous_action_item_id <> action_item_id "
            "AND submission_id IS NULL) OR "
            "(event_type = 'application_applied' AND sequence_number = 3 "
            "AND from_stage = 'ready_to_apply' AND to_stage = 'applied' "
            "AND previous_action_item_id IS NOT NULL "
            "AND previous_action_item_id <> action_item_id "
            "AND submission_id IS NOT NULL)",
        )
    op.create_index(
        "uq_application_activity_events_owner_ready",
        "application_activity_events",
        ["owner_id", "application_id"],
        unique=True,
        postgresql_where=sa.text("event_type = 'application_ready_to_apply'"),
        sqlite_where=sa.text("event_type = 'application_ready_to_apply'"),
    )
    op.create_index(
        "uq_application_activity_events_owner_applied",
        "application_activity_events",
        ["owner_id", "application_id"],
        unique=True,
        postgresql_where=sa.text("event_type = 'application_applied'"),
        sqlite_where=sa.text("event_type = 'application_applied'"),
    )
    op.create_index(
        "uq_application_activity_events_owner_submission",
        "application_activity_events",
        ["owner_id", "submission_id"],
        unique=True,
        postgresql_where=sa.text("submission_id IS NOT NULL"),
        sqlite_where=sa.text("submission_id IS NOT NULL"),
    )


def _batch_recreate_mode() -> str:
    """Recreate tables only where SQLite requires batch-copy DDL."""

    return "always" if op.get_bind().dialect.name == "sqlite" else "auto"
