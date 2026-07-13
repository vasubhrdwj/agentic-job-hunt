"""Add durable manual outreach sequences, message revisions, and events.

Revision ID: 20260713_0008
Revises: 20260713_0007
Create Date: 2026-07-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260713_0008"
down_revision = "20260713_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outreach_sequences",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.String(length=32), nullable=False),
        sa.Column("contact_plan_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("active_wave", sa.Integer(), nullable=True),
        sa.Column("reason_code", sa.String(length=100), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('active', 'paused', 'stopped', 'completed')",
            name=op.f("ck_outreach_sequences_status"),
        ),
        sa.CheckConstraint(
            "active_wave IS NULL OR active_wave BETWEEN 1 AND 5",
            name=op.f("ck_outreach_sequences_active_wave"),
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR length(trim(reason_code)) BETWEEN 1 AND 100",
            name=op.f("ck_outreach_sequences_reason_code"),
        ),
        sa.CheckConstraint(
            "(status = 'active' AND active_wave IS NOT NULL "
            "AND reason_code IS NULL AND paused_at IS NULL "
            "AND stopped_at IS NULL AND completed_at IS NULL) OR "
            "(status = 'paused' AND active_wave IS NOT NULL "
            "AND reason_code IS NOT NULL AND paused_at IS NOT NULL "
            "AND stopped_at IS NULL AND completed_at IS NULL) OR "
            "(status = 'stopped' AND active_wave IS NULL "
            "AND reason_code IS NOT NULL AND paused_at IS NULL "
            "AND stopped_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status = 'completed' AND active_wave IS NULL "
            "AND paused_at IS NULL AND stopped_at IS NULL "
            "AND completed_at IS NOT NULL)",
            name=op.f("ck_outreach_sequences_status_shape"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_outreach_sequences_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_outreach_sequences_owner_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "contact_plan_id"],
            ["contact_plans.owner_id", "contact_plans.application_id", "contact_plans.id"],
            name="fk_outreach_sequences_owner_contact_plan",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_outreach_sequences_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outreach_sequences")),
        sa.UniqueConstraint(
            "owner_id", "id", name="uq_outreach_sequences_owner_id_id"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            "id",
            name="uq_outreach_sequences_owner_application_id",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            name="uq_outreach_sequences_owner_application",
        ),
    )
    op.create_index(
        "ix_outreach_sequences_owner_status",
        "outreach_sequences",
        ["owner_id", "status", "updated_at"],
    )

    op.create_table(
        "outreach_message_versions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.String(length=32), nullable=False),
        sa.Column("outreach_sequence_id", sa.String(length=32), nullable=False),
        sa.Column("application_contact_id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("encrypted_body", sa.Text(), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('initial', 'follow_up')",
            name=op.f("ck_outreach_message_versions_kind"),
        ),
        sa.CheckConstraint(
            "version_number >= 1",
            name=op.f("ck_outreach_message_versions_version_number_positive"),
        ),
        sa.CheckConstraint(
            "length(trim(encrypted_body)) >= 1 "
            "AND length(trim(encryption_key_id)) BETWEEN 1 AND 32",
            name=op.f("ck_outreach_message_versions_encrypted_body_envelope"),
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64",
            name=op.f("ck_outreach_message_versions_content_hash"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "outreach_sequence_id"],
            [
                "outreach_sequences.owner_id",
                "outreach_sequences.application_id",
                "outreach_sequences.id",
            ],
            name="fk_outreach_message_versions_owner_sequence",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "application_contact_id"],
            [
                "application_contacts.owner_id",
                "application_contacts.application_id",
                "application_contacts.id",
            ],
            name="fk_outreach_message_versions_owner_contact",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_outreach_message_versions_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outreach_message_versions")),
        sa.UniqueConstraint(
            "owner_id", "id", name="uq_outreach_message_versions_owner_id_id"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            "outreach_sequence_id",
            "application_contact_id",
            "id",
            "kind",
            name="uq_outreach_message_versions_event_ref",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "outreach_sequence_id",
            "application_contact_id",
            "kind",
            "version_number",
            name="uq_outreach_message_versions_revision",
        ),
    )
    op.create_index(
        "ix_outreach_message_versions_sequence",
        "outreach_message_versions",
        [
            "owner_id",
            "outreach_sequence_id",
            "application_contact_id",
            "kind",
            "created_at",
        ],
    )

    op.create_table(
        "outreach_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.String(length=32), nullable=False),
        sa.Column("outreach_sequence_id", sa.String(length=32), nullable=False),
        sa.Column("application_contact_id", sa.String(length=32), nullable=True),
        sa.Column("message_version_id", sa.String(length=32), nullable=True),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=True),
        sa.Column("channel", sa.String(length=20), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("reason_code", sa.String(length=100), nullable=True),
        sa.Column("wave", sa.Integer(), nullable=True),
        sa.Column("follow_up_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("encrypted_note", sa.Text(), nullable=True),
        sa.Column("note_key_id", sa.String(length=32), nullable=True),
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
            name=op.f("ck_outreach_events_sequence_number_positive"),
        ),
        sa.CheckConstraint(
            "event_type IN ('sequence_started', 'message_saved', 'copied', "
            "'marked_sent', 'outcome_recorded', 'paused', 'resumed', "
            "'stopped', 'wave_advanced')",
            name=op.f("ck_outreach_events_event_type"),
        ),
        sa.CheckConstraint(
            "kind IS NULL OR kind IN ('initial', 'follow_up')",
            name=op.f("ck_outreach_events_kind"),
        ),
        sa.CheckConstraint(
            "channel IS NULL OR channel IN ('linkedin', 'email', 'other')",
            name=op.f("ck_outreach_events_channel"),
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('no_reply', 'declined', 'unreachable', "
            "'useful_reply', 'introduced', 'referred', 'do_not_contact')",
            name=op.f("ck_outreach_events_outcome"),
        ),
        sa.CheckConstraint(
            "wave IS NULL OR wave BETWEEN 1 AND 5",
            name=op.f("ck_outreach_events_wave"),
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR length(trim(reason_code)) BETWEEN 1 AND 100",
            name=op.f("ck_outreach_events_reason_code"),
        ),
        sa.CheckConstraint(
            "length(idempotency_key_hash) = 64",
            name=op.f("ck_outreach_events_mutation_hash"),
        ),
        sa.CheckConstraint(
            "(event_type = 'marked_sent' AND kind = 'initial' "
            "AND follow_up_due_at IS NOT NULL) OR "
            "(NOT (event_type = 'marked_sent' AND kind = 'initial') "
            "AND follow_up_due_at IS NULL)",
            name=op.f("ck_outreach_events_follow_up_due_shape"),
        ),
        sa.CheckConstraint(
            "(encrypted_note IS NULL AND note_key_id IS NULL) OR "
            "(encrypted_note IS NOT NULL AND length(trim(encrypted_note)) >= 1 "
            "AND note_key_id IS NOT NULL "
            "AND length(trim(note_key_id)) BETWEEN 1 AND 32)",
            name=op.f("ck_outreach_events_note_envelope"),
        ),
        sa.CheckConstraint(
            "encrypted_note IS NULL OR "
            "event_type IN ('outcome_recorded', 'paused', 'resumed', 'stopped')",
            name=op.f("ck_outreach_events_note_event_type"),
        ),
        sa.CheckConstraint(
            "(event_type = 'sequence_started' "
            "AND application_contact_id IS NULL AND message_version_id IS NULL "
            "AND kind IS NULL AND channel IS NULL AND outcome IS NULL "
            "AND reason_code IS NULL AND wave = 1) OR "
            "(event_type = 'message_saved' "
            "AND application_contact_id IS NOT NULL AND message_version_id IS NOT NULL "
            "AND kind IS NOT NULL AND channel IS NULL AND outcome IS NULL "
            "AND reason_code IS NULL AND wave IS NULL) OR "
            "(event_type = 'copied' "
            "AND application_contact_id IS NOT NULL AND message_version_id IS NOT NULL "
            "AND kind IS NOT NULL AND channel IS NULL AND outcome IS NULL "
            "AND reason_code IS NULL AND wave IS NULL) OR "
            "(event_type = 'marked_sent' "
            "AND application_contact_id IS NOT NULL AND message_version_id IS NOT NULL "
            "AND kind IS NOT NULL AND channel IS NOT NULL AND outcome IS NULL "
            "AND reason_code IS NULL AND wave IS NULL) OR "
            "(event_type = 'outcome_recorded' "
            "AND application_contact_id IS NOT NULL AND message_version_id IS NULL "
            "AND kind IS NULL AND channel IS NULL AND outcome IS NOT NULL "
            "AND reason_code IS NULL AND wave IS NULL) OR "
            "(event_type IN ('paused', 'stopped') "
            "AND application_contact_id IS NULL AND message_version_id IS NULL "
            "AND kind IS NULL AND channel IS NULL AND outcome IS NULL "
            "AND reason_code IS NOT NULL AND wave IS NULL) OR "
            "(event_type = 'resumed' "
            "AND application_contact_id IS NULL AND message_version_id IS NULL "
            "AND kind IS NULL AND channel IS NULL AND outcome IS NULL "
            "AND reason_code IS NOT NULL AND wave IS NULL) OR "
            "(event_type = 'wave_advanced' "
            "AND application_contact_id IS NULL AND message_version_id IS NULL "
            "AND kind IS NULL AND channel IS NULL AND outcome IS NULL "
            "AND reason_code IS NULL AND wave BETWEEN 2 AND 5)",
            name=op.f("ck_outreach_events_event_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "outreach_sequence_id"],
            [
                "outreach_sequences.owner_id",
                "outreach_sequences.application_id",
                "outreach_sequences.id",
            ],
            name="fk_outreach_events_owner_sequence",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "application_contact_id"],
            [
                "application_contacts.owner_id",
                "application_contacts.application_id",
                "application_contacts.id",
            ],
            name="fk_outreach_events_owner_contact",
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
                "kind",
            ],
            [
                "outreach_message_versions.owner_id",
                "outreach_message_versions.application_id",
                "outreach_message_versions.outreach_sequence_id",
                "outreach_message_versions.application_contact_id",
                "outreach_message_versions.id",
                "outreach_message_versions.kind",
            ],
            name="fk_outreach_events_owner_message_version",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_outreach_events_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outreach_events")),
        sa.UniqueConstraint(
            "owner_id", "id", name="uq_outreach_events_owner_id_id"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "outreach_sequence_id",
            "sequence_number",
            name="uq_outreach_events_owner_sequence_number",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "outreach_sequence_id",
            "idempotency_key_hash",
            name="uq_outreach_events_owner_sequence_mutation",
        ),
    )
    op.create_index(
        "ix_outreach_events_contact_outcome",
        "outreach_events",
        ["owner_id", "application_contact_id", "event_type", "occurred_at"],
    )
    op.create_index(
        "ix_outreach_events_timeline",
        "outreach_events",
        ["owner_id", "outreach_sequence_id", "occurred_at", "sequence_number"],
    )
    op.create_index(
        "uq_outreach_events_marked_sent",
        "outreach_events",
        ["owner_id", "outreach_sequence_id", "application_contact_id", "kind"],
        unique=True,
        postgresql_where=sa.text("event_type = 'marked_sent'"),
        sqlite_where=sa.text("event_type = 'marked_sent'"),
    )


def downgrade() -> None:
    op.execute("DELETE FROM owner_mutation_receipts WHERE namespace LIKE 'outreach.%'")

    op.drop_index("uq_outreach_events_marked_sent", table_name="outreach_events")
    op.drop_index("ix_outreach_events_timeline", table_name="outreach_events")
    op.drop_index("ix_outreach_events_contact_outcome", table_name="outreach_events")
    op.drop_table("outreach_events")

    op.drop_index(
        "ix_outreach_message_versions_sequence",
        table_name="outreach_message_versions",
    )
    op.drop_table("outreach_message_versions")

    op.drop_index(
        "ix_outreach_sequences_owner_status", table_name="outreach_sequences"
    )
    op.drop_table("outreach_sequences")
