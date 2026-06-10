"""Gemini-backed outreach drafting tool.

Replaces ``draft_message_mock`` with a real Gemini call once the module is
importable. ``registry._optional_tool`` discovers it automatically, so no
orchestration code changes when this file lands.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

from opentelemetry import trace

from ..mcp_client import query_past_drafts
from ..schemas import PastDraft, Person, Role
from .mocks import draft_message_mock


LOGGER = logging.getLogger(__name__)
TRACER = trace.get_tracer(__name__)

DEFAULT_MODEL = "gemini-3.5-flash"
DEFAULT_TEMPERATURE = 0.4
DEFAULT_MAX_OUTPUT_TOKENS = 512
RESUME_EXCERPT_CHARS = 800
DEFAULT_EXEMPLAR_TOP_K = 3
EXEMPLAR_SCORE_THRESHOLD = 4.0
EXEMPLAR_CHAR_CAP = 600
EXEMPLAR_BLOCK_CHAR_CAP = 2_000


def _draft_model() -> str:
    """Drafting model, overridable per deployment.

    The judge model in evals.py is intentionally NOT configurable here:
    keeping the measuring stick constant is what makes the V10 round
    comparison (and any model swap) interpretable.
    """
    return os.environ.get("GEMINI_DRAFT_MODEL", "").strip() or DEFAULT_MODEL

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
    *,
    keywords: tuple[str, ...] | list[str] = (),
    use_self_rag: bool = True,
    exemplar_cache: dict[tuple[frozenset[str], int], list[PastDraft]] | None = None,
) -> str:
    """Draft a personalized referral request for one role/person pair.

    ``keywords`` should mirror ``criteria.role_keywords`` from the calling hunt;
    Phoenix retrieval matches traced keywords exactly so derived heuristics from
    Role text would silently miss the seeded corpus.

    ``use_self_rag`` toggles V8 self-retrieval. Pass False for the V10 round-1
    baseline. ``exemplar_cache`` is a per-run dict keyed by
    ``(frozenset(keywords_casefold), top_k)`` so the 9-draft pipeline issues at
    most one Phoenix query per unique keyword set per hunt.
    """
    _load_dotenv_if_available()
    role = Role.model_validate(role)
    person = Person.model_validate(person)

    exemplars = _fetch_exemplars(
        keywords=tuple(keywords),
        use_self_rag=use_self_rag,
        exemplar_cache=exemplar_cache,
    )

    api_key = _get_google_api_key()
    if not api_key:
        LOGGER.warning("GOOGLE_API_KEY missing; falling back to mock draft.")
        return draft_message_mock(role, person, resume_text)

    try:
        raw = _generate(role, person, resume_text, exemplars=exemplars, api_key=api_key)
    except Exception as exc:  # network / SDK / model failure
        LOGGER.warning("Gemini draft failed (%s); falling back to mock.", exc)
        return draft_message_mock(role, person, resume_text)

    cleaned = _clean(raw)
    if not _is_usable(cleaned):
        LOGGER.warning("Gemini draft rejected by post-processing; falling back to mock.")
        return draft_message_mock(role, person, resume_text)

    return cleaned


def _generate(
    role: Role,
    person: Person,
    resume_text: str,
    *,
    exemplars: list[PastDraft],
    api_key: str,
) -> str:
    from google.genai import Client
    from google.genai import types as genai_types

    client = Client(api_key=api_key)
    user_prompt = _build_user_prompt(role, person, resume_text, exemplars=exemplars)

    response = client.models.generate_content(
        model=_draft_model(),
        contents=user_prompt,
        config=genai_types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=DEFAULT_TEMPERATURE,
            max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return (response.text or "").strip()


def _build_user_prompt(
    role: Role,
    person: Person,
    resume_text: str,
    *,
    exemplars: list[PastDraft] | None = None,
) -> str:
    excerpt = _resume_excerpt(resume_text, role=role)
    first_name = person.name.split()[0] if person.name.strip() else "there"
    exemplar_block = _format_exemplar_block(exemplars or [])
    return (
        f"{exemplar_block}"
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


def _format_exemplar_block(exemplars: list[PastDraft]) -> str:
    """Render high-scoring past drafts as a prompt prefix.

    PastDraft only carries ``eval_score`` (no outcome field), so the header
    intentionally says "high-scoring past examples" instead of overclaiming
    reply outcomes.
    """
    if not exemplars:
        return ""

    lines: list[str] = [
        "Here are high-scoring past examples for similar roles. They earned the "
        "highest evaluation scores in our archive. Match their pattern -- a "
        "concrete spec or product detail, a named team, and a specific next "
        "step -- but write something new for this person and role. Do not copy "
        "phrasing verbatim.",
        "",
    ]
    running_chars = sum(len(line) for line in lines)
    for index, exemplar in enumerate(exemplars, start=1):
        message = _truncate(exemplar.message.strip(), EXEMPLAR_CHAR_CAP)
        score = f"{exemplar.eval_score:.1f}" if exemplar.eval_score is not None else "n/a"
        header = f"--- Past example {index} (score {score}) ---"
        candidate = [header, message, ""]
        candidate_len = sum(len(line) for line in candidate)
        if running_chars + candidate_len > EXEMPLAR_BLOCK_CHAR_CAP:
            break
        lines.extend(candidate)
        running_chars += candidate_len

    lines.append("")
    return "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


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


def _fetch_exemplars(
    *,
    keywords: tuple[str, ...],
    use_self_rag: bool,
    exemplar_cache: dict[tuple[frozenset[str], int], list[PastDraft]] | None,
    top_k: int = DEFAULT_EXEMPLAR_TOP_K,
) -> list[PastDraft]:
    """Self-RAG retrieval gated behind ``use_self_rag``.

    Emits a child ``query_past_drafts`` span for every call (cache hit included)
    so the Phoenix trace shows retrieval evidence per draft. Cache key folds
    case + dedupes so ``["SCIM", "scim"]`` is the same key.
    """
    with TRACER.start_as_current_span("query_past_drafts") as rag_span:
        rag_span.set_attribute("job_hunt.rag.enabled", use_self_rag)
        rag_span.set_attribute("job_hunt.rag.requested_top_k", top_k)
        rag_span.set_attribute("job_hunt.rag.keywords", tuple(keywords))

        if not use_self_rag:
            rag_span.set_attribute("job_hunt.rag.fallback_reason", "disabled")
            return []
        if not keywords:
            rag_span.set_attribute("job_hunt.rag.fallback_reason", "no_keywords")
            return []

        cache_key = (frozenset(k.casefold() for k in keywords), top_k)
        if exemplar_cache is not None and cache_key in exemplar_cache:
            cached = exemplar_cache[cache_key]
            rag_span.set_attribute("job_hunt.rag.cache_hit", True)
            return _annotate_and_filter(rag_span, cached)

        rag_span.set_attribute("job_hunt.rag.cache_hit", False)
        try:
            drafts = _run_query_past_drafts(list(keywords), top_k)
        except Exception as exc:  # mcp/rest/timeout
            LOGGER.warning("Self-RAG retrieval failed (%s); falling back to baseline.", exc)
            rag_span.set_attribute("job_hunt.rag.fallback_reason", "error")
            rag_span.set_attribute("job_hunt.rag.returned_count", 0)
            return []

        if exemplar_cache is not None:
            exemplar_cache[cache_key] = drafts

        return _annotate_and_filter(rag_span, drafts)


def _annotate_and_filter(rag_span: Any, drafts: list[PastDraft]) -> list[PastDraft]:
    rag_span.set_attribute("job_hunt.rag.returned_count", len(drafts))
    rag_span.set_attribute(
        "job_hunt.rag.retrieved_span_ids",
        tuple(draft.span_id for draft in drafts),
    )
    rag_span.set_attribute(
        "job_hunt.rag.retrieved_scores",
        tuple(float(draft.eval_score or 0.0) for draft in drafts),
    )
    if not drafts:
        rag_span.set_attribute("job_hunt.rag.fallback_reason", "no_results")
        return []

    filtered = [
        draft
        for draft in drafts
        if draft.eval_score is not None and draft.eval_score >= EXEMPLAR_SCORE_THRESHOLD
    ]
    rag_span.set_attribute("job_hunt.rag.exemplars_used", len(filtered))
    if not filtered:
        rag_span.set_attribute("job_hunt.rag.fallback_reason", "all_below_threshold")
    return filtered


def _run_query_past_drafts(keywords: list[str], top_k: int) -> list[PastDraft]:
    """Sync bridge into the async ``query_past_drafts`` API.

    Safe to call from FastAPI ``def`` endpoints because they run in the
    threadpool with no live event loop. If an endpoint ever switches to
    ``async def``, this needs to grow a running-loop branch.
    """
    return asyncio.run(query_past_drafts(keywords, top_k=top_k))


def _get_google_api_key() -> str:
    return os.environ.get("GOOGLE_API_KEY", "").strip()


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    load_dotenv()
