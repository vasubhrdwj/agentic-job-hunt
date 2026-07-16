"""Owner-facing contracts for profile, evidence, and saved-search onboarding.

These models are deliberately provider-free. They validate durable owner input
and produce a lossless projection into the current ``HuntRequestPayload``
criteria, but they never parse a resume, search the web, or invoke a model.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .schemas import EmploymentType, JobCriteria
from .security import MAX_RESUME_CHARS


OpaqueId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")]
ShortText = Annotated[str, Field(min_length=1, max_length=200)]
Seniority = Literal["junior", "mid", "senior", "staff"]
WorkMode = Literal["remote", "hybrid", "onsite"]
DesiredEmploymentType = Literal["full_time", "contract", "intern"]
AuthorizationStatus = Literal[
    "citizen",
    "permanent_resident",
    "work_permit",
    "needs_sponsorship",
    "not_authorized",
    "other",
]
OnboardingStep = Literal[
    "profile",
    "resume",
    "career_track",
    "evidence",
    "saved_search",
    "complete",
]
ResumeSource = Literal["pasted", "uploaded", "imported", "edited"]
EvidenceOrigin = Literal["owner_entered", "resume_suggestion"]
EvidenceApprovalState = Literal["pending", "approved", "rejected", "retired"]
ScheduleCadence = Literal["manual", "daily", "weekdays", "weekly"]
DayOfWeek = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class ContractModel(BaseModel):
    """Reject silently ignored fields and normalize surrounding whitespace."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class WorkAuthorization(ContractModel):
    country_code: str = Field(pattern=r"^[A-Za-z]{2}$")
    status: AuthorizationStatus

    @field_validator("country_code")
    @classmethod
    def normalize_country_code(cls, value: str) -> str:
        return value.upper()


class ResumeVersionCreate(ContractModel):
    label: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=MAX_RESUME_CHARS)
    source: ResumeSource = "pasted"
    parent_resume_version_id: OpaqueId | None = None
    set_as_base: bool = False

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("resume content must not be blank")
        return value


class ResumeVersionSummary(ContractModel):
    id: OpaqueId
    label: str
    source: ResumeSource
    parent_resume_version_id: OpaqueId | None
    is_base: bool
    character_count: int = Field(ge=1, le=MAX_RESUME_CHARS)
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class ResumeVersionDetail(ResumeVersionSummary):
    content: str = Field(min_length=1, max_length=MAX_RESUME_CHARS)


class ResumeVersionList(ContractModel):
    items: list[ResumeVersionSummary]


class CandidateProfileData(ContractModel):
    career_thesis: str | None = Field(default=None, min_length=1, max_length=2_000)
    current_title: str | None = Field(default=None, min_length=1, max_length=200)
    current_location: str | None = Field(default=None, min_length=1, max_length=200)
    work_authorizations: list[WorkAuthorization] = Field(
        default_factory=list,
        max_length=20,
    )
    work_modes: list[WorkMode] = Field(default_factory=list, max_length=3)
    employment_types: list[DesiredEmploymentType] = Field(
        default_factory=lambda: ["full_time"],
        min_length=1,
        max_length=3,
    )
    notice_period_days: int | None = Field(default=None, ge=0, le=365)
    onboarding_step: OnboardingStep = "profile"

    @model_validator(mode="after")
    def reject_duplicate_preferences(self) -> Self:
        _require_unique(self.work_modes, "work_modes")
        _require_unique(self.employment_types, "employment_types")
        countries = [entry.country_code for entry in self.work_authorizations]
        _require_unique(countries, "work_authorizations country_code")
        return self


class CandidateProfileWrite(CandidateProfileData):
    @model_validator(mode="after")
    def require_meaningful_profile_details(self) -> Self:
        if not _profile_has_meaningful_details(self):
            raise ValueError(
                "profile must include at least one meaningful personal detail"
            )
        return self


class CandidateProfileResponse(CandidateProfileData):
    id: OpaqueId
    base_resume: ResumeVersionSummary | None = None
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


def _profile_has_meaningful_details(profile: CandidateProfileData) -> bool:
    return bool(
        profile.career_thesis
        or profile.current_title
        or profile.current_location
        or profile.work_authorizations
        or profile.work_modes
        or profile.notice_period_days is not None
    )


class CareerPriorities(ContractModel):
    compensation: int = Field(default=3, ge=0, le=5)
    scope: int = Field(default=3, ge=0, le=5)
    learning: int = Field(default=3, ge=0, le=5)
    company_quality: int = Field(default=3, ge=0, le=5)
    flexibility: int = Field(default=3, ge=0, le=5)

    @model_validator(mode="after")
    def require_one_priority(self) -> Self:
        if not any(self.model_dump().values()):
            raise ValueError("at least one career priority must be greater than zero")
        return self


class CareerTrackCreate(ContractModel):
    name: str = Field(min_length=1, max_length=120)
    role_families: list[ShortText] = Field(min_length=1, max_length=10)
    seniority_levels: list[Seniority] = Field(min_length=1, max_length=4)
    target_locations: list[ShortText] = Field(min_length=1, max_length=20)
    priorities: CareerPriorities = Field(default_factory=CareerPriorities)
    active: bool = True

    @model_validator(mode="after")
    def reject_duplicate_track_values(self) -> Self:
        _require_unique_casefolded(self.role_families, "role_families")
        _require_unique(self.seniority_levels, "seniority_levels")
        _require_unique_casefolded(self.target_locations, "target_locations")
        return self


class CareerTrackPatch(ContractModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role_families: list[ShortText] | None = Field(default=None, min_length=1, max_length=10)
    seniority_levels: list[Seniority] | None = Field(
        default=None,
        min_length=1,
        max_length=4,
    )
    target_locations: list[ShortText] | None = Field(
        default=None,
        min_length=1,
        max_length=20,
    )
    priorities: CareerPriorities | None = None
    active: bool | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("career track patch must change at least one field")
        if self.role_families is not None:
            _require_unique_casefolded(self.role_families, "role_families")
        if self.seniority_levels is not None:
            _require_unique(self.seniority_levels, "seniority_levels")
        if self.target_locations is not None:
            _require_unique_casefolded(self.target_locations, "target_locations")
        return self


class CareerTrackResponse(CareerTrackCreate):
    id: OpaqueId
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class CareerTrackList(ContractModel):
    items: list[CareerTrackResponse]


class AchievementEvidenceCreate(ContractModel):
    statement: str = Field(min_length=1, max_length=1_000)
    source_resume_version_id: OpaqueId | None = None
    source_excerpt: str | None = Field(default=None, min_length=1, max_length=2_000)
    skills: list[ShortText] = Field(default_factory=list, max_length=30)
    origin: EvidenceOrigin = "owner_entered"

    @model_validator(mode="after")
    def validate_skills(self) -> Self:
        _require_unique_casefolded(self.skills, "skills")
        return self


class AchievementEvidencePatch(ContractModel):
    statement: str | None = Field(default=None, min_length=1, max_length=1_000)
    source_excerpt: str | None = Field(default=None, min_length=1, max_length=2_000)
    skills: list[ShortText] | None = Field(default=None, max_length=30)
    approval_state: EvidenceApprovalState | None = Field(
        default=None,
        description=(
            "Explicit review transition. New evidence is pending; pending may be "
            "approved or rejected, and only previously approved evidence may be retired."
        ),
    )

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("evidence patch must change at least one field")
        if self.skills is not None:
            _require_unique_casefolded(self.skills, "skills")
        return self


class AchievementEvidenceResponse(ContractModel):
    id: OpaqueId
    statement: str
    source_resume_version_id: OpaqueId | None
    source_excerpt: str | None
    skills: list[str]
    origin: EvidenceOrigin
    approval_state: EvidenceApprovalState
    approved_at: datetime | None
    rejected_at: datetime | None
    retired_at: datetime | None
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class AchievementEvidenceList(ContractModel):
    items: list[AchievementEvidenceResponse]


class SavedSearchCriteria(ContractModel):
    """Validated, lossless transport equivalent of the current JobCriteria."""

    role_keywords: list[ShortText] = Field(min_length=1, max_length=20)
    seniority: Seniority
    location: list[ShortText] = Field(min_length=1, max_length=20)
    comp_min_lpa: int | None = Field(default=None, ge=0, le=1_000)
    comp_max_lpa: int | None = Field(default=None, ge=0, le=1_000)
    employment_types: list[DesiredEmploymentType] = Field(
        default_factory=lambda: ["full_time"],
        min_length=1,
        max_length=3,
    )
    max_age_days: int | None = Field(default=45, ge=1, le=365)
    country: str = Field(default="in", pattern=r"^[a-z]{2}$")

    @model_validator(mode="after")
    def validate_criteria(self) -> Self:
        _require_unique_casefolded(self.role_keywords, "role_keywords")
        _require_unique_casefolded(self.location, "location")
        _require_unique(self.employment_types, "employment_types")
        if (
            self.comp_min_lpa is not None
            and self.comp_max_lpa is not None
            and self.comp_min_lpa > self.comp_max_lpa
        ):
            raise ValueError("comp_min_lpa must be less than or equal to comp_max_lpa")
        return self

    def to_job_criteria(self) -> JobCriteria:
        return JobCriteria.model_validate(self.model_dump(mode="json"))


class SavedSearchSchedule(ContractModel):
    cadence: ScheduleCadence = "manual"
    timezone: str = Field(min_length=1, max_length=64)
    local_time: time | None = None
    days_of_week: list[DayOfWeek] = Field(default_factory=list, max_length=7)

    @field_validator("timezone")
    @classmethod
    def timezone_must_be_iana(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def validate_schedule_shape(self) -> Self:
        _require_unique(self.days_of_week, "days_of_week")
        if self.local_time is not None and (
            self.local_time.tzinfo is not None
            or self.local_time.second != 0
            or self.local_time.microsecond != 0
        ):
            raise ValueError("local_time must be a timezone-free HH:MM value")
        if self.cadence == "manual":
            if self.local_time is not None or self.days_of_week:
                raise ValueError("manual schedules cannot include a time or weekdays")
        elif self.cadence in {"daily", "weekdays"}:
            if self.local_time is None:
                raise ValueError(f"{self.cadence} schedules require local_time")
            if self.days_of_week:
                raise ValueError(f"{self.cadence} schedules cannot include days_of_week")
        elif self.cadence == "weekly":
            if self.local_time is None or not self.days_of_week:
                raise ValueError("weekly schedules require local_time and days_of_week")
        return self


class SavedSearchCreate(ContractModel):
    name: str = Field(min_length=1, max_length=120)
    career_track_id: OpaqueId
    resume_version_id: OpaqueId | None = None
    criteria: SavedSearchCriteria
    schedule: SavedSearchSchedule
    pack: str = Field(default="backend_india", pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    use_self_rag: bool = True
    active: bool = True


class SavedSearchPatch(ContractModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    career_track_id: OpaqueId | None = None
    resume_version_id: OpaqueId | None = None
    criteria: SavedSearchCriteria | None = None
    schedule: SavedSearchSchedule | None = None
    pack: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    use_self_rag: bool | None = None
    active: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("saved search patch must change at least one field")
        return self


class SavedSearchResponse(SavedSearchCreate):
    id: OpaqueId
    last_scan_at: datetime | None = None
    next_scan_at: datetime | None = None
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def next_scan_matches_state(self) -> Self:
        if (not self.active or self.schedule.cadence == "manual") and self.next_scan_at:
            raise ValueError("inactive and manual searches cannot have next_scan_at")
        return self


class SavedSearchList(ContractModel):
    items: list[SavedSearchResponse]


class HuntInput(ContractModel):
    resume_text: str = Field(min_length=1, max_length=MAX_RESUME_CHARS)
    criteria: SavedSearchCriteria
    pack: str
    use_self_rag: bool
    provider_consent_required: Literal[True] = True


HuntInputBlocker = Literal[
    "profile_missing",
    "base_resume_missing",
    "selected_resume_missing",
    "career_track_inactive",
    "saved_search_inactive",
]


class SavedSearchHuntInputResponse(ContractModel):
    saved_search_id: OpaqueId
    saved_search_version: int = Field(ge=1)
    career_track_id: OpaqueId
    career_track_version: int = Field(ge=1)
    resume: ResumeVersionSummary | None
    ready: bool
    blockers: list[HuntInputBlocker] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    input: HuntInput | None

    @model_validator(mode="after")
    def readiness_is_consistent(self) -> Self:
        if self.ready and (self.blockers or self.input is None or self.resume is None):
            raise ValueError("ready hunt input cannot have blockers or missing input")
        if not self.ready and not self.blockers:
            raise ValueError("blocked hunt input must explain at least one blocker")
        return self


class ProblemFieldError(ContractModel):
    field: str
    message: str


class ProblemResponse(ContractModel):
    code: str
    message: str
    retryable: bool = False
    request_id: str
    field_errors: list[ProblemFieldError] | None = None


class WorkspaceDeleteResponse(ContractModel):
    ok: Literal[True] = True


def _require_unique(values: list[object], field: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} must not contain duplicates")


def _require_unique_casefolded(values: list[str], field: str) -> None:
    normalized = [value.casefold() for value in values]
    _require_unique(normalized, field)


__all__ = [
    "AchievementEvidenceCreate",
    "AchievementEvidenceList",
    "AchievementEvidencePatch",
    "AchievementEvidenceResponse",
    "CandidateProfileData",
    "CandidateProfileResponse",
    "CandidateProfileWrite",
    "CareerTrackCreate",
    "CareerTrackList",
    "CareerTrackPatch",
    "CareerTrackResponse",
    "HuntInput",
    "ProblemResponse",
    "ResumeVersionCreate",
    "ResumeVersionDetail",
    "ResumeVersionList",
    "ResumeVersionSummary",
    "SavedSearchCreate",
    "SavedSearchCriteria",
    "SavedSearchHuntInputResponse",
    "SavedSearchList",
    "SavedSearchPatch",
    "SavedSearchResponse",
    "SavedSearchSchedule",
    "WorkAuthorization",
    "WorkspaceDeleteResponse",
]
