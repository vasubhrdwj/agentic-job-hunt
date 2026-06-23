"""SerpAPI Google Jobs fallback source adapter."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlsplit

from opentelemetry import trace

from job_hunt_agent.schemas import (
    Company,
    CompanySource,
    EmploymentType,
    JobCriteria,
    Role,
)
from job_hunt_agent.sources.base import safe_url_path_parts
from job_hunt_agent.tools import job_search


LOGGER = logging.getLogger(__name__)
TRACER = trace.get_tracer(__name__)

_LEGAL_COMPANY_SUFFIXES = {
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "limited",
    "llc",
    "llp",
    "ltd",
    "plc",
    "private",
    "pvt",
}
_VIA_DECORATORS = {"careers", "career", "jobs", "job", "official"}
_LOW_CONFIDENCE = 0.35
_GOOGLE_JOBS_CONFIDENCE = 0.65
_FIRST_PARTY_CONFIDENCE = 0.8


class GoogleJobsAdapter:
    """Fetch company-filtered roles from Google Jobs as a final fallback."""

    name = CompanySource.google_jobs.value

    def supports(self, company: Company) -> bool:
        """Google Jobs can be attempted for any company."""
        del company
        return True

    def fetch_open_roles(
        self,
        company: Company,
        criteria: JobCriteria,
    ) -> list[Role]:
        """Return matching Google Jobs roles, or honestly return no results."""
        with TRACER.start_as_current_span(
            "job_source.google_jobs.fetch_open_roles",
        ) as span:
            span.set_attribute("job_source.name", self.name)
            span.set_attribute("job_source.company_slug", company.slug)
            roles, query_count = self._fetch_open_roles(company, criteria)
            span.set_attribute("job_source.query_count", query_count)
            span.set_attribute("job_source.role_count", len(roles))
            span.set_attribute(
                "job_source.first_party_role_count",
                sum(_is_first_party_url(role.url, company) for role in roles),
            )
            span.set_attribute(
                "job_source.low_confidence_role_count",
                sum(role.confidence < 0.5 for role in roles),
            )
            return roles

    def _fetch_open_roles(
        self,
        company: Company,
        criteria: JobCriteria,
    ) -> tuple[list[Role], int]:
        job_search._load_dotenv_if_available()
        api_key = job_search._get_serpapi_api_key()
        if not api_key:
            LOGGER.warning(
                "SERPAPI_API_KEY or SERPAPI_KEY is missing; cannot search "
                "Google Jobs for %s.",
                company.slug,
            )
            return [], 0

        roles: list[Role] = []
        seen_job_ids: set[str] = set()
        seen_role_keys: set[tuple[str, str]] = set()
        requests = _company_requests(company, criteria)

        for query, location in requests:
            try:
                payload = job_search._fetch_google_jobs(
                    query=query,
                    location=location,
                    api_key=api_key,
                )
            except Exception as exc:
                LOGGER.warning(
                    "Google Jobs fetch failed for %s: %s",
                    company.slug,
                    exc,
                )
                continue
            if not payload:
                continue
            if not isinstance(payload, dict):
                LOGGER.warning(
                    "Google Jobs returned a malformed payload for %s.",
                    company.slug,
                )
                continue
            jobs = payload.get("jobs_results")
            if not isinstance(jobs, list):
                LOGGER.warning(
                    "Google Jobs returned a malformed jobs_results value for %s.",
                    company.slug,
                )
                continue

            for item in jobs:
                if not isinstance(item, dict):
                    continue
                if not _company_names_match(item.get("company_name"), company):
                    continue

                apply_urls = _ordered_apply_urls(item, company)
                if not apply_urls:
                    continue

                mapped, job_id = job_search._role_from_google_job(
                    item,
                    criteria=criteria,
                )
                if mapped is None:
                    continue
                if (
                    criteria.employment_types
                    and mapped.employment_type is not EmploymentType.unknown
                    and mapped.employment_type not in criteria.employment_types
                ):
                    continue
                if job_id and job_id in seen_job_ids:
                    continue

                role_key = (
                    job_search._normalize_match_text(mapped.title),
                    apply_urls[0].casefold().rstrip("/"),
                )
                if role_key in seen_role_keys:
                    continue

                role = mapped.model_copy(
                    update={
                        "company": company.name,
                        "url": apply_urls[0],
                        "source": CompanySource.google_jobs,
                        "apply_urls": apply_urls,
                        "posted_at": _posted_at(item),
                        "raw_description": _raw_description(item),
                        "confidence": _confidence_for(item, company),
                    },
                )
                roles.append(role)
                if job_id:
                    seen_job_ids.add(job_id)
                seen_role_keys.add(role_key)

        if not roles:
            LOGGER.info("Google Jobs returned no matching roles for %s.", company.slug)
        return roles, len(requests)


GoogleJobsSourceAdapter = GoogleJobsAdapter


def _company_requests(
    company: Company,
    criteria: JobCriteria,
) -> list[tuple[str, str]]:
    requests: list[tuple[str, str]] = []
    for query, location in job_search._build_google_jobs_requests(criteria):
        requests.append((f'{query} "{company.name}"', location))
    return requests[: job_search.DEFAULT_MAX_JOB_QUERIES]


def _company_names_match(value: Any, company: Company) -> bool:
    candidate_tokens = _company_tokens(value)
    expected_tokens = _company_tokens(company.name)
    if not candidate_tokens or not expected_tokens:
        return False
    if candidate_tokens == expected_tokens:
        return True

    allowed_locations = {
        token
        for location in company.hire_locations
        for token in job_search._normalize_match_text(location).split()
    }
    suffix = candidate_tokens[len(expected_tokens) :]
    return bool(
        suffix
        and candidate_tokens[: len(expected_tokens)] == expected_tokens
        and set(suffix) <= allowed_locations
    )


def _company_tokens(value: Any) -> list[str]:
    tokens = job_search._normalize_match_text(str(value or "")).split()
    while tokens and tokens[-1] in _LEGAL_COMPANY_SUFFIXES:
        tokens.pop()
    return tokens


def _ordered_apply_urls(item: dict[str, Any], company: Company) -> list[str]:
    urls: list[str] = []
    options = item.get("apply_options")
    if isinstance(options, list):
        for option in options:
            if not isinstance(option, dict):
                continue
            url = _valid_https_url(option.get("link"))
            if url and url not in urls:
                urls.append(url)

    if not urls:
        share_url = _valid_https_url(item.get("share_link"))
        if share_url:
            urls.append(share_url)

    first_party = [url for url in urls if _is_first_party_url(url, company)]
    third_party = [url for url in urls if url not in first_party]
    return [*first_party, *third_party]


def _valid_https_url(value: Any) -> str:
    url = str(value or "").strip()
    if (
        not url
        or "\\" in url
        or any(ord(character) < 32 for character in url)
        or any(character.isspace() for character in url)
    ):
        return ""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or safe_url_path_parts(parsed.path) is None
    ):
        return ""
    return url


def _is_first_party_url(url: str, company: Company) -> bool:
    valid_url = _valid_https_url(url)
    if not valid_url:
        return False
    hostname = (urlsplit(valid_url).hostname or "").casefold().rstrip(".")
    if not hostname:
        return False
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in (_normalized_domain(value) for value in company.careers_domains)
        if domain
    )


def _normalized_domain(value: str) -> str:
    candidate = value.strip().casefold()
    if not candidate:
        return ""
    try:
        parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
        parsed.port
    except ValueError:
        return ""
    if parsed.username is not None or parsed.password is not None:
        return ""
    return (parsed.hostname or "").rstrip(".")


def _posted_at(item: dict[str, Any]) -> str | None:
    detected = item.get("detected_extensions")
    if not isinstance(detected, dict):
        return None
    value = str(detected.get("posted_at") or "").strip()
    return value or None


def _raw_description(item: dict[str, Any]) -> str | None:
    value = str(item.get("description") or "").strip()
    return value or None


def _confidence_for(item: dict[str, Any], company: Company) -> float:
    detected = item.get("detected_extensions")
    if not isinstance(detected, dict):
        detected = {}
    description = str(item.get("description") or "")
    if _has_untrusted_via(item, company) or job_search._has_hourly_contract_signal(
        item,
        detected,
        description,
    ):
        return _LOW_CONFIDENCE
    if any(_is_first_party_url(url, company) for url in _ordered_apply_urls(item, company)):
        return _FIRST_PARTY_CONFIDENCE
    return _GOOGLE_JOBS_CONFIDENCE


def _has_untrusted_via(item: dict[str, Any], company: Company) -> bool:
    via = str(item.get("via") or "").strip()
    if not via:
        return False
    via_tokens = [
        token
        for token in _company_tokens(via)
        if token not in _VIA_DECORATORS
    ]
    company_tokens = _company_tokens(company.name)
    return via_tokens != company_tokens


__all__ = ["GoogleJobsAdapter", "GoogleJobsSourceAdapter"]
