"""Amazon Jobs public-search source adapter."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from opentelemetry import trace
from pydantic import ValidationError

from job_hunt_agent.schemas import (
    Company,
    CompanySource,
    EmploymentType,
    JobCriteria,
    Role,
)
from job_hunt_agent.sources.base import safe_url_path_parts


LOGGER = logging.getLogger(__name__)
TRACER = trace.get_tracer(__name__)

AMAZON_SEARCH_ENDPOINT = "https://www.amazon.jobs/en/search.json"
AMAZON_JOBS_ORIGIN = "https://www.amazon.jobs"
AMAZON_SOURCE_TOKENS = {"amazon", "amazon.jobs"}
AMAZON_TRUSTED_HOSTS = {
    "account.amazon.com",
    "account.amazon.jobs",
    "amazon.jobs",
    "www.amazon.jobs",
}
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_RESULT_LIMIT = 100
DEFAULT_MAX_QUERIES = 3
SUMMARY_LIMIT = 520

_COUNTRY_ISO3 = {
    "au": "AUS",
    "br": "BRA",
    "ca": "CAN",
    "de": "DEU",
    "es": "ESP",
    "fr": "FRA",
    "gb": "GBR",
    "in": "IND",
    "it": "ITA",
    "jp": "JPN",
    "mx": "MEX",
    "nl": "NLD",
    "pl": "POL",
    "sg": "SGP",
    "uk": "GBR",
    "us": "USA",
}
_ADVANCED_LEVELS = {
    "architect",
    "director",
    "head",
    "lead",
    "manager",
    "principal",
    "senior",
    "sr",
    "staff",
}
_JUNIOR_LEVELS = {"associate", "entry", "graduate", "jr", "junior"}
_NON_JUNIOR_LEVEL_PATTERN = re.compile(
    r"\b(?:engineer|developer|sde)\s+"
    r"(?:ii|iii|iv|v|[2-9]|l[2-9]|ic[2-9])\b"
    r"|\b(?:level|grade)\s+(?:ii|iii|iv|v|[2-9])\b"
    r"|\b(?:l|ic)[2-9]\b",
)
_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "div",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "ol",
    "p",
    "section",
    "table",
    "tr",
    "ul",
}
_SKIP_TAGS = {"script", "style"}


class AmazonAdapter:
    """Fetch and normalize currently published roles from amazon.jobs."""

    name = CompanySource.bespoke.value

    def supports(self, company: Company) -> bool:
        """Return whether ``company`` is the configured Amazon bespoke source."""
        token = (company.source_token or "").strip().casefold()
        return (
            company.source is CompanySource.bespoke
            and company.slug == "amazon"
            and token in AMAZON_SOURCE_TOKENS
        )

    def fetch_open_roles(
        self,
        company: Company,
        criteria: JobCriteria,
    ) -> list[Role]:
        """Return matching Amazon roles, or an honest empty list on failure."""
        with TRACER.start_as_current_span(
            "job_source.amazon.fetch_open_roles",
        ) as span:
            span.set_attribute("job_source.name", self.name)
            span.set_attribute("job_source.company_slug", company.slug)
            roles = self._fetch_open_roles(company, criteria)
            span.set_attribute("job_source.role_count", len(roles))
            return roles

    def _fetch_open_roles(
        self,
        company: Company,
        criteria: JobCriteria,
    ) -> list[Role]:
        if not self.supports(company):
            LOGGER.warning(
                "Amazon source is not configured for company %s.",
                company.slug,
            )
            return []

        roles: list[Role] = []
        seen_ids: set[str] = set()
        for query in _search_queries(criteria)[:DEFAULT_MAX_QUERIES]:
            payload = _fetch_search_payload(query=query, country=criteria.country)
            if payload is None:
                continue
            for index, item in enumerate(payload):
                if not isinstance(item, dict):
                    LOGGER.warning(
                        "Skipping malformed Amazon job %s: expected an object.",
                        index,
                    )
                    continue
                try:
                    role = _role_from_job(
                        item,
                        company=company,
                        criteria=criteria,
                    )
                except (TypeError, ValueError, ValidationError) as exc:
                    LOGGER.warning(
                        "Skipping malformed Amazon job %s: %s.",
                        item.get("id_icims", item.get("id", index)),
                        exc,
                    )
                    continue
                if role is None:
                    continue
                job_id = _clean_text(item.get("id_icims") or item.get("id"))
                dedupe_key = job_id or role.url.casefold().rstrip("/")
                if dedupe_key in seen_ids:
                    continue
                seen_ids.add(dedupe_key)
                roles.append(role)

        if not roles:
            LOGGER.info("No Amazon roles matched the criteria.")
        return roles


AmazonSourceAdapter = AmazonAdapter


def _search_queries(criteria: JobCriteria) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()
    for keyword in criteria.role_keywords:
        query = _clean_text(keyword)
        key = query.casefold()
        if not query or key in seen:
            continue
        seen.add(key)
        queries.append(query)
    return queries or [""]


def _fetch_search_payload(*, query: str, country: str) -> list[Any] | None:
    params: list[tuple[str, str]] = [
        ("base_query", query),
        ("offset", "0"),
        ("result_limit", str(DEFAULT_RESULT_LIMIT)),
    ]
    country_iso3 = _country_iso3(country)
    if country_iso3:
        params.append(("normalized_country_code[]", country_iso3))

    url = f"{AMAZON_SEARCH_ENDPOINT}?{urlencode(params)}"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "job-hunt-signal/2",
        },
    )

    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            raw_payload = response.read()
    except HTTPError as exc:
        LOGGER.warning("Amazon Jobs request failed with HTTP %s.", exc.code)
        return None
    except (URLError, TimeoutError, OSError) as exc:
        LOGGER.warning("Amazon Jobs request failed: %s.", exc)
        return None

    try:
        payload = json.loads(raw_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        LOGGER.warning("Amazon Jobs returned malformed JSON: %s.", exc)
        return None

    if not isinstance(payload, dict):
        LOGGER.warning("Amazon Jobs returned a malformed payload: expected an object.")
        return None
    if payload.get("error"):
        LOGGER.warning("Amazon Jobs returned an error: %s.", payload["error"])
        return None
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        LOGGER.warning("Amazon Jobs returned a malformed payload: jobs is not a list.")
        return None
    if not jobs:
        LOGGER.info("Amazon Jobs returned no published roles for query %r.", query)
    return jobs


def _role_from_job(
    item: dict[str, Any],
    *,
    company: Company,
    criteria: JobCriteria,
) -> Role | None:
    title = _clean_text(item.get("title"))
    if not title:
        raise ValueError("missing title")

    apply_url = _trusted_amazon_url(item.get("url_next_step"))
    public_url = _public_job_url(item.get("job_path"))
    url = apply_url or public_url
    if not url:
        raise ValueError("missing trusted first-party apply URL")

    raw_description = _raw_description(item)
    searchable = _normalize_match_text(f"{title} {raw_description}")
    matched_keywords = _matched_keywords(criteria.role_keywords, searchable)
    if _has_keywords(criteria) and not matched_keywords:
        return None
    if _seniority_mismatches(criteria.seniority, title):
        return None
    if not _matches_location(criteria.location, item):
        return None

    employment_type = _employment_type(item)
    if (
        criteria.employment_types
        and employment_type is not EmploymentType.unknown
        and employment_type not in criteria.employment_types
    ):
        return None

    posted_at = _normalized_posted_date(item.get("posted_date"))
    source_job_id = _clean_text(item.get("id_icims") or item.get("id")) or None
    if not _within_max_age(
        posted_at,
        criteria.max_age_days,
        job_id=source_job_id or "unknown",
    ):
        return None

    location = (
        _clean_text(item.get("normalized_location"))
        or _clean_text(item.get("location"))
        or "Location not specified"
    )
    subsidiary = _clean_text(item.get("company_name"))
    return Role(
        company=company.name,
        title=title,
        url=url,
        location=location,
        summary=_summary(item, raw_description=raw_description),
        match_reason=_match_reason(
            company=company,
            subsidiary=subsidiary,
            matched_keywords=matched_keywords,
            location=location,
            posted_at=posted_at,
        ),
        source=CompanySource.bespoke,
        company_slug=company.slug if source_job_id is not None else None,
        source_job_id=source_job_id,
        apply_urls=_dedupe_urls([apply_url, public_url]),
        posted_at=posted_at,
        employment_type=employment_type,
        raw_description=raw_description or None,
        confidence=1.0,
    )


def _country_iso3(country: str) -> str:
    normalized = _clean_text(country).casefold()
    if len(normalized) == 3 and normalized.isalpha():
        return normalized.upper()
    return _COUNTRY_ISO3.get(normalized, "")


def _trusted_amazon_url(value: Any) -> str:
    url = _clean_text(value)
    if not url or "\\" in url or any(character.isspace() for character in url):
        return ""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme != "https"
        or parsed.hostname not in AMAZON_TRUSTED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or safe_url_path_parts(parsed.path) is None
    ):
        return ""
    return url


def _public_job_url(value: Any) -> str:
    path = _clean_text(value)
    if not re.fullmatch(r"/en/jobs/\d+/[a-z0-9-]+", path):
        return ""
    return f"{AMAZON_JOBS_ORIGIN}{path}"


def _raw_description(item: dict[str, Any]) -> str:
    sections: list[str] = []
    description = _html_to_text(item.get("description"))
    if description:
        sections.append(description)

    basic = _html_to_text(item.get("basic_qualifications"))
    if basic:
        sections.append(f"Basic qualifications: {basic}")

    preferred = _html_to_text(item.get("preferred_qualifications"))
    if preferred:
        sections.append(f"Preferred qualifications: {preferred}")
    return _clean_text(" ".join(sections))


def _summary(item: dict[str, Any], *, raw_description: str) -> str:
    short = _html_to_text(item.get("description_short"))
    if short:
        return _truncate(short, SUMMARY_LIMIT)
    sentences = re.split(r"(?<=[.!?])\s+", raw_description)
    return _truncate(" ".join(sentences[:2]), SUMMARY_LIMIT)


def _match_reason(
    *,
    company: Company,
    subsidiary: str,
    matched_keywords: list[str],
    location: str,
    posted_at: str | None,
) -> str:
    details = [f"Direct posting from {company.name}'s amazon.jobs board."]
    if subsidiary and subsidiary.casefold() != company.name.casefold():
        details.append(f"Hiring entity: {subsidiary}.")
    if matched_keywords:
        quoted = ", ".join(f'"{keyword}"' for keyword in matched_keywords[:3])
        details.append(f"Listing text matches requested keyword {quoted}.")
    if location:
        details.append(f"Location: {location}.")
    if posted_at:
        details.append(f"Posted {posted_at}.")
    return " ".join(details)


def _employment_type(item: dict[str, Any]) -> EmploymentType:
    schedule = _normalize_match_text(item.get("job_schedule_type"))
    title = _normalize_match_text(item.get("title"))
    evidence = f"{title} {schedule}".strip()
    if item.get("is_intern") is True or re.search(
        r"\b(?:intern|internship|apprentice)\b",
        evidence,
    ):
        return EmploymentType.intern
    if re.search(
        r"\b(?:contract|contractor|temporary|seasonal|fixed term|ftc)\b",
        evidence,
    ):
        return EmploymentType.contract
    if re.search(r"\b(?:full time|fulltime|regular)\b", evidence):
        return EmploymentType.full_time
    return EmploymentType.unknown


def _normalized_posted_date(value: Any) -> str | None:
    posted_date = _clean_text(value)
    if not posted_date:
        return None
    try:
        parsed = datetime.strptime(posted_date, "%B %d, %Y")
    except ValueError:
        return None
    return parsed.date().isoformat()


def _within_max_age(
    posted_at: str | None,
    max_age_days: int | None,
    *,
    job_id: str,
) -> bool:
    if max_age_days is None:
        return True
    if posted_at is None:
        LOGGER.warning(
            "Skipping Amazon job %s because posted_date is missing or invalid.",
            job_id,
        )
        return False
    posted = datetime.fromisoformat(posted_at).date()
    cutoff = _utc_now().date() - timedelta(days=max_age_days)
    return posted >= cutoff


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _has_keywords(criteria: JobCriteria) -> bool:
    return any(keyword.strip() for keyword in criteria.role_keywords)


def _matched_keywords(keywords: list[str], searchable: str) -> list[str]:
    matched: list[str] = []
    for keyword in keywords:
        normalized = _normalize_match_text(keyword)
        if normalized and re.search(
            rf"(?:^|\s){re.escape(normalized)}(?:\s|$)",
            searchable,
        ):
            matched.append(_clean_text(keyword))
    return matched


def _seniority_mismatches(target: str, title: str) -> bool:
    normalized = _normalize_match_text(title)
    tokens = set(normalized.split())
    advanced = bool(tokens & _ADVANCED_LEVELS)
    junior = bool(tokens & _JUNIOR_LEVELS) or bool(
        re.search(r"\b(?:engineer|developer|sde)\s+(?:i|1)\b", normalized),
    )
    non_junior_level = bool(_NON_JUNIOR_LEVEL_PATTERN.search(normalized))

    if target == "junior":
        return advanced or (non_junior_level and not junior)
    if target == "mid":
        return advanced or junior
    if target == "senior":
        return junior or bool(tokens & {"principal", "staff"})
    if target == "staff":
        return junior or non_junior_level
    return False


def _matches_location(requested: list[str], item: dict[str, Any]) -> bool:
    targets = [
        _location_tokens(location)
        for location in requested
        if _clean_text(location)
    ]
    if not targets:
        return True

    evidence = [_location_tokens(value) for value in _location_evidence(item)]
    return any(
        target and target.issubset(candidate)
        for target in targets
        for candidate in evidence
    )


def _location_evidence(item: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    primary = " ".join(
        filter(
            None,
            (
                _clean_text(item.get("location")),
                _clean_text(item.get("normalized_location")),
                _clean_text(item.get("city")),
                _clean_text(item.get("state")),
                _clean_text(item.get("country_code")),
            ),
        ),
    )
    if primary:
        evidence.append(primary)

    locations = item.get("locations")
    if not isinstance(locations, list):
        return evidence
    for raw_location in locations:
        location: Any = raw_location
        if isinstance(raw_location, str):
            try:
                location = json.loads(raw_location)
            except json.JSONDecodeError:
                location = raw_location
        if isinstance(location, dict):
            label = " ".join(
                _clean_text(location.get(key))
                for key in (
                    "location",
                    "normalizedLocation",
                    "normalizedCountryName",
                    "normalizedCountryCode",
                    "normalizedStateName",
                    "normalizedCityName",
                    "city",
                    "region",
                    "type",
                )
                if _clean_text(location.get(key))
            )
        else:
            label = _clean_text(location)
        if label:
            evidence.append(label)
    return evidence


def _location_tokens(value: str) -> set[str]:
    aliases = {
        "bangalore": "bengaluru",
        "in": "india",
        "ind": "india",
        "ka": "karnataka",
        "uk": "united kingdom",
        "us": "united states",
        "usa": "united states",
        "virtual": "remote",
    }
    normalized = _normalize_match_text(value)
    expanded = " ".join(aliases.get(token, token) for token in normalized.split())
    return set(expanded.split())


def _dedupe_urls(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        key = value.casefold().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipped_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag in _SKIP_TAGS:
            self._skipped_depth += 1
        elif not self._skipped_depth and tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skipped_depth:
            self._skipped_depth -= 1
        elif not self._skipped_depth and tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skipped_depth:
            self.parts.append(data)


def _html_to_text(value: Any) -> str:
    html = _clean_text(value)
    if not html:
        return ""
    parser = _PlainTextParser()
    parser.feed(html)
    parser.close()
    return _clean_text("".join(parser.parts))


def _truncate(value: str, limit: int) -> str:
    value = _clean_text(value)
    if len(value) <= limit:
        return value
    return f"{value[:limit].rstrip()}…"


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _normalize_match_text(value: Any) -> str:
    normalized = _clean_text(value).casefold().replace("&", " and ")
    return _clean_text(re.sub(r"[^a-z0-9]+", " ", normalized))
