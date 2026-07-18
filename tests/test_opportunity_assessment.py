from job_hunt_agent.opportunity_assessment import (
    AssessmentEvidence,
    AssessmentPosting,
    AssessmentProfile,
    AssessmentTarget,
    assess_opportunity,
)


TARGET = AssessmentTarget(
    role_families=(
        "Backend Software Engineer",
        "Software Development Engineer",
        "Platform Engineer",
        "Infrastructure Engineer",
        "Site Reliability Engineer",
    ),
    seniority_levels=("junior", "mid"),
    target_locations=("India", "Remote India"),
)
PROFILE = AssessmentProfile(
    current_location="Gurugram, India",
    employment_types=("full_time",),
)
RESUME = """
Software Engineer building Node.js and TypeScript backend services on AWS.
Owned Lambda event pipelines, OAuth, SCIM, Kafka/MSK delivery, DLQs, retries,
Docker services, REST APIs, PostgreSQL, and distributed systems.
"""
EVIDENCE = (
    AssessmentEvidence(
        id="xapi",
        statement="Owned an AWS Lambda xAPI pipeline with OAuth delivery.",
        skills=("AWS", "Lambda", "OAuth", "event-driven systems"),
    ),
    AssessmentEvidence(
        id="kafka",
        statement="Shipped Kafka retry, jitter, DLQ, and at-least-once reliability.",
        skills=("Kafka", "retries", "DLQ"),
    ),
)


def test_backend_role_gets_a_strong_explainable_assessment() -> None:
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Software Engineer I (Backend)",
            description=(
                "What we are looking for. 1-2 years of experience building backend "
                "services. Work with Node.js, AWS, REST APIs, Kafka, PostgreSQL, "
                "distributed systems, CI/CD, and Docker. " * 4
            ),
            location="India",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert result.fit_band == "strong"
    assert result.confidence == "high"
    assert {"Node.js", "AWS", "Kafka", "REST"} <= set(result.matched_terms)
    assert result.approved_evidence_ids == ("xapi", "kafka")
    assert result.representative_requirement is not None
    assert result.representative_requirement in (
        "What we are looking for. 1-2 years of experience building backend "
        "services. Work with Node.js, AWS, REST APIs, Kafka, PostgreSQL, "
        "distributed systems, CI/CD, and Docker. " * 4
    )
    assert any("experience requirement" in gap for gap in result.gaps)


def test_infrastructure_role_with_major_skill_gaps_is_a_stretch() -> None:
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Site Reliability Engineer II",
            description=(
                "Required: operate Kubernetes using Terraform and Helm, own on-call, "
                "and build observability with AWS, Docker, Redis, and PostgreSQL. " * 5
            ),
            location="India",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert result.fit_band == "stretch"
    assert any("Kubernetes" in gap and "Terraform" in gap for gap in result.gaps)


def test_more_senior_title_is_never_promoted_above_stretch() -> None:
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Staff Backend Engineer",
            description=(
                "Lead Node.js, AWS, Lambda, Kafka, Docker, REST, PostgreSQL, OAuth, "
                "SCIM, and distributed systems architecture. " * 5
            ),
            location="India",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert result.fit_band == "stretch"
    assert any("more senior" in gap for gap in result.gaps)


def test_non_backend_role_is_low_even_when_generic_prose_overlaps() -> None:
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Administrative Business Partner",
            description=(
                "Partner with software engineering teams and people across the company. "
                "The role requires strong experience, communication, and organization. " * 5
            ),
            location="India",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert result.fit_band == "low"
    assert result.matched_terms == ()


def test_explicit_employment_conflict_is_low_but_unknown_location_is_not_a_failure() -> None:
    conflict = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description="Build Node.js AWS Kafka REST PostgreSQL services. " * 15,
            location=None,
            employment_type="contract",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )
    unknown_location = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description="Build Node.js AWS Kafka REST PostgreSQL services. " * 15,
            location=None,
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert conflict.fit_band == "low"
    assert unknown_location.fit_band == "strong"
    assert all("location" not in gap.casefold() for gap in unknown_location.gaps)


def test_no_approved_evidence_cannot_receive_the_strong_band() -> None:
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description="Build Node.js AWS Kafka REST PostgreSQL services. " * 15,
            location="India",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=(),
    )

    assert result.fit_band == "promising"
    assert result.confidence == "high"
    assert result.approved_evidence_ids == ()
