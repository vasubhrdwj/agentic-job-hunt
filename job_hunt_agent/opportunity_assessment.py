"""Deterministic, provider-free opportunity assessment.

The assessment deliberately returns bands and inspectable evidence instead of an
opaque percentage. Missing facts stay unknown, and the result never dismisses or
otherwise mutates an opportunity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


ASSESSMENT_ALGORITHM_VERSION = "backend-opportunity-fit-v2"
FitBand = Literal["strong", "promising", "stretch", "low", "insufficient_data"]
AssessmentConfidence = Literal["high", "medium", "low"]
EligibilityState = Literal["eligible", "uncertain", "likely_ineligible"]


@dataclass(frozen=True)
class AssessmentPosting:
    title: str
    description: str
    location: str | None = None
    employment_type: str | None = None


@dataclass(frozen=True)
class AssessmentTarget:
    role_families: tuple[str, ...]
    seniority_levels: tuple[str, ...]
    target_locations: tuple[str, ...]


@dataclass(frozen=True)
class AssessmentProfile:
    current_location: str | None = None
    work_modes: tuple[str, ...] = ()
    employment_types: tuple[str, ...] = ()
    years_of_experience: float | None = None
    work_authorizations: tuple["AssessmentAuthorization", ...] = ()


@dataclass(frozen=True)
class AssessmentAuthorization:
    country_code: str
    status: str


@dataclass(frozen=True)
class AssessmentEvidence:
    id: str
    statement: str
    skills: tuple[str, ...] = ()


@dataclass(frozen=True)
class OpportunityAssessment:
    algorithm_version: str
    fit_band: FitBand
    confidence: AssessmentConfidence
    eligibility: EligibilityState
    matched_terms: tuple[str, ...]
    representative_requirement: str | None
    approved_evidence_ids: tuple[str, ...]
    strengths: tuple[str, ...]
    gaps: tuple[str, ...]


@dataclass(frozen=True)
class _ExperienceRequirement:
    minimum_years: int
    text: str


_SKILL_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Node.js", (r"\bnode[.]?js\b",)),
    ("TypeScript", (r"\btypescript\b",)),
    ("JavaScript", (r"\bjavascript\b",)),
    ("Python", (r"\bpython\b",)),
    ("Go", (r"\bgolang\b", r"\bGo\b")),
    ("Java", (r"\bjava\b",)),
    ("FastAPI", (r"\bfastapi\b",)),
    ("Express", (r"\bexpress[.]?js\b", r"\bexpress framework\b")),
    ("Spring", (r"\bspring boot\b", r"\bspring framework\b")),
    ("AWS", (r"\baws\b", r"amazon web services")),
    ("Lambda", (r"\blambda\b",)),
    ("ECS", (r"\becs\b",)),
    ("ALB", (r"\balb\b", r"application load balancer")),
    ("S3", (r"\bs3\b",)),
    ("Kafka", (r"\bkafka\b", r"\bmsk\b")),
    ("Docker", (r"\bdocker\b",)),
    ("Kubernetes", (r"\bkubernetes\b", r"\bk8s\b")),
    ("Terraform", (r"\bterraform\b",)),
    ("Helm", (r"\bhelm\b",)),
    ("nginx", (r"\bnginx\b",)),
    ("Cloudflare", (r"\bcloudflare\b",)),
    ("CI/CD", (r"\bci[/ -]?cd\b", r"continuous integration")),
    ("OAuth", (r"\boauth(?: 2[.]0)?\b",)),
    ("OIDC", (r"\boidc\b", r"open.?id connect")),
    ("SCIM", (r"\bscim\b",)),
    ("JWT", (r"\bjwt\b",)),
    ("REST", (r"\bREST(?:ful)?\b", r"\brest(?:ful)? apis?\b")),
    ("gRPC", (r"\bgrpc\b",)),
    ("GraphQL", (r"\bgraphql\b",)),
    ("PostgreSQL", (r"\bpostgres(?:ql)?\b",)),
    ("MySQL", (r"\bmysql\b",)),
    ("SQL", (r"\bsql\b",)),
    ("Redis", (r"\bredis\b",)),
    ("MongoDB", (r"\bmongodb\b",)),
    ("DynamoDB", (r"\bdynamodb\b",)),
    ("Firestore", (r"\bfirestore\b",)),
    ("microservices", (r"\bmicroservices?\b",)),
    ("distributed systems", (r"\bdistributed systems?\b",)),
    ("event-driven systems", (r"\bevent[ -]driven\b",)),
    ("retries", (r"\bretr(?:y|ies)\b", r"exponential backoff")),
    ("DLQ", (r"\bdlq\b", r"dead.?letter queue")),
    ("observability", (r"\bobservability\b", r"\bopentelemetry\b")),
    ("LLMs", (r"\bllms?\b", r"large language models?")),
    ("PyTorch", (r"\bpytorch\b",)),
    ("QLoRA", (r"\bqlora\b",)),
    ("GRPO", (r"\bgrpo\b",)),
)
_ROLE_GROUP_PATTERNS: dict[str, tuple[str, ...]] = {
    "backend": ("backend", "back-end", "server-side", "api engineer"),
    "software": ("software engineer", "software developer", "sde"),
    "platform": (
        "platform",
        "infrastructure",
        "site reliability",
        "sre",
        "devops",
        "cloud engineer",
    ),
    "frontend": ("frontend", "front-end", "ui engineer", "web designer"),
    "mobile": ("android", "ios", "mobile engineer"),
    "data": ("data engineer", "analytics engineer", "data scientist"),
    "security": ("security engineer", "application security"),
    "product": ("product manager", "product owner", "product management"),
    "business": (
        "administrative",
        "account executive",
        "business partner",
        "recruiter",
        "sales",
        "marketing",
    ),
}
_ADJACENT_ROLE_GROUPS = frozenset({"backend", "software", "platform"})
_SPECIALIST_ROLE_GROUPS = frozenset(
    {"backend", "platform", "frontend", "mobile", "data", "security", "product", "business"}
)
_SENIORITY_ORDER = {"junior": 0, "mid": 1, "senior": 2, "staff": 3}
_SENTENCE_SPLIT = re.compile(r"(?:[\r\n]+|(?<=[.!?]))\s+")
_EXPERIENCE = re.compile(
    r"\b(?P<minimum>\d+)(?:\s*(?:[-–—]|to)\s*(?P<maximum>\d+))?"
    r"\s*[+]?(?:\s+years?|\s+yrs?)\b",
    re.IGNORECASE,
)
_COUNTRY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "IN",
        (
            r"\bindia\b",
            r"\bbengaluru\b",
            r"\bbangalore\b",
            r"\bgurugram\b",
            r"\bgurgaon\b",
            r"\bhyderabad\b",
            r"\bpune\b",
            r"\bmumbai\b",
            r"\bchennai\b",
            r"\bnoida\b",
            r"\bnew delhi\b",
        ),
    ),
    ("US", (r"\bunited states\b", r"\bu[.]?s[.]?a?[.]?\b")),
    ("CA", (r"\bcanada\b",)),
    ("GB", (r"\bunited kingdom\b", r"\bu[.]?k[.]?\b", r"\bbritain\b")),
    ("AU", (r"\baustralia\b",)),
    ("DE", (r"\bgermany\b",)),
    ("SG", (r"\bsingapore\b",)),
)


def assess_opportunity(
    *,
    posting: AssessmentPosting,
    target: AssessmentTarget,
    profile: AssessmentProfile,
    resume_text: str,
    evidence: tuple[AssessmentEvidence, ...],
) -> OpportunityAssessment:
    description = " ".join(posting.description.split())
    candidate_text = " ".join(
        [resume_text, *(item.statement for item in evidence), *(" ".join(item.skills) for item in evidence)]
    )
    jd_skills = _extract_skills(description)
    candidate_skills = _extract_skills(candidate_text)
    matched_skills = jd_skills & candidate_skills

    supporting = sorted(
        (
            (
                index,
                item.id,
                jd_skills & _extract_skills(f"{item.statement} {' '.join(item.skills)}"),
            )
            for index, item in enumerate(evidence)
        ),
        key=lambda item: (-len(item[2]), item[0]),
    )
    evidence_supported_skills: set[str] = set()
    supporting_ids_list: list[str] = []
    for _index, item_id, supported_skills in supporting:
        if supported_skills - evidence_supported_skills:
            supporting_ids_list.append(item_id)
            evidence_supported_skills.update(supported_skills)
    supporting_ids = tuple(supporting_ids_list)

    earned = 0.0
    known = 0.0
    relevance_conflict = False
    eligibility_conflicts: list[str] = []
    eligibility_unknowns: list[str] = []
    gaps: list[str] = []

    role_points, role_known, role_conflict = _role_alignment(posting.title, target.role_families)
    earned += role_points
    known += role_known
    if role_conflict:
        relevance_conflict = True
        gaps.append("The role title is outside your saved target role families.")

    if jd_skills:
        known += 40
        skill_coverage = len(matched_skills) / len(jd_skills)
        earned += 40 * skill_coverage
    else:
        skill_coverage = None

    if jd_skills:
        known += 20
        evidence_target = min(3, len(jd_skills))
        earned += 20 * min(1, len(evidence_supported_skills) / evidence_target)

    employment_points, employment_known, employment_conflict = _employment_alignment(
        posting.employment_type,
        profile.employment_types,
    )
    earned += employment_points
    known += employment_known
    if employment_conflict:
        eligibility_conflicts.append("employment")
        gaps.append("The posting's employment type conflicts with your saved preference.")
    elif employment_known == 0:
        eligibility_unknowns.append("employment")
        gaps.append("The employment type could not be checked against your preference.")

    location_points, location_known, location_conflict, location_unknown, location_gap = _location_alignment(
        posting.location,
        target.target_locations,
        profile.work_modes,
    )
    earned += location_points
    known += location_known
    if location_conflict:
        eligibility_conflicts.append("location")
        gaps.append(location_gap or "The posting location conflicts with your saved target.")
    elif location_unknown:
        eligibility_unknowns.append("location")
        gaps.append(location_gap or "The posting location could not be checked safely.")

    authorization_conflict, authorization_unknown, authorization_gap = _authorization_check(
        posting.location,
        profile.current_location,
        profile.work_authorizations,
    )
    if authorization_conflict:
        eligibility_conflicts.append("authorization")
        gaps.append(authorization_gap or "Your saved work authorization conflicts with this location.")
    elif authorization_unknown:
        eligibility_unknowns.append("authorization")
        gaps.append(authorization_gap or "Work authorization needs verification.")

    seniority_points, seniority_known, above_target = _seniority_alignment(
        posting.title,
        target.seniority_levels,
    )
    earned += seniority_points
    known += seniority_known
    if above_target:
        gaps.append("The title appears more senior than your saved target level.")

    missing_skills = _ordered_missing_skills(description, jd_skills - candidate_skills)
    if missing_skills:
        gaps.append(
            "Not found in your approved résumé/evidence: " + ", ".join(missing_skills[:4]) + "."
        )
    experience_requirement = _experience_requirement(description)
    if experience_requirement:
        if profile.years_of_experience is None:
            eligibility_unknowns.append("experience")
            gaps.append(
                "Verify the stated experience requirement: " + experience_requirement.text
            )
        elif profile.years_of_experience + 0.25 < experience_requirement.minimum_years:
            eligibility_conflicts.append("experience")
            gaps.append(
                f"The posting asks for at least {experience_requirement.minimum_years} years; "
                f"your profile records {profile.years_of_experience:g}."
            )

    eligibility: EligibilityState
    if eligibility_conflicts:
        eligibility = "likely_ineligible"
    elif eligibility_unknowns:
        eligibility = "uncertain"
    else:
        eligibility = "eligible"

    normalized_score = 100 * earned / known if known else 0
    full_description = len(description) >= 400
    signal_count = len(jd_skills) + int(experience_requirement is not None)
    description_sufficient = len(description) >= 200 and signal_count >= 2
    confidence: AssessmentConfidence
    if known >= 80 and full_description and signal_count >= 3:
        confidence = "high"
    elif known >= 50 and description_sufficient:
        confidence = "medium"
    else:
        confidence = "low"
    if eligibility == "uncertain" and confidence == "high":
        confidence = "medium"

    thin_skill_coverage = (
        skill_coverage is not None and len(jd_skills) >= 4 and skill_coverage < 0.4
    )
    if relevance_conflict or eligibility == "likely_ineligible":
        fit_band: FitBand = "low"
    elif not description_sufficient:
        fit_band = "insufficient_data"
    elif above_target or thin_skill_coverage:
        fit_band = "stretch"
    elif (
        normalized_score >= 70
        and known >= 70
        and len(matched_skills) >= 3
        and len(evidence_supported_skills) >= 2
        and eligibility == "eligible"
        and confidence == "high"
    ):
        fit_band = "strong"
    elif normalized_score >= 50 and known >= 50 and confidence != "low":
        fit_band = "promising"
    elif normalized_score >= 30 and confidence != "low":
        fit_band = "stretch"
    elif confidence != "low":
        fit_band = "low"
    else:
        fit_band = "insufficient_data"

    strengths: list[str] = []
    if role_points >= 20:
        strengths.append("The title aligns with your saved target role families.")
    if matched_skills:
        strengths.append(
            "Supported JD skills: " + ", ".join(_ordered_skills(matched_skills)[:6]) + "."
        )
    if supporting_ids:
        strengths.append(
            f"{len(supporting_ids)} approved achievement"
            f"{'s' if len(supporting_ids) != 1 else ''} support the JD skills."
        )

    return OpportunityAssessment(
        algorithm_version=ASSESSMENT_ALGORITHM_VERSION,
        fit_band=fit_band,
        confidence=confidence,
        eligibility=eligibility,
        matched_terms=tuple(_ordered_skills(matched_skills)[:20]),
        representative_requirement=_representative_requirement(description, matched_skills),
        approved_evidence_ids=supporting_ids[:20],
        strengths=tuple(strengths[:3]),
        gaps=tuple(_deduplicate(gaps)[:3]),
    )


def _extract_skills(text: str) -> set[str]:
    skills: set[str] = set()
    for name, patterns in _SKILL_PATTERNS:
        if name == "Go":
            matched = bool(
                re.search(r"\bgolang\b", text, re.IGNORECASE)
                or re.search(r"\bGo\b", text)
            )
        elif name == "REST":
            matched = bool(
                re.search(r"\bREST(?:ful)?\b", text)
                or re.search(r"\brest(?:ful)? apis?\b", text, re.IGNORECASE)
            )
        else:
            matched = any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)
        if matched:
            skills.add(name)
    return skills


def _role_alignment(
    title: str,
    role_families: tuple[str, ...],
) -> tuple[float, float, bool]:
    if not role_families:
        return 0, 0, False
    normalized_title = _normalized(title)
    normalized_families = tuple(_normalized(value) for value in role_families)
    title_groups = _role_groups(normalized_title)
    target_groups = set().union(*(_role_groups(value) for value in normalized_families))
    title_specialists = title_groups & _SPECIALIST_ROLE_GROUPS
    target_specialists = target_groups & _SPECIALIST_ROLE_GROUPS
    if title_specialists and target_specialists and not (
        title_specialists & target_specialists
        or title_specialists <= _ADJACENT_ROLE_GROUPS
        and target_specialists <= _ADJACENT_ROLE_GROUPS
    ):
        return 0, 30, True
    if any(family in normalized_title or normalized_title in family for family in normalized_families):
        return 30, 30, False
    if title_groups & target_groups:
        return 30, 30, False
    if title_groups & _ADJACENT_ROLE_GROUPS and target_groups & _ADJACENT_ROLE_GROUPS:
        return 20, 30, False
    # Provider titles such as "Member of Technical Staff" do not expose a
    # recognizable role family. Treat them as unknown rather than fabricating
    # a conflict; explicit frontend/product/etc. conflicts are handled above.
    return 0, 30, bool(title_groups and target_groups)


def _role_groups(value: str) -> set[str]:
    return {
        group
        for group, patterns in _ROLE_GROUP_PATTERNS.items()
        if any(pattern in value for pattern in patterns)
    }


def _employment_alignment(
    posting_value: str | None,
    preferred: tuple[str, ...],
) -> tuple[float, float, bool]:
    normalized = (posting_value or "").strip().casefold()
    if not normalized or normalized == "unknown" or not preferred:
        return 0, 0, False
    allowed = {value.casefold() for value in preferred}
    return (4, 4, False) if normalized in allowed else (0, 4, True)


def _location_alignment(
    posting_value: str | None,
    target_locations: tuple[str, ...],
    work_modes: tuple[str, ...],
) -> tuple[float, float, bool, bool, str | None]:
    location = _normalized(posting_value or "")
    if not location:
        return 0, 0, False, True, "The posting location was not provided."

    posting_mode = next(
        (mode for mode in ("remote", "hybrid", "onsite") if mode in location),
        "onsite" if "on site" in location else None,
    )
    allowed_modes = {mode.casefold() for mode in work_modes}
    if posting_mode and allowed_modes and posting_mode not in allowed_modes:
        return (
            0,
            4,
            True,
            False,
            f"The posting appears {posting_mode}, outside your saved work modes.",
        )

    posting_country = _country_code(posting_value or "")
    target_countries = {
        code
        for target in target_locations
        if (code := _country_code(target)) is not None
    }
    if posting_country and target_countries:
        if posting_country not in target_countries:
            return 0, 4, True, False, "The posting geography is outside your saved target locations."
        return 4, 4, False, False, None

    if any(
        _contains_phrase(location, _normalized(target))
        or _contains_phrase(_normalized(target), location)
        for target in target_locations
    ):
        return 4, 4, False, False, None
    return 0, 0, False, True, "The posting geography needs verification."


def _authorization_check(
    posting_location: str | None,
    current_location: str | None,
    authorizations: tuple[AssessmentAuthorization, ...],
) -> tuple[bool, bool, str | None]:
    posting_country = _country_code(posting_location or "")
    if posting_country is None:
        return False, False, None
    if posting_country == _country_code(current_location or ""):
        return False, False, None
    authorization = next(
        (
            item
            for item in authorizations
            if item.country_code.strip().upper() == posting_country
        ),
        None,
    )
    if authorization is None:
        return False, True, f"Work authorization for {posting_country} is not recorded."
    status = authorization.status.strip().casefold()
    if status == "not_authorized":
        return True, False, f"Your profile says you are not authorized to work in {posting_country}."
    if status in {"needs_sponsorship", "other"}:
        return False, True, f"Work authorization for {posting_country} needs verification."
    return False, False, None


def _seniority_alignment(
    title: str,
    target_levels: tuple[str, ...],
) -> tuple[float, float, bool]:
    level = _title_seniority(title)
    known_targets = [_SENIORITY_ORDER[item] for item in target_levels if item in _SENIORITY_ORDER]
    if level is None or not known_targets:
        return 0, 0, False
    value = _SENIORITY_ORDER[level]
    if value in known_targets:
        return 2, 2, False
    above_target = value > max(known_targets)
    return 0, 2, above_target


def _title_seniority(title: str) -> str | None:
    value = _normalized(title)
    if any(token in value for token in ("staff", "principal", "lead")):
        return "staff"
    if re.search(r"\b(?:senior|sr|iii|iv)\b", value):
        return "senior"
    if re.search(r"\b(?:junior|jr|associate|graduate|entry|engineer i)\b", value):
        return "junior"
    if re.search(r"\b(?:engineer ii|developer ii)\b", value):
        return "mid"
    return None


def _ordered_missing_skills(description: str, skills: set[str]) -> list[str]:
    required_text = " ".join(
        sentence
        for sentence in _sentences(description)
        if re.search(r"\b(?:must|required|requirements?|qualifications?|looking for)\b", sentence, re.IGNORECASE)
    )
    return sorted(
        skills,
        key=lambda skill: (skill not in _extract_skills(required_text), _skill_index(skill)),
    )


def _ordered_skills(skills: set[str]) -> list[str]:
    return sorted(skills, key=_skill_index)


def _skill_index(skill: str) -> int:
    return next(
        (index for index, (name, _patterns) in enumerate(_SKILL_PATTERNS) if name == skill),
        len(_SKILL_PATTERNS),
    )


def _experience_requirement(description: str) -> _ExperienceRequirement | None:
    for sentence in _sentences(description):
        match = _EXPERIENCE.search(sentence)
        if match and _looks_like_experience_requirement(sentence, match):
            return _ExperienceRequirement(
                minimum_years=int(match.group("minimum")),
                text=_trim_sentence(sentence, 150),
            )
    return None


def _looks_like_experience_requirement(sentence: str, match: re.Match[str]) -> bool:
    if re.search(
        r"\b(?:experience|experienced|minimum|at least|required|requirements?|"
        r"qualifications?|professional|industry)\b",
        sentence,
        re.IGNORECASE,
    ):
        return True
    after_years = sentence[match.end() :]
    return bool(
        re.match(
            r"\s+(?:of\s+)?(?:building|developing|engineering|programming|working|designing)",
            after_years,
            re.IGNORECASE,
        )
    )


def _country_code(value: str) -> str | None:
    return next(
        (
            code
            for code, patterns in _COUNTRY_PATTERNS
            if any(re.search(pattern, value, re.IGNORECASE) for pattern in patterns)
        ),
        None,
    )


def _representative_requirement(
    description: str,
    matched_skills: set[str],
) -> str | None:
    candidates: list[tuple[int, int, str]] = []
    for index, sentence in enumerate(_sentences(description)):
        sentence_skills = _extract_skills(sentence)
        marker = bool(
            re.search(
                r"\b(?:must|required|requirements?|qualifications?|looking for|preferred)\b",
                sentence,
                re.IGNORECASE,
            )
        )
        overlap = len(sentence_skills & matched_skills)
        if marker or overlap:
            candidates.append((overlap + int(marker), -index, sentence))
    if not candidates:
        return None
    return _trim_sentence(max(candidates)[2], 500)


def _sentences(value: str) -> list[str]:
    return [" ".join(item.split()).strip(" -•\t") for item in _SENTENCE_SPLIT.split(value) if item.strip()]


def _trim_sentence(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    shortened = value[: limit - 1].rsplit(" ", 1)[0]
    return f"{shortened}…"


def _normalized(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").split())


def _contains_phrase(value: str, phrase: str) -> bool:
    if not value or not phrase:
        return False
    return bool(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", value))


def _deduplicate(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


__all__ = [
    "ASSESSMENT_ALGORITHM_VERSION",
    "AssessmentEvidence",
    "AssessmentPosting",
    "AssessmentProfile",
    "AssessmentTarget",
    "OpportunityAssessment",
    "assess_opportunity",
]
