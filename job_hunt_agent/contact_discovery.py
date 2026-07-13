"""Pure, evidence-preserving contact discovery and bench selection.

This module deliberately owns no provider credentials and performs no database
work.  Callers inject a search provider, receive an auditable candidate pool,
and persist the result in a separate transaction.  A search failure is never
reported as successful exhaustion, and a five-person target never permits
inventing or weakening evidence for missing contacts.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from itertools import islice
from typing import Iterator, Protocol, TypeAlias, runtime_checkable
from urllib.parse import urlsplit

from .schemas import Role
from .sources.base import safe_url_path_parts
from .tools.referrals import (
    _company_aliases,
    _company_matches,
    _contact_category,
    _current_company_signal,
    _display_title,
    _extract_name_and_title,
    _looks_like_person_name,
    _normalize_match_text,
    _title_overlaps_role,
    _title_points_to_other_company,
)


DEFAULT_CANDIDATE_LIMIT = 12
MAX_CANDIDATE_LIMIT = 12
DEFAULT_TARGET_COUNT = 5
DEFAULT_CONFIDENCE_FLOOR = 0.75
MAX_RESULT_TEXT_CHARS = 1_000
MAX_RESULT_URL_CHARS = 2_048
MAX_PUBLIC_NAME_CHARS = 200
MAX_CURRENT_TITLE_CHARS = 300


class DiscoveryCategory(str, Enum):
    """The independently searched contact lane."""

    peer = "peer"
    leader = "leader"
    recruiter = "recruiter"


class ContactCategory(str, Enum):
    """Application-specific category, aligned with durable contact rows."""

    team_peer = "team_peer"
    team_leader = "team_leader"
    recruiter = "recruiter"


class DiscoveryOutcome(str, Enum):
    """Why bounded discovery stopped."""

    exhausted = "exhausted"
    candidate_limit_reached = "candidate_limit_reached"
    incomplete = "incomplete"
    partial_provider_failure = "partial_provider_failure"
    provider_failure = "provider_failure"
    configuration_failure = "configuration_failure"


class BenchCoverageStatus(str, Enum):
    met = "met"
    partial = "partial"


@dataclass(frozen=True)
class DiscoveryQuery:
    category: DiscoveryCategory
    query: str


@dataclass(frozen=True)
class ProviderSearchResult:
    """One provider observation before verification and normalization."""

    result_title: str
    result_url: str
    result_excerpt: str
    result_position: int | None = None
    observed_at: datetime | None = None
    confidence: float | None = None


ProviderResultInput: TypeAlias = ProviderSearchResult | Mapping[str, object]


@dataclass(frozen=True)
class ProviderSearchPage:
    """Optional richer provider response.

    Providers returning a plain iterable are treated as having exhausted the
    single bounded query they were asked to perform.  A paginated adapter can
    set ``exhausted=False`` to prevent the result from claiming exhaustion.
    """

    results: Iterable[ProviderResultInput]
    exhausted: bool = True


@runtime_checkable
class ContactSearchProvider(Protocol):
    """Injected boundary for public-profile search."""

    name: str

    def search(
        self,
        query: str,
        *,
        category: DiscoveryCategory,
        limit: int,
    ) -> Iterable[ProviderResultInput] | ProviderSearchPage:
        """Return public search observations for one independent lane."""
        ...


class ContactProviderError(RuntimeError):
    """Known retryable provider failure without exposing provider payloads."""


class ContactProviderConfigurationError(ContactProviderError):
    """Known non-retryable local/provider configuration failure."""


class _MalformedProviderResponse(ValueError):
    """Internal marker for a response container that cannot be trusted."""


@dataclass(frozen=True)
class ContactEvidence:
    """Exact bounded search observation supporting current employment."""

    provider: str
    query: str
    query_category: DiscoveryCategory
    result_position: int
    result_title: str
    result_excerpt: str
    result_url: str
    observed_at: datetime


@dataclass(frozen=True)
class DiscoveredContact:
    """One deduplicated, current-employer-verified public profile."""

    public_name: str
    current_title: str
    current_company: str
    profile_url: str
    normalized_profile_url: str
    profile_source: str
    category: ContactCategory
    verified_current_employer: bool
    confidence: float
    why_relevant: str
    score_total: int
    score_components: Mapping[str, int]
    evidence: tuple[ContactEvidence, ...]

    @property
    def primary_evidence(self) -> ContactEvidence:
        """Return the deterministic evidence snapshot used for persistence."""

        if not self.evidence:
            raise ValueError("a discovered contact has no employer evidence")
        return self.evidence[0]


@dataclass(frozen=True)
class RejectedContactResult:
    """A bounded provider observation that failed a named evidence rule."""

    reason_code: str
    query: str
    query_category: DiscoveryCategory
    result_position: int
    result_title: str
    result_excerpt: str
    result_url: str
    observed_at: datetime
    normalized_profile_url: str | None = None


@dataclass(frozen=True)
class DiagnosticCount:
    code: str
    count: int


@dataclass(frozen=True)
class ProviderFailureDiagnostic:
    query: str
    category: DiscoveryCategory
    code: str
    retryable: bool
    error_type: str


@dataclass(frozen=True)
class DiscoveryDiagnostics:
    outcome: DiscoveryOutcome
    exhausted: bool
    retryable: bool
    candidate_limit_reached: bool
    queries_attempted: int
    queries_succeeded: int
    queries_failed: int
    results_observed: int
    accepted_count: int
    rejected_counts: tuple[DiagnosticCount, ...]
    provider_failures: tuple[ProviderFailureDiagnostic, ...]

    @property
    def provider_failed(self) -> bool:
        return bool(self.provider_failures)


@dataclass(frozen=True)
class ContactDiscoveryResult:
    candidates: tuple[DiscoveredContact, ...]
    rejected_results: tuple[RejectedContactResult, ...]
    diagnostics: DiscoveryDiagnostics

    def __iter__(self) -> Iterator[DiscoveredContact]:
        return iter(self.candidates)

    def __len__(self) -> int:
        return len(self.candidates)


@dataclass(frozen=True)
class ContactShortfallReason:
    code: str
    count: int
    detail: str


@dataclass(frozen=True)
class ContactBenchSelection:
    selected: tuple[DiscoveredContact, ...]
    target_count: int
    verified_count: int
    eligible_count: int
    coverage_status: BenchCoverageStatus
    exhausted: bool
    retryable: bool
    shortfall_reasons: tuple[ContactShortfallReason, ...]
    discovery_diagnostics: DiscoveryDiagnostics | None

    @property
    def coverage_label(self) -> str:
        return f"{self.verified_count}/{self.target_count} verified"


def build_contact_queries(role: Role | Mapping[str, object]) -> tuple[DiscoveryQuery, ...]:
    """Build one independent bounded query for every useful contact lane."""

    validated = Role.model_validate(role)
    company = _query_phrase(validated.company)
    title = _query_phrase(validated.title)
    return (
        DiscoveryQuery(
            category=DiscoveryCategory.peer,
            query=f'site:linkedin.com/in "{company}" "{title}"',
        ),
        DiscoveryQuery(
            category=DiscoveryCategory.leader,
            query=(
                f'site:linkedin.com/in "{company}" '
                '"engineering manager" OR "engineering director" OR "head of engineering"'
            ),
        ),
        DiscoveryQuery(
            category=DiscoveryCategory.recruiter,
            query=(
                f'site:linkedin.com/in "{company}" '
                '"technical recruiter" OR "talent sourcer"'
            ),
        ),
    )


def normalize_profile_url(value: str) -> tuple[str, str] | None:
    """Return ``(canonical_url, source)`` for a strict public person profile.

    The function accepts LinkedIn country hosts but canonicalizes them to
    ``www.linkedin.com``.  It rejects credentials, non-HTTPS URLs, unusual
    ports, path traversal, company/search pages, GitHub repositories, and
    lookalike hosts.  Query strings and fragments never participate in identity.
    """

    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if (
        not cleaned
        or len(cleaned) > MAX_RESULT_URL_CHARS
        or "\\" in cleaned
        or any(character.isspace() or ord(character) < 32 for character in cleaned)
    ):
        return None
    try:
        parsed = urlsplit(cleaned)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or not parsed.hostname
    ):
        return None

    host = parsed.hostname.casefold()
    path_parts = safe_url_path_parts(parsed.path)
    if path_parts is None:
        return None

    linkedin_host = (
        host in {"linkedin.com", "www.linkedin.com"}
        or re.fullmatch(r"[a-z]{2,3}\.linkedin\.com", host) is not None
    )
    if linkedin_host:
        if len(path_parts) != 2 or path_parts[0].casefold() != "in":
            return None
        slug = path_parts[1].casefold()
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,199}", slug) is None:
            return None
        return f"https://www.linkedin.com/in/{slug}", "linkedin"

    if host in {"github.com", "www.github.com"}:
        if len(path_parts) != 1:
            return None
        username = path_parts[0].casefold()
        if (
            len(username) > 39
            or "--" in username
            or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?", username)
            is None
            or username in _GITHUB_RESERVED_PATHS
        ):
            return None
        return f"https://github.com/{username}", "github"

    return None


def discover_contacts(
    role: Role | Mapping[str, object],
    *,
    provider: ContactSearchProvider,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    observed_at: datetime | None = None,
) -> ContactDiscoveryResult:
    """Discover a bounded candidate pool without stopping when five appear."""

    validated_role = Role.model_validate(role)
    if isinstance(candidate_limit, bool) or not 1 <= candidate_limit <= MAX_CANDIDATE_LIMIT:
        raise ValueError(f"candidate_limit must be between 1 and {MAX_CANDIDATE_LIMIT}")
    fallback_observed_at = _aware_utc(observed_at or datetime.now(timezone.utc))
    provider_name = _provider_name(provider)

    accepted: dict[str, tuple[DiscoveredContact, list[ContactEvidence]]] = {}
    blocked_urls: set[str] = set()
    rejected: list[RejectedContactResult] = []
    rejection_counts: Counter[str] = Counter()
    provider_failures: list[ProviderFailureDiagnostic] = []
    queries_succeeded = 0
    results_observed = 0
    all_pages_exhausted = True

    queries = build_contact_queries(validated_role)
    for query_spec in queries:
        try:
            response = provider.search(
                query_spec.query,
                category=query_spec.category,
                limit=candidate_limit,
            )
            if isinstance(response, ProviderSearchPage):
                raw_results = response.results
                all_pages_exhausted = all_pages_exhausted and response.exhausted
            else:
                raw_results = response
            if isinstance(raw_results, (str, bytes, bytearray, Mapping)):
                raise _MalformedProviderResponse
            iterator = iter(raw_results)
            bounded = list(islice(iterator, candidate_limit + 1))
        except Exception as exc:  # Provider boundaries must become safe diagnostics.
            configuration_failure = isinstance(exc, ContactProviderConfigurationError)
            malformed_failure = isinstance(exc, _MalformedProviderResponse)
            provider_failures.append(
                ProviderFailureDiagnostic(
                    query=query_spec.query,
                    category=query_spec.category,
                    code=(
                        "provider_configuration_failure"
                        if configuration_failure
                        else (
                            "provider_malformed_response"
                            if malformed_failure
                            else "provider_failure"
                        )
                    ),
                    retryable=not configuration_failure,
                    error_type=(
                        "MalformedProviderResponse"
                        if malformed_failure
                        else type(exc).__name__[:100]
                    ),
                )
            )
            continue

        if len(bounded) > candidate_limit:
            bounded = bounded[:candidate_limit]
            all_pages_exhausted = False
            rejection_counts["provider_result_limit_exceeded"] += 1
        results_observed += len(bounded)

        coerced_results: list[tuple[int, ProviderSearchResult]] = []
        for fallback_position, raw_result in enumerate(bounded, start=1):
            coerced = _coerce_provider_result(raw_result)
            if coerced is None:
                rejection_counts["invalid_provider_result"] += 1
                continue
            coerced_results.append((fallback_position, coerced))

        if bounded and not coerced_results:
            provider_failures.append(
                ProviderFailureDiagnostic(
                    query=query_spec.query,
                    category=query_spec.category,
                    code="provider_malformed_response",
                    retryable=True,
                    error_type="MalformedProviderResponse",
                )
            )
            continue

        queries_succeeded += 1
        for fallback_position, coerced in coerced_results:
            position = _result_position(coerced.result_position, fallback_position)
            result_observed_at = _coerce_observed_at(
                coerced.observed_at,
                fallback=fallback_observed_at,
            )
            result_title = _bounded_text(coerced.result_title)
            result_excerpt = _bounded_text(coerced.result_excerpt) or result_title
            result_url = coerced.result_url.strip()

            parsed, reason_code, normalized_url = _contact_from_result(
                result=coerced,
                role=validated_role,
                query_spec=query_spec,
                provider_name=provider_name,
                position=position,
                result_title=result_title,
                result_excerpt=result_excerpt,
                result_url=result_url,
                observed_at=result_observed_at,
            )
            if parsed is None:
                reason = reason_code or "invalid_provider_result"
                rejection_counts[reason] += 1
                rejected.append(
                    RejectedContactResult(
                        reason_code=reason,
                        query=query_spec.query,
                        query_category=query_spec.category,
                        result_position=position,
                        result_title=result_title,
                        result_excerpt=result_excerpt,
                        result_url=result_url[:MAX_RESULT_URL_CHARS],
                        observed_at=result_observed_at,
                        normalized_profile_url=normalized_url,
                    )
                )
                if normalized_url and reason in {
                    "former_target_employer",
                    "conflicting_current_employer",
                }:
                    blocked_urls.add(normalized_url)
                continue

            evidence = parsed.primary_evidence
            existing = accepted.get(parsed.normalized_profile_url)
            if existing is None:
                accepted[parsed.normalized_profile_url] = (parsed, [evidence])
                continue

            existing_contact, evidences = existing
            evidences.append(evidence)
            best = min((existing_contact, parsed), key=_candidate_sort_key)
            accepted[parsed.normalized_profile_url] = (best, evidences)
            rejection_counts["duplicate_profile_merged"] += 1

    for normalized_url in sorted(blocked_urls):
        blocked = accepted.pop(normalized_url, None)
        if blocked is None:
            continue
        rejection_counts["conflicting_duplicate_evidence"] += 1
        contact, evidences = blocked
        for evidence in evidences:
            rejected.append(
                RejectedContactResult(
                    reason_code="conflicting_duplicate_evidence",
                    query=evidence.query,
                    query_category=evidence.query_category,
                    result_position=evidence.result_position,
                    result_title=evidence.result_title,
                    result_excerpt=evidence.result_excerpt,
                    result_url=evidence.result_url,
                    observed_at=evidence.observed_at,
                    normalized_profile_url=contact.normalized_profile_url,
                )
            )

    candidates = []
    for contact, evidences in accepted.values():
        # Keep the evidence that produced ``contact.score_components`` first;
        # the remaining observations are audit context, not a replacement for
        # the score's provenance.
        primary_evidence = contact.primary_evidence
        remaining_evidence = list(evidences)
        remaining_evidence.remove(primary_evidence)
        ordered_evidence = (
            primary_evidence,
            *sorted(remaining_evidence, key=_evidence_sort_key),
        )
        candidates.append(replace(contact, evidence=ordered_evidence))
    candidates.sort(key=_candidate_sort_key)

    overflow = candidates[candidate_limit:]
    candidates = candidates[:candidate_limit]
    for contact in overflow:
        rejection_counts["candidate_limit"] += 1
        evidence = contact.primary_evidence
        rejected.append(
            RejectedContactResult(
                reason_code="candidate_limit",
                query=evidence.query,
                query_category=evidence.query_category,
                result_position=evidence.result_position,
                result_title=evidence.result_title,
                result_excerpt=evidence.result_excerpt,
                result_url=evidence.result_url,
                observed_at=evidence.observed_at,
                normalized_profile_url=contact.normalized_profile_url,
            )
        )

    diagnostics = _discovery_diagnostics(
        candidate_count=len(candidates),
        candidate_limit=candidate_limit,
        queries_attempted=len(queries),
        queries_succeeded=queries_succeeded,
        results_observed=results_observed,
        all_pages_exhausted=all_pages_exhausted,
        rejection_counts=rejection_counts,
        provider_failures=provider_failures,
    )
    return ContactDiscoveryResult(
        candidates=tuple(candidates),
        rejected_results=tuple(
            sorted(
                rejected,
                key=lambda item: (
                    item.query_category.value,
                    item.result_position,
                    item.normalized_profile_url or "",
                    item.reason_code,
                ),
            )
        ),
        diagnostics=diagnostics,
    )


def select_contact_bench(
    candidates_or_result: Iterable[DiscoveredContact] | ContactDiscoveryResult,
    *,
    target_count: int = DEFAULT_TARGET_COUNT,
    confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
) -> ContactBenchSelection:
    """Select a deterministic, category-diverse bench without padding."""

    if isinstance(target_count, bool) or not 1 <= target_count <= DEFAULT_TARGET_COUNT:
        raise ValueError(f"target_count must be between 1 and {DEFAULT_TARGET_COUNT}")
    if (
        isinstance(confidence_floor, bool)
        or not isinstance(confidence_floor, (int, float))
        or not DEFAULT_CONFIDENCE_FLOOR <= float(confidence_floor) <= 1.0
    ):
        raise ValueError(
            f"confidence_floor must be between {DEFAULT_CONFIDENCE_FLOOR} and 1.0"
        )

    if isinstance(candidates_or_result, ContactDiscoveryResult):
        candidates = candidates_or_result.candidates
        discovery = candidates_or_result.diagnostics
    else:
        candidates = tuple(candidates_or_result)
        discovery = None

    best_by_profile: dict[str, DiscoveredContact] = {}
    for candidate in candidates:
        if not _eligible_for_bench(candidate, confidence_floor=float(confidence_floor)):
            continue
        existing = best_by_profile.get(candidate.normalized_profile_url)
        if existing is None or _candidate_sort_key(candidate) < _candidate_sort_key(existing):
            best_by_profile[candidate.normalized_profile_url] = candidate

    ranked = sorted(best_by_profile.values(), key=_candidate_sort_key)
    selected: list[DiscoveredContact] = []

    def take(category: ContactCategory) -> None:
        for candidate in ranked:
            if candidate not in selected and candidate.category is category:
                selected.append(candidate)
                return

    desired_mix = (
        ContactCategory.team_peer,
        ContactCategory.team_leader,
        ContactCategory.recruiter,
        ContactCategory.team_peer,
    )
    for category in desired_mix[:target_count]:
        take(category)
    for candidate in ranked:
        if candidate not in selected:
            selected.append(candidate)
        if len(selected) >= target_count:
            break
    selected = selected[:target_count]

    missing_count = target_count - len(selected)
    shortfall_reasons: list[ContactShortfallReason] = []
    if missing_count:
        shortfall_reasons.append(
            ContactShortfallReason(
                code="verified_contacts_shortfall",
                count=missing_count,
                detail=(
                    f"Only {len(selected)} of {target_count} distinct contacts met "
                    f"the {float(confidence_floor):.2f} evidence floor."
                ),
            )
        )
        if discovery is not None and discovery.provider_failed:
            shortfall_reasons.append(
                ContactShortfallReason(
                    code="provider_failure",
                    count=discovery.queries_failed,
                    detail="One or more discovery lanes failed; retry may find more contacts.",
                )
            )
        elif discovery is not None and discovery.exhausted:
            shortfall_reasons.append(
                ContactShortfallReason(
                    code="search_exhausted",
                    count=missing_count,
                    detail="The configured discovery budget completed without enough evidence.",
                )
            )
        else:
            shortfall_reasons.append(
                ContactShortfallReason(
                    code="discovery_incomplete",
                    count=missing_count,
                    detail="Discovery is not proven exhausted; more verified contacts may exist.",
                )
            )

    return ContactBenchSelection(
        selected=tuple(selected),
        target_count=target_count,
        verified_count=len(selected),
        eligible_count=len(ranked),
        coverage_status=(
            BenchCoverageStatus.met if not missing_count else BenchCoverageStatus.partial
        ),
        exhausted=bool(discovery and discovery.exhausted),
        retryable=bool(discovery and discovery.retryable),
        shortfall_reasons=tuple(shortfall_reasons),
        discovery_diagnostics=discovery,
    )


def _contact_from_result(
    *,
    result: ProviderSearchResult,
    role: Role,
    query_spec: DiscoveryQuery,
    provider_name: str,
    position: int,
    result_title: str,
    result_excerpt: str,
    result_url: str,
    observed_at: datetime,
) -> tuple[DiscoveredContact | None, str | None, str | None]:
    if not result_title or not result_excerpt:
        return None, "missing_result_evidence", None
    normalized = normalize_profile_url(result_url)
    if normalized is None:
        return None, "invalid_profile_url", None
    normalized_url, profile_source = normalized

    name, current_title = _extract_name_and_title(
        raw_title=result_title,
        snippet=result_excerpt,
        source=profile_source,  # type: ignore[arg-type]
        role=role,
    )
    if not name or not _looks_like_person_name(name):
        return None, "invalid_person_name", normalized_url
    if len(name) > MAX_PUBLIC_NAME_CHARS:
        return None, "person_name_too_long", normalized_url
    if not current_title:
        return None, "missing_current_title", normalized_url
    if _former_target_employer(role.company, f"{result_title} {result_excerpt}"):
        return None, "former_target_employer", normalized_url
    if _title_points_to_other_company(current_title, role.company) or _has_conflicting_employer(
        role.company,
        f"{result_title}. {result_excerpt}",
    ):
        return None, "conflicting_current_employer", normalized_url
    if not _current_company_signal(
        raw_title=result_title,
        snippet=result_excerpt,
        company=role.company,
    ):
        return None, "current_employer_unverified", normalized_url

    current_title = _display_title(current_title)
    if not current_title or not _appropriate_title(current_title, role.title):
        return None, "role_relevance_unverified", normalized_url
    if len(current_title) > MAX_CURRENT_TITLE_CHARS:
        return None, "current_title_too_long", normalized_url

    confidence = _contact_confidence(result.confidence, profile_source)
    if confidence is None:
        return None, "invalid_confidence", normalized_url

    legacy_category = _contact_category(current_title)
    category = {
        "peer": ContactCategory.team_peer,
        "leader": ContactCategory.team_leader,
        "recruiter": ContactCategory.recruiter,
    }[legacy_category]
    evidence_url = urlsplit(result_url)._replace(fragment="").geturl()
    evidence = ContactEvidence(
        provider=provider_name,
        query=query_spec.query,
        query_category=query_spec.category,
        result_position=position,
        result_title=result_title,
        result_excerpt=result_excerpt,
        result_url=evidence_url,
        observed_at=observed_at,
    )
    score_components = _score_components(
        confidence=confidence,
        profile_source=profile_source,
        category=category,
        query_category=query_spec.category,
        current_title=current_title,
        role_title=role.title,
    )
    why_relevant = _why_relevant(
        public_name=name,
        current_title=current_title,
        company=role.company,
        role_title=role.title,
        category=category,
    )
    return (
        DiscoveredContact(
            public_name=name,
            current_title=current_title,
            current_company=role.company,
            profile_url=normalized_url,
            normalized_profile_url=normalized_url,
            profile_source=profile_source,
            category=category,
            verified_current_employer=True,
            confidence=confidence,
            why_relevant=why_relevant,
            score_total=min(1_000, sum(score_components.values())),
            score_components=score_components,
            evidence=(evidence,),
        ),
        None,
        normalized_url,
    )


def _coerce_provider_result(value: object) -> ProviderSearchResult | None:
    if isinstance(value, ProviderSearchResult):
        return value
    if not isinstance(value, Mapping):
        return None
    title = value.get("result_title", value.get("title"))
    url = value.get("result_url", value.get("url", value.get("link")))
    excerpt = value.get("result_excerpt", value.get("excerpt", value.get("snippet", "")))
    if not isinstance(title, str) or not isinstance(url, str) or not isinstance(excerpt, str):
        return None
    position = value.get("result_position", value.get("position"))
    if not isinstance(position, int) or isinstance(position, bool):
        position = None
    raw_observed_at = value.get("observed_at")
    parsed_observed_at = _parse_datetime(raw_observed_at)
    confidence = value.get("confidence")
    if confidence is not None and (
        isinstance(confidence, bool) or not isinstance(confidence, (int, float))
    ):
        confidence = -1.0
    return ProviderSearchResult(
        result_title=title,
        result_url=url,
        result_excerpt=excerpt,
        result_position=position,
        observed_at=parsed_observed_at,
        confidence=float(confidence) if confidence is not None else None,
    )


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _coerce_observed_at(value: datetime | None, *, fallback: datetime) -> datetime:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return fallback
    return _aware_utc(value)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _result_position(value: int | None, fallback: int) -> int:
    if value is None or isinstance(value, bool) or value < 1:
        return fallback
    return value


def _bounded_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()[:MAX_RESULT_TEXT_CHARS]


def _query_phrase(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\\", " ").replace('"', " ")).strip()[:200]


def _provider_name(provider: ContactSearchProvider) -> str:
    value = getattr(provider, "name", "")
    if not isinstance(value, str) or not value.strip():
        value = type(provider).__name__
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())[:64]
    return cleaned or "contact_search_provider"


def _contact_confidence(value: float | None, source: str) -> float | None:
    if value is None:
        return 0.9 if source == "linkedin" else 0.8
    if isinstance(value, bool) or not 0.0 <= value <= 1.0:
        return None
    return round(float(value), 4)


def _former_target_employer(company: str, text: str) -> bool:
    normalized = _normalize_match_text(text)
    former = r"(?:former|formerly|previously|ex|alum|alumnus|alumni)"
    for alias in _company_aliases(company):
        if re.search(rf"\b{former}\b.{{0,40}}\b{re.escape(alias)}\b", normalized):
            return True
        if re.search(rf"\b{re.escape(alias)}\b.{{0,40}}\b{former}\b", normalized):
            return True
    return False


_CURRENT_EMPLOYER_PATTERN = re.compile(
    r"\b(?:engineer|engineering manager|manager|recruiter|developer|director|"
    r"architect|sourcer|lead|head)\s+(?:at|@)\s+"
    r"(?P<employer>[^.;|]{2,80}?)"
    r"(?=\s+(?:where|working|building|leading|focused|specializing)\b|[.;|]|$)",
    flags=re.I,
)


def _has_conflicting_employer(company: str, text: str) -> bool:
    for match in _CURRENT_EMPLOYER_PATTERN.finditer(text):
        prefix = _normalize_match_text(text[max(0, match.start() - 45) : match.start()])
        if re.search(r"\b(?:former|formerly|previously|ex)\b", prefix):
            continue
        employer = match.group("employer")
        if not _company_matches(company, employer):
            return True
    return False


_GENERIC_LEVEL_TERMS = {
    "associate",
    "chief",
    "head",
    "i",
    "ii",
    "iii",
    "iv",
    "junior",
    "lead",
    "manager",
    "principal",
    "senior",
    "sr",
    "staff",
}
_TECHNICAL_FAMILY = {
    "architect",
    "backend",
    "cloud",
    "data",
    "developer",
    "devops",
    "engineer",
    "engineering",
    "frontend",
    "identity",
    "infrastructure",
    "platform",
    "product",
    "security",
    "software",
}


def _appropriate_title(candidate_title: str, role_title: str) -> bool:
    candidate_terms = set(_normalize_match_text(candidate_title).split())
    if candidate_terms & {"recruiter", "sourcer", "talent"}:
        return True
    role_terms = set(_normalize_match_text(role_title).split()) - _GENERIC_LEVEL_TERMS
    meaningful_candidate = candidate_terms - _GENERIC_LEVEL_TERMS
    if meaningful_candidate & role_terms:
        return True
    return bool(candidate_terms & _TECHNICAL_FAMILY and role_terms & _TECHNICAL_FAMILY)


def _score_components(
    *,
    confidence: float,
    profile_source: str,
    category: ContactCategory,
    query_category: DiscoveryCategory,
    current_title: str,
    role_title: str,
) -> dict[str, int]:
    aligned_category = {
        ContactCategory.team_peer: DiscoveryCategory.peer,
        ContactCategory.team_leader: DiscoveryCategory.leader,
        ContactCategory.recruiter: DiscoveryCategory.recruiter,
    }[category]
    return {
        "confidence": round(confidence * 650),
        "source_quality": 90 if profile_source == "linkedin" else 60,
        "role_relevance": 140 if _title_overlaps_role(current_title, role_title) else 80,
        "category_alignment": 55 if aligned_category is query_category else 20,
        "evidence_quality": 45,
        "bench_utility": 20 if category is ContactCategory.team_peer else 30,
    }


def _why_relevant(
    *,
    public_name: str,
    current_title: str,
    company: str,
    role_title: str,
    category: ContactCategory,
) -> str:
    del public_name  # The explanation should be reusable and evidence-focused.
    if category is ContactCategory.recruiter:
        return (
            f"Their current {current_title} role at {company} can help route interest "
            f"in the {role_title} opening."
        )
    if category is ContactCategory.team_leader:
        return (
            f"Their current {current_title} role at {company} places them near the "
            f"hiring context for the {role_title} opening."
        )
    return (
        f"Their current {current_title} role at {company} is a relevant peer signal "
        f"for the {role_title} opening."
    )


def _evidence_sort_key(evidence: ContactEvidence) -> tuple[int, int, str, str]:
    category_order = {
        DiscoveryCategory.peer: 0,
        DiscoveryCategory.leader: 1,
        DiscoveryCategory.recruiter: 2,
    }
    return (
        category_order[evidence.query_category],
        evidence.result_position,
        evidence.query,
        evidence.result_url,
    )


def _candidate_sort_key(contact: DiscoveredContact) -> tuple[int, float, str, str, str]:
    return (
        -contact.score_total,
        -contact.confidence,
        contact.normalized_profile_url,
        contact.public_name.casefold(),
        contact.current_title.casefold(),
    )


def _eligible_for_bench(contact: DiscoveredContact, *, confidence_floor: float) -> bool:
    normalized = normalize_profile_url(contact.normalized_profile_url)
    return bool(
        contact.verified_current_employer
        and contact.confidence >= confidence_floor
        and contact.public_name.strip()
        and contact.current_title.strip()
        and contact.current_company.strip()
        and contact.why_relevant.strip()
        and contact.evidence
        and normalized is not None
        and normalized[0] == contact.normalized_profile_url
        and contact.profile_url == contact.normalized_profile_url
        and all(
            evidence.provider.strip()
            and evidence.query.strip()
            and evidence.result_excerpt.strip()
            and evidence.result_url.strip()
            and normalize_profile_url(evidence.result_url) is not None
            and evidence.observed_at.tzinfo is not None
            and evidence.observed_at.utcoffset() is not None
            for evidence in contact.evidence
        )
    )


def _discovery_diagnostics(
    *,
    candidate_count: int,
    candidate_limit: int,
    queries_attempted: int,
    queries_succeeded: int,
    results_observed: int,
    all_pages_exhausted: bool,
    rejection_counts: Counter[str],
    provider_failures: Sequence[ProviderFailureDiagnostic],
) -> DiscoveryDiagnostics:
    queries_failed = queries_attempted - queries_succeeded
    configuration_only = bool(provider_failures) and all(
        failure.code == "provider_configuration_failure" for failure in provider_failures
    )
    retryable = any(failure.retryable for failure in provider_failures)
    candidate_limit_reached = candidate_count >= candidate_limit
    exhausted = (
        not provider_failures
        and all_pages_exhausted
        and not candidate_limit_reached
        and queries_succeeded == queries_attempted
    )

    if configuration_only and queries_succeeded == 0:
        outcome = DiscoveryOutcome.configuration_failure
    elif provider_failures and queries_succeeded == 0:
        outcome = DiscoveryOutcome.provider_failure
    elif provider_failures:
        outcome = DiscoveryOutcome.partial_provider_failure
    elif candidate_limit_reached:
        outcome = DiscoveryOutcome.candidate_limit_reached
    elif exhausted:
        outcome = DiscoveryOutcome.exhausted
    else:
        outcome = DiscoveryOutcome.incomplete

    return DiscoveryDiagnostics(
        outcome=outcome,
        exhausted=exhausted,
        retryable=retryable,
        candidate_limit_reached=candidate_limit_reached,
        queries_attempted=queries_attempted,
        queries_succeeded=queries_succeeded,
        queries_failed=queries_failed,
        results_observed=results_observed,
        accepted_count=candidate_count,
        rejected_counts=tuple(
            DiagnosticCount(code=code, count=count)
            for code, count in sorted(rejection_counts.items())
            if count > 0
        ),
        provider_failures=tuple(provider_failures),
    )


_GITHUB_RESERVED_PATHS = {
    "about",
    "apps",
    "collections",
    "enterprise",
    "events",
    "explore",
    "features",
    "gist",
    "login",
    "marketplace",
    "new",
    "orgs",
    "organizations",
    "pricing",
    "search",
    "settings",
    "sponsors",
    "topics",
}


__all__ = [
    "BenchCoverageStatus",
    "ContactBenchSelection",
    "ContactCategory",
    "ContactDiscoveryResult",
    "ContactEvidence",
    "ContactProviderConfigurationError",
    "ContactProviderError",
    "ContactSearchProvider",
    "ContactShortfallReason",
    "DEFAULT_CANDIDATE_LIMIT",
    "DEFAULT_CONFIDENCE_FLOOR",
    "DEFAULT_TARGET_COUNT",
    "DiagnosticCount",
    "DiscoveredContact",
    "DiscoveryCategory",
    "DiscoveryDiagnostics",
    "DiscoveryOutcome",
    "DiscoveryQuery",
    "ProviderFailureDiagnostic",
    "ProviderSearchPage",
    "ProviderSearchResult",
    "RejectedContactResult",
    "build_contact_queries",
    "discover_contacts",
    "normalize_profile_url",
    "select_contact_bench",
]
