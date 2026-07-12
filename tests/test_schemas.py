"""Round-trip serialization tests for the shared schemas.

If these break, the contract between Arpita's tools and Vasu's agent is
broken — both tracks rely on these shapes staying stable.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from job_hunt_agent.schemas import (
    Company,
    CompanySource,
    EmploymentType,
    HuntResult,
    JobCriteria,
    OutcomeLog,
    OutreachDraft,
    PastDraft,
    Person,
    Role,
)
from job_hunt_agent.sources.base import SourceAdapter


LEGACY_ROLE_DATA = {
    "company": "Okta",
    "title": "Senior Engineer, Identity",
    "url": "https://okta.com/careers/role/123",
    "location": "Bangalore",
    "summary": "Build SCIM 2.0 provisioning for enterprise customers.",
    "match_reason": "Listing explicitly mentions SCIM 2.0 + Okta lifecycle hooks.",
}


# --- JobCriteria ---------------------------------------------------------

def test_jobcriteria_round_trip_minimal():
    """A legacy minimal payload remains valid and receives only V2 defaults."""
    data = {
        "role_keywords": ["SCIM", "IAM"],
        "seniority": "mid",
        "location": ["Remote-India"],
    }
    model = JobCriteria(**data)
    assert model.comp_min_lpa is None
    assert model.comp_max_lpa is None
    assert model.employment_types == [EmploymentType.full_time]
    assert model.max_age_days == 45
    assert model.country == "in"
    # JSON round trip preserves equality.
    assert JobCriteria.model_validate_json(model.model_dump_json()) == model


def test_jobcriteria_round_trip_full():
    """All fields populated: dict round trip is lossless."""
    data = {
        "role_keywords": ["identity", "okta"],
        "seniority": "senior",
        "location": ["Hyderabad", "Bangalore"],
        "comp_min_lpa": 40,
        "comp_max_lpa": 80,
    }
    model = JobCriteria(**data)
    assert model.model_dump(exclude_defaults=True) == data
    assert JobCriteria(**model.model_dump()) == model


def test_jobcriteria_rejects_bad_seniority():
    """Seniority is constrained to the Literal set."""
    with pytest.raises(ValidationError):
        JobCriteria(
            role_keywords=["SCIM"],
            seniority="principal",  # not in the Literal
            location=["Remote"],
        )


# --- Company -------------------------------------------------------------

def test_company_round_trip_and_safe_defaults():
    company = Company(
        name="Razorpay",
        slug="razorpay",
        source=CompanySource.greenhouse,
        source_token="razorpay",
    )

    assert company.careers_domains == []
    assert company.hire_locations == []
    assert company.tags == []
    assert company.active is True
    assert Company.model_validate_json(company.model_dump_json()) == company

    other = Company(
        name="Example",
        slug="example",
        source=CompanySource.google_jobs,
        source_token=None,
    )
    company.tags.append("fintech")
    assert other.tags == []


def test_company_requires_source_token_even_when_nullable():
    with pytest.raises(ValidationError):
        Company(
            name="Razorpay",
            slug="razorpay",
            source=CompanySource.greenhouse,
        )


# --- Role ----------------------------------------------------------------

def test_role_round_trip():
    model = Role(**LEGACY_ROLE_DATA)
    assert model.model_dump(exclude_defaults=True) == LEGACY_ROLE_DATA
    assert Role.model_validate_json(model.model_dump_json()) == model


def test_legacy_role_construction_gets_backward_compatible_defaults():
    model = Role(**LEGACY_ROLE_DATA)

    assert model.source is CompanySource.google_jobs
    assert model.company_slug is None
    assert model.source_job_id is None
    assert model.apply_urls == []
    assert model.posted_at is None
    assert model.employment_type is EmploymentType.unknown
    assert model.raw_description is None
    assert model.fit_score is None
    assert model.confidence == 1.0


def test_role_v2_fields_round_trip():
    model = Role(
        **LEGACY_ROLE_DATA,
        source=CompanySource.greenhouse,
        company_slug="okta",
        source_job_id="123",
        apply_urls=[
            "https://okta.com/careers/role/123",
            "https://boards.greenhouse.io/okta/jobs/123",
        ],
        posted_at="2026-06-20T10:30:00Z",
        employment_type=EmploymentType.full_time,
        raw_description="Full job description.",
        fit_score=0.87,
        confidence=0.95,
    )

    assert Role.model_validate_json(model.model_dump_json()) == model


def test_role_native_identity_is_optional_but_never_half_populated():
    assert Role(**LEGACY_ROLE_DATA).company_slug is None

    for update in ({"company_slug": "okta"}, {"source_job_id": "123"}):
        with pytest.raises(ValidationError, match="must either both be set"):
            Role(**LEGACY_ROLE_DATA, **update)


@pytest.mark.parametrize(
    ("field", "value"),
    [("fit_score", -0.01), ("fit_score", 1.01), ("confidence", -0.01), ("confidence", 1.01)],
)
def test_role_rejects_scores_outside_unit_interval(field, value):
    with pytest.raises(ValidationError):
        Role(**LEGACY_ROLE_DATA, **{field: value})


def test_role_apply_urls_default_is_not_shared():
    first = Role(**LEGACY_ROLE_DATA)
    second = Role(**LEGACY_ROLE_DATA)

    first.apply_urls.append("https://example.com/apply")
    assert second.apply_urls == []


def test_role_requires_all_fields():
    """All legacy Role fields remain required."""
    with pytest.raises(ValidationError):
        Role(company="Okta", title="Engineer")  # missing url, location, summary, match_reason


# --- Person --------------------------------------------------------------

def test_person_round_trip():
    data = {
        "name": "Priya Sharma",
        "title": "Staff Engineer, Identity Platform",
        "company": "Okta",
        "profile_url": "https://linkedin.com/in/priya-sharma-example",
        "source": "linkedin",
        "why_relevant": (
            "Leads the SCIM provisioning team — owns the system the role would join."
        ),
    }
    model = Person(**data)
    assert model.model_dump(exclude_defaults=True) == data
    assert model.verified_current_employer is False
    assert model.confidence == 0.0
    assert Person.model_validate_json(model.model_dump_json()) == model


def test_person_honesty_fields_round_trip():
    person = Person(
        name="Priya Sharma",
        title="Staff Engineer, Identity Platform",
        company="Okta",
        profile_url="https://linkedin.com/in/priya-sharma-example",
        source="linkedin",
        why_relevant="Current identity-platform engineer at the hiring company.",
        verified_current_employer=True,
        confidence=0.9,
    )

    assert Person.model_validate_json(person.model_dump_json()) == person


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_person_rejects_confidence_outside_unit_interval(confidence):
    with pytest.raises(ValidationError):
        Person(
            name="X",
            title="Y",
            company="Z",
            profile_url="https://example.com",
            source="other",
            why_relevant="Evidence-backed referral target.",
            confidence=confidence,
        )


def test_person_rejects_bad_source():
    """Source is constrained to the Literal set."""
    with pytest.raises(ValidationError):
        Person(
            name="X",
            title="Y",
            company="Z",
            profile_url="https://example.com",
            source="twitter",  # not in the Literal
            why_relevant="...",
        )


# --- SourceAdapter -------------------------------------------------------

def test_source_adapter_is_runtime_importable_and_structural():
    class Adapter:
        name = "test"

        def supports(self, company: Company) -> bool:
            return company.active

        def fetch_open_roles(
            self,
            company: Company,
            criteria: JobCriteria,
        ) -> list[Role]:
            return []

    assert isinstance(Adapter(), SourceAdapter)


# --- HuntResult ----------------------------------------------------------

def test_huntresult_round_trip():
    role = Role(
        company="Okta",
        title="Senior Engineer, Identity",
        url="https://okta.com/careers/role/123",
        location="Bangalore",
        summary="Build SCIM 2.0 provisioning for enterprise customers.",
        match_reason="Listing explicitly mentions SCIM 2.0 + Okta lifecycle hooks.",
    )
    person = Person(
        name="Priya Sharma",
        title="Staff Engineer, Identity Platform",
        company="Okta",
        profile_url="https://linkedin.com/in/priya-sharma-example",
        source="linkedin",
        why_relevant="Leads the identity platform adjacent to the open role.",
    )
    draft = OutreachDraft(
        draft_id="draft-fixed-123",
        role=role,
        person=person,
        message="Hi Priya, I saw the identity role and your platform work seems close to it.",
    )
    result = HuntResult(run_id="run-fixed-abc", roles=[role], outreach=[draft])

    assert HuntResult.model_validate_json(result.model_dump_json()) == result


# --- OutreachDraft draft_id ---------------------------------------------

def test_outreach_draft_generates_unique_ids():
    role = Role(
        company="Okta",
        title="Senior Engineer, Identity",
        url="https://okta.com/careers/role/123",
        location="Bangalore",
        summary="Build SCIM 2.0 provisioning.",
        match_reason="Mentions SCIM 2.0.",
    )
    person = Person(
        name="Priya Sharma",
        title="Staff Engineer",
        company="Okta",
        profile_url="https://linkedin.com/in/priya",
        source="linkedin",
        why_relevant="Adjacent team.",
    )
    a = OutreachDraft(role=role, person=person, message="hi")
    b = OutreachDraft(role=role, person=person, message="hi")
    assert a.draft_id and b.draft_id
    assert a.draft_id != b.draft_id


def test_huntresult_requires_run_id():
    """run_id is required — there is no default. The runner generates one."""
    with pytest.raises(ValidationError):
        HuntResult(roles=[], outreach=[])


# --- OutcomeLog ----------------------------------------------------------

def test_outcome_log_round_trip_minimal():
    log = OutcomeLog(draft_id="draft-1", outcome="replied")
    assert log.notes is None
    assert log.logged_at is None
    assert OutcomeLog.model_validate_json(log.model_dump_json()) == log


def test_outcome_log_round_trip_full():
    log = OutcomeLog(
        draft_id="draft-1",
        outcome="introduced",
        notes="Forwarded to hiring manager.",
        logged_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
    )
    parsed = OutcomeLog.model_validate_json(log.model_dump_json())
    assert parsed == log


def test_outcome_log_rejects_bad_outcome():
    with pytest.raises(ValidationError):
        OutcomeLog(draft_id="draft-1", outcome="ghosted")


# --- PastDraft -----------------------------------------------------------


def test_pastdraft_round_trip():
    draft = PastDraft(
        message="Hi Priya, I saw the identity role and your platform work.",
        role_title="Senior Engineer, Identity",
        company="Okta",
        eval_score=4.5,
        matched_keywords=["SCIM"],
        span_id="span-123",
        trace_id="trace-456",
    )

    assert PastDraft.model_validate_json(draft.model_dump_json()) == draft
