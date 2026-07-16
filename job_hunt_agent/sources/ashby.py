"""Ashby public job-board source adapter."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
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

ASHBY_BOARD_ENDPOINT = "https://api.ashbyhq.com/posting-api/job-board/{board}"
REQUEST_TIMEOUT_SECONDS = 20
_ADVANCED_LEVELS = {
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
_NON_JUNIOR_LEVEL = re.compile(
    r"\b(?:engineer|developer|sde)\s+(?:ii|iii|iv|v|[2-9]|l[2-9]|ic[2-9])\b"
    r"|\b(?:level|grade)\s+(?:ii|iii|iv|v|[2-9])\b"
    r"|\b(?:l|ic)[2-9]\b",
)


class AshbyAdapter:
    """Fetch and normalize published jobs from an Ashby-hosted board."""

    name = CompanySource.ashby.value

    def supports(self, company: Company) -> bool:
        return (
            company.source is CompanySource.ashby
            and bool(company.source_token and company.source_token.strip())
        )

    def fetch_open_roles(
        self,
        company: Company,
        criteria: JobCriteria,
    ) -> list[Role]:
        with TRACER.start_as_current_span(
            "job_source.ashby.fetch_open_roles",
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
                "Ashby source is not configured for company %s.",
                company.slug,
            )
            return []

        board = company.source_token.strip()
        payload = _request_json(
            ASHBY_BOARD_ENDPOINT.format(board=quote(board, safe="")),
            source="Ashby",
            company_slug=company.slug,
        )
        if payload is None:
            return []
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            LOGGER.warning(
                "Ashby returned a malformed payload for %s: jobs is not a list.",
                company.slug,
            )
            return []

        roles: list[Role] = []
        for index, job in enumerate(payload["jobs"]):
            if not isinstance(job, dict):
                LOGGER.warning(
                    "Skipping malformed Ashby job %s for %s.",
                    index,
                    company.slug,
                )
                continue
            try:
                role = _role_from_job(
                    job,
                    company=company,
                    criteria=criteria,
                    board=board,
                )
            except (TypeError, ValueError) as exc:
                LOGGER.warning(
                    "Skipping malformed Ashby job %s for %s: %s.",
                    _clean_text(job.get("id")) or index,
                    company.slug,
                    exc,
                )
                continue
            if role is not None:
                roles.append(role)

        if not roles:
            LOGGER.info("No Ashby roles for %s matched the criteria.", company.slug)
        return roles


AshbySourceAdapter = AshbyAdapter


def _role_from_job(
    job: dict[str, Any],
    *,
    company: Company,
    criteria: JobCriteria,
    board: str,
) -> Role | None:
    if job.get("isListed") is False:
        return None

    title = _clean_text(job.get("title"))
    location = _clean_text(job.get("location"))
    if not title:
        raise ValueError("missing title")

    apply_url = _trusted_ashby_url(job.get("applyUrl"), board)
    job_url = _trusted_ashby_url(job.get("jobUrl"), board)
    url = apply_url or job_url
    if not url:
        raise ValueError("missing trusted applyUrl or jobUrl")

    raw_description = _clean_text(job.get("descriptionPlain"))
    if not raw_description:
        raw_description = _html_to_plain(job.get("descriptionHtml"))
    locations = _ashby_locations(job, location)
    posted_at = _clean_text(job.get("publishedAt")) or None
    employment_type = _normalize_employment_type(job.get("employmentType"))
    source_job_id = _clean_text(job.get("id")) or None
    matched_keywords = _filter_match(
        criteria,
        title=title,
        description=raw_description,
        locations=locations,
        employment_type=employment_type,
        posted_at=posted_at,
        source_name="Ashby",
        company_slug=company.slug,
        job_id=source_job_id or "unknown",
        logger=LOGGER,
    )
    if matched_keywords is None:
        return None

    return Role(
        company=company.name,
        title=title,
        url=url,
        location=location or "Location not specified",
        summary=_summary(raw_description, title=title, location=location),
        match_reason=_match_reason(
            company=company,
            source="Ashby",
            keywords=matched_keywords,
            location=location,
        ),
        source=CompanySource.ashby,
        company_slug=company.slug if source_job_id is not None else None,
        source_job_id=source_job_id,
        apply_urls=_dedupe([apply_url, job_url]),
        posted_at=posted_at,
        employment_type=employment_type,
        raw_description=raw_description or None,
        confidence=1.0,
    )


def _request_json(
    url: str,
    *,
    source: str,
    company_slug: str,
    data: bytes | None = None,
) -> Any | None:
    request = Request(
        url,
        data=data,
        method="POST" if data is not None else "GET",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "job-hunt-signal/2",
        },
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read()
    except HTTPError as exc:
        LOGGER.warning(
            "%s request failed for %s with HTTP %s.",
            source,
            company_slug,
            exc.code,
        )
        return None
    except (URLError, TimeoutError, OSError) as exc:
        LOGGER.warning("%s request failed for %s: %s.", source, company_slug, exc)
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        LOGGER.warning(
            "%s returned malformed JSON for %s: %s.",
            source,
            company_slug,
            exc,
        )
        return None


def _trusted_ashby_url(value: Any, board: str) -> str:
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
        or parsed.hostname != "jobs.ashbyhq.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        return ""
    path_parts = safe_url_path_parts(parsed.path)
    if path_parts is None:
        return ""
    if not path_parts or path_parts[0].casefold() != board.casefold():
        return ""
    return url


def _ashby_locations(job: dict[str, Any], primary: str) -> list[str]:
    locations = [primary]
    secondary = job.get("secondaryLocations")
    if isinstance(secondary, list):
        for item in secondary:
            if not isinstance(item, dict):
                continue
            locations.append(_clean_text(item.get("location")))
            address = item.get("address")
            if isinstance(address, dict):
                locations.extend(_postal_address_parts(address.get("postalAddress")))
    address = job.get("address")
    if isinstance(address, dict):
        locations.extend(_postal_address_parts(address.get("postalAddress")))
    workplace_type = _clean_text(job.get("workplaceType"))
    if workplace_type and primary:
        locations.append(f"{workplace_type} {primary}")
    return [value for value in locations if value]


def _postal_address_parts(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    combined = ", ".join(
        part
        for part in (
            _clean_text(value.get("addressLocality")),
            _clean_text(value.get("addressRegion")),
            _clean_text(value.get("addressCountry")),
        )
        if part
    )
    return [combined] if combined else []


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipped_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag in {"script", "style"}:
            self.skipped_depth += 1
        elif tag in {"br", "div", "h1", "h2", "h3", "li", "p", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.skipped_depth:
            self.skipped_depth -= 1
        elif tag in {"div", "h1", "h2", "h3", "li", "p", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skipped_depth:
            self.parts.append(data)


def _html_to_plain(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    parser = _PlainTextParser()
    try:
        parser.feed(unescape(value))
        parser.close()
    except ValueError:
        return _clean_text(unescape(value))
    return "\n".join(
        line
        for line in (_clean_text(part) for part in "".join(parser.parts).splitlines())
        if line
    )


def _filter_match(
    criteria: JobCriteria,
    *,
    title: str,
    description: str,
    locations: list[str],
    employment_type: EmploymentType,
    posted_at: str | None,
    source_name: str,
    company_slug: str,
    job_id: str,
    logger: logging.Logger,
) -> list[str] | None:
    searchable = _normalize_match_text(f"{title}\n{description}")
    matched_keywords = _matched_keywords(criteria.role_keywords, searchable)
    if any(value.strip() for value in criteria.role_keywords) and not matched_keywords:
        return None
    if _seniority_mismatches(criteria.seniority, title):
        return None
    if not _locations_match(criteria.location, locations):
        return None
    if (
        criteria.employment_types
        and employment_type is not EmploymentType.unknown
        and employment_type not in criteria.employment_types
    ):
        return None
    if not _within_max_age(
        posted_at,
        criteria.max_age_days,
        source_name=source_name,
        company_slug=company_slug,
        job_id=job_id,
        logger=logger,
    ):
        return None
    return matched_keywords


def _matched_keywords(keywords: list[str], searchable: str) -> list[str]:
    padded = f" {searchable} "
    matches: list[str] = []
    for keyword in keywords:
        cleaned = _clean_text(keyword)
        normalized = _normalize_match_text(cleaned)
        if normalized and f" {normalized} " in padded:
            matches.append(cleaned)
    return matches


def _seniority_mismatches(target: str, title: str) -> bool:
    normalized = _normalize_match_text(title)
    tokens = set(normalized.split())
    staff = bool({"principal", "staff"} & tokens)
    senior = bool({"lead", "senior", "sr"} & tokens)
    junior = bool(_JUNIOR_LEVELS & tokens)
    if target == "junior":
        return bool(_ADVANCED_LEVELS & tokens) or bool(
            _NON_JUNIOR_LEVEL.search(normalized),
        )
    if target == "mid":
        return staff or bool({"director", "head"} & tokens)
    if target == "senior":
        return junior or staff
    if target == "staff":
        return not staff or junior or (senior and not staff)
    return False


def _locations_match(requested: list[str], available: list[str]) -> bool:
    wanted = [_location_tokens(value) for value in requested if value.strip()]
    if not wanted:
        return True
    candidates = [
        _location_tokens(segment)
        for value in available
        for segment in re.split(r"[;|+\n]+", value)
        if segment.strip()
    ]
    return any(
        request_tokens and request_tokens <= candidate_tokens
        for request_tokens in wanted
        for candidate_tokens in candidates
    )


def _location_tokens(value: str) -> set[str]:
    aliases = {
        "bangalore": "bengaluru",
        "in": "india",
        "usa": "us",
    }
    return {
        aliases.get(token, token)
        for token in _normalize_match_text(value).split()
    }


def _normalize_employment_type(value: Any) -> EmploymentType:
    normalized = _normalize_match_text(_clean_text(value))
    if re.search(r"\b(intern|internship|co op)\b", normalized):
        return EmploymentType.intern
    if re.search(
        r"\b(contract|contractor|temporary|freelance|consultant|fixed term)\b",
        normalized,
    ):
        return EmploymentType.contract
    if re.search(r"\b(full time|fulltime|full)\b", normalized):
        return EmploymentType.full_time
    return EmploymentType.unknown


def _within_max_age(
    posted_at: str | None,
    max_age_days: int | None,
    *,
    source_name: str,
    company_slug: str,
    job_id: str,
    logger: logging.Logger,
) -> bool:
    if max_age_days is None:
        return True
    if not posted_at:
        logger.warning(
            "Keeping %s job %s for %s with unknown freshness because the posted "
            "date is missing.",
            source_name,
            job_id,
            company_slug,
        )
        return True
    try:
        normalized = posted_at[:-1] + "+00:00" if posted_at.endswith("Z") else posted_at
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
    except ValueError:
        logger.warning(
            "Keeping %s job %s for %s with unknown freshness because posted date "
            "%r is invalid.",
            source_name,
            job_id,
            company_slug,
            posted_at,
        )
        return True
    return parsed >= _utc_now() - timedelta(days=max_age_days)


def _summary(description: str, *, title: str, location: str) -> str:
    flat = _clean_text(description)
    if not flat:
        return f"{title} — {location}." if location else f"{title}."
    sentences = re.split(r"(?<=[.!?])\s+", flat)
    summary = " ".join(sentences[:3])
    return summary if len(summary) <= 600 else f"{summary[:597].rsplit(' ', 1)[0]}..."


def _match_reason(
    *,
    company: Company,
    source: str,
    keywords: list[str],
    location: str,
) -> str:
    parts = [f"Direct posting from {company.name}'s {source} job board."]
    if keywords:
        parts.append(
            "Listing text matches "
            + ", ".join(f'"{keyword}"' for keyword in keywords[:3])
            + ".",
        )
    if location:
        parts.append(f"Location: {location}.")
    return " ".join(parts)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_match_text(value: str) -> str:
    compatible = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"[^a-z0-9]+", " ", compatible.casefold())
    return re.sub(r"\s+", " ", normalized).strip()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = ["AshbyAdapter", "AshbySourceAdapter"]
