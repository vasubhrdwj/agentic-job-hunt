"""Strict contracts for deterministic, evidence-grounded application artifacts."""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from .application_pack_schemas import (
    ApplicationPackEvidenceReference,
    ApplicationPackEvidenceSnapshot,
    ApplicationPackRequirementResponse,
    ApplicationPackSummary,
)
from .application_schemas import OpaqueId, UTCDateTime
from .profile_schemas import ResumeVersionSummary
from .security import MAX_RESUME_CHARS


APPLICATION_ARTIFACT_SCHEMA_VERSION = "artifacts-v1"
APPLICATION_ARTIFACT_GENERATOR_VERSION = "application-artifacts-deterministic-v1"
APPLICATION_ARTIFACT_DIFF_VERSION = "line-diff-v1"
MAX_APPLICATION_ARTIFACT_QUESTIONS = 20
MAX_APPLICATION_ARTIFACT_SELECTED_EVIDENCE = 5
MAX_APPLICATION_ARTIFACT_NOTE_CHARS = 5_000
MAX_APPLICATION_ARTIFACT_ANSWER_CHARS = 4_000


class ApplicationArtifactContractModel(BaseModel):
    """Reject ignored fields while preserving exact owner-entered text."""

    model_config = ConfigDict(extra="forbid")


class ApplicationArtifactStatus(str, Enum):
    not_started = "not_started"
    draft = "draft"
    approved = "approved"


class ApplicationArtifactAnswerStatus(str, Enum):
    answered = "answered"
    needs_owner_input = "needs_owner_input"


class ApplicationArtifactBlocker(str, Enum):
    application_pack_missing = "application_pack_missing"
    grounding_review_required = "grounding_review_required"
    posting_closed = "posting_closed"
    grounded_evidence_missing = "grounded_evidence_missing"
    grounding_evidence_changed = "grounding_evidence_changed"
    questions_need_owner_input = "questions_need_owner_input"
    tailored_resume_unchanged = "tailored_resume_unchanged"
    current_revision_rejected = "current_revision_rejected"


class ApplicationArtifactQuestion(ApplicationArtifactContractModel):
    id: OpaqueId
    text: str = Field(min_length=1, max_length=2_000)
    character_limit: StrictInt | None = Field(default=None, ge=1, le=10_000)
    evidence_refs: list[ApplicationPackEvidenceReference] = Field(
        default_factory=list,
        max_length=3,
    )

    @field_validator("text")
    @classmethod
    def exact_question_has_visible_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question text must contain non-whitespace text")
        return value

    @model_validator(mode="after")
    def evidence_is_unique(self) -> Self:
        ids = [item.id for item in self.evidence_refs]
        if len(ids) != len(set(ids)):
            raise ValueError("question evidence_refs must not contain duplicates")
        return self


class ApplicationArtifactRevisionCreate(ApplicationArtifactContractModel):
    operation: Literal["generate"] = "generate"
    grounding_revision_id: OpaqueId
    parent_artifact_revision_id: OpaqueId | None = None
    generation_mode: Literal["deterministic"] = "deterministic"
    selected_evidence_refs: list[ApplicationPackEvidenceReference] | None = Field(
        default=None,
        max_length=MAX_APPLICATION_ARTIFACT_SELECTED_EVIDENCE,
    )
    questions: list[ApplicationArtifactQuestion] = Field(
        default_factory=list,
        max_length=MAX_APPLICATION_ARTIFACT_QUESTIONS,
    )

    @model_validator(mode="after")
    def ids_and_versions_are_consistent(self) -> Self:
        question_ids = [item.id for item in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("questions must not contain duplicate ids")
        if self.selected_evidence_refs is not None:
            ids = [item.id for item in self.selected_evidence_refs]
            if len(ids) != len(set(ids)):
                raise ValueError("selected_evidence_refs must not contain duplicates")
        versions: dict[str, int] = {}
        refs = list(self.selected_evidence_refs or [])
        refs.extend(ref for item in self.questions for ref in item.evidence_refs)
        for ref in refs:
            previous = versions.setdefault(ref.id, ref.version)
            if previous != ref.version:
                raise ValueError("one evidence id cannot be referenced at multiple versions")
        return self


class ApplicationArtifactEvidenceClaimSource(ApplicationArtifactContractModel):
    kind: Literal["evidence_snapshot"] = "evidence_snapshot"
    evidence_id: OpaqueId
    evidence_version: StrictInt = Field(ge=1)
    quote: str = Field(min_length=1, max_length=1_000)


class ApplicationArtifactJobDescriptionClaimSource(ApplicationArtifactContractModel):
    kind: Literal["job_description_span"] = "job_description_span"
    grounding_revision_id: OpaqueId
    source_start: StrictInt = Field(ge=0, le=100_000)
    source_end: StrictInt = Field(ge=1, le=100_000)
    quote: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def source_span_is_ordered(self) -> Self:
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self


class ApplicationArtifactPostingFieldClaimSource(ApplicationArtifactContractModel):
    kind: Literal["posting_field"] = "posting_field"
    posting_version_id: OpaqueId
    field: Literal["company_name", "title"]
    value: str = Field(min_length=1, max_length=300)


ApplicationArtifactClaimSource = Annotated[
    ApplicationArtifactEvidenceClaimSource
    | ApplicationArtifactJobDescriptionClaimSource
    | ApplicationArtifactPostingFieldClaimSource,
    Field(discriminator="kind"),
]


class ApplicationArtifactClaim(ApplicationArtifactContractModel):
    id: OpaqueId
    start: StrictInt = Field(ge=0, le=MAX_RESUME_CHARS)
    end: StrictInt = Field(ge=1, le=MAX_RESUME_CHARS)
    text: str = Field(min_length=1, max_length=2_000)
    derivation: Literal["verbatim"] = "verbatim"
    sources: list[ApplicationArtifactClaimSource] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def claim_span_is_ordered(self) -> Self:
        if self.end <= self.start:
            raise ValueError("claim end must be greater than start")
        return self


class ApplicationArtifactDocument(ApplicationArtifactContractModel):
    text: str = Field(max_length=MAX_RESUME_CHARS)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    claims: list[ApplicationArtifactClaim] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def content_and_claims_match(self) -> Self:
        if hashlib.sha256(self.text.encode("utf-8")).hexdigest() != self.content_hash:
            raise ValueError("document content_hash does not match text")
        ids = [item.id for item in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError("document claims must not contain duplicate ids")
        previous_end = 0
        for claim in sorted(self.claims, key=lambda item: (item.start, item.end)):
            if claim.start < previous_end:
                raise ValueError("document claims must not overlap")
            if self.text[claim.start : claim.end] != claim.text:
                raise ValueError("claim text must match its exact document span")
            previous_end = claim.end
        return self


class ApplicationArtifactAnswer(ApplicationArtifactDocument):
    id: OpaqueId
    question_id: OpaqueId
    status: ApplicationArtifactAnswerStatus

    @model_validator(mode="after")
    def answer_state_matches_text(self) -> Self:
        if self.status is ApplicationArtifactAnswerStatus.needs_owner_input:
            if self.text or self.claims:
                raise ValueError("needs_owner_input answers must be empty")
        elif not self.text.strip() or not self.claims:
            raise ValueError("answered questions require grounded answer text")
        return self


class ApplicationArtifactDiffLine(ApplicationArtifactContractModel):
    operation: Literal["equal", "delete", "insert"]
    text: str
    base_line_number: StrictInt | None = Field(default=None, ge=1)
    tailored_line_number: StrictInt | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def line_numbers_match_operation(self) -> Self:
        if self.operation == "equal" and (
            self.base_line_number is None or self.tailored_line_number is None
        ):
            raise ValueError("equal diff lines require both line numbers")
        if self.operation == "delete" and (
            self.base_line_number is None or self.tailored_line_number is not None
        ):
            raise ValueError("delete diff lines require only a base line number")
        if self.operation == "insert" and (
            self.base_line_number is not None or self.tailored_line_number is None
        ):
            raise ValueError("insert diff lines require only a tailored line number")
        return self


class ApplicationArtifactDiff(ApplicationArtifactContractModel):
    algorithm_version: Literal["line-diff-v1"] = APPLICATION_ARTIFACT_DIFF_VERSION
    base_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tailored_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    lines: list[ApplicationArtifactDiffLine] = Field(default_factory=list, max_length=20_000)

    @model_validator(mode="after")
    def exact_diff_reconstructs_both_documents(self) -> Self:
        base = "".join(
            item.text for item in self.lines if item.operation in {"equal", "delete"}
        )
        tailored = "".join(
            item.text for item in self.lines if item.operation in {"equal", "insert"}
        )
        if hashlib.sha256(base.encode("utf-8")).hexdigest() != self.base_content_hash:
            raise ValueError("diff does not reconstruct the base resume")
        if hashlib.sha256(tailored.encode("utf-8")).hexdigest() != self.tailored_content_hash:
            raise ValueError("diff does not reconstruct the tailored resume")
        return self


class ApplicationArtifactRevisionResponse(ApplicationArtifactContractModel):
    id: OpaqueId
    application_pack_id: OpaqueId
    grounding_revision_id: OpaqueId
    grounding_review_event_id: OpaqueId
    parent_artifact_revision_id: OpaqueId | None
    revision_number: StrictInt = Field(ge=1)
    source: Literal["deterministic"]
    generator_version: Literal["application-artifacts-deterministic-v1"]
    selected_evidence: list[ApplicationPackEvidenceSnapshot] = Field(
        default_factory=list,
        max_length=MAX_APPLICATION_ARTIFACT_SELECTED_EVIDENCE,
    )
    questions: list[ApplicationArtifactQuestion] = Field(
        default_factory=list,
        max_length=MAX_APPLICATION_ARTIFACT_QUESTIONS,
    )
    tailored_resume: ApplicationArtifactDocument
    company_note: ApplicationArtifactDocument
    answers: list[ApplicationArtifactAnswer] = Field(
        default_factory=list,
        max_length=MAX_APPLICATION_ARTIFACT_QUESTIONS,
    )
    diff: ApplicationArtifactDiff
    created_at: UTCDateTime

    @model_validator(mode="after")
    def questions_and_answers_are_one_exact_set(self) -> Self:
        question_ids = [item.id for item in self.questions]
        answer_question_ids = [item.question_id for item in self.answers]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("questions must not contain duplicate ids")
        if answer_question_ids != question_ids:
            raise ValueError("answers must preserve exact question order and ids")
        if self.diff.tailored_content_hash != self.tailored_resume.content_hash:
            raise ValueError("diff must name the exact tailored resume")
        return self


class ApplicationArtifactEventCreate(ApplicationArtifactContractModel):
    event_type: Literal["approved", "rejected"]
    artifact_revision_id: OpaqueId
    confirm_artifacts_reviewed: Literal[True] | None = None

    @model_validator(mode="after")
    def approval_requires_explicit_confirmation(self) -> Self:
        if self.event_type == "approved" and self.confirm_artifacts_reviewed is not True:
            raise ValueError("approved events require confirm_artifacts_reviewed=true")
        if self.event_type == "rejected" and self.confirm_artifacts_reviewed is not None:
            raise ValueError("rejected events do not accept review confirmation")
        return self


class ApplicationArtifactEventResponse(ApplicationArtifactContractModel):
    id: OpaqueId
    application_pack_id: OpaqueId
    artifact_revision_id: OpaqueId
    sequence_number: StrictInt = Field(ge=1)
    event_type: Literal["approved", "rejected"]
    tailored_resume_version_id: OpaqueId | None
    occurred_at: UTCDateTime

    @model_validator(mode="after")
    def event_resume_shape_is_valid(self) -> Self:
        if (self.event_type == "approved") != (self.tailored_resume_version_id is not None):
            raise ValueError("only approved events name a tailored resume")
        return self


class ApplicationArtifactSourceCatalog(ApplicationArtifactContractModel):
    reviewed_grounding_revision_id: OpaqueId
    reviewed_grounding_revision_number: StrictInt = Field(ge=1)
    reviewed_grounding_event_id: OpaqueId
    evidence: list[ApplicationPackEvidenceSnapshot] = Field(
        default_factory=list,
        max_length=500,
    )
    unsupported_requirements: list[ApplicationPackRequirementResponse] = Field(
        default_factory=list,
        max_length=40,
    )


class ApplicationArtifactsResponse(ApplicationArtifactContractModel):
    data_source: Literal["database"] = "database"
    application_id: OpaqueId
    status: ApplicationArtifactStatus
    pack: ApplicationPackSummary | None = None
    source_catalog: ApplicationArtifactSourceCatalog | None = None
    current_revision: ApplicationArtifactRevisionResponse | None = None
    current_event: ApplicationArtifactEventResponse | None = None
    approved_revision: ApplicationArtifactRevisionResponse | None = None
    approval_event: ApplicationArtifactEventResponse | None = None
    tailored_resume_version: ResumeVersionSummary | None = None
    blockers: list[ApplicationArtifactBlocker] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def projection_is_consistent(self) -> Self:
        if len(self.blockers) != len(set(self.blockers)):
            raise ValueError("blockers must not contain duplicates")
        if self.pack is None:
            if any(
                item is not None
                for item in (
                    self.source_catalog,
                    self.current_revision,
                    self.current_event,
                    self.approved_revision,
                    self.approval_event,
                    self.tailored_resume_version,
                )
            ):
                raise ValueError("artifact resources require an application pack")
            if self.status is not ApplicationArtifactStatus.not_started:
                raise ValueError("missing application packs are not_started")
            return self
        if self.current_event is not None and (
            self.current_revision is None
            or self.current_event.artifact_revision_id != self.current_revision.id
        ):
            raise ValueError("current_event must name current_revision")
        approved_items = (self.approved_revision, self.approval_event)
        if (approved_items[0] is None) != (approved_items[1] is None):
            raise ValueError("approved revision and event must appear together")
        if self.approved_revision is not None and self.approval_event is not None:
            if self.approval_event.event_type != "approved":
                raise ValueError("approval_event must be approved")
            if self.approval_event.artifact_revision_id != self.approved_revision.id:
                raise ValueError("approval_event must name approved_revision")
            if self.approval_event.tailored_resume_version_id != (
                self.tailored_resume_version.id if self.tailored_resume_version else None
            ):
                raise ValueError("tailored resume must match approval event")
        if self.status is ApplicationArtifactStatus.approved:
            if self.current_revision is None or self.current_event is None:
                raise ValueError("approved status requires a current approval")
            if self.current_event.event_type != "approved":
                raise ValueError("approved status requires an approved event")
        return self


__all__ = [
    "APPLICATION_ARTIFACT_DIFF_VERSION",
    "APPLICATION_ARTIFACT_GENERATOR_VERSION",
    "APPLICATION_ARTIFACT_SCHEMA_VERSION",
    "ApplicationArtifactAnswer",
    "ApplicationArtifactAnswerStatus",
    "ApplicationArtifactBlocker",
    "ApplicationArtifactClaim",
    "ApplicationArtifactClaimSource",
    "ApplicationArtifactContractModel",
    "ApplicationArtifactDiff",
    "ApplicationArtifactDiffLine",
    "ApplicationArtifactDocument",
    "ApplicationArtifactEventCreate",
    "ApplicationArtifactEventResponse",
    "ApplicationArtifactEvidenceClaimSource",
    "ApplicationArtifactJobDescriptionClaimSource",
    "ApplicationArtifactPostingFieldClaimSource",
    "ApplicationArtifactQuestion",
    "ApplicationArtifactRevisionCreate",
    "ApplicationArtifactRevisionResponse",
    "ApplicationArtifactStatus",
    "ApplicationArtifactSourceCatalog",
    "ApplicationArtifactsResponse",
]
