"""Round-trip serialization tests for the shared schemas.

If these break, the contract between Arpita's tools and Vasu's agent is
broken — both tracks rely on these shapes staying stable.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from job_hunt_agent.schemas import (
    HuntResult,
    JobCriteria,
    OutcomeLog,
    OutreachDraft,
    Person,
    Role,
)


# --- JobCriteria ---------------------------------------------------------

def test_jobcriteria_round_trip_minimal():
    """Minimal payload: optional comp fields default to None and survive a round trip."""
    data = {
        "role_keywords": ["SCIM", "IAM"],
        "seniority": "mid",
        "location": ["Remote-India"],
    }
    model = JobCriteria(**data)
    assert model.comp_min_lpa is None
    assert model.comp_max_lpa is None
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
    assert model.model_dump() == data
    assert JobCriteria(**model.model_dump()) == model


def test_jobcriteria_rejects_bad_seniority():
    """Seniority is constrained to the Literal set."""
    with pytest.raises(ValidationError):
        JobCriteria(
            role_keywords=["SCIM"],
            seniority="principal",  # not in the Literal
            location=["Remote"],
        )


# --- Role ----------------------------------------------------------------

def test_role_round_trip():
    data = {
        "company": "Okta",
        "title": "Senior Engineer, Identity",
        "url": "https://okta.com/careers/role/123",
        "location": "Bangalore",
        "summary": "Build SCIM 2.0 provisioning for enterprise customers.",
        "match_reason": "Listing explicitly mentions SCIM 2.0 + Okta lifecycle hooks.",
    }
    model = Role(**data)
    assert model.model_dump() == data
    assert Role.model_validate_json(model.model_dump_json()) == model


def test_role_requires_all_fields():
    """Every Role field is required — there are no optional fallbacks here."""
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
    assert model.model_dump() == data
    assert Person.model_validate_json(model.model_dump_json()) == model


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
