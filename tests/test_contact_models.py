"""Focused persistence invariants for verified application contact benches."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    Application,
    ApplicationContact,
    Base,
    Contact,
    ContactPlan,
    JobPosting,
    JobPostingVersion,
    Owner,
    OwnerOpportunity,
)


NOW = datetime(2026, 7, 14, 8, 30, tzinfo=timezone.utc)


@pytest.fixture
def contact_db(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'contacts.db'}")
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        session.add_all(
            [
                Owner(id="owner-a", display_name="Owner A", timezone="Asia/Kolkata"),
                Owner(id="owner-b", display_name="Owner B", timezone="UTC"),
            ]
        )
        session.flush()
        session.add(_posting())
        session.flush()
        session.add(_posting_version())
        session.flush()
        session.add(
            OwnerOpportunity(
                id="opportunity-a",
                owner_id="owner-a",
                job_posting_id="posting-a",
                decision="pursued",
                first_surfaced_at=NOW,
                last_surfaced_at=NOW,
                version=1,
            )
        )
        session.flush()
        session.add(
            Application(
                id="application-a",
                owner_id="owner-a",
                owner_opportunity_id="opportunity-a",
                job_posting_id="posting-a",
                pursued_posting_version_id="posting-version-a",
                stage="pursuing",
                version=1,
            )
        )
    try:
        yield database
    finally:
        database.dispose()


def _posting() -> JobPosting:
    return JobPosting(
        id="posting-a",
        owner_id="owner-a",
        identity_kind="native",
        identity_key="source:greenhouse:acme:123",
        identity_key_hash="1" * 64,
        source="greenhouse",
        company_slug="acme",
        source_job_id="123",
        canonical_url="https://boards.greenhouse.io/acme/jobs/123",
        lifecycle_state="open",
        consecutive_complete_omissions=0,
        first_confirmed_at=NOW,
        last_confirmed_at=NOW,
        version=1,
    )


def _posting_version() -> JobPostingVersion:
    return JobPostingVersion(
        id="posting-version-a",
        owner_id="owner-a",
        job_posting_id="posting-a",
        version_number=1,
        content_hash="2" * 64,
        source="greenhouse",
        source_job_id="123",
        company_name="Acme",
        title="Senior Backend Engineer",
        canonical_url="https://boards.greenhouse.io/acme/jobs/123",
        apply_urls=["https://boards.greenhouse.io/acme/jobs/123"],
        location="Remote India",
        summary="Build reliable backend systems.",
        description="Design and operate reliable backend systems.",
        employment_type="full_time",
        source_facts={},
        source_confidence=1.0,
        observed_at=NOW,
    )


def _plan(
    *,
    plan_id: str = "plan-a",
    plan_number: int = 1,
    owner_id: str = "owner-a",
    status: str = "completed",
    target_count: int = 5,
    discovered_count: int = 0,
    verified_count: int = 0,
    selected_count: int = 0,
    coverage_status: str = "partial",
    exhausted: bool = True,
    shortfall_reasons: list[str] | None = None,
) -> ContactPlan:
    terminal = status in {"completed", "failed", "cancelled"}
    return ContactPlan(
        id=plan_id,
        owner_id=owner_id,
        application_id="application-a",
        plan_number=plan_number,
        status=status,
        target_count=target_count,
        candidate_limit=12,
        confidence_floor=0.75,
        policy_version="contact-policy-v1",
        scoring_version="contact-score-v1",
        discovered_count=discovered_count,
        verified_count=verified_count,
        selected_count=selected_count,
        coverage_status=coverage_status,
        exhausted=exhausted,
        retryable=False,
        shortfall_reasons=shortfall_reasons or ["fixture_shortfall"],
        error_code="discovery_failed" if status == "failed" else None,
        version=1,
        started_at=NOW if status == "running" else None,
        finalized_at=NOW if terminal else None,
    )


def _contact(
    *,
    contact_id: str,
    identity_hash: str,
    owner_id: str = "owner-a",
) -> Contact:
    slug = contact_id.replace("contact-", "person-")
    profile_url = f"https://www.linkedin.com/in/{slug}"
    return Contact(
        id=contact_id,
        owner_id=owner_id,
        identity_key=f"linkedin:{slug}",
        identity_key_hash=identity_hash,
        profile_url=profile_url,
        normalized_profile_url=profile_url,
        profile_source="linkedin",
        public_name=f"Person {slug}",
        lifecycle="active",
        version=1,
    )


def _application_contact(
    *,
    row_id: str,
    contact_id: str,
    pool_rank: int,
    owner_id: str = "owner-a",
    plan_id: str = "plan-a",
    verification_status: str = "unverified",
    confidence: float = 0.0,
    bench_rank: int | None = None,
    wave: int | None = None,
    bench_state: str = "candidate",
) -> ApplicationContact:
    verified = verification_status == "verified"
    return ApplicationContact(
        id=row_id,
        owner_id=owner_id,
        application_id="application-a",
        contact_plan_id=plan_id,
        contact_id=contact_id,
        discovery_provider="public_web",
        discovery_query="Acme backend engineering team",
        result_position=pool_rank,
        discovered_at=NOW,
        current_title="Senior Software Engineer",
        current_company="Acme",
        category="team_peer",
        verification_status=verification_status,
        confidence=confidence,
        verified_at=NOW if verified else None,
        employer_evidence_excerpt="Senior Software Engineer at Acme" if verified else None,
        employer_evidence_url=(
            f"https://www.linkedin.com/in/{contact_id}" if verified else None
        ),
        employer_evidence_source="linkedin" if verified else None,
        employer_evidence_observed_at=NOW if verified else None,
        why_relevant="Works on the likely hiring team.",
        relationship_status="unknown",
        team_proximity_status="inferred",
        score_total=820 if verified else 300,
        score_components={"role_fit": 300},
        scoring_version="contact-score-v1",
        pool_rank=pool_rank,
        bench_rank=bench_rank,
        wave=wave,
        bench_state=bench_state,
        unlocked_at=NOW if bench_state == "ready" else None,
        version=1,
    )


def test_contact_identity_is_deduplicated_per_owner(contact_db: Database) -> None:
    shared_hash = "a" * 64
    with contact_db.session() as session:
        session.add(
            _contact(contact_id="contact-a", identity_hash=shared_hash)
        )

    with pytest.raises(IntegrityError):
        with contact_db.session() as session:
            session.add(
                _contact(contact_id="contact-a-duplicate", identity_hash=shared_hash)
            )

    with pytest.raises(IntegrityError):
        with contact_db.session() as session:
            duplicate_url = _contact(
                contact_id="contact-same-url",
                identity_hash="b" * 64,
            )
            duplicate_url.profile_url = "https://www.linkedin.com/in/person-a"
            duplicate_url.normalized_profile_url = (
                "https://www.linkedin.com/in/person-a"
            )
            session.add(duplicate_url)

    with contact_db.session() as session:
        session.add(
            _contact(
                contact_id="contact-b",
                identity_hash=shared_hash,
                owner_id="owner-b",
            )
        )

    with contact_db.session() as session:
        assert session.scalar(select(func.count(Contact.id))) == 2


def test_contact_plan_and_role_evidence_reject_cross_owner_edges(
    contact_db: Database,
) -> None:
    with pytest.raises(IntegrityError):
        with contact_db.session() as session:
            session.add(_plan(owner_id="owner-b"))

    with contact_db.session() as session:
        session.add(_plan())
        session.add(
            _contact(
                contact_id="contact-b",
                identity_hash="b" * 64,
                owner_id="owner-b",
            )
        )

    with pytest.raises(IntegrityError):
        with contact_db.session() as session:
            session.add(
                _application_contact(
                    row_id="application-contact-cross-owner",
                    contact_id="contact-b",
                    pool_rank=1,
                )
            )

    with pytest.raises(IntegrityError):
        with contact_db.session() as session:
            session.add(
                _application_contact(
                    row_id="application-contact-cross-application",
                    owner_id="owner-b",
                    contact_id="contact-b",
                    pool_rank=1,
                )
            )


def test_completed_partial_plan_truthfully_records_three_of_five(
    contact_db: Database,
) -> None:
    with contact_db.session() as session:
        session.add(
            _plan(
                status="completed",
                discovered_count=8,
                verified_count=3,
                selected_count=3,
                coverage_status="partial",
                exhausted=True,
                shortfall_reasons=[
                    "Only three public profiles had current-employer evidence."
                ],
            )
        )

    with contact_db.session() as session:
        plan = session.get(ContactPlan, "plan-a")
        assert plan is not None
        assert (plan.selected_count, plan.target_count) == (3, 5)
        assert plan.coverage_status == "partial"
        assert plan.exhausted is True
        assert plan.shortfall_reasons

    with pytest.raises(IntegrityError):
        with contact_db.session() as session:
            session.add(
                _plan(
                    plan_id="plan-false-met",
                    plan_number=2,
                    status="completed",
                    discovered_count=3,
                    verified_count=3,
                    selected_count=3,
                    coverage_status="met",
                )
            )

    with pytest.raises(IntegrityError):
        with contact_db.session() as session:
            session.add(
                _plan(
                    plan_id="plan-wrong-target",
                    plan_number=3,
                    target_count=3,
                )
            )


def test_verified_bench_members_require_evidence_confidence_and_valid_ranks(
    contact_db: Database,
) -> None:
    contact_ids = ["contact-valid", "contact-no-proof", "contact-low", "contact-unverified"]
    with contact_db.session() as session:
        session.add(_plan())
        session.add_all(
            [
                _contact(contact_id=contact_id, identity_hash=f"{index:x}" * 64)
                for index, contact_id in enumerate(contact_ids, start=1)
            ]
        )
        session.flush()
        session.add(
            _application_contact(
                row_id="application-contact-valid",
                contact_id="contact-valid",
                pool_rank=1,
                verification_status="verified",
                confidence=0.9,
                bench_rank=1,
                wave=1,
                bench_state="ready",
            )
        )

    with pytest.raises(IntegrityError):
        with contact_db.session() as session:
            row = _application_contact(
                row_id="application-contact-no-proof",
                contact_id="contact-no-proof",
                pool_rank=2,
                verification_status="verified",
                confidence=0.9,
            )
            row.employer_evidence_excerpt = None
            session.add(row)

    with pytest.raises(IntegrityError):
        with contact_db.session() as session:
            row = _application_contact(
                row_id="application-contact-empty-source",
                contact_id="contact-no-proof",
                pool_rank=2,
                verification_status="verified",
                confidence=0.9,
            )
            row.employer_evidence_source = "   "
            session.add(row)

    with pytest.raises(IntegrityError):
        with contact_db.session() as session:
            session.add(
                _application_contact(
                    row_id="application-contact-low",
                    contact_id="contact-low",
                    pool_rank=2,
                    verification_status="verified",
                    confidence=0.74,
                )
            )

    with pytest.raises(IntegrityError):
        with contact_db.session() as session:
            session.add(
                _application_contact(
                    row_id="application-contact-unverified",
                    contact_id="contact-unverified",
                    pool_rank=2,
                    verification_status="unverified",
                    confidence=0.0,
                    bench_rank=2,
                    wave=2,
                    bench_state="ready",
                )
            )


def test_pool_and_non_null_bench_ranks_are_unique_within_a_plan(
    contact_db: Database,
) -> None:
    with contact_db.session() as session:
        session.add(_plan())
        session.add_all(
            [
                _contact(contact_id="contact-one", identity_hash="1" * 64),
                _contact(contact_id="contact-two", identity_hash="2" * 64),
                _contact(contact_id="contact-three", identity_hash="3" * 64),
            ]
        )
        session.flush()
        session.add(
            _application_contact(
                row_id="application-contact-one",
                contact_id="contact-one",
                pool_rank=1,
                verification_status="verified",
                confidence=0.9,
                bench_rank=1,
                wave=1,
                bench_state="ready",
            )
        )

    with pytest.raises(IntegrityError):
        with contact_db.session() as session:
            session.add(
                _application_contact(
                    row_id="application-contact-duplicate-pool",
                    contact_id="contact-two",
                    pool_rank=1,
                )
            )

    with pytest.raises(IntegrityError):
        with contact_db.session() as session:
            session.add(
                _application_contact(
                    row_id="application-contact-duplicate-bench",
                    contact_id="contact-three",
                    pool_rank=2,
                    verification_status="verified",
                    confidence=0.9,
                    bench_rank=1,
                    wave=2,
                    bench_state="ready",
                )
            )


def test_deleting_application_cascades_role_data_but_retains_canonical_contact(
    contact_db: Database,
) -> None:
    with contact_db.session() as session:
        session.add(_plan())
        session.add(_contact(contact_id="contact-keeper", identity_hash="f" * 64))
        session.flush()
        session.add(
            _application_contact(
                row_id="application-contact-keeper",
                contact_id="contact-keeper",
                pool_rank=1,
            )
        )

    with contact_db.session() as session:
        application = session.get(Application, "application-a")
        assert application is not None
        session.delete(application)

    with contact_db.session() as session:
        assert session.scalar(select(func.count(ContactPlan.id))) == 0
        assert session.scalar(select(func.count(ApplicationContact.id))) == 0
        assert session.scalar(select(func.count(Contact.id))) == 1
        assert session.get(Contact, "contact-keeper") is not None


def test_contact_reference_is_deferred_for_owner_erasure_but_blocks_direct_delete(
    contact_db: Database,
) -> None:
    contact_fk = next(
        constraint
        for constraint in ApplicationContact.__table__.foreign_key_constraints
        if constraint.name == "fk_application_contacts_owner_contact"
    )
    assert contact_fk.ondelete is None
    assert contact_fk.deferrable is True
    assert contact_fk.initially == "DEFERRED"

    with contact_db.session() as session:
        session.add(_plan())
        session.add(_contact(contact_id="contact-owner-delete", identity_hash="e" * 64))
        session.flush()
        session.add(
            _application_contact(
                row_id="application-contact-owner-delete",
                contact_id="contact-owner-delete",
                pool_rank=1,
            )
        )

    with pytest.raises(IntegrityError):
        with contact_db.session() as session:
            contact = session.get(Contact, "contact-owner-delete")
            assert contact is not None
            session.delete(contact)

    with contact_db.session() as session:
        owner = session.get(Owner, "owner-a")
        assert owner is not None
        session.delete(owner)

    with contact_db.session() as session:
        assert session.scalar(select(func.count(ContactPlan.id))) == 0
        assert session.scalar(select(func.count(ApplicationContact.id))) == 0
        assert session.scalar(select(func.count(Contact.id))) == 0
