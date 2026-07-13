"""Durable, owner-scoped manual outreach sequences and immutable history."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _uuid_hex() -> str:
    return uuid4().hex


class OutreachSequence(Base):
    """One manually operated, policy-gated outreach sequence per application."""

    __tablename__ = "outreach_sequences"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_outreach_sequences_owner_id_id"),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "id",
            name="uq_outreach_sequences_owner_application_id",
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            name="uq_outreach_sequences_owner_application",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_outreach_sequences_owner_application",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "contact_plan_id"],
            ["contact_plans.owner_id", "contact_plans.application_id", "contact_plans.id"],
            name="fk_outreach_sequences_owner_contact_plan",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "status IN ('active', 'paused', 'stopped', 'completed')",
            name="status",
        ),
        CheckConstraint(
            "active_wave IS NULL OR active_wave BETWEEN 1 AND 5",
            name="active_wave",
        ),
        CheckConstraint(
            "reason_code IS NULL OR length(trim(reason_code)) BETWEEN 1 AND 100",
            name="reason_code",
        ),
        CheckConstraint(
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
            name="status_shape",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_outreach_sequences_owner_status",
            "owner_id",
            "status",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String(32), nullable=False)
    contact_plan_id: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    active_wave: Mapped[int | None] = mapped_column(Integer, default=1)
    reason_code: Mapped[str | None] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OutreachMessageVersion(Base):
    """One immutable encrypted revision of an initial or follow-up message."""

    __tablename__ = "outreach_message_versions"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "id", name="uq_outreach_message_versions_owner_id_id"
        ),
        UniqueConstraint(
            "owner_id",
            "application_id",
            "outreach_sequence_id",
            "application_contact_id",
            "id",
            "kind",
            name="uq_outreach_message_versions_event_ref",
        ),
        UniqueConstraint(
            "owner_id",
            "outreach_sequence_id",
            "application_contact_id",
            "kind",
            "version_number",
            name="uq_outreach_message_versions_revision",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "outreach_sequence_id"],
            [
                "outreach_sequences.owner_id",
                "outreach_sequences.application_id",
                "outreach_sequences.id",
            ],
            name="fk_outreach_message_versions_owner_sequence",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
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
        CheckConstraint("kind IN ('initial', 'follow_up')", name="kind"),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        CheckConstraint(
            "length(trim(encrypted_body)) >= 1 "
            "AND length(trim(encryption_key_id)) BETWEEN 1 AND 32",
            name="encrypted_body_envelope",
        ),
        CheckConstraint("length(content_hash) = 64", name="content_hash"),
        Index(
            "ix_outreach_message_versions_sequence",
            "owner_id",
            "outreach_sequence_id",
            "application_contact_id",
            "kind",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String(32), nullable=False)
    outreach_sequence_id: Mapped[str] = mapped_column(String(32), nullable=False)
    application_contact_id: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    encrypted_body: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OutreachEvent(Base):
    """Append-only manual action and sequence transition history."""

    __tablename__ = "outreach_events"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_outreach_events_owner_id_id"),
        UniqueConstraint(
            "owner_id",
            "outreach_sequence_id",
            "sequence_number",
            name="uq_outreach_events_owner_sequence_number",
        ),
        UniqueConstraint(
            "owner_id",
            "outreach_sequence_id",
            "idempotency_key_hash",
            name="uq_outreach_events_owner_sequence_mutation",
        ),
        ForeignKeyConstraint(
            ["owner_id", "application_id", "outreach_sequence_id"],
            [
                "outreach_sequences.owner_id",
                "outreach_sequences.application_id",
                "outreach_sequences.id",
            ],
            name="fk_outreach_events_owner_sequence",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
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
        ForeignKeyConstraint(
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
        CheckConstraint("sequence_number >= 1", name="sequence_number_positive"),
        CheckConstraint(
            "event_type IN ('sequence_started', 'message_saved', 'copied', "
            "'marked_sent', 'outcome_recorded', 'paused', 'resumed', "
            "'stopped', 'wave_advanced')",
            name="event_type",
        ),
        CheckConstraint(
            "kind IS NULL OR kind IN ('initial', 'follow_up')",
            name="kind",
        ),
        CheckConstraint(
            "channel IS NULL OR channel IN ('linkedin', 'email', 'other')",
            name="channel",
        ),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('no_reply', 'declined', 'unreachable', "
            "'useful_reply', 'introduced', 'referred', 'do_not_contact')",
            name="outcome",
        ),
        CheckConstraint("wave IS NULL OR wave BETWEEN 1 AND 5", name="wave"),
        CheckConstraint(
            "reason_code IS NULL OR length(trim(reason_code)) BETWEEN 1 AND 100",
            name="reason_code",
        ),
        CheckConstraint("length(idempotency_key_hash) = 64", name="mutation_hash"),
        CheckConstraint(
            "(event_type = 'marked_sent' AND kind = 'initial' "
            "AND follow_up_due_at IS NOT NULL) OR "
            "(NOT (event_type = 'marked_sent' AND kind = 'initial') "
            "AND follow_up_due_at IS NULL)",
            name="follow_up_due_shape",
        ),
        CheckConstraint(
            "(encrypted_note IS NULL AND note_key_id IS NULL) OR "
            "(encrypted_note IS NOT NULL AND length(trim(encrypted_note)) >= 1 "
            "AND note_key_id IS NOT NULL "
            "AND length(trim(note_key_id)) BETWEEN 1 AND 32)",
            name="note_envelope",
        ),
        CheckConstraint(
            "encrypted_note IS NULL OR "
            "event_type IN ('outcome_recorded', 'paused', 'resumed', 'stopped')",
            name="note_event_type",
        ),
        CheckConstraint(
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
            name="event_shape",
        ),
        Index(
            "uq_outreach_events_marked_sent",
            "owner_id",
            "outreach_sequence_id",
            "application_contact_id",
            "kind",
            unique=True,
            sqlite_where=text("event_type = 'marked_sent'"),
            postgresql_where=text("event_type = 'marked_sent'"),
        ),
        Index(
            "ix_outreach_events_timeline",
            "owner_id",
            "outreach_sequence_id",
            "occurred_at",
            "sequence_number",
        ),
        Index(
            "ix_outreach_events_contact_outcome",
            "owner_id",
            "application_contact_id",
            "event_type",
            "occurred_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String(32), nullable=False)
    outreach_sequence_id: Mapped[str] = mapped_column(String(32), nullable=False)
    application_contact_id: Mapped[str | None] = mapped_column(String(32))
    message_version_id: Mapped[str | None] = mapped_column(String(32))
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str | None] = mapped_column(String(20))
    channel: Mapped[str | None] = mapped_column(String(20))
    outcome: Mapped[str | None] = mapped_column(String(32))
    reason_code: Mapped[str | None] = mapped_column(String(100))
    wave: Mapped[int | None] = mapped_column(Integer)
    follow_up_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    encrypted_note: Mapped[str | None] = mapped_column(Text)
    note_key_id: Mapped[str | None] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["OutreachEvent", "OutreachMessageVersion", "OutreachSequence"]
