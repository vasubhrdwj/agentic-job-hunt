"""Add durable scheduled and repeatable application interview rounds.

Revision ID: 20260715_0013
Revises: 20260715_0012
Create Date: 2026-07-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260715_0013"
down_revision = "20260715_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _create_interview_rounds()
    _link_actions_to_rounds()
    _create_interview_round_events()
    _link_application_activity_to_rounds()


def downgrade() -> None:
    _assert_downgrade_is_lossless()
    op.execute(
        "DELETE FROM owner_mutation_receipts "
        "WHERE namespace LIKE 'interview_round.%'"
    )
    _restore_application_activity()
    op.drop_index(
        "uq_application_interview_round_events_owner_terminal",
        table_name="application_interview_round_events",
    )
    op.drop_index(
        "ix_application_interview_round_events_timeline",
        table_name="application_interview_round_events",
    )
    op.drop_table("application_interview_round_events")
    _unlink_actions_from_rounds()
    op.drop_index(
        "ix_application_interview_rounds_owner_schedule",
        table_name="application_interview_rounds",
    )
    op.drop_index(
        "uq_application_interview_rounds_owner_scheduled",
        table_name="application_interview_rounds",
    )
    op.drop_table("application_interview_rounds")


def _create_interview_rounds() -> None:
    op.create_table(
        "application_interview_rounds",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.String(length=32), nullable=False),
        sa.Column(
            "application_submission_id", sa.String(length=32), nullable=False
        ),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "scheduled_start_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("scheduled_timezone", sa.String(length=64), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("meeting_format", sa.String(length=20), nullable=False),
        sa.Column("completed_on", sa.Date(), nullable=True),
        sa.Column("cancelled_on", sa.Date(), nullable=True),
        sa.Column("cancelled_by", sa.String(length=20), nullable=True),
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
            "round_number >= 1",
            name=op.f("ck_application_interview_rounds_round_number_positive"),
        ),
        sa.CheckConstraint(
            "kind IN ('hiring_manager', 'technical', 'system_design', "
            "'behavioral', 'case_study', 'panel', 'final', 'other')",
            name=op.f("ck_application_interview_rounds_kind"),
        ),
        sa.CheckConstraint(
            "length(trim(title)) BETWEEN 1 AND 160",
            name=op.f("ck_application_interview_rounds_title_length"),
        ),
        sa.CheckConstraint(
            "status IN ('scheduled', 'completed', 'cancelled')",
            name=op.f("ck_application_interview_rounds_status"),
        ),
        sa.CheckConstraint(
            "duration_minutes BETWEEN 15 AND 480",
            name=op.f("ck_application_interview_rounds_duration_minutes"),
        ),
        sa.CheckConstraint(
            "length(trim(scheduled_timezone)) BETWEEN 1 AND 64",
            name=op.f(
                "ck_application_interview_rounds_scheduled_timezone_length"
            ),
        ),
        sa.CheckConstraint(
            "meeting_format IN ('video', 'phone', 'onsite', 'unspecified')",
            name=op.f("ck_application_interview_rounds_meeting_format"),
        ),
        sa.CheckConstraint(
            "cancelled_by IS NULL OR cancelled_by IN "
            "('employer', 'candidate', 'mutual', 'unknown')",
            name=op.f("ck_application_interview_rounds_cancelled_by"),
        ),
        sa.CheckConstraint(
            "(status = 'scheduled' AND completed_on IS NULL "
            "AND cancelled_on IS NULL AND cancelled_by IS NULL) OR "
            "(status = 'completed' AND completed_on IS NOT NULL "
            "AND cancelled_on IS NULL AND cancelled_by IS NULL) OR "
            "(status = 'cancelled' AND cancelled_on IS NOT NULL "
            "AND cancelled_by IS NOT NULL AND completed_on IS NULL)",
            name=op.f("ck_application_interview_rounds_status_shape"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_application_interview_rounds_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_application_interview_rounds_owner_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "application_submission_id"],
            [
                "application_submissions.owner_id",
                "application_submissions.application_id",
                "application_submissions.id",
            ],
            name="fk_application_interview_rounds_owner_submission",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f(
                "fk_application_interview_rounds_owner_id_owners"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_application_interview_rounds")
        ),
        sa.UniqueConstraint(
            "owner_id",
            "id",
            name="uq_application_interview_rounds_owner_id_id",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            "id",
            name="uq_application_interview_rounds_owner_application_id",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            "round_number",
            name="uq_application_interview_rounds_owner_number",
        ),
    )
    op.create_index(
        "uq_application_interview_rounds_owner_scheduled",
        "application_interview_rounds",
        ["owner_id", "application_id"],
        unique=True,
        sqlite_where=sa.text("status = 'scheduled'"),
        postgresql_where=sa.text("status = 'scheduled'"),
    )
    op.create_index(
        "ix_application_interview_rounds_owner_schedule",
        "application_interview_rounds",
        ["owner_id", "status", "scheduled_start_at"],
    )


def _link_actions_to_rounds() -> None:
    with op.batch_alter_table("action_items", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column("interview_round_id", sa.String(length=32), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_action_items_owner_interview_round",
            "application_interview_rounds",
            ["owner_id", "application_id", "interview_round_id"],
            ["owner_id", "application_id", "id"],
            deferrable=True,
            initially="DEFERRED",
        )
        batch_op.create_check_constraint(
            op.f("ck_action_items_interview_round_kind"),
            "interview_round_id IS NULL OR kind = 'prepare_interview'",
        )


def _unlink_actions_from_rounds() -> None:
    with op.batch_alter_table("action_items", recreate="always") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_action_items_interview_round_kind"), type_="check"
        )
        batch_op.drop_constraint(
            "fk_action_items_owner_interview_round", type_="foreignkey"
        )
        batch_op.drop_column("interview_round_id")


def _create_interview_round_events() -> None:
    op.create_table(
        "application_interview_round_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.String(length=32), nullable=False),
        sa.Column("interview_round_id", sa.String(length=32), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column(
            "scheduled_start_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("scheduled_timezone", sa.String(length=64), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("meeting_format", sa.String(length=20), nullable=False),
        sa.Column("effective_on", sa.Date(), nullable=True),
        sa.Column("cancelled_by", sa.String(length=20), nullable=True),
        sa.Column("previous_action_item_id", sa.String(length=32), nullable=False),
        sa.Column("action_item_id", sa.String(length=32), nullable=False),
        sa.Column("recording_method", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sequence_number >= 1",
            name=op.f(
                "ck_application_interview_round_events_sequence_positive"
            ),
        ),
        sa.CheckConstraint(
            "event_type IN ('scheduled', 'rescheduled', 'completed', 'cancelled')",
            name=op.f("ck_application_interview_round_events_event_type"),
        ),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status = 'scheduled'",
            name=op.f("ck_application_interview_round_events_from_status"),
        ),
        sa.CheckConstraint(
            "to_status IN ('scheduled', 'completed', 'cancelled')",
            name=op.f("ck_application_interview_round_events_to_status"),
        ),
        sa.CheckConstraint(
            "duration_minutes BETWEEN 15 AND 480",
            name=op.f(
                "ck_application_interview_round_events_duration_minutes"
            ),
        ),
        sa.CheckConstraint(
            "length(trim(scheduled_timezone)) BETWEEN 1 AND 64",
            name=op.f(
                "ck_application_interview_round_events_scheduled_timezone_length"
            ),
        ),
        sa.CheckConstraint(
            "meeting_format IN ('video', 'phone', 'onsite', 'unspecified')",
            name=op.f(
                "ck_application_interview_round_events_meeting_format"
            ),
        ),
        sa.CheckConstraint(
            "cancelled_by IS NULL OR cancelled_by IN "
            "('employer', 'candidate', 'mutual', 'unknown')",
            name=op.f("ck_application_interview_round_events_cancelled_by"),
        ),
        sa.CheckConstraint(
            "(event_type = 'scheduled' AND sequence_number = 1 "
            "AND from_status IS NULL AND to_status = 'scheduled' "
            "AND effective_on IS NULL AND cancelled_by IS NULL) OR "
            "(event_type = 'rescheduled' AND sequence_number >= 2 "
            "AND from_status = 'scheduled' AND to_status = 'scheduled' "
            "AND effective_on IS NULL AND cancelled_by IS NULL) OR "
            "(event_type = 'completed' AND sequence_number >= 2 "
            "AND from_status = 'scheduled' AND to_status = 'completed' "
            "AND effective_on IS NOT NULL AND cancelled_by IS NULL) OR "
            "(event_type = 'cancelled' AND sequence_number >= 2 "
            "AND from_status = 'scheduled' AND to_status = 'cancelled' "
            "AND effective_on IS NOT NULL AND cancelled_by IS NOT NULL)",
            name=op.f("ck_application_interview_round_events_event_shape"),
        ),
        sa.CheckConstraint(
            "previous_action_item_id <> action_item_id",
            name=op.f(
                "ck_application_interview_round_events_action_replaced"
            ),
        ),
        sa.CheckConstraint(
            "recording_method = 'manual'",
            name=op.f(
                "ck_application_interview_round_events_recording_method"
            ),
        ),
        sa.CheckConstraint(
            "length(idempotency_key_hash) = 64",
            name=op.f("ck_application_interview_round_events_mutation_hash"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "interview_round_id"],
            [
                "application_interview_rounds.owner_id",
                "application_interview_rounds.application_id",
                "application_interview_rounds.id",
            ],
            name="fk_application_interview_round_events_owner_round",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "previous_action_item_id"],
            ["action_items.owner_id", "action_items.application_id", "action_items.id"],
            name="fk_application_interview_round_events_owner_previous_action",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "action_item_id"],
            ["action_items.owner_id", "action_items.application_id", "action_items.id"],
            name="fk_application_interview_round_events_owner_action",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f(
                "fk_application_interview_round_events_owner_id_owners"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_application_interview_round_events")
        ),
        sa.UniqueConstraint(
            "owner_id",
            "id",
            name="uq_application_interview_round_events_owner_id_id",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            "interview_round_id",
            "sequence_number",
            name="uq_application_interview_round_events_owner_sequence",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            "idempotency_key_hash",
            name="uq_application_interview_round_events_owner_mutation",
        ),
    )
    op.create_index(
        "uq_application_interview_round_events_owner_terminal",
        "application_interview_round_events",
        ["owner_id", "application_id", "interview_round_id"],
        unique=True,
        sqlite_where=sa.text("event_type IN ('completed', 'cancelled')"),
        postgresql_where=sa.text("event_type IN ('completed', 'cancelled')"),
    )
    op.create_index(
        "ix_application_interview_round_events_timeline",
        "application_interview_round_events",
        ["owner_id", "application_id", "interview_round_id", "sequence_number"],
    )


def _link_application_activity_to_rounds() -> None:
    with op.batch_alter_table(
        "application_activity_events", recreate="always"
    ) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_application_activity_events_event_shape"), type_="check"
        )
        batch_op.add_column(
            sa.Column("interview_round_id", sa.String(length=32), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_application_activity_events_owner_interview_round",
            "application_interview_rounds",
            ["owner_id", "application_id", "interview_round_id"],
            ["owner_id", "application_id", "id"],
            deferrable=True,
            initially="DEFERRED",
        )
        batch_op.create_check_constraint(
            op.f("ck_application_activity_events_event_shape"),
            _activity_shape(with_round=True),
        )


def _restore_application_activity() -> None:
    with op.batch_alter_table(
        "application_activity_events", recreate="always"
    ) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_application_activity_events_event_shape"), type_="check"
        )
        batch_op.drop_constraint(
            "fk_application_activity_events_owner_interview_round",
            type_="foreignkey",
        )
        batch_op.create_check_constraint(
            op.f("ck_application_activity_events_event_shape"),
            _activity_shape(with_round=False),
        )
        batch_op.drop_column("interview_round_id")


def _activity_shape(*, with_round: bool) -> str:
    no_round = " AND interview_round_id IS NULL" if with_round else ""
    return (
        "(event_type = 'application_created' AND sequence_number = 1 "
        "AND from_stage IS NULL AND to_stage = 'pursuing' "
        "AND action_item_id IS NOT NULL "
        "AND previous_action_item_id IS NULL AND submission_id IS NULL "
        f"AND effective_on IS NULL AND outcome_id IS NULL{no_round}) OR "
        "(event_type = 'application_ready_to_apply' AND sequence_number = 2 "
        "AND from_stage = 'pursuing' AND to_stage = 'ready_to_apply' "
        "AND action_item_id IS NOT NULL "
        "AND previous_action_item_id IS NOT NULL "
        "AND previous_action_item_id <> action_item_id "
        "AND submission_id IS NULL AND effective_on IS NULL "
        f"AND outcome_id IS NULL{no_round}) OR "
        "(event_type = 'application_applied' AND sequence_number = 3 "
        "AND from_stage = 'ready_to_apply' AND to_stage = 'applied' "
        "AND action_item_id IS NOT NULL "
        "AND previous_action_item_id IS NOT NULL "
        "AND previous_action_item_id <> action_item_id "
        "AND submission_id IS NOT NULL AND effective_on IS NULL "
        f"AND outcome_id IS NULL{no_round}) OR "
        "(event_type = 'application_screening' AND sequence_number >= 4 "
        "AND from_stage = 'applied' AND to_stage = 'screening' "
        "AND action_item_id IS NOT NULL "
        "AND previous_action_item_id IS NOT NULL "
        "AND previous_action_item_id <> action_item_id "
        "AND submission_id IS NULL AND effective_on IS NOT NULL "
        f"AND outcome_id IS NULL{no_round}) OR "
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
        f"AND outcome_id IS NULL{no_round}) OR "
        "(event_type = 'application_closed' AND sequence_number >= 2 "
        "AND from_stage IN ('pursuing', 'ready_to_apply', 'applied', "
        "'screening', 'interviewing', 'offer') AND to_stage = 'closed' "
        "AND action_item_id IS NULL AND previous_action_item_id IS NOT NULL "
        "AND submission_id IS NULL AND effective_on IS NOT NULL "
        f"AND outcome_id IS NOT NULL{no_round})"
    )


def _assert_downgrade_is_lossless() -> None:
    connection = op.get_bind()
    checks = (
        (
            "an interview round exists",
            "SELECT 1 FROM application_interview_rounds LIMIT 1",
        ),
        (
            "an interview-round event exists",
            "SELECT 1 FROM application_interview_round_events LIMIT 1",
        ),
        (
            "an action names an interview round",
            "SELECT 1 FROM action_items WHERE interview_round_id IS NOT NULL LIMIT 1",
        ),
        (
            "application activity names an interview round",
            "SELECT 1 FROM application_activity_events "
            "WHERE interview_round_id IS NOT NULL LIMIT 1",
        ),
    )
    for reason, statement in checks:
        if connection.execute(sa.text(statement)).first() is not None:
            raise RuntimeError(
                "Cannot downgrade 20260715_0013 without losing interview-round "
                f"history: {reason}."
            )
