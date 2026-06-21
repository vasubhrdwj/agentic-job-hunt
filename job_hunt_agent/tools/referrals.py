"""SerpAPI-backed referral discovery tool."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from ..schemas import Person, Role
from .job_search import (
    _fetch_serpapi_search,
    _get_serpapi_api_key,
    _iter_serpapi_results,
    _load_dotenv_if_available,
)


LOGGER = logging.getLogger(__name__)

DEFAULT_NUM_RESULTS_PER_QUERY = 10
DEFAULT_MAX_QUERIES = 8
DEFAULT_TARGET_COUNT = 3

ENGINEERING_TERMS = {
    "architect",
    "backend",
    "cloud",
    "data",
    "developer",
    "devops",
    "engineering",
    "engineer",
    "frontend",
    "fullstack",
    "iam",
    "identity",
    "infrastructure",
    "integrations",
    "manager",
    "oidc",
    "oauth",
    "okta",
    "platform",
    "principal",
    "sailpoint",
    "saml",
    "scim",
    "security",
    "senior",
    "software",
    "staff",
}

TITLE_TERMS = {
    "analyst",
    "architect",
    "developer",
    "director",
    "engineer",
    "engineering",
    "head",
    "lead",
    "manager",
    "officer",
    "principal",
    "recruiter",
    "security",
    "sourcer",
    "staff",
    "talent",
}

GENERIC_ROLE_TERMS = {
    "i",
    "ii",
    "iii",
    "iv",
    "and",
    "remote",
    "associate",
    "engineer",
    "engineering",
    "senior",
    "software",
    "staff",
    "sr",
}

NON_PERSON_NAME_TERMS = {
    "about",
    "careers",
    "company",
    "directory",
    "hiring",
    "jobs",
    "linkedin",
    "login",
    "people",
    "profile",
    "profiles",
    "search",
}

GITHUB_NON_PROFILE_PATHS = {
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


@dataclass(frozen=True)
class ReferralCandidate:
    person: Person
    score: int
    query: str
    company_visible: bool
    low_confidence: bool = False


def find_referrals(role: Role) -> list[Person]:
    """Return plausible referral candidates for a role.

    The tool only returns profiles discovered by search results. If search
    produces nothing usable, it returns [] instead of manufacturing contacts.
    """
    _load_dotenv_if_available()
    role = Role.model_validate(role)

    api_key = _get_serpapi_api_key()
    if not api_key:
        LOGGER.warning("SERPAPI_API_KEY or SERPAPI_KEY is missing; returning no referral targets.")
        return []

    candidates: list[ReferralCandidate] = []
    seen_urls: set[str] = set()

    for query in _build_queries(role)[:DEFAULT_MAX_QUERIES]:
        payload = _fetch_serpapi_search(
            query=query,
            api_key=api_key,
            num_results=DEFAULT_NUM_RESULTS_PER_QUERY,
        )
        if not payload:
            continue

        for item in _iter_serpapi_results(payload):
            parsed = _candidate_from_result(item, role=role, query=query)
            if parsed is None:
                continue

            url_key = parsed.person.profile_url.lower().rstrip("/")
            if url_key in seen_urls:
                continue
            seen_urls.add(url_key)

            candidates.append(parsed)

        if _has_enough_high_confidence_candidates(candidates):
            break

    people = _choose_people(candidates)
    if not people:
        LOGGER.warning(
            "No verified current employees found for %s - %s. "
            "Try the company team page or a direct LinkedIn company-employee search.",
            role.company,
            role.title,
        )
    return people


def _build_queries(role: Role) -> list[str]:
    keywords = _keywords_from_role(role)
    queries: list[str] = []

    for keyword in keywords[:4]:
        queries.append(f'site:linkedin.com/in "{role.company}" "{keyword}"')
        queries.append(f'site:linkedin.com/in "{role.company}" "{keyword}" "engineer"')

    queries.append(f'site:linkedin.com/in "{role.company}" "engineering manager"')
    queries.append(f'site:github.com "{role.company}" "{keywords[0]}" "engineer"')

    return _dedupe_strings(queries)


def _keywords_from_role(role: Role) -> list[str]:
    haystack = _normalize_match_text(
        f"{role.title} {role.summary} {role.match_reason}"
    )
    tokens = haystack.split()

    keywords: list[str] = []
    title_focus = _title_focus(role.title)
    if title_focus:
        keywords.append(title_focus)

    for token in tokens:
        if token in ENGINEERING_TERMS and token not in GENERIC_ROLE_TERMS:
            keywords.append(token)

    if "identity" in tokens and "access" in tokens:
        keywords.append("identity access")
    if "security" in tokens and "engineer" in tokens:
        keywords.append("security engineer")
    if "backend" in tokens and "engineer" in tokens:
        keywords.append("backend engineer")

    if not keywords:
        keywords.append("engineering")

    return _dedupe_strings(keywords)


def _title_focus(title: str) -> str:
    normalized = _normalize_match_text(title)
    tokens = [
        token for token in normalized.split()
        if token not in GENERIC_ROLE_TERMS and len(token) > 1
    ]
    if not tokens:
        return ""
    return " ".join(tokens[:3])


def _candidate_from_result(
    item: dict[str, Any],
    *,
    role: Role,
    query: str,
) -> ReferralCandidate | None:
    raw_url = str(item.get("link") or "").strip()
    raw_title = _clean_text(str(item.get("title") or ""))
    snippet = _clean_text(str(item.get("snippet") or ""))
    source = _source_from_url(raw_url)
    if source is None or not raw_title:
        return None

    profile_url = _normalize_profile_url(raw_url, source)
    if profile_url is None:
        return None

    name, title = _extract_name_and_title(
        raw_title=raw_title,
        snippet=snippet,
        source=source,
        role=role,
    )
    if not name or not _looks_like_person_name(name):
        return None

    evidence_text = f"{raw_title} {snippet} {profile_url}"
    company_visible = _company_matches(role.company, evidence_text)
    role_signal = _best_role_signal(role, evidence_text)
    current_company_signal = _current_company_signal(
        raw_title=raw_title,
        snippet=snippet,
        company=role.company,
    )

    if not title:
        return None
    title_points_elsewhere = _title_points_to_other_company(title, role.company)
    display_title = _display_title(title)
    if title_points_elsewhere or not current_company_signal or not display_title:
        return None

    person_source: Literal["linkedin", "github", "company_page", "other"]
    person_source = source
    confidence = 0.9 if source == "linkedin" else 0.75
    person = Person(
        name=name,
        title=display_title,
        company=role.company,
        profile_url=profile_url,
        source=person_source,
        why_relevant=_build_why_relevant(
            role=role,
            title=display_title,
            role_signal=role_signal,
            company_visible=current_company_signal,
            low_confidence=False,
        ),
        verified_current_employer=True,
        confidence=confidence,
    )

    return ReferralCandidate(
        person=person,
        score=_score_candidate(
            source=source,
            title=display_title,
            role=role,
            evidence_text=evidence_text,
            company_visible=current_company_signal,
            low_confidence=False,
        ),
        query=query,
        company_visible=current_company_signal,
        low_confidence=False,
    )


def _source_from_url(url: str) -> Literal["linkedin", "github"] | None:
    if "\\" in url or any(character.isspace() for character in url):
        return None
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        return None
    hostname = parsed.hostname.casefold()
    if hostname == "linkedin.com" or hostname.endswith(".linkedin.com"):
        return "linkedin"
    if hostname == "github.com" or hostname.endswith(".github.com"):
        return "github"
    return None


def _normalize_profile_url(url: str, source: Literal["linkedin", "github"]) -> str | None:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").casefold()
    path = parsed.path.rstrip("/")

    if source == "linkedin":
        if not path.lower().startswith("/in/"):
            return None
        slug = path.split("/", 2)[-1]
        if not slug or slug.lower() in {"jobs", "people", "pub"}:
            return None
        return f"https://{hostname}{path}"

    path_parts = [part for part in path.split("/") if part]
    if len(path_parts) != 1:
        return None
    username = path_parts[0]
    if username.lower() in GITHUB_NON_PROFILE_PATHS:
        return None
    return f"https://{hostname}/{username}"


def _extract_name_and_title(
    *,
    raw_title: str,
    snippet: str,
    source: Literal["linkedin", "github"],
    role: Role,
) -> tuple[str, str]:
    cleaned_title = _strip_search_suffix(raw_title, source)
    parts = _split_title_parts(cleaned_title)

    name = parts[0] if parts else cleaned_title
    title = ""

    if source == "linkedin":
        title = _title_from_linkedin_parts(parts, role.company)
        if not title:
            title = _title_from_snippet(snippet, role.company)
    else:
        title = _title_from_snippet(snippet, role.company)
        if not title:
            title = _github_title_from_parts(parts)

    name = _clean_person_name(name)
    title = _clean_person_title(title)
    title = _remove_name_prefix_from_title(title, name)
    return name, title


def _strip_search_suffix(raw_title: str, source: Literal["linkedin", "github"]) -> str:
    if source == "linkedin":
        return re.sub(r"\s*\|\s*LinkedIn\s*$", "", raw_title, flags=re.I).strip()
    return re.sub(r"\s*[-|]\s*GitHub\s*$", "", raw_title, flags=re.I).strip()


def _split_title_parts(value: str) -> list[str]:
    normalized = (
        value.replace("\u2013", " - ")
        .replace("\u2014", " - ")
        .replace("\u00b7", " - ")
        .replace("|", " - ")
    )
    return [_clean_text(part) for part in normalized.split(" - ") if part.strip()]


def _title_from_linkedin_parts(parts: list[str], company: str) -> str:
    if len(parts) < 2:
        return ""

    for part in parts[1:]:
        if _company_matches(company, part):
            continue
        if _looks_like_title(part):
            return part
    return ""


def _title_from_snippet(snippet: str, company: str) -> str:
    if not snippet:
        return ""

    company_pattern = re.escape(company)
    patterns = [
        rf"(?P<title>[A-Z][^.;|]{{2,90}}?)\s+at\s+{company_pattern}",
        rf"(?P<title>[A-Z][^.;|]{{2,90}}?)\s+-\s+{company_pattern}",
        rf"Current(?:ly)?:\s*(?P<title>[^.;|]{{2,90}}?)\s+at\s+{company_pattern}",
    ]
    for pattern in patterns:
        match = re.search(pattern, snippet, flags=re.I)
        if match:
            title = _clean_person_title(match.group("title"))
            if _looks_like_title(title):
                return title

    parts = _split_title_parts(snippet)
    for index, part in enumerate(parts):
        if _company_matches(company, part) and index > 0:
            possible_title = _clean_person_title(parts[index - 1])
            if _looks_like_title(possible_title):
                return possible_title

    return ""


def _github_title_from_parts(parts: list[str]) -> str:
    for part in parts[1:]:
        if _looks_like_title(part):
            return part
    return ""


def _looks_like_person_name(value: str) -> bool:
    normalized = _normalize_match_text(value)
    if not normalized:
        return False
    if any(term in normalized.split() for term in NON_PERSON_NAME_TERMS):
        return False

    words = normalized.split()
    if len(words) < 2 or len(words) > 5:
        return False
    return all(any(character.isalpha() for character in word) for word in words)


def _looks_like_title(value: str) -> bool:
    normalized = _normalize_match_text(value)
    if not normalized:
        return False
    return any(term in normalized.split() for term in TITLE_TERMS)


def _score_candidate(
    *,
    source: Literal["linkedin", "github"],
    title: str,
    role: Role,
    evidence_text: str,
    company_visible: bool,
    low_confidence: bool,
) -> int:
    score = 60 if source == "linkedin" else 35
    if company_visible:
        score += 35
    if _title_mentions_engineering(title):
        score += 25
    if _title_mentions_hiring_context(title):
        score += 20
    if _title_overlaps_role(title, role.title):
        score += 15
    if _best_role_signal(role, evidence_text):
        score += 15
    if _normalize_match_text(title) in {"technical recruiter", "recruiter", "sourcer"}:
        score -= 10
    if low_confidence:
        score -= 45
    return score


def _title_mentions_engineering(title: str) -> bool:
    normalized = _normalize_match_text(title)
    engineering_terms = {"architect", "developer", "engineer", "engineering", "principal", "staff"}
    return any(term in normalized.split() for term in engineering_terms)


def _title_mentions_hiring_context(title: str) -> bool:
    normalized = _normalize_match_text(title)
    return any(term in normalized.split() for term in {"lead", "manager", "principal", "staff"})


def _title_overlaps_role(person_title: str, role_title: str) -> bool:
    person_terms = {
        token for token in _normalize_match_text(person_title).split()
        if token not in GENERIC_ROLE_TERMS
    }
    role_terms = {
        token for token in _normalize_match_text(role_title).split()
        if token not in GENERIC_ROLE_TERMS
    }
    return bool(person_terms & role_terms)


def _best_role_signal(role: Role, evidence_text: str) -> str:
    haystack = _normalize_match_text(evidence_text)
    for keyword in _keywords_from_role(role):
        if _normalize_match_text(keyword) in haystack:
            return keyword
    return ""


def _build_why_relevant(
    *,
    role: Role,
    title: str,
    role_signal: str,
    company_visible: bool,
    low_confidence: bool,
) -> str:
    if low_confidence:
        return (
            f"Lower-confidence profile result for {role.company}; verify manually, "
            f"but the search result connects this person to the {role.title} target."
        )

    if role_signal:
        return (
            f"Their title ({title}) is close to the {role.title} role, and the "
            f"search result also surfaced the {role_signal} signal."
        )

    if company_visible:
        return (
            f"Their title ({title}) suggests they are near the hiring team for "
            f"{role.company}'s {role.title} role."
        )

    return (
        f"Their profile appeared for the {role.company} referral search tied to "
        f"the {role.title} role."
    )


def _choose_people(
    candidates: list[ReferralCandidate],
) -> list[Person]:
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            candidate.score,
            candidate.company_visible,
            candidate.person.source == "linkedin",
        ),
        reverse=True,
    )
    return [candidate.person for candidate in ranked[:DEFAULT_TARGET_COUNT]]


def _current_company_signal(*, raw_title: str, snippet: str, company: str) -> bool:
    normalized_snippet = _normalize_match_text(snippet)
    for alias in _company_aliases(company):
        if re.search(
            rf"\b(?:former|formerly|previously|ex)\b.{{0,30}}\b{re.escape(alias)}\b",
            normalized_snippet,
        ):
            return False
    if _company_matches(company, raw_title):
        return True

    haystack = normalized_snippet
    for alias in _company_aliases(company):
        if f" at {alias}" in haystack:
            return True
        if f" experience {alias}" in haystack or f" current {alias}" in haystack:
            return True
    return False


def _has_enough_high_confidence_candidates(candidates: list[ReferralCandidate]) -> bool:
    high_confidence_count = sum(
        1 for candidate in candidates
        if not candidate.low_confidence and candidate.company_visible and candidate.score >= 110
    )
    return high_confidence_count >= DEFAULT_TARGET_COUNT


def _company_matches(company: str, text: str) -> bool:
    haystack = _normalize_match_text(text)
    for alias in _company_aliases(company):
        if re.search(rf"\b{re.escape(alias)}\b", haystack):
            return True
    return False


def _company_aliases(company: str) -> set[str]:
    normalized = _normalize_match_text(company)
    if not normalized:
        return set()

    aliases = {normalized}
    suffixes = {"inc", "ltd", "llc", "pvt", "private", "technologies", "technology", "corp", "corporation"}
    tokens = [token for token in normalized.split() if token not in suffixes]
    if tokens:
        aliases.add(" ".join(tokens))
    if len(tokens) >= 2:
        aliases.add(" ".join(tokens[:2]))
    return {alias for alias in aliases if alias}


def _clean_person_name(value: str) -> str:
    value = re.sub(r"\s*\([^)]*\)\s*$", "", value)
    value = re.sub(r"\s+", " ", value).strip(" -,:")
    return value


def _clean_person_title(value: str) -> str:
    value = value.replace("\u2026", "...")
    value = re.sub(r"\s+at\s+.+$", "", value, flags=re.I)
    value = re.sub(r"^(?:i\s+am\s+currently|currently|i'm)\s+(?:an?\s+)?", "", value, flags=re.I)
    value = re.sub(r"^as\s+(?:an?\s+)?", "", value, flags=re.I)
    value = re.split(r"\s+(?:on|in|for)\s+the\s+", value, maxsplit=1, flags=re.I)[0]
    value = re.sub(r"\s+", " ", value).strip(" -,:")
    for separator in (",", "..."):
        prefix = value.split(separator, 1)[0].strip(" -,:")
        if prefix and _looks_like_title(prefix):
            value = prefix
            break
    value = re.sub(r"^(?:an?|the)\s+", "", value, flags=re.I)
    return value


def _title_points_to_other_company(title: str, company: str) -> bool:
    match = re.search(r"\s+@\s*(?P<company>[^,;|()]{2,80})$", title)
    if not match:
        return False
    mentioned_company = match.group("company")
    return not _company_matches(company, mentioned_company)


def _display_title(title: str) -> str:
    return re.sub(r"\s+@\s*[^,;|()]{2,80}$", "", title).strip(" -,:")


def _remove_name_prefix_from_title(title: str, name: str) -> str:
    name_words = _clean_person_name(name).split()
    if not title or not name_words:
        return title

    prefixes = {
        name_words[0],
        name_words[-1],
        " ".join(name_words),
    }
    cleaned = title
    for prefix in sorted(prefixes, key=len, reverse=True):
        cleaned = re.sub(
            rf"^{re.escape(prefix)}\s*(?:[-:|]|\u00b7)+\s*",
            "",
            cleaned,
            flags=re.I,
        )
    cleaned = cleaned.strip(" -,:")
    cleaned = re.sub(r"^as\s+(?:an?\s+)?", "", cleaned, flags=re.I)
    return re.sub(r"^(?:an?|the)\s+", "", cleaned, flags=re.I)


def _clean_text(value: str) -> str:
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _normalize_match_text(value: str) -> str:
    value = value.lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped
