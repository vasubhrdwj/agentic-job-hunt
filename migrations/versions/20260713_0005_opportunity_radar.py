"""Add durable opportunity scans, postings, provenance, and owner decisions.

Revision ID: 20260713_0005
Revises: 20260713_0004
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260713_0005"
down_revision = "20260713_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_postings",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("identity_kind", sa.String(length=16), nullable=False),
        sa.Column("identity_key", sa.Text(), nullable=False),
        sa.Column("identity_key_hash", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("company_slug", sa.String(length=120), nullable=False),
        sa.Column("source_job_id", sa.String(length=512), nullable=True),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("lifecycle_state", sa.String(length=16), nullable=False),
        sa.Column("closure_reason", sa.String(length=32), nullable=True),
        sa.Column("consecutive_complete_omissions", sa.Integer(), nullable=False),
        sa.Column("first_confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_lifecycle_evaluated_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
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
            "(identity_kind = 'native' AND source_job_id IS NOT NULL) OR "
            "(identity_kind = 'url' AND source_job_id IS NULL)",
            name=op.f("ck_job_postings_identity_shape"),
        ),
        sa.CheckConstraint(
            "(lifecycle_state = 'open' AND closed_at IS NULL "
            "AND closure_reason IS NULL) OR "
            "(lifecycle_state = 'closed' AND closed_at IS NOT NULL "
            "AND closure_reason IS NOT NULL)",
            name=op.f("ck_job_postings_lifecycle_timestamps"),
        ),
        sa.CheckConstraint(
            "closure_reason <> 'two_complete_omissions' OR "
            "consecutive_complete_omissions >= 2",
            name=op.f("ck_job_postings_omission_closure_threshold"),
        ),
        sa.CheckConstraint(
            "closure_reason IS NULL OR "
            "closure_reason IN ('explicit', 'two_complete_omissions')",
            name=op.f("ck_job_postings_closure_reason"),
        ),
        sa.CheckConstraint(
            "identity_kind IN ('native', 'url')",
            name=op.f("ck_job_postings_identity_kind"),
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('open', 'closed')",
            name=op.f("ck_job_postings_lifecycle_state"),
        ),
        sa.CheckConstraint(
            "consecutive_complete_omissions >= 0",
            name=op.f("ck_job_postings_omissions_nonnegative"),
        ),
        sa.CheckConstraint(
            "last_confirmed_at >= first_confirmed_at",
            name=op.f("ck_job_postings_confirmed_order"),
        ),
        sa.CheckConstraint(
            "version >= 1", name=op.f("ck_job_postings_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_job_postings_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_postings")),
        sa.UniqueConstraint("owner_id", "id", name="uq_job_postings_owner_id_id"),
        sa.UniqueConstraint(
            "owner_id",
            "identity_key_hash",
            name="uq_job_postings_owner_identity_hash",
        ),
    )
    op.create_index(
        "ix_job_postings_owner_company",
        "job_postings",
        ["owner_id", "company_slug", "last_confirmed_at"],
    )
    op.create_index(
        "ix_job_postings_owner_lifecycle",
        "job_postings",
        ["owner_id", "lifecycle_state"],
    )

    op.create_table(
        "job_posting_aliases",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("job_posting_id", sa.String(length=32), nullable=False),
        sa.Column("alias_kind", sa.String(length=16), nullable=False),
        sa.Column("alias_key", sa.Text(), nullable=False),
        sa.Column("alias_key_hash", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("company_slug", sa.String(length=120), nullable=False),
        sa.Column("source_job_id", sa.String(length=512), nullable=True),
        sa.Column("normalized_url", sa.Text(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(alias_kind = 'native' AND source_job_id IS NOT NULL "
            "AND normalized_url IS NULL) OR "
            "(alias_kind = 'url' AND source_job_id IS NULL "
            "AND normalized_url IS NOT NULL)",
            name=op.f("ck_job_posting_aliases_alias_shape"),
        ),
        sa.CheckConstraint(
            "alias_kind IN ('native', 'url')",
            name=op.f("ck_job_posting_aliases_alias_kind"),
        ),
        sa.CheckConstraint(
            "last_seen_at >= first_seen_at",
            name=op.f("ck_job_posting_aliases_seen_order"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "job_posting_id"],
            ["job_postings.owner_id", "job_postings.id"],
            name="fk_job_posting_aliases_owner_posting",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_job_posting_aliases_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_posting_aliases")),
        sa.UniqueConstraint(
            "owner_id", "alias_key_hash", name="uq_job_posting_aliases_owner_hash"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "job_posting_id",
            "id",
            name="uq_job_posting_aliases_owner_posting_id",
        ),
    )
    op.create_index(
        "ix_job_posting_aliases_posting",
        "job_posting_aliases",
        ["job_posting_id", "last_seen_at"],
    )

    op.create_table(
        "job_posting_versions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("job_posting_id", sa.String(length=32), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_job_id", sa.String(length=512), nullable=True),
        sa.Column("company_name", sa.String(length=240), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("apply_urls", sa.JSON(), nullable=False),
        sa.Column("location", sa.String(length=500), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("employment_type", sa.String(length=20), nullable=False),
        sa.Column("posted_at_text", sa.String(length=100), nullable=True),
        sa.Column("source_updated_at_text", sa.String(length=100), nullable=True),
        sa.Column("source_facts", sa.JSON(), nullable=False),
        sa.Column("source_confidence", sa.Float(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "employment_type IN ('full_time', 'contract', 'intern', 'unknown')",
            name=op.f("ck_job_posting_versions_employment_type"),
        ),
        sa.CheckConstraint(
            "source_confidence >= 0 AND source_confidence <= 1",
            name=op.f("ck_job_posting_versions_confidence_range"),
        ),
        sa.CheckConstraint(
            "version_number >= 1",
            name=op.f("ck_job_posting_versions_number_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "job_posting_id"],
            ["job_postings.owner_id", "job_postings.id"],
            name="fk_job_posting_versions_owner_posting",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_job_posting_versions_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_posting_versions")),
        sa.UniqueConstraint(
            "owner_id",
            "job_posting_id",
            "id",
            name="uq_job_posting_versions_owner_posting_id",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "job_posting_id",
            "version_number",
            name="uq_job_posting_versions_owner_number",
        ),
    )
    op.create_index(
        "ix_job_posting_versions_posting_observed",
        "job_posting_versions",
        ["job_posting_id", "observed_at"],
    )

    op.create_table(
        "opportunity_scans",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("saved_search_id", sa.String(length=32), nullable=False),
        sa.Column("saved_search_version", sa.Integer(), nullable=False),
        sa.Column("criteria_schema_version", sa.Integer(), nullable=False),
        sa.Column("criteria_snapshot", sa.JSON(), nullable=False),
        sa.Column("pack_snapshot", sa.String(length=64), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("background_job_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("stage", sa.String(length=100), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("terminal_source_count", sa.Integer(), nullable=False),
        sa.Column("successful_source_count", sa.Integer(), nullable=False),
        sa.Column("failed_source_count", sa.Integer(), nullable=False),
        sa.Column("observed_count", sa.Integer(), nullable=False),
        sa.Column("new_posting_count", sa.Integer(), nullable=False),
        sa.Column("changed_posting_count", sa.Integer(), nullable=False),
        sa.Column("new_opportunity_count", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
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
            "(status IN ('succeeded', 'partial', 'failed', 'cancelled') "
            "AND finalized_at IS NOT NULL) OR "
            "(status IN ('queued', 'running') AND finalized_at IS NULL)",
            name=op.f("ck_opportunity_scans_finalized_timestamp"),
        ),
        sa.CheckConstraint(
            "stage <> ''", name=op.f("ck_opportunity_scans_stage_nonempty")
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'partial', "
            "'failed', 'cancelled')",
            name=op.f("ck_opportunity_scans_status"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('running', 'succeeded', 'partial', 'failed') "
            "OR started_at IS NOT NULL",
            name=op.f("ck_opportunity_scans_started_timestamp"),
        ),
        sa.CheckConstraint(
            "trigger IN ('manual', 'scheduled')",
            name=op.f("ck_opportunity_scans_trigger"),
        ),
        sa.CheckConstraint(
            "criteria_schema_version >= 1",
            name=op.f("ck_opportunity_scans_criteria_version_positive"),
        ),
        sa.CheckConstraint(
            "saved_search_version >= 1",
            name=op.f("ck_opportunity_scans_search_version_positive"),
        ),
        sa.CheckConstraint(
            "source_count >= 0 AND terminal_source_count >= 0 AND "
            "successful_source_count >= 0 AND failed_source_count >= 0 AND "
            "observed_count >= 0 AND new_posting_count >= 0 AND "
            "changed_posting_count >= 0 AND new_opportunity_count >= 0",
            name=op.f("ck_opportunity_scans_counts_nonnegative"),
        ),
        sa.CheckConstraint(
            "terminal_source_count <= source_count AND "
            "successful_source_count <= terminal_source_count AND "
            "failed_source_count <= terminal_source_count AND "
            "successful_source_count + failed_source_count <= terminal_source_count",
            name=op.f("ck_opportunity_scans_source_counts_ordered"),
        ),
        sa.CheckConstraint(
            "version >= 1", name=op.f("ck_opportunity_scans_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["background_job_id"],
            ["background_jobs.id"],
            name=op.f("fk_opportunity_scans_background_job_id_background_jobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "saved_search_id"],
            ["saved_searches.owner_id", "saved_searches.id"],
            name="fk_opportunity_scans_owner_search",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_opportunity_scans_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_opportunity_scans")),
        sa.UniqueConstraint(
            "background_job_id",
            name=op.f("uq_opportunity_scans_background_job_id"),
        ),
        sa.UniqueConstraint(
            "owner_id", "id", name="uq_opportunity_scans_owner_id_id"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "idempotency_key_hash",
            name="uq_opportunity_scans_owner_idempotency",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "saved_search_id",
            "dedupe_key",
            name="uq_opportunity_scans_owner_search_dedupe",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "saved_search_id",
            "id",
            name="uq_opportunity_scans_owner_search_id",
        ),
    )
    op.create_index(
        "ix_opportunity_scans_owner_status",
        "opportunity_scans",
        ["owner_id", "status", "created_at"],
    )
    op.create_index(
        "ix_opportunity_scans_search_scheduled",
        "opportunity_scans",
        ["saved_search_id", "scheduled_for"],
    )

    op.create_table(
        "owner_opportunities",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("job_posting_id", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("decision_reason_code", sa.String(length=64), nullable=True),
        sa.Column("reviewed_posting_version_id", sa.String(length=32), nullable=True),
        sa.Column("decision_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_surfaced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_surfaced_at", sa.DateTime(timezone=True), nullable=False),
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
            "(decision = 'dismiss' AND decision_reason_code IS NOT NULL) OR "
            "(decision IN ('inbox', 'watch') AND decision_reason_code IS NULL)",
            name=op.f("ck_owner_opportunities_decision_reason"),
        ),
        sa.CheckConstraint(
            "decision IN ('inbox', 'watch', 'dismiss')",
            name=op.f("ck_owner_opportunities_decision"),
        ),
        sa.CheckConstraint(
            "last_surfaced_at >= first_surfaced_at",
            name=op.f("ck_owner_opportunities_surfaced_order"),
        ),
        sa.CheckConstraint(
            "version >= 1", name=op.f("ck_owner_opportunities_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "job_posting_id", "reviewed_posting_version_id"],
            [
                "job_posting_versions.owner_id",
                "job_posting_versions.job_posting_id",
                "job_posting_versions.id",
            ],
            name="fk_owner_opportunities_owner_reviewed_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "job_posting_id"],
            ["job_postings.owner_id", "job_postings.id"],
            name="fk_owner_opportunities_owner_posting",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_owner_opportunities_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_owner_opportunities")),
        sa.UniqueConstraint(
            "owner_id",
            "id",
            "job_posting_id",
            name="uq_owner_opportunities_owner_id_posting",
        ),
        sa.UniqueConstraint(
            "owner_id", "id", name="uq_owner_opportunities_owner_id_id"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "job_posting_id",
            name="uq_owner_opportunities_owner_posting",
        ),
    )
    op.create_index(
        "ix_owner_opportunities_today",
        "owner_opportunities",
        ["owner_id", "decision", "last_surfaced_at"],
    )

    op.create_table(
        "opportunity_decision_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("owner_opportunity_id", sa.String(length=32), nullable=False),
        sa.Column("job_posting_id", sa.String(length=32), nullable=False),
        sa.Column("posting_version_id", sa.String(length=32), nullable=False),
        sa.Column("previous_decision", sa.String(length=16), nullable=False),
        sa.Column("new_decision", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("encrypted_note", sa.Text(), nullable=True),
        sa.Column("note_key_id", sa.String(length=32), nullable=True),
        sa.Column("compensates_event_id", sa.String(length=32), nullable=True),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(new_decision = 'dismiss' AND reason_code IS NOT NULL) OR "
            "(new_decision IN ('inbox', 'watch') AND reason_code IS NULL)",
            name=op.f("ck_opportunity_decision_events_decision_reason"),
        ),
        sa.CheckConstraint(
            "previous_decision IN ('inbox', 'watch', 'dismiss') AND "
            "new_decision IN ('inbox', 'watch', 'dismiss')",
            name=op.f("ck_opportunity_decision_events_decision_values"),
        ),
        sa.CheckConstraint(
            "(encrypted_note IS NULL AND note_key_id IS NULL) OR "
            "(encrypted_note IS NOT NULL AND note_key_id IS NOT NULL)",
            name=op.f("ck_opportunity_decision_events_note_envelope_complete"),
        ),
        sa.CheckConstraint(
            "compensates_event_id IS NULL OR compensates_event_id <> id",
            name=op.f("ck_opportunity_decision_events_not_self_compensating"),
        ),
        sa.CheckConstraint(
            "previous_decision <> new_decision",
            name=op.f("ck_opportunity_decision_events_decision_changed"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "job_posting_id", "posting_version_id"],
            [
                "job_posting_versions.owner_id",
                "job_posting_versions.job_posting_id",
                "job_posting_versions.id",
            ],
            name="fk_opportunity_decision_events_owner_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "owner_opportunity_id", "compensates_event_id"],
            [
                "opportunity_decision_events.owner_id",
                "opportunity_decision_events.owner_opportunity_id",
                "opportunity_decision_events.id",
            ],
            name="fk_opportunity_decision_events_compensates",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "owner_opportunity_id", "job_posting_id"],
            [
                "owner_opportunities.owner_id",
                "owner_opportunities.id",
                "owner_opportunities.job_posting_id",
            ],
            name="fk_opportunity_decision_events_owner_opportunity",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_opportunity_decision_events_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_opportunity_decision_events")
        ),
        sa.UniqueConstraint(
            "owner_id",
            "id",
            name="uq_opportunity_decision_events_owner_id_id",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "owner_opportunity_id",
            "id",
            name="uq_opportunity_decision_events_owner_opportunity_id",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "owner_opportunity_id",
            "idempotency_key_hash",
            name="uq_opportunity_decision_events_idempotency",
        ),
    )
    op.create_index(
        "ix_opportunity_decision_events_timeline",
        "opportunity_decision_events",
        ["owner_opportunity_id", "occurred_at"],
    )

    op.create_table(
        "opportunity_scan_sources",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("opportunity_scan_id", sa.String(length=32), nullable=False),
        sa.Column("company_slug", sa.String(length=120), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("fetch_scope", sa.String(length=24), nullable=False),
        sa.Column("completeness", sa.String(length=16), nullable=False),
        sa.Column("observed_count", sa.Integer(), nullable=False),
        sa.Column("returned_count", sa.Integer(), nullable=False),
        sa.Column("persisted_count", sa.Integer(), nullable=False),
        sa.Column("warning_codes", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("used_fallback", sa.Boolean(), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
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
            "(status = 'failed' AND error_code IS NOT NULL) OR "
            "(status <> 'failed' AND error_code IS NULL)",
            name=op.f("ck_opportunity_scan_sources_failure_code"),
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND started_at IS NULL AND completed_at IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status IN ('succeeded', 'failed') AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL) OR "
            "(status = 'cancelled' AND completed_at IS NOT NULL)",
            name=op.f("ck_opportunity_scan_sources_timestamps_match_status"),
        ),
        sa.CheckConstraint(
            "completeness <> 'complete' OR "
            "(status = 'succeeded' AND fetch_scope = 'board_snapshot')",
            name=op.f("ck_opportunity_scan_sources_complete_board_only"),
        ),
        sa.CheckConstraint(
            "completeness IN ('unknown', 'partial', 'complete')",
            name=op.f("ck_opportunity_scan_sources_completeness"),
        ),
        sa.CheckConstraint(
            "fetch_scope IN ('criteria_filtered', 'board_snapshot')",
            name=op.f("ck_opportunity_scan_sources_fetch_scope"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_opportunity_scan_sources_status"),
        ),
        sa.CheckConstraint(
            "observed_count >= 0 AND returned_count >= 0 AND persisted_count >= 0 "
            "AND returned_count <= observed_count AND persisted_count <= returned_count",
            name=op.f("ck_opportunity_scan_sources_counts_ordered"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_opportunity_scan_sources_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "opportunity_scan_id"],
            ["opportunity_scans.owner_id", "opportunity_scans.id"],
            name="fk_opportunity_scan_sources_owner_scan",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_opportunity_scan_sources_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_opportunity_scan_sources")
        ),
        sa.UniqueConstraint(
            "owner_id", "id", name="uq_opportunity_scan_sources_owner_id_id"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "opportunity_scan_id",
            "company_slug",
            "source",
            name="uq_opportunity_scan_sources_partition",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "opportunity_scan_id",
            "id",
            name="uq_opportunity_scan_sources_owner_scan_id",
        ),
    )
    op.create_index(
        "ix_opportunity_scan_sources_health",
        "opportunity_scan_sources",
        ["owner_id", "source", "completed_at"],
    )
    op.create_index(
        "ix_opportunity_scan_sources_scan_status",
        "opportunity_scan_sources",
        ["opportunity_scan_id", "status"],
    )

    op.create_table(
        "saved_search_matches",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("saved_search_id", sa.String(length=32), nullable=False),
        sa.Column("job_posting_id", sa.String(length=32), nullable=False),
        sa.Column("first_scan_id", sa.String(length=32), nullable=False),
        sa.Column("last_scan_id", sa.String(length=32), nullable=False),
        sa.Column("last_posting_version_id", sa.String(length=32), nullable=False),
        sa.Column("match_count", sa.Integer(), nullable=False),
        sa.Column("first_matched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_matched_at", sa.DateTime(timezone=True), nullable=False),
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
            "last_matched_at >= first_matched_at",
            name=op.f("ck_saved_search_matches_matched_order"),
        ),
        sa.CheckConstraint(
            "match_count >= 1",
            name=op.f("ck_saved_search_matches_match_count_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "job_posting_id", "last_posting_version_id"],
            [
                "job_posting_versions.owner_id",
                "job_posting_versions.job_posting_id",
                "job_posting_versions.id",
            ],
            name="fk_saved_search_matches_owner_last_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "job_posting_id"],
            ["job_postings.owner_id", "job_postings.id"],
            name="fk_saved_search_matches_owner_posting",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "saved_search_id", "first_scan_id"],
            [
                "opportunity_scans.owner_id",
                "opportunity_scans.saved_search_id",
                "opportunity_scans.id",
            ],
            name="fk_saved_search_matches_owner_first_scan",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "saved_search_id", "last_scan_id"],
            [
                "opportunity_scans.owner_id",
                "opportunity_scans.saved_search_id",
                "opportunity_scans.id",
            ],
            name="fk_saved_search_matches_owner_last_scan",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "saved_search_id"],
            ["saved_searches.owner_id", "saved_searches.id"],
            name="fk_saved_search_matches_owner_search",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_saved_search_matches_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_search_matches")),
        sa.UniqueConstraint(
            "owner_id", "id", name="uq_saved_search_matches_owner_id_id"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "saved_search_id",
            "job_posting_id",
            name="uq_saved_search_matches_owner_search_posting",
        ),
    )
    op.create_index(
        "ix_saved_search_matches_owner_recent",
        "saved_search_matches",
        ["owner_id", "last_matched_at"],
    )

    op.create_table(
        "job_observations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("opportunity_scan_id", sa.String(length=32), nullable=False),
        sa.Column("opportunity_scan_source_id", sa.String(length=32), nullable=False),
        sa.Column("job_posting_id", sa.String(length=32), nullable=False),
        sa.Column("job_posting_version_id", sa.String(length=32), nullable=False),
        sa.Column("job_posting_alias_id", sa.String(length=32), nullable=False),
        sa.Column("first_party_url_verified", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "job_posting_id", "job_posting_alias_id"],
            [
                "job_posting_aliases.owner_id",
                "job_posting_aliases.job_posting_id",
                "job_posting_aliases.id",
            ],
            name="fk_job_observations_owner_posting_alias",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "job_posting_id", "job_posting_version_id"],
            [
                "job_posting_versions.owner_id",
                "job_posting_versions.job_posting_id",
                "job_posting_versions.id",
            ],
            name="fk_job_observations_owner_posting_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "opportunity_scan_id", "opportunity_scan_source_id"],
            [
                "opportunity_scan_sources.owner_id",
                "opportunity_scan_sources.opportunity_scan_id",
                "opportunity_scan_sources.id",
            ],
            name="fk_job_observations_owner_scan_source",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_job_observations_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_observations")),
        sa.UniqueConstraint(
            "owner_id", "id", name="uq_job_observations_owner_id_id"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "opportunity_scan_source_id",
            "job_posting_id",
            name="uq_job_observations_source_posting",
        ),
    )
    op.create_index(
        "ix_job_observations_posting",
        "job_observations",
        ["job_posting_id", "observed_at"],
    )
    op.create_index(
        "ix_job_observations_scan",
        "job_observations",
        ["opportunity_scan_id", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_job_observations_scan", table_name="job_observations")
    op.drop_index("ix_job_observations_posting", table_name="job_observations")
    op.drop_table("job_observations")

    op.drop_index(
        "ix_saved_search_matches_owner_recent", table_name="saved_search_matches"
    )
    op.drop_table("saved_search_matches")

    op.drop_index(
        "ix_opportunity_scan_sources_scan_status",
        table_name="opportunity_scan_sources",
    )
    op.drop_index(
        "ix_opportunity_scan_sources_health",
        table_name="opportunity_scan_sources",
    )
    op.drop_table("opportunity_scan_sources")

    op.drop_index(
        "ix_opportunity_decision_events_timeline",
        table_name="opportunity_decision_events",
    )
    op.drop_table("opportunity_decision_events")

    op.drop_index(
        "ix_owner_opportunities_today", table_name="owner_opportunities"
    )
    op.drop_table("owner_opportunities")

    op.drop_index(
        "ix_opportunity_scans_search_scheduled", table_name="opportunity_scans"
    )
    op.drop_index(
        "ix_opportunity_scans_owner_status", table_name="opportunity_scans"
    )
    op.drop_table("opportunity_scans")

    op.drop_index(
        "ix_job_posting_versions_posting_observed",
        table_name="job_posting_versions",
    )
    op.drop_table("job_posting_versions")

    op.drop_index(
        "ix_job_posting_aliases_posting", table_name="job_posting_aliases"
    )
    op.drop_table("job_posting_aliases")

    op.drop_index("ix_job_postings_owner_lifecycle", table_name="job_postings")
    op.drop_index("ix_job_postings_owner_company", table_name="job_postings")
    op.drop_table("job_postings")
