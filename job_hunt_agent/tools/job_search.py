"""SerpAPI-backed job-search tool."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..schemas import EmploymentType, JobCriteria, Role


LOGGER = logging.getLogger(__name__)

SERPAPI_SEARCH_ENDPOINT = "https://serpapi.com/search.json"
DEFAULT_MAX_RESULTS = 5

# google_jobs (structured engine) config — replaces the Google-dork job search.
GOOGLE_JOBS_ENGINE = "google_jobs"
DEFAULT_COUNTRY = "in"
DEFAULT_LANGUAGE = "en"
DEFAULT_MAX_JOB_QUERIES = 4


def search_jobs(criteria: JobCriteria | dict[str, Any]) -> list[Role]:
    """Return currently-open job postings that match the supplied criteria.

    Uses SerpAPI's structured ``google_jobs`` engine (an aggregator across
    LinkedIn, Indeed, Greenhouse, Lever, and company boards) instead of
    scraping Google result snippets. The public contract stays fixed for the
    agent: JobCriteria -> list[Role]. Returns [] honestly when nothing matches,
    with no fallback that would reintroduce low-quality scraped data.
    """
    _load_dotenv_if_available()
    criteria = JobCriteria.model_validate(criteria)

    api_key = _get_serpapi_api_key()
    if not api_key:
        LOGGER.warning("SERPAPI_API_KEY or SERPAPI_KEY is missing; returning no roles.")
        return []

    roles: list[Role] = []
    seen_job_ids: set[str] = set()
    seen_keys: set[str] = set()

    for query, location in _build_google_jobs_requests(criteria)[:DEFAULT_MAX_JOB_QUERIES]:
        payload = _fetch_google_jobs(query=query, location=location, api_key=api_key)
        if not payload:
            continue

        for item in payload.get("jobs_results", []):
            if not isinstance(item, dict):
                continue
            role, job_id = _role_from_google_job(item, criteria=criteria)
            if role is None:
                continue
            if job_id and job_id in seen_job_ids:
                continue
            key = _dedupe_key(role)
            if key in seen_keys:
                continue

            seen_job_ids.add(job_id or key)
            seen_keys.add(key)
            roles.append(role)
            if len(roles) >= DEFAULT_MAX_RESULTS:
                return roles

    return roles


def _build_google_jobs_requests(criteria: JobCriteria) -> list[tuple[str, str]]:
    """Build (query, location) pairs to send to the google_jobs engine."""
    keywords = [_normalize_space(keyword) for keyword in criteria.role_keywords if keyword.strip()]
    locations = [_normalize_space(location) for location in criteria.location if location.strip()]
    if not keywords:
        keywords = ["backend engineer"]
    if not locations:
        locations = ["India"]

    requests: list[tuple[str, str]] = []
    for keyword in keywords[:3]:
        for location in locations[:3]:
            requests.append(_google_jobs_query_for(keyword, location))
    return _dedupe_request_pairs(requests)


def _google_jobs_query_for(keyword: str, location: str) -> tuple[str, str]:
    """Map a (keyword, location) into a google_jobs (q, location) pair.

    google_jobs treats ``location`` as a physical search origin, so remote
    requests become a 'remote' query anchored to the country instead.
    """
    lowered = location.lower()
    if "remote" in lowered:
        return f"{keyword} remote", "India"
    if "india" not in lowered:
        return keyword, f"{location}, India"
    return keyword, location


def _dedupe_request_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str]] = []
    for pair in pairs:
        key = (pair[0].lower(), pair[1].lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(pair)
    return deduped


def _fetch_google_jobs(*, query: str, location: str, api_key: str) -> dict[str, Any] | None:
    params = {
        "engine": GOOGLE_JOBS_ENGINE,
        "q": query,
        "api_key": api_key,
        "hl": DEFAULT_LANGUAGE,
        "gl": DEFAULT_COUNTRY,
    }
    if location:
        params["location"] = location
    url = f"{SERPAPI_SEARCH_ENDPOINT}?{urlencode(params)}"
    request = Request(url, headers={"Accept": "application/json"})

    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        LOGGER.warning("google_jobs request failed with HTTP %s: %s", exc.code, _read_error_body(exc))
        return None
    except (URLError, TimeoutError) as exc:
        LOGGER.warning("google_jobs request failed: %s", exc)
        return None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        LOGGER.warning("google_jobs returned malformed JSON: %s", exc)
        return None

    if not isinstance(payload, dict):
        LOGGER.warning(
            "google_jobs returned a non-object JSON payload: %s",
            type(payload).__name__,
        )
        return None
    error = payload.get("error")
    if error:
        # google_jobs returns an error string when a query simply has no matches.
        LOGGER.info("google_jobs returned no results for %r: %s", query, error)
        return None
    return payload


def _role_from_google_job(
    item: dict[str, Any],
    *,
    criteria: JobCriteria,
) -> tuple[Role | None, str | None]:
    title = _normalize_space(str(item.get("title") or ""))
    company = _normalize_space(str(item.get("company_name") or ""))
    if not title or not company:
        return None, None
    if not _title_looks_like_engineering(title):
        return None, None
    if _seniority_mismatches(criteria.seniority, title):
        return None, None

    apply_url = _best_apply_link(item)
    if not apply_url:
        return None, None

    raw_detected = item.get("detected_extensions")
    detected = raw_detected if isinstance(raw_detected, dict) else {}
    description = _normalize_space(str(item.get("description") or ""))
    location = _google_job_location(item, detected, criteria)
    summary = _summary_from_google_job(item, description, title=title, company=company)
    employment_type = _employment_type_from_google_job(
        item,
        detected=detected,
        description=description,
    )
    match_reason = _match_reason_from_google_job(
        criteria=criteria,
        title=title,
        description=description,
        detected=detected,
        location=location,
    )
    job_id = str(item.get("job_id") or "").strip() or None

    role = Role(
        company=company,
        title=title,
        url=apply_url,
        location=location,
        summary=summary,
        match_reason=match_reason,
        employment_type=employment_type,
    )
    return role, job_id


def _title_looks_like_engineering(title: str) -> bool:
    tokens = set(_normalize_match_text(title).split())
    role_terms = {"engineer", "developer", "sde", "programmer", "architect"}
    return bool(role_terms & tokens)


def _seniority_mismatches(target_seniority: str, title: str) -> bool:
    normalized = title.lower()
    if target_seniority != "junior":
        return False

    senior_terms = (
        r"\bsenior\b",
        r"\bsr\.?\b",
        r"\bstaff\b",
        r"\bprincipal\b",
        r"\blead\b",
        r"\bmanager\b",
        r"\barchitect\b",
        r"\bsde\s*[- ]?(?:2|ii|3|iii|4|iv)\b",
        r"\bsoftware (?:development )?engineer\s+(?:ii|iii|iv|2|3|4)\b",
        r"\bbackend engineer\s+(?:ii|iii|iv|2|3|4)\b",
    )
    return any(re.search(pattern, normalized) for pattern in senior_terms)


def _best_apply_link(item: dict[str, Any]) -> str:
    options = item.get("apply_options")
    if isinstance(options, list):
        for option in options:
            if isinstance(option, dict) and option.get("link"):
                return str(option["link"]).strip()
    share_link = item.get("share_link")
    return str(share_link).strip() if share_link else ""


def _google_job_location(
    item: dict[str, Any],
    detected: dict[str, Any],
    criteria: JobCriteria,
) -> str:
    if detected.get("work_from_home"):
        return "Remote"
    location = _normalize_space(str(item.get("location") or ""))
    if location:
        return location
    return criteria.location[0] if criteria.location else "Location not specified"


def _employment_type_from_google_job(
    item: dict[str, Any],
    *,
    detected: dict[str, Any],
    description: str,
) -> EmploymentType:
    schedule = _normalize_match_text(str(detected.get("schedule_type") or ""))
    title = _normalize_match_text(str(item.get("title") or ""))
    description_text = _normalize_match_text(description)

    explicit_intern = (
        re.search(r"\b(?:intern|internship|apprentice|apprenticeship)\b", schedule)
        or re.search(r"\b(?:intern|internship|apprentice|apprenticeship)\b", title)
        or re.search(
            r"\b(?:type|employment|position|role)\s+"
            r"(?:is\s+)?(?:an?\s+)?(?:intern|internship|apprentice|apprenticeship)\b",
            description_text,
        )
    )
    if explicit_intern:
        return EmploymentType.intern

    explicit_contract = (
        re.search(r"\b(?:contract|contractor|freelance|temporary|fixed term)\b", schedule)
        or re.search(r"\b(?:contract|contractor|freelance)\b", title)
        or re.search(
            r"\b(?:type|employment|engagement|position|role)\s+"
            r"(?:is\s+)?(?:a\s+)?(?:contract|contractor|freelance|temporary)\b",
            description_text,
        )
    )
    if explicit_contract or _has_hourly_contract_signal(item, detected, description):
        return EmploymentType.contract

    if re.search(r"\b(?:full time|permanent)\b", schedule):
        return EmploymentType.full_time

    return EmploymentType.unknown


def _has_hourly_contract_signal(
    item: dict[str, Any],
    detected: dict[str, Any],
    description: str,
) -> bool:
    raw_text = " ".join(
        (
            str(item.get("title") or ""),
            str(detected.get("salary") or ""),
            description,
        )
    ).lower()
    hourly_pay = bool(
        re.search(
            r"(?:[$₹£€]\s*[\d,.]+(?:\s*[–—-]\s*[$₹£€]?\s*[\d,.]+)?\s*"
            r"(?:/|\bper\b|\ban\b)\s*(?:hour|hr)\b|\bhourly\s+"
            r"(?:rate|pay|compensation)\b)",
            raw_text,
        )
    )
    contract_context = bool(
        re.search(
            r"\b(?:contract|contractor|freelance|gig|accepted task|no fixed task)\b",
            raw_text,
        )
    )
    return hourly_pay and contract_context


def _summary_from_google_job(
    item: dict[str, Any],
    description: str,
    *,
    title: str,
    company: str,
) -> str:
    highlights = item.get("job_highlights")
    if isinstance(highlights, list):
        for section in highlights:
            if not isinstance(section, dict):
                continue
            section_title = str(section.get("title") or "").lower()
            entries = section.get("items") or []
            if section_title in {"responsibilities", "qualifications"} and entries:
                joined = " ".join(
                    _normalize_space(str(entry)) for entry in entries[:2] if str(entry).strip()
                )
                if joined:
                    return _truncate(joined, 320)

    if description:
        sentences = re.split(r"(?<=[.!?])\s+", description)
        summary = " ".join(sentences[:2]).strip()
        if summary:
            return _truncate(summary, 320)

    return f"Open {title} role at {company}."


def _match_reason_from_google_job(
    *,
    criteria: JobCriteria,
    title: str,
    description: str,
    detected: dict[str, Any],
    location: str,
) -> str:
    reasons: list[str] = []

    matched = _first_keyword_sentence(description, criteria.role_keywords)
    if matched is not None:
        keyword, sentence = matched
        reasons.append(f'Job description mentions "{keyword}": {_truncate(sentence, 160)}')
    else:
        reasons.append(f"'{title}' matches your backend search.")

    posted_at = detected.get("posted_at")
    if posted_at:
        reasons.append(f"Posted {posted_at}.")
    if location:
        reasons.append(f"Location: {location}.")

    return " ".join(reasons)


def _first_keyword_sentence(
    description: str,
    keywords: list[str],
) -> tuple[str, str] | None:
    if not description:
        return None
    for keyword in keywords:
        token = keyword.strip()
        if not token:
            continue
        match = re.search(
            r"[^.!?]*\b" + re.escape(token) + r"\b[^.!?]*[.!?]",
            description,
            flags=re.IGNORECASE,
        )
        if match:
            return keyword, _normalize_space(match.group(0))
    return None


def _truncate(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "…"


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    load_dotenv()


def _get_serpapi_api_key() -> str | None:
    return os.getenv("SERPAPI_API_KEY") or os.getenv("SERPAPI_KEY")


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


def _dedupe_key(role: Role) -> str:
    company = role.company.lower().strip()
    title = role.title.lower().strip()
    if title and company and company != "unknown company":
        return f"{company}::{title}"
    return role.url.lower().rstrip("/")


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalize_match_text(value: str) -> str:
    value = value.lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return _normalize_space(value)


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
