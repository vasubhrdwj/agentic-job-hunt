"""Phoenix self-retrieval for past outreach drafts.

Trace contract owned by this app:
- ``job_hunt.role.keywords``: sequence of role keywords used for retrieval.
- ``job_hunt.draft.output_text``: trimmed draft text returned by ``draft_message``.
- ``job_hunt.eval.composite_score``: optional 1-5 score written by V9 evals.
- ``job_hunt.seed.tag=seed``: required marker for curated seed exemplars.

The public wrapper returns domain objects, not raw Phoenix/MCP payloads.
User-traffic spans are intentionally ignored until owner-scoped retrieval exists.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from .schemas import PastDraft
from .tracing import DEFAULT_PROJECT_NAME


LOGGER = logging.getLogger(__name__)

DRAFT_TEXT_ATTRIBUTE = "job_hunt.draft.output_text"
EVAL_SCORE_ATTRIBUTE = "job_hunt.eval.composite_score"
ROLE_KEYWORDS_ATTRIBUTE = "job_hunt.role.keywords"
ROLE_TITLE_ATTRIBUTE = "job_hunt.role.title"
ROLE_COMPANY_ATTRIBUTE = "job_hunt.role.company"
SEED_TAG_ATTRIBUTE = "job_hunt.seed.tag"
SEED_TAG_VALUE = "seed"

DEFAULT_QUERY_TIMEOUT_SECONDS = 1.5
DEFAULT_MCP_COLD_TIMEOUT_SECONDS = 4.0
DEFAULT_SPAN_LIMIT = 100
DEFAULT_LOOKBACK_HOURS = 24.0
DEFAULT_MAX_PAGES = 3
FAILURE_CACHE_SECONDS = 60.0

Transport = Literal["rest", "mcp", "auto"]

_failure_cache: dict[str, float] = {}
_project_identifier_cache: dict[tuple[str, str], str] = {}
_mcp_first_call_done = False


@dataclass(frozen=True)
class _PhoenixConfig:
    base_url: str
    api_key: str | None
    project: str
    limit: int
    timeout_s: float
    start_time: str | None
    max_pages: int


async def query_past_drafts(
    role_keywords: list[str],
    top_k: int = 3,
    *,
    project: str = DEFAULT_PROJECT_NAME,
    transport: Transport | None = None,
    timeout_s: float | None = None,
) -> list[PastDraft]:
    """Return matching past drafts from Phoenix, sorted by eval score.

    Results are filtered by exact, case-insensitive matches against the traced
    ``job_hunt.role.keywords`` attribute. Up to ``top_k`` drafts are returned,
    sorted by ``eval_score`` descending with ``None`` scores last. Auth errors,
    no traces, malformed payloads, MCP failures, and timeouts return ``[]``.

    ``timeout_s`` defaults to the ``PHOENIX_QUERY_TIMEOUT_S`` env var, falling
    back to 1.5s. Phoenix Cloud regularly exceeds 1.5s on long lookbacks, and
    one timeout silences self-RAG for an entire run via the exemplar cache --
    deployments should size this generously (8s in render.yaml).
    """
    _load_dotenv_if_available()
    timeout_s = _resolve_timeout(timeout_s)
    keywords = _clean_keywords(role_keywords)
    if top_k <= 0 or not keywords:
        return []

    selected_transport = _selected_transport(transport)
    config = _build_config(
        project=project,
        min_limit=max(DEFAULT_SPAN_LIMIT, top_k * 10),
        timeout_s=timeout_s,
    )

    spans: list[dict[str, Any]]
    if selected_transport in {"mcp", "auto"}:
        try:
            spans = await asyncio.wait_for(
                _fetch_spans_mcp(config),
                timeout=_mcp_timeout(selected_transport, timeout_s),
            )
        except Exception as exc:
            _warn_failure_once(
                f"mcp:{config.base_url}:{config.project}",
                "Phoenix MCP query failed; falling back to REST",
                exc,
            )
        else:
            return _select_top_drafts(
                spans,
                keywords,
                top_k,
                outcome_by_message=_load_draft_outcomes(),
            )

    try:
        spans = await asyncio.wait_for(_fetch_spans_rest(config), timeout=timeout_s)
    except Exception as exc:
        _warn_failure_once(
            f"rest:{config.base_url}:{config.project}",
            "Phoenix REST query failed",
            exc,
        )
        return []

    return _select_top_drafts(
        spans,
        keywords,
        top_k,
        outcome_by_message=_load_draft_outcomes(),
    )


def _resolve_timeout(timeout_s: float | None) -> float:
    if timeout_s is not None:
        return timeout_s
    raw = os.getenv("PHOENIX_QUERY_TIMEOUT_S", "").strip()
    if raw:
        try:
            return max(0.1, float(raw))
        except ValueError:
            LOGGER.warning(
                "Invalid PHOENIX_QUERY_TIMEOUT_S=%r; using %.1fs default.",
                raw,
                DEFAULT_QUERY_TIMEOUT_SECONDS,
            )
    return DEFAULT_QUERY_TIMEOUT_SECONDS


def _select_top_drafts(
    spans: list[dict[str, Any]],
    keywords: list[str],
    top_k: int,
    *,
    outcome_by_message: dict[str, str] | None = None,
) -> list[PastDraft]:
    drafts = _parse_past_drafts(
        spans,
        keywords,
        outcome_by_message=outcome_by_message or {},
    )
    drafts.sort(
        key=lambda draft: (
            -_outcome_priority(draft.outcome),
            draft.eval_score is None,
            -(draft.eval_score or 0.0),
        )
    )
    # Dedupe by message text: re-uploaded seeds and re-traced runs produce
    # identical drafts under distinct span_ids, and duplicate exemplars
    # waste few-shot slots.
    selected: list[PastDraft] = []
    seen_messages: set[str] = set()
    for draft in drafts:
        normalized = _normalize_draft_message(draft.message)
        if normalized in seen_messages:
            continue
        seen_messages.add(normalized)
        selected.append(draft)
        if len(selected) == top_k:
            break
    return selected


def _parse_past_drafts(
    spans: list[dict[str, Any]],
    keywords: list[str],
    *,
    outcome_by_message: dict[str, str],
) -> list[PastDraft]:
    drafts: list[PastDraft] = []
    for span in spans:
        attributes = _span_attributes(span)
        if _string_attribute(attributes, SEED_TAG_ATTRIBUTE).casefold() != SEED_TAG_VALUE:
            continue

        message = _string_attribute(attributes, DRAFT_TEXT_ATTRIBUTE).strip()
        if not message:
            continue

        matched_keywords = _matched_keywords(attributes.get(ROLE_KEYWORDS_ATTRIBUTE), keywords)
        if not matched_keywords:
            continue

        drafts.append(
            PastDraft(
                message=message,
                role_title=_string_attribute(attributes, ROLE_TITLE_ATTRIBUTE),
                company=_string_attribute(attributes, ROLE_COMPANY_ATTRIBUTE),
                eval_score=_coerce_score(attributes.get(EVAL_SCORE_ATTRIBUTE)),
                outcome=outcome_by_message.get(_normalize_draft_message(message)),
                matched_keywords=matched_keywords,
                span_id=_span_id(span),
                trace_id=_trace_id(span),
            )
        )
    return drafts


def _load_draft_outcomes() -> dict[str, str]:
    try:
        from .persistence import load_draft_outcomes

        return load_draft_outcomes()
    except Exception as exc:
        _warn_failure_once(
            "outcomes:sqlite",
            "Outcome lookup failed; ranking past drafts by judge score only",
            exc,
        )
        return {}


def _normalize_draft_message(message: str) -> str:
    return " ".join(message.split()).casefold()


def _outcome_priority(outcome: str | None) -> int:
    return {
        "introduced": 3,
        "replied": 2,
        "pending": 1,
        None: 0,
        "rejected": -1,
        "no_reply": -2,
    }.get(outcome, 0)


async def _fetch_spans_rest(config: _PhoenixConfig) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_fetch_spans_rest_sync, config)


def _fetch_spans_rest_sync(config: _PhoenixConfig) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(config.max_pages):
        body = _fetch_spans_rest_page(config, cursor=cursor)
        spans.extend(_spans_from_rest_body(body))
        cursor = _next_cursor(body)
        if not cursor:
            break
    return spans


def _fetch_spans_rest_page(config: _PhoenixConfig, *, cursor: str | None) -> dict[str, Any]:
    try:
        return _fetch_project_spans(
            config,
            project_identifier=config.project,
            cursor=cursor,
        )
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        resolved_identifier = _resolve_project_identifier(config)
        if not resolved_identifier or resolved_identifier == config.project:
            raise
        return _fetch_project_spans(
            config,
            project_identifier=resolved_identifier,
            cursor=cursor,
        )


def _fetch_project_spans(
    config: _PhoenixConfig,
    *,
    project_identifier: str,
    cursor: str | None,
) -> dict[str, Any]:
    path_identifier = urllib.parse.quote(project_identifier, safe="")
    query_params: dict[str, str | int] = {"limit": config.limit}
    if config.start_time:
        query_params["start_time"] = config.start_time
    if cursor:
        query_params["cursor"] = cursor
    query = urllib.parse.urlencode(query_params)
    url = f"{config.base_url}/v1/projects/{path_identifier}/spans?{query}"
    return _fetch_json(url, config)


def _resolve_project_identifier(config: _PhoenixConfig) -> str | None:
    cache_key = (config.base_url, config.project)
    if cache_key in _project_identifier_cache:
        return _project_identifier_cache[cache_key]

    query = urllib.parse.urlencode({"limit": 100})
    body = _fetch_json(f"{config.base_url}/v1/projects?{query}", config)
    for project_info in body.get("data", []):
        if not isinstance(project_info, dict):
            continue
        names = {
            str(project_info.get("name", "")),
            str(project_info.get("project_name", "")),
        }
        if config.project not in names:
            continue
        identifier = str(project_info.get("id") or project_info.get("name") or config.project)
        _project_identifier_cache[cache_key] = identifier
        return identifier
    return None


def _fetch_json(url: str, config: _PhoenixConfig) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=_phoenix_headers(config))
    with urllib.request.urlopen(request, timeout=config.timeout_s) as response:
        payload = response.read().decode("utf-8")
    body = json.loads(payload)
    if not isinstance(body, dict):
        raise ValueError("Phoenix returned a non-object JSON payload")
    return body


def _spans_from_rest_body(body: dict[str, Any]) -> list[dict[str, Any]]:
    data = body.get("data", [])
    if not isinstance(data, list):
        return []
    return [span for span in data if isinstance(span, dict)]


def _next_cursor(body: dict[str, Any]) -> str | None:
    cursor = body.get("next_cursor")
    if isinstance(cursor, str) and cursor:
        return cursor
    return None


async def _fetch_spans_mcp(config: _PhoenixConfig) -> list[dict[str, Any]]:
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        raise RuntimeError("the mcp package is not installed") from exc

    env = os.environ.copy()
    env["PHOENIX_HOST"] = config.base_url
    if config.api_key:
        env["PHOENIX_API_KEY"] = config.api_key

    args = [
        "-y",
        os.getenv("PHOENIX_MCP_PACKAGE", "@arizeai/phoenix-mcp@latest"),
        "--baseUrl",
        config.base_url,
    ]
    server_params = StdioServerParameters(
        command=os.getenv("PHOENIX_MCP_COMMAND", "npx"),
        args=args,
        env=env,
    )
    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(stdio_client(server_params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        result = await session.call_tool(
            "get-spans",
            {"projectName": config.project, "limit": config.limit},
        )
    return _spans_from_mcp_result(result)


def _spans_from_mcp_result(result: Any) -> list[dict[str, Any]]:
    body = _mcp_result_json(result)
    spans = body.get("spans", body.get("data", []))
    if not isinstance(spans, list):
        return []
    return [span for span in spans if isinstance(span, dict)]


def _mcp_result_json(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result

    for content in getattr(result, "content", []) or []:
        text = getattr(content, "text", None)
        if text is None and isinstance(content, dict):
            text = content.get("text")
        if not text:
            continue
        parsed = json.loads(str(text))
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("Phoenix MCP returned no JSON text content")


def _build_config(*, project: str, min_limit: int, timeout_s: float) -> _PhoenixConfig:
    limit = int(os.getenv("PHOENIX_QUERY_SPAN_LIMIT", str(min_limit)))
    return _PhoenixConfig(
        base_url=_phoenix_base_url(),
        api_key=os.getenv("PHOENIX_API_KEY"),
        project=project,
        limit=max(1, min(limit, 1000)),
        timeout_s=timeout_s,
        start_time=_query_start_time(),
        max_pages=_query_max_pages(),
    )


def _phoenix_base_url() -> str:
    endpoint = (
        os.getenv("PHOENIX_BASE_URL")
        or os.getenv("PHOENIX_COLLECTOR_ENDPOINT")
        or os.getenv("PHOENIX_HOST")
        or "http://localhost:6006"
    ).strip()
    endpoint = endpoint.rstrip("/")
    for suffix in ("/v1/traces", "/v1"):
        if endpoint.endswith(suffix):
            endpoint = endpoint[: -len(suffix)]
    return endpoint.rstrip("/")


def _phoenix_headers(config: _PhoenixConfig) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    headers.update(_client_headers_from_env())
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    return headers


def _query_start_time() -> str | None:
    lookback = os.getenv("PHOENIX_QUERY_LOOKBACK_HOURS", str(DEFAULT_LOOKBACK_HOURS))
    try:
        hours = float(lookback)
    except ValueError:
        hours = DEFAULT_LOOKBACK_HOURS
    if hours <= 0:
        return None
    start = datetime.now(timezone.utc) - timedelta(hours=hours)
    return start.isoformat().replace("+00:00", "Z")


def _query_max_pages() -> int:
    try:
        max_pages = int(os.getenv("PHOENIX_QUERY_MAX_PAGES", str(DEFAULT_MAX_PAGES)))
    except ValueError:
        max_pages = DEFAULT_MAX_PAGES
    return max(1, min(max_pages, 10))


def _client_headers_from_env() -> dict[str, str]:
    raw_headers = os.getenv("PHOENIX_CLIENT_HEADERS", "")
    headers: dict[str, str] = {}
    for part in raw_headers.split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            headers[key] = value
    return headers


def _selected_transport(transport: Transport | None) -> Transport:
    selected = (transport or os.getenv("PHOENIX_QUERY_TRANSPORT", "rest")).lower()
    if selected not in {"rest", "mcp", "auto"}:
        return "rest"
    return selected  # type: ignore[return-value]


def _mcp_timeout(transport: Transport, timeout_s: float) -> float:
    global _mcp_first_call_done
    if transport == "mcp" and not _mcp_first_call_done:
        _mcp_first_call_done = True
        return max(timeout_s, DEFAULT_MCP_COLD_TIMEOUT_SECONDS)
    return timeout_s


def _clean_keywords(role_keywords: list[str]) -> list[str]:
    keywords: list[str] = []
    seen: set[str] = set()
    for keyword in role_keywords:
        cleaned = str(keyword).strip()
        normalized = cleaned.casefold()
        if not cleaned or normalized in seen:
            continue
        seen.add(normalized)
        keywords.append(cleaned)
    return keywords


def _matched_keywords(attribute_value: Any, keywords: list[str]) -> list[str]:
    traced_keywords = {_normalize_keyword(value) for value in _attribute_values(attribute_value)}
    return [keyword for keyword in keywords if _normalize_keyword(keyword) in traced_keywords]


def _attribute_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        values: list[str] = []
        for item in value:
            values.extend(_attribute_values(item))
        return values
    return [str(value)]


def _normalize_keyword(value: str) -> str:
    return value.strip().casefold()


def _span_attributes(span: dict[str, Any]) -> dict[str, Any]:
    attributes = span.get("attributes", {})
    if isinstance(attributes, dict):
        return attributes

    if isinstance(attributes, list):
        parsed: dict[str, Any] = {}
        for item in attributes:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if not key:
                continue
            parsed[str(key)] = _unwrap_otel_value(item.get("value"))
        return parsed

    return {}


def _unwrap_otel_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in (
        "stringValue",
        "intValue",
        "doubleValue",
        "boolValue",
        "string_value",
        "int_value",
        "double_value",
        "bool_value",
    ):
        if key in value:
            return value[key]
    if "arrayValue" in value:
        values = value["arrayValue"].get("values", [])
        return [_unwrap_otel_value(item) for item in values]
    if "array_value" in value:
        values = value["array_value"].get("values", [])
        return [_unwrap_otel_value(item) for item in values]
    return value


def _string_attribute(attributes: dict[str, Any], key: str) -> str:
    value = attributes.get(key)
    if value is None:
        return ""
    return str(value).strip()


def _coerce_score(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if 1.0 <= score <= 5.0:
        return score
    return None


def _span_id(span: dict[str, Any]) -> str:
    context = span.get("context")
    if isinstance(context, dict) and context.get("span_id"):
        return str(context["span_id"])
    return str(span.get("span_id") or span.get("id") or "")


def _trace_id(span: dict[str, Any]) -> str:
    context = span.get("context")
    if isinstance(context, dict) and context.get("trace_id"):
        return str(context["trace_id"])
    return str(span.get("trace_id") or "")


def _warn_failure_once(cache_key: str, message: str, exc: Exception) -> None:
    now = time.monotonic()
    last_warning = _failure_cache.get(cache_key)
    if last_warning is not None and now - last_warning < FAILURE_CACHE_SECONDS:
        return
    _failure_cache[cache_key] = now
    LOGGER.warning("%s (%s)", message, _exception_summary(exc))


def _exception_summary(exc: Exception) -> str:
    return type(exc).__name__


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()
