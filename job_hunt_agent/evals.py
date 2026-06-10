"""LLM-as-judge scoring for drafted outreach messages (V9).

``score_draft`` asks Gemini to rate one drafted message on four 1-5
sub-scores (personalization, specificity, ask, tone); the composite is
their average. Failures never break the pipeline: any auth, network, or
parsing problem logs a warning and returns ``None``.

The judge rubric deliberately mirrors ``fixtures/SEED_NOTES.md``: high
scores require the same three signals the seeded corpus rewards (concrete
technical detail, named team, specific next step), so V10's round
comparison measures the pattern the self-RAG exemplars teach.

Calibrate before trusting scores: ``python scripts/validate_judge.py``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from opentelemetry import trace
from pydantic import BaseModel, Field

from .schemas import Person, Role


LOGGER = logging.getLogger(__name__)
TRACER = trace.get_tracer(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"
JUDGE_TEMPERATURE = 0.0
JUDGE_MAX_OUTPUT_TOKENS = 512
SUB_SCORE_NAMES = ("personalization", "specificity", "ask", "tone")

_MARKDOWN_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$")

JUDGE_SYSTEM_PROMPT = """
You are a strict evaluator of cold outreach messages asking for job referrals.
You are given the role being pursued, the recipient, and the message. Score
ONLY the message text. Return JSON only, matching the schema you are given.

Score four dimensions, each an integer from 1 (bad) to 5 (excellent):

- personalization: Is the message visibly written for THIS recipient?
  5 = names the recipient's specific team or product area AND connects it to
      their actual work
  4 = references their actual work or team, but the connection is loose
  3 = references only their title or company; nothing about their work
  1 = interchangeable; could be sent to anyone ("your team", "your company")
- specificity: Does the sender ground the fit in concrete technical detail?
  5 = cites a precise, verifiable identifier: an RFC number ("SCIM 2.0 RFC
      7644"), an exact spec section, a named framework feature ("Next.js App
      Router"), or a concrete scale metric ("40k-seat IdP")
  4 = names a real technology or protocol, but without identifier-level
      precision ("SCIM provisioning", "OIDC migration")
  3 = real skills described generically ("identity systems", "frontend work")
  1 = no concrete detail ("passionate about technology", "strong skills")
- ask: Is there one specific, low-friction next step?
  5 = time-boxed with concrete options ("15 minutes Tuesday or Wednesday?")
  4 = specific and low-friction but not time-boxed ("could you point me to
      the hiring manager for this team?")
  3 = vague willingness ("open to a quick chat?", "would love to connect")
  1 = no real ask, or open-ended ("let me know your thoughts sometime")
- tone: Does it read like a busy, competent human wrote it?
  5 = warm, direct, 3-4 sentences, no fluff
  3 = mostly fine but padded or stiff
  1 = influencer language, hype, emoji, flattery, placeholders like
      [Your Name], or "I hope this finds you well"

Be strict. "Competent but generic" is a 3, not a 4. A 5 must be earned by the
exact behaviors listed above -- when in doubt between two scores, give the
lower one. Most adequate professional messages should average near 3. Set
rationale to one sentence naming the strongest and weakest dimension.
""".strip()


class EvalResult(BaseModel):
    """Judge verdict for one drafted message."""

    personalization: int = Field(ge=1, le=5)
    specificity: int = Field(ge=1, le=5)
    ask: int = Field(ge=1, le=5)
    tone: int = Field(ge=1, le=5)
    composite: float = Field(
        ge=1,
        le=5,
        description="Average of the four sub-scores, rounded to 2 decimals.",
    )
    rationale: str = Field(
        description="One judge sentence naming the strongest and weakest dimension.",
    )


class _JudgeVerdict(BaseModel):
    """Raw model output; composite is computed locally, never trusted."""

    personalization: int = Field(ge=1, le=5)
    specificity: int = Field(ge=1, le=5)
    ask: int = Field(ge=1, le=5)
    tone: int = Field(ge=1, le=5)
    rationale: str = ""


def score_draft(
    role: Role | dict[str, Any],
    person: Person | dict[str, Any],
    message: str,
    *,
    model: str = DEFAULT_MODEL,
) -> EvalResult | None:
    """Score one drafted message 1-5; ``None`` means "no score", never an error.

    Emits a ``score_draft`` span (child of the caller's ``draft_message`` span
    when tracing is configured) carrying the sub-scores and composite. The
    caller still must copy ``composite`` onto the ``draft_message`` span itself
    -- ``mcp_client.query_past_drafts()`` reads scores from there, not from
    this child span (see fixtures/SEED_NOTES.md, "V9 Dependency").
    """
    _load_dotenv_if_available()
    role = Role.model_validate(role)
    person = Person.model_validate(person)

    with TRACER.start_as_current_span("score_draft") as span:
        span.set_attribute("job_hunt.eval.model", model)

        api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
        if not api_key:
            LOGGER.warning("GOOGLE_API_KEY missing; skipping draft eval.")
            span.set_attribute("job_hunt.eval.skip_reason", "no_api_key")
            return None

        try:
            verdict = _judge(role, person, message, api_key=api_key, model=model)
        except Exception as exc:  # network / SDK / parse failure
            LOGGER.warning("Draft eval failed (%s); continuing without score.", exc)
            span.set_attribute("job_hunt.eval.skip_reason", "error")
            return None

        result = EvalResult(
            personalization=verdict.personalization,
            specificity=verdict.specificity,
            ask=verdict.ask,
            tone=verdict.tone,
            composite=_composite(verdict),
            rationale=verdict.rationale,
        )
        for name in SUB_SCORE_NAMES:
            span.set_attribute(f"job_hunt.eval.{name}", getattr(result, name))
        span.set_attribute("job_hunt.eval.composite_score", result.composite)
        span.set_attribute("job_hunt.eval.rationale", result.rationale)
        return result


def _composite(verdict: _JudgeVerdict) -> float:
    total = sum(getattr(verdict, name) for name in SUB_SCORE_NAMES)
    return round(total / len(SUB_SCORE_NAMES), 2)


def _judge(
    role: Role,
    person: Person,
    message: str,
    *,
    api_key: str,
    model: str,
) -> _JudgeVerdict:
    from google.genai import Client
    from google.genai import types as genai_types

    client = Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=_build_judge_prompt(role, person, message),
        config=genai_types.GenerateContentConfig(
            system_instruction=JUDGE_SYSTEM_PROMPT,
            temperature=JUDGE_TEMPERATURE,
            max_output_tokens=JUDGE_MAX_OUTPUT_TOKENS,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
            response_mime_type="application/json",
            response_schema=_JudgeVerdict,
        ),
    )
    return _parse_verdict(response.text or "")


def _build_judge_prompt(role: Role, person: Person, message: str) -> str:
    return (
        f"Role being pursued:\n"
        f"- Company: {role.company}\n"
        f"- Title: {role.title}\n"
        f"- Summary: {role.summary}\n\n"
        f"Recipient:\n"
        f"- Name: {person.name}\n"
        f"- Title: {person.title}\n"
        f"- Relevance: {person.why_relevant}\n\n"
        f"Message to score:\n---\n{message.strip()}\n---\n\n"
        "Score the message. JSON only."
    )


def _parse_verdict(raw: str) -> _JudgeVerdict:
    """Parse judge JSON, clamping sub-scores into [1, 5] before validation."""
    text = _MARKDOWN_FENCE_RE.sub("", raw.strip()).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"judge returned non-object JSON: {type(data).__name__}")
    for name in SUB_SCORE_NAMES:
        value = int(round(float(data.get(name, 0))))
        data[name] = min(5, max(1, value))
    data["rationale"] = str(data.get("rationale", "")).strip()
    return _JudgeVerdict.model_validate(data)


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    load_dotenv()
