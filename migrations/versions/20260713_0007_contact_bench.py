"""Add durable verified contact plans and evidence snapshots.

Revision ID: 20260713_0007
Revises: 20260713_0006
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260713_0007"
down_revision = "20260713_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contact_plans",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.String(length=32), nullable=False),
        sa.Column("plan_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("candidate_limit", sa.Integer(), nullable=False),
        sa.Column("confidence_floor", sa.Float(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("scoring_version", sa.String(length=64), nullable=False),
        sa.Column("background_job_id", sa.String(length=32), nullable=True),
        sa.Column("discovered_count", sa.Integer(), nullable=False),
        sa.Column("verified_count", sa.Integer(), nullable=False),
        sa.Column("selected_count", sa.Integer(), nullable=False),
        sa.Column("coverage_status", sa.String(length=16), nullable=False),
        sa.Column("exhausted", sa.Boolean(), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("shortfall_reasons", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
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
            "plan_number >= 1",
            name=op.f("ck_contact_plans_plan_number_positive"),
        ),
        sa.CheckConstraint(
            "target_count = 5",
            name=op.f("ck_contact_plans_target_count_five"),
        ),
        sa.CheckConstraint(
            "candidate_limit BETWEEN target_count AND 12",
            name=op.f("ck_contact_plans_candidate_limit_bounded"),
        ),
        sa.CheckConstraint(
            "confidence_floor >= 0.75 AND confidence_floor <= 1.0",
            name=op.f("ck_contact_plans_confidence_floor"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name=op.f("ck_contact_plans_status"),
        ),
        sa.CheckConstraint(
            "coverage_status IN ('pending', 'met', 'partial')",
            name=op.f("ck_contact_plans_coverage_status"),
        ),
        sa.CheckConstraint(
            "discovered_count >= 0 AND verified_count >= 0 AND selected_count >= 0 "
            "AND selected_count <= verified_count "
            "AND verified_count <= discovered_count "
            "AND discovered_count <= candidate_limit",
            name=op.f("ck_contact_plans_counts_ordered"),
        ),
        sa.CheckConstraint(
            "(status = 'queued' AND started_at IS NULL AND finalized_at IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND finalized_at IS NULL) OR "
            "(status IN ('completed', 'failed', 'cancelled') "
            "AND finalized_at IS NOT NULL)",
            name=op.f("ck_contact_plans_status_timestamps"),
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND coverage_status IN ('met', 'partial')) OR "
            "(status <> 'completed' AND coverage_status = 'pending')",
            name=op.f("ck_contact_plans_completion_coverage"),
        ),
        sa.CheckConstraint(
            "(coverage_status = 'met' AND selected_count = target_count) OR "
            "(coverage_status = 'partial' AND selected_count < target_count) OR "
            "coverage_status = 'pending'",
            name=op.f("ck_contact_plans_coverage_counts"),
        ),
        sa.CheckConstraint(
            "exhausted = false OR status = 'completed'",
            name=op.f("ck_contact_plans_exhausted_only_when_completed"),
        ),
        sa.CheckConstraint(
            "(status = 'failed' AND error_code IS NOT NULL) OR "
            "(status <> 'failed' AND error_code IS NULL)",
            name=op.f("ck_contact_plans_failure_code"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('queued', 'running') OR background_job_id IS NOT NULL",
            name=op.f("ck_contact_plans_active_job_required"),
        ),
        sa.CheckConstraint(
            "version >= 1", name=op.f("ck_contact_plans_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_contact_plans_owner_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["background_job_id"],
            ["background_jobs.id"],
            name=op.f("fk_contact_plans_background_job_id_background_jobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_contact_plans_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contact_plans")),
        sa.UniqueConstraint(
            "background_job_id",
            name=op.f("uq_contact_plans_background_job_id"),
        ),
        sa.UniqueConstraint("owner_id", "id", name="uq_contact_plans_owner_id_id"),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            "id",
            name="uq_contact_plans_owner_application_id",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            "plan_number",
            name="uq_contact_plans_owner_application_number",
        ),
    )
    op.create_index(
        "ix_contact_plans_owner_status",
        "contact_plans",
        ["owner_id", "status", "updated_at"],
    )
    op.create_index(
        "uq_contact_plans_owner_application_active",
        "contact_plans",
        ["owner_id", "application_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
        sqlite_where=sa.text("status IN ('queued', 'running')"),
    )

    op.create_table(
        "contacts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("identity_key", sa.Text(), nullable=False),
        sa.Column("identity_key_hash", sa.String(length=64), nullable=False),
        sa.Column("profile_url", sa.Text(), nullable=False),
        sa.Column("normalized_profile_url", sa.Text(), nullable=False),
        sa.Column("profile_source", sa.String(length=24), nullable=False),
        sa.Column("public_name", sa.String(length=200), nullable=False),
        sa.Column("lifecycle", sa.String(length=24), nullable=False),
        sa.Column("do_not_contact_at", sa.DateTime(timezone=True), nullable=True),
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
            "profile_source IN ('linkedin', 'github', 'company_page', 'other')",
            name=op.f("ck_contacts_profile_source"),
        ),
        sa.CheckConstraint(
            "lifecycle IN ('active', 'do_not_contact', 'retired')",
            name=op.f("ck_contacts_lifecycle"),
        ),
        sa.CheckConstraint(
            "(lifecycle = 'do_not_contact' AND do_not_contact_at IS NOT NULL) OR "
            "(lifecycle <> 'do_not_contact' AND do_not_contact_at IS NULL)",
            name=op.f("ck_contacts_do_not_contact_timestamp"),
        ),
        sa.CheckConstraint(
            "length(trim(public_name)) BETWEEN 1 AND 200",
            name=op.f("ck_contacts_name"),
        ),
        sa.CheckConstraint(
            "length(normalized_profile_url) BETWEEN 9 AND 2048 "
            "AND normalized_profile_url LIKE 'https://%'",
            name=op.f("ck_contacts_normalized_profile_url"),
        ),
        sa.CheckConstraint(
            "length(profile_url) BETWEEN 9 AND 2048 "
            "AND profile_url LIKE 'https://%'",
            name=op.f("ck_contacts_profile_url"),
        ),
        sa.CheckConstraint(
            "version >= 1", name=op.f("ck_contacts_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_contacts_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contacts")),
        sa.UniqueConstraint("owner_id", "id", name="uq_contacts_owner_id_id"),
        sa.UniqueConstraint(
            "owner_id", "identity_key_hash", name="uq_contacts_owner_identity_hash"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "normalized_profile_url",
            name="uq_contacts_owner_normalized_profile_url",
        ),
    )
    op.create_index(
        "ix_contacts_owner_lifecycle",
        "contacts",
        ["owner_id", "lifecycle", "updated_at"],
    )

    op.create_table(
        "application_contacts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.String(length=32), nullable=False),
        sa.Column("contact_plan_id", sa.String(length=32), nullable=False),
        sa.Column("contact_id", sa.String(length=32), nullable=False),
        sa.Column("discovery_provider", sa.String(length=64), nullable=False),
        sa.Column("discovery_query", sa.Text(), nullable=False),
        sa.Column("result_position", sa.Integer(), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_title", sa.String(length=300), nullable=False),
        sa.Column("current_company", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=24), nullable=False),
        sa.Column("verification_status", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("employer_evidence_excerpt", sa.String(length=1000), nullable=True),
        sa.Column("employer_evidence_url", sa.Text(), nullable=True),
        sa.Column("employer_evidence_source", sa.String(length=64), nullable=True),
        sa.Column(
            "employer_evidence_observed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("why_relevant", sa.String(length=2000), nullable=False),
        sa.Column("relationship_status", sa.String(length=16), nullable=False),
        sa.Column("relationship_evidence_summary", sa.Text(), nullable=True),
        sa.Column("relationship_evidence_url", sa.Text(), nullable=True),
        sa.Column("team_proximity_status", sa.String(length=16), nullable=False),
        sa.Column("team_evidence_summary", sa.Text(), nullable=True),
        sa.Column("team_evidence_url", sa.Text(), nullable=True),
        sa.Column("score_total", sa.Integer(), nullable=False),
        sa.Column("score_components", sa.JSON(), nullable=False),
        sa.Column("scoring_version", sa.String(length=64), nullable=False),
        sa.Column("pool_rank", sa.Integer(), nullable=False),
        sa.Column("bench_rank", sa.Integer(), nullable=True),
        sa.Column("wave", sa.Integer(), nullable=True),
        sa.Column("bench_state", sa.String(length=20), nullable=False),
        sa.Column("exclusion_reason", sa.String(length=100), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unlocked_at", sa.DateTime(timezone=True), nullable=True),
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
            "result_position >= 1",
            name=op.f("ck_application_contacts_result_position_positive"),
        ),
        sa.CheckConstraint(
            "pool_rank BETWEEN 1 AND 12",
            name=op.f("ck_application_contacts_pool_rank"),
        ),
        sa.CheckConstraint(
            "bench_rank IS NULL OR bench_rank BETWEEN 1 AND 5",
            name=op.f("ck_application_contacts_bench_rank"),
        ),
        sa.CheckConstraint(
            "wave IS NULL OR wave BETWEEN 1 AND 5",
            name=op.f("ck_application_contacts_wave"),
        ),
        sa.CheckConstraint(
            "category IN ('warm_path', 'team_peer', 'adjacent_peer', "
            "'team_leader', 'recruiter', 'other')",
            name=op.f("ck_application_contacts_category"),
        ),
        sa.CheckConstraint(
            "verification_status IN ('unverified', 'verified', 'rejected', 'stale')",
            name=op.f("ck_application_contacts_verification_status"),
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name=op.f("ck_application_contacts_confidence"),
        ),
        sa.CheckConstraint(
            "relationship_status IN ('verified', 'inferred', 'unknown')",
            name=op.f("ck_application_contacts_relationship_status"),
        ),
        sa.CheckConstraint(
            "team_proximity_status IN ('verified', 'inferred', 'unknown')",
            name=op.f("ck_application_contacts_team_proximity_status"),
        ),
        sa.CheckConstraint(
            "bench_state IN ('candidate', 'excluded', 'overflow', 'ready', "
            "'reserve', 'paused', 'stopped')",
            name=op.f("ck_application_contacts_bench_state"),
        ),
        sa.CheckConstraint(
            "score_total >= 0 AND score_total <= 1000",
            name=op.f("ck_application_contacts_score_total"),
        ),
        sa.CheckConstraint(
            "length(trim(current_title)) BETWEEN 1 AND 300",
            name=op.f("ck_application_contacts_current_title"),
        ),
        sa.CheckConstraint(
            "length(trim(current_company)) BETWEEN 1 AND 200",
            name=op.f("ck_application_contacts_current_company"),
        ),
        sa.CheckConstraint(
            "length(trim(why_relevant)) BETWEEN 1 AND 2000",
            name=op.f("ck_application_contacts_why_relevant"),
        ),
        sa.CheckConstraint(
            "(verification_status = 'verified' AND confidence >= 0.75 "
            "AND verified_at IS NOT NULL "
            "AND employer_evidence_excerpt IS NOT NULL "
            "AND employer_evidence_url IS NOT NULL "
            "AND employer_evidence_source IS NOT NULL "
            "AND employer_evidence_observed_at IS NOT NULL) OR "
            "(verification_status <> 'verified' AND bench_rank IS NULL)",
            name=op.f("ck_application_contacts_verified_evidence"),
        ),
        sa.CheckConstraint(
            "employer_evidence_excerpt IS NULL OR "
            "length(trim(employer_evidence_excerpt)) BETWEEN 1 AND 1000",
            name=op.f("ck_application_contacts_employer_evidence_excerpt"),
        ),
        sa.CheckConstraint(
            "employer_evidence_url IS NULL OR "
            "(length(employer_evidence_url) BETWEEN 9 AND 2048 "
            "AND employer_evidence_url LIKE 'https://%')",
            name=op.f("ck_application_contacts_employer_evidence_url"),
        ),
        sa.CheckConstraint(
            "employer_evidence_source IS NULL OR "
            "length(trim(employer_evidence_source)) BETWEEN 1 AND 64",
            name=op.f("ck_application_contacts_employer_evidence_source"),
        ),
        sa.CheckConstraint(
            "(bench_rank IS NULL AND wave IS NULL "
            "AND bench_state IN ('candidate', 'excluded', 'overflow')) OR "
            "(bench_rank IS NOT NULL AND wave IS NOT NULL "
            "AND verification_status = 'verified' AND exclusion_reason IS NULL "
            "AND bench_state IN ('ready', 'reserve', 'paused', 'stopped'))",
            name=op.f("ck_application_contacts_bench_selection"),
        ),
        sa.CheckConstraint(
            "(bench_state = 'ready' AND unlocked_at IS NOT NULL) OR "
            "(bench_state = 'reserve' AND unlocked_at IS NULL) OR "
            "bench_state IN ('candidate', 'excluded', 'overflow', 'paused', 'stopped')",
            name=op.f("ck_application_contacts_bench_unlock"),
        ),
        sa.CheckConstraint(
            "category <> 'warm_path' OR relationship_status = 'verified'",
            name=op.f("ck_application_contacts_warm_path_verified"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_application_contacts_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id"],
            ["applications.owner_id", "applications.id"],
            name="fk_application_contacts_owner_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "application_id", "contact_plan_id"],
            ["contact_plans.owner_id", "contact_plans.application_id", "contact_plans.id"],
            name="fk_application_contacts_owner_plan",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "contact_id"],
            ["contacts.owner_id", "contacts.id"],
            name="fk_application_contacts_owner_contact",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_application_contacts_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_contacts")),
        sa.UniqueConstraint(
            "owner_id", "id", name="uq_application_contacts_owner_id_id"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "application_id",
            "id",
            name="uq_application_contacts_owner_application_id",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "contact_plan_id",
            "contact_id",
            name="uq_application_contacts_owner_plan_contact",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "contact_plan_id",
            "pool_rank",
            name="uq_application_contacts_owner_plan_pool_rank",
        ),
    )
    op.create_index(
        "ix_application_contacts_owner_application",
        "application_contacts",
        ["owner_id", "application_id", "bench_rank"],
    )
    op.create_index(
        "ix_application_contacts_owner_contact",
        "application_contacts",
        ["owner_id", "contact_id", "created_at"],
    )
    op.create_index(
        "uq_application_contacts_owner_plan_bench_rank",
        "application_contacts",
        ["owner_id", "contact_plan_id", "bench_rank"],
        unique=True,
        postgresql_where=sa.text("bench_rank IS NOT NULL"),
        sqlite_where=sa.text("bench_rank IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_application_contacts_owner_plan_bench_rank",
        table_name="application_contacts",
    )
    op.drop_index(
        "ix_application_contacts_owner_contact",
        table_name="application_contacts",
    )
    op.drop_index(
        "ix_application_contacts_owner_application",
        table_name="application_contacts",
    )
    op.drop_table("application_contacts")

    op.drop_index("ix_contacts_owner_lifecycle", table_name="contacts")
    op.drop_table("contacts")

    op.drop_index(
        "uq_contact_plans_owner_application_active",
        table_name="contact_plans",
    )
    op.drop_index("ix_contact_plans_owner_status", table_name="contact_plans")
    op.drop_table("contact_plans")

    # The domain rows must disappear before their active job FKs can be
    # removed. Then clean older-schema tables so no runnable orphan or replay
    # receipt survives a downgrade/re-upgrade cycle.
    op.execute("DELETE FROM background_jobs WHERE kind = 'discover_contacts'")
    op.execute(
        "DELETE FROM owner_mutation_receipts "
        "WHERE namespace LIKE 'contact_search.create:%'"
    )
