"""Strict provider-free contracts for grounded application requirement reviews."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from .application_schemas import OpaqueId, UTCDateTime
from .profile_schemas import AchievementEvidenceResponse


MAX_APPLICATION_PACK_REQUIREMENTS = 40
MAX_APPLICATION_PACK_EVIDENCE_PER_REQUIREMENT = 10
MAX_APPLICATION_PACK_JOB_DESCRIPTION_CHARS = 100_000
MAX_APPLICATION_PACK_REQUIREMENT_CHARS = 2_000
MAX_APPLICATION_PACK_CURRENT_EVIDENCE = 500
APPLICATION_PACK_EXTRACTION_VERSION = "requirements-v1"


class ApplicationPackContractModel(BaseModel):
    """Reject silently ignored fields without rewriting exact source text."""

    model_config = ConfigDict(extra="forbid")


class ApplicationPackStatus(str, Enum):
    not_started = "not_started"
    draft = "draft"
    reviewed = "reviewed"


class ApplicationPackRevisionSource(str, Enum):
    extracted = "extracted"
    edited = "edited"


class ApplicationPackDescriptionSource(str, Enum):
    persisted_description = "persisted_description"
    owner_supplied = "owner_supplied"


class ApplicationPackRequirementImportance(str, Enum):
    required = "required"
    preferred = "preferred"


class ApplicationPackRequirementCoverage(str, Enum):
    needs_review = "needs_review"
    supported = "supported"
    partial = "partial"
    unsupported = "unsupported"


class ApplicationPackBlocker(str, Enum):
    base_resume_missing = "base_resume_missing"
    approved_evidence_missing = "approved_evidence_missing"
    owner_job_description_required = "owner_job_description_required"
    no_requirements_extracted = "no_requirements_extracted"
    requirements_need_review = "requirements_need_review"
    mapped_evidence_changed = "mapped_evidence_changed"
    posting_closed = "posting_closed"


class ApplicationPackCreate(ApplicationPackContractModel):
    base_resume_version_id: OpaqueId
    owner_job_description: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_APPLICATION_PACK_JOB_DESCRIPTION_CHARS,
        description=(
            "Exact owner-supplied job description. Accepted only when the pinned "
            "posting version has no persisted description."
        ),
    )

    @field_validator("owner_job_description")
    @classmethod
    def owner_description_has_visible_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("owner_job_description must contain non-whitespace text")
        return value


class ApplicationPackEvidenceReference(ApplicationPackContractModel):
    id: OpaqueId
    version: StrictInt = Field(ge=1)


class ApplicationPackEvidenceSnapshot(ApplicationPackContractModel):
    id: OpaqueId
    version: StrictInt = Field(ge=1)
    statement: str = Field(min_length=1, max_length=1_000)
    source_resume_version_id: OpaqueId | None = None
    source_excerpt: str | None = Field(default=None, min_length=1, max_length=2_000)
    skills: list[str] = Field(default_factory=list, max_length=30)
    approved_at: UTCDateTime


class ApplicationPackRequirementReview(ApplicationPackContractModel):
    id: OpaqueId
    ordinal: StrictInt = Field(ge=1, le=MAX_APPLICATION_PACK_REQUIREMENTS)
    importance: ApplicationPackRequirementImportance
    text: str = Field(min_length=1, max_length=MAX_APPLICATION_PACK_REQUIREMENT_CHARS)
    source_start: StrictInt = Field(ge=0, le=MAX_APPLICATION_PACK_JOB_DESCRIPTION_CHARS)
    source_end: StrictInt = Field(ge=1, le=MAX_APPLICATION_PACK_JOB_DESCRIPTION_CHARS)
    coverage: ApplicationPackRequirementCoverage
    evidence_refs: list[ApplicationPackEvidenceReference] = Field(
        default_factory=list,
        max_length=MAX_APPLICATION_PACK_EVIDENCE_PER_REQUIREMENT,
    )

    @model_validator(mode="after")
    def review_shape_is_consistent(self) -> Self:
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        evidence_ids = [item.id for item in self.evidence_refs]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_refs must not contain duplicate evidence")
        if self.coverage in {
            ApplicationPackRequirementCoverage.supported,
            ApplicationPackRequirementCoverage.partial,
        } and not self.evidence_refs:
            raise ValueError(f"{self.coverage.value} requirements require evidence_refs")
        if (
            self.coverage is ApplicationPackRequirementCoverage.unsupported
            and self.evidence_refs
        ):
            raise ValueError("unsupported requirements cannot include evidence_refs")
        return self


class ApplicationPackRevisionCreate(ApplicationPackContractModel):
    parent_revision_id: OpaqueId
    requirements: list[ApplicationPackRequirementReview] = Field(
        min_length=1,
        max_length=MAX_APPLICATION_PACK_REQUIREMENTS,
    )

    @model_validator(mode="after")
    def full_review_is_unique(self) -> Self:
        requirement_ids = [item.id for item in self.requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("requirements must not contain duplicate ids")
        ordinals = [item.ordinal for item in self.requirements]
        if len(ordinals) != len(set(ordinals)):
            raise ValueError("requirements must not contain duplicate ordinals")
        spans = [(item.source_start, item.source_end) for item in self.requirements]
        if len(spans) != len(set(spans)):
            raise ValueError("requirements must not contain duplicate source spans")
        return self


class ApplicationPackReviewedEventCreate(ApplicationPackContractModel):
    event_type: Literal["reviewed"]
    revision_id: OpaqueId
    confirm_requirements_reviewed: Literal[True]

    @field_validator("confirm_requirements_reviewed", mode="before")
    @classmethod
    def confirmation_is_boolean_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("confirm_requirements_reviewed must be the boolean true")
        return value


ApplicationPackEventCreate = ApplicationPackReviewedEventCreate


class ApplicationPackSummary(ApplicationPackContractModel):
    id: OpaqueId
    version: StrictInt = Field(ge=1)
    application_id: OpaqueId
    posting_version_id: OpaqueId
    base_resume_version_id: OpaqueId
    created_at: UTCDateTime
    updated_at: UTCDateTime

    @model_validator(mode="after")
    def timestamps_are_ordered(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        return self


class ApplicationPackRequirementResponse(ApplicationPackContractModel):
    id: OpaqueId
    ordinal: StrictInt = Field(ge=1, le=MAX_APPLICATION_PACK_REQUIREMENTS)
    importance: ApplicationPackRequirementImportance
    text: str = Field(min_length=1, max_length=MAX_APPLICATION_PACK_REQUIREMENT_CHARS)
    source_start: StrictInt = Field(ge=0, le=MAX_APPLICATION_PACK_JOB_DESCRIPTION_CHARS)
    source_end: StrictInt = Field(ge=1, le=MAX_APPLICATION_PACK_JOB_DESCRIPTION_CHARS)
    coverage: ApplicationPackRequirementCoverage
    evidence: list[ApplicationPackEvidenceSnapshot] = Field(
        default_factory=list,
        max_length=MAX_APPLICATION_PACK_EVIDENCE_PER_REQUIREMENT,
    )

    @model_validator(mode="after")
    def response_shape_is_consistent(self) -> Self:
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        evidence_ids = [item.id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence must not contain duplicates")
        if self.coverage in {
            ApplicationPackRequirementCoverage.supported,
            ApplicationPackRequirementCoverage.partial,
        } and not self.evidence:
            raise ValueError(f"{self.coverage.value} requirements require evidence")
        if self.coverage is ApplicationPackRequirementCoverage.unsupported and self.evidence:
            raise ValueError("unsupported requirements cannot include evidence")
        return self


class ApplicationPackRevisionResponse(ApplicationPackContractModel):
    id: OpaqueId
    application_pack_id: OpaqueId
    parent_revision_id: OpaqueId | None
    revision_number: StrictInt = Field(ge=1)
    source: ApplicationPackRevisionSource
    extraction_version: Literal["requirements-v1"]
    job_description_source: ApplicationPackDescriptionSource
    job_description: str = Field(
        min_length=1,
        max_length=MAX_APPLICATION_PACK_JOB_DESCRIPTION_CHARS,
    )
    requirements: list[ApplicationPackRequirementResponse] = Field(
        default_factory=list,
        max_length=MAX_APPLICATION_PACK_REQUIREMENTS,
    )
    created_at: UTCDateTime

    @model_validator(mode="after")
    def requirements_are_one_ordered_set(self) -> Self:
        ids = [item.id for item in self.requirements]
        ordinals = [item.ordinal for item in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("requirements must not contain duplicate ids")
        if ordinals != sorted(ordinals) or len(ordinals) != len(set(ordinals)):
            raise ValueError("requirement ordinals must be unique and ordered")
        for requirement in self.requirements:
            source_text = self.job_description[
                requirement.source_start : requirement.source_end
            ]
            if source_text != requirement.text:
                raise ValueError("requirement text must match its exact source span")
        return self


class ApplicationPackEventResponse(ApplicationPackContractModel):
    id: OpaqueId
    application_pack_id: OpaqueId
    revision_id: OpaqueId
    sequence_number: StrictInt = Field(ge=1)
    event_type: Literal["reviewed"]
    occurred_at: UTCDateTime


class ApplicationPackResponse(ApplicationPackContractModel):
    data_source: Literal["database"] = "database"
    application_id: OpaqueId
    status: ApplicationPackStatus
    pack: ApplicationPackSummary | None = None
    current_revision: ApplicationPackRevisionResponse | None = None
    reviewed_revision: ApplicationPackRevisionResponse | None = None
    review_event: ApplicationPackEventResponse | None = None
    current_approved_evidence: list[AchievementEvidenceResponse] = Field(
        default_factory=list,
        max_length=MAX_APPLICATION_PACK_CURRENT_EVIDENCE,
    )
    blockers: list[ApplicationPackBlocker] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def aggregate_state_is_consistent(self) -> Self:
        if len(self.blockers) != len(set(self.blockers)):
            raise ValueError("blockers must not contain duplicates")
        if self.status is ApplicationPackStatus.not_started:
            if any(
                item is not None
                for item in (
                    self.pack,
                    self.current_revision,
                    self.reviewed_revision,
                    self.review_event,
                )
            ):
                raise ValueError("not_started cannot expose persisted pack resources")
            return self
        if self.pack is None or self.current_revision is None:
            raise ValueError("persisted pack states require pack and current_revision")
        if self.pack.application_id != self.application_id:
            raise ValueError("pack must belong to the requested application")
        if self.current_revision.application_pack_id != self.pack.id:
            raise ValueError("current_revision must belong to pack")
        if self.status is ApplicationPackStatus.draft:
            if (self.reviewed_revision is None) != (self.review_event is None):
                raise ValueError("latest reviewed revision and event must appear together")
            if (
                self.reviewed_revision is not None
                and self.review_event is not None
                and self.review_event.revision_id != self.reviewed_revision.id
            ):
                raise ValueError("review event must name reviewed_revision")
        else:
            if self.reviewed_revision is None or self.review_event is None:
                raise ValueError("reviewed packs require the exact review event and revision")
            if self.review_event.revision_id != self.reviewed_revision.id:
                raise ValueError("review event must name reviewed_revision")
            if self.reviewed_revision.id != self.current_revision.id:
                raise ValueError("reviewed_revision must remain the current revision")
        return self


__all__ = [
    "ApplicationPackBlocker",
    "APPLICATION_PACK_EXTRACTION_VERSION",
    "ApplicationPackCreate",
    "ApplicationPackDescriptionSource",
    "ApplicationPackEventCreate",
    "ApplicationPackEventResponse",
    "ApplicationPackEvidenceReference",
    "ApplicationPackEvidenceSnapshot",
    "ApplicationPackRequirementCoverage",
    "ApplicationPackRequirementImportance",
    "ApplicationPackRequirementResponse",
    "ApplicationPackRequirementReview",
    "ApplicationPackRevisionCreate",
    "ApplicationPackRevisionResponse",
    "ApplicationPackRevisionSource",
    "ApplicationPackStatus",
    "ApplicationPackSummary",
    "ApplicationPackResponse",
    "MAX_APPLICATION_PACK_JOB_DESCRIPTION_CHARS",
    "MAX_APPLICATION_PACK_REQUIREMENTS",
]
