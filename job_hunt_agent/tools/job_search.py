"""Google Custom Search backed job-search tool for A2."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

try:
    from job_hunt_agent.schemas import JobCriteria, Role
except ModuleNotFoundError:
    from schemas import JobCriteria, Role


LOGGER = logging.getLogger(__name__)

GOOGLE_CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"
DEFAULT_NUM_RESULTS_PER_QUERY = 5
DEFAULT_MAX_RESULTS = 5
DEFAULT_MAX_QUERIES = 4


def search_jobs(criteria: JobCriteria | dict[str, Any]) -> list[Role]:
    """Return LinkedIn job postings that match the supplied criteria.

    This is intentionally provider-shaped internally, but the public contract
    stays fixed for the agent: JobCriteria -> list[Role].
    """
    _load_dotenv_if_available()
    criteria = JobCriteria.model_validate(criteria)

    api_key = os.getenv("GOOGLE_CSE_API_KEY")
    search_engine_id = os.getenv("GOOGLE_CSE_ID")
    if not api_key or not search_engine_id:
        LOGGER.warning("GOOGLE_CSE_API_KEY or GOOGLE_CSE_ID is missing; returning no roles.")
        return []

    roles: list[Role] = []
    seen: set[str] = set()

    for query in _build_queries(criteria)[:DEFAULT_MAX_QUERIES]:
        payload = _fetch_google_cse(
            query=query,
            api_key=api_key,
            search_engine_id=search_engine_id,
            num_results=DEFAULT_NUM_RESULTS_PER_QUERY,
        )
        if not payload:
            continue

        for item in payload.get("items", []):
            role = _role_from_result(item, criteria=criteria, query=query)
            if role is None:
                continue

            key = _dedupe_key(role)
            if key in seen:
                continue

            seen.add(key)
            roles.append(role)
            if len(roles) >= DEFAULT_MAX_RESULTS:
                return roles

    return roles


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    load_dotenv()


def _build_queries(criteria: JobCriteria) -> list[str]:
    keywords = [_clean_query_part(keyword) for keyword in criteria.role_keywords if keyword.strip()]
    locations = [_clean_query_part(location) for location in criteria.location if location.strip()]

    if not keywords:
        keywords = ["software engineer"]
    if not locations:
        locations = ["India"]

    queries: list[str] = []
    for keyword in keywords:
        for location in locations:
            queries.append(f'site:linkedin.com/jobs "{keyword}" "{location}"')

    if len(keywords) > 1:
        joined_keywords = " ".join(keywords[:3])
        queries.insert(0, f'site:linkedin.com/jobs "{joined_keywords}" "{locations[0]}"')

    return _dedupe_strings(queries)


def _fetch_google_cse(
    *,
    query: str,
    api_key: str,
    search_engine_id: str,
    num_results: int,
) -> dict[str, Any] | None:
    params = {
        "key": api_key,
        "cx": search_engine_id,
        "q": query,
        "num": str(min(max(num_results, 1), 10)),
    }
    url = f"{GOOGLE_CSE_ENDPOINT}?{urlencode(params)}"
    request = Request(url, headers={"Accept": "application/json"})

    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = _read_error_body(exc)
        LOGGER.warning("Google CSE request failed with HTTP %s: %s", exc.code, body)
    except (URLError, TimeoutError) as exc:
        LOGGER.warning("Google CSE request failed: %s", exc)
    except json.JSONDecodeError as exc:
        LOGGER.warning("Google CSE returned malformed JSON: %s", exc)

    return None


def _role_from_result(
    item: dict[str, Any],
    *,
    criteria: JobCriteria,
    query: str,
) -> Role | None:
    url = str(item.get("link") or "").strip()
    if not _is_linkedin_job_url(url):
        return None

    raw_title = str(item.get("title") or "").strip()
    snippet = _normalize_space(str(item.get("snippet") or ""))
    if not raw_title:
        return None

    title, company = _parse_title_and_company(raw_title)
    if not title:
        return None

    location = _infer_location(criteria, raw_title, snippet)
    summary = _build_summary(title=title, company=company, snippet=snippet)
    match_reason = _build_match_reason(
        criteria=criteria,
        title=title,
        company=company,
        snippet=snippet,
        location=location,
        query=query,
    )

    return Role(
        company=company,
        title=title,
        url=url,
        location=location,
        summary=summary,
        match_reason=match_reason,
    )


def _is_linkedin_job_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = parsed.netloc.lower()
    path = parsed.path.lower()
    if "linkedin.com" not in hostname:
        return False
    if "/jobs" not in path:
        return False
    blocked_fragments = ("/jobs/search", "/jobs/collections", "/jobs/jobs-in")
    return not any(fragment in path for fragment in blocked_fragments)


def _parse_title_and_company(raw_title: str) -> tuple[str, str]:
    title = re.sub(r"\s*\|\s*LinkedIn\s*$", "", raw_title, flags=re.IGNORECASE)
    title = _normalize_space(title)

    hiring_match = re.match(r"(?P<company>.+?)\s+hiring\s+(?P<title>.+?)(?:\s+in\s+.+)?$", title, re.I)
    if hiring_match:
        return (
            _clean_title(hiring_match.group("title")),
            _clean_company(hiring_match.group("company")),
        )

    at_match = re.match(r"(?P<title>.+?)\s+at\s+(?P<company>.+?)(?:\s+-\s+.+)?$", title, re.I)
    if at_match:
        return (
            _clean_title(at_match.group("title")),
            _clean_company(at_match.group("company")),
        )

    if " - " in title:
        parts = [_normalize_space(part) for part in title.split(" - ") if part.strip()]
        if len(parts) >= 2:
            return _clean_title(parts[0]), _clean_company(parts[-1])

    return _clean_title(title), "Unknown company"


def _infer_location(criteria: JobCriteria, raw_title: str, snippet: str) -> str:
    haystack = f"{raw_title} {snippet}".lower()
    for location in criteria.location:
        normalized = _clean_query_part(location)
        location_tokens = normalized.replace("-", " ").lower()
        if location.lower() in haystack or location_tokens in haystack:
            return normalized
    return criteria.location[0] if criteria.location else "Location not available in Google result"


def _build_summary(*, title: str, company: str, snippet: str) -> str:
    if snippet:
        return snippet
    if company == "Unknown company":
        return f"Google returned a LinkedIn job result for {title}."
    return f"Google returned a LinkedIn job result for {title} at {company}."


def _build_match_reason(
    *,
    criteria: JobCriteria,
    title: str,
    company: str,
    snippet: str,
    location: str,
    query: str,
) -> str:
    haystack = f"{title} {company} {snippet}".lower()
    matched_keywords = [
        keyword for keyword in criteria.role_keywords if keyword.strip() and keyword.lower() in haystack
    ]

    reasons: list[str] = []
    if matched_keywords:
        reasons.append(f"Mentions {', '.join(matched_keywords[:3])} in the Google result.")
    else:
        reasons.append(f"Appeared for the query `{query}`.")

    if location and not location.startswith("Location not available"):
        reasons.append(f"Location signal matches {location}.")

    if snippet:
        reasons.append("Snippet gives enough context for a first-pass role fit check.")

    return " ".join(reasons)


def _dedupe_key(role: Role) -> str:
    if role.url:
        return role.url.lower().rstrip("/")
    return f"{role.company.lower()}::{role.title.lower()}"


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def _clean_query_part(value: str) -> str:
    return _normalize_space(value.replace("_", " ").replace("-", " "))


def _clean_title(value: str) -> str:
    value = re.sub(r"\s+in\s+.+$", "", value, flags=re.IGNORECASE)
    return _normalize_space(value).strip(" -")


def _clean_company(value: str) -> str:
    value = re.sub(r"\s+is\s+now\s+hiring.*$", "", value, flags=re.IGNORECASE)
    return _normalize_space(value).strip(" -")


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _read_error_body(exc: HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8")
    except Exception:
        return exc.reason

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body[:500]

    error = payload.get("error", {})
    message = error.get("message")
    if message:
        return str(message)
    return body[:500]
