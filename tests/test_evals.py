"""Offline tests for the V9 LLM-as-judge eval. No network calls."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from job_hunt_agent import evals
from job_hunt_agent.evals import EvalResult, _JudgeVerdict, _parse_verdict, score_draft
from job_hunt_agent.run import run_hunt
from job_hunt_agent.schemas import JobCriteria, Person, Role
from job_hunt_agent.tools.mocks import score_draft_mock
from job_hunt_agent.tools.registry import PipelineTools, build_pipeline_tools


ROLE = Role(
    company="Okta",
    title="Senior Software Engineer, Lifecycle Management",
    url="https://www.linkedin.com/jobs/view/okta-123",
    location="Remote-India",
    summary="Build SCIM provisioning and lifecycle automation.",
    match_reason="Listing names SCIM 2.0 provisioning.",
)
PERSON = Person(
    name="Anika Rao",
    title="Staff Engineer, Lifecycle Management",
    company="Okta",
    profile_url="https://www.linkedin.com/in/anika-rao",
    source="linkedin",
    why_relevant="Owns the provisioning service this role feeds into.",
    verified_current_employer=True,
    confidence=0.9,
)
GOOD_MESSAGE = (
    "Hi Anika — I noticed the Lifecycle Management team owns the SCIM 2.0 "
    "RFC 7644 provisioning surface. My last three years were exactly that. "
    "Would 15 min on Tuesday work to ask how the team scopes the role?\nThanks,"
)
LAZY_MESSAGE = (
    "Hi, I hope this finds you well! I am passionate about technology and "
    "would love to connect sometime about opportunities at your company!!"
)


@pytest.fixture
def no_dotenv(monkeypatch: pytest.MonkeyPatch):
    """Stop score_draft from re-loading the developer's real .env."""
    monkeypatch.setattr(evals, "_load_dotenv_if_available", lambda: None)


def _verdict(**overrides) -> _JudgeVerdict:
    base = dict(personalization=5, specificity=4, ask=4, tone=5, rationale="strong hook")
    base.update(overrides)
    return _JudgeVerdict.model_validate(base)


def test_parse_verdict_clamps_out_of_range_scores() -> None:
    raw = json.dumps(
        {"personalization": 9, "specificity": 0, "ask": 3.6, "tone": -2, "rationale": "x"}
    )
    verdict = _parse_verdict(raw)
    assert verdict.personalization == 5
    assert verdict.specificity == 1
    assert verdict.ask == 4
    assert verdict.tone == 1


def test_parse_verdict_strips_markdown_fences() -> None:
    raw = '```json\n{"personalization": 4, "specificity": 4, "ask": 4, "tone": 4, "rationale": "ok"}\n```'
    verdict = _parse_verdict(raw)
    assert verdict.personalization == 4


def test_parse_verdict_rejects_non_object_json() -> None:
    with pytest.raises(ValueError):
        _parse_verdict("[1, 2, 3]")


def test_score_draft_returns_none_without_api_key(
    monkeypatch: pytest.MonkeyPatch, no_dotenv
) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert score_draft(ROLE, PERSON, GOOD_MESSAGE) is None


def test_score_draft_returns_none_when_judge_raises(
    monkeypatch: pytest.MonkeyPatch, no_dotenv, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    marker = "PRIVATE-DRAFT-IN-JUDGE-EXCEPTION-4f2a"
    with patch.object(evals, "_judge", side_effect=RuntimeError(marker)):
        assert score_draft(ROLE, PERSON, GOOD_MESSAGE) is None
    assert marker not in caplog.text
    assert "RuntimeError" in caplog.text


def test_score_draft_computes_composite_locally(
    monkeypatch: pytest.MonkeyPatch, no_dotenv
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    with patch.object(evals, "_judge", return_value=_verdict()):
        result = score_draft(ROLE, PERSON, GOOD_MESSAGE)
    assert isinstance(result, EvalResult)
    assert result.composite == 4.5
    assert result.rationale == "strong hook"


def test_mock_judge_rewards_seed_pattern() -> None:
    good = score_draft_mock(ROLE, PERSON, GOOD_MESSAGE)
    lazy = score_draft_mock(ROLE, PERSON, LAZY_MESSAGE)
    assert good.composite > lazy.composite
    assert good.composite >= 4.0
    assert lazy.composite <= 2.5


def test_mock_pipeline_bundle_includes_judge() -> None:
    tools = build_pipeline_tools(use_mocks=True)
    assert tools.score_draft is score_draft_mock


def _criteria() -> JobCriteria:
    return JobCriteria(
        role_keywords=["SCIM"], seniority="senior", location=["Remote-India"]
    )


def test_run_hunt_attaches_eval_scores() -> None:
    tools = PipelineTools(
        search_jobs=lambda criteria: [ROLE],
        find_referrals=lambda role: [PERSON],
        draft_message=lambda role, person, resume_text, **_kw: GOOD_MESSAGE,
        score_draft=lambda role, person, message: EvalResult(
            personalization=5, specificity=5, ask=4, tone=4,
            composite=4.5, rationale="test",
        ),
    )
    with patch("job_hunt_agent.run.build_pipeline_tools", return_value=tools):
        result = run_hunt(resume_text="SCIM work.", criteria=_criteria())
    assert result.outreach[0].eval_score == 4.5


def test_run_hunt_survives_judge_returning_none() -> None:
    tools = PipelineTools(
        search_jobs=lambda criteria: [ROLE],
        find_referrals=lambda role: [PERSON],
        draft_message=lambda role, person, resume_text, **_kw: GOOD_MESSAGE,
        score_draft=lambda role, person, message: None,
    )
    with patch("job_hunt_agent.run.build_pipeline_tools", return_value=tools):
        result = run_hunt(resume_text="SCIM work.", criteria=_criteria())
    assert result.outreach[0].eval_score is None


def test_run_hunt_backward_compatible_without_judge() -> None:
    tools = PipelineTools(
        search_jobs=lambda criteria: [ROLE],
        find_referrals=lambda role: [PERSON],
        draft_message=lambda role, person, resume_text, **_kw: GOOD_MESSAGE,
    )
    with patch("job_hunt_agent.run.build_pipeline_tools", return_value=tools):
        result = run_hunt(resume_text="SCIM work.", criteria=_criteria())
    assert result.outreach[0].eval_score is None
