"""Immutable, owner-scoped cache records for grounded opportunity-fit verdicts."""

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
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _uuid_hex() -> str:
    return uuid4().hex


class OpportunityFitEvaluation(Base):
    """One encrypted, validated model verdict for an exact fit-input snapshot.

    The verdict itself stays inside the bound encrypted payload. The cleartext
    columns are only the fingerprints and evaluator metadata required to find
    and invalidate a cached result without decrypting every owner record.
    """

    __tablename__ = "opportunity_fit_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "id",
            name="uq_opportunity_fit_evaluations_owner_id_id",
        ),
        UniqueConstraint(
            "owner_id",
            "input_fingerprint",
            name="uq_opportunity_fit_evaluations_owner_input_fingerprint",
        ),
        ForeignKeyConstraint(
            ["owner_id", "job_posting_id", "posting_version_id"],
            [
                "job_posting_versions.owner_id",
                "job_posting_versions.job_posting_id",
                "job_posting_versions.id",
            ],
            name="fk_opportunity_fit_evaluations_owner_posting_version",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "length(posting_hash) = 64",
            name="posting_hash_length",
        ),
        CheckConstraint(
            "length(profile_input_fingerprint) = 64",
            name="profile_input_fingerprint_length",
        ),
        CheckConstraint(
            "length(input_fingerprint) = 64",
            name="input_fingerprint_length",
        ),
        CheckConstraint(
            "length(trim(evaluator_version)) BETWEEN 1 AND 64",
            name="evaluator_version",
        ),
        CheckConstraint(
            "length(trim(provider)) BETWEEN 1 AND 64",
            name="provider",
        ),
        CheckConstraint(
            "length(trim(model)) BETWEEN 1 AND 120",
            name="model",
        ),
        CheckConstraint(
            "result_schema_version = 1",
            name="result_schema_version",
        ),
        CheckConstraint(
            "length(trim(encrypted_payload)) >= 1 "
            "AND length(trim(encryption_key_id)) BETWEEN 1 AND 32",
            name="encrypted_payload_envelope",
        ),
        CheckConstraint("version = 1", name="immutable_version"),
        Index(
            "ix_opportunity_fit_evaluations_owner_posting",
            "owner_id",
            "job_posting_id",
            "created_at",
        ),
        Index(
            "ix_opportunity_fit_evaluations_owner_created",
            "owner_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    job_posting_id: Mapped[str] = mapped_column(String(32), nullable=False)
    posting_version_id: Mapped[str] = mapped_column(String(32), nullable=False)
    posting_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    result_schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["OpportunityFitEvaluation"]
