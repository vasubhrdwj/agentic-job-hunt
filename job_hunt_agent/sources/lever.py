"""Lever Postings API adapter."""

from __future__ import annotations

import json
import logging
import re
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from opentelemetry import trace

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

LEVER_POSTINGS_ENDPOINT = "https://api.lever.co/v0/postings/{site}?mode=json"
LEVER_JOB_HOSTS = {"jobs.lever.co", "jobs.eu.lever.co"}
REQUEST_TIMEOUT_SECONDS = 20
SUMMARY_SECTION_LIMIT = 240
SUMMARY_LIMIT = 520
_ADVANCED_LEVELS = {
    "senior",
    "sr",
    "staff",
    "principal",
    "lead",
    "manager",
    "director",
    "head",
}
_JUNIOR_LEVELS = {"junior", "jr", "associate", "entry", "graduate"}


class LeverAdapter:
    """Fetch published jobs from a company's public Lever board."""

    name = CompanySource.lever.value

    def supports(self, company: Company) -> bool:
        """Return whether the company has a usable Lever site token."""
        return (
            company.source is CompanySource.lever
            and bool(company.source_token and company.source_token.strip())
        )

    def fetch_open_roles(
        self,
        company: Company,
        criteria: JobCriteria,
    ) -> list[Role]:
        """Fetch and map currently published Lever postings.

        Lever's documented response does not expose a reliable posting date, so
        mapped roles deliberately leave ``posted_at`` unset.
        """
        with TRACER.start_as_current_span(
            "job_source.lever.fetch_open_roles",
        ) as span:
            span.set_attribute("job_source.name", self.name)
            span.set_attribute("job_source.company_slug", company.slug)
            span.set_attribute(
                "job_source.source_token_configured",
                bool(company.source_token and company.source_token.strip()),
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
                "Cannot fetch Lever roles for company %r: source is not Lever "
                "or has no source token.",
                company.slug,
            )
            return []

        site = company.source_token.strip()
        payload = _fetch_postings(site)
        if payload is None:
            return []
        if not payload:
            LOGGER.info("Lever returned no published roles for site %r.", site)
            return []

        roles: list[Role] = []
        for index, posting in enumerate(payload):
            if not isinstance(posting, dict):
                LOGGER.warning(
                    "Skipping malformed Lever posting %s for site %r: expected object.",
                    index,
                    site,
                )
                continue

            try:
                role = _role_from_posting(
                    posting,
                    company=company,
                    criteria=criteria,
                )
            except (TypeError, ValueError) as exc:
                LOGGER.warning(
                    "Skipping malformed Lever posting %s for site %r: %s",
                    index,
                    site,
                    exc,
                )
                continue
            if role is not None:
                roles.append(role)

        if not roles:
            LOGGER.info("No Lever roles for site %r matched the criteria.", site)
        return roles


LeverSourceAdapter = LeverAdapter


def _fetch_postings(site: str) -> list[Any] | None:
    url = LEVER_POSTINGS_ENDPOINT.format(site=quote(site, safe=""))
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "job-hunt-signal/2.0",
        },
    )

    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        LOGGER.warning(
            "Lever request for site %r failed with HTTP %s.",
            site,
            exc.code,
        )
        return None
    except (URLError, TimeoutError, OSError) as exc:
        LOGGER.warning("Lever request for site %r failed: %s", site, exc)
        return None
    except (UnicodeError, json.JSONDecodeError) as exc:
        LOGGER.warning("Lever returned malformed JSON for site %r: %s", site, exc)
        return None

    if not isinstance(payload, list):
        LOGGER.warning(
            "Lever returned an unexpected payload for site %r: expected a list.",
            site,
        )
        return None
    return payload


def _role_from_posting(
    posting: dict[str, Any],
    *,
    company: Company,
    criteria: JobCriteria,
) -> Role | None:
    title = _normalize_space(posting.get("text"))
    if not title:
        raise ValueError("missing text")

    apply_url = _valid_lever_url(posting.get("applyUrl"), company)
    hosted_url = _valid_lever_url(posting.get("hostedUrl"), company)
    url = apply_url or hosted_url
    if not url:
        raise ValueError("missing trusted applyUrl or hostedUrl")

    apply_urls = _dedupe([apply_url, hosted_url])
    categories = posting.get("categories")
    if not isinstance(categories, dict):
        categories = {}

    location = _location_from_categories(categories)
    commitment = _normalize_space(categories.get("commitment"))
    raw_description = _full_description_from_posting(posting)
    summary = _summary_from_posting(posting, raw_description=raw_description)
    searchable = _normalize_for_match(f"{title} {raw_description} {summary}")
    matched_keywords = _matched_keywords(criteria.role_keywords, searchable)
    if _has_keywords(criteria) and not matched_keywords:
        return None
    if _seniority_mismatches(criteria.seniority, title):
        return None
    if not _matches_location(criteria.location, posting, categories):
        return None

    employment_type = _normalize_employment_type(commitment)
    if (
        criteria.employment_types
        and employment_type is not EmploymentType.unknown
        and employment_type not in criteria.employment_types
    ):
        return None

    source_job_id = _normalize_space(posting.get("id")) or None
    return Role(
        company=company.name,
        title=title,
        url=url,
        location=location,
        summary=summary,
        match_reason=_match_reason(
            company=company,
            matched_keywords=matched_keywords,
            commitment=commitment,
            location=location,
        ),
        source=CompanySource.lever,
        company_slug=company.slug if source_job_id is not None else None,
        source_job_id=source_job_id,
        apply_urls=apply_urls,
        posted_at=None,
        employment_type=employment_type,
        raw_description=raw_description or None,
        confidence=1.0,
    )


def _location_from_categories(categories: dict[str, Any]) -> str:
    primary = _normalize_space(categories.get("location"))
    if primary:
        return primary

    all_locations = categories.get("allLocations")
    if not isinstance(all_locations, list):
        return ""
    return "; ".join(
        location
        for location in (_normalize_space(value) for value in all_locations)
        if location
    )


def _normalize_employment_type(commitment: str) -> EmploymentType:
    normalized = re.sub(r"[^a-z0-9]+", " ", commitment.casefold()).strip()
    if not normalized:
        return EmploymentType.unknown
    if re.search(r"\b(intern|internship|co op)\b", normalized):
        return EmploymentType.intern
    if re.search(
        r"\b(contract|contractor|temporary|freelance|consultant|fixed term)\b",
        normalized,
    ):
        return EmploymentType.contract
    if re.search(r"\b(full time|fulltime)\b", normalized):
        return EmploymentType.full_time
    return EmploymentType.unknown


def _summary_from_posting(
    posting: dict[str, Any],
    *,
    raw_description: str,
) -> str:
    raw_lists = posting.get("lists")
    sections: list[str] = []
    if isinstance(raw_lists, list):
        for raw_section in raw_lists:
            if not isinstance(raw_section, dict):
                continue
            heading = _normalize_space(raw_section.get("text"))
            content = _html_to_text(raw_section.get("content"))
            if not content:
                continue
            section = f"{heading}: {content}" if heading else content
            sections.append(_truncate(section, SUMMARY_SECTION_LIMIT))
            if len(sections) == 2:
                break

    if sections:
        return _truncate(" ".join(sections), SUMMARY_LIMIT)
    return _description_summary(raw_description)


def _full_description_from_posting(posting: dict[str, Any]) -> str:
    """Compose all public Lever description fields into one plain-text JD."""

    parts: list[str] = []
    seen: set[str] = set()

    description = _clean_description(posting.get("descriptionPlain"))
    if not description:
        description = _html_to_text(posting.get("description"))
    _append_description_part(parts, seen, description)

    raw_lists = posting.get("lists")
    if isinstance(raw_lists, list):
        for raw_section in raw_lists:
            if not isinstance(raw_section, dict):
                continue
            heading = _normalize_space(raw_section.get("text"))
            content = _html_to_text(raw_section.get("content"))
            if not content:
                continue
            section = f"{heading}: {content}" if heading else content
            if _append_description_part(parts, seen, section):
                # Some Lever payloads repeat a structured list verbatim in
                # additionalPlain. Track its unheaded body as the same content.
                seen.add(_description_identity(content))

    additional = _clean_description(posting.get("additionalPlain"))
    if not additional:
        additional = _html_to_text(posting.get("additional"))
    _append_description_part(parts, seen, additional)
    return "\n\n".join(parts)


def _append_description_part(
    parts: list[str],
    seen: set[str],
    value: str,
) -> bool:
    cleaned = _normalize_space(value)
    identity = _description_identity(cleaned)
    if not identity or identity in seen:
        return False
    parts.append(cleaned)
    seen.add(identity)
    return True


def _description_identity(value: str) -> str:
    return _normalize_for_match(value)


def _description_summary(description: str) -> str:
    if not description:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", _normalize_space(description))
    return _truncate(" ".join(sentences[:2]), SUMMARY_LIMIT)


def _match_reason(
    *,
    company: Company,
    matched_keywords: list[str],
    commitment: str,
    location: str,
) -> str:
    details = [f"Direct posting from {company.name}'s Lever job board."]
    if matched_keywords:
        quoted = ", ".join(f'"{keyword}"' for keyword in matched_keywords[:3])
        details.append(f"Listing text matches requested keyword {quoted}.")
    if commitment:
        details.append(f"Lever lists the commitment as {commitment}.")
    if location:
        details.append(f"Location: {location}.")
    return " ".join(details)


def _valid_lever_url(value: Any, company: Company) -> str:
    url = str(value or "").strip()
    if not url or "\\" in url or any(character.isspace() for character in url):
        return ""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme != "https"
        or parsed.hostname not in LEVER_JOB_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        return ""
    token = (company.source_token or "").strip().casefold()
    safe_parts = safe_url_path_parts(parsed.path)
    if safe_parts is None:
        return ""
    path_parts = [part.casefold() for part in safe_parts]
    if not token or not path_parts or path_parts[0] != token:
        return ""
    return url


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _clean_description(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_for_match(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _has_keywords(criteria: JobCriteria) -> bool:
    return any(keyword.strip() for keyword in criteria.role_keywords)


def _matched_keywords(keywords: list[str], searchable: str) -> list[str]:
    matches: list[str] = []
    padded_searchable = f" {searchable} "
    for keyword in keywords:
        cleaned = _normalize_space(keyword)
        normalized_keyword = _normalize_for_match(cleaned)
        if normalized_keyword and f" {normalized_keyword} " in padded_searchable:
            matches.append(cleaned)
    return matches


def _seniority_mismatches(target: str, title: str) -> bool:
    title_tokens = set(_normalize_for_match(title).split())
    has_staff_level = bool({"staff", "principal"} & title_tokens)
    has_senior_level = bool({"senior", "sr", "lead"} & title_tokens)
    has_junior_level = bool(_JUNIOR_LEVELS & title_tokens)

    if target == "junior":
        return bool(_ADVANCED_LEVELS & title_tokens)
    if target == "mid":
        return has_staff_level or bool({"director", "head"} & title_tokens)
    if target == "senior":
        return has_junior_level or has_staff_level
    if target == "staff":
        return not has_staff_level or has_junior_level or (
            has_senior_level and not has_staff_level
        )
    return False


def _matches_location(
    requested_locations: list[str],
    posting: dict[str, Any],
    categories: dict[str, Any],
) -> bool:
    requested = [
        _location_tokens(value)
        for value in requested_locations
        if value.strip()
    ]
    if not requested:
        return True

    source_locations = [_normalize_space(categories.get("location"))]
    all_locations = categories.get("allLocations")
    if isinstance(all_locations, list):
        source_locations.extend(_normalize_space(value) for value in all_locations)
    source_locations.extend(
        [
            _normalize_space(posting.get("workplaceType")),
            _normalize_space(posting.get("country")),
        ]
    )

    available = [
        _location_tokens(value)
        for value in source_locations
        if value
    ]
    return any(
        requested_tokens and requested_tokens <= available_tokens
        for requested_tokens in requested
        for available_tokens in available
    )


def _location_tokens(value: str) -> set[str]:
    aliases = {
        "bangalore": "bengaluru",
        "in": "india",
        "usa": "us",
    }
    return {
        aliases.get(token, token)
        for token in _normalize_for_match(value).split()
    }


def _truncate(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag in {"br", "div", "li", "p"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"div", "li", "p"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _html_to_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    parser = _PlainTextParser()
    try:
        parser.feed(value)
        parser.close()
    except ValueError as exc:
        LOGGER.warning("Could not parse Lever list HTML: %s", exc)
        return ""
    return _normalize_space("".join(parser.parts))


__all__ = ["LeverAdapter", "LeverSourceAdapter"]
