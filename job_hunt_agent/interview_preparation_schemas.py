"""Strict contracts for deterministic, evidence-only interview preparation."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from .application_pack_schemas import (
    ApplicationPackEvidenceReference,
    ApplicationPackEvidenceSnapshot,
)
from .application_schemas import OpaqueId, UTCDateTime


MAX_PREPARATION_PROMPTS = 12
MAX_STAR_SECTION_CHARS = 3_000


class InterviewPreparationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InterviewPreparationStatus(str, Enum):
    blocked = "blocked"
    not_started = "not_started"
    in_progress = "in_progress"
    ready = "ready"


class InterviewPreparationTargetKind(str, Enum):
    recruiter_screen = "recruiter_screen"
    interview_round = "interview_round"


class InterviewPreparationPromptCategory(str, Enum):
    role_motivation = "role_motivation"
    key_requirement = "key_requirement"
    impact = "impact"
    conflict_ambiguity = "conflict_ambiguity"
    failure_learning = "failure_learning"
    leadership_collaboration = "leadership_collaboration"


class InterviewPreparationMissingFact(str, Enum):
    situation_context = "situation_context"
    personal_responsibility = "personal_responsibility"
    specific_actions = "specific_actions"
    verified_result = "verified_result"
    motivation_connection = "motivation_connection"
    conflict_or_ambiguity_details = "conflict_or_ambiguity_details"
    setback_and_learning_details = "setback_and_learning_details"
    leadership_or_collaboration_details = "leadership_or_collaboration_details"


class InterviewPreparationBlocker(str, Enum):
    application_not_submitted = "application_not_submitted"
    application_closed = "application_closed"
    reviewed_application_pack_missing = "reviewed_application_pack_missing"
    approved_evidence_missing = "approved_evidence_missing"
    evidence_snapshot_changed = "evidence_snapshot_changed"
    required_requirement_evidence_missing = "required_requirement_evidence_missing"
    required_prompt_capacity_exceeded = "required_prompt_capacity_exceeded"


class InterviewPreparationRoleContext(InterviewPreparationContract):
    job_posting_id: OpaqueId
    posting_version_id: OpaqueId
    company: str = Field(min_length=1, max_length=240)
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(max_length=20_000)


class InterviewPreparationTarget(InterviewPreparationContract):
    kind: InterviewPreparationTargetKind
    label: str = Field(min_length=1, max_length=160)
    interview_round_id: OpaqueId | None = None
    interview_round_version: StrictInt | None = Field(default=None, ge=1)
    interview_round_kind: str | None = Field(default=None, min_length=1, max_length=32)
    scheduled_start_at: UTCDateTime | None = None
    scheduled_timezone: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def exact_round_shape(self) -> Self:
        round_values = (
            self.interview_round_id,
            self.interview_round_version,
            self.interview_round_kind,
            self.scheduled_start_at,
            self.scheduled_timezone,
        )
        if self.kind is InterviewPreparationTargetKind.recruiter_screen:
            if any(value is not None for value in round_values):
                raise ValueError("recruiter-screen preparation cannot name an interview round")
        elif any(value is None for value in round_values):
            raise ValueError("interview-round preparation requires the exact scheduled round")
        return self


class InterviewPreparationRequirement(InterviewPreparationContract):
    id: OpaqueId
    ordinal: StrictInt = Field(ge=1, le=40)
    importance: Literal["required", "preferred"]
    text: str = Field(min_length=1, max_length=2_000)
    coverage: Literal["supported", "partial", "unsupported", "needs_review"]
    evidence: list[ApplicationPackEvidenceSnapshot] = Field(default_factory=list, max_length=10)


class InterviewPreparationEvidenceGap(InterviewPreparationContract):
    requirement_id: OpaqueId
    importance: Literal["required", "preferred"]
    requirement_text: str = Field(min_length=1, max_length=2_000)
    reason: Literal["no_approved_evidence", "evidence_changed"]


class InterviewPreparationStarDraft(InterviewPreparationContract):
    situation: str = Field(default="", max_length=MAX_STAR_SECTION_CHARS)
    task: str = Field(default="", max_length=MAX_STAR_SECTION_CHARS)
    action: str = Field(default="", max_length=MAX_STAR_SECTION_CHARS)
    result: str = Field(default="", max_length=MAX_STAR_SECTION_CHARS)


class InterviewPreparationStartingDraft(InterviewPreparationContract):
    """Unsaved, deterministic source material for overcoming a blank page.

    Situation, task, and action stay empty because neither a role requirement nor an
    achievement statement proves those STAR facts. Result may contain one exact
    approved statement only when that statement explicitly describes an outcome.
    """

    generation_method: Literal["exact_sources_v1"] = "exact_sources_v1"
    source_requirement_id: OpaqueId
    source_evidence: list[ApplicationPackEvidenceReference] = Field(
        min_length=1,
        max_length=10,
    )
    result_evidence: ApplicationPackEvidenceReference | None = None
    draft: InterviewPreparationStarDraft
    missing_facts: list[InterviewPreparationMissingFact] = Field(
        min_length=1,
        max_length=8,
    )

    @model_validator(mode="after")
    def source_references_are_unique(self) -> Self:
        refs = [(item.id, item.version) for item in self.source_evidence]
        if len(refs) != len(set(refs)):
            raise ValueError("starting-draft evidence references must be unique")
        if self.result_evidence is not None and (
            self.result_evidence.id,
            self.result_evidence.version,
        ) not in set(refs):
            raise ValueError("result evidence must belong to the starting-draft sources")
        if len(self.missing_facts) != len(set(self.missing_facts)):
            raise ValueError("starting-draft missing facts must be unique")
        return self


class InterviewPreparationPromptDraftCreate(InterviewPreparationStarDraft):
    prompt_id: OpaqueId


class InterviewPreparationPrompt(InterviewPreparationContract):
    id: OpaqueId
    category: InterviewPreparationPromptCategory
    question: str = Field(min_length=1, max_length=1_000)
    requirement_id: OpaqueId | None = None
    requirement_text: str | None = Field(default=None, min_length=1, max_length=2_000)
    evidence: list[ApplicationPackEvidenceSnapshot] = Field(min_length=1, max_length=10)
    starting_draft: InterviewPreparationStartingDraft | None = None
    draft: InterviewPreparationStarDraft
    missing_sections: list[Literal["situation", "task", "action", "result"]] = Field(
        default_factory=list,
        max_length=4,
    )

    @model_validator(mode="after")
    def prompt_is_evidence_backed(self) -> Self:
        if (self.requirement_id is None) != (self.requirement_text is None):
            raise ValueError("prompt requirement id and text must appear together")
        expected = [
            field
            for field in ("situation", "task", "action", "result")
            if not getattr(self.draft, field).strip()
        ]
        if self.missing_sections != expected:
            raise ValueError("missing_sections must match the owner-authored STAR draft")
        if self.starting_draft is not None:
            if self.requirement_id is None or self.requirement_text is None:
                raise ValueError("a grounded starting draft requires an exact requirement")
            expected_starting_draft = build_grounded_starting_draft(
                category=self.category,
                requirement_id=self.requirement_id,
                evidence=self.evidence,
            )
            if self.starting_draft != expected_starting_draft:
                raise ValueError(
                    "starting_draft must contain only the exact pinned requirement and evidence"
                )
        return self


def build_grounded_starting_draft(
    *,
    category: InterviewPreparationPromptCategory,
    requirement_id: str,
    evidence: list[ApplicationPackEvidenceSnapshot],
) -> InterviewPreparationStartingDraft:
    """Build an auditable, non-inventive starter from exact approved sources."""

    if not evidence:
        raise ValueError("a grounded starting draft requires approved evidence")
    unique_evidence: list[ApplicationPackEvidenceSnapshot] = []
    seen: set[tuple[str, int]] = set()
    for item in evidence:
        key = (item.id, item.version)
        if key not in seen:
            unique_evidence.append(item)
            seen.add(key)

    result_source = next(
        (item for item in unique_evidence if _statement_explicitly_names_result(item.statement)),
        None,
    )
    missing_facts = [
        InterviewPreparationMissingFact.situation_context,
        InterviewPreparationMissingFact.personal_responsibility,
        InterviewPreparationMissingFact.specific_actions,
    ]
    if result_source is None:
        missing_facts.append(InterviewPreparationMissingFact.verified_result)
    category_gap = {
        InterviewPreparationPromptCategory.role_motivation: (
            InterviewPreparationMissingFact.motivation_connection
        ),
        InterviewPreparationPromptCategory.conflict_ambiguity: (
            InterviewPreparationMissingFact.conflict_or_ambiguity_details
        ),
        InterviewPreparationPromptCategory.failure_learning: (
            InterviewPreparationMissingFact.setback_and_learning_details
        ),
        InterviewPreparationPromptCategory.leadership_collaboration: (
            InterviewPreparationMissingFact.leadership_or_collaboration_details
        ),
    }.get(category)
    if category_gap is not None:
        missing_facts.append(category_gap)

    return InterviewPreparationStartingDraft(
        source_requirement_id=requirement_id,
        source_evidence=[
            ApplicationPackEvidenceReference(id=item.id, version=item.version)
            for item in unique_evidence
        ],
        result_evidence=(
            ApplicationPackEvidenceReference(
                id=result_source.id,
                version=result_source.version,
            )
            if result_source is not None
            else None
        ),
        draft=InterviewPreparationStarDraft(
            result=result_source.statement if result_source is not None else "",
        ),
        missing_facts=missing_facts,
    )


def _statement_explicitly_names_result(statement: str) -> bool:
    """Conservatively recognize an outcome without rewriting or interpreting it."""

    lowered = statement.casefold().strip()
    result_prefixes = (
        "achieved ",
        "accelerated ",
        "boosted ",
        "cut ",
        "decreased ",
        "delivered ",
        "eliminated ",
        "grew ",
        "improved ",
        "increased ",
        "lowered ",
        "prevented ",
        "raised ",
        "ranked ",
        "reached ",
        "reduced ",
        "saved ",
        "scaled ",
        "won ",
    )
    if lowered.startswith(result_prefixes):
        return True
    explicit_outcome_words = (
        " achieved ",
        " accelerated ",
        " boosted ",
        " decreased ",
        " eliminated ",
        " grew ",
        " improved ",
        " increased ",
        " lowered ",
        " prevented ",
        " raised ",
        " reduced ",
        " saved ",
    )
    result_connectors = (
        " resulting in ",
        " resulted in ",
        " leading to ",
        " led to ",
        " reducing ",
        " increasing ",
        " improving ",
        " cutting ",
        " saving ",
    )
    return (
        any(word in f" {lowered} " for word in explicit_outcome_words)
        or any(connector in f" {lowered} " for connector in result_connectors)
        or _statement_names_numeric_change(statement)
        or (
            lowered.startswith("top ")
            and any(character.isdigit() for character in lowered[4:12])
        )
    )


def _statement_names_numeric_change(statement: str) -> bool:
    """Accept metric transitions while rejecting architectural arrow diagrams."""

    parts = statement.replace("→", "->").split("->")
    return any(
        any(character.isdigit() for character in left[-32:])
        and any(character.isdigit() for character in right[:32])
        for left, right in zip(parts, parts[1:], strict=False)
    )


class InterviewPreparationRevisionSummary(InterviewPreparationContract):
    id: OpaqueId
    revision_number: StrictInt = Field(ge=1)
    parent_revision_id: OpaqueId | None = None
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    recording_method: Literal["owner_authored"]
    created_at: UTCDateTime


class InterviewPreparationRevisionCreate(InterviewPreparationContract):
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_revision_id: OpaqueId | None = None
    prompt_drafts: list[InterviewPreparationPromptDraftCreate] = Field(
        min_length=1,
        max_length=MAX_PREPARATION_PROMPTS,
    )
    confirm_owner_authored: Literal[True]

    @field_validator("confirm_owner_authored", mode="before")
    @classmethod
    def confirmation_is_boolean_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("confirm_owner_authored must be the boolean true")
        return value

    @model_validator(mode="after")
    def prompt_drafts_are_unique_and_nonempty(self) -> Self:
        ids = [item.prompt_id for item in self.prompt_drafts]
        if len(ids) != len(set(ids)):
            raise ValueError("prompt_drafts must not contain duplicate prompt ids")
        if not any(
            value.strip()
            for item in self.prompt_drafts
            for value in (item.situation, item.task, item.action, item.result)
        ):
            raise ValueError("at least one STAR section must contain owner-authored text")
        return self


class ApplicationInterviewPreparationResponse(InterviewPreparationContract):
    data_source: Literal["database"] = "database"
    generation_method: Literal["deterministic_scaffold"] = "deterministic_scaffold"
    truth_policy: Literal["owner_authored_only"] = "owner_authored_only"
    application_id: OpaqueId
    application_version: StrictInt = Field(ge=1)
    application_submission_id: OpaqueId | None = None
    preparation_id: OpaqueId | None = None
    preparation_version: StrictInt | None = Field(default=None, ge=1)
    write_version_scope: Literal["application", "preparation"]
    write_version: StrictInt = Field(ge=1)
    status: InterviewPreparationStatus
    source_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    role: InterviewPreparationRoleContext
    target: InterviewPreparationTarget
    grounding_revision_id: OpaqueId | None = None
    latest_revision: InterviewPreparationRevisionSummary | None = None
    requirements: list[InterviewPreparationRequirement] = Field(default_factory=list, max_length=40)
    required_evidence_backed_count: StrictInt = Field(ge=0, le=40)
    prompt_capacity: Literal[12] = MAX_PREPARATION_PROMPTS
    evidence_gaps: list[InterviewPreparationEvidenceGap] = Field(default_factory=list, max_length=40)
    prompts: list[InterviewPreparationPrompt] = Field(
        default_factory=list,
        max_length=MAX_PREPARATION_PROMPTS,
    )
    previous_context_stale: bool = False
    previous_prompts: list[InterviewPreparationPrompt] = Field(
        default_factory=list,
        max_length=MAX_PREPARATION_PROMPTS,
        description=(
            "Read-only owner-authored prompts from the latest saved revision when "
            "its pinned context no longer matches the current target."
        ),
    )
    blockers: list[InterviewPreparationBlocker] = Field(default_factory=list, max_length=10)
    next_steps: list[str] = Field(default_factory=list, max_length=10)
    disclaimer: Literal[
        "Grounded starters quote only exact approved evidence and never guess missing STAR facts. They are not saved or approved unless you deliberately use, verify, and save them."
    ] = (
        "Grounded starters quote only exact approved evidence and never guess missing STAR facts. They are not saved or approved unless you deliberately use, verify, and save them."
    )

    @model_validator(mode="after")
    def aggregate_shape_is_safe(self) -> Self:
        if len(self.blockers) != len(set(self.blockers)):
            raise ValueError("blockers must not contain duplicates")
        if self.preparation_id is None:
            if self.preparation_version is not None or self.latest_revision is not None:
                raise ValueError("unsaved preparation cannot expose persisted versions")
            if self.write_version_scope != "application":
                raise ValueError("new preparation must use the application version")
        else:
            if self.preparation_version is None or self.write_version_scope != "preparation":
                raise ValueError("saved preparation must use its own version")
            if self.write_version != self.preparation_version:
                raise ValueError("write_version must match preparation_version")
        if self.status is InterviewPreparationStatus.blocked and not self.blockers:
            raise ValueError("blocked preparation must explain its blockers")
        if self.status is not InterviewPreparationStatus.blocked and self.blockers:
            raise ValueError("unblocked preparation cannot expose hard blockers")
        if self.prompts and any(not prompt.evidence for prompt in self.prompts):
            raise ValueError("every suggested story must preserve approved evidence")
        required_evidence_backed_count = sum(
            requirement.importance == "required" and bool(requirement.evidence)
            for requirement in self.requirements
        )
        if self.required_evidence_backed_count != required_evidence_backed_count:
            raise ValueError(
                "required_evidence_backed_count must match the pinned requirements"
            )
        capacity_exceeded = (
            InterviewPreparationBlocker.required_prompt_capacity_exceeded
            in self.blockers
        )
        if capacity_exceeded != (
            self.required_evidence_backed_count > self.prompt_capacity
        ):
            raise ValueError(
                "required prompt capacity blocker must match the exact requirement count"
            )
        if self.previous_context_stale != bool(self.previous_prompts):
            raise ValueError("stale context must expose its read-only prior prompts")
        return self


__all__ = [
    "ApplicationInterviewPreparationResponse",
    "InterviewPreparationBlocker",
    "InterviewPreparationEvidenceGap",
    "InterviewPreparationMissingFact",
    "InterviewPreparationPrompt",
    "InterviewPreparationPromptCategory",
    "InterviewPreparationPromptDraftCreate",
    "InterviewPreparationRequirement",
    "InterviewPreparationRevisionCreate",
    "InterviewPreparationRevisionSummary",
    "InterviewPreparationRoleContext",
    "InterviewPreparationStarDraft",
    "InterviewPreparationStartingDraft",
    "InterviewPreparationStatus",
    "InterviewPreparationTarget",
    "InterviewPreparationTargetKind",
    "MAX_PREPARATION_PROMPTS",
    "build_grounded_starting_draft",
]
