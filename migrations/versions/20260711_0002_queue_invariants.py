"""Scope job deduplication to an owner or the system queue.

Revision ID: 20260711_0002
Revises: 20260711_0001
Create Date: 2026-07-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260711_0002"
down_revision = "20260711_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("background_jobs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "dedupe_scope",
                sa.String(length=72),
                server_default=sa.text("'system'"),
                nullable=True,
            )
        )
    op.execute(
        "UPDATE background_jobs "
        "SET dedupe_scope = CASE "
        "WHEN owner_id IS NULL THEN 'system' "
        "ELSE 'owner:' || owner_id END"
    )
    with op.batch_alter_table("background_jobs") as batch_op:
        batch_op.alter_column("dedupe_scope", nullable=False)
        batch_op.drop_constraint("uq_background_jobs_kind_dedupe", type_="unique")
        batch_op.create_unique_constraint(
            "uq_background_jobs_scope_kind_dedupe",
            ["dedupe_scope", "kind", "dedupe_key"],
        )
        batch_op.create_check_constraint(
            "dedupe_scope_matches_owner",
            "(owner_id IS NULL AND dedupe_scope = 'system') OR "
            "(owner_id IS NOT NULL AND dedupe_scope = 'owner:' || owner_id)",
        )


def downgrade() -> None:
    _make_legacy_dedupe_keys_unique()
    with op.batch_alter_table("background_jobs") as batch_op:
        batch_op.drop_constraint(
            "dedupe_scope_matches_owner",
            type_="check",
        )
        batch_op.drop_constraint("uq_background_jobs_scope_kind_dedupe", type_="unique")
        batch_op.create_unique_constraint(
            "uq_background_jobs_kind_dedupe",
            ["kind", "dedupe_key"],
        )
        batch_op.drop_column("dedupe_scope")


def _make_legacy_dedupe_keys_unique() -> None:
    """Preserve all rows before restoring 0001's global uniqueness rule.

    Owner-scoped duplicates are valid at 0002 but cannot coexist at 0001. Keep
    the first key unchanged and deterministically suffix later collisions with
    their opaque job id before the old constraint is recreated.
    """

    connection = op.get_bind()
    rows = list(
        connection.execute(
            sa.text(
                "SELECT id, kind, dedupe_key FROM background_jobs "
                "ORDER BY kind, dedupe_key, id"
            )
        ).mappings()
    )
    seen: set[tuple[str, str]] = set()
    for row in rows:
        job_id = str(row["id"])
        kind = str(row["kind"])
        original = str(row["dedupe_key"])
        candidate = original
        counter = 0
        while (kind, candidate) in seen:
            counter += 1
            suffix = f":legacy:{job_id}:{counter}"
            candidate = original[: 255 - len(suffix)] + suffix
        if candidate != original:
            connection.execute(
                sa.text(
                    "UPDATE background_jobs SET dedupe_key = :dedupe_key "
                    "WHERE id = :job_id"
                ),
                {"dedupe_key": candidate, "job_id": job_id},
            )
        seen.add((kind, candidate))
