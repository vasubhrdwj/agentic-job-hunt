"""Workday public CXS job-board source adapter."""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import quote, urlsplit

from opentelemetry import trace

from job_hunt_agent.schemas import Company, CompanySource, JobCriteria, Role
from job_hunt_agent.sources import ashby as _common
from job_hunt_agent.sources.base import safe_url_path_parts


LOGGER = logging.getLogger(__name__)
TRACER = trace.get_tracer(__name__)

PAGE_SIZE = 20
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+:[A-Za-z0-9_-]+$")


class WorkdayAdapter:
    """Fetch and normalize public Workday CXS postings."""

    name = CompanySource.workday.value

    def supports(self, company: Company) -> bool:
        token = (company.source_token or "").strip()
        return (
            company.source is CompanySource.workday
            and bool(_TOKEN_PATTERN.fullmatch(token))
            and _workday_host(company) is not None
        )

    def fetch_open_roles(
        self,
        company: Company,
        criteria: JobCriteria,
    ) -> list[Role]:
        with TRACER.start_as_current_span(
            "job_source.workday.fetch_open_roles",
        ) as span:
            span.set_attribute("job_source.name", self.name)
            span.set_attribute("job_source.company_slug", company.slug)
            span.set_attribute(
                "job_source.source_token_configured",
                bool(company.source_token),
            )
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
                "Workday source is not configured for company %s; expected "
                "tenant:site token and a myworkdayjobs.com careers domain.",
                company.slug,
            )
            return []

        tenant, site = company.source_token.strip().split(":", 1)
        host = _workday_host(company)
        assert host is not None
        base = f"https://{host}/wday/cxs/{tenant}/{site}"
        postings = _fetch_postings(base, criteria, company.slug)
        if postings is None:
            return []

        roles: list[Role] = []
        for index, posting in enumerate(postings):
            if not isinstance(posting, dict):
                LOGGER.warning(
                    "Skipping malformed Workday posting %s for %s.",
                    index,
                    company.slug,
                )
                continue
            external_path = _safe_external_path(posting.get("externalPath"))
            if not external_path:
                LOGGER.warning(
                    "Skipping Workday posting %s for %s: invalid externalPath.",
                    index,
                    company.slug,
                )
                continue
            detail = _common._request_json(
                base + external_path,
                source="Workday",
                company_slug=company.slug,
            )
            if not isinstance(detail, dict):
                continue
            try:
                role = _role_from_detail(
                    detail,
                    company=company,
                    criteria=criteria,
                    expected_host=host,
                    site=site,
                )
            except (TypeError, ValueError) as exc:
                LOGGER.warning(
                    "Skipping malformed Workday posting %s for %s: %s.",
                    external_path,
                    company.slug,
                    exc,
                )
                continue
            if role is not None:
                roles.append(role)

        if not roles:
            LOGGER.info("No Workday roles for %s matched the criteria.", company.slug)
        return roles


WorkdaySourceAdapter = WorkdayAdapter


def _fetch_postings(
    base: str,
    criteria: JobCriteria,
    company_slug: str,
) -> list[Any] | None:
    postings: list[Any] = []
    seen_external_paths: set[str] = set()
    for search_text in _search_queries(criteria):
        offset = 0
        fetched_for_query = 0
        while True:
            body = json.dumps(
                {
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "searchText": search_text,
                },
            ).encode("utf-8")
            payload = _common._request_json(
                base + "/jobs",
                source="Workday",
                company_slug=company_slug,
                data=body,
            )
            if payload is None:
                return None
            if not isinstance(payload, dict) or not isinstance(
                payload.get("jobPostings"),
                list,
            ):
                LOGGER.warning(
                    "Workday returned a malformed jobs payload for %s.",
                    company_slug,
                )
                return None
            page = payload["jobPostings"]
            fetched_for_query += len(page)
            for posting in page:
                external_path = (
                    _common._clean_text(posting.get("externalPath"))
                    if isinstance(posting, dict)
                    else ""
                )
                if external_path:
                    if external_path in seen_external_paths:
                        continue
                    seen_external_paths.add(external_path)
                postings.append(posting)
            total = payload.get("total")
            if (
                not isinstance(total, int)
                or fetched_for_query >= total
                or not page
            ):
                break
            offset += len(page)
    return postings


def _search_queries(criteria: JobCriteria) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()
    for keyword in criteria.role_keywords:
        query = _common._clean_text(keyword)
        key = query.casefold()
        if not query or key in seen:
            continue
        seen.add(key)
        queries.append(query)
    return queries or [""]


def _role_from_detail(
    detail: dict[str, Any],
    *,
    company: Company,
    criteria: JobCriteria,
    expected_host: str,
    site: str,
) -> Role | None:
    info = detail.get("jobPostingInfo")
    if not isinstance(info, dict):
        raise ValueError("missing jobPostingInfo")
    if info.get("posted") is False or info.get("canApply") is False:
        return None

    title = _common._clean_text(info.get("title"))
    if not title:
        raise ValueError("missing title")
    url = _trusted_url(info.get("externalUrl"), expected_host, site)
    if not url:
        raise ValueError("missing trusted externalUrl")

    raw_description = _common._html_to_plain(info.get("jobDescription"))
    location = _common._clean_text(info.get("location"))
    country = info.get("country")
    country_name = (
        _common._clean_text(country.get("descriptor"))
        if isinstance(country, dict)
        else ""
    )
    locations = [location, country_name]
    if location and country_name:
        locations.append(f"{location}, {country_name}")
    posted_at = _common._clean_text(info.get("startDate")) or None
    employment_type = _common._normalize_employment_type(info.get("timeType"))
    source_job_id = (
        _common._clean_text(info.get("jobReqId"))
        or _common._clean_text(info.get("id"))
    ) or None
    matched = _common._filter_match(
        criteria,
        title=title,
        description=raw_description,
        locations=locations,
        employment_type=employment_type,
        posted_at=posted_at,
        source_name="Workday",
        company_slug=company.slug,
        job_id=source_job_id or "unknown",
        logger=LOGGER,
    )
    if matched is None:
        return None

    return Role(
        company=company.name,
        title=title,
        url=url,
        location=location or "Location not specified",
        summary=_common._summary(raw_description, title=title, location=location),
        match_reason=_common._match_reason(
            company=company,
            source="Workday",
            keywords=matched,
            location=location,
        ),
        source=CompanySource.workday,
        company_slug=company.slug if source_job_id is not None else None,
        source_job_id=source_job_id,
        apply_urls=[url],
        posted_at=posted_at,
        employment_type=employment_type,
        raw_description=raw_description or None,
        confidence=1.0,
    )


def _workday_host(company: Company) -> str | None:
    for domain in company.careers_domains:
        host = domain.strip().casefold().rstrip(".")
        if host.endswith(".myworkdayjobs.com") and "/" not in host:
            return host
    return None


def _safe_external_path(value: Any) -> str:
    path = _common._clean_text(value)
    if not path.startswith("/job/") or "://" in path or ".." in path:
        return ""
    return quote(path, safe="/-._~")


def _trusted_url(value: Any, expected_host: str, site: str) -> str:
    url = _common._clean_text(value)
    if not url or "\\" in url or any(character.isspace() for character in url):
        return ""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return ""
    path_parts = safe_url_path_parts(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or path_parts is None
        or len(path_parts) < 3
        or path_parts[0].casefold() != site.casefold()
        or path_parts[1] != "job"
    ):
        return ""
    return url


__all__ = ["WorkdayAdapter", "WorkdaySourceAdapter"]
