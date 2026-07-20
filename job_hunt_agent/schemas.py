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
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class CompanySource(str, Enum):
    """Supported strategies for discovering a company's open roles."""

    greenhouse = "greenhouse"
    lever = "lever"
    ashby = "ashby"
    workday = "workday"
    smartrecruiters = "smartrecruiters"
    workable = "workable"
    bespoke = "bespoke"
    google_jobs = "google_jobs"
    scrape = "scrape"


class Company(BaseModel):
    """Registry entry describing how to discover one company's open roles."""

    name: str = Field(description="Display name of the company.")
    slug: str = Field(description="Stable company identifier, for example 'razorpay'.")
    source: CompanySource = Field(description="Strategy used to reach the careers board.")
    source_token: str | None = Field(
        description=(
            "Platform-specific board token, site name, tenant, or equivalent. "
            "None when the selected source does not need one."
        ),
    )
    careers_domains: list[str] = Field(
        default_factory=list,
        description="Domains accepted as first-party apply URLs for this company.",
    )
    hire_locations: list[str] = Field(
        default_factory=list,
        description="Locations where this company is known to hire.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Registry-pack tags such as 'backend' or 'fintech'.",
    )
    active: bool = Field(
        default=True,
        description="Whether this registry entry should participate in searches.",
    )


class EmploymentType(str, Enum):
    """Normalized employment types exposed by source adapters."""

    full_time = "full_time"
    contract = "contract"
    intern = "intern"
    unknown = "unknown"


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
    employment_types: list[EmploymentType] = Field(
        default_factory=lambda: [EmploymentType.full_time],
        description="Employment types accepted by the search.",
    )
    max_age_days: int | None = Field(
        default=45,
        description="Maximum listing age in days, or None to disable age filtering.",
    )
    country: str = Field(
        default="in",
        description="Lowercase country code used to scope source queries.",
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
    source: CompanySource = Field(
        default=CompanySource.google_jobs,
        description="Source strategy that produced this role.",
    )
    company_slug: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        description=(
            "Stable company-registry slug used with source_job_id to identify "
            "the native posting. None for legacy or unregistered results."
        ),
    )
    source_job_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description=(
            "Source-native posting identifier. Stable identity is the tuple "
            "(source, company_slug, source_job_id); None for legacy results."
        ),
    )
    apply_urls: list[str] = Field(
        default_factory=list,
        description="All known apply links, with first-party links first.",
    )
    posted_at: str | None = Field(
        default=None,
        description="Source-provided posting timestamp or date.",
    )
    source_updated_at: str | None = Field(
        default=None,
        description=(
            "Source-provided last-updated timestamp. This is not presented as "
            "the original posting date."
        ),
    )
    employment_type: EmploymentType = Field(
        default=EmploymentType.unknown,
        description="Normalized employment type reported by the source.",
    )
    raw_description: str | None = Field(
        default=None,
        description="Full job-description text used by downstream fit scoring.",
    )
    fit_score: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Resume-fit score from 0 to 1, when available.",
    )
    confidence: float = Field(
        default=1.0,
        ge=0,
        le=1,
        description="Source-quality confidence from 0 to 1.",
    )

    @model_validator(mode="after")
    def native_identity_is_complete(self) -> "Role":
        """Reject ambiguous half-identities while accepting every legacy role."""

        if (self.company_slug is None) != (self.source_job_id is None):
            raise ValueError(
                "company_slug and source_job_id must either both be set or both be null"
            )
        if self.company_slug is not None and self.company_slug != self.company_slug.strip():
            raise ValueError("company_slug must not have surrounding whitespace")
        if self.source_job_id is not None and self.source_job_id != self.source_job_id.strip():
            raise ValueError("source_job_id must not have surrounding whitespace")
        return self


class Person(BaseModel):
    """A plausible referral lead derived from saved public-source evidence."""

    name: str = Field(
        description="Person name parsed from the saved public-source result.",
    )
    title: str = Field(
        description=(
            "Job title indicated by the saved public-source evidence; not "
            "independently verified as current."
        )
    )
    company: str = Field(
        description=(
            "Company named as the person's current employer by the saved public-source "
            "evidence. Should match Role.company for a usable referral lead; this field "
            "is not independent employment verification."
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
            "One source-qualified sentence on why this result may be a useful lead "
            "for the role. It must not turn a search snippet into an independently "
            "verified title or employer claim."
        ),
    )
    verified_current_employer: bool = Field(
        default=False,
        description=(
            "True only when the saved public search result contains a current-employer "
            "signal for this company. It means source evidence supports the claim, not "
            "that the profile or employment was independently verified."
        ),
    )
    confidence: float = Field(
        default=0.0,
        ge=0,
        le=1,
        description=(
            "Legacy source-evidence heuristic from 0 to 1; not a calibrated "
            "probability or independent verification score."
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
    eval_score: float | None = Field(
        default=None,
        ge=1,
        le=5,
        description=(
            "Composite 1-5 LLM-judge score written by run_hunt() (V9). None "
            "when the judge was unavailable; never blocks the pipeline."
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
    outcome: Literal["replied", "no_reply", "introduced", "rejected", "pending"] | None = Field(
        default=None,
        description="Best logged real-world outcome for this exact draft text.",
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
