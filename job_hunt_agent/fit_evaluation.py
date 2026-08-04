"""Provider-neutral, evidence-constrained model fit evaluation.

The model may interpret a job description and explain fit, but it cannot
override deterministic eligibility or cite evidence outside the approved
catalog. Runtime providers and durable caching are layered on this pure module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .opportunity_assessment import OpportunityAssessment


FIT_EVALUATION_POLICY_VERSION = "evidence-fit-policy-v1"
FitBand = Literal["strong", "promising", "stretch", "low", "insufficient_data"]
EvaluationMethod = Literal["model", "deterministic"]
FallbackReason = Literal[
    "prefiltered",
    "provider_unavailable",
    "invalid_verdict",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class FitEvaluationPosting(_StrictModel):
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=100_000)
    location: str | None = Field(default=None, min_length=1, max_length=500)
    employment_type: str | None = Field(default=None, min_length=1, max_length=80)


class FitEvaluationTarget(_StrictModel):
    role_families: tuple[str, ...] = Field(min_length=1, max_length=10)
    seniority_levels: tuple[str, ...] = Field(default=(), max_length=4)
    target_locations: tuple[str, ...] = Field(default=(), max_length=20)


class FitEvaluationAuthorization(_StrictModel):
    country_code: str = Field(min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")
    status: str = Field(min_length=1, max_length=40)


class FitEvaluationProfile(_StrictModel):
    career_thesis: str | None = Field(default=None, min_length=1, max_length=2_000)
    current_title: str | None = Field(default=None, min_length=1, max_length=200)
    current_location: str | None = Field(default=None, min_length=1, max_length=200)
    years_of_experience: float | None = Field(default=None, ge=0, le=60)
    skills: tuple[str, ...] = Field(default=(), max_length=80)
    work_authorizations: tuple[FitEvaluationAuthorization, ...] = Field(
        default=(),
        max_length=20,
    )
    work_modes: tuple[str, ...] = Field(default=(), max_length=3)
    employment_types: tuple[str, ...] = Field(default=(), max_length=3)


class FitEvaluationEvidence(_StrictModel):
    id: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$",
    )
    statement: str = Field(min_length=1, max_length=2_000)
    skills: tuple[str, ...] = Field(default=(), max_length=80)


class FitEvaluationInput(_StrictModel):
    posting: FitEvaluationPosting
    target: FitEvaluationTarget
    profile: FitEvaluationProfile
    evidence: tuple[FitEvaluationEvidence, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def evidence_ids_are_unique(self) -> "FitEvaluationInput":
        ids = [item.id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("approved evidence ids must be unique")
        return self


class FitVerdict(_StrictModel):
    """Exact structured output accepted from a fit-evaluation provider."""

    band: FitBand
    reasons: tuple[str, ...] = Field(min_length=1, max_length=3)
    gaps: tuple[str, ...] = Field(default=(), max_length=3)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def explanation_is_bounded_and_unique(self) -> "FitVerdict":
        for field_name in ("reasons", "gaps"):
            values = getattr(self, field_name)
            if any(not value.strip() or len(value) > 200 for value in values):
                raise ValueError(f"{field_name} must contain 1-200 character strings")
            if len(values) != len({value.strip().casefold() for value in values}):
                raise ValueError(f"{field_name} must not contain duplicates")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence_ids must not contain duplicates")
        return self


class FitEvaluationProvider(Protocol):
    def evaluate(self, inputs: FitEvaluationInput) -> FitVerdict:
        """Return one strict verdict without mutating application state."""


@dataclass(frozen=True)
class ResolvedFitEvaluation:
    policy_version: str
    method: EvaluationMethod
    band: FitBand
    reasons: tuple[str, ...]
    gaps: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    fallback_reason: FallbackReason | None = None


class InvalidFitVerdict(ValueError):
    """The provider returned a structurally valid but ungrounded verdict."""


def should_request_model(
    deterministic: OpportunityAssessment,
    inputs: FitEvaluationInput,
) -> bool:
    """Cheap, deterministic pre-filter before any provider call."""

    return bool(
        deterministic.eligibility != "likely_ineligible"
        and deterministic.fit_band != "insufficient_data"
        and len(inputs.posting.description.strip()) >= 200
    )


def evaluate_fit_with_fallback(
    *,
    provider: FitEvaluationProvider,
    inputs: FitEvaluationInput,
    deterministic: OpportunityAssessment,
) -> ResolvedFitEvaluation:
    """Run one provider call or return the deterministic result safely."""

    if not should_request_model(deterministic, inputs):
        return _deterministic_result(deterministic, reason="prefiltered")
    try:
        verdict = provider.evaluate(inputs)
    except Exception:  # noqa: BLE001 - provider failures are a supported fallback.
        return _deterministic_result(deterministic, reason="provider_unavailable")
    try:
        return merge_fit_verdict(
            deterministic=deterministic,
            inputs=inputs,
            verdict=verdict,
        )
    except InvalidFitVerdict:
        return _deterministic_result(deterministic, reason="invalid_verdict")


def merge_fit_verdict(
    *,
    deterministic: OpportunityAssessment,
    inputs: FitEvaluationInput,
    verdict: FitVerdict,
) -> ResolvedFitEvaluation:
    """Apply deterministic safety gates to a validated provider verdict."""

    allowed_ids = {item.id for item in inputs.evidence}
    unknown_ids = set(verdict.evidence_ids) - allowed_ids
    if unknown_ids:
        raise InvalidFitVerdict("verdict cited evidence outside the approved catalog")

    band = verdict.band
    if deterministic.eligibility == "likely_ineligible":
        band = "low"
    elif band == "strong" and (
        deterministic.eligibility != "eligible"
        or deterministic.confidence == "low"
        or not verdict.evidence_ids
    ):
        band = "promising"

    return ResolvedFitEvaluation(
        policy_version=FIT_EVALUATION_POLICY_VERSION,
        method="model",
        band=band,
        reasons=tuple(value.strip() for value in verdict.reasons),
        gaps=tuple(value.strip() for value in verdict.gaps),
        evidence_ids=verdict.evidence_ids,
    )


def _deterministic_result(
    deterministic: OpportunityAssessment,
    *,
    reason: FallbackReason,
) -> ResolvedFitEvaluation:
    return ResolvedFitEvaluation(
        policy_version=FIT_EVALUATION_POLICY_VERSION,
        method="deterministic",
        band=deterministic.fit_band,
        reasons=deterministic.strengths,
        gaps=deterministic.gaps,
        evidence_ids=deterministic.approved_evidence_ids,
        fallback_reason=reason,
    )


__all__ = [
    "FIT_EVALUATION_POLICY_VERSION",
    "FitEvaluationAuthorization",
    "FitEvaluationEvidence",
    "FitEvaluationInput",
    "FitEvaluationPosting",
    "FitEvaluationProfile",
    "FitEvaluationProvider",
    "FitEvaluationTarget",
    "FitVerdict",
    "InvalidFitVerdict",
    "ResolvedFitEvaluation",
    "evaluate_fit_with_fallback",
    "merge_fit_verdict",
    "should_request_model",
]
