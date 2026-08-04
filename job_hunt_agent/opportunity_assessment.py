"""Deterministic, provider-free opportunity assessment.

The assessment deliberately returns bands and inspectable evidence instead of an
opaque percentage. Missing facts stay unknown, and the result never dismisses or
otherwise mutates an opportunity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


ASSESSMENT_ALGORITHM_VERSION = "backend-opportunity-fit-v5"
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
    minimum_years: float
    text: str
    hard: bool


@dataclass(frozen=True)
class _RequirementClause:
    text: str
    soft: bool


@dataclass(frozen=True)
class _SkillRequirements:
    required: frozenset[str]
    optional: frozenset[str]
    alternatives: tuple[frozenset[str], ...]

    @property
    def all_skills(self) -> frozenset[str]:
        return frozenset(
            set(self.required)
            | set(self.optional)
            | set().union(*self.alternatives, set())
        )

    @property
    def required_skills(self) -> frozenset[str]:
        return frozenset(set(self.required) | set().union(*self.alternatives, set()))

    @property
    def unit_count(self) -> int:
        return len(self.required) + len(self.alternatives)


_SKILL_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Node.js", (r"\bnode[.]?js\b",)),
    ("TypeScript", (r"\btypescript\b",)),
    ("JavaScript", (r"\bjavascript\b",)),
    ("Python", (r"\bpython\b",)),
    ("Go", (r"\bgolang\b",)),
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
    "quality": (
        "quality assurance",
        "qa engineer",
        "test automation",
        "automation engineer",
        "software development engineer in test",
        "sdet",
    ),
    "machine_learning": (
        "machine learning",
        "ml engineer",
        "ai engineer",
        "artificial intelligence",
    ),
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
    {
        "backend",
        "platform",
        "frontend",
        "mobile",
        "data",
        "security",
        "quality",
        "machine_learning",
        "product",
        "business",
    }
)
_SENIORITY_ORDER = {"junior": 0, "mid": 1, "senior": 2, "staff": 3}
_SENTENCE_SPLIT = re.compile(r"(?:[\r\n]+|(?<=[.!?]))\s+")
_EXPERIENCE = re.compile(
    r"\b(?P<minimum>\d+(?:[.]\d+)?)"
    r"(?:\s*(?:[-–—]|to)\s*(?P<maximum>\d+(?:[.]\d+)?))?"
    r"\s*[+]?(?:\s+years?|\s+yrs?)\b",
    re.IGNORECASE,
)
_SOFT_REQUIREMENT = re.compile(
    r"\b(?:preferred|optional|ideally|nice to have|bonus|desirable)\b",
    re.IGNORECASE,
)
_SOFT_SECTION = re.compile(
    r"^(?:preferred|optional|nice to have|bonus|desirable|additional)\b",
    re.IGNORECASE,
)
_HARD_SECTION = re.compile(
    r"^(?:requirements?|required|minimum|(?:basic )?qualifications?|must have|"
    r"responsibilities|what you(?: will|'ll) do)\b",
    re.IGNORECASE,
)
_NEGATED_REQUIREMENT = re.compile(
    r"\b(?:not required|no .{0,40}required|"
    r"without requiring|do not need)\b",
    re.IGNORECASE,
)
_HARD_EXPERIENCE = re.compile(
    r"\b(?:required?|requires?|minimum|at least|must|need(?:ed|s)?)\b",
    re.IGNORECASE,
)
_COMPANY_TENURE = re.compile(
    r"\b(?:company|business|organization|founders?|team|we)\b.{0,40}"
    r"\b(?:has|have|been|operat(?:e|ed|ing)|serv(?:e|ed|ing)|exist(?:ed|ing)?)\b|"
    r"\b(?:serving customers|in business)\b",
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
_GO_ORDINARY_FOLLOW = re.compile(
    r"\bGo\b\s+(?:to|live|forward|ahead|beyond|through|home|here|there)\b",
    re.IGNORECASE,
)
_GO_TECH_CONTEXT = re.compile(
    r"(?:\b(?:code(?:base|d)?|program(?:ming)?|language|software|backend|"
    r"services?|microservices?|apis?|skills?|developer|engineer(?:ing)?|experience|"
    r"proficiency|knowledge|using|used|build|built|develop(?:ed|ment)?|"
    r"written|implemented|stack|technolog(?:y|ies)|production)\b"
    r"(?:\W+\w+){0,6}\W+\bGo\b)|"
    r"(?:\bGo\b\W+(?:programming\s+language|language|developer|engineer|"
    r"services?|microservices?|apis?|codebase|runtime|sdk|tooling)\b)|"
    r"(?:\b(?:Python|Java|JavaScript|TypeScript|Rust|C\+\+|C#)\b"
    r"\s*(?:,|/|&|\band\b|\bor\b)\s*\bGo\b)|"
    r"(?:\bGo\b\s*(?:,|/|&|\band\b|\bor\b)\s*"
    r"\b(?:Python|Java|JavaScript|TypeScript|Rust|C\+\+|C#)\b)",
    re.IGNORECASE,
)


def assess_opportunity(
    *,
    posting: AssessmentPosting,
    target: AssessmentTarget,
    profile: AssessmentProfile,
    resume_text: str,
    evidence: tuple[AssessmentEvidence, ...],
) -> OpportunityAssessment:
    description = _normalize_description(posting.description)
    candidate_text = " ".join(
        [
            resume_text,
            *(item.statement for item in evidence),
            *(f"Technical skills: {' '.join(item.skills)}" for item in evidence),
        ]
    )
    requirements = _classify_skill_requirements(description)
    jd_skills = set(requirements.all_skills)
    required_skills = set(requirements.required_skills)
    candidate_skills = _extract_skills(candidate_text)
    matched_skills = jd_skills & candidate_skills
    matched_required_skills = required_skills & candidate_skills
    matched_requirement_units = _matched_requirement_units(requirements, candidate_skills)

    supporting = sorted(
        (
            (
                index,
                item.id,
                required_skills
                & _extract_skills(
                    f"{item.statement} Technical skills: {' '.join(item.skills)}"
                ),
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
    evidence_supported_units = _matched_requirement_units(
        requirements,
        evidence_supported_skills,
    )

    earned = 0.0
    known = 0.0
    relevance_conflict = False
    role_uncertain = False
    eligibility_conflicts: list[str] = []
    eligibility_unknowns: list[str] = []
    critical_gaps: list[str] = []
    gaps: list[str] = []

    (
        role_points,
        role_known,
        role_conflict,
        role_uncertain,
        role_adjacent,
    ) = _role_alignment(
        posting.title,
        target.role_families,
    )
    earned += role_points
    known += role_known
    if role_conflict:
        relevance_conflict = True
        critical_gaps.append("The role title is outside your saved target role families.")
    elif role_adjacent:
        gaps.append(
            "The role title is adjacent to, but not an exact match for, your saved target role families."
        )
    elif role_uncertain:
        gaps.append("The role title needs verification against your saved target role families.")

    if requirements.unit_count:
        known += 40
        skill_coverage = matched_requirement_units / requirements.unit_count
        earned += 40 * skill_coverage
    else:
        skill_coverage = None

    if required_skills:
        known += 20
        evidence_target = min(3, requirements.unit_count)
        earned += 20 * min(1, evidence_supported_units / evidence_target)

    employment_points, employment_known, employment_conflict = _employment_alignment(
        posting.employment_type,
        profile.employment_types,
    )
    earned += employment_points
    known += employment_known
    if employment_conflict:
        eligibility_conflicts.append("employment")
        critical_gaps.append("The posting's employment type conflicts with your saved preference.")
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
        critical_gaps.append(
            location_gap or "The posting location conflicts with your saved target."
        )
    elif location_unknown:
        eligibility_unknowns.append("location")
        gaps.append(location_gap or "The posting location could not be checked safely.")

    authorization_conflict, authorization_unknown, authorization_gap = _authorization_check(
        posting.location,
        profile.current_location,
        profile.work_authorizations,
        target.target_locations,
    )
    if authorization_conflict:
        eligibility_conflicts.append("authorization")
        critical_gaps.append(
            authorization_gap
            or "Your saved work authorization conflicts with this location."
        )
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
        critical_gaps.append("The title appears more senior than your saved target level.")

    missing_skills = _ordered_skills(set(requirements.required) - candidate_skills)
    if missing_skills:
        gaps.append(
            "Not found in your approved résumé/evidence: " + ", ".join(missing_skills[:4]) + "."
        )
    missing_alternatives = [
        group for group in requirements.alternatives if not (group & candidate_skills)
    ]
    if missing_alternatives:
        choices = ", ".join(_ordered_skills(set(missing_alternatives[0])))
        gaps.append(f"Not found in your approved résumé/evidence: one of {choices}.")
    experience_requirement = _experience_requirement(description)
    preferred_experience_gap = False
    if experience_requirement:
        if not experience_requirement.hard:
            if (
                profile.years_of_experience is not None
                and profile.years_of_experience + 0.25
                < experience_requirement.minimum_years
            ):
                preferred_experience_gap = True
                gaps.append(
                    "The posting prefers at least "
                    f"{experience_requirement.minimum_years:g} years; "
                    f"your profile records {profile.years_of_experience:g}."
                )
        elif profile.years_of_experience is None:
            eligibility_unknowns.append("experience")
            gaps.append(
                "Verify the stated experience requirement: " + experience_requirement.text
            )
        elif profile.years_of_experience + 0.25 < experience_requirement.minimum_years:
            eligibility_conflicts.append("experience")
            critical_gaps.append(
                f"The posting asks for at least {experience_requirement.minimum_years:g} years; "
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
    signal_count = requirements.unit_count + int(experience_requirement is not None)
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
        skill_coverage is not None
        and requirements.unit_count >= 4
        and skill_coverage < 0.4
    )
    if relevance_conflict or eligibility == "likely_ineligible":
        fit_band: FitBand = "low"
    elif not description_sufficient:
        fit_band = "insufficient_data"
    else:
        stretch_cap = (
            role_uncertain
            or role_adjacent
            or above_target
            or thin_skill_coverage
            or preferred_experience_gap
        )
        if (
            normalized_score >= 70
            and known >= 70
            and len(matched_required_skills) >= 3
            and evidence_supported_units >= 2
            and eligibility == "eligible"
        ):
            base_band: FitBand = "strong"
        elif normalized_score >= 50 and known >= 50 and confidence != "low":
            base_band = "promising"
        elif normalized_score >= 30 and confidence != "low":
            base_band = "stretch"
        elif confidence != "low":
            base_band = "low"
        else:
            base_band = "insufficient_data"
        fit_band = (
            "stretch"
            if stretch_cap and base_band in {"strong", "promising"}
            else base_band
        )

    strengths: list[str] = []
    if role_points >= 30:
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
        gaps=tuple(_deduplicate([*critical_gaps, *gaps])[:3]),
    )


def _extract_skills(text: str) -> set[str]:
    skills: set[str] = set()
    for name, patterns in _SKILL_PATTERNS:
        if name == "Go":
            matched = _mentions_go_technology(text)
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


def _mentions_go_technology(text: str) -> bool:
    if re.search(r"\bgolang\b", text, re.IGNORECASE):
        return True
    for mention in re.finditer(r"\bGo\b", text):
        window = text[max(0, mention.start() - 96) : mention.end() + 96]
        local_start = mention.start() - max(0, mention.start() - 96)
        if _GO_ORDINARY_FOLLOW.match(window, pos=local_start):
            continue
        if _GO_TECH_CONTEXT.search(window):
            return True
    return False


def _classify_skill_requirements(description: str) -> _SkillRequirements:
    required: set[str] = set()
    optional: set[str] = set()
    alternatives: list[frozenset[str]] = []
    for clause in _requirement_clauses(description):
        skills = _extract_skills(clause.text)
        if not skills or _NEGATED_REQUIREMENT.search(clause.text):
            continue
        if clause.soft:
            optional.update(skills)
            continue
        alternative = _alternative_skill_group(clause.text, skills)
        if alternative is not None:
            alternatives.append(alternative)
            required.update(skills - alternative)
        else:
            required.update(skills)

    normalized_alternatives: list[frozenset[str]] = []
    seen_groups: set[frozenset[str]] = set()
    for group in alternatives:
        remaining = frozenset(group - required)
        if len(remaining) < 2 or remaining in seen_groups:
            continue
        normalized_alternatives.append(remaining)
        seen_groups.add(remaining)
    alternative_skills = set().union(*normalized_alternatives, set())
    optional.difference_update(required | alternative_skills)
    return _SkillRequirements(
        required=frozenset(required),
        optional=frozenset(optional),
        alternatives=tuple(normalized_alternatives),
    )


def _alternative_skill_group(text: str, skills: set[str]) -> frozenset[str] | None:
    if len(skills) < 2:
        return None
    or_matches = list(re.finditer(r"\bor\b", text, re.IGNORECASE))
    if not or_matches:
        return None
    conjunction = or_matches[-1]
    prefix = text[: conjunction.start()]
    start_markers = list(
        re.finditer(
            r"\b(?:either|one of|plus|with|using|in|and)\b|:",
            prefix,
            re.IGNORECASE,
        )
    )
    start = start_markers[-1].end() if start_markers else 0
    suffix = re.split(
        r",\s*(?:plus|and)\b|\b(?:plus|along with|as well as|and)\b|;",
        text[conjunction.end() :],
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    segment = f"{prefix[start:]} or {suffix}"
    alternatives = _extract_skills(segment)
    if len(alternatives) >= 2:
        return frozenset(alternatives)
    return None


def _matched_requirement_units(
    requirements: _SkillRequirements,
    candidate_skills: set[str],
) -> int:
    return len(requirements.required & candidate_skills) + sum(
        bool(group & candidate_skills) for group in requirements.alternatives
    )


def _role_alignment(
    title: str,
    role_families: tuple[str, ...],
) -> tuple[float, float, bool, bool, bool]:
    if not role_families:
        return 0, 0, False, True, False
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
        return 0, 30, True, False, False
    if any(family in normalized_title or normalized_title in family for family in normalized_families):
        return 30, 30, False, False, False
    if title_groups & target_groups:
        return 30, 30, False, False, False
    if title_groups == {"software"} and "backend" in target_groups:
        return 30, 30, False, False, False
    if title_groups & _ADJACENT_ROLE_GROUPS and target_groups & _ADJACENT_ROLE_GROUPS:
        return 20, 30, False, False, True
    # Provider titles such as "Member of Technical Staff" do not expose a
    # recognizable role family. Treat them as unknown rather than fabricating
    # a conflict; explicit frontend/product/etc. conflicts are handled above.
    explicit_conflict = bool(title_groups and target_groups)
    return 0, 30, explicit_conflict, not explicit_conflict, False


def _role_groups(value: str) -> set[str]:
    return {
        group
        for group, patterns in _ROLE_GROUP_PATTERNS.items()
        if any(_contains_phrase(value, _normalized(pattern)) for pattern in patterns)
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

    posting_modes = {
        mode for mode in ("remote", "hybrid", "onsite") if mode in location
    }
    if "on site" in location:
        posting_modes.add("onsite")
    allowed_modes = {mode.casefold() for mode in work_modes}
    if posting_modes and allowed_modes and not (posting_modes & allowed_modes):
        return (
            0,
            4,
            True,
            False,
            "The posting's offered work modes are outside your saved work modes.",
        )
    posting_countries = _country_codes(posting_value or "")
    target_countries = set().union(
        *(_country_codes(target) for target in target_locations),
        set(),
    )
    if posting_countries and target_countries:
        if not (posting_countries & target_countries):
            return 0, 4, True, False, "The posting geography is outside your saved target locations."
        return 4, 4, False, False, None

    if any(
        _contains_phrase(location, _normalized(target))
        for target in target_locations
    ):
        return 4, 4, False, False, None
    return 0, 0, False, True, "The posting geography needs verification."


def _authorization_check(
    posting_location: str | None,
    current_location: str | None,
    authorizations: tuple[AssessmentAuthorization, ...],
    target_locations: tuple[str, ...],
) -> tuple[bool, bool, str | None]:
    posting_countries = _country_codes(posting_location or "")
    if not posting_countries:
        return False, False, None
    target_countries = set().union(
        *(_country_codes(target) for target in target_locations),
        set(),
    )
    compatible_countries = (
        posting_countries & target_countries
        if target_countries
        else posting_countries
    )
    if not compatible_countries:
        # Geography already reports the hard conflict; authorization cannot
        # rescue an option outside the saved target.
        return False, False, None
    authorization_by_country = {
        item.country_code.strip().upper(): item.status.strip().casefold()
        for item in authorizations
    }
    eligible_statuses = {"citizen", "permanent_resident", "work_permit"}
    if any(
        authorization_by_country.get(country) in eligible_statuses
        for country in compatible_countries
    ):
        return False, False, None

    uncertain_countries = sorted(
        country
        for country in compatible_countries
        if authorization_by_country.get(country) in {None, "needs_sponsorship", "other"}
    )
    if uncertain_countries:
        current_countries = _country_codes(current_location or "")
        location_note = (
            " Current location is not treated as proof of authorization."
            if current_countries & set(uncertain_countries)
            else ""
        )
        return (
            False,
            True,
            "Work authorization needs verification for "
            + ", ".join(uncertain_countries)
            + "."
            + location_note,
        )

    countries = ", ".join(sorted(compatible_countries))
    return (
        True,
        False,
        f"Your profile says you are not authorized for the offered location(s): {countries}.",
    )


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
    candidates: list[_ExperienceRequirement] = []
    for clause in _requirement_clauses(description):
        for match in _EXPERIENCE.finditer(clause.text):
            local_context = _local_experience_context(clause.text, match)
            if not _looks_like_experience_requirement(local_context, match=None):
                continue
            candidates.append(
                _ExperienceRequirement(
                    minimum_years=float(match.group("minimum")),
                    text=_trim_sentence(local_context, 150),
                    hard=_experience_is_hard(local_context, clause_soft=clause.soft),
                )
            )
    if not candidates:
        return None
    hard_candidates = [candidate for candidate in candidates if candidate.hard]
    return max(hard_candidates or candidates, key=lambda candidate: candidate.minimum_years)


def _local_experience_context(text: str, match: re.Match[str]) -> str:
    start = max(text.rfind(",", 0, match.start()), text.rfind(";", 0, match.start())) + 1
    comma = text.find(",", match.end())
    semicolon = text.find(";", match.end())
    ends = [position for position in (comma, semicolon) if position >= 0]
    end = min(ends) if ends else len(text)
    return text[start:end].strip()


def _experience_is_hard(text: str, *, clause_soft: bool) -> bool:
    if _NEGATED_REQUIREMENT.search(text):
        return False
    if _HARD_EXPERIENCE.search(text):
        return True
    if _SOFT_REQUIREMENT.search(text):
        return False
    return not clause_soft


def _looks_like_experience_requirement(
    sentence: str,
    match: re.Match[str] | None,
) -> bool:
    if _COMPANY_TENURE.search(sentence):
        return False
    if re.search(
        r"\b(?:experience|experienced|minimum|at least|required|requirements?|"
        r"qualifications?|professional|industry)\b",
        sentence,
        re.IGNORECASE,
    ):
        return True
    local_match = match or _EXPERIENCE.search(sentence)
    if local_match is None:
        return False
    after_years = sentence[local_match.end() :]
    return bool(
        re.match(
            r"\s+(?:of\s+)?(?:building|developing|engineering|programming|working|designing)",
            after_years,
            re.IGNORECASE,
        )
    )


def _country_codes(value: str) -> set[str]:
    return {
        code
        for code, patterns in _COUNTRY_PATTERNS
        if any(re.search(pattern, value, re.IGNORECASE) for pattern in patterns)
    }


def _requirement_clauses(value: str) -> list[_RequirementClause]:
    clauses: list[_RequirementClause] = []
    soft_section = False
    for raw_line in value.splitlines():
        line = " ".join(raw_line.split()).strip(" -•\t")
        if not line:
            continue
        if _SOFT_SECTION.search(line):
            soft_section = True
        elif _HARD_SECTION.search(line):
            soft_section = False
        for item in re.split(
            r"(?<=[.!?;])\s+|,?\s+(?:but|while|however)\s+",
            line,
            flags=re.IGNORECASE,
        ):
            text = item.strip(" -•\t")
            if not text:
                continue
            explicitly_soft = bool(_SOFT_REQUIREMENT.search(text))
            explicitly_hard = bool(_HARD_SECTION.search(text))
            clauses.append(
                _RequirementClause(
                    text=text,
                    soft=explicitly_soft or (soft_section and not explicitly_hard),
                )
            )
    return clauses


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


def _normalize_description(value: str) -> str:
    return "\n".join(
        line
        for raw_line in value.splitlines()
        if (line := " ".join(raw_line.split()))
    )


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
