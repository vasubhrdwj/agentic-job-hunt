"""Gemini-backed outreach drafting tool.

Replaces ``draft_message_mock`` with a real Gemini call once the module is
importable. ``registry._optional_tool`` discovers it automatically, so no
orchestration code changes when this file lands.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from ..schemas import Person, Role
from .mocks import draft_message_mock


LOGGER = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_TEMPERATURE = 0.4
DEFAULT_MAX_OUTPUT_TOKENS = 512
RESUME_EXCERPT_CHARS = 800

SYSTEM_PROMPT = """
You write short, human-sounding referral request messages for a job hunt.

Hard rules:
- Output is plain text only. No markdown, no bullet points, no "Subject:" line.
- Length: 3 to 4 sentences. Never longer.
- Structure: greeting, one specific hook tying the recipient's work to the role,
  a low-friction ask (a pointer or quick chat), short sign-off.
- Reference one concrete fact about the company or role and one concrete fact
  about the person's title or work. No generic praise.
- Never use placeholders like [Your Name], [Company], or [Role]. Write nothing
  in square brackets.
- Do not invent facts. Use only what is in the prompt.
- Sign off with "Thanks," on its own line. Do not add the sender's name
  (the user will add it before sending).
- Tone: warm but direct. No LinkedIn-influencer language, no hype, no flattery,
  no "I hope this finds you well".
""".strip()

_PLACEHOLDER_RE = re.compile(r"\[[^\]]+\]")
_MARKDOWN_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$")
_SUBJECT_LINE_RE = re.compile(r"^\s*subject\s*:.*$", re.IGNORECASE | re.MULTILINE)


def draft_message(
    role: Role | dict[str, Any],
    person: Person | dict[str, Any],
    resume_text: str = "",
) -> str:
    """Draft a personalized referral request for one role/person pair."""
    _load_dotenv_if_available()
    role = Role.model_validate(role)
    person = Person.model_validate(person)

    api_key = _get_google_api_key()
    if not api_key:
        LOGGER.warning("GOOGLE_API_KEY missing; falling back to mock draft.")
        return draft_message_mock(role, person, resume_text)

    try:
        raw = _generate(role, person, resume_text, api_key=api_key)
    except Exception as exc:  # network / SDK / model failure
        LOGGER.warning("Gemini draft failed (%s); falling back to mock.", exc)
        return draft_message_mock(role, person, resume_text)

    cleaned = _clean(raw)
    if not _is_usable(cleaned):
        LOGGER.warning("Gemini draft rejected by post-processing; falling back to mock.")
        return draft_message_mock(role, person, resume_text)

    return cleaned


def _generate(role: Role, person: Person, resume_text: str, *, api_key: str) -> str:
    from google.genai import Client
    from google.genai import types as genai_types

    client = Client(api_key=api_key)
    user_prompt = _build_user_prompt(role, person, resume_text)

    response = client.models.generate_content(
        model=DEFAULT_MODEL,
        contents=user_prompt,
        config=genai_types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=DEFAULT_TEMPERATURE,
            max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return (response.text or "").strip()


def _build_user_prompt(role: Role, person: Person, resume_text: str) -> str:
    excerpt = _resume_excerpt(resume_text, role=role)
    first_name = person.name.split()[0] if person.name.strip() else "there"
    return (
        f"Write a referral request to {person.name} (use the first name "
        f'"{first_name}" in the greeting).\n\n'
        f"Role:\n"
        f"- Company: {role.company}\n"
        f"- Title: {role.title}\n"
        f"- Location: {role.location}\n"
        f"- Summary: {role.summary}\n"
        f"- Why this role fits: {role.match_reason}\n\n"
        f"Person:\n"
        f"- Name: {person.name}\n"
        f"- Title: {person.title}\n"
        f"- Why they are a sensible contact: {person.why_relevant}\n\n"
        f"Sender resume excerpt (use to ground one concrete fit signal):\n"
        f"{excerpt}\n\n"
        "Now write the message. Plain text. 3 to 4 sentences. End with "
        '"Thanks," on its own line and nothing after it.'
    )


def _resume_excerpt(resume_text: str, *, role: Role) -> str:
    """Pick a slice of resume that overlaps with the role's keywords."""
    text = (resume_text or "").strip()
    if not text:
        return "(no resume provided)"

    if len(text) <= RESUME_EXCERPT_CHARS:
        return text

    keywords = _role_keywords(role)
    lowered = text.lower()
    for keyword in keywords:
        index = lowered.find(keyword.lower())
        if index == -1:
            continue
        start = max(0, index - RESUME_EXCERPT_CHARS // 3)
        end = min(len(text), start + RESUME_EXCERPT_CHARS)
        return text[start:end].strip()

    return text[:RESUME_EXCERPT_CHARS].strip()


def _role_keywords(role: Role) -> list[str]:
    """Cheap keyword extraction from the role's structured fields."""
    seeds: list[str] = []
    for value in (role.title, role.summary, role.match_reason):
        seeds.extend(re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]{2,}", value or ""))
    seen: set[str] = set()
    keywords: list[str] = []
    for token in seeds:
        normalized = token.strip(".-")
        if len(normalized) < 3:
            continue
        lower = normalized.lower()
        if lower in seen:
            continue
        seen.add(lower)
        keywords.append(normalized)
    return keywords


def _clean(text: str) -> str:
    if not text:
        return ""
    cleaned = _MARKDOWN_FENCE_RE.sub("", text).strip()
    cleaned = _SUBJECT_LINE_RE.sub("", cleaned).strip()
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _is_usable(text: str) -> bool:
    if not text or len(text) < 40:
        return False
    if _PLACEHOLDER_RE.search(text):
        return False
    return True


def _get_google_api_key() -> str:
    return os.environ.get("GOOGLE_API_KEY", "").strip()


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    load_dotenv()
