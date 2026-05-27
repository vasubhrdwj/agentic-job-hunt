"""Data contracts shared across the job-hunt agent.

Every tool in the pipeline consumes and returns one of these models:

    JobCriteria  -> search_jobs()       -> list[Role]
    Role         -> find_referrals()    -> list[Person]
    Role, Person -> draft_message()     -> str
    run_hunt()   -> HuntResult

Pydantic v2 is used so we get JSON-schema generation for free
(the Google ADK uses these schemas to teach Gemini how to call the tools,
and field descriptions become part of that prompt).
"""

from typing import Literal

from pydantic import BaseModel, Field


class JobCriteria(BaseModel):
    """User-supplied filters that constrain which jobs we go fetch."""

    role_keywords: list[str] = Field(
        description=(
            "Keywords that must appear in the job title or body. "
            "Example: ['SCIM', 'identity', 'IAM']."
        ),
    )
    seniority: Literal["junior", "mid", "senior", "staff"] = Field(
        description="Seniority band the user is targeting.",
    )
    location: list[str] = Field(
        description=(
            "Acceptable locations. Free-form strings matched against the listing "
            "site's conventions. Example: ['Hyderabad', 'Remote-India']."
        ),
    )
    comp_min_lpa: int | None = Field(
        default=None,
        description="Minimum acceptable comp in lakhs per annum (INR). Optional.",
    )
    comp_max_lpa: int | None = Field(
        default=None,
        description="Upper bound for comp in lakhs per annum (INR). Optional.",
    )


class Role(BaseModel):
    """A single open job posting that matched the user's criteria."""

    company: str = Field(description="Name of the hiring company.")
    title: str = Field(
        description="Job title exactly as it appears on the posting.",
    )
    url: str = Field(
        description=(
            "Canonical URL to the job posting. Must resolve to a real page — "
            "never fabricated by the model."
        ),
    )
    location: str = Field(
        description="Location string from the posting (city, region, or 'Remote').",
    )
    summary: str = Field(
        description=(
            "2-3 sentence summary of the role, extracted from the posting. "
            "Plain text, no markdown."
        ),
    )
    match_reason: str = Field(
        description=(
            "Specific justification for why this role matches the criteria. "
            "Should reference concrete details from the listing, not just "
            "echo the keywords back."
        ),
    )


class Person(BaseModel):
    """A plausible referral target at the hiring company."""

    name: str = Field(
        description="Person's full name as it appears on their public profile.",
    )
    title: str = Field(description="Current job title at the target company.")
    company: str = Field(
        description=(
            "Company the person currently works at. Should match Role.company "
            "for a valid referral candidate."
        ),
    )
    profile_url: str = Field(
        description=(
            "URL to a public profile (LinkedIn, GitHub, or company page). "
            "Must come from a real tool result — never fabricated by the model."
        ),
    )
    source: Literal["linkedin", "github", "company_page", "other"] = Field(
        description="Where the profile URL came from.",
    )
    why_relevant: str = Field(
        description=(
            "One sentence on why this person is a good referral target for this "
            "specific role. Should reference something concrete about the role "
            "or the person's title — not generic praise."
        ),
    )


class OutreachDraft(BaseModel):
    """One drafted outreach message for one role/person pair."""

    role: Role = Field(description="Role this outreach draft is tied to.")
    person: Person = Field(description="Referral target this draft is addressed to.")
    message: str = Field(
        description=(
            "Concise referral-request message. Plain text, no markdown, no "
            "placeholder fields."
        ),
    )


class HuntResult(BaseModel):
    """Structured output from the end-to-end job-hunt runner."""

    roles: list[Role] = Field(description="Roles found for the supplied criteria.")
    outreach: list[OutreachDraft] = Field(
        description="Drafted outreach messages grouped by role/person pair.",
    )
