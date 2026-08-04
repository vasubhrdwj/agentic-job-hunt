"""Persistence invariants for encrypted opportunity-fit evaluation cache rows."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    Base,
    JobPosting,
    JobPostingVersion,
    OpportunityFitEvaluation,
    Owner,
)


NOW = datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def fit_cache_db(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'fit-cache.db'}")
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        for suffix in ("a", "b"):
            session.add(
                Owner(
                    id=f"owner-{suffix}",
                    display_name=f"Owner {suffix.upper()}",
                    timezone="UTC",
                )
            )
        session.flush()
        for suffix in ("a", "b"):
            session.add(_posting(suffix))
        session.flush()
        for suffix in ("a", "b"):
            session.add(_posting_version(suffix))
    try:
        yield database
    finally:
        database.dispose()


def _posting(suffix: str) -> JobPosting:
    return JobPosting(
        id=f"posting-{suffix}",
        owner_id=f"owner-{suffix}",
        identity_kind="native",
        identity_key=f"source:example:{suffix}",
        identity_key_hash=suffix * 64,
        source="example",
        company_slug=f"example-{suffix}",
        source_job_id=suffix,
        canonical_url=f"https://careers.example.com/jobs/{suffix}",
        lifecycle_state="open",
        consecutive_complete_omissions=0,
        first_confirmed_at=NOW,
        last_confirmed_at=NOW,
        version=1,
    )


def _posting_version(suffix: str) -> JobPostingVersion:
    return JobPostingVersion(
        id=f"posting-version-{suffix}",
        owner_id=f"owner-{suffix}",
        job_posting_id=f"posting-{suffix}",
        version_number=1,
        content_hash=("1" if suffix == "a" else "2") * 64,
        source="example",
        source_job_id=suffix,
        company_name=f"Example {suffix.upper()}",
        title="Backend Engineer",
        canonical_url=f"https://careers.example.com/jobs/{suffix}",
        apply_urls=[f"https://careers.example.com/jobs/{suffix}"],
        location="Remote",
        summary="Build reliable backend systems.",
        description="Design APIs and event-driven services.",
        employment_type="full_time",
        source_facts={},
        source_confidence=1.0,
        observed_at=NOW,
    )


def _evaluation(
    suffix: str,
    *,
    evaluation_id: str | None = None,
    input_fingerprint: str | None = None,
    owner_id: str | None = None,
) -> OpportunityFitEvaluation:
    return OpportunityFitEvaluation(
        id=evaluation_id or f"fit-{suffix}",
        owner_id=owner_id or f"owner-{suffix}",
        job_posting_id=f"posting-{suffix}",
        posting_version_id=f"posting-version-{suffix}",
        posting_hash=("3" if suffix == "a" else "4") * 64,
        profile_input_fingerprint=("5" if suffix == "a" else "6") * 64,
        input_fingerprint=input_fingerprint
        or (("7" if suffix == "a" else "8") * 64),
        evaluator_version="fit-policy-v1",
        provider="google-gemini",
        model="gemini-2.5-flash",
        result_schema_version=1,
        encrypted_payload=f"ciphertext-{suffix}",
        encryption_key_id="local-dev",
        version=1,
        created_at=NOW,
    )


def test_input_fingerprint_is_unique_per_owner_not_globally(
    fit_cache_db: Database,
) -> None:
    shared_fingerprint = "9" * 64
    with fit_cache_db.session() as session:
        session.add_all(
            [
                _evaluation("a", input_fingerprint=shared_fingerprint),
                _evaluation("b", input_fingerprint=shared_fingerprint),
            ]
        )

    with pytest.raises(IntegrityError):
        with fit_cache_db.session() as session:
            session.add(
                _evaluation(
                    "a",
                    evaluation_id="fit-a-duplicate",
                    input_fingerprint=shared_fingerprint,
                )
            )


def test_evaluation_cannot_reference_another_owners_posting_version(
    fit_cache_db: Database,
) -> None:
    with pytest.raises(IntegrityError):
        with fit_cache_db.session() as session:
            session.add(
                _evaluation(
                    "a",
                    evaluation_id="fit-cross-owner",
                    owner_id="owner-b",
                )
            )


def test_posting_version_delete_cascades_only_its_cached_evaluations(
    fit_cache_db: Database,
) -> None:
    with fit_cache_db.session() as session:
        session.add_all([_evaluation("a"), _evaluation("b")])

    with fit_cache_db.session() as session:
        session.execute(
            delete(JobPostingVersion).where(JobPostingVersion.id == "posting-version-a")
        )

    with fit_cache_db.session() as session:
        assert session.scalar(
            select(func.count()).select_from(OpportunityFitEvaluation)
        ) == 1
        remaining = session.scalar(select(OpportunityFitEvaluation))
        assert remaining is not None
        assert remaining.owner_id == "owner-b"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("posting_hash", "short"),
        ("profile_input_fingerprint", "short"),
        ("input_fingerprint", "short"),
        ("evaluator_version", " "),
        ("provider", " "),
        ("model", " "),
        ("result_schema_version", 2),
        ("encrypted_payload", " "),
        ("version", 2),
    ],
)
def test_evaluation_rejects_invalid_or_mutable_cache_metadata(
    fit_cache_db: Database,
    field: str,
    value: object,
) -> None:
    evaluation = _evaluation("a")
    setattr(evaluation, field, value)
    with pytest.raises(IntegrityError):
        with fit_cache_db.session() as session:
            session.add(evaluation)
