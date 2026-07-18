"""Tiered job-source resolution across a company registry."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from opentelemetry import trace

from job_hunt_agent.schemas import Company, CompanySource, EmploymentType, JobCriteria, Role
from job_hunt_agent.sources.amazon import AmazonAdapter
from job_hunt_agent.sources.ashby import AshbyAdapter
from job_hunt_agent.sources.base import (
    FetchCompleteness,
    FetchScope,
    SourceAdapter,
    SourceFetchResult,
    safe_url_path_parts,
)
from job_hunt_agent.sources.google_jobs import GoogleJobsAdapter
from job_hunt_agent.sources.greenhouse import GreenhouseAdapter
from job_hunt_agent.sources.lever import LeverAdapter
from job_hunt_agent.sources.registry import CompanyRegistry
from job_hunt_agent.sources.smartrecruiters import SmartRecruitersAdapter
from job_hunt_agent.sources.workable import WorkableAdapter
from job_hunt_agent.sources.workday import WorkdayAdapter


LOGGER = logging.getLogger(__name__)
TRACER = trace.get_tracer(__name__)

_RELATIVE_AGE = re.compile(r"(\d+)\+?\s+(minute|hour|day|week|month)s?\s+ago")
_JUNIOR_MAX_REQUIRED_YEARS = 2.0
_BACKEND_ROLE_FAMILY_ALIASES = (
    "Application Engineer",
    "Site Reliability Engineer",
    "SDE",
    "Backend Developer",
    "Infrastructure Engineer",
    "Associate Software Engineer",
    "Backend Engineer",
    "Software Engineer",
    "Software Development Engineer",
    "Platform Engineer",
)
_ADVANCED_TITLE_TOKENS = frozenset(
    {
        "architect",
        "director",
        "fellow",
        "head",
        "lead",
        "manager",
        "principal",
        "senior",
        "sr",
        "staff",
    }
)
_ADVANCED_TITLE_LEVEL = re.compile(
    r"\b(?:software\s+development\s+engineer|software\s+engineer|"
    r"engineer|developer|sde)\s+(?:level\s+)?(?:l?[3-9]|iii|iv|v|vi|vii|viii|ix|x)\b"
    r"|\b(?:level\s+)?(?:l?[3-9]|iii|iv|v|vi|vii|viii|ix|x)\s+"
    r"(?:software\s+development\s+engineer|software\s+engineer|"
    r"engineer|developer|sde)\b"
)
_ADVANCED_ROLE_DESCRIPTION = re.compile(
    r"(?:\b(?:hiring|looking\s+for|seeking|role\s+is|position\s+is)\b.{0,80}"
    r"\b(?:intermediate|mid\s+level|senior|sr|staff|principal|lead|manager)\b"
    r".{0,40}\b(?:engineer|developer)\b)"
    r"|(?:\b(?:intermediate|mid\s+level|senior|sr|staff|principal|lead|manager)\b"
    r".{0,40}\b(?:engineer|developer)\b.{0,20}\b(?:opening|position|role)\b)"
)
_YEAR_VALUE = r"(?:\d{1,2}(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten)"
_YEAR_UNIT = r"(?:years?|yrs?)"
_EXPERIENCE_RANGE = re.compile(
    rf"\b(?P<low>{_YEAR_VALUE})\s*(?:-|to)\s*(?P<high>{_YEAR_VALUE})\s*"
    rf"{_YEAR_UNIT}\b"
)
_EXPERIENCE_BETWEEN = re.compile(
    rf"\bbetween\s+(?P<low>{_YEAR_VALUE})\s+and\s+(?P<high>{_YEAR_VALUE})\s*"
    rf"{_YEAR_UNIT}\b"
)
_EXPERIENCE_SINGLE = re.compile(
    rf"\b(?:(?P<qualifier>at\s+least|minimum(?:\s+of)?|more\s+than|over)\s+)?"
    rf"(?P<years>{_YEAR_VALUE})\s*(?P<plus>\+|or\s+more)?\s*{_YEAR_UNIT}\b"
)
_EXPERIENCE_CONTEXT = re.compile(
    r"\b(?:experience|experienced|qualifications?|requirements?|requires?|required|"
    r"minimum|must|need(?:ed|s)?|should\s+have|you\s+have|looking\s+for)\b"
)
_OPTIONAL_EXPERIENCE_CONTEXT = re.compile(
    r"\b(?:bonus|desirable|ideally|nice\s+to\s+have|preferred)\b"
)
_UPPER_BOUND_EXPERIENCE_CONTEXT = re.compile(
    r"\b(?:at\s+most|less\s+than|maximum(?:\s+of)?|no\s+more\s+than|up\s+to)\s*$"
)
_WORD_YEAR_VALUES = {
    "one": 1.0,
    "two": 2.0,
    "three": 3.0,
    "four": 4.0,
    "five": 5.0,
    "six": 6.0,
    "seven": 7.0,
    "eight": 8.0,
    "nine": 9.0,
    "ten": 10.0,
}


class SourceResolver:
    """Select adapters, fall back honestly, and normalize aggregate results."""

    def __init__(
        self,
        adapters: Iterable[SourceAdapter] | None = None,
        *,
        fallback: SourceAdapter | None = None,
    ) -> None:
        configured = tuple(adapters or _default_adapters())
        self._adapters = configured
        self._by_name = {adapter.name: adapter for adapter in configured}
        self._fallback = fallback or self._by_name.get(
            CompanySource.google_jobs.value,
            GoogleJobsAdapter(),
        )
        self._cache: dict[tuple[str, str, str, bool], SourceFetchResult] = {}

    def fetch_company_roles(
        self,
        company: Company,
        criteria: JobCriteria,
        *,
        use_cache: bool = True,
        allow_fallback: bool = True,
    ) -> list[Role]:
        """Fetch one company's roles through its configured source and fallback."""

        return self.fetch_company_roles_result(
            company,
            criteria,
            use_cache=use_cache,
            allow_fallback=allow_fallback,
        ).roles

    def fetch_company_roles_result(
        self,
        company: Company,
        criteria: JobCriteria,
        *,
        use_cache: bool = True,
        allow_fallback: bool = True,
    ) -> SourceFetchResult:
        """Fetch roles with explicit scope and completeness metadata.

        The list-returning method remains the compatibility projection. Current
        adapters all apply criteria internally and do not distinguish a source
        outage from an empty response, so this path is deliberately marked
        non-authoritative for posting closure.
        """

        requested_criteria = JobCriteria.model_validate(criteria)
        # The saved/requested criteria remains the canonical user snapshot and
        # cache identity. Source adapters receive a derived vocabulary so common
        # backend titles are discoverable without rewriting the user's search.
        source_criteria = _criteria_with_backend_role_aliases(requested_criteria)
        cache_key = (
            company.model_dump_json(),
            requested_criteria.model_dump_json(),
            date.today().isoformat(),
            allow_fallback,
        )
        if use_cache and cache_key in self._cache:
            cached = self._cache[cache_key]
            return cached.model_copy(
                update={
                    "roles": [role.model_copy(deep=True) for role in cached.roles],
                    "cache_hit": True,
                },
                deep=True,
            )

        started_at = datetime.now(timezone.utc)
        with TRACER.start_as_current_span("job_source.resolve_company") as span:
            span.set_attribute("job_source.company_slug", company.slug)
            span.set_attribute("job_source.configured_source", company.source.value)
            adapter = self._adapter_for(company)
            roles, source_failed = self._fetch(adapter, company, source_criteria)
            warning_codes = ["source_fetch_failed"] if source_failed else []
            used_fallback = False
            if (
                not roles
                and allow_fallback
                and adapter is not self._fallback
                and self._fallback.supports(company)
            ):
                used_fallback = True
                roles, fallback_failed = self._fetch(
                    self._fallback,
                    company,
                    source_criteria,
                )
                if fallback_failed:
                    warning_codes.append("fallback_source_fetch_failed")

            normalized = _filter_and_dedupe(roles, source_criteria)
            selected_adapter = self._fallback if used_fallback else adapter
            span.set_attribute(
                "job_source.selected_source",
                selected_adapter.name,
            )
            span.set_attribute("job_source.used_fallback", used_fallback)
            span.set_attribute("job_source.fetch_failed", source_failed)
            span.set_attribute("job_source.role_count", len(normalized))

        result = SourceFetchResult(
            company_slug=company.slug,
            source=_adapter_source(selected_adapter, fallback=company.source),
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            scope=FetchScope.criteria_filtered,
            completeness=FetchCompleteness.partial,
            roles=[role.model_copy(deep=True) for role in normalized],
            observed_count=len(roles),
            returned_count=len(normalized),
            cache_hit=False,
            used_fallback=used_fallback,
            warning_codes=warning_codes,
        )

        if use_cache:
            self._cache[cache_key] = result.model_copy(deep=True)
        return result

    def fetch_registry_roles(
        self,
        registry: CompanyRegistry,
        criteria: JobCriteria,
        *,
        max_per_company: int | None = None,
        max_total: int | None = None,
        use_cache: bool = True,
        allow_fallback: bool = True,
        require_first_party: bool = False,
    ) -> list[Role]:
        """Aggregate roles from every active company in a registry."""

        roles: list[Role] = []
        for company in registry.active_companies:
            company_roles = self.fetch_company_roles(
                company,
                criteria,
                use_cache=use_cache,
                allow_fallback=allow_fallback,
            )
            if require_first_party:
                company_roles = [
                    role
                    for role in company_roles
                    if is_first_party_role(role, company)
                ]
            if max_per_company is not None:
                company_roles = company_roles[: max(0, max_per_company)]
            roles.extend(company_roles)
            if max_total is not None and len(roles) >= max_total:
                roles = roles[:max_total]
                break
        return _dedupe_roles(roles)

    def clear_cache(self) -> None:
        self._cache.clear()

    def _adapter_for(self, company: Company) -> SourceAdapter:
        explicit = self._by_name.get(company.source.value)
        if explicit is not None and explicit.supports(company):
            return explicit
        for adapter in self._adapters:
            if adapter is self._fallback:
                continue
            if adapter.supports(company):
                return adapter
        return self._fallback

    @staticmethod
    def _fetch(
        adapter: SourceAdapter,
        company: Company,
        criteria: JobCriteria,
    ) -> tuple[list[Role], bool]:
        try:
            return (
                [
                    Role.model_validate(role)
                    for role in adapter.fetch_open_roles(company, criteria)
                ],
                False,
            )
        except Exception as exc:
            LOGGER.warning(
                "Source adapter %s failed for %s (%s)",
                adapter.name,
                company.slug,
                type(exc).__name__,
            )
            return [], True


def is_first_party_role(role: Role, company: Company) -> bool:
    """Return whether the preferred role URL belongs to a configured careers domain."""

    try:
        parsed = urlsplit(role.url)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or safe_url_path_parts(parsed.path) is None
        or "\\" in role.url
        or any(character.isspace() for character in role.url)
    ):
        return False
    hostname = parsed.hostname.casefold().rstrip(".")
    path_parts = safe_url_path_parts(parsed.path)
    if path_parts is None or not _matches_shared_ats_tenant(
        hostname,
        path_parts,
        company,
    ):
        return False
    return any(
        hostname == domain.casefold()
        or hostname.endswith(f".{domain.casefold()}")
        for domain in company.careers_domains
    )


def _matches_shared_ats_tenant(
    hostname: str,
    path_parts: tuple[str, ...],
    company: Company,
) -> bool:
    token = (company.source_token or "").strip().casefold()
    expected: str | None = None
    if hostname == "greenhouse.io" or hostname.endswith(".greenhouse.io"):
        expected = token
    elif hostname in {"jobs.lever.co", "jobs.eu.lever.co"}:
        expected = token
    elif hostname == "jobs.ashbyhq.com":
        expected = token
    elif hostname == "jobs.smartrecruiters.com":
        expected = token
    elif hostname == "apply.workable.com":
        expected = token
    elif hostname.endswith(".myworkdayjobs.com"):
        expected = token.split(":", 1)[1] if ":" in token else ""

    if expected is None:
        return True
    return bool(path_parts and expected and path_parts[0].casefold() == expected)


def _default_adapters() -> tuple[SourceAdapter, ...]:
    return (
        GreenhouseAdapter(),
        LeverAdapter(),
        AshbyAdapter(),
        WorkdayAdapter(),
        SmartRecruitersAdapter(),
        WorkableAdapter(),
        AmazonAdapter(),
        GoogleJobsAdapter(),
    )


def _criteria_with_backend_role_aliases(criteria: JobCriteria) -> JobCriteria:
    """Return source-only criteria with conservative backend title aliases."""

    normalized_keywords = [_normalized(value) for value in criteria.role_keywords]
    keyword_token_sets = [set(value.split()) for value in normalized_keywords if value]
    is_backend_role_search = any(
        "backend" in tokens
        or "sde" in tokens
        or (
            "software" in tokens
            and bool(tokens.intersection({"developer", "engineer", "engineering"}))
        )
        for tokens in keyword_token_sets
    )
    if not is_backend_role_search:
        return criteria.model_copy(deep=True)

    expanded = list(criteria.role_keywords)
    seen = {normalized for normalized in normalized_keywords if normalized}
    for alias in _BACKEND_ROLE_FAMILY_ALIASES:
        normalized_alias = _normalized(alias)
        if normalized_alias in seen:
            continue
        expanded.append(alias)
        seen.add(normalized_alias)
    return criteria.model_copy(update={"role_keywords": expanded}, deep=True)


def _filter_and_dedupe(roles: Iterable[Role], criteria: JobCriteria) -> list[Role]:
    filtered: list[Role] = []
    for role in roles:
        if not _matches_role_intent(role, criteria):
            continue
        if not _matches_seniority_evidence(role, criteria):
            continue
        if (
            criteria.employment_types
            and role.employment_type is not EmploymentType.unknown
            and role.employment_type not in criteria.employment_types
        ):
            continue
        age = _age_days(role.posted_at)
        if (
            criteria.max_age_days is not None
            and age is not None
            and age > criteria.max_age_days
        ):
            continue
        filtered.append(role)
    return _dedupe_roles(filtered)


def _dedupe_roles(roles: Iterable[Role]) -> list[Role]:
    selected: list[Role] = []
    seen_native_ids: set[tuple[str, str, str]] = set()
    seen_urls: set[str] = set()
    for role in roles:
        native_id = _native_identity(role)
        urls = {
            _normalized_url_identity(url)
            for url in [role.url, *role.apply_urls]
            if url.strip()
        }
        if (
            native_id is not None
            and native_id in seen_native_ids
        ) or seen_urls.intersection(urls):
            continue
        if native_id is not None:
            seen_native_ids.add(native_id)
        seen_urls.update(urls)
        selected.append(role)
    return selected


def _native_identity(role: Role) -> tuple[str, str, str] | None:
    if role.company_slug is None or role.source_job_id is None:
        return None
    return (
        role.source.value,
        role.company_slug.casefold(),
        role.source_job_id,
    )


def _adapter_source(
    adapter: SourceAdapter,
    *,
    fallback: CompanySource,
) -> CompanySource:
    try:
        return CompanySource(adapter.name)
    except ValueError:
        return fallback


def _normalized_url_identity(value: str) -> str:
    """Normalize a trusted HTTPS posting URL without changing path semantics."""

    cleaned = value.strip()
    try:
        parsed = urlsplit(cleaned)
        port = parsed.port
    except ValueError:
        return cleaned.rstrip("/")
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        return cleaned.rstrip("/")

    hostname = parsed.hostname.casefold().rstrip(".")
    netloc = hostname if port in (None, 443) else f"{hostname}:{port}"
    path = parsed.path.rstrip("/") or "/"
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_")
        ),
        doseq=True,
    )
    return urlunsplit(("https", netloc, path, query, ""))


def _age_days(posted_at: str | None) -> int | None:
    if not posted_at:
        return None
    normalized = posted_at.strip().casefold()
    if normalized in {"today", "just posted", "just now"}:
        return 0
    relative = _RELATIVE_AGE.search(normalized)
    if relative:
        value = int(relative.group(1))
        unit = relative.group(2)
        return {
            "minute": 0,
            "hour": 0,
            "day": value,
            "week": value * 7,
            "month": value * 30,
        }[unit]
    try:
        parsed = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed_date = date.fromisoformat(posted_at[:10])
        except ValueError:
            return None
        return max(0, (date.today() - parsed_date).days)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).days)


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _matches_role_intent(role: Role, criteria: JobCriteria) -> bool:
    requested = {
        token
        for keyword in criteria.role_keywords
        for token in _normalized(keyword).split()
    }
    title = set(_normalized(role.title).split())
    if "backend" in requested and title.intersection(
        {"frontend", "android", "ios", "mobile"}
    ):
        return False
    return True


def _matches_seniority_evidence(role: Role, criteria: JobCriteria) -> bool:
    if criteria.seniority != "junior":
        return True
    if _title_is_above_junior(role.title):
        return False
    description = " ".join(
        value
        for value in (role.summary, role.raw_description or "")
        if value.strip()
    )
    if _description_names_advanced_role(description):
        return False
    return not _requires_above_junior_experience(description)


def _title_is_above_junior(title: str) -> bool:
    normalized = _normalized(title)
    tokens = set(normalized.split())
    if tokens.intersection(_ADVANCED_TITLE_TOKENS):
        return True
    if "intermediate" in tokens or ({"mid", "level"} <= tokens):
        return True
    return bool(_ADVANCED_TITLE_LEVEL.search(normalized))


def _description_names_advanced_role(description: str) -> bool:
    normalized = _normalized(description)
    return bool(_ADVANCED_ROLE_DESCRIPTION.search(normalized))


def _requires_above_junior_experience(description: str) -> bool:
    normalized = re.sub(r"\s+", " ", description.casefold())
    normalized = normalized.replace("–", "-").replace("—", "-")
    range_spans: list[tuple[int, int]] = []
    for pattern in (_EXPERIENCE_RANGE, _EXPERIENCE_BETWEEN):
        for match in pattern.finditer(normalized):
            range_spans.append(match.span())
            if not _is_required_experience_mention(normalized, match):
                continue
            if _year_value(match.group("low")) > _JUNIOR_MAX_REQUIRED_YEARS:
                return True
    for match in _EXPERIENCE_SINGLE.finditer(normalized):
        if any(
            start <= match.start() and match.end() <= end
            for start, end in range_spans
        ):
            continue
        if not _is_required_experience_mention(normalized, match):
            continue
        years = _year_value(match.group("years"))
        qualifier = " ".join((match.group("qualifier") or "").split())
        if qualifier in {"more than", "over"}:
            if years >= _JUNIOR_MAX_REQUIRED_YEARS:
                return True
        elif years > _JUNIOR_MAX_REQUIRED_YEARS:
            return True
    return False


def _is_required_experience_mention(text: str, match: re.Match[str]) -> bool:
    before = text[max(0, match.start() - 80) : match.start()]
    after = text[match.end() : match.end() + 80]
    nearby_before = before[-40:]
    nearby_after = after[:40]
    if _UPPER_BOUND_EXPERIENCE_CONTEXT.search(nearby_before):
        return False
    if _OPTIONAL_EXPERIENCE_CONTEXT.search(nearby_before) or (
        _OPTIONAL_EXPERIENCE_CONTEXT.search(nearby_after)
        and not _EXPERIENCE_CONTEXT.search(nearby_before)
    ):
        return False
    return bool(_EXPERIENCE_CONTEXT.search(f"{before} {after}"))


def _year_value(value: str) -> float:
    word_value = _WORD_YEAR_VALUES.get(value)
    return word_value if word_value is not None else float(value)


__all__ = ["SourceResolver", "is_first_party_role"]
