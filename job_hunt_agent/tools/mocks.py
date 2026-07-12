"""Schema-backed mock tools for the V2 agent loop."""

from __future__ import annotations

import re
from typing import Any

from ..schemas import JobCriteria, Person, Role


def _validate_criteria(criteria: JobCriteria | dict[str, Any]) -> JobCriteria:
    return JobCriteria.model_validate(criteria)


def _validate_role(role: Role | dict[str, Any]) -> Role:
    return Role.model_validate(role)


def _validate_person(person: Person | dict[str, Any]) -> Person:
    return Person.model_validate(person)


def search_jobs_mock(criteria: JobCriteria) -> list[dict[str, Any]]:
    """Return three canned roles that match the supplied job criteria."""
    criteria = _validate_criteria(criteria)
    keywords = ", ".join(criteria.role_keywords) or "identity"
    locations = ", ".join(criteria.location) or "Remote"

    roles = [
        Role(
            company="Okta",
            title="Senior Software Engineer, Lifecycle Management",
            url="https://example.com/mock/jobs/okta-lifecycle-management",
            location="Remote-India",
            summary=(
                "Build provisioning and lifecycle workflows for enterprise identity "
                "customers. The role touches SCIM integrations, directory sync, and "
                "large-scale account automation."
            ),
            match_reason=(
                f"Matches {keywords} because the role centers on identity lifecycle "
                f"automation and accepts locations compatible with {locations}."
            ),
            company_slug="okta",
            source_job_id="mock-okta-lifecycle-management",
        ),
        Role(
            company="Saviynt",
            title="Backend Engineer, Identity Governance",
            url="https://example.com/mock/jobs/saviynt-identity-governance",
            location="Hyderabad",
            summary=(
                "Work on identity governance APIs, access workflows, and integrations "
                "used by security teams. The role emphasizes backend reliability and "
                "enterprise identity domain knowledge."
            ),
            match_reason=(
                f"Matches {keywords} through IAM-adjacent backend work and a Hyderabad "
                "location fit."
            ),
            company_slug="saviynt",
            source_job_id="mock-saviynt-identity-governance",
        ),
        Role(
            company="JumpCloud",
            title="Software Engineer, Directory Platform",
            url="https://example.com/mock/jobs/jumpcloud-directory-platform",
            location="Remote",
            summary=(
                "Build directory platform services for device, user, and access "
                "management. The team owns identity data flows used across customer "
                "integrations."
            ),
            match_reason=(
                f"Matches {keywords} because the work is centered on directory and "
                "identity platform services."
            ),
            company_slug="jumpcloud",
            source_job_id="mock-jumpcloud-directory-platform",
        ),
    ]
    return [role.model_dump() for role in roles]


def find_referrals_mock(role: Role) -> list[dict[str, Any]]:
    """Return five diverse canned referral targets for a role."""
    role = _validate_role(role)
    people_by_company = {
        "Okta": [
            Person(
                name="Anika Rao",
                title="Staff Engineer, Lifecycle Management",
                company="Okta",
                profile_url="https://example.com/mock/profiles/anika-rao",
                source="other",
                why_relevant=(
                    "Works on lifecycle management, which directly overlaps with "
                    "the role's provisioning and SCIM focus."
                ),
            ),
            Person(
                name="Rahul Mehta",
                title="Engineering Manager, Identity Platform",
                company="Okta",
                profile_url="https://example.com/mock/profiles/rahul-mehta",
                source="other",
                why_relevant=(
                    "Leads identity platform work and would understand the team fit "
                    "for this backend role."
                ),
            ),
            Person(
                name="Meera Iyer",
                title="Senior Product Engineer, Integrations",
                company="Okta",
                profile_url="https://example.com/mock/profiles/meera-iyer",
                source="other",
                why_relevant=(
                    "Works near enterprise integrations, a key part of the role's "
                    "lifecycle automation surface."
                ),
            ),
            Person(
                name="Vikram Joshi",
                title="Senior Engineer, Directory Sync",
                company="Okta",
                profile_url="https://example.com/mock/profiles/vikram-joshi",
                source="other",
                why_relevant=(
                    "Builds directory synchronization systems adjacent to the "
                    "role's provisioning scope."
                ),
            ),
            Person(
                name="Sanya Kulkarni",
                title="Technical Recruiter, Engineering",
                company="Okta",
                profile_url="https://example.com/mock/profiles/sanya-kulkarni",
                source="other",
                why_relevant=(
                    "Recruits for engineering and can route interest to the "
                    "appropriate lifecycle-management team."
                ),
            ),
        ],
        "Saviynt": [
            Person(
                name="Karan Shah",
                title="Principal Engineer, Identity Governance",
                company="Saviynt",
                profile_url="https://example.com/mock/profiles/karan-shah",
                source="other",
                why_relevant=(
                    "Owns identity governance architecture, which maps closely to "
                    "the role's access workflow work."
                ),
            ),
            Person(
                name="Nisha Menon",
                title="Engineering Manager, Backend Platform",
                company="Saviynt",
                profile_url="https://example.com/mock/profiles/nisha-menon",
                source="other",
                why_relevant=(
                    "Manages backend platform engineers and can judge fit for the "
                    "role's reliability-heavy API work."
                ),
            ),
            Person(
                name="Arjun Nair",
                title="Senior Software Engineer, Access Workflows",
                company="Saviynt",
                profile_url="https://example.com/mock/profiles/arjun-nair",
                source="other",
                why_relevant=(
                    "Builds access workflow systems similar to the work described "
                    "in the posting."
                ),
            ),
            Person(
                name="Deepak Rao",
                title="Staff Engineer, Backend Platform",
                company="Saviynt",
                profile_url="https://example.com/mock/profiles/deepak-rao",
                source="other",
                why_relevant=(
                    "Works on the backend platform supporting identity-governance "
                    "services."
                ),
            ),
            Person(
                name="Kavya Bhat",
                title="Technical Recruiter, Product Engineering",
                company="Saviynt",
                profile_url="https://example.com/mock/profiles/kavya-bhat",
                source="other",
                why_relevant=(
                    "Recruits product engineers and can confirm the relevant hiring "
                    "team and process."
                ),
            ),
        ],
        "JumpCloud": [
            Person(
                name="Priya Das",
                title="Staff Engineer, Directory Platform",
                company="JumpCloud",
                profile_url="https://example.com/mock/profiles/priya-das",
                source="other",
                why_relevant=(
                    "Works on directory platform services, directly aligned with "
                    "the target role."
                ),
            ),
            Person(
                name="Dev Patel",
                title="Engineering Manager, Identity Data",
                company="JumpCloud",
                profile_url="https://example.com/mock/profiles/dev-patel",
                source="other",
                why_relevant=(
                    "Leads identity data systems and could speak to team scope and "
                    "technical expectations."
                ),
            ),
            Person(
                name="Sara Thomas",
                title="Senior Engineer, Customer Integrations",
                company="JumpCloud",
                profile_url="https://example.com/mock/profiles/sara-thomas",
                source="other",
                why_relevant=(
                    "Works on customer integrations that depend on reliable "
                    "directory data flows."
                ),
            ),
            Person(
                name="Arvind Krishnan",
                title="Senior Engineer, Device Identity",
                company="JumpCloud",
                profile_url="https://example.com/mock/profiles/arvind-krishnan",
                source="other",
                why_relevant=(
                    "Builds identity services that consume the directory platform's "
                    "core data flows."
                ),
            ),
            Person(
                name="Isha Kapoor",
                title="Technical Recruiter, Platform Engineering",
                company="JumpCloud",
                profile_url="https://example.com/mock/profiles/isha-kapoor",
                source="other",
                why_relevant=(
                    "Recruits platform engineers and can route questions about the "
                    "directory-platform opening."
                ),
            ),
        ],
    }

    people = people_by_company.get(role.company, _fallback_people_for_role(role))
    return [
        person.model_copy(
            update={
                "verified_current_employer": True,
                "confidence": 1.0,
            },
        ).model_dump()
        for person in people[:5]
    ]


def _fallback_people_for_role(role: Role) -> list[Person]:
    """Return mock referral targets for real search results during V3 hybrid runs."""
    company_slug = _mock_slug(role.company)
    title_scope = role.title.split(",")[0]
    return [
        Person(
            name="Asha Verma",
            title=f"Engineering Manager, {title_scope}",
            company=role.company,
            profile_url=f"https://example.com/mock/profiles/{company_slug}-asha-verma",
            source="other",
            why_relevant=(
                f"Mock contact for {role.company}; the title is close to the "
                f"{role.title} role's likely engineering team."
            ),
        ),
        Person(
            name="Rohan Kapoor",
            title=f"Senior Engineer, {title_scope}",
            company=role.company,
            profile_url=f"https://example.com/mock/profiles/{company_slug}-rohan-kapoor",
            source="other",
            why_relevant=(
                f"Mock contact for {role.company}; senior engineering work is "
                "likely adjacent to the role's implementation scope."
            ),
        ),
        Person(
            name="Neha Srinivasan",
            title="Technical Recruiter",
            company=role.company,
            profile_url=f"https://example.com/mock/profiles/{company_slug}-neha-srinivasan",
            source="other",
            why_relevant=(
                f"Mock contact for {role.company}; recruiting can route the "
                f"candidate to the {role.title} hiring team."
            ),
        ),
        Person(
            name="Kabir Malhotra",
            title=f"Staff Engineer, {title_scope}",
            company=role.company,
            profile_url=f"https://example.com/mock/profiles/{company_slug}-kabir-malhotra",
            source="other",
            why_relevant=(
                f"Mock contact for {role.company}; staff-level work is likely "
                f"adjacent to the {role.title} role's technical scope."
            ),
        ),
        Person(
            name="Tara Anand",
            title="Talent Sourcer, Engineering",
            company=role.company,
            profile_url=f"https://example.com/mock/profiles/{company_slug}-tara-anand",
            source="other",
            why_relevant=(
                f"Mock recruiting contact for {role.company}; can route interest "
                f"to the hiring team for {role.title}."
            ),
        ),
    ]


def _mock_slug(value: str) -> str:
    slug = "".join(
        character.lower() if character.isalnum() else "-"
        for character in value
    )
    return "-".join(part for part in slug.split("-") if part) or "unknown-company"


def draft_message_mock(
    role: Role,
    person: Person,
    resume_text: str = "",
    **_kwargs: object,
) -> str:
    """Draft a concise mock outreach message for one role/person pair."""
    role = _validate_role(role)
    person = _validate_person(person)
    resume_signal = "my backend and identity systems experience"
    if "SCIM" in resume_text.upper():
        resume_signal = "my recent SCIM and identity automation work"
    use_self_rag = bool(_kwargs.get("use_self_rag", True))
    if use_self_rag:
        team_name = role.title.split(",")[0].strip()
        return (
            f"Hi {person.name.split()[0]}, I saw the {team_name} team is hiring "
            f"for {role.title} at {role.company}. My SCIM 2.0 RFC 7644 and "
            f"backend automation work maps directly to the role's scope. "
            "Would you be open to a 15 min pointer on Tuesday or Wednesday?\n"
            "Thanks,"
        )

    return (
        f"Hi {person.name.split()[0]}, I saw {role.company}'s {role.title} role and "
        f"your work as {person.title} caught my eye because it sits close to "
        "this team's scope. "
        f"My background lines up through {resume_signal}. "
        "Would you be open to a quick pointer on whether this team is the right "
        "place to apply?"
    )


def score_draft_mock(
    role: Role,
    person: Person,
    message: str,
    **_kwargs: object,
) -> "EvalResult":
    """Deterministic offline judge mirroring the SEED_NOTES rubric.

    Detects the same three signals the seeded corpus rewards (concrete
    technical detail, named team, specific next step) so mock pipelines
    produce plausible, varied scores without a network call.
    """
    from ..evals import EvalResult

    _validate_role(role)
    _validate_person(person)
    text = message or ""

    tech_signal = bool(
        re.search(
            r"\b(?:RFC ?\d+|SCIM|OIDC|OAuth|SAML|Next\.js|RAG|Kubernetes|gRPC)\b",
            text,
            re.IGNORECASE,
        )
    )
    team_signal = bool(re.search(r"\b[A-Z][\w-]*(?: [A-Z][\w-]*)* team\b", text))
    ask_signal = "?" in text and bool(
        re.search(r"\b(?:\d+ ?min(?:ute)?s?|tue|wed|thu|next week|pointer)\b", text, re.IGNORECASE)
    )
    hype_signal = bool(re.search(r"!!|🚀|game.changer|crushing it|\[[^\]]+\]", text))

    personalization = 5 if team_signal else 2
    specificity = 5 if tech_signal else 2
    ask = 5 if ask_signal else 2
    tone = 2 if hype_signal else 4
    composite = round((personalization + specificity + ask + tone) / 4, 2)
    return EvalResult(
        personalization=personalization,
        specificity=specificity,
        ask=ask,
        tone=tone,
        composite=composite,
        rationale="Mock judge: deterministic rubric over seed-pattern signals.",
    )
