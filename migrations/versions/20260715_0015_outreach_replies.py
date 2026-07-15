"""Add immutable replies attributed to exact manual outreach sends.

Revision ID: 20260715_0015
Revises: 20260715_0014
Create Date: 2026-07-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260715_0015"
down_revision = "20260715_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_outreach_events_reply_target",
        "outreach_events",
        [
            "owner_id",
            "application_id",
            "outreach_sequence_id",
            "application_contact_id",
            "id",
            "event_type",
            "message_version_id",
            "kind",
        ],
        unique=True,
    )
    op.create_table(
        "outreach_replies",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.String(length=32), nullable=False),
        sa.Column("outreach_sequence_id", sa.String(length=32), nullable=False),
        sa.Column("application_contact_id", sa.String(length=32), nullable=False),
        sa.Column("marked_sent_event_id", sa.String(length=32), nullable=False),
        sa.Column("marked_sent_event_type", sa.String(length=32), nullable=False),
        sa.Column("message_version_id", sa.String(length=32), nullable=False),
        sa.Column("message_kind", sa.String(length=20), nullable=False),
        sa.Column("reply_kind", sa.String(length=32), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("encrypted_note", sa.Text(), nullable=True),
        sa.Column("note_key_id", sa.String(length=32), nullable=True),
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
            "marked_sent_event_type = 'marked_sent'",
            name=op.f("ck_outreach_replies_marked_sent_event_type"),
        ),
        sa.CheckConstraint(
            "message_kind IN ('initial', 'follow_up')",
            name=op.f("ck_outreach_replies_message_kind"),
        ),
        sa.CheckConstraint(
            "reply_kind IN ('reply_received', 'useful_reply', 'introduced', "
            "'referred', 'declined', 'do_not_contact')",
            name=op.f("ck_outreach_replies_reply_kind"),
        ),
        sa.CheckConstraint(
            "(encrypted_note IS NULL AND note_key_id IS NULL) OR "
            "(encrypted_note IS NOT NULL AND length(trim(encrypted_note)) >= 1 "
            "AND note_key_id IS NOT NULL "
            "AND length(trim(note_key_id)) BETWEEN 1 AND 32)",
            name=op.f("ck_outreach_replies_note_envelope"),
        ),
        sa.CheckConstraint(
            "recording_method = 'manual'",
            name=op.f("ck_outreach_replies_recording_method"),
        ),
        sa.CheckConstraint(
            "length(idempotency_key_hash) = 64",
            name=op.f("ck_outreach_replies_mutation_hash"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "outreach_sequence_id"],
            [
                "outreach_sequences.owner_id",
                "outreach_sequences.application_id",
                "outreach_sequences.id",
            ],
            name="fk_outreach_replies_owner_sequence",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "application_contact_id"],
            [
                "application_contacts.owner_id",
                "application_contacts.application_id",
                "application_contacts.id",
            ],
            name="fk_outreach_replies_owner_contact",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            [
                "owner_id",
                "application_id",
                "outreach_sequence_id",
                "application_contact_id",
                "message_version_id",
                "message_kind",
            ],
            [
                "outreach_message_versions.owner_id",
                "outreach_message_versions.application_id",
                "outreach_message_versions.outreach_sequence_id",
                "outreach_message_versions.application_contact_id",
                "outreach_message_versions.id",
                "outreach_message_versions.kind",
            ],
            name="fk_outreach_replies_owner_message_version",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            [
                "owner_id",
                "application_id",
                "outreach_sequence_id",
                "application_contact_id",
                "marked_sent_event_id",
                "marked_sent_event_type",
                "message_version_id",
                "message_kind",
            ],
            [
                "outreach_events.owner_id",
                "outreach_events.application_id",
                "outreach_events.outreach_sequence_id",
                "outreach_events.application_contact_id",
                "outreach_events.id",
                "outreach_events.event_type",
                "outreach_events.message_version_id",
                "outreach_events.kind",
            ],
            name="fk_outreach_replies_owner_sent_event",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_outreach_replies_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outreach_replies")),
        sa.UniqueConstraint(
            "owner_id",
            "id",
            name="uq_outreach_replies_owner_id_id",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "outreach_sequence_id",
            "idempotency_key_hash",
            name="uq_outreach_replies_owner_sequence_mutation",
        ),
    )
    op.create_index(
        "ix_outreach_replies_sent_attempt",
        "outreach_replies",
        [
            "owner_id",
            "outreach_sequence_id",
            "marked_sent_event_id",
            "recorded_at",
            "id",
        ],
    )
    op.create_index(
        "ix_outreach_replies_timeline",
        "outreach_replies",
        ["owner_id", "outreach_sequence_id", "recorded_at", "id"],
    )


def downgrade() -> None:
    _assert_downgrade_is_lossless()
    op.execute(
        "DELETE FROM owner_mutation_receipts "
        "WHERE namespace LIKE 'outreach.reply.record:%'"
    )
    op.drop_index(
        "ix_outreach_replies_timeline",
        table_name="outreach_replies",
    )
    op.drop_index(
        "ix_outreach_replies_sent_attempt",
        table_name="outreach_replies",
    )
    op.drop_table("outreach_replies")
    op.drop_index(
        "uq_outreach_events_reply_target",
        table_name="outreach_events",
    )


def _assert_downgrade_is_lossless() -> None:
    connection = op.get_bind()
    if connection.execute(
        sa.text("SELECT 1 FROM outreach_replies LIMIT 1")
    ).first() is not None:
        raise RuntimeError(
            "Cannot downgrade 20260715_0015 without losing outreach reply "
            "history: a reply exists."
        )
