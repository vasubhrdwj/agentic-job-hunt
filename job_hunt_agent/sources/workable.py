"""Workable public careers API source adapter."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote, urlsplit

from opentelemetry import trace

from job_hunt_agent.schemas import Company, CompanySource, JobCriteria, Role
from job_hunt_agent.sources import ashby as _common
from job_hunt_agent.sources.base import safe_url_path_parts


LOGGER = logging.getLogger(__name__)
TRACER = trace.get_tracer(__name__)

WORKABLE_JOBS_ENDPOINT = "https://apply.workable.com/api/v3/accounts/{account}/jobs"
WORKABLE_DETAIL_ENDPOINT = (
    "https://apply.workable.com/api/v2/accounts/{account}/jobs/{shortcode}"
)


class WorkableAdapter:
    """Fetch and normalize public Workable postings."""

    name = CompanySource.workable.value

    def supports(self, company: Company) -> bool:
        return (
            company.source is CompanySource.workable
            and bool(company.source_token and company.source_token.strip())
        )

    def fetch_open_roles(
        self,
        company: Company,
        criteria: JobCriteria,
    ) -> list[Role]:
        with TRACER.start_as_current_span(
            "job_source.workable.fetch_open_roles",
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
                "Workable source is not configured for company %s.",
                company.slug,
            )
            return []

        account = company.source_token.strip()
        postings = _fetch_postings(account, criteria, company.slug)
        if postings is None:
            return []

        roles: list[Role] = []
        for index, posting in enumerate(postings):
            if not isinstance(posting, dict):
                LOGGER.warning(
                    "Skipping malformed Workable posting %s for %s.",
                    index,
                    company.slug,
                )
                continue
            shortcode = _common._clean_text(posting.get("shortcode"))
            if not shortcode:
                LOGGER.warning(
                    "Skipping Workable posting %s for %s: missing shortcode.",
                    index,
                    company.slug,
                )
                continue
            detail = _common._request_json(
                WORKABLE_DETAIL_ENDPOINT.format(
                    account=quote(account, safe=""),
                    shortcode=quote(shortcode, safe=""),
                ),
                source="Workable",
                company_slug=company.slug,
            )
            if not isinstance(detail, dict):
                continue
            try:
                role = _role_from_detail(
                    detail,
                    company=company,
                    criteria=criteria,
                    account=account,
                )
            except (TypeError, ValueError) as exc:
                LOGGER.warning(
                    "Skipping malformed Workable posting %s for %s: %s.",
                    shortcode,
                    company.slug,
                    exc,
                )
                continue
            if role is not None:
                roles.append(role)

        if not roles:
            LOGGER.info("No Workable roles for %s matched the criteria.", company.slug)
        return roles


WorkableSourceAdapter = WorkableAdapter


def _fetch_postings(
    account: str,
    criteria: JobCriteria,
    company_slug: str,
) -> list[Any] | None:
    endpoint = WORKABLE_JOBS_ENDPOINT.format(account=quote(account, safe=""))
    query = " ".join(value.strip() for value in criteria.role_keywords if value.strip())
    token: str | None = None
    postings: list[Any] = []
    while True:
        body: dict[str, Any] = {"query": query}
        if token:
            body["token"] = token
        payload = _common._request_json(
            endpoint,
            source="Workable",
            company_slug=company_slug,
            data=json.dumps(body).encode("utf-8"),
        )
        if payload is None:
            return None
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            LOGGER.warning(
                "Workable returned a malformed jobs payload for %s.",
                company_slug,
            )
            return None
        postings.extend(payload["results"])
        next_page = payload.get("nextPage")
        if not isinstance(next_page, str) or not next_page or next_page == token:
            break
        token = next_page
    return postings


def _role_from_detail(
    detail: dict[str, Any],
    *,
    company: Company,
    criteria: JobCriteria,
    account: str,
) -> Role | None:
    if detail.get("state") not in (None, "published") or detail.get("isInternal") is True:
        return None

    title = _common._clean_text(detail.get("title"))
    shortcode = _common._clean_text(detail.get("shortcode"))
    if not title:
        raise ValueError("missing title")
    if not shortcode:
        raise ValueError("missing shortcode")
    url = (
        "https://apply.workable.com/"
        + quote(account, safe="")
        + "/j/"
        + quote(shortcode, safe="")
        + "/"
    )
    if not _trusted_url(url, account, shortcode):
        raise ValueError("could not build a trusted public job URL")

    raw_description = "\n\n".join(
        text
        for text in (
            _common._html_to_plain(detail.get("description")),
            _common._html_to_plain(detail.get("requirements")),
            _common._html_to_plain(detail.get("benefits")),
        )
        if text
    )
    location = _location_label(detail.get("location"))
    locations = _all_locations(detail, location)
    if detail.get("remote") or detail.get("workplace") == "remote":
        locations.append(f"Remote {location}")
    posted_at = _common._clean_text(detail.get("published")) or None
    employment_type = _common._normalize_employment_type(detail.get("type"))
    matched = _common._filter_match(
        criteria,
        title=title,
        description=raw_description,
        locations=locations,
        employment_type=employment_type,
        posted_at=posted_at,
        source_name="Workable",
        company_slug=company.slug,
        job_id=shortcode,
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
            source="Workable",
            keywords=matched,
            location=location,
        ),
        source=CompanySource.workable,
        apply_urls=[url],
        posted_at=posted_at,
        employment_type=employment_type,
        raw_description=raw_description or None,
        confidence=1.0,
    )


def _location_label(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return ", ".join(
        part
        for part in (
            _common._clean_text(value.get("city")),
            _common._clean_text(value.get("region")),
            _common._clean_text(value.get("country")),
        )
        if part
    )


def _all_locations(detail: dict[str, Any], primary: str) -> list[str]:
    values = [primary]
    locations = detail.get("locations")
    if isinstance(locations, list):
        values.extend(_location_label(item) for item in locations)
    return [value for value in values if value]


def _trusted_url(value: str, account: str, shortcode: str) -> bool:
    if not value or "\\" in value or any(character.isspace() for character in value):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    parts = safe_url_path_parts(parsed.path)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "apply.workable.com"
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and parts is not None
        and len(parts) >= 3
        and parts[0].casefold() == account.casefold()
        and parts[1] == "j"
        and parts[2].casefold() == shortcode.casefold()
    )


__all__ = ["WorkableAdapter", "WorkableSourceAdapter"]
