"""Add append-only corrections for coarse application milestone dates.

Revision ID: 20260715_0014
Revises: 20260715_0013
Create Date: 2026-07-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260715_0014"
down_revision = "20260715_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _add_activity_correction_target_identity()
    _create_application_milestone_corrections()


def downgrade() -> None:
    _assert_downgrade_is_lossless()
    op.execute(
        "DELETE FROM owner_mutation_receipts "
        "WHERE namespace LIKE 'application.milestone_correction:%'"
    )
    op.drop_index(
        "ix_application_milestone_corrections_timeline",
        table_name="application_milestone_corrections",
    )
    op.drop_index(
        "uq_application_milestone_corrections_owner_supersedes",
        table_name="application_milestone_corrections",
    )
    op.drop_table("application_milestone_corrections")
    _remove_activity_correction_target_identity()


def _add_activity_correction_target_identity() -> None:
    with op.batch_alter_table(
        "application_activity_events", recreate=_batch_recreate_mode()
    ) as batch_op:
        batch_op.create_unique_constraint(
            "uq_application_activity_events_owner_application_id",
            ["owner_id", "application_id", "id"],
        )


def _remove_activity_correction_target_identity() -> None:
    with op.batch_alter_table(
        "application_activity_events", recreate=_batch_recreate_mode()
    ) as batch_op:
        batch_op.drop_constraint(
            "uq_application_activity_events_owner_application_id",
            type_="unique",
        )


def _create_application_milestone_corrections() -> None:
    op.create_table(
        "application_milestone_corrections",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.String(length=32), nullable=False),
        sa.Column("activity_event_id", sa.String(length=32), nullable=False),
        sa.Column("correction_number", sa.Integer(), nullable=False),
        sa.Column("supersedes_correction_id", sa.String(length=32), nullable=True),
        sa.Column("previous_effective_on", sa.Date(), nullable=False),
        sa.Column("corrected_effective_on", sa.Date(), nullable=False),
        sa.Column("recording_method", sa.String(length=16), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "correction_number BETWEEN 1 AND 50",
            name=op.f(
                "ck_application_milestone_corrections_number_range"
            ),
        ),
        sa.CheckConstraint(
            "(correction_number = 1 AND supersedes_correction_id IS NULL) OR "
            "(correction_number >= 2 AND supersedes_correction_id IS NOT NULL)",
            name=op.f(
                "ck_application_milestone_corrections_chain_shape"
            ),
        ),
        sa.CheckConstraint(
            "previous_effective_on <> corrected_effective_on",
            name=op.f(
                "ck_application_milestone_corrections_date_changed"
            ),
        ),
        sa.CheckConstraint(
            "recording_method = 'manual'",
            name=op.f(
                "ck_application_milestone_corrections_recording_method"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_application_milestone_corrections_owner_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "activity_event_id"],
            [
                "application_activity_events.owner_id",
                "application_activity_events.application_id",
                "application_activity_events.id",
            ],
            name="fk_application_milestone_corrections_owner_activity",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            [
                "owner_id",
                "application_id",
                "activity_event_id",
                "supersedes_correction_id",
            ],
            [
                "application_milestone_corrections.owner_id",
                "application_milestone_corrections.application_id",
                "application_milestone_corrections.activity_event_id",
                "application_milestone_corrections.id",
            ],
            name="fk_application_milestone_corrections_owner_supersedes",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f(
                "fk_application_milestone_corrections_owner_id_owners"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_application_milestone_corrections")
        ),
        sa.UniqueConstraint(
            "owner_id",
            "id",
            name="uq_application_milestone_corrections_owner_id_id",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            "activity_event_id",
            "id",
            name="uq_application_milestone_corrections_owner_event_id",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            "activity_event_id",
            "correction_number",
            name="uq_application_milestone_corrections_owner_number",
        ),
    )
    op.create_index(
        "uq_application_milestone_corrections_owner_supersedes",
        "application_milestone_corrections",
        [
            "owner_id",
            "application_id",
            "activity_event_id",
            "supersedes_correction_id",
        ],
        unique=True,
        sqlite_where=sa.text("supersedes_correction_id IS NOT NULL"),
        postgresql_where=sa.text("supersedes_correction_id IS NOT NULL"),
    )
    op.create_index(
        "ix_application_milestone_corrections_timeline",
        "application_milestone_corrections",
        ["owner_id", "application_id", "activity_event_id", "correction_number"],
    )


def _batch_recreate_mode() -> str:
    """Recreate tables only where SQLite requires batch-copy DDL."""

    return "always" if op.get_bind().dialect.name == "sqlite" else "auto"


def _assert_downgrade_is_lossless() -> None:
    connection = op.get_bind()
    if connection.execute(
        sa.text("SELECT 1 FROM application_milestone_corrections LIMIT 1")
    ).first() is not None:
        raise RuntimeError(
            "Cannot downgrade 20260715_0014 without losing application "
            "milestone correction history: a correction exists."
        )
