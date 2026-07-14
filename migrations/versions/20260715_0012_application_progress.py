"""Add durable application progress and terminal outcome records.

Revision ID: 20260715_0012
Revises: 20260714_0011
Create Date: 2026-07-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260715_0012"
down_revision = "20260714_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _create_application_outcomes()
    _allow_application_progress()
    _allow_progress_action_kinds()
    _allow_progress_activity()


def downgrade() -> None:
    _assert_downgrade_is_lossless()

    op.drop_index(
        "uq_application_activity_events_owner_outcome",
        table_name="application_activity_events",
    )
    op.drop_index(
        "uq_application_activity_events_owner_closed",
        table_name="application_activity_events",
    )
    op.drop_index(
        "uq_application_activity_events_owner_offer",
        table_name="application_activity_events",
    )
    op.drop_index(
        "uq_application_activity_events_owner_interviewing",
        table_name="application_activity_events",
    )
    op.drop_index(
        "uq_application_activity_events_owner_screening",
        table_name="application_activity_events",
    )
    with op.batch_alter_table(
        "application_activity_events", recreate="always"
    ) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_application_activity_events_event_shape"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_application_activity_events_event_type"), type_="check"
        )
        batch_op.drop_constraint(
            "fk_application_activity_events_owner_outcome", type_="foreignkey"
        )
        batch_op.alter_column(
            "action_item_id",
            existing_type=sa.String(length=32),
            nullable=False,
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
        batch_op.drop_column("outcome_id")
        batch_op.drop_column("effective_on")

    _restore_action_kinds()

    with op.batch_alter_table("applications", recreate="always") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_applications_outcome_shape"), type_="check"
        )
        batch_op.drop_constraint(op.f("ck_applications_stage"), type_="check")
        batch_op.drop_constraint(
            "fk_applications_owner_outcome", type_="foreignkey"
        )
        batch_op.create_check_constraint(
            op.f("ck_applications_stage"),
            "stage IN ('pursuing', 'ready_to_apply', 'applied')",
        )
        batch_op.drop_column("outcome_id")

    op.drop_index(
        "ix_application_outcomes_owner_metrics",
        table_name="application_outcomes",
    )
    op.drop_table("application_outcomes")


def _create_application_outcomes() -> None:
    op.create_table(
        "application_outcomes",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.String(length=32), nullable=False),
        sa.Column(
            "application_submission_id", sa.String(length=32), nullable=True
        ),
        sa.Column("stage_at_outcome", sa.String(length=24), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("outcome_on", sa.Date(), nullable=False),
        sa.Column("recording_method", sa.String(length=16), nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stage_at_outcome IN ('pursuing', 'ready_to_apply', 'applied', "
            "'screening', 'interviewing', 'offer')",
            name=op.f("ck_application_outcomes_stage_at_outcome"),
        ),
        sa.CheckConstraint(
            "outcome IN ('rejected', 'withdrawn', 'offer_accepted', "
            "'offer_declined', 'no_response', 'posting_closed')",
            name=op.f("ck_application_outcomes_outcome"),
        ),
        sa.CheckConstraint(
            "recording_method = 'manual'",
            name=op.f("ck_application_outcomes_recording_method"),
        ),
        sa.CheckConstraint(
            "(stage_at_outcome IN ('pursuing', 'ready_to_apply') "
            "AND outcome IN ('withdrawn', 'posting_closed') "
            "AND application_submission_id IS NULL) OR "
            "(stage_at_outcome IN ('applied', 'screening', 'interviewing', "
            "'offer') AND application_submission_id IS NOT NULL)",
            name=op.f("ck_application_outcomes_submission_shape"),
        ),
        sa.CheckConstraint(
            "(outcome IN ('offer_accepted', 'offer_declined') "
            "AND stage_at_outcome = 'offer') OR "
            "outcome NOT IN ('offer_accepted', 'offer_declined')",
            name=op.f("ck_application_outcomes_offer_outcome_stage"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_application_outcomes_owner_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "application_submission_id"],
            [
                "application_submissions.owner_id",
                "application_submissions.application_id",
                "application_submissions.id",
            ],
            name="fk_application_outcomes_owner_submission",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_application_outcomes_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_outcomes")),
        sa.UniqueConstraint(
            "owner_id", "id", name="uq_application_outcomes_owner_id_id"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            name="uq_application_outcomes_owner_application",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            "id",
            name="uq_application_outcomes_owner_application_id",
        ),
    )
    op.create_index(
        "ix_application_outcomes_owner_metrics",
        "application_outcomes",
        ["owner_id", "outcome", "outcome_on"],
    )


def _allow_application_progress() -> None:
    with op.batch_alter_table("applications", recreate="always") as batch_op:
        batch_op.drop_constraint(op.f("ck_applications_stage"), type_="check")
        batch_op.add_column(
            sa.Column("outcome_id", sa.String(length=32), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_applications_owner_outcome",
            "application_outcomes",
            ["owner_id", "id", "outcome_id"],
            ["owner_id", "application_id", "id"],
            deferrable=True,
            initially="DEFERRED",
        )
        batch_op.create_check_constraint(
            op.f("ck_applications_stage"),
            "stage IN ('pursuing', 'ready_to_apply', 'applied', 'screening', "
            "'interviewing', 'offer', 'closed')",
        )
        batch_op.create_check_constraint(
            op.f("ck_applications_outcome_shape"),
            "(stage = 'closed' AND outcome_id IS NOT NULL) OR "
            "(stage <> 'closed' AND outcome_id IS NULL)",
        )


def _allow_progress_action_kinds() -> None:
    with op.batch_alter_table("action_items", recreate="always") as batch_op:
        batch_op.drop_constraint(op.f("ck_action_items_kind"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_action_items_kind"),
            "kind IN ('review_and_prepare_application', 'submit_application', "
            "'follow_up_application', 'prepare_recruiter_screen', "
            "'prepare_interview', 'review_offer')",
        )


def _restore_action_kinds() -> None:
    with op.batch_alter_table("action_items", recreate="always") as batch_op:
        batch_op.drop_constraint(op.f("ck_action_items_kind"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_action_items_kind"),
            "kind IN ('review_and_prepare_application', 'submit_application', "
            "'follow_up_application')",
        )


def _allow_progress_activity() -> None:
    with op.batch_alter_table(
        "application_activity_events", recreate="always"
    ) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_application_activity_events_event_shape"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_application_activity_events_event_type"), type_="check"
        )
        batch_op.alter_column(
            "action_item_id",
            existing_type=sa.String(length=32),
            nullable=True,
        )
        batch_op.add_column(sa.Column("effective_on", sa.Date(), nullable=True))
        batch_op.add_column(
            sa.Column("outcome_id", sa.String(length=32), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_application_activity_events_owner_outcome",
            "application_outcomes",
            ["owner_id", "application_id", "outcome_id"],
            ["owner_id", "application_id", "id"],
            deferrable=True,
            initially="DEFERRED",
        )
        batch_op.create_check_constraint(
            op.f("ck_application_activity_events_event_type"),
            "event_type IN ('application_created', 'application_ready_to_apply', "
            "'application_applied', 'application_screening', "
            "'application_interviewing', 'application_offer', "
            "'application_closed')",
        )
        batch_op.create_check_constraint(
            op.f("ck_application_activity_events_event_shape"),
            "(event_type = 'application_created' AND sequence_number = 1 "
            "AND from_stage IS NULL AND to_stage = 'pursuing' "
            "AND action_item_id IS NOT NULL "
            "AND previous_action_item_id IS NULL AND submission_id IS NULL "
            "AND effective_on IS NULL AND outcome_id IS NULL) OR "
            "(event_type = 'application_ready_to_apply' AND sequence_number = 2 "
            "AND from_stage = 'pursuing' AND to_stage = 'ready_to_apply' "
            "AND action_item_id IS NOT NULL "
            "AND previous_action_item_id IS NOT NULL "
            "AND previous_action_item_id <> action_item_id "
            "AND submission_id IS NULL AND effective_on IS NULL "
            "AND outcome_id IS NULL) OR "
            "(event_type = 'application_applied' AND sequence_number = 3 "
            "AND from_stage = 'ready_to_apply' AND to_stage = 'applied' "
            "AND action_item_id IS NOT NULL "
            "AND previous_action_item_id IS NOT NULL "
            "AND previous_action_item_id <> action_item_id "
            "AND submission_id IS NOT NULL AND effective_on IS NULL "
            "AND outcome_id IS NULL) OR "
            "(event_type = 'application_screening' AND sequence_number >= 4 "
            "AND from_stage = 'applied' AND to_stage = 'screening' "
            "AND action_item_id IS NOT NULL "
            "AND previous_action_item_id IS NOT NULL "
            "AND previous_action_item_id <> action_item_id "
            "AND submission_id IS NULL AND effective_on IS NOT NULL "
            "AND outcome_id IS NULL) OR "
            "(event_type = 'application_interviewing' AND sequence_number >= 4 "
            "AND from_stage IN ('applied', 'screening') "
            "AND to_stage = 'interviewing' AND action_item_id IS NOT NULL "
            "AND previous_action_item_id IS NOT NULL "
            "AND previous_action_item_id <> action_item_id "
            "AND submission_id IS NULL AND effective_on IS NOT NULL "
            "AND outcome_id IS NULL) OR "
            "(event_type = 'application_offer' AND sequence_number >= 4 "
            "AND from_stage IN ('applied', 'screening', 'interviewing') "
            "AND to_stage = 'offer' AND action_item_id IS NOT NULL "
            "AND previous_action_item_id IS NOT NULL "
            "AND previous_action_item_id <> action_item_id "
            "AND submission_id IS NULL AND effective_on IS NOT NULL "
            "AND outcome_id IS NULL) OR "
            "(event_type = 'application_closed' AND sequence_number >= 2 "
            "AND from_stage IN ('pursuing', 'ready_to_apply', 'applied', "
            "'screening', 'interviewing', 'offer') AND to_stage = 'closed' "
            "AND action_item_id IS NULL "
            "AND previous_action_item_id IS NOT NULL "
            "AND submission_id IS NULL AND effective_on IS NOT NULL "
            "AND outcome_id IS NOT NULL)",
        )
    _create_progress_activity_indexes()


def _create_progress_activity_indexes() -> None:
    for suffix, event_type in (
        ("screening", "application_screening"),
        ("interviewing", "application_interviewing"),
        ("offer", "application_offer"),
        ("closed", "application_closed"),
    ):
        predicate = sa.text(f"event_type = '{event_type}'")
        op.create_index(
            f"uq_application_activity_events_owner_{suffix}",
            "application_activity_events",
            ["owner_id", "application_id"],
            unique=True,
            postgresql_where=predicate,
            sqlite_where=predicate,
        )
    op.create_index(
        "uq_application_activity_events_owner_outcome",
        "application_activity_events",
        ["owner_id", "outcome_id"],
        unique=True,
        postgresql_where=sa.text("outcome_id IS NOT NULL"),
        sqlite_where=sa.text("outcome_id IS NOT NULL"),
    )


def _assert_downgrade_is_lossless() -> None:
    connection = op.get_bind()
    unsafe_checks = (
        (
            "an application uses a Phase 6A stage",
            "SELECT 1 FROM applications "
            "WHERE stage IN ('screening', 'interviewing', 'offer', 'closed') "
            "OR outcome_id IS NOT NULL LIMIT 1",
        ),
        (
            "a Phase 6A progress event exists",
            "SELECT 1 FROM application_activity_events "
            "WHERE event_type IN ('application_screening', "
            "'application_interviewing', 'application_offer', "
            "'application_closed') OR outcome_id IS NOT NULL LIMIT 1",
        ),
        (
            "an application outcome exists",
            "SELECT 1 FROM application_outcomes LIMIT 1",
        ),
        (
            "a Phase 6A action exists",
            "SELECT 1 FROM action_items "
            "WHERE kind IN ('prepare_recruiter_screen', 'prepare_interview', "
            "'review_offer') LIMIT 1",
        ),
    )
    for reason, statement in unsafe_checks:
        if connection.execute(sa.text(statement)).first() is not None:
            raise RuntimeError(
                "Cannot downgrade 20260715_0012 without losing application "
                f"progress: {reason}."
            )
