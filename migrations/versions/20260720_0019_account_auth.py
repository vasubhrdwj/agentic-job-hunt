"""Add multi-user email credentials and bounded login throttling.

Revision ID: 20260720_0019
Revises: 20260715_0018
Create Date: 2026-07-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260720_0019"
down_revision = "20260715_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owner_credentials",
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("normalized_email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_owner_credentials_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("owner_id", name=op.f("pk_owner_credentials")),
        sa.UniqueConstraint(
            "normalized_email",
            name=op.f("uq_owner_credentials_normalized_email"),
        ),
    )
    op.create_table(
        "auth_throttle_buckets",
        sa.Column("bucket_id", sa.String(length=3), nullable=False),
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(bucket_id) = 3",
            name=op.f("ck_auth_throttle_buckets_bucket_id_length"),
        ),
        sa.CheckConstraint(
            "failure_count >= 0",
            name=op.f("ck_auth_throttle_buckets_failure_count_nonnegative"),
        ),
        sa.PrimaryKeyConstraint("bucket_id", name=op.f("pk_auth_throttle_buckets")),
    )
    op.execute(
        sa.text(
            "INSERT INTO auth_throttle_buckets "
            "(bucket_id, failure_count, window_started_at) VALUES "
            "('sgn', 0, CURRENT_TIMESTAMP)"
        )
    )

    # The retired shared access key may have issued several browser sessions.
    # Keep only the most recently used active session per legacy owner so a
    # stale copy cannot race the one-time email/password account claim.
    bind = op.get_bind()
    sessions = sa.table(
        "owner_sessions",
        sa.column("id", sa.String(length=32)),
        sa.column("owner_id", sa.String(length=64)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("expires_at", sa.DateTime(timezone=True)),
        sa.column("last_seen_at", sa.DateTime(timezone=True)),
        sa.column("revoked_at", sa.DateTime(timezone=True)),
    )
    active_owner_ids = list(
        bind.execute(
            sa.select(sessions.c.owner_id)
            .where(
                sessions.c.revoked_at.is_(None),
                sessions.c.expires_at > sa.func.now(),
            )
            .distinct()
        ).scalars()
    )
    for owner_id in active_owner_ids:
        keep_session_id = bind.execute(
            sa.select(sessions.c.id)
            .where(
                sessions.c.owner_id == owner_id,
                sessions.c.revoked_at.is_(None),
                sessions.c.expires_at > sa.func.now(),
            )
            .order_by(
                sa.func.coalesce(
                    sessions.c.last_seen_at,
                    sessions.c.created_at,
                ).desc(),
                sessions.c.created_at.desc(),
                sessions.c.id.desc(),
            )
            .limit(1)
        ).scalar_one()
        bind.execute(
            sa.update(sessions)
            .where(
                sessions.c.owner_id == owner_id,
                sessions.c.id != keep_session_id,
                sessions.c.revoked_at.is_(None),
            )
            .values(revoked_at=sa.func.now())
        )


def downgrade() -> None:
    bind = op.get_bind()
    credentials = int(
        bind.execute(sa.text("SELECT COUNT(*) FROM owner_credentials")).scalar_one()
    )
    if credentials:
        raise RuntimeError(
            "refusing account-auth downgrade while owner credentials exist; "
            "an operator must preserve or explicitly remove accounts first"
        )
    op.drop_table("auth_throttle_buckets")
    op.drop_table("owner_credentials")
