"""SerpAPI-backed job-search tool."""

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

SERPAPI_SEARCH_ENDPOINT = "https://serpapi.com/search.json"
DEFAULT_NUM_RESULTS_PER_QUERY = 5
DEFAULT_MAX_RESULTS = 5
DEFAULT_MAX_QUERIES = 4


def search_jobs(criteria: JobCriteria | dict[str, Any]) -> list[Role]:
    """Return LinkedIn job postings that match the supplied criteria.

    Internals normalize SerpAPI's result shape, but the public contract stays
    fixed for the agent: JobCriteria -> list[Role].
    """
    _load_dotenv_if_available()
    criteria = JobCriteria.model_validate(criteria)

    api_key = _get_serpapi_api_key()
    if not api_key:
        LOGGER.warning("SERPAPI_API_KEY or SERPAPI_KEY is missing; returning no roles.")
        return []

    roles: list[Role] = []
    seen: set[str] = set()

    for query in _build_queries(criteria)[:DEFAULT_MAX_QUERIES]:
        payload = _fetch_serpapi_search(
            query=query,
            api_key=api_key,
            num_results=DEFAULT_NUM_RESULTS_PER_QUERY,
        )
        if not payload:
            continue

        for item in _iter_serpapi_results(payload):
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


def _get_serpapi_api_key() -> str | None:
    return os.getenv("SERPAPI_API_KEY") or os.getenv("SERPAPI_KEY")


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


def _fetch_serpapi_search(
    *,
    query: str,
    api_key: str,
    num_results: int,
) -> dict[str, Any] | None:
    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "num": str(min(max(num_results, 1), 10)),
        "hl": "en",
    }
    url = f"{SERPAPI_SEARCH_ENDPOINT}?{urlencode(params)}"
    request = Request(url, headers={"Accept": "application/json"})

    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = _read_error_body(exc)
        LOGGER.warning("SerpAPI request failed with HTTP %s: %s", exc.code, body)
    except (URLError, TimeoutError) as exc:
        LOGGER.warning("SerpAPI request failed: %s", exc)
    except json.JSONDecodeError as exc:
        LOGGER.warning("SerpAPI returned malformed JSON: %s", exc)
    else:
        error = payload.get("error")
        if error:
            LOGGER.warning("SerpAPI returned an error: %s", error)
            return None
        return payload

    return None


def _iter_serpapi_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    organic_results = payload.get("organic_results", [])
    if not isinstance(organic_results, list):
        return []
    return [item for item in organic_results if isinstance(item, dict)]


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
    url_company = _company_from_linkedin_job_url(url)
    if url_company and _should_use_url_company(company):
        company = url_company
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


def _company_from_linkedin_job_url(url: str) -> str | None:
    path = urlparse(url).path.lower()
    match = re.match(r"/jobs/view/.+-at-(?P<company>[a-z0-9-]+)-\d+/?$", path)
    if not match:
        return None
    return _company_from_slug(match.group("company"))


def _should_use_url_company(company: str) -> bool:
    generic_values = {
        "unknown company",
        "remote",
        "hybrid",
        "onsite",
        "on-site",
        "india",
        "remote india",
    }
    return company.lower().strip() in generic_values


def _company_from_slug(slug: str) -> str:
    words = [word for word in slug.split("-") if word]
    return " ".join(_format_company_word(word) for word in words)


def _format_company_word(word: str) -> str:
    if len(word) <= 3 and word.isalpha():
        return word.upper()
    return word.capitalize()


def _infer_location(criteria: JobCriteria, raw_title: str, snippet: str) -> str:
    haystack = f"{raw_title} {snippet}".lower()
    for location in criteria.location:
        normalized = _clean_query_part(location)
        location_tokens = normalized.replace("-", " ").lower()
        if location.lower() in haystack or location_tokens in haystack:
            return normalized
    return criteria.location[0] if criteria.location else "Location not available in search result"


def _build_summary(*, title: str, company: str, snippet: str) -> str:
    if snippet:
        return snippet
    if company == "Unknown company":
        return f"SerpAPI returned a LinkedIn job result for {title}."
    return f"SerpAPI returned a LinkedIn job result for {title} at {company}."


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
        reasons.append(f"Mentions {', '.join(matched_keywords[:3])} in the search result.")
    else:
        reasons.append(f"Appeared for the query `{query}`.")

    if location and not location.startswith("Location not available"):
        reasons.append(f"Location signal matches {location}.")

    if snippet:
        reasons.append("Snippet gives enough context for a first-pass role fit check.")

    return " ".join(reasons)


def _dedupe_key(role: Role) -> str:
    company = role.company.lower().strip()
    title = role.title.lower().strip()
    if title and company and company != "unknown company":
        return f"{company}::{title}"
    return role.url.lower().rstrip("/")


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
    if isinstance(error, str):
        return error
    message = error.get("message") if isinstance(error, dict) else None
    if message:
        return str(message)
    return body[:500]
