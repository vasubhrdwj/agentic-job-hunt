"""Greenhouse Job Board API source adapter."""

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

GREENHOUSE_JOBS_ENDPOINT = (
    "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
)
DEFAULT_TIMEOUT_SECONDS = 20
_GREENHOUSE_DOMAIN = "greenhouse.io"
_ADVANCED_LEVELS = {
    "director",
    "head",
    "intermediate",
    "lead",
    "manager",
    "mid",
    "principal",
    "senior",
    "sr",
    "staff",
}
_JUNIOR_LEVELS = {"junior", "jr", "associate", "entry", "graduate"}
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


class GreenhouseAdapter:
    """Fetch and normalize published jobs from a Greenhouse board."""

    name = CompanySource.greenhouse.value

    def supports(self, company: Company) -> bool:
        """Return whether ``company`` has a usable Greenhouse board token."""
        return (
            company.source is CompanySource.greenhouse
            and bool(company.source_token and company.source_token.strip())
        )

    def fetch_open_roles(
        self,
        company: Company,
        criteria: JobCriteria,
    ) -> list[Role]:
        """Return matching roles, or an honest empty list on source failure."""
        with TRACER.start_as_current_span(
            "job_source.greenhouse.fetch_open_roles",
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
                "Greenhouse source is not configured for company %s.",
                company.slug,
            )
            return []

        token = quote(company.source_token.strip(), safe="")
        url = GREENHOUSE_JOBS_ENDPOINT.format(token=token)
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
            LOGGER.warning(
                "Greenhouse request failed for %s with HTTP %s.",
                company.slug,
                exc.code,
            )
            return []
        except (URLError, TimeoutError, OSError) as exc:
            LOGGER.warning(
                "Greenhouse request failed for %s: %s.",
                company.slug,
                exc,
            )
            return []

        try:
            payload = json.loads(raw_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            LOGGER.warning(
                "Greenhouse returned malformed JSON for %s: %s.",
                company.slug,
                exc,
            )
            return []

        return _roles_from_payload(payload, company=company, criteria=criteria)


# A descriptive alias keeps integrations resilient while the resolver is built in parallel.
GreenhouseSourceAdapter = GreenhouseAdapter


def _roles_from_payload(
    payload: Any,
    *,
    company: Company,
    criteria: JobCriteria,
) -> list[Role]:
    if not isinstance(payload, dict):
        LOGGER.warning(
            "Greenhouse returned a malformed payload for %s: expected an object.",
            company.slug,
        )
        return []

    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        LOGGER.warning(
            "Greenhouse returned a malformed payload for %s: jobs is not a list.",
            company.slug,
        )
        return []
    if not jobs:
        LOGGER.info("Greenhouse returned no open roles for %s.", company.slug)
        return []

    roles: list[Role] = []
    for index, item in enumerate(jobs):
        if not isinstance(item, dict):
            LOGGER.warning(
                "Skipping malformed Greenhouse job %s for %s: expected an object.",
                index,
                company.slug,
            )
            continue
        try:
            role = _role_from_job(item, company=company, criteria=criteria)
        except (TypeError, ValueError, ValidationError) as exc:
            LOGGER.warning(
                "Skipping malformed Greenhouse job %s for %s: %s.",
                item.get("id", index),
                company.slug,
                exc,
            )
            continue
        if role is not None:
            roles.append(role)

    if not roles:
        LOGGER.info("No Greenhouse roles for %s matched the criteria.", company.slug)
    return roles


def _role_from_job(
    item: dict[str, Any],
    *,
    company: Company,
    criteria: JobCriteria,
) -> Role | None:
    title = _clean_text(item.get("title"))
    apply_url = _clean_text(item.get("absolute_url"))
    location_data = item.get("location")
    location = (
        _clean_text(location_data.get("name"))
        if isinstance(location_data, dict)
        else ""
    )
    if not title:
        raise ValueError("missing title")
    if not apply_url:
        raise ValueError("missing absolute_url")
    if not _is_trusted_apply_url(apply_url, company):
        raise ValueError("absolute_url is not a trusted first-party URL")

    raw_description = _html_to_plain(item.get("content"))
    searchable = _normalize_match_text(f"{title}\n{raw_description}")
    matched_keywords = _matched_keywords(criteria.role_keywords, searchable)
    if _has_keywords(criteria) and not matched_keywords:
        return None
    if _seniority_mismatches(criteria.seniority, title):
        return None
    if not _matches_location(criteria.location, item, location):
        return None
    location = _best_display_location(criteria.location, item, location)

    employment_type = _employment_type(item, title, raw_description)
    if (
        criteria.employment_types
        and employment_type is not EmploymentType.unknown
        and employment_type not in criteria.employment_types
    ):
        return None

    posted_at = _clean_text(item.get("first_published")) or None
    source_updated_at = _clean_text(item.get("updated_at")) or None
    source_job_id = _clean_text(item.get("id")) or None
    if not _within_max_age(
        posted_at,
        criteria.max_age_days,
        company_slug=company.slug,
        job_id=source_job_id or "unknown",
    ):
        return None
    summary = _summary_from_description(raw_description, title=title, location=location)
    return Role(
        company=company.name,
        title=title,
        url=apply_url,
        location=location or "Location not specified",
        summary=summary,
        match_reason=_match_reason(
            title=title,
            location=location,
            matched_keywords=matched_keywords,
        ),
        source=CompanySource.greenhouse,
        company_slug=company.slug if source_job_id is not None else None,
        source_job_id=source_job_id,
        apply_urls=[apply_url],
        posted_at=posted_at,
        source_updated_at=source_updated_at,
        employment_type=employment_type,
        raw_description=raw_description or None,
        confidence=1.0,
    )


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


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
        elif tag in _BLOCK_TAGS and not self._skipped_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skipped_depth:
            self._skipped_depth -= 1
        elif tag in _BLOCK_TAGS and not self._skipped_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipped_depth:
            self.parts.append(data)


def _html_to_plain(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""

    decoded = value
    for _ in range(2):
        unescaped = unescape(decoded)
        if unescaped == decoded:
            break
        decoded = unescaped

    parser = _PlainTextParser()
    try:
        parser.feed(decoded)
        parser.close()
    except ValueError as exc:
        LOGGER.warning("Greenhouse description contained malformed HTML: %s.", exc)
        return _clean_text(decoded)

    lines = [_clean_text(line) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def _summary_from_description(
    description: str,
    *,
    title: str,
    location: str,
) -> str:
    flat_description = _clean_text(description)
    if not flat_description:
        if location:
            return f"{title} — {location}."
        return f"{title}."

    sentences = re.split(r"(?<=[.!?])\s+", flat_description)
    selected: list[str] = []
    for sentence in sentences:
        candidate = _clean_text(sentence)
        if not candidate:
            continue
        if selected and len(" ".join([*selected, candidate])) > 600:
            break
        selected.append(candidate)
        if len(selected) == 3:
            break

    summary = " ".join(selected) or flat_description
    if len(summary) <= 600:
        return summary
    shortened = summary[:597].rsplit(" ", 1)[0]
    return f"{shortened}..."


def _has_keywords(criteria: JobCriteria) -> bool:
    return any(keyword.strip() for keyword in criteria.role_keywords)


def _matched_keywords(keywords: list[str], searchable: str) -> list[str]:
    padded_searchable = f" {searchable} "
    matches: list[str] = []
    for keyword in keywords:
        cleaned = _clean_text(keyword)
        if not cleaned:
            continue
        normalized_keyword = _normalize_match_text(cleaned)
        if normalized_keyword and f" {normalized_keyword} " in padded_searchable:
            matches.append(cleaned)
    return matches


def _normalize_match_text(value: str) -> str:
    compatible = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"[^a-z0-9]+", " ", compatible.casefold())
    return re.sub(r"\s+", " ", normalized).strip()


def _seniority_mismatches(target: str, title: str) -> bool:
    normalized_title = _normalize_match_text(title)
    title_tokens = set(normalized_title.split())
    has_staff_level = bool({"staff", "principal"} & title_tokens)
    has_senior_level = bool({"senior", "sr", "lead"} & title_tokens)
    has_junior_level = bool(_JUNIOR_LEVELS & title_tokens)

    if target == "junior":
        return bool(_ADVANCED_LEVELS & title_tokens) or bool(
            _NON_JUNIOR_LEVEL_PATTERN.search(normalized_title),
        )
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
    item: dict[str, Any],
    location: str,
) -> bool:
    requested = [
        _location_tokens(value)
        for value in requested_locations
        if value.strip()
    ]
    if not requested:
        return True

    source_locations = [location]
    offices = item.get("offices")
    if isinstance(offices, list):
        for office in offices:
            if not isinstance(office, dict):
                continue
            source_locations.extend(
                [
                    _clean_text(office.get("name")),
                    _clean_text(office.get("location")),
                ]
            )
    available = [
        _location_tokens(segment)
        for value in source_locations
        if value
        for segment in re.split(r"[;|+\n]+", value)
        if segment.strip()
    ]
    return any(
        requested_tokens and requested_tokens <= available_tokens
        for requested_tokens in requested
        for available_tokens in available
    )


def _best_display_location(
    requested_locations: list[str],
    item: dict[str, Any],
    location: str,
) -> str:
    if location and _normalize_match_text(location) not in {
        "n a",
        "na",
        "not specified",
    }:
        return location
    requested = [
        _location_tokens(value)
        for value in requested_locations
        if value.strip()
    ]
    offices = item.get("offices")
    if not isinstance(offices, list):
        return location
    for office in offices:
        if not isinstance(office, dict):
            continue
        candidates = (
            _clean_text(office.get("location")),
            _clean_text(office.get("name")),
        )
        for candidate in candidates:
            available = _location_tokens(candidate)
            if candidate and any(
                wanted and wanted <= available
                for wanted in requested
            ):
                return candidate
    return location


def _location_tokens(value: str) -> set[str]:
    aliases = {"bangalore": "bengaluru"}
    return {
        aliases.get(token, token)
        for token in _normalize_match_text(value).split()
    }


def _within_max_age(
    posted_at: str | None,
    max_age_days: int | None,
    *,
    company_slug: str,
    job_id: str,
) -> bool:
    if max_age_days is None:
        return True
    if not posted_at:
        LOGGER.warning(
            "Keeping Greenhouse job %s for %s with unknown freshness because "
            "first_published is missing.",
            job_id,
            company_slug,
        )
        return True

    try:
        published = _parse_greenhouse_datetime(posted_at)
    except ValueError:
        LOGGER.warning(
            "Keeping Greenhouse job %s for %s with unknown freshness because "
            "first_published %r is invalid.",
            job_id,
            company_slug,
            posted_at,
        )
        return True

    cutoff = _utc_now() - timedelta(days=max_age_days)
    if published < cutoff:
        LOGGER.info(
            "Skipping stale Greenhouse job %s for %s: first_published %s is older "
            "than %s days.",
            job_id,
            company_slug,
            posted_at,
            max_age_days,
        )
        return False
    return True


def _parse_greenhouse_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith(("Z", "z")):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _employment_type(
    item: dict[str, Any],
    title: str,
    raw_description: str,
) -> EmploymentType:
    evidence = [title]
    metadata = item.get("metadata")
    if isinstance(metadata, list):
        for field in metadata:
            if not isinstance(field, dict):
                continue
            name = _normalize_match_text(_clean_text(field.get("name")))
            if name not in {"employment type", "job type", "commitment"}:
                continue
            evidence.append(_clean_text(field.get("value")))

    explicit_description = re.search(
        r"\b(?:employment|job)\s+type\s*[:\-]\s*"
        r"([^.\n]{1,80})"
        r"|\b(?:full[- ]time|part[- ]time|fixed[- ]term|contract)"
        r"\s+(?:position|role|employment)\b",
        raw_description,
        flags=re.IGNORECASE,
    )
    if explicit_description:
        evidence.append(explicit_description.group(0))
    return _employment_type_from_text(" ".join(evidence))


def _employment_type_from_text(value: str) -> EmploymentType:
    normalized = _normalize_match_text(value)
    if "intern" in normalized:
        return EmploymentType.intern
    if any(
        term in normalized
        for term in ("contract", "temporary", "freelance", "fixed term", "ftc")
    ):
        return EmploymentType.contract
    if any(term in normalized for term in ("full time", "fulltime", "permanent")):
        return EmploymentType.full_time
    return EmploymentType.unknown


def _match_reason(
    *,
    title: str,
    location: str,
    matched_keywords: list[str],
) -> str:
    details: list[str] = []
    if matched_keywords:
        quoted = ", ".join(f'"{keyword}"' for keyword in matched_keywords[:3])
        details.append(f"the listing mentions {quoted}")
    if location:
        details.append(f"the posted location is {location}")
    if details:
        return f"First-party Greenhouse role: {'; '.join(details)}."
    return f"First-party Greenhouse listing for {title}."


def _is_trusted_apply_url(url: str, company: Company) -> bool:
    if "\\" in url or any(character.isspace() for character in url):
        return False
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        return False
    if host == _GREENHOUSE_DOMAIN or host.endswith(f".{_GREENHOUSE_DOMAIN}"):
        token = (company.source_token or "").strip().casefold()
        safe_parts = safe_url_path_parts(parsed.path)
        if safe_parts is None:
            return False
        path_parts = [part.casefold() for part in safe_parts]
        return bool(token and path_parts and path_parts[0] == token)

    for domain in company.careers_domains:
        trusted = _domain_from_registry_value(domain)
        if trusted == _GREENHOUSE_DOMAIN or trusted.endswith(
            f".{_GREENHOUSE_DOMAIN}"
        ):
            continue
        if trusted and (host == trusted or host.endswith(f".{trusted}")):
            return True
    return False


def _domain_from_registry_value(value: str) -> str:
    cleaned = value.strip().casefold()
    if not cleaned:
        return ""
    parsed = urlsplit(cleaned if "://" in cleaned else f"https://{cleaned}")
    return (parsed.hostname or "").rstrip(".")


__all__ = ["GreenhouseAdapter", "GreenhouseSourceAdapter"]
