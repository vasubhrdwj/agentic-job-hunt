"""SmartRecruiters public Posting API source adapter."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from opentelemetry import trace

from job_hunt_agent.schemas import (
    Company,
    CompanySource,
    JobCriteria,
    Role,
)
from job_hunt_agent.sources import ashby as _common
from job_hunt_agent.sources.base import safe_url_path_parts


LOGGER = logging.getLogger(__name__)
TRACER = trace.get_tracer(__name__)

SMARTRECRUITERS_POSTINGS_ENDPOINT = (
    "https://api.smartrecruiters.com/v1/companies/{company}/postings"
)
PAGE_SIZE = 100


class SmartRecruitersAdapter:
    """Fetch and normalize public SmartRecruiters postings."""

    name = CompanySource.smartrecruiters.value

    def supports(self, company: Company) -> bool:
        return (
            company.source is CompanySource.smartrecruiters
            and bool(company.source_token and company.source_token.strip())
        )

    def fetch_open_roles(
        self,
        company: Company,
        criteria: JobCriteria,
    ) -> list[Role]:
        with TRACER.start_as_current_span(
            "job_source.smartrecruiters.fetch_open_roles",
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
                "SmartRecruiters source is not configured for company %s.",
                company.slug,
            )
            return []

        token = company.source_token.strip()
        postings = _fetch_postings(token, criteria, company.slug)
        if postings is None:
            return []

        roles: list[Role] = []
        for index, posting in enumerate(postings):
            if not isinstance(posting, dict):
                LOGGER.warning(
                    "Skipping malformed SmartRecruiters posting %s for %s.",
                    index,
                    company.slug,
                )
                continue
            posting_id = _common._clean_text(posting.get("id"))
            if not posting_id:
                LOGGER.warning(
                    "Skipping SmartRecruiters posting %s for %s: missing id.",
                    index,
                    company.slug,
                )
                continue
            detail = _common._request_json(
                SMARTRECRUITERS_POSTINGS_ENDPOINT.format(
                    company=quote(token, safe=""),
                )
                + "/"
                + quote(posting_id, safe=""),
                source="SmartRecruiters",
                company_slug=company.slug,
            )
            if not isinstance(detail, dict):
                continue
            try:
                role = _role_from_detail(
                    detail,
                    company=company,
                    criteria=criteria,
                    token=token,
                )
            except (TypeError, ValueError) as exc:
                LOGGER.warning(
                    "Skipping malformed SmartRecruiters posting %s for %s: %s.",
                    posting_id,
                    company.slug,
                    exc,
                )
                continue
            if role is not None:
                roles.append(role)

        if not roles:
            LOGGER.info(
                "No SmartRecruiters roles for %s matched the criteria.",
                company.slug,
            )
        return roles


SmartRecruitersSourceAdapter = SmartRecruitersAdapter


def _fetch_postings(
    token: str,
    criteria: JobCriteria,
    company_slug: str,
) -> list[Any] | None:
    postings: list[Any] = []
    seen_posting_ids: set[str] = set()
    for query in _search_queries(criteria):
        offset = 0
        fetched_for_query = 0
        while True:
            params: dict[str, Any] = {
                "limit": PAGE_SIZE,
                "offset": offset,
            }
            if query:
                params["q"] = query
            url = (
                SMARTRECRUITERS_POSTINGS_ENDPOINT.format(
                    company=quote(token, safe=""),
                )
                + "?"
                + urlencode(params)
            )
            payload = _common._request_json(
                url,
                source="SmartRecruiters",
                company_slug=company_slug,
            )
            if payload is None:
                return None
            if not isinstance(payload, dict) or not isinstance(
                payload.get("content"),
                list,
            ):
                LOGGER.warning(
                    "SmartRecruiters returned a malformed postings payload for %s.",
                    company_slug,
                )
                return None
            page = payload["content"]
            fetched_for_query += len(page)
            for posting in page:
                posting_id = (
                    _common._clean_text(posting.get("id"))
                    if isinstance(posting, dict)
                    else ""
                )
                if posting_id:
                    if posting_id in seen_posting_ids:
                        continue
                    seen_posting_ids.add(posting_id)
                postings.append(posting)
            total = payload.get("totalFound")
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
    token: str,
) -> Role | None:
    if detail.get("active") is False or detail.get("visibility") not in (None, "PUBLIC"):
        return None

    title = _common._clean_text(detail.get("name"))
    if not title:
        raise ValueError("missing name")
    posting_url = _trusted_url(detail.get("postingUrl"), token)
    apply_url = _trusted_url(detail.get("applyUrl"), token)
    url = posting_url or apply_url
    if not url:
        raise ValueError("missing trusted postingUrl or applyUrl")

    raw_description = _description(detail)
    location_data = detail.get("location")
    if not isinstance(location_data, dict):
        location_data = {}
    location = (
        _common._clean_text(location_data.get("fullLocation"))
        or _location_label(location_data)
    )
    locations = [location]
    if location_data.get("remote"):
        locations.append(f"Remote {location}")
    employment = detail.get("typeOfEmployment")
    employment_label = (
        employment.get("label") if isinstance(employment, dict) else employment
    )
    employment_type = _common._normalize_employment_type(employment_label)
    posted_at = _common._clean_text(detail.get("releasedDate")) or None
    source_job_id = _common._clean_text(detail.get("id")) or None
    matched = _common._filter_match(
        criteria,
        title=title,
        description=raw_description,
        locations=locations,
        employment_type=employment_type,
        posted_at=posted_at,
        source_name="SmartRecruiters",
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
            source="SmartRecruiters",
            keywords=matched,
            location=location,
        ),
        source=CompanySource.smartrecruiters,
        company_slug=company.slug if source_job_id is not None else None,
        source_job_id=source_job_id,
        apply_urls=_common._dedupe([posting_url, apply_url]),
        posted_at=posted_at,
        employment_type=employment_type,
        raw_description=raw_description or None,
        confidence=1.0,
    )


def _description(detail: dict[str, Any]) -> str:
    job_ad = detail.get("jobAd")
    sections = job_ad.get("sections") if isinstance(job_ad, dict) else None
    if not isinstance(sections, dict):
        return ""
    parts: list[str] = []
    for name in (
        "companyDescription",
        "jobDescription",
        "qualifications",
        "additionalInformation",
    ):
        section = sections.get(name)
        if not isinstance(section, dict):
            continue
        text = _common._html_to_plain(section.get("text"))
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _location_label(location: dict[str, Any]) -> str:
    return ", ".join(
        value
        for value in (
            _common._clean_text(location.get("city")),
            _common._clean_text(location.get("region")),
            _common._clean_text(location.get("country")),
        )
        if value
    )


def _trusted_url(value: Any, token: str) -> str:
    url = _common._clean_text(value)
    if not url or "\\" in url or any(character.isspace() for character in url):
        return ""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme != "https"
        or parsed.hostname != "jobs.smartrecruiters.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        return ""
    parts = safe_url_path_parts(parsed.path)
    if parts is None:
        return ""
    if not parts or parts[0].casefold() != token.casefold():
        return ""
    return url


__all__ = ["SmartRecruitersAdapter", "SmartRecruitersSourceAdapter"]
