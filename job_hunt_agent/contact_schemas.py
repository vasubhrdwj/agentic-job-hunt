"""Strict, provider-free contracts for an application's source-backed contact bench.

The read model deliberately separates the latest search attempt from the last
completed result.  A queued retry can therefore be shown without hiding a
previously useful bench, while an application with no plan has an explicit
``not_started`` state instead of a fabricated empty search.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    model_validator,
)

from .application_schemas import HttpsUrl, OpaqueId, UTCDateTime


TARGET_CONTACT_COUNT = 5
MAX_CONTACT_CANDIDATES = 12
MIN_VERIFIED_CONFIDENCE = 0.75
MAX_SHORTFALL_REASONS = 12
MAX_SCORE_COMPONENTS = 32

ShortfallReasonCode = Annotated[
    str,
    Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]
ScoreComponentName = Annotated[str, Field(min_length=1, max_length=100)]
ScoreComponentValue = StrictInt | StrictFloat


class ContactContractModel(BaseModel):
    """Reject silently ignored fields and trim public text."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ContactSearchStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ContactCoverageStatus(str, Enum):
    pending = "pending"
    met = "met"
    partial = "partial"


class ContactBenchStatus(str, Enum):
    not_started = "not_started"
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ContactBenchCoverage(str, Enum):
    not_started = "not_started"
    pending = "pending"
    met = "met"
    partial = "partial"


class ContactCategory(str, Enum):
    warm_path = "warm_path"
    team_peer = "team_peer"
    adjacent_peer = "adjacent_peer"
    team_leader = "team_leader"
    recruiter = "recruiter"
    other = "other"


class ContactProfileSource(str, Enum):
    linkedin = "linkedin"
    github = "github"
    company_page = "company_page"
    other = "other"


class ContactLifecycle(str, Enum):
    active = "active"
    do_not_contact = "do_not_contact"
    retired = "retired"


class ContactEvidenceStatus(str, Enum):
    verified = "verified"
    inferred = "inferred"
    unknown = "unknown"


class ContactBenchState(str, Enum):
    ready = "ready"
    reserve = "reserve"
    paused = "paused"
    stopped = "stopped"


class ContactShortfallReason(ContactContractModel):
    """One honest, countable explanation for returning fewer than five people."""

    code: ShortfallReasonCode
    count: StrictInt = Field(ge=1, le=MAX_CONTACT_CANDIDATES)
    detail: str = Field(min_length=1, max_length=1_000)


class EmployerEvidenceResponse(ContactContractModel):
    excerpt: str = Field(
        min_length=1,
        max_length=1_000,
        description="Exact public search-result snippet saved for this lead.",
    )
    url: HttpsUrl = Field(description="Public source result supporting the saved snippet.")
    source: str = Field(
        min_length=1,
        max_length=64,
        description="Public-search provider that returned the result.",
    )
    observed_at: UTCDateTime = Field(
        description="When the source result was captured, not a profile-verification time."
    )


class RelevanceEvidenceResponse(ContactContractModel):
    status: ContactEvidenceStatus
    summary: str | None = Field(default=None, min_length=1, max_length=1_000)
    url: HttpsUrl | None = None

    @model_validator(mode="after")
    def verified_claim_has_public_evidence(self) -> Self:
        if self.status is ContactEvidenceStatus.verified and (
            self.summary is None or self.url is None
        ):
            raise ValueError("verified relevance requires a summary and evidence URL")
        return self


class ContactBenchItem(ContactContractModel):
    """One selected person with the evidence snapshot used for this role."""

    id: OpaqueId
    contact_id: OpaqueId
    version: int = Field(ge=1)
    public_name: str = Field(min_length=1, max_length=200)
    profile_url: HttpsUrl
    profile_source: ContactProfileSource
    lifecycle: ContactLifecycle
    current_title: str = Field(
        min_length=1,
        max_length=300,
        description="Title parsed from the saved source result; not independently verified.",
    )
    current_company: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "Employer indicated by the saved source result; not independently verified."
        ),
    )
    category: ContactCategory
    confidence: float = Field(
        ge=MIN_VERIFIED_CONFIDENCE,
        le=1.0,
        description=(
            "Legacy numeric heuristic for saved public-source evidence; it is not a "
            "calibrated probability or independent verification score."
        ),
    )
    verified_at: UTCDateTime = Field(
        description=(
            "When source evidence passed the configured threshold; not an independent "
            "profile or employment verification timestamp."
        )
    )
    employer_evidence: EmployerEvidenceResponse
    why_relevant: str = Field(min_length=1, max_length=2_000)
    relationship: RelevanceEvidenceResponse
    team_proximity: RelevanceEvidenceResponse
    score_total: int = Field(ge=0, le=1_000)
    score_components: dict[ScoreComponentName, ScoreComponentValue] = Field(
        default_factory=dict,
        max_length=MAX_SCORE_COMPONENTS,
    )
    scoring_version: str = Field(min_length=1, max_length=64)
    bench_rank: int = Field(ge=1, le=TARGET_CONTACT_COUNT)
    wave: int = Field(ge=1, le=TARGET_CONTACT_COUNT)
    bench_state: ContactBenchState
    cooldown_until: UTCDateTime | None = None
    unlocked_at: UTCDateTime | None = None

    @model_validator(mode="after")
    def selected_contact_is_safe_and_ranked(self) -> Self:
        for value in self.score_components.values():
            if isinstance(value, bool) or not math.isfinite(float(value)):
                raise ValueError("score components must be finite numbers")
            if value < 0 or value > 1_000:
                raise ValueError("score components must be between 0 and 1000")
        if self.bench_state is ContactBenchState.ready and self.unlocked_at is None:
            raise ValueError("a ready contact requires an unlock timestamp")
        if (
            self.bench_state is ContactBenchState.ready
            and self.lifecycle is not ContactLifecycle.active
        ):
            raise ValueError("only active contacts can be ready")
        if self.bench_state is ContactBenchState.reserve and self.unlocked_at is not None:
            raise ValueError("a reserve contact cannot be unlocked")
        return self


class ContactSearchSnapshot(ContactContractModel):
    """The newest persisted search attempt, including terminal failures."""

    id: OpaqueId
    version: int = Field(ge=1)
    plan_number: int = Field(ge=1)
    status: ContactSearchStatus
    job_stage: str | None = Field(default=None, min_length=1, max_length=100)
    target_count: Literal[5] = TARGET_CONTACT_COUNT
    candidate_limit: int = Field(ge=TARGET_CONTACT_COUNT, le=MAX_CONTACT_CANDIDATES)
    confidence_floor: float = Field(
        ge=MIN_VERIFIED_CONFIDENCE,
        le=1.0,
        description="Minimum legacy source-evidence heuristic required for selection.",
    )
    discovered_count: int = Field(ge=0, le=MAX_CONTACT_CANDIDATES)
    evidence_verified_count: int = Field(
        ge=0,
        le=MAX_CONTACT_CANDIDATES,
        description=(
            "Results meeting the saved source-evidence threshold; not independently "
            "verified profiles."
        ),
    )
    selected_count: int = Field(ge=0, le=TARGET_CONTACT_COUNT)
    coverage_status: ContactCoverageStatus
    exhausted: bool
    retryable: bool
    shortfall_reasons: list[ContactShortfallReason] = Field(
        default_factory=list,
        max_length=MAX_SHORTFALL_REASONS,
    )
    error_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    started_at: UTCDateTime | None = None
    finalized_at: UTCDateTime | None = None
    created_at: UTCDateTime
    updated_at: UTCDateTime

    @model_validator(mode="after")
    def lifecycle_and_counts_are_consistent(self) -> Self:
        reason_codes = [reason.code for reason in self.shortfall_reasons]
        if len(reason_codes) != len(set(reason_codes)):
            raise ValueError("shortfall reasons must be distinct")
        if not (
            self.selected_count
            <= self.evidence_verified_count
            <= self.discovered_count
            <= self.candidate_limit
        ):
            raise ValueError("contact search counts must be ordered")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at cannot precede created_at")
        if self.finalized_at is not None and self.finalized_at < self.created_at:
            raise ValueError("finalized_at cannot precede created_at")

        if self.status is ContactSearchStatus.queued:
            if self.started_at is not None or self.finalized_at is not None:
                raise ValueError("queued searches cannot be started or finalized")
        elif self.status is ContactSearchStatus.running:
            if self.started_at is None or self.finalized_at is not None:
                raise ValueError("running searches require only started_at")
        elif self.finalized_at is None:
            raise ValueError("terminal searches require finalized_at")

        if self.status is ContactSearchStatus.completed:
            if self.coverage_status is ContactCoverageStatus.pending:
                raise ValueError("completed searches require final coverage")
        elif self.coverage_status is not ContactCoverageStatus.pending:
            raise ValueError("unfinished searches require pending coverage")

        if self.coverage_status is ContactCoverageStatus.met:
            if self.selected_count != TARGET_CONTACT_COUNT:
                raise ValueError("met coverage requires five selected contacts")
            if self.shortfall_reasons:
                raise ValueError("met coverage cannot contain shortfall reasons")
        elif self.coverage_status is ContactCoverageStatus.partial:
            if self.selected_count >= TARGET_CONTACT_COUNT:
                raise ValueError("partial coverage must contain fewer than five contacts")
            if not self.shortfall_reasons:
                raise ValueError("partial coverage requires an honest shortfall reason")
        elif self.shortfall_reasons:
            raise ValueError("pending coverage cannot contain final shortfall reasons")

        if self.exhausted and self.status is not ContactSearchStatus.completed:
            raise ValueError("only completed searches can be exhausted")
        if self.status is ContactSearchStatus.failed:
            if self.error_code is None:
                raise ValueError("failed searches require an error code")
        elif self.error_code is not None:
            raise ValueError("only failed searches can contain an error code")
        return self


class ContactBenchResult(ContactContractModel):
    """The last successfully completed, evidence-backed selection."""

    contact_plan_id: OpaqueId
    plan_number: int = Field(ge=1)
    target_count: Literal[5] = TARGET_CONTACT_COUNT
    verified_count: int = Field(
        ge=0,
        le=TARGET_CONTACT_COUNT,
        description=(
            "Selected source-backed leads. The legacy field name does not imply "
            "independent profile or employment verification."
        ),
    )
    coverage_status: Literal[
        ContactCoverageStatus.met,
        ContactCoverageStatus.partial,
    ]
    exhausted: bool
    shortfall_reasons: list[ContactShortfallReason] = Field(
        default_factory=list,
        max_length=MAX_SHORTFALL_REASONS,
    )
    contacts: list[ContactBenchItem] = Field(
        default_factory=list,
        max_length=TARGET_CONTACT_COUNT,
    )
    completed_at: UTCDateTime

    @model_validator(mode="after")
    def result_is_complete_without_padding(self) -> Self:
        reason_codes = [reason.code for reason in self.shortfall_reasons]
        if len(reason_codes) != len(set(reason_codes)):
            raise ValueError("shortfall reasons must be distinct")
        if self.verified_count != len(self.contacts):
            raise ValueError("verified_count must equal the returned contact count")
        ranks = [contact.bench_rank for contact in self.contacts]
        if ranks != list(range(1, len(self.contacts) + 1)):
            raise ValueError("bench ranks must be consecutive and ordered")
        contact_ids = [contact.contact_id for contact in self.contacts]
        profile_urls = [str(contact.profile_url) for contact in self.contacts]
        if len(contact_ids) != len(set(contact_ids)):
            raise ValueError("contact results must contain distinct people")
        if len(profile_urls) != len(set(profile_urls)):
            raise ValueError("contact results must contain distinct profile URLs")

        if self.coverage_status is ContactCoverageStatus.met:
            if self.verified_count != TARGET_CONTACT_COUNT:
                raise ValueError("met coverage requires exactly five contacts")
            if self.shortfall_reasons:
                raise ValueError("met coverage cannot contain shortfall reasons")
        else:
            if self.verified_count >= TARGET_CONTACT_COUNT:
                raise ValueError("partial coverage must contain fewer than five contacts")
            if not self.shortfall_reasons:
                raise ValueError("partial coverage requires an honest shortfall reason")
        return self


class ApplicationContactBenchResponse(ContactContractModel):
    """Database-only contact state for one owner-scoped application."""

    data_source: Literal["database"] = "database"
    application_id: OpaqueId
    status: ContactBenchStatus
    target_count: Literal[5] = TARGET_CONTACT_COUNT
    verified_count: int = Field(
        ge=0,
        le=TARGET_CONTACT_COUNT,
        description=(
            "Source-backed leads in the last completed result; not independently "
            "verified profiles."
        ),
    )
    coverage_status: ContactBenchCoverage
    current_search: ContactSearchSnapshot | None = None
    last_completed_result: ContactBenchResult | None = None

    @model_validator(mode="after")
    def state_describes_current_and_last_good_results(self) -> Self:
        if self.status is ContactBenchStatus.not_started:
            if self.current_search is not None or self.last_completed_result is not None:
                raise ValueError("not_started cannot contain a search or result")
            if (
                self.verified_count != 0
                or self.coverage_status is not ContactBenchCoverage.not_started
            ):
                raise ValueError("not_started must report zero source-backed leads")
            return self

        if self.current_search is None:
            raise ValueError("a started contact bench requires current_search")
        if self.status.value != self.current_search.status.value:
            raise ValueError("contact bench status must match current_search")

        result = self.last_completed_result
        if result is None:
            if self.verified_count != 0 or self.coverage_status is not ContactBenchCoverage.pending:
                raise ValueError("a bench without a completed result must remain pending")
        else:
            if result.plan_number > self.current_search.plan_number:
                raise ValueError("the completed result cannot follow the current search")
            if self.verified_count != result.verified_count:
                raise ValueError("verified_count must match the last completed result")
            if self.coverage_status.value != result.coverage_status.value:
                raise ValueError("coverage must match the last completed result")

        if self.current_search.status is ContactSearchStatus.completed:
            if result is None or result.contact_plan_id != self.current_search.id:
                raise ValueError("a completed current search must be the returned result")
        return self


__all__ = [
    "ApplicationContactBenchResponse",
    "ContactBenchCoverage",
    "ContactBenchItem",
    "ContactBenchResult",
    "ContactBenchState",
    "ContactBenchStatus",
    "ContactCategory",
    "ContactCoverageStatus",
    "ContactEvidenceStatus",
    "ContactLifecycle",
    "ContactProfileSource",
    "ContactSearchSnapshot",
    "ContactSearchStatus",
    "ContactShortfallReason",
    "EmployerEvidenceResponse",
    "MIN_VERIFIED_CONFIDENCE",
    "RelevanceEvidenceResponse",
    "TARGET_CONTACT_COUNT",
]
