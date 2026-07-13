"""Add the atomic application pursuit and first-next-action boundary.

Revision ID: 20260713_0006
Revises: 20260713_0005
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260713_0006"
down_revision = "20260713_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _allow_pursued_decisions()

    op.create_table(
        "applications",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("owner_opportunity_id", sa.String(length=32), nullable=False),
        sa.Column("job_posting_id", sa.String(length=32), nullable=False),
        sa.Column(
            "pursued_posting_version_id", sa.String(length=32), nullable=False
        ),
        sa.Column("stage", sa.String(length=24), nullable=False),
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
            "stage = 'pursuing'", name=op.f("ck_applications_stage")
        ),
        sa.CheckConstraint(
            "version >= 1", name=op.f("ck_applications_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "owner_opportunity_id", "job_posting_id"],
            [
                "owner_opportunities.owner_id",
                "owner_opportunities.id",
                "owner_opportunities.job_posting_id",
            ],
            name="fk_applications_owner_opportunity",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "job_posting_id", "pursued_posting_version_id"],
            [
                "job_posting_versions.owner_id",
                "job_posting_versions.job_posting_id",
                "job_posting_versions.id",
            ],
            name="fk_applications_owner_posting_version",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_applications_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_applications")),
        sa.UniqueConstraint(
            "owner_id", "id", name="uq_applications_owner_id_id"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "owner_opportunity_id",
            name="uq_applications_owner_opportunity",
        ),
    )
    op.create_index(
        "ix_applications_owner_stage",
        "applications",
        ["owner_id", "stage", "updated_at"],
    )

    op.create_table(
        "action_items",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
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
            "kind = 'review_and_prepare_application'",
            name=op.f("ck_action_items_kind"),
        ),
        sa.CheckConstraint(
            "length(trim(title)) BETWEEN 1 AND 240",
            name=op.f("ck_action_items_title_length"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'completed', 'cancelled')",
            name=op.f("ck_action_items_status"),
        ),
        sa.CheckConstraint(
            "(status = 'open' AND completed_at IS NULL AND cancelled_at IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL "
            "AND cancelled_at IS NULL) OR "
            "(status = 'cancelled' AND cancelled_at IS NOT NULL "
            "AND completed_at IS NULL)",
            name=op.f("ck_action_items_status_timestamps"),
        ),
        sa.CheckConstraint(
            "version >= 1", name=op.f("ck_action_items_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_action_items_owner_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_action_items_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_items")),
        sa.UniqueConstraint(
            "owner_id", "id", name="uq_action_items_owner_id_id"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            "id",
            name="uq_action_items_owner_application_id",
        ),
    )
    op.create_index(
        "ix_action_items_owner_due",
        "action_items",
        ["owner_id", "status", "due_on"],
    )
    op.create_index(
        "uq_action_items_owner_application_open",
        "action_items",
        ["owner_id", "application_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
        sqlite_where=sa.text("status = 'open'"),
    )

    op.create_table(
        "application_activity_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.String(length=32), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("from_stage", sa.String(length=24), nullable=True),
        sa.Column("to_stage", sa.String(length=24), nullable=False),
        sa.Column("action_item_id", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sequence_number >= 1",
            name=op.f("ck_application_activity_events_sequence_positive"),
        ),
        sa.CheckConstraint(
            "event_type = 'application_created'",
            name=op.f("ck_application_activity_events_event_type"),
        ),
        sa.CheckConstraint(
            "sequence_number = 1 AND from_stage IS NULL "
            "AND to_stage = 'pursuing' AND action_item_id IS NOT NULL",
            name=op.f("ck_application_activity_events_creation_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_application_activity_events_owner_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "action_item_id"],
            [
                "action_items.owner_id",
                "action_items.application_id",
                "action_items.id",
            ],
            name="fk_application_activity_events_owner_action",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_application_activity_events_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_application_activity_events")
        ),
        sa.UniqueConstraint(
            "owner_id",
            "id",
            name="uq_application_activity_events_owner_id_id",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            "sequence_number",
            name="uq_application_activity_events_owner_sequence",
        ),
    )
    op.create_index(
        "ix_application_activity_events_timeline",
        "application_activity_events",
        ["application_id", "occurred_at", "sequence_number"],
    )
    op.create_index(
        "uq_application_activity_events_owner_created",
        "application_activity_events",
        ["owner_id", "application_id"],
        unique=True,
        postgresql_where=sa.text("event_type = 'application_created'"),
        sqlite_where=sa.text("event_type = 'application_created'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_application_activity_events_owner_created",
        table_name="application_activity_events",
    )
    op.drop_index(
        "ix_application_activity_events_timeline",
        table_name="application_activity_events",
    )
    op.drop_table("application_activity_events")

    op.drop_index(
        "uq_action_items_owner_application_open", table_name="action_items"
    )
    op.drop_index("ix_action_items_owner_due", table_name="action_items")
    op.drop_table("action_items")

    op.drop_index("ix_applications_owner_stage", table_name="applications")
    op.drop_table("applications")

    # A downgrade intentionally discards the new pursuit audit transition and
    # returns pursued opportunities to a representable pre-0006 state.
    op.execute(
        "DELETE FROM opportunity_decision_events "
        "WHERE previous_decision = 'pursued' OR new_decision = 'pursued'"
    )
    op.execute(
        "UPDATE owner_opportunities SET decision = 'watch', "
        "decision_reason_code = NULL WHERE decision = 'pursued'"
    )
    _restore_radar_decisions()


def _allow_pursued_decisions() -> None:
    with op.batch_alter_table("owner_opportunities") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_owner_opportunities_decision_reason"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_owner_opportunities_decision"), type_="check"
        )
        batch_op.create_check_constraint(
            op.f("ck_owner_opportunities_decision"),
            "decision IN ('inbox', 'watch', 'dismiss', 'pursued')",
        )
        batch_op.create_check_constraint(
            op.f("ck_owner_opportunities_decision_reason"),
            "(decision = 'dismiss' AND decision_reason_code IS NOT NULL) OR "
            "(decision IN ('inbox', 'watch', 'pursued') "
            "AND decision_reason_code IS NULL)",
        )

    with op.batch_alter_table("opportunity_decision_events") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_opportunity_decision_events_decision_reason"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_opportunity_decision_events_decision_values"), type_="check"
        )
        batch_op.create_check_constraint(
            op.f("ck_opportunity_decision_events_decision_values"),
            "previous_decision IN ('inbox', 'watch', 'dismiss', 'pursued') AND "
            "new_decision IN ('inbox', 'watch', 'dismiss', 'pursued')",
        )
        batch_op.create_check_constraint(
            op.f("ck_opportunity_decision_events_decision_reason"),
            "(new_decision = 'dismiss' AND reason_code IS NOT NULL) OR "
            "(new_decision IN ('inbox', 'watch', 'pursued') "
            "AND reason_code IS NULL)",
        )


def _restore_radar_decisions() -> None:
    with op.batch_alter_table("opportunity_decision_events") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_opportunity_decision_events_decision_reason"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_opportunity_decision_events_decision_values"), type_="check"
        )
        batch_op.create_check_constraint(
            op.f("ck_opportunity_decision_events_decision_values"),
            "previous_decision IN ('inbox', 'watch', 'dismiss') AND "
            "new_decision IN ('inbox', 'watch', 'dismiss')",
        )
        batch_op.create_check_constraint(
            op.f("ck_opportunity_decision_events_decision_reason"),
            "(new_decision = 'dismiss' AND reason_code IS NOT NULL) OR "
            "(new_decision IN ('inbox', 'watch') AND reason_code IS NULL)",
        )

    with op.batch_alter_table("owner_opportunities") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_owner_opportunities_decision_reason"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_owner_opportunities_decision"), type_="check"
        )
        batch_op.create_check_constraint(
            op.f("ck_owner_opportunities_decision"),
            "decision IN ('inbox', 'watch', 'dismiss')",
        )
        batch_op.create_check_constraint(
            op.f("ck_owner_opportunities_decision_reason"),
            "(decision = 'dismiss' AND decision_reason_code IS NOT NULL) OR "
            "(decision IN ('inbox', 'watch') AND decision_reason_code IS NULL)",
        )
