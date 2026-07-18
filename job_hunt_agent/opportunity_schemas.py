"""Provider-free transport contracts for the durable opportunity radar.

These models describe manual scan progress, the database-only Today inbox,
opportunity review details, and the first reversible owner decisions.  They do
not start source fetches, invoke a model, or define persistence behavior.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from .application_schemas import PursuitBundle
from .schemas import CompanySource


MAX_CURSOR_CHARS = 512
MAX_DECISION_NOTE_CHARS = 500
MAX_DESCRIPTION_CHARS = 100_000
MAX_PAGE_SIZE = 50


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a UTC offset")
    return value.astimezone(timezone.utc)


def _https_url(value: str) -> str:
    if any(character.isspace() for character in value) or "\\" in value:
        raise ValueError("URL must be a valid HTTPS URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL must be a valid HTTPS URL") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
    ):
        raise ValueError("URL must be a valid HTTPS URL without credentials or fragments")
    return value


UTCDateTime = Annotated[datetime, AfterValidator(_as_utc)]
OpaqueId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")]
CursorToken = Annotated[
    str,
    Field(min_length=1, max_length=MAX_CURSOR_CHARS, pattern=r"^[A-Za-z0-9_-]+$"),
]
SafeSlug = Annotated[
    str,
    Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"),
]
ShortText = Annotated[str, Field(min_length=1, max_length=200)]
HttpsUrl = Annotated[
    str,
    Field(min_length=1, max_length=2_048),
    AfterValidator(_https_url),
]


class ContractModel(BaseModel):
    """Reject silently ignored input and normalize surrounding whitespace."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ScanTrigger(str, Enum):
    manual = "manual"


class ScanStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    partial = "partial"
    failed = "failed"
    cancelled = "cancelled"


class ScanStage(str, Enum):
    queued = "queued"
    fetching = "fetching"
    persisting = "persisting"
    matching = "matching"
    finalizing = "finalizing"
    complete = "complete"


class ScanWarningScope(str, Enum):
    scan = "scan"
    source = "source"


class ScanWarningCode(str, Enum):
    """Public codes that cannot contain raw provider or exception content."""

    source_timeout = "source_timeout"
    source_unavailable = "source_unavailable"
    source_invalid_response = "source_invalid_response"
    source_incomplete = "source_incomplete"
    source_rate_limited = "source_rate_limited"
    source_fallback_used = "source_fallback_used"
    scan_interrupted = "scan_interrupted"
    scan_retrying = "scan_retrying"


class EvidenceState(str, Enum):
    verified = "verified"
    inferred = "inferred"
    unknown = "unknown"


class OpportunityFactField(str, Enum):
    location = "location"
    employment_type = "employment_type"
    posted_date = "posted_date"
    compensation = "compensation"


class UnknownReasonCode(str, Enum):
    not_reported_by_source = "not_reported_by_source"
    source_field_ambiguous = "source_field_ambiguous"
    source_refresh_degraded = "source_refresh_degraded"
    not_supported_yet = "not_supported_yet"


class EmploymentTypeValue(str, Enum):
    full_time = "full_time"
    contract = "contract"
    intern = "intern"


class CompensationPeriod(str, Enum):
    annual = "annual"
    monthly = "monthly"
    hourly = "hourly"


class PostingState(str, Enum):
    open = "open"
    closed = "closed"
    unknown = "unknown"


class PostingChangeKind(str, Enum):
    new = "new"
    changed = "changed"
    unchanged = "unchanged"
    closed = "closed"
    reopened = "reopened"


class PostingChangedField(str, Enum):
    title = "title"
    description = "description"
    location = "location"
    employment_type = "employment_type"
    posted_date = "posted_date"
    compensation = "compensation"
    canonical_url = "canonical_url"
    state = "state"


class OpportunityLane(str, Enum):
    reach = "reach"
    core = "core"
    hedge = "hedge"
    unassigned = "unassigned"


class ApplicationAcquisitionSource(str, Enum):
    job_hunt_search = "job_hunt_search"
    referral = "referral"
    recruiter_inbound = "recruiter_inbound"
    direct_company = "direct_company"
    job_board = "job_board"
    other = "other"


class MatchAssessmentState(str, Enum):
    assessed = "assessed"
    not_assessed = "not_assessed"


class NotAssessedReason(str, Enum):
    assessment_pending = "assessment_pending"
    resume_unavailable = "resume_unavailable"
    description_unavailable = "description_unavailable"
    not_requested = "not_requested"


class OpportunityDecisionState(str, Enum):
    inbox = "inbox"
    watch = "watch"
    dismiss = "dismiss"
    pursued = "pursued"


class OpportunityDecisionAction(str, Enum):
    pursue = "pursue"
    watch = "watch"
    dismiss = "dismiss"
    restore_to_inbox = "restore_to_inbox"


class DismissReason(str, Enum):
    not_relevant = "not_relevant"
    seniority_mismatch = "seniority_mismatch"
    location_or_mode = "location_or_mode"
    compensation = "compensation"
    not_a_better_move = "not_a_better_move"
    company = "company"
    already_applied = "already_applied"
    closed_or_invalid = "closed_or_invalid"
    duplicate = "duplicate"
    other = "other"


class TodayView(str, Enum):
    inbox = "inbox"
    watching = "watching"
    dismissed = "dismissed"
    all = "all"


class ScanHealthState(str, Enum):
    never_run = "never_run"
    healthy = "healthy"
    degraded = "degraded"
    running = "running"


class ScanCreateRequest(ContractModel):
    """Explicitly manual in this slice; automatic cadence is not implied."""

    trigger: Literal[ScanTrigger.manual] = ScanTrigger.manual


class ScanCounts(ContractModel):
    sources_total: int = Field(default=0, ge=0, le=10_000)
    sources_completed: int = Field(default=0, ge=0, le=10_000)
    sources_succeeded: int = Field(default=0, ge=0, le=10_000)
    sources_degraded: int = Field(default=0, ge=0, le=10_000)
    sources_failed: int = Field(default=0, ge=0, le=10_000)
    observed_postings: int = Field(default=0, ge=0, le=1_000_000)
    matched_postings: int = Field(default=0, ge=0, le=1_000_000)
    new_opportunities: int = Field(default=0, ge=0, le=1_000_000)
    changed_postings: int = Field(default=0, ge=0, le=1_000_000)

    @model_validator(mode="after")
    def counts_are_consistent(self) -> Self:
        if self.sources_completed > self.sources_total:
            raise ValueError("sources_completed cannot exceed sources_total")
        categorized = (
            self.sources_succeeded + self.sources_degraded + self.sources_failed
        )
        if categorized != self.sources_completed:
            raise ValueError(
                "completed sources must equal succeeded + degraded + failed"
            )
        if self.matched_postings > self.observed_postings:
            raise ValueError("matched_postings cannot exceed observed_postings")
        if self.new_opportunities > self.matched_postings:
            raise ValueError("new_opportunities cannot exceed matched_postings")
        if self.changed_postings > self.observed_postings:
            raise ValueError("changed_postings cannot exceed observed_postings")
        return self


class ScanWarning(ContractModel):
    scope: ScanWarningScope
    code: ScanWarningCode
    message: str = Field(min_length=1, max_length=300)
    retryable: bool = False
    company_slug: SafeSlug | None = None
    source: CompanySource | None = None
    occurred_at: UTCDateTime
    last_success_at: UTCDateTime | None = None

    @model_validator(mode="after")
    def source_scope_is_complete(self) -> Self:
        if self.scope is ScanWarningScope.source:
            if self.company_slug is None or self.source is None:
                raise ValueError(
                    "source warnings require company_slug and source"
                )
        elif self.company_slug is not None or self.source is not None:
            raise ValueError("scan warnings cannot name a company or source")
        if (
            self.last_success_at is not None
            and self.last_success_at > self.occurred_at
        ):
            raise ValueError("last_success_at cannot follow occurred_at")
        return self


class ScanStatusResponse(ContractModel):
    id: OpaqueId
    version: int = Field(ge=1)
    saved_search_id: OpaqueId
    saved_search_version: int = Field(ge=1)
    trigger: ScanTrigger = ScanTrigger.manual
    status: ScanStatus
    stage: ScanStage
    queued_at: UTCDateTime
    started_at: UTCDateTime | None = None
    completed_at: UTCDateTime | None = None
    counts: ScanCounts = Field(default_factory=ScanCounts)
    warnings: list[ScanWarning] = Field(default_factory=list, max_length=1_000)

    @model_validator(mode="after")
    def lifecycle_is_consistent(self) -> Self:
        if self.started_at is not None and self.started_at < self.queued_at:
            raise ValueError("started_at cannot precede queued_at")
        if self.completed_at is not None:
            lower_bound = self.started_at or self.queued_at
            if self.completed_at < lower_bound:
                raise ValueError("completed_at cannot precede scan start")

        terminal = {
            ScanStatus.succeeded,
            ScanStatus.partial,
            ScanStatus.failed,
            ScanStatus.cancelled,
        }
        if self.status is ScanStatus.queued:
            if (
                self.stage is not ScanStage.queued
                or self.started_at is not None
                or self.completed_at is not None
                or self.counts.sources_completed != 0
            ):
                raise ValueError("queued scans must have queued-only state")
        elif self.status is ScanStatus.running:
            if (
                self.stage in {ScanStage.queued, ScanStage.complete}
                or self.started_at is None
                or self.completed_at is not None
            ):
                raise ValueError("running scans require an active stage and start time")
        elif self.status in terminal:
            if self.stage is not ScanStage.complete or self.completed_at is None:
                raise ValueError("terminal scans require complete stage and timestamp")
            if self.status is not ScanStatus.cancelled and self.started_at is None:
                raise ValueError("completed scans require started_at")

        if self.status is ScanStatus.succeeded and (
            self.counts.sources_degraded
            or self.counts.sources_failed
            or self.warnings
        ):
            raise ValueError("succeeded scans cannot contain degradation")
        if self.status is ScanStatus.partial and (
            not self.warnings
            or not (self.counts.sources_degraded or self.counts.sources_failed)
        ):
            raise ValueError("partial scans require degraded counts and warnings")
        if self.status is ScanStatus.failed and not self.warnings:
            raise ValueError("failed scans require a safe warning")

        warning_keys = [
            (warning.scope, warning.code, warning.company_slug, warning.source)
            for warning in self.warnings
        ]
        if len(warning_keys) != len(set(warning_keys)):
            raise ValueError("scan warnings must not contain duplicates")
        return self


class ScanCreateResponse(ScanStatusResponse):
    """A created scan may be a replay of any already-persisted status."""


class _EvidenceFactBase(ContractModel):
    state: EvidenceState
    source_label: str | None = Field(default=None, min_length=1, max_length=120)
    observed_at: UTCDateTime | None = None

    @model_validator(mode="after")
    def source_evidence_is_paired(self) -> Self:
        if (self.source_label is None) != (self.observed_at is None):
            raise ValueError("source_label and observed_at must be supplied together")
        if self.state is not EvidenceState.unknown and self.source_label is None:
            raise ValueError("known facts require source evidence")
        return self


class TextEvidenceFact(_EvidenceFactBase):
    value: str | None = Field(default=None, min_length=1, max_length=300)

    @model_validator(mode="after")
    def value_matches_state(self) -> Self:
        if (self.state is EvidenceState.unknown) != (self.value is None):
            raise ValueError("unknown facts must have null values; known facts need values")
        return self


class EmploymentTypeEvidenceFact(_EvidenceFactBase):
    value: EmploymentTypeValue | None = None

    @model_validator(mode="after")
    def value_matches_state(self) -> Self:
        if (self.state is EvidenceState.unknown) != (self.value is None):
            raise ValueError("unknown facts must have null values; known facts need values")
        return self


class DateEvidenceFact(_EvidenceFactBase):
    value: date | None = None

    @model_validator(mode="after")
    def value_matches_state(self) -> Self:
        if (self.state is EvidenceState.unknown) != (self.value is None):
            raise ValueError("unknown facts must have null values; known facts need values")
        return self


class CompensationValue(ContractModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    period: CompensationPeriod
    minimum: int | None = Field(default=None, ge=0, le=1_000_000_000)
    maximum: int | None = Field(default=None, ge=0, le=1_000_000_000)

    @model_validator(mode="after")
    def range_is_useful(self) -> Self:
        if self.minimum is None and self.maximum is None:
            raise ValueError("compensation needs a minimum or maximum")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("compensation minimum cannot exceed maximum")
        return self


class CompensationEvidenceFact(_EvidenceFactBase):
    value: CompensationValue | None = None

    @model_validator(mode="after")
    def value_matches_state(self) -> Self:
        if (self.state is EvidenceState.unknown) != (self.value is None):
            raise ValueError("unknown facts must have null values; known facts need values")
        return self


class OpportunityFacts(ContractModel):
    location: TextEvidenceFact
    employment_type: EmploymentTypeEvidenceFact
    posted_date: DateEvidenceFact
    compensation: CompensationEvidenceFact


class OpportunityUnknown(ContractModel):
    field: OpportunityFactField
    reason_code: UnknownReasonCode
    message: str = Field(min_length=1, max_length=240)


class SavedSearchProvenance(ContractModel):
    saved_search_id: OpaqueId
    saved_search_name: str = Field(min_length=1, max_length=120)
    first_matched_at: UTCDateTime
    last_matched_at: UTCDateTime

    @model_validator(mode="after")
    def match_times_are_ordered(self) -> Self:
        if self.last_matched_at < self.first_matched_at:
            raise ValueError("last_matched_at cannot precede first_matched_at")
        return self


class TransparentMatchSummary(ContractModel):
    """A local, inspectable match result with no opaque fit percentage."""

    state: MatchAssessmentState
    algorithm_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    resume_version_id: OpaqueId | None = None
    matched_terms: list[ShortText] = Field(default_factory=list, max_length=20)
    representative_requirement: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
    )
    approved_evidence_ids: list[OpaqueId] = Field(default_factory=list, max_length=20)
    not_assessed_reason: NotAssessedReason | None = None

    @model_validator(mode="after")
    def assessment_is_truthful(self) -> Self:
        if len(self.matched_terms) != len(
            {term.casefold() for term in self.matched_terms}
        ):
            raise ValueError("matched_terms must not contain duplicates")
        if len(self.approved_evidence_ids) != len(set(self.approved_evidence_ids)):
            raise ValueError("approved_evidence_ids must not contain duplicates")
        if self.state is MatchAssessmentState.assessed:
            if (
                self.algorithm_version is None
                or self.resume_version_id is None
                or self.not_assessed_reason is not None
            ):
                raise ValueError(
                    "assessed matches require algorithm and resume versions only"
                )
        elif (
            self.not_assessed_reason is None
            or self.algorithm_version is not None
            or self.resume_version_id is not None
            or self.matched_terms
            or self.representative_requirement is not None
            or self.approved_evidence_ids
        ):
            raise ValueError(
                "not-assessed matches require a reason and no assessment evidence"
            )
        return self


class OpportunityDecisionRequest(ContractModel):
    action: OpportunityDecisionAction
    dismiss_reason: DismissReason | None = None
    note: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_DECISION_NOTE_CHARS,
    )
    restore_decision_event_id: OpaqueId | None = None
    initial_action_due_on: date | None = None
    acquisition_source: ApplicationAcquisitionSource | None = None
    selected_saved_search_id: OpaqueId | None = None

    @model_validator(mode="after")
    def action_shape_is_exact(self) -> Self:
        if self.action is OpportunityDecisionAction.pursue:
            if (
                self.dismiss_reason is not None
                or self.note is not None
                or self.restore_decision_event_id is not None
            ):
                raise ValueError(
                    "pursue cannot include dismiss, note, or restore fields"
                )
            if (
                self.acquisition_source is not None
                and self.acquisition_source
                is not ApplicationAcquisitionSource.job_hunt_search
                and self.selected_saved_search_id is not None
            ):
                raise ValueError(
                    "only job_hunt_search acquisition can select a saved search"
                )
        elif self.action is OpportunityDecisionAction.dismiss:
            if self.dismiss_reason is None:
                raise ValueError("dismiss requires a structured dismiss_reason")
            if self.restore_decision_event_id is not None:
                raise ValueError("dismiss cannot restore a decision event")
            if self.initial_action_due_on is not None:
                raise ValueError("dismiss cannot include an initial action due date")
            if self.acquisition_source is not None or self.selected_saved_search_id is not None:
                raise ValueError("dismiss cannot include pursuit attribution")
            if self.dismiss_reason is DismissReason.other and self.note is None:
                raise ValueError("dismiss_reason other requires a note")
        elif self.action is OpportunityDecisionAction.watch:
            if (
                self.dismiss_reason is not None
                or self.restore_decision_event_id is not None
                or self.initial_action_due_on is not None
                or self.acquisition_source is not None
                or self.selected_saved_search_id is not None
            ):
                raise ValueError(
                    "watch cannot include dismiss, restore, or pursue fields"
                )
        elif self.action is OpportunityDecisionAction.restore_to_inbox:
            if self.restore_decision_event_id is None:
                raise ValueError("restore_to_inbox requires restore_decision_event_id")
            if (
                self.dismiss_reason is not None
                or self.note is not None
                or self.initial_action_due_on is not None
                or self.acquisition_source is not None
                or self.selected_saved_search_id is not None
            ):
                raise ValueError(
                    "restore_to_inbox cannot include a reason, note, or pursue fields"
                )
        return self


class PursueOpportunityRequest(ContractModel):
    """Narrow pursue-only command used by the atomic application boundary."""

    action: Literal[OpportunityDecisionAction.pursue] = (
        OpportunityDecisionAction.pursue
    )
    initial_action_due_on: date | None = None
    acquisition_source: ApplicationAcquisitionSource = (
        ApplicationAcquisitionSource.job_hunt_search
    )
    selected_saved_search_id: OpaqueId | None = None

    @model_validator(mode="after")
    def selected_search_matches_source(self) -> Self:
        if (
            self.acquisition_source
            is not ApplicationAcquisitionSource.job_hunt_search
            and self.selected_saved_search_id is not None
        ):
            raise ValueError(
                "only job_hunt_search acquisition can select a saved search"
            )
        return self


class OpportunityDecisionEvent(ContractModel):
    id: OpaqueId
    opportunity_id: OpaqueId
    action: OpportunityDecisionAction
    previous_state: OpportunityDecisionState
    state: OpportunityDecisionState
    dismiss_reason: DismissReason | None = None
    note: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_DECISION_NOTE_CHARS,
    )
    restores_event_id: OpaqueId | None = None
    created_at: UTCDateTime

    @model_validator(mode="after")
    def transition_matches_action(self) -> Self:
        if self.action is OpportunityDecisionAction.pursue:
            if (
                self.previous_state
                not in {
                    OpportunityDecisionState.inbox,
                    OpportunityDecisionState.watch,
                    OpportunityDecisionState.dismiss,
                }
                or self.state is not OpportunityDecisionState.pursued
                or self.dismiss_reason is not None
                or self.note is not None
                or self.restores_event_id is not None
            ):
                raise ValueError(
                    "pursue events must enter pursued from inbox, watch, or dismiss"
                )
        elif self.action is OpportunityDecisionAction.watch:
            if (
                self.state is not OpportunityDecisionState.watch
                or self.dismiss_reason is not None
                or self.restores_event_id is not None
            ):
                raise ValueError("watch events must transition to watch")
        elif self.action is OpportunityDecisionAction.dismiss:
            if (
                self.state is not OpportunityDecisionState.dismiss
                or self.dismiss_reason is None
                or self.restores_event_id is not None
            ):
                raise ValueError("dismiss events must include a reason")
            if self.dismiss_reason is DismissReason.other and self.note is None:
                raise ValueError("dismiss_reason other requires a note")
        elif self.action is OpportunityDecisionAction.restore_to_inbox:
            if (
                self.state is not OpportunityDecisionState.inbox
                or self.previous_state
                not in {
                    OpportunityDecisionState.watch,
                    OpportunityDecisionState.dismiss,
                }
                or self.dismiss_reason is not None
                or self.note is not None
                or self.restores_event_id is None
            ):
                raise ValueError("restore events must restore a prior decision to inbox")
        return self


class OpportunityDecisionResponse(ContractModel):
    opportunity_id: OpaqueId
    opportunity_version: int = Field(ge=1)
    state: OpportunityDecisionState
    event: OpportunityDecisionEvent
    pursuit: PursuitBundle | None = None

    @model_validator(mode="after")
    def event_matches_resource(self) -> Self:
        if self.event.opportunity_id != self.opportunity_id:
            raise ValueError("decision event must belong to the opportunity")
        if self.event.state is not self.state:
            raise ValueError("decision event state must match the opportunity")
        if self.event.action is OpportunityDecisionAction.pursue:
            if self.state is not OpportunityDecisionState.pursued or self.pursuit is None:
                raise ValueError("pursue responses require a pursuit bundle")
            if self.pursuit.application.opportunity_id != self.opportunity_id:
                raise ValueError("pursuit application must belong to the opportunity")
        elif self.pursuit is not None:
            raise ValueError("only pursue responses can include a pursuit bundle")
        return self


class OpportunityPosting(ContractModel):
    id: OpaqueId
    company: str = Field(min_length=1, max_length=200)
    company_slug: SafeSlug
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=2_000)
    canonical_url: HttpsUrl
    source: CompanySource
    source_job_id: str | None = Field(default=None, min_length=1, max_length=512)
    first_party: bool
    state: PostingState
    change_kind: PostingChangeKind
    first_seen_at: UTCDateTime
    last_confirmed_at: UTCDateTime
    changed_at: UTCDateTime | None = None

    @model_validator(mode="after")
    def observation_times_are_consistent(self) -> Self:
        if self.last_confirmed_at < self.first_seen_at:
            raise ValueError("last_confirmed_at cannot precede first_seen_at")
        change_needs_time = self.change_kind in {
            PostingChangeKind.changed,
            PostingChangeKind.closed,
            PostingChangeKind.reopened,
        }
        if change_needs_time != (self.changed_at is not None):
            raise ValueError("changed, closed, and reopened postings require changed_at")
        if self.changed_at is not None:
            if self.changed_at < self.first_seen_at:
                raise ValueError("changed_at cannot precede first_seen_at")
            if (
                self.change_kind is not PostingChangeKind.closed
                and self.changed_at > self.last_confirmed_at
            ):
                raise ValueError(
                    "non-closure changed_at cannot follow last_confirmed_at"
                )
        return self


class TodayOpportunityItem(ContractModel):
    id: OpaqueId
    version: int = Field(ge=1)
    state: OpportunityDecisionState = OpportunityDecisionState.inbox
    lane: OpportunityLane = OpportunityLane.unassigned
    posting: OpportunityPosting
    facts: OpportunityFacts
    unknowns: list[OpportunityUnknown] = Field(default_factory=list, max_length=4)
    discovered_by: list[SavedSearchProvenance] = Field(min_length=1, max_length=100)
    match: TransparentMatchSummary
    latest_decision: OpportunityDecisionEvent | None = None
    created_at: UTCDateTime
    updated_at: UTCDateTime

    @model_validator(mode="after")
    def projection_is_consistent(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")

        provenance_ids = [item.saved_search_id for item in self.discovered_by]
        if len(provenance_ids) != len(set(provenance_ids)):
            raise ValueError("saved-search provenance must not contain duplicates")

        unknown_fields = [item.field for item in self.unknowns]
        if len(unknown_fields) != len(set(unknown_fields)):
            raise ValueError("unknown fields must not contain duplicates")
        fact_states = {
            OpportunityFactField.location: self.facts.location.state,
            OpportunityFactField.employment_type: self.facts.employment_type.state,
            OpportunityFactField.posted_date: self.facts.posted_date.state,
            OpportunityFactField.compensation: self.facts.compensation.state,
        }
        expected_unknowns = {
            field for field, state in fact_states.items() if state is EvidenceState.unknown
        }
        if set(unknown_fields) != expected_unknowns:
            raise ValueError("every unknown fact needs exactly one explicit unknown")

        if self.latest_decision is None:
            if self.state is not OpportunityDecisionState.inbox:
                raise ValueError("decided opportunities require a latest decision")
        elif (
            self.latest_decision.opportunity_id != self.id
            or self.latest_decision.state is not self.state
        ):
            raise ValueError("latest decision must match the opportunity")
        return self


class PostingVersionSummary(ContractModel):
    version: int = Field(ge=1)
    observed_at: UTCDateTime
    change_kind: PostingChangeKind
    changed_fields: list[PostingChangedField] = Field(
        default_factory=list,
        max_length=len(PostingChangedField),
    )

    @model_validator(mode="after")
    def changed_fields_are_consistent(self) -> Self:
        if len(self.changed_fields) != len(set(self.changed_fields)):
            raise ValueError("changed_fields must not contain duplicates")
        if self.change_kind is PostingChangeKind.unchanged and self.changed_fields:
            raise ValueError("unchanged versions cannot list changed fields")
        if self.change_kind is PostingChangeKind.changed and not self.changed_fields:
            raise ValueError("changed versions must list changed fields")
        return self


class OpportunityDetailResponse(TodayOpportunityItem):
    data_source: Literal["database"] = "database"
    description: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_DESCRIPTION_CHARS,
    )
    apply_urls: list[HttpsUrl] = Field(min_length=1, max_length=20)
    posting_versions: list[PostingVersionSummary] = Field(min_length=1, max_length=500)
    decision_history: list[OpportunityDecisionEvent] = Field(
        default_factory=list,
        max_length=500,
    )

    @model_validator(mode="after")
    def detail_history_is_consistent(self) -> Self:
        if len(self.apply_urls) != len(set(self.apply_urls)):
            raise ValueError("apply_urls must not contain duplicates")

        posting_versions = [item.version for item in self.posting_versions]
        if posting_versions != sorted(posting_versions) or len(posting_versions) != len(
            set(posting_versions)
        ):
            raise ValueError("posting_versions must be unique and ordered")

        event_ids = [event.id for event in self.decision_history]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("decision history must not contain duplicates")
        if any(event.opportunity_id != self.id for event in self.decision_history):
            raise ValueError("decision history must belong to the opportunity")
        event_times = [event.created_at for event in self.decision_history]
        if event_times != sorted(event_times):
            raise ValueError("decision history must be chronological")
        if self.latest_decision is not None and (
            not self.decision_history
            or self.decision_history[-1].id != self.latest_decision.id
        ):
            raise ValueError("latest decision must end the decision history")
        return self


class TodaySummary(ContractModel):
    needs_decision: int = Field(ge=0)
    watching: int = Field(ge=0)
    dismissed: int = Field(ge=0)


class TodayScanHealth(ContractModel):
    state: ScanHealthState
    active_searches: int = Field(ge=0, le=10_000)
    running_scan_id: OpaqueId | None = None
    last_attempt_at: UTCDateTime | None = None
    last_success_at: UTCDateTime | None = None
    warnings: list[ScanWarning] = Field(default_factory=list, max_length=1_000)

    @model_validator(mode="after")
    def health_is_consistent(self) -> Self:
        if (
            self.last_success_at is not None
            and self.last_attempt_at is not None
            and self.last_success_at > self.last_attempt_at
        ):
            raise ValueError("last_success_at cannot follow last_attempt_at")
        if self.state is ScanHealthState.never_run and (
            self.running_scan_id is not None
            or self.last_attempt_at is not None
            or self.last_success_at is not None
            or self.warnings
        ):
            raise ValueError("never-run health cannot contain scan state")
        if self.state is ScanHealthState.running and self.running_scan_id is None:
            raise ValueError("running health requires running_scan_id")
        if self.state is not ScanHealthState.running and self.running_scan_id is not None:
            raise ValueError("only running health can name a running scan")
        if self.state is ScanHealthState.healthy and self.warnings:
            raise ValueError("healthy scan state cannot contain warnings")
        if self.state is ScanHealthState.degraded and not self.warnings:
            raise ValueError("degraded scan state requires warnings")
        return self


class TodayQuery(ContractModel):
    view: TodayView = TodayView.inbox
    scan_id: OpaqueId | None = None
    saved_search_id: OpaqueId | None = None
    lane: OpportunityLane | None = None
    cursor: CursorToken | None = None
    limit: int = Field(default=20, ge=1, le=MAX_PAGE_SIZE)


class TodayListResponse(ContractModel):
    data_source: Literal["database"] = "database"
    as_of: UTCDateTime
    summary: TodaySummary
    scan_health: TodayScanHealth
    items: list[TodayOpportunityItem] = Field(default_factory=list, max_length=MAX_PAGE_SIZE)
    next_cursor: CursorToken | None = None


__all__ = [
    "ApplicationAcquisitionSource",
    "CompensationEvidenceFact",
    "CompensationPeriod",
    "CompensationValue",
    "CursorToken",
    "DateEvidenceFact",
    "DismissReason",
    "EmploymentTypeEvidenceFact",
    "EmploymentTypeValue",
    "EvidenceState",
    "HttpsUrl",
    "MatchAssessmentState",
    "NotAssessedReason",
    "OpaqueId",
    "OpportunityDecisionAction",
    "OpportunityDecisionEvent",
    "OpportunityDecisionRequest",
    "OpportunityDecisionResponse",
    "OpportunityDecisionState",
    "OpportunityDetailResponse",
    "OpportunityFactField",
    "OpportunityFacts",
    "OpportunityLane",
    "OpportunityPosting",
    "OpportunityUnknown",
    "PostingChangeKind",
    "PostingChangedField",
    "PostingState",
    "PostingVersionSummary",
    "PursueOpportunityRequest",
    "SavedSearchProvenance",
    "ScanCounts",
    "ScanCreateRequest",
    "ScanCreateResponse",
    "ScanHealthState",
    "ScanStage",
    "ScanStatus",
    "ScanStatusResponse",
    "ScanTrigger",
    "ScanWarning",
    "ScanWarningCode",
    "ScanWarningScope",
    "TextEvidenceFact",
    "TodayListResponse",
    "TodayOpportunityItem",
    "TodayQuery",
    "TodayScanHealth",
    "TodaySummary",
    "TodayView",
    "TransparentMatchSummary",
    "UTCDateTime",
    "UnknownReasonCode",
]
