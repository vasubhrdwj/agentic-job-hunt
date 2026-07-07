"""Tiered job-source resolution across a company registry."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Iterable
from urllib.parse import urlsplit

from opentelemetry import trace

from job_hunt_agent.schemas import Company, CompanySource, EmploymentType, JobCriteria, Role
from job_hunt_agent.sources.amazon import AmazonAdapter
from job_hunt_agent.sources.ashby import AshbyAdapter
from job_hunt_agent.sources.base import SourceAdapter, safe_url_path_parts
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
        self._cache: dict[tuple[str, str, str], tuple[Role, ...]] = {}

    def fetch_company_roles(
        self,
        company: Company,
        criteria: JobCriteria,
        *,
        use_cache: bool = True,
        allow_fallback: bool = True,
    ) -> list[Role]:
        """Fetch one company's roles through its configured source and fallback."""

        criteria = JobCriteria.model_validate(criteria)
        cache_key = (
            company.model_dump_json(),
            criteria.model_dump_json(),
            date.today().isoformat(),
        )
        if use_cache and cache_key in self._cache:
            return [role.model_copy(deep=True) for role in self._cache[cache_key]]

        with TRACER.start_as_current_span("job_source.resolve_company") as span:
            span.set_attribute("job_source.company_slug", company.slug)
            span.set_attribute("job_source.configured_source", company.source.value)
            adapter = self._adapter_for(company)
            roles = self._fetch(adapter, company, criteria)
            used_fallback = False
            if (
                not roles
                and allow_fallback
                and adapter is not self._fallback
                and self._fallback.supports(company)
            ):
                used_fallback = True
                roles = self._fetch(self._fallback, company, criteria)

            normalized = _filter_and_dedupe(roles, criteria)
            span.set_attribute(
                "job_source.selected_source",
                adapter.name if not used_fallback else self._fallback.name,
            )
            span.set_attribute("job_source.used_fallback", used_fallback)
            span.set_attribute("job_source.role_count", len(normalized))

        if use_cache:
            self._cache[cache_key] = tuple(
                role.model_copy(deep=True) for role in normalized
            )
        return normalized

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
    ) -> list[Role]:
        try:
            return [
                Role.model_validate(role)
                for role in adapter.fetch_open_roles(company, criteria)
            ]
        except Exception as exc:
            LOGGER.warning(
                "Source adapter %s failed for %s (%s)",
                adapter.name,
                company.slug,
                type(exc).__name__,
            )
            return []


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


def _filter_and_dedupe(roles: Iterable[Role], criteria: JobCriteria) -> list[Role]:
    filtered: list[Role] = []
    for role in roles:
        if not _matches_role_intent(role, criteria):
            continue
        if not _matches_seniority_evidence(role, criteria):
            continue
        if (
            criteria.employment_types
            and role.employment_type not in criteria.employment_types
        ):
            continue
        age = _age_days(role.posted_at)
        if (
            criteria.max_age_days is not None
            and (age is None or age > criteria.max_age_days)
        ):
            continue
        filtered.append(role)
    return _dedupe_roles(filtered)


def _dedupe_roles(roles: Iterable[Role]) -> list[Role]:
    selected: list[Role] = []
    seen_company_titles: set[tuple[str, str]] = set()
    seen_urls: set[str] = set()
    for role in roles:
        company_title = (
            _normalized(role.company),
            _normalized(role.title),
        )
        urls = {
            url.strip().casefold().rstrip("/")
            for url in [role.url, *role.apply_urls]
            if url.strip()
        }
        if company_title in seen_company_titles or seen_urls.intersection(urls):
            continue
        seen_company_titles.add(company_title)
        seen_urls.update(urls)
        selected.append(role)
    return selected


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
    evidence = " ".join(
        [
            role.title,
            role.raw_description or "",
        ]
    ).casefold()
    if re.search(
        r"\b(?:senior|staff|principal|lead|manager|intermediate|mid-level)\b.{0,30}"
        r"\b(?:engineer|developer)\b",
        evidence,
    ):
        return False
    years = [
        int(value)
        for value in re.findall(
            r"\b(?:at least\s+|minimum\s+)?(\d{1,2})\+?\s+years?\b",
            evidence,
        )
    ]
    return not years or min(years) <= 2


__all__ = ["SourceResolver", "is_first_party_role"]
