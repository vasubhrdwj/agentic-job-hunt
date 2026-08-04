from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from job_hunt_agent.fit_evaluation import (
    FitEvaluationEvidence,
    FitEvaluationInput,
    FitEvaluationPosting,
    FitEvaluationProfile,
    FitEvaluationTarget,
    FitVerdict,
    InvalidFitVerdict,
    evaluate_fit_with_fallback,
    merge_fit_verdict,
    should_request_model,
)
from job_hunt_agent.opportunity_assessment import OpportunityAssessment


def _inputs() -> FitEvaluationInput:
    return FitEvaluationInput(
        posting=FitEvaluationPosting(
            title="Backend Engineer",
            description="Build reliable APIs, event pipelines, and distributed systems. " * 6,
            location="India",
            employment_type="full_time",
        ),
        target=FitEvaluationTarget(
            role_families=("Backend Engineer",),
            seniority_levels=("junior", "mid"),
            target_locations=("India",),
        ),
        profile=FitEvaluationProfile(
            current_title="Software Engineer",
            current_location="India",
            years_of_experience=1,
            skills=("AWS", "Kafka", "REST"),
            employment_types=("full_time",),
        ),
        evidence=(
            FitEvaluationEvidence(
                id="event-pipeline",
                statement="Owned a production AWS event pipeline with retry and DLQ handling.",
                skills=("AWS", "Kafka", "DLQ"),
            ),
        ),
    )


def _deterministic() -> OpportunityAssessment:
    return OpportunityAssessment(
        algorithm_version="backend-opportunity-fit-v5",
        fit_band="promising",
        confidence="medium",
        eligibility="eligible",
        matched_terms=("AWS", "Kafka"),
        representative_requirement="Build reliable event pipelines.",
        approved_evidence_ids=("event-pipeline",),
        strengths=("Approved event-pipeline evidence supports the role.",),
        gaps=("Compensation was not provided.",),
    )


class _Provider:
    def __init__(self, verdict: FitVerdict | Exception) -> None:
        self.verdict = verdict
        self.calls = 0

    def evaluate(self, inputs: FitEvaluationInput) -> FitVerdict:
        self.calls += 1
        if isinstance(self.verdict, Exception):
            raise self.verdict
        return self.verdict


def test_verdict_contract_is_strict_bounded_and_deduplicated() -> None:
    with pytest.raises(ValidationError, match="extra"):
        FitVerdict.model_validate(
            {
                "band": "strong",
                "reasons": ["Grounded reason"],
                "gaps": [],
                "evidence_ids": [],
                "score": 92,
            }
        )
    with pytest.raises(ValidationError, match="duplicates"):
        FitVerdict(
            band="promising",
            reasons=("Same reason", "same reason"),
        )
    with pytest.raises(ValidationError, match="1-200"):
        FitVerdict(band="promising", reasons=("x" * 201,))
    with pytest.raises(ValidationError, match="at most 3"):
        FitVerdict(
            band="promising",
            reasons=("one", "two", "three", "four"),
        )


def test_fit_contract_normalizes_whitespace_and_uses_public_id_limit() -> None:
    verdict = FitVerdict(
        band="promising",
        reasons=("  Grounded reason  ",),
    )

    assert verdict.reasons == ("Grounded reason",)
    with pytest.raises(ValidationError):
        FitEvaluationEvidence(
            id="e" * 33,
            statement="Approved evidence",
        )


def test_model_verdict_must_cite_only_approved_evidence() -> None:
    with pytest.raises(InvalidFitVerdict, match="approved catalog"):
        merge_fit_verdict(
            deterministic=_deterministic(),
            inputs=_inputs(),
            verdict=FitVerdict(
                band="strong",
                reasons=("The profile shows relevant production ownership.",),
                evidence_ids=("invented-evidence",),
            ),
        )


def test_strong_requires_eligible_supported_nonlow_inputs() -> None:
    verdict = FitVerdict(
        band="strong",
        reasons=("Production pipeline ownership directly supports the role.",),
        evidence_ids=("event-pipeline",),
    )
    accepted = merge_fit_verdict(
        deterministic=_deterministic(),
        inputs=_inputs(),
        verdict=verdict,
    )
    unsupported = merge_fit_verdict(
        deterministic=_deterministic(),
        inputs=_inputs(),
        verdict=verdict.model_copy(update={"evidence_ids": ()}),
    )
    uncertain = merge_fit_verdict(
        deterministic=replace(_deterministic(), eligibility="uncertain"),
        inputs=_inputs(),
        verdict=verdict,
    )

    assert accepted.band == "strong"
    assert unsupported.band == "promising"
    assert uncertain.band == "promising"


def test_model_cannot_override_likely_ineligible_result() -> None:
    result = merge_fit_verdict(
        deterministic=replace(
            _deterministic(),
            fit_band="low",
            eligibility="likely_ineligible",
        ),
        inputs=_inputs(),
        verdict=FitVerdict(
            band="strong",
            reasons=("Skills appear relevant.",),
            evidence_ids=("event-pipeline",),
        ),
    )

    assert result.band == "low"


def test_prefilter_skips_ineligible_and_insufficient_postings() -> None:
    assert should_request_model(_deterministic(), _inputs()) is True
    assert (
        should_request_model(
            replace(_deterministic(), eligibility="likely_ineligible"),
            _inputs(),
        )
        is False
    )
    assert (
        should_request_model(
            replace(_deterministic(), fit_band="insufficient_data"),
            _inputs(),
        )
        is False
    )


def test_provider_failure_and_invalid_output_use_deterministic_fallback() -> None:
    failed = _Provider(TimeoutError("provider timeout"))
    invalid = _Provider(
        FitVerdict(
            band="strong",
            reasons=("Looks aligned.",),
            evidence_ids=("unknown",),
        )
    )

    failed_result = evaluate_fit_with_fallback(
        provider=failed,
        inputs=_inputs(),
        deterministic=_deterministic(),
    )
    invalid_result = evaluate_fit_with_fallback(
        provider=invalid,
        inputs=_inputs(),
        deterministic=_deterministic(),
    )

    assert failed_result.method == "deterministic"
    assert failed_result.fallback_reason == "provider_unavailable"
    assert failed_result.band == _deterministic().fit_band
    assert invalid_result.fallback_reason == "invalid_verdict"
    assert failed.calls == invalid.calls == 1


def test_prefiltered_result_makes_no_provider_call() -> None:
    provider = _Provider(
        FitVerdict(band="promising", reasons=("Would otherwise be called.",))
    )
    deterministic = replace(_deterministic(), fit_band="insufficient_data")

    result = evaluate_fit_with_fallback(
        provider=provider,
        inputs=_inputs(),
        deterministic=deterministic,
    )

    assert result.method == "deterministic"
    assert result.fallback_reason == "prefiltered"
    assert provider.calls == 0
