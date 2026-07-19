from job_hunt_agent.opportunity_assessment import (
    AssessmentAuthorization,
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
    years_of_experience=1,
    work_authorizations=(AssessmentAuthorization("IN", "citizen"),),
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
    assert result.eligibility == "eligible"
    assert {"Node.js", "AWS", "Kafka", "REST"} <= set(result.matched_terms)
    assert result.approved_evidence_ids == ("xapi", "kafka")
    assert result.representative_requirement is not None
    assert result.representative_requirement in (
        "What we are looking for. 1-2 years of experience building backend "
        "services. Work with Node.js, AWS, REST APIs, Kafka, PostgreSQL, "
        "distributed systems, CI/CD, and Docker. " * 4
    )
    assert all("experience requirement" not in gap for gap in result.gaps)
    assert all(len(item) <= 200 for item in (*result.strengths, *result.gaps))


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


def test_adjacent_sre_role_is_not_promoted_as_a_backend_match() -> None:
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Site Reliability Engineer",
            description=(
                "Operate reliable AWS services using Node.js, Kafka, Docker, REST, "
                "PostgreSQL, OAuth, SCIM, and distributed systems. " * 10
            ),
            location="India",
            employment_type="full_time",
        ),
        target=AssessmentTarget(
            role_families=(
                "Backend Software Engineer",
                "Software Development Engineer",
                "Backend Developer",
            ),
            seniority_levels=("junior", "mid"),
            target_locations=("India", "Remote India"),
        ),
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert result.fit_band == "stretch"
    assert any("adjacent to" in gap for gap in result.gaps)
    assert all("title aligns" not in strength.casefold() for strength in result.strengths)


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
    assert conflict.eligibility == "likely_ineligible"
    assert unknown_location.fit_band == "promising"
    assert unknown_location.eligibility == "uncertain"
    assert any("location" in gap.casefold() for gap in unknown_location.gaps)


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


def test_remote_us_and_onsite_mode_conflicts_cannot_score_as_matches() -> None:
    remote_us = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description="Build Node.js AWS Kafka REST PostgreSQL services. " * 15,
            location="Remote — United States",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )
    onsite = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description="Build Node.js AWS Kafka REST PostgreSQL services. " * 15,
            location="India (Onsite)",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=AssessmentProfile(
            current_location="Gurugram, India",
            work_modes=("remote",),
            employment_types=("full_time",),
            years_of_experience=1,
        ),
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert (remote_us.fit_band, remote_us.eligibility) == ("low", "likely_ineligible")
    assert (onsite.fit_band, onsite.eligibility) == ("low", "likely_ineligible")


def test_indiana_is_not_mistaken_for_india() -> None:
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description="Build Node.js AWS Kafka REST PostgreSQL services. " * 15,
            location="Indianapolis, Indiana (Onsite)",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert result.fit_band != "strong"
    assert result.eligibility == "uncertain"


def test_unmet_experience_and_authorization_are_eligibility_failures() -> None:
    experience = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description="Required: 10+ years building Node.js AWS Kafka REST services. " * 12,
            location="India",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )
    authorization = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description="Build Node.js AWS Kafka REST PostgreSQL services. " * 15,
            location="Remote - United States",
            employment_type="full_time",
        ),
        target=AssessmentTarget(
            role_families=TARGET.role_families,
            seniority_levels=TARGET.seniority_levels,
            target_locations=("United States",),
        ),
        profile=AssessmentProfile(
            current_location="Gurugram, India",
            employment_types=("full_time",),
            years_of_experience=1,
            work_authorizations=(AssessmentAuthorization("US", "not_authorized"),),
        ),
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert (experience.fit_band, experience.eligibility) == ("low", "likely_ineligible")
    assert (authorization.fit_band, authorization.eligibility) == ("low", "likely_ineligible")


def test_current_location_does_not_override_explicit_authorization() -> None:
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description="Build Node.js AWS Kafka REST PostgreSQL services. " * 15,
            location="India",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=AssessmentProfile(
            current_location="Gurugram, India",
            employment_types=("full_time",),
            years_of_experience=1,
            work_authorizations=(AssessmentAuthorization("IN", "not_authorized"),),
        ),
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert (result.fit_band, result.eligibility) == ("low", "likely_ineligible")


def test_missing_work_mode_or_remote_geography_stays_uncertain() -> None:
    india_without_mode = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description="Build Node.js AWS Kafka REST PostgreSQL services. " * 15,
            location="India",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=AssessmentProfile(
            current_location="Gurugram, India",
            work_modes=("remote",),
            employment_types=("full_time",),
            years_of_experience=1,
            work_authorizations=(AssessmentAuthorization("IN", "citizen"),),
        ),
        resume_text=RESUME,
        evidence=EVIDENCE,
    )
    remote_without_country = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description="Build Node.js AWS Kafka REST PostgreSQL services. " * 15,
            location="Remote",
            employment_type="full_time",
        ),
        target=AssessmentTarget(
            role_families=TARGET.role_families,
            seniority_levels=TARGET.seniority_levels,
            target_locations=("Remote India",),
        ),
        profile=AssessmentProfile(
            current_location="Gurugram, India",
            work_modes=("remote",),
            employment_types=("full_time",),
            years_of_experience=1,
            work_authorizations=(AssessmentAuthorization("IN", "citizen"),),
        ),
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert india_without_mode.eligibility == "uncertain"
    assert india_without_mode.fit_band != "strong"
    assert remote_without_country.eligibility == "uncertain"
    assert remote_without_country.fit_band != "strong"


def test_decimal_and_preferred_experience_are_not_hard_failures() -> None:
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description=(
                "Preferred qualifications: 1.5 years of experience with Node.js, AWS, "
                "Kafka, REST APIs, and PostgreSQL. " * 12
            ),
            location="India",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=AssessmentProfile(
            current_location="Gurugram, India",
            employment_types=("full_time",),
            years_of_experience=1,
            work_authorizations=(AssessmentAuthorization("IN", "citizen"),),
        ),
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert result.eligibility == "eligible"
    assert result.fit_band == "insufficient_data"
    assert any("1.5 years" in gap for gap in result.gaps)


def test_bullet_sections_keep_required_experience_hard() -> None:
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description=(
                "Requirements\n"
                "5+ years of backend experience required\n"
                "Node.js, AWS, REST APIs, and Kafka\n"
                "Preferred\n"
                "Kubernetes and Terraform\n"
            ) * 8,
            location="India",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert (result.fit_band, result.eligibility) == ("low", "likely_ineligible")
    assert any("at least 5 years" in gap for gap in result.gaps)


def test_optional_tool_list_does_not_dilute_required_skill_coverage() -> None:
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description=(
                "Requirements\n"
                "Build Node.js services with AWS, REST APIs, and Kafka.\n"
                "Nice to have\n"
                "Kubernetes, Terraform, Helm, Redis, MongoDB, GraphQL, and gRPC.\n"
            ) * 8,
            location="India",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert result.fit_band == "strong"
    assert all("Kubernetes" not in gap for gap in result.gaps)


def test_alternative_and_negated_skills_do_not_create_false_gaps() -> None:
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description=(
                "Required: experience with either Node.js or Python, plus AWS and REST APIs. "
                "Kubernetes experience is not required. " * 10
            ),
            location="India",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert result.fit_band != "stretch"
    assert all("Kubernetes" not in gap for gap in result.gaps)


def test_multiple_offered_modes_and_countries_accept_any_viable_option() -> None:
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description="Build Node.js services with AWS, REST APIs, and Kafka. " * 12,
            location="Hybrid or Remote - United States or Canada",
            employment_type="full_time",
        ),
        target=AssessmentTarget(
            role_families=TARGET.role_families,
            seniority_levels=TARGET.seniority_levels,
            target_locations=("Canada",),
        ),
        profile=AssessmentProfile(
            current_location="Gurugram, India",
            work_modes=("hybrid",),
            employment_types=("full_time",),
            years_of_experience=1,
            work_authorizations=(AssessmentAuthorization("CA", "work_permit"),),
        ),
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert result.eligibility == "eligible"
    assert result.fit_band != "low"


def test_authorization_must_match_the_target_compatible_country() -> None:
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description="Build Node.js services with AWS, REST APIs, and Kafka. " * 12,
            location="Remote - United States or Canada",
            employment_type="full_time",
        ),
        target=AssessmentTarget(
            role_families=TARGET.role_families,
            seniority_levels=TARGET.seniority_levels,
            target_locations=("Canada",),
        ),
        profile=AssessmentProfile(
            current_location="Gurugram, India",
            work_modes=("remote",),
            employment_types=("full_time",),
            years_of_experience=1,
            work_authorizations=(
                AssessmentAuthorization("US", "work_permit"),
                AssessmentAuthorization("CA", "not_authorized"),
            ),
        ),
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert (result.fit_band, result.eligibility) == ("low", "likely_ineligible")
    assert any("CA" in gap for gap in result.gaps)


def test_experience_hardness_is_local_to_each_requirement() -> None:
    mixed_preference = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description=(
                "Requirements: 5 years of experience required, Python preferred. "
                "Build Node.js AWS REST Kafka services. " * 10
            ),
            location="India",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )
    company_age = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description=(
                "With 20 years of experience serving customers, we require 1 year "
                "of backend experience with Node.js AWS REST Kafka services. " * 10
            ),
            location="India",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert mixed_preference.eligibility == "likely_ineligible"
    assert any("at least 5 years" in gap for gap in mixed_preference.gaps)
    assert company_age.eligibility == "eligible"
    assert all("20" not in gap for gap in company_age.gaps)


def test_negation_applies_only_to_its_local_skill_subclause() -> None:
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description=(
                "Kubernetes is not required, but AWS and REST APIs are required. "
                "Build Node.js services with Kafka. " * 12
            ),
            location="India",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert {"AWS", "REST", "Node.js", "Kafka"} <= set(result.matched_terms)
    assert result.fit_band != "insufficient_data"
    assert all("Kubernetes" not in gap for gap in result.gaps)


def test_general_disjunction_list_counts_as_one_requirement() -> None:
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description=(
                "Required: Go, Java, or Python, plus AWS and REST APIs. "
                "Operate reliable Kafka services. " * 12
            ),
            location="India",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME + " Python services.",
        evidence=EVIDENCE,
    )

    assert result.fit_band != "stretch"
    assert all("Go" not in gap and "Java" not in gap for gap in result.gaps)


def test_role_taxonomy_uses_boundaries_and_rejects_explicit_specializations() -> None:
    salesforce = assess_opportunity(
        posting=AssessmentPosting(
            title="Salesforce Software Engineer",
            description="Build Node.js services with AWS, REST APIs, and Kafka. " * 12,
            location="India",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )
    assert salesforce.fit_band != "low"

    for title in ("QA Automation Engineer", "Machine Learning Engineer"):
        specialized = assess_opportunity(
            posting=AssessmentPosting(
                title=title,
                description="Build Node.js services with AWS, REST APIs, and Kafka. " * 12,
                location="India",
                employment_type="full_time",
            ),
            target=TARGET,
            profile=PROFILE,
            resume_text=RESUME,
            evidence=EVIDENCE,
        )
        assert specialized.fit_band == "low"


def test_hard_conflict_is_not_hidden_behind_unknown_gaps() -> None:
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description="Required: 10+ years building Node.js AWS REST Kafka services. " * 10,
            location=None,
            employment_type=None,
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert result.eligibility == "likely_ineligible"
    assert any("at least 10 years" in gap for gap in result.gaps)


def test_blank_or_sparse_descriptions_remain_insufficient_data() -> None:
    for description in ("", "Node.js AWS REST"):
        result = assess_opportunity(
            posting=AssessmentPosting(
                title="Backend Engineer",
                description=description,
                location="India",
                employment_type="full_time",
            ),
            target=TARGET,
            profile=PROFILE,
            resume_text=RESUME,
            evidence=EVIDENCE,
        )
        assert result.fit_band == "insufficient_data"
        assert result.confidence == "low"


def test_explicit_frontend_and_product_roles_conflict_with_backend_target() -> None:
    for title in (
        "Frontend Software Development Engineer",
        "Product Manager, Developer APIs",
    ):
        result = assess_opportunity(
            posting=AssessmentPosting(
                title=title,
                description="Build Node.js AWS Kafka REST PostgreSQL services. " * 15,
                location="India",
                employment_type="full_time",
            ),
            target=TARGET,
            profile=PROFILE,
            resume_text=RESUME,
            evidence=EVIDENCE,
        )
        assert result.fit_band == "low"


def test_unusual_engineering_title_is_unknown_instead_of_rejected() -> None:
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Member of Technical Staff",
            description="Build Node.js AWS Kafka REST PostgreSQL services. " * 15,
            location="Bengaluru",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert result.fit_band != "low"
    assert result.eligibility == "eligible"


def test_company_age_is_not_mistaken_for_required_experience() -> None:
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description=(
                "We have served customers for 20 years across global markets. "
                "Required: 1+ years of experience building Node.js AWS REST services. " * 10
            ),
            location="India",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert result.eligibility == "eligible"
    assert all("20" not in gap for gap in result.gaps)


def test_ambiguous_ordinary_words_do_not_become_technical_skills() -> None:
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description=(
                "Express ideas clearly during Spring 2027 and help the rest of the team "
                "organize shipping containers. " * 8
            ),
            location="India",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=EVIDENCE,
    )

    assert result.matched_terms == ()
    assert result.fit_band == "insufficient_data"


def test_duplicate_aws_evidence_does_not_fake_broad_requirement_coverage() -> None:
    duplicate_evidence = (
        AssessmentEvidence("aws-one", "Used AWS in production.", ("AWS",)),
        AssessmentEvidence("aws-two", "Deployed another service on AWS.", ("AWS",)),
    )
    result = assess_opportunity(
        posting=AssessmentPosting(
            title="Backend Engineer",
            description="Build Node.js services using AWS and REST APIs. " * 15,
            location="India",
            employment_type="full_time",
        ),
        target=TARGET,
        profile=PROFILE,
        resume_text=RESUME,
        evidence=duplicate_evidence,
    )

    assert result.fit_band == "promising"
    assert result.approved_evidence_ids == ("aws-one",)
