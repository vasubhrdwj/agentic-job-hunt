"""Add encrypted, replay-stable resume import snapshots.

Revision ID: 20260721_0020
Revises: 20260720_0019
Create Date: 2026-07-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260721_0020"
down_revision = "20260720_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resume_imports",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("resume_version_id", sa.String(length=32), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=120), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(parser_version)) BETWEEN 1 AND 64",
            name=op.f("ck_resume_imports_parser_version"),
        ),
        sa.CheckConstraint(
            "length(trim(media_type)) BETWEEN 1 AND 120",
            name=op.f("ck_resume_imports_media_type"),
        ),
        sa.CheckConstraint(
            "page_count IS NULL OR (page_count >= 1 AND page_count <= 1000)",
            name=op.f("ck_resume_imports_page_count"),
        ),
        sa.CheckConstraint(
            "length(trim(encrypted_payload)) >= 1 "
            "AND length(trim(encryption_key_id)) BETWEEN 1 AND 32",
            name=op.f("ck_resume_imports_encrypted_payload_envelope"),
        ),
        sa.CheckConstraint(
            "version = 1",
            name=op.f("ck_resume_imports_immutable_version"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "resume_version_id"],
            ["resume_versions.owner_id", "resume_versions.id"],
            name="fk_resume_imports_owner_resume",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["owners.id"],
            name=op.f("fk_resume_imports_owner_id_owners"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resume_imports")),
        sa.UniqueConstraint(
            "owner_id",
            "id",
            name="uq_resume_imports_owner_id_id",
        ),
    )
    op.create_index(
        "ix_resume_imports_owner_created",
        "resume_imports",
        ["owner_id", "created_at"],
    )
    op.create_index(
        "ix_resume_imports_owner_resume",
        "resume_imports",
        ["owner_id", "resume_version_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    imports = int(
        bind.execute(sa.text("SELECT COUNT(*) FROM resume_imports")).scalar_one()
    )
    if imports:
        raise RuntimeError(
            "refusing resume-import downgrade while encrypted import snapshots exist; "
            "export or delete the owning workspaces first"
        )
    op.drop_index("ix_resume_imports_owner_resume", table_name="resume_imports")
    op.drop_index("ix_resume_imports_owner_created", table_name="resume_imports")
    op.drop_table("resume_imports")
