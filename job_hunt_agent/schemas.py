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

from datetime import datetime
from typing import Literal
from uuid import uuid4

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

    draft_id: str = Field(
        default_factory=lambda: uuid4().hex,
        description=(
            "Stable id for this draft. Outcomes log against this id so the "
            "(run_id, draft_id) pair is the canonical join key."
        ),
    )
    role: Role = Field(description="Role this outreach draft is tied to.")
    person: Person = Field(description="Referral target this draft is addressed to.")
    message: str = Field(
        description=(
            "Concise referral-request message. Plain text, no markdown, no "
            "placeholder fields."
        ),
    )


class PastDraft(BaseModel):
    """A previously generated draft retrieved from Phoenix traces."""

    message: str = Field(description="Past outreach draft text.")
    role_title: str = Field(description="Role title the past draft targeted.")
    company: str = Field(description="Company the past draft targeted.")
    eval_score: float | None = Field(
        default=None,
        ge=1,
        le=5,
        description="Composite 1-5 evaluation score, if one has been written.",
    )
    matched_keywords: list[str] = Field(
        description="Query keywords that matched this draft's traced role keywords.",
    )
    span_id: str = Field(description="Phoenix/OpenTelemetry span ID for the draft.")
    trace_id: str = Field(description="Phoenix/OpenTelemetry trace ID for the draft.")


class HuntResult(BaseModel):
    """Structured output from the end-to-end job-hunt runner."""

    run_id: str = Field(
        description=(
            "Stable id for this hunt. Ties the result to its Phoenix trace and "
            "to any outcomes logged later. Generated by run_hunt() if not "
            "supplied by the caller."
        ),
    )
    roles: list[Role] = Field(description="Roles found for the supplied criteria.")
    outreach: list[OutreachDraft] = Field(
        description="Drafted outreach messages grouped by role/person pair.",
    )


class OutcomeLog(BaseModel):
    """User-logged result for one drafted message."""

    draft_id: str = Field(
        description=(
            "OutreachDraft.draft_id this outcome belongs to. The owning run_id "
            "is supplied by the request envelope, not the log entry."
        ),
    )
    outcome: Literal["replied", "no_reply", "introduced", "rejected", "pending"] = Field(
        description="Latest user-reported status for this draft.",
    )
    notes: str | None = Field(
        default=None,
        description="Optional free-text note about the outcome.",
    )
    logged_at: datetime | None = Field(
        default=None,
        description=(
            "When the outcome was recorded. Server overwrites this on insert "
            "with a timezone-aware UTC timestamp."
        ),
    )
