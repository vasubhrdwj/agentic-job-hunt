"""Owner-scoped persistence for normalized job postings and Today decisions.

The repository accepts already-fetched :class:`Role` records. It never calls a
source adapter or model provider. Public posting facts are versioned in clear
text; resume-derived ``match_reason`` and ``fit_score`` are deliberately not
included in the persisted snapshot.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import env_bool
from .job_queue import utcnow
from .fit_evaluation import (
    FitEvaluationAuthorization,
    FitEvaluationEvidence,
    FitEvaluationInput,
    FitEvaluationPosting,
    FitEvaluationProfile,
    FitEvaluationTarget,
    FitVerdict,
    merge_fit_verdict,
)
from .fit_evaluation_repository import (
    FitEvaluatorIdentity,
    fit_evaluator_version,
    fit_input_fingerprint,
    fit_profile_input_fingerprint,
)
from .gemini_fit_provider import (
    DEFAULT_FIT_MODEL,
    FIT_PROMPT_VERSION,
    FIT_PROVIDER_NAME,
)
from .models import (
    AchievementEvidence,
    CandidateProfile,
    CareerTrack,
    JobObservation,
    JobPosting,
    JobPostingAlias,
    JobPostingVersion,
    OpportunityDecisionEvent as OpportunityDecisionEventRow,
    OpportunityFitEvaluation,
    OpportunityScan,
    OpportunityScanSource,
    OwnerOpportunity,
    ResumeVersion,
    SavedSearch,
    SavedSearchMatch,
)
from .opportunity_assessment import (
    AssessmentAuthorization,
    AssessmentEvidence,
    AssessmentPosting,
    AssessmentProfile,
    AssessmentTarget,
    assess_opportunity,
)
from .opportunity_schemas import (
    AssessmentConfidence,
    CompensationEvidenceFact,
    DateEvidenceFact,
    DismissReason,
    EmploymentTypeEvidenceFact,
    EvidenceState,
    MatchAssessmentState,
    NotAssessedReason,
    OpportunityDecisionAction,
    OpportunityDecisionEvent,
    OpportunityDecisionRequest,
    OpportunityDecisionResponse,
    OpportunityDecisionState,
    OpportunityDetailResponse,
    OpportunityFactField,
    OpportunityEligibility,
    OpportunityFitBand,
    OpportunityFacts,
    OpportunityLane,
    OpportunityPosting,
    OpportunityUnknown,
    PostingChangeKind,
    PostingChangedField,
    PostingState,
    PostingVersionSummary,
    SavedSearchProvenance,
    ScanHealthState,
    ScanWarning,
    ScanWarningCode,
    ScanWarningScope,
    TextEvidenceFact,
    TodayListResponse,
    TodayOpportunityItem,
    TodayQuery,
    TodayRecommendationSignals,
    TodayScanHealth,
    TodaySort,
    TodaySummary,
    TodayView,
    TransparentMatchSummary,
    UnknownReasonCode,
)
from .private_payloads import decrypt_private_payload, encrypt_private_payload
from .profile_schemas import CandidateProfileData
from .repository_errors import ResourceConflict, require_version
from .schemas import Role
from .security import DataKeyring
from .sources.base import safe_url_path_parts


_TRACKING_QUERY_KEYS = frozenset(
    {
        "dclid",
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "msclkid",
    }
)
_DECISION_NOTE_KIND = "opportunity_decision_note"

# Preference learning stays intentionally small and legible. A title can map to
# more than one category, but free-form descriptions and private resume text are
# never mined for implicit preferences.
_TITLE_ROLE_TAG_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "backend",
        re.compile(
            r"\bback[ -]?end\b|\bserver[ -]?side\b|\bapi (?:engineer|developer)\b",
            re.I,
        ),
    ),
    ("platform", re.compile(r"\bplatform\b", re.I)),
    ("infrastructure", re.compile(r"\binfrastructure\b|\binfra\b", re.I)),
    (
        "site reliability",
        re.compile(r"\bsite reliability\b|\bSRE\b", re.I),
    ),
    ("devops", re.compile(r"\bdevops\b", re.I)),
    ("security", re.compile(r"\bsecurity\b|\bapplication security\b", re.I)),
    (
        "machine learning",
        re.compile(r"\bmachine learning\b|\bML (?:engineer|scientist)\b", re.I),
    ),
    (
        "artificial intelligence",
        re.compile(r"\bartificial intelligence\b|\bAI (?:engineer|researcher)\b", re.I),
    ),
    ("data engineering", re.compile(r"\bdata engineer(?:ing)?\b", re.I)),
    ("data science", re.compile(r"\bdata scientist\b|\bdata science\b", re.I)),
    ("full stack", re.compile(r"\bfull[ -]?stack\b", re.I)),
    ("frontend", re.compile(r"\bfront[ -]?end\b|\bweb UI\b", re.I)),
    (
        "mobile",
        re.compile(r"\bmobile\b|\biOS\b|\bAndroid\b|\bReact Native\b", re.I),
    ),
    (
        "quality engineering",
        re.compile(
            r"\bquality (?:assurance|engineer(?:ing)?)\b|"
            r"\bQA engineer\b|\btest automation\b",
            re.I,
        ),
    ),
)
_PREFERENCE_MIN_POSITIVE_DECISIONS = 2
_PREFERENCE_MIN_NEGATIVE_DECISIONS = 2
_PREFERENCE_MIN_TAG_MARGIN = 2
_PREFERENCE_NEGATIVE_REASONS = frozenset({"not_relevant", "not_a_better_move"})


class OpportunityRepositoryError(RuntimeError):
    """Base class for safe opportunity persistence failures."""


class InvalidTodayCursor(ValueError):
    """The opaque Today pagination token cannot continue one stable ordering."""


class PostingIdentityConflict(OpportunityRepositoryError):
    """Two stable aliases unexpectedly point at different postings."""


class OpportunityNotFound(OpportunityRepositoryError):
    """An owner-scoped opportunity is absent without revealing other owners."""


class DecisionIdempotencyConflict(ResourceConflict):
    """An idempotency key was reused with a different decision request."""


@dataclass(frozen=True)
class PostingIdentity:
    kind: str
    key: str
    key_hash: str
    source: str
    company_slug: str
    source_job_id: str | None
    canonical_url: str


@dataclass(frozen=True)
class PersistedRole:
    posting_id: str
    posting_version_id: str
    observation_id: str
    saved_search_match_id: str
    opportunity_id: str
    posting_created: bool
    version_created: bool
    posting_changed: bool
    match_created: bool
    opportunity_created: bool
    replayed: bool


@dataclass(frozen=True)
class _LegacyTodayCursor:
    surfaced_at: datetime
    opportunity_id: str


@dataclass(frozen=True)
class _DiverseTodayCursor:
    snapshot_at: datetime
    company_position: int
    surfaced_at: datetime
    opportunity_id: str


@dataclass(frozen=True)
class _RecommendedTodayCursor:
    snapshot_at: datetime
    offset: int
    ordering_fingerprint: str
    query_fingerprint: str


@dataclass(frozen=True)
class _RecommendedTodayCandidate:
    opportunity: OwnerOpportunity
    posting: JobPosting
    version: JobPostingVersion
    match_rows: tuple[tuple[SavedSearchMatch, SavedSearch], ...]
    match: TransparentMatchSummary
    recency: _PostingRecency
    preference: _RevealedPreferenceSignal


@dataclass(frozen=True)
class _PostingRecency:
    """Stable, categorical age signal calculated from the page snapshot."""

    age_days: int
    source: str
    stale_priority: int
    freshness_priority: int


@dataclass(frozen=True)
class _RevealedPreferenceProfile:
    """Title categories with enough opposing owner decisions to learn from."""

    preferred_tags: frozenset[str] = frozenset()
    deprioritized_tags: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _RevealedPreferenceSignal:
    """One explainable title-category tie-breaker; neutral is the default."""

    priority: int = 1
    preferred_tags: tuple[str, ...] = ()
    deprioritized_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class _AssessmentPrivateInputs:
    profile: AssessmentProfile
    model_profile: FitEvaluationProfile
    evidence: tuple[AssessmentEvidence, ...]
    model_evidence: tuple[FitEvaluationEvidence, ...]
    profile_id: str | None
    profile_version: int | None
    evidence_versions: tuple[tuple[str, int], ...]
    available: bool


@dataclass(frozen=True)
class _PinnedResumeInput:
    content: str | None
    content_hash: str | None
    version: int | None
    unavailable_reason: NotAssessedReason | None


@dataclass
class _OpportunityAssessmentContext:
    """Request-local private inputs and pinned saved-search dependencies."""

    session: Session
    owner_id: str
    keyring: DataKeyring
    selected_saved_search_id: str | None
    private_inputs: _AssessmentPrivateInputs
    fit_identity: FitEvaluatorIdentity | None
    searches: dict[str, SavedSearch | None] = field(default_factory=dict)
    tracks: dict[str, CareerTrack | None] = field(default_factory=dict)
    resumes: dict[str, _PinnedResumeInput] = field(default_factory=dict)
    fit_rows: dict[str, list[OpportunityFitEvaluation]] = field(default_factory=dict)
    fit_rows_loaded: set[str] = field(default_factory=set)

    def assess(
        self,
        *,
        version: JobPostingVersion,
        match_rows: list[tuple[SavedSearchMatch, SavedSearch]],
    ) -> TransparentMatchSummary:
        search = self._select_search(match_rows)
        if search is None:
            return _not_assessed(NotAssessedReason.assessment_unavailable)

        description = _assessment_description(version.description)
        if description is None:
            return _not_assessed(NotAssessedReason.description_unavailable)
        if not self.private_inputs.available:
            return _not_assessed(NotAssessedReason.assessment_unavailable)

        track = self._track(search.career_track_id)
        if track is None:
            return _not_assessed(NotAssessedReason.assessment_unavailable)
        resume = self._resume(search.resume_version_id)
        if resume.unavailable_reason is not None:
            return _not_assessed(resume.unavailable_reason)
        assert resume.content is not None

        try:
            target = AssessmentTarget(
                role_families=_private_input_strings(track.role_families),
                seniority_levels=_private_input_strings(track.seniority_levels),
                target_locations=_private_input_strings(track.target_locations),
            )
        except (TypeError, ValueError):
            return _not_assessed(NotAssessedReason.assessment_unavailable)

        assessment_posting = AssessmentPosting(
            title=version.title,
            description=description,
            location=(
                version.location
                if version.location.strip().casefold() != "location not specified"
                else None
            ),
            employment_type=(
                version.employment_type
                if version.employment_type != "unknown"
                else None
            ),
        )
        result = assess_opportunity(
            posting=assessment_posting,
            target=target,
            profile=self.private_inputs.profile,
            resume_text=resume.content,
            evidence=self.private_inputs.evidence,
        )
        algorithm_version = result.algorithm_version
        input_fingerprint = self._input_fingerprint(
            algorithm_version=result.algorithm_version,
            posting_version=version,
            search=search,
            track=track,
            resume=resume,
        )
        fit_band = result.fit_band
        strengths = result.strengths
        gaps = result.gaps
        evidence_ids = result.approved_evidence_ids
        cached = self._cached_model_fit(
            version=version,
            search=search,
            track=track,
            resume=resume,
            posting=assessment_posting,
            target=target,
            deterministic=result,
        )
        if cached is not None:
            algorithm_version, input_fingerprint, resolved = cached
            fit_band = resolved.band
            strengths = resolved.reasons
            gaps = resolved.gaps
            evidence_ids = resolved.evidence_ids
        return TransparentMatchSummary(
            state=MatchAssessmentState.assessed,
            algorithm_version=algorithm_version,
            resume_version_id=search.resume_version_id,
            assessment_saved_search_id=search.id,
            assessment_input_fingerprint=input_fingerprint,
            fit_band=fit_band,
            confidence=result.confidence,
            eligibility=result.eligibility,
            matched_terms=list(result.matched_terms),
            representative_requirement=result.representative_requirement,
            approved_evidence_ids=list(evidence_ids),
            strengths=list(strengths),
            gaps=list(gaps),
        )

    def preload_fit_rows(self, posting_version_ids: list[str]) -> None:
        """Bulk-load derived model verdict rows once for a Today result set."""

        if self.fit_identity is None:
            return
        pending = sorted(set(posting_version_ids) - self.fit_rows_loaded)
        if not pending:
            return
        for row in self.session.scalars(
            select(OpportunityFitEvaluation).where(
                OpportunityFitEvaluation.owner_id == self.owner_id,
                OpportunityFitEvaluation.posting_version_id.in_(pending),
            )
        ):
            self.fit_rows.setdefault(row.posting_version_id, []).append(row)
        self.fit_rows_loaded.update(pending)

    def _cached_model_fit(
        self,
        *,
        version: JobPostingVersion,
        search: SavedSearch,
        track: CareerTrack,
        resume: _PinnedResumeInput,
        posting: AssessmentPosting,
        target: AssessmentTarget,
        deterministic,
    ):
        identity = self.fit_identity
        if identity is None:
            return None
        if (
            self.private_inputs.profile_id is None
            or self.private_inputs.profile_version is None
            or resume.version is None
            or resume.content_hash is None
        ):
            return None
        inputs = FitEvaluationInput(
            posting=FitEvaluationPosting(
                title=posting.title,
                description=posting.description,
                location=posting.location,
                employment_type=posting.employment_type,
            ),
            target=FitEvaluationTarget(
                role_families=target.role_families,
                seniority_levels=target.seniority_levels,
                target_locations=target.target_locations,
            ),
            profile=self.private_inputs.model_profile,
            evidence=self.private_inputs.model_evidence,
        )
        profile_fingerprint = fit_profile_input_fingerprint(
            saved_search_id=search.id,
            saved_search_version=search.version,
            career_track_id=track.id,
            career_track_version=track.version,
            resume_id=search.resume_version_id,
            resume_version=resume.version,
            resume_content_hash=resume.content_hash,
            candidate_profile_id=self.private_inputs.profile_id,
            candidate_profile_version=self.private_inputs.profile_version,
            evidence_versions=self.private_inputs.evidence_versions,
            inputs=inputs,
        )
        input_fingerprint = fit_input_fingerprint(
            owner_id=self.owner_id,
            job_posting_id=version.job_posting_id,
            posting_version_id=version.id,
            posting_version_number=version.version_number,
            posting_hash=version.content_hash,
            profile_input_fingerprint=profile_fingerprint,
            identity=identity,
        )
        evaluator_version = fit_evaluator_version(identity)
        self.preload_fit_rows([version.id])
        row = next(
            (
                candidate
                for candidate in self.fit_rows.get(version.id, [])
                if candidate.input_fingerprint == input_fingerprint
            ),
            None,
        )
        if row is None:
            return None
        if (
            row.job_posting_id != version.job_posting_id
            or row.posting_hash != version.content_hash
            or row.profile_input_fingerprint != profile_fingerprint
            or row.evaluator_version != evaluator_version
            or row.provider != identity.provider
            or row.model != identity.model
            or row.result_schema_version != 1
            or row.version != 1
        ):
            return None
        try:
            private = decrypt_private_payload(
                self.keyring,
                record_kind="opportunity_fit_evaluation",
                owner_id=self.owner_id,
                record_id=row.id,
                encryption_key_id=row.encryption_key_id,
                ciphertext=row.encrypted_payload,
            )
            verdict = FitVerdict.model_validate(private.get("verdict"))
            resolved = merge_fit_verdict(
                deterministic=deterministic,
                inputs=inputs,
                verdict=verdict,
            )
        except (TypeError, ValueError):
            # A corrupt/stale derived cache must never break Today. The local
            # deterministic assessment remains the truthful fallback.
            return None
        return evaluator_version, input_fingerprint, resolved

    def _input_fingerprint(
        self,
        *,
        algorithm_version: str,
        posting_version: JobPostingVersion,
        search: SavedSearch,
        track: CareerTrack,
        resume: _PinnedResumeInput,
    ) -> str:
        payload = {
            "algorithm": algorithm_version,
            "owner_scope": self.owner_id,
            "posting": [
                posting_version.id,
                posting_version.version_number,
                posting_version.content_hash,
            ],
            "saved_search": [search.id, search.version],
            "career_track": [track.id, track.version],
            "resume": [search.resume_version_id, resume.version],
            "profile_version": self.private_inputs.profile_version,
            "evidence_versions": self.private_inputs.evidence_versions,
        }
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def _select_search(
        self,
        match_rows: list[tuple[SavedSearchMatch, SavedSearch]],
    ) -> SavedSearch | None:
        for _match, search in match_rows:
            self.searches[search.id] = search
        if self.selected_saved_search_id is not None:
            return self.searches.get(self.selected_saved_search_id)
        active_rows = [pair for pair in match_rows if pair[1].active]
        if not active_rows:
            return None
        _match, search = max(
            active_rows,
            key=lambda pair: (
                _as_utc(pair[0].last_matched_at),
                pair[0].id,
            ),
        )
        return search

    def _track(self, track_id: str) -> CareerTrack | None:
        if track_id not in self.tracks:
            self.tracks[track_id] = self.session.scalar(
                select(CareerTrack).where(
                    CareerTrack.owner_id == self.owner_id,
                    CareerTrack.id == track_id,
                )
            )
        return self.tracks[track_id]

    def _resume(self, resume_id: str) -> _PinnedResumeInput:
        cached = self.resumes.get(resume_id)
        if cached is not None:
            return cached
        row = self.session.scalar(
            select(ResumeVersion).where(
                ResumeVersion.owner_id == self.owner_id,
                ResumeVersion.id == resume_id,
            )
        )
        if row is None:
            result = _PinnedResumeInput(
                content=None,
                content_hash=None,
                version=None,
                unavailable_reason=NotAssessedReason.resume_unavailable,
            )
        else:
            try:
                payload = decrypt_private_payload(
                    self.keyring,
                    record_kind="resume_version",
                    owner_id=self.owner_id,
                    record_id=row.id,
                    encryption_key_id=row.encryption_key_id,
                    ciphertext=row.encrypted_content,
                )
                content = payload.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("resume private payload is invalid")
                result = _PinnedResumeInput(
                    content=content,
                    content_hash=row.content_hash,
                    version=row.version,
                    unavailable_reason=None,
                )
            except (TypeError, ValueError):
                result = _PinnedResumeInput(
                    content=None,
                    content_hash=None,
                    version=None,
                    unavailable_reason=NotAssessedReason.assessment_unavailable,
                )
        self.resumes[resume_id] = result
        return result


def _build_assessment_context(
    session: Session,
    *,
    owner_id: str,
    keyring: DataKeyring,
    selected_saved_search_id: str | None,
) -> _OpportunityAssessmentContext:
    profile_row = session.scalar(
        select(CandidateProfile).where(CandidateProfile.owner_id == owner_id)
    )
    evidence_rows = list(
        session.scalars(
            select(AchievementEvidence)
            .where(
                AchievementEvidence.owner_id == owner_id,
                AchievementEvidence.approval_state == "approved",
            )
            .order_by(AchievementEvidence.created_at, AchievementEvidence.id)
        )
    )
    try:
        profile = AssessmentProfile()
        model_profile = FitEvaluationProfile()
        profile_id = None
        if profile_row is not None:
            payload = decrypt_private_payload(
                keyring,
                record_kind="candidate_profile",
                owner_id=owner_id,
                record_id=profile_row.id,
                encryption_key_id=profile_row.encryption_key_id,
                ciphertext=profile_row.encrypted_payload,
            )
            data = CandidateProfileData.model_validate(
                {**payload, "onboarding_step": profile_row.onboarding_state}
            )
            profile = AssessmentProfile(
                current_location=data.current_location,
                work_modes=tuple(data.work_modes),
                employment_types=tuple(data.employment_types),
                years_of_experience=data.years_of_experience,
                work_authorizations=tuple(
                    AssessmentAuthorization(
                        country_code=authorization.country_code,
                        status=authorization.status,
                    )
                    for authorization in data.work_authorizations
                ),
            )
            model_profile = FitEvaluationProfile(
                career_thesis=data.career_thesis,
                current_title=data.current_title,
                current_location=data.current_location,
                years_of_experience=data.years_of_experience,
                skills=tuple(data.skills),
                work_authorizations=tuple(
                    FitEvaluationAuthorization(
                        country_code=authorization.country_code,
                        status=authorization.status,
                    )
                    for authorization in data.work_authorizations
                ),
                work_modes=tuple(data.work_modes),
                employment_types=tuple(data.employment_types),
            )
            profile_id = profile_row.id

        evidence: list[AssessmentEvidence] = []
        model_evidence: list[FitEvaluationEvidence] = []
        for row in evidence_rows:
            payload = decrypt_private_payload(
                keyring,
                record_kind="achievement_evidence",
                owner_id=owner_id,
                record_id=row.id,
                encryption_key_id=row.encryption_key_id,
                ciphertext=row.encrypted_payload,
            )
            statement = payload.get("statement")
            if not isinstance(statement, str) or not statement.strip():
                raise ValueError("achievement evidence private payload is invalid")
            evidence.append(
                AssessmentEvidence(
                    id=row.id,
                    statement=statement,
                    skills=_private_input_strings(row.skills),
                )
            )
            model_evidence.append(
                FitEvaluationEvidence(
                    id=row.id,
                    statement=statement,
                    skills=_private_input_strings(row.skills),
                )
            )
        private_inputs = _AssessmentPrivateInputs(
            profile=profile,
            model_profile=model_profile,
            evidence=tuple(evidence),
            model_evidence=tuple(model_evidence),
            profile_id=profile_id,
            profile_version=profile_row.version if profile_row is not None else None,
            evidence_versions=tuple((row.id, row.version) for row in evidence_rows),
            available=True,
        )
    except (TypeError, ValueError):
        private_inputs = _AssessmentPrivateInputs(
            profile=AssessmentProfile(),
            model_profile=FitEvaluationProfile(),
            evidence=(),
            model_evidence=(),
            profile_id=None,
            profile_version=None,
            evidence_versions=(),
            available=False,
        )
    return _OpportunityAssessmentContext(
        session=session,
        owner_id=owner_id,
        keyring=keyring,
        selected_saved_search_id=selected_saved_search_id,
        private_inputs=private_inputs,
        fit_identity=_configured_fit_identity(),
    )


def _configured_fit_identity() -> FitEvaluatorIdentity | None:
    if not env_bool("ENABLE_LLM_FIT_EVALUATION", default=False):
        return None
    return FitEvaluatorIdentity(
        provider=FIT_PROVIDER_NAME,
        model=os.getenv("GEMINI_FIT_MODEL", "").strip() or DEFAULT_FIT_MODEL,
        prompt_version=FIT_PROMPT_VERSION,
    )


def _selected_assessment_search_id(
    session: Session,
    *,
    owner_id: str,
    query: TodayQuery,
) -> str | None:
    if query.saved_search_id is not None:
        return query.saved_search_id
    if query.scan_id is None:
        return None
    return session.scalar(
        select(OpportunityScan.saved_search_id).where(
            OpportunityScan.owner_id == owner_id,
            OpportunityScan.id == query.scan_id,
        )
    )


def _assessment_description(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = "\n".join(
        line
        for raw_line in value.splitlines()
        if (line := " ".join(raw_line.split()))
    )
    if len(normalized) < 20 or len(normalized.split()) < 3:
        return None
    return normalized[:100_000].rstrip()


def _private_input_strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError("private assessment input must be a string list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("private assessment input must contain non-blank strings")
        result.append(item)
    return tuple(result)


def _not_assessed(reason: NotAssessedReason) -> TransparentMatchSummary:
    return TransparentMatchSummary(
        state=MatchAssessmentState.not_assessed,
        not_assessed_reason=reason,
    )


def canonicalize_posting_url(value: str) -> str:
    """Return a stable HTTPS identity URL or reject an unsafe posting URL.

    Host casing, default HTTPS ports, fragments, trailing slashes, query order,
    and known tracking parameters are normalized. Path casing and meaningful
    query parameters (for example ``gh_jid``) remain intact.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError("posting URL is required")
    cleaned = value.strip()
    if (
        "\\" in cleaned
        or any(character.isspace() for character in cleaned)
        or any(ord(character) < 32 for character in cleaned)
    ):
        raise ValueError("posting URL must be a safe HTTPS URL")
    try:
        parsed = urlsplit(cleaned)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("posting URL must be a safe HTTPS URL") from exc
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or safe_url_path_parts(parsed.path) is None
    ):
        raise ValueError("posting URL must be HTTPS without credentials or traversal")

    hostname = parsed.hostname.casefold().rstrip(".")
    if not hostname:
        raise ValueError("posting URL hostname is required")
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    path = parsed.path.rstrip("/") or "/"
    query_pairs: list[tuple[str, str]] = []
    for key, item_value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = key.casefold()
        if normalized_key.startswith("utm_") or normalized_key in _TRACKING_QUERY_KEYS:
            continue
        if any(ord(character) < 32 for character in key + item_value):
            raise ValueError("posting URL query contains control characters")
        query_pairs.append((key, item_value))
    query = urlencode(sorted(query_pairs), doseq=True)
    return urlunsplit(("https", netloc, path, query, ""))


def posting_identity(
    role: Role,
    *,
    canonical_url: str | None = None,
    company_slug: str | None = None,
) -> PostingIdentity:
    """Build identity from native source facts, falling back only to URL."""

    normalized_url = canonical_url or canonicalize_posting_url(role.url)
    resolved_slug = (role.company_slug or company_slug or "").strip().casefold()
    if not resolved_slug:
        raise ValueError("company_slug is required for durable posting identity")
    source = role.source.value
    source_job_id = role.source_job_id.strip() if role.source_job_id else None
    if source_job_id:
        kind = "native"
        key = _identity_key("native", source, resolved_slug, source_job_id)
    else:
        kind = "url"
        key = _identity_key("url", resolved_slug, normalized_url)
    return PostingIdentity(
        kind=kind,
        key=key,
        key_hash=_sha256(key),
        source=source,
        company_slug=resolved_slug,
        source_job_id=source_job_id,
        canonical_url=normalized_url,
    )


def persist_scan_source_role(
    session: Session,
    *,
    owner_id: str,
    scan_source_id: str,
    role: Role,
    first_party_url_verified: bool,
    now: datetime | None = None,
) -> PersistedRole:
    """Idempotently persist one observed Role through the normalized graph."""

    current = _as_utc(now or utcnow())
    source_row = session.scalar(
        select(OpportunityScanSource)
        .where(
            OpportunityScanSource.owner_id == owner_id,
            OpportunityScanSource.id == scan_source_id,
        )
        .with_for_update()
    )
    if source_row is None:
        raise ValueError("scan source does not exist for owner")
    scan = session.scalar(
        select(OpportunityScan)
        .where(
            OpportunityScan.owner_id == owner_id,
            OpportunityScan.id == source_row.opportunity_scan_id,
        )
        .with_for_update()
    )
    if scan is None:
        raise OpportunityRepositoryError("scan source has no owner scan")
    if role.source.value != source_row.source:
        raise ValueError("role source does not match scan source")
    if (
        role.company_slug is not None
        and role.company_slug.casefold() != source_row.company_slug.casefold()
    ):
        raise ValueError("role company_slug does not match scan source")

    canonical_url = canonicalize_posting_url(role.url)
    identity = posting_identity(
        role,
        canonical_url=canonical_url,
        company_slug=source_row.company_slug,
    )
    canonical_apply_urls = _canonical_apply_urls(role, canonical_url)
    aliases = _alias_specs(identity)

    posting, posting_created = _find_or_create_posting(
        session,
        owner_id=owner_id,
        identity=identity,
        aliases=aliases,
        now=current,
    )
    posting = session.scalar(
        select(JobPosting)
        .where(JobPosting.owner_id == owner_id, JobPosting.id == posting.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert posting is not None
    if (
        identity.kind == "native"
        and posting.identity_kind == "native"
        and posting.identity_key_hash != identity.key_hash
    ):
        # A concurrent first native enrichment may have claimed a URL-fallback
        # posting while this transaction waited. Resolve the now-distinct
        # requisition again instead of merging through the shared URL.
        posting, posting_created = _find_or_create_posting(
            session,
            owner_id=owner_id,
            identity=identity,
            aliases=aliases,
            now=current,
        )
        posting = session.scalar(
            select(JobPosting)
            .where(JobPosting.owner_id == owner_id, JobPosting.id == posting.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert posting is not None
    # A scan can wait behind another scan after capturing its observation
    # timestamp. Once the posting lock is ours, keep every downstream record
    # monotonic with the posting that may have committed while we waited.
    current = max(
        current,
        _as_utc(posting.first_confirmed_at),
        _as_utc(posting.last_confirmed_at),
    )

    existing_observation = session.scalar(
        select(JobObservation).where(
            JobObservation.owner_id == owner_id,
            JobObservation.opportunity_scan_source_id == source_row.id,
            JobObservation.job_posting_id == posting.id,
        )
    )
    if existing_observation is not None:
        match = _owner_match(session, owner_id, scan.saved_search_id, posting.id)
        opportunity = _owner_opportunity(session, owner_id, posting.id)
        if match is None or opportunity is None:
            raise OpportunityRepositoryError("observation graph is incomplete")
        return PersistedRole(
            posting_id=posting.id,
            posting_version_id=existing_observation.job_posting_version_id,
            observation_id=existing_observation.id,
            saved_search_match_id=match.id,
            opportunity_id=opportunity.id,
            posting_created=False,
            version_created=False,
            posting_changed=False,
            match_created=False,
            opportunity_created=False,
            replayed=True,
        )

    if identity.kind == "native" and posting.identity_kind == "url":
        # The first stable source ID upgrades a URL fallback. Retain the URL
        # alias for lookup, but make subsequent differing requisition IDs
        # distinct by promoting the posting's primary identity.
        posting.identity_kind = "native"
        posting.identity_key = identity.key
        posting.identity_key_hash = identity.key_hash
        posting.source = identity.source
        posting.company_slug = identity.company_slug
        posting.source_job_id = identity.source_job_id

    alias_rows: list[JobPostingAlias] = []
    for spec in aliases:
        try:
            alias_rows.append(
                _ensure_alias(
                    session,
                    owner_id=owner_id,
                    posting=posting,
                    spec=spec,
                    now=current,
                )
            )
        except PostingIdentityConflict:
            # A generic canonical URL may legitimately be shared by distinct
            # native requisitions. Native identity remains authoritative; the
            # URL simply cannot be an alias for both postings.
            if identity.kind == "native" and spec.kind == "url":
                continue
            raise
    if not alias_rows:
        raise OpportunityRepositoryError("posting has no durable identity alias")
    observation_alias = next(
        (row for row in alias_rows if row.alias_key_hash == identity.key_hash),
        alias_rows[0],
    )

    snapshot = _public_role_snapshot(role, canonical_url, canonical_apply_urls)
    content_hash = _sha256(_canonical_json(snapshot))
    latest_version = _latest_posting_version(
        session,
        owner_id=owner_id,
        posting_id=posting.id,
    )
    version_created = False
    posting_changed = False
    if latest_version is None or latest_version.content_hash != content_hash:
        version_row = JobPostingVersion(
            owner_id=owner_id,
            job_posting_id=posting.id,
            version_number=(latest_version.version_number + 1 if latest_version else 1),
            content_hash=content_hash,
            source=snapshot["source"],
            source_job_id=snapshot["source_job_id"],
            company_name=snapshot["company_name"],
            title=snapshot["title"],
            canonical_url=snapshot["canonical_url"],
            apply_urls=snapshot["apply_urls"],
            location=snapshot["location"],
            summary=snapshot["summary"],
            description=snapshot["description"],
            employment_type=snapshot["employment_type"],
            posted_at_text=snapshot["posted_at_text"],
            source_updated_at_text=snapshot["source_updated_at_text"],
            source_facts=snapshot["source_facts"],
            source_confidence=snapshot["source_confidence"],
            observed_at=current,
        )
        try:
            with session.begin_nested():
                session.add(version_row)
                session.flush()
            version_created = True
        except IntegrityError:
            winner = _latest_posting_version(
                session,
                owner_id=owner_id,
                posting_id=posting.id,
            )
            if winner is None or winner.content_hash != content_hash:
                raise PostingIdentityConflict(
                    "concurrent posting version changed; retry the sighting"
                )
            version_row = winner
        posting_changed = latest_version is not None
    else:
        version_row = latest_version

    if posting.lifecycle_state == "closed":
        posting.lifecycle_state = "open"
        posting.closed_at = None
        posting.closure_reason = None
        posting_changed = True
    posting.consecutive_complete_omissions = 0
    posting.last_confirmed_at = max(_as_utc(posting.last_confirmed_at), current)
    posting.canonical_url = canonical_url
    if posting_changed:
        posting.last_changed_at = current
        posting.version += 1
    posting.updated_at = current

    observation = JobObservation(
        owner_id=owner_id,
        opportunity_scan_id=scan.id,
        opportunity_scan_source_id=source_row.id,
        job_posting_id=posting.id,
        job_posting_version_id=version_row.id,
        job_posting_alias_id=observation_alias.id,
        first_party_url_verified=first_party_url_verified,
        observed_at=current,
    )
    session.add(observation)
    session.flush()

    match, match_created = _upsert_saved_search_match(
        session,
        owner_id=owner_id,
        scan=scan,
        posting=posting,
        posting_version=version_row,
        now=current,
    )
    opportunity, opportunity_created = _upsert_owner_opportunity(
        session,
        owner_id=owner_id,
        posting=posting,
        changed=posting_changed or match_created,
        now=current,
    )

    source_row.persisted_count += 1
    source_row.version += 1
    source_row.updated_at = current
    scan.observed_count += 1
    scan.new_posting_count += int(posting_created)
    scan.changed_posting_count += int(posting_changed and not posting_created)
    scan.new_opportunity_count += int(opportunity_created)
    scan.version += 1
    scan.updated_at = current
    session.flush()
    return PersistedRole(
        posting_id=posting.id,
        posting_version_id=version_row.id,
        observation_id=observation.id,
        saved_search_match_id=match.id,
        opportunity_id=opportunity.id,
        posting_created=posting_created,
        version_created=version_created,
        posting_changed=posting_changed,
        match_created=match_created,
        opportunity_created=opportunity_created,
        replayed=False,
    )


def list_today_opportunities(
    session: Session,
    *,
    owner_id: str,
    query: TodayQuery,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> TodayListResponse:
    """Build the Today inbox exclusively from persisted owner-scoped rows."""

    current = _as_utc(now or utcnow())
    decision_by_view = {
        TodayView.inbox: "inbox",
        TodayView.watching: "watch",
        TodayView.dismissed: "dismiss",
    }
    filters = [
        OwnerOpportunity.owner_id == owner_id,
        OwnerOpportunity.job_posting_id.in_(
            select(JobPosting.id).where(
                JobPosting.owner_id == owner_id,
                JobPosting.lifecycle_state == "open",
            )
        ),
    ]
    if query.view is not TodayView.all:
        filters.append(
            OwnerOpportunity.decision == decision_by_view[query.view]
        )
    if query.scan_id is not None:
        filters.append(
            OwnerOpportunity.job_posting_id.in_(
                select(JobObservation.job_posting_id).where(
                    JobObservation.owner_id == owner_id,
                    JobObservation.opportunity_scan_id == query.scan_id,
                )
            )
        )
    if query.saved_search_id is not None:
        filters.append(
            OwnerOpportunity.job_posting_id.in_(
                select(SavedSearchMatch.job_posting_id).where(
                    SavedSearchMatch.owner_id == owner_id,
                    SavedSearchMatch.saved_search_id == query.saved_search_id,
                )
            )
        )
    cursor = _decode_cursor(query.cursor) if query.cursor else None
    if query.scan_id is None and not isinstance(cursor, _LegacyTodayCursor):
        source_snapshot_at = (
            cursor.snapshot_at
            if isinstance(cursor, (_DiverseTodayCursor, _RecommendedTodayCursor))
            else current
        )
        current_inbox = _current_inbox_snapshot_condition(
            owner_id=owner_id,
            snapshot_at=source_snapshot_at,
            saved_search_id=query.saved_search_id,
        )
        if query.view is TodayView.inbox:
            filters.append(current_inbox)
        elif query.view is TodayView.all:
            filters.append(
                (OwnerOpportunity.decision != "inbox") | current_inbox
            )
    if isinstance(cursor, _RecommendedTodayCursor) and query.sort is not TodaySort.recommended:
        raise InvalidTodayCursor("cursor is invalid")
    assessment_context = _build_assessment_context(
        session,
        owner_id=owner_id,
        keyring=keyring,
        selected_saved_search_id=_selected_assessment_search_id(
            session,
            owner_id=owner_id,
            query=query,
        ),
    )
    lane_is_supported = query.lane in (None, OpportunityLane.unassigned)
    recommended_mode = (
        isinstance(cursor, _RecommendedTodayCursor)
        or (cursor is None and query.sort is TodaySort.recommended)
    )
    if recommended_mode:
        snapshot_at = cursor.snapshot_at if cursor is not None else current
        query_fingerprint = _recommended_query_fingerprint(query)
        if (
            isinstance(cursor, _RecommendedTodayCursor)
            and not hmac.compare_digest(cursor.query_fingerprint, query_fingerprint)
        ):
            raise InvalidTodayCursor("cursor is invalid")
        candidates = (
            _recommended_today_candidates(
                session,
                filters=filters,
                snapshot_at=snapshot_at,
                assessment_context=assessment_context,
            )
            if lane_is_supported
            else []
        )
        ordered = _rank_recommended_today(candidates)
        ordering_fingerprint = _recommended_ordering_fingerprint(ordered)
        offset = cursor.offset if isinstance(cursor, _RecommendedTodayCursor) else 0
        if isinstance(cursor, _RecommendedTodayCursor) and (
            offset > len(ordered)
            or not hmac.compare_digest(
                cursor.ordering_fingerprint,
                ordering_fingerprint,
            )
        ):
            # A profile, evidence, posting, filter, or decision changed after page
            # one. Refuse to splice two different rankings together; a fresh load
            # gives the owner one coherent recommendation order.
            raise InvalidTodayCursor("cursor is invalid")
        selected_candidates = ordered[offset : offset + query.limit]
        items = _recommended_today_items(
            session,
            candidates=selected_candidates,
            keyring=keyring,
        )
        next_offset = offset + len(selected_candidates)
        next_cursor = (
            _encode_recommended_cursor(
                snapshot_at=snapshot_at,
                offset=next_offset,
                ordering_fingerprint=ordering_fingerprint,
                query_fingerprint=query_fingerprint,
            )
            if next_offset < len(ordered)
            else None
        )
    elif isinstance(cursor, _LegacyTodayCursor):
        # Cursors issued before company-diverse ordering must finish traversing
        # the old recency order. Switching order mid-pagination can duplicate or
        # omit rows, so legacy tokens deliberately retain legacy semantics.
        legacy_statement = (
            select(OwnerOpportunity)
            .where(*filters)
            .where(
                (OwnerOpportunity.last_surfaced_at < cursor.surfaced_at)
                | (
                    (OwnerOpportunity.last_surfaced_at == cursor.surfaced_at)
                    & (OwnerOpportunity.id < cursor.opportunity_id)
                )
            )
            .order_by(
                OwnerOpportunity.last_surfaced_at.desc(),
                OwnerOpportunity.id.desc(),
            )
            .limit(query.limit + 1)
        )
        rows = list(session.scalars(legacy_statement)) if lane_is_supported else []
        has_more = len(rows) > query.limit
        selected = rows[: query.limit]
        items = [
            _today_item(
                session,
                opportunity=row,
                keyring=keyring,
                assessment_context=assessment_context,
            )
            for row in selected
        ]
        next_cursor = (
            _encode_cursor(selected[-1].last_surfaced_at, selected[-1].id)
            if has_more and selected
            else None
        )
    else:
        snapshot_at = cursor.snapshot_at if cursor is not None else current
        company_position = func.row_number().over(
            partition_by=JobPosting.company_slug,
            order_by=(
                OwnerOpportunity.last_surfaced_at.desc(),
                OwnerOpportunity.id.desc(),
            ),
        ).label("company_position")
        ranked = (
            select(
                OwnerOpportunity.id.label("opportunity_id"),
                OwnerOpportunity.last_surfaced_at.label("surfaced_at"),
                company_position,
            )
            .join(
                JobPosting,
                (JobPosting.owner_id == OwnerOpportunity.owner_id)
                & (JobPosting.id == OwnerOpportunity.job_posting_id),
            )
            .where(*filters)
            .where(OwnerOpportunity.last_surfaced_at <= snapshot_at)
            .cte("ranked_today_opportunities")
        )
        diverse_statement = select(
            OwnerOpportunity,
            ranked.c.company_position,
        ).join(
            ranked,
            ranked.c.opportunity_id == OwnerOpportunity.id,
        )
        if cursor is not None:
            diverse_statement = diverse_statement.where(
                (ranked.c.company_position > cursor.company_position)
                | (
                    (ranked.c.company_position == cursor.company_position)
                    & (ranked.c.surfaced_at < cursor.surfaced_at)
                )
                | (
                    (ranked.c.company_position == cursor.company_position)
                    & (ranked.c.surfaced_at == cursor.surfaced_at)
                    & (ranked.c.opportunity_id < cursor.opportunity_id)
                )
            )
        diverse_statement = diverse_statement.order_by(
            ranked.c.company_position.asc(),
            ranked.c.surfaced_at.desc(),
            ranked.c.opportunity_id.desc(),
        ).limit(query.limit + 1)
        ranked_rows = (
            list(session.execute(diverse_statement)) if lane_is_supported else []
        )
        rows = [row[0] for row in ranked_rows]
        company_positions = [int(row[1]) for row in ranked_rows]
        has_more = len(rows) > query.limit
        selected = rows[: query.limit]
        selected_company_positions = company_positions[: query.limit]
        items = [
            _today_item(
                session,
                opportunity=row,
                keyring=keyring,
                assessment_context=assessment_context,
            )
            for row in selected
        ]
        next_cursor = (
            _encode_diverse_cursor(
                snapshot_at=snapshot_at,
                company_position=selected_company_positions[-1],
                surfaced_at=selected[-1].last_surfaced_at,
                opportunity_id=selected[-1].id,
            )
            if has_more and selected
            else None
        )
    return TodayListResponse(
        data_source="database",
        as_of=current,
        summary=_today_summary(
            session,
            owner_id,
            scan_id=query.scan_id,
            as_of=current,
        ),
        scan_health=_today_scan_health(session, owner_id),
        items=items,
        next_cursor=next_cursor,
    )


def load_opportunity_detail(
    session: Session,
    *,
    owner_id: str,
    opportunity_id: str,
    keyring: DataKeyring,
    selected_saved_search_id: str | None = None,
) -> OpportunityDetailResponse | None:
    """Return one database-only review projection, or None across owner scope."""

    opportunity = session.scalar(
        select(OwnerOpportunity).where(
            OwnerOpportunity.owner_id == owner_id,
            OwnerOpportunity.id == opportunity_id,
        )
    )
    if opportunity is None:
        return None
    assessment_context = _build_assessment_context(
        session,
        owner_id=owner_id,
        keyring=keyring,
        selected_saved_search_id=selected_saved_search_id,
    )
    base = _today_item(
        session,
        opportunity=opportunity,
        keyring=keyring,
        assessment_context=assessment_context,
    )
    versions = list(
        session.scalars(
            select(JobPostingVersion)
            .where(
                JobPostingVersion.owner_id == owner_id,
                JobPostingVersion.job_posting_id == opportunity.job_posting_id,
            )
            .order_by(JobPostingVersion.version_number, JobPostingVersion.id)
        )
    )
    history = list(
        session.scalars(
            select(OpportunityDecisionEventRow)
            .where(
                OpportunityDecisionEventRow.owner_id == owner_id,
                OpportunityDecisionEventRow.owner_opportunity_id == opportunity.id,
            )
            .order_by(
                OpportunityDecisionEventRow.occurred_at,
                OpportunityDecisionEventRow.created_at,
                OpportunityDecisionEventRow.id,
            )
        )
    )
    summaries: list[PostingVersionSummary] = []
    previous: JobPostingVersion | None = None
    for version in versions:
        changed_fields = _changed_fields(previous, version)
        summaries.append(
            PostingVersionSummary(
                version=version.version_number,
                observed_at=_as_utc(version.observed_at),
                change_kind=(
                    PostingChangeKind.new
                    if previous is None
                    else PostingChangeKind.changed
                ),
                changed_fields=changed_fields,
            )
        )
        previous = version
    latest = versions[-1]
    return OpportunityDetailResponse(
        **base.model_dump(),
        data_source="database",
        description=_trimmed(latest.description, 100_000),
        apply_urls=list(latest.apply_urls),
        posting_versions=summaries,
        decision_history=[_decision_event_response(row, keyring) for row in history],
    )


def decide_owner_opportunity(
    session: Session,
    *,
    owner_id: str,
    opportunity_id: str,
    request: OpportunityDecisionRequest,
    expected_version: int,
    idempotency_key: str,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> OpportunityDecisionResponse:
    """Apply one optimistic, idempotent decision and append its audit event."""

    current = _as_utc(now or utcnow())
    normalized_key = idempotency_key.strip()
    if not normalized_key or len(normalized_key) > 200:
        raise ValueError("idempotency key must be 1-200 characters")
    key_hash = _sha256(normalized_key)
    request_payload = request.model_dump(mode="json")
    # Phase 2B added a pursue-only field to this shared transport model. Keep
    # ordinary decision hashes byte-for-byte compatible with Phase 2A so an
    # already accepted Watch/Dismiss/Restore key still replays after upgrade.
    if request.action is not OpportunityDecisionAction.pursue:
        request_payload.pop("initial_action_due_on", None)
        request_payload.pop("acquisition_source", None)
        request_payload.pop("selected_saved_search_id", None)
    request_hash = _sha256(_canonical_json(request_payload))
    opportunity = session.scalar(
        select(OwnerOpportunity)
        .where(
            OwnerOpportunity.owner_id == owner_id,
            OwnerOpportunity.id == opportunity_id,
        )
        .with_for_update()
    )
    if opportunity is None:
        raise OpportunityNotFound("opportunity not found")
    replay = session.scalar(
        select(OpportunityDecisionEventRow).where(
            OpportunityDecisionEventRow.owner_id == owner_id,
            OpportunityDecisionEventRow.owner_opportunity_id == opportunity.id,
            OpportunityDecisionEventRow.idempotency_key_hash == key_hash,
        )
    )
    if replay is not None:
        if not hmac.compare_digest(replay.request_hash, request_hash):
            raise DecisionIdempotencyConflict(
                "idempotency key was already used for another decision"
            )
        if opportunity.decision != replay.new_decision:
            raise ResourceConflict("decision replay was superseded by a newer decision")
        return _decision_response(opportunity, replay, keyring)

    if request.action is OpportunityDecisionAction.pursue:
        raise ValueError("pursue must use the atomic application boundary")
    if opportunity.decision == "pursued":
        raise ResourceConflict(
            "pursued opportunities are managed through their application"
        )

    require_version(
        "opportunity",
        opportunity.id,
        expected=expected_version,
        actual=opportunity.version,
    )
    latest_version = _latest_posting_version(
        session,
        owner_id=owner_id,
        posting_id=opportunity.job_posting_id,
    )
    if latest_version is None:
        raise OpportunityRepositoryError("opportunity posting has no version")

    action = request.action
    if action is OpportunityDecisionAction.watch:
        target = "watch"
        reason = None
        compensates = None
    elif action is OpportunityDecisionAction.dismiss:
        target = "dismiss"
        reason = request.dismiss_reason.value if request.dismiss_reason else None
        compensates = None
    elif action is OpportunityDecisionAction.restore_to_inbox:
        target = "inbox"
        reason = None
        compensates = session.scalar(
            select(OpportunityDecisionEventRow)
            .where(
                OpportunityDecisionEventRow.owner_id == owner_id,
                OpportunityDecisionEventRow.owner_opportunity_id == opportunity.id,
            )
            .order_by(
                OpportunityDecisionEventRow.occurred_at.desc(),
                OpportunityDecisionEventRow.created_at.desc(),
                OpportunityDecisionEventRow.id.desc(),
            )
            .limit(1)
            .with_for_update()
        )
        if (
            compensates is None
            or compensates.id != request.restore_decision_event_id
            or compensates.new_decision != opportunity.decision
        ):
            raise ResourceConflict("restore target is not the current opportunity decision")
    else:  # pragma: no cover - enum exhaustiveness is defended above.
        raise ValueError("unsupported opportunity decision action")
    if opportunity.decision == target:
        raise ResourceConflict("opportunity is already in the requested decision state")

    event_id = uuid4().hex
    encrypted_note = None
    note_key_id = None
    if request.note is not None:
        envelope = encrypt_private_payload(
            keyring,
            record_kind=_DECISION_NOTE_KIND,
            owner_id=owner_id,
            record_id=event_id,
            payload={"note": request.note},
        )
        encrypted_note = envelope.ciphertext
        note_key_id = envelope.key_id
    event = OpportunityDecisionEventRow(
        id=event_id,
        owner_id=owner_id,
        owner_opportunity_id=opportunity.id,
        job_posting_id=opportunity.job_posting_id,
        posting_version_id=latest_version.id,
        previous_decision=opportunity.decision,
        new_decision=target,
        reason_code=reason,
        encrypted_note=encrypted_note,
        note_key_id=note_key_id,
        compensates_event_id=compensates.id if compensates is not None else None,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
        occurred_at=current,
    )
    session.add(event)
    opportunity.decision = target
    opportunity.decision_reason_code = reason
    opportunity.reviewed_posting_version_id = latest_version.id
    opportunity.decision_updated_at = current
    opportunity.version += 1
    opportunity.updated_at = current
    session.flush()
    return _decision_response(opportunity, event, keyring)


def _today_item(
    session: Session,
    *,
    opportunity: OwnerOpportunity,
    keyring: DataKeyring,
    assessment_context: _OpportunityAssessmentContext,
    assessed_match: TransparentMatchSummary | None = None,
) -> TodayOpportunityItem:
    posting = session.scalar(
        select(JobPosting).where(
            JobPosting.owner_id == opportunity.owner_id,
            JobPosting.id == opportunity.job_posting_id,
        )
    )
    latest = _latest_posting_version(
        session,
        owner_id=opportunity.owner_id,
        posting_id=opportunity.job_posting_id,
    )
    if posting is None or latest is None:
        raise OpportunityRepositoryError("opportunity posting graph is incomplete")
    latest_observation = session.scalar(
        select(JobObservation)
        .where(
            JobObservation.owner_id == opportunity.owner_id,
            JobObservation.job_posting_id == posting.id,
            JobObservation.job_posting_version_id == latest.id,
        )
        .order_by(JobObservation.observed_at.desc(), JobObservation.id.desc())
        .limit(1)
    )
    if latest_observation is None:
        raise OpportunityRepositoryError("posting version has no observation")
    match_rows = list(
        session.execute(
            select(SavedSearchMatch, SavedSearch)
            .join(
                SavedSearch,
                (SavedSearch.owner_id == SavedSearchMatch.owner_id)
                & (SavedSearch.id == SavedSearchMatch.saved_search_id),
            )
            .where(
                SavedSearchMatch.owner_id == opportunity.owner_id,
                SavedSearchMatch.job_posting_id == posting.id,
            )
            .order_by(SavedSearchMatch.first_matched_at, SavedSearchMatch.id)
        )
    )
    latest_event = session.scalar(
        select(OpportunityDecisionEventRow)
        .where(
            OpportunityDecisionEventRow.owner_id == opportunity.owner_id,
            OpportunityDecisionEventRow.owner_opportunity_id == opportunity.id,
        )
        .order_by(
            OpportunityDecisionEventRow.occurred_at.desc(),
            OpportunityDecisionEventRow.created_at.desc(),
            OpportunityDecisionEventRow.id.desc(),
        )
        .limit(1)
    )
    match = (
        assessed_match
        if assessed_match is not None
        else assessment_context.assess(version=latest, match_rows=match_rows)
    )
    return _today_item_from_graph(
        opportunity=opportunity,
        posting=posting,
        latest=latest,
        latest_observation=latest_observation,
        match_rows=match_rows,
        latest_event=latest_event,
        match=match,
        keyring=keyring,
    )


def _today_item_from_graph(
    *,
    opportunity: OwnerOpportunity,
    posting: JobPosting,
    latest: JobPostingVersion,
    latest_observation: JobObservation,
    match_rows: list[tuple[SavedSearchMatch, SavedSearch]],
    latest_event: OpportunityDecisionEventRow | None,
    match: TransparentMatchSummary,
    keyring: DataKeyring,
    recommendation: TodayRecommendationSignals | None = None,
) -> TodayOpportunityItem:
    facts, unknowns = _facts_and_unknowns(latest)
    if posting.lifecycle_state == "closed":
        change_kind = PostingChangeKind.closed
        changed_at = _as_utc(posting.closed_at) if posting.closed_at else None
    elif opportunity.reviewed_posting_version_id == latest.id:
        change_kind = PostingChangeKind.unchanged
        changed_at = None
    elif latest.version_number == 1:
        change_kind = PostingChangeKind.new
        changed_at = None
    else:
        change_kind = PostingChangeKind.changed
        changed_at = _as_utc(posting.last_changed_at or latest.observed_at)
    return TodayOpportunityItem(
        id=opportunity.id,
        version=opportunity.version,
        state=OpportunityDecisionState(opportunity.decision),
        lane=OpportunityLane.unassigned,
        posting=OpportunityPosting(
            id=posting.id,
            company=latest.company_name,
            company_slug=posting.company_slug,
            title=latest.title,
            summary=_trimmed(latest.summary, 2_000) or "Summary unavailable.",
            canonical_url=posting.canonical_url,
            source=latest.source,
            source_job_id=latest.source_job_id,
            first_party=latest_observation.first_party_url_verified,
            state=PostingState(posting.lifecycle_state),
            change_kind=change_kind,
            first_seen_at=_as_utc(posting.first_confirmed_at),
            last_confirmed_at=_as_utc(posting.last_confirmed_at),
            changed_at=changed_at,
        ),
        facts=facts,
        unknowns=unknowns,
        discovered_by=[
            SavedSearchProvenance(
                saved_search_id=match.saved_search_id,
                saved_search_name=search.name,
                first_matched_at=_as_utc(match.first_matched_at),
                last_matched_at=_as_utc(match.last_matched_at),
            )
            for match, search in match_rows
        ],
        match=match,
        recommendation=recommendation,
        latest_decision=(
            _decision_event_response(latest_event, keyring)
            if latest_event is not None
            else None
        ),
        created_at=_as_utc(opportunity.created_at),
        updated_at=_as_utc(opportunity.updated_at),
    )


def _facts_and_unknowns(
    version: JobPostingVersion,
) -> tuple[OpportunityFacts, list[OpportunityUnknown]]:
    observed = _as_utc(version.observed_at)
    source_label = version.source
    unknowns: list[OpportunityUnknown] = []
    location_known = bool(
        version.location.strip()
        and version.location.strip().casefold() != "location not specified"
    )
    if not location_known:
        unknowns.append(_unknown(OpportunityFactField.location))
    employment_known = version.employment_type != "unknown"
    if not employment_known:
        unknowns.append(_unknown(OpportunityFactField.employment_type))
    posted_date = _parse_posted_date(version.posted_at_text)
    if posted_date is None:
        unknowns.append(_unknown(OpportunityFactField.posted_date))
    unknowns.append(_unknown(OpportunityFactField.compensation))
    return (
        OpportunityFacts(
            location=TextEvidenceFact(
                value=version.location if location_known else None,
                state=EvidenceState.verified if location_known else EvidenceState.unknown,
                source_label=source_label if location_known else None,
                observed_at=observed if location_known else None,
            ),
            employment_type=EmploymentTypeEvidenceFact(
                value=version.employment_type if employment_known else None,
                state=EvidenceState.verified if employment_known else EvidenceState.unknown,
                source_label=source_label if employment_known else None,
                observed_at=observed if employment_known else None,
            ),
            posted_date=DateEvidenceFact(
                value=posted_date,
                state=EvidenceState.verified if posted_date else EvidenceState.unknown,
                source_label=source_label if posted_date else None,
                observed_at=observed if posted_date else None,
            ),
            compensation=CompensationEvidenceFact(
                value=None,
                state=EvidenceState.unknown,
            ),
        ),
        unknowns,
    )


def _unknown(field: OpportunityFactField) -> OpportunityUnknown:
    return OpportunityUnknown(
        field=field,
        reason_code=UnknownReasonCode.not_reported_by_source,
        message=f"{field.value.replace('_', ' ').title()} was not reported by the source.",
    )


def _parse_posted_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _today_summary(
    session: Session,
    owner_id: str,
    *,
    scan_id: str | None = None,
    as_of: datetime,
) -> TodaySummary:
    statement = select(
        OwnerOpportunity.decision,
        func.count(OwnerOpportunity.id),
    ).where(
        OwnerOpportunity.owner_id == owner_id,
        OwnerOpportunity.job_posting_id.in_(
            select(JobPosting.id).where(
                JobPosting.owner_id == owner_id,
                JobPosting.lifecycle_state == "open",
            )
        ),
    )
    if scan_id is not None:
        statement = statement.where(
            OwnerOpportunity.job_posting_id.in_(
                select(JobObservation.job_posting_id).where(
                    JobObservation.owner_id == owner_id,
                    JobObservation.opportunity_scan_id == scan_id,
                )
            )
        )
    else:
        statement = statement.where(
            (OwnerOpportunity.decision != "inbox")
            | _current_inbox_snapshot_condition(
                owner_id=owner_id,
                snapshot_at=as_of,
            )
        )
    counts = dict(
        session.execute(statement.group_by(OwnerOpportunity.decision)).all()
    )
    return TodaySummary(
        needs_decision=int(counts.get("inbox", 0)),
        watching=int(counts.get("watch", 0)),
        dismissed=int(counts.get("dismiss", 0)),
    )


def _current_inbox_snapshot_condition(
    *,
    owner_id: str,
    snapshot_at: datetime,
    saved_search_id: str | None = None,
) -> Any:
    """Keep Inbox roles from each active search partition's current snapshot.

    Failed and transport-empty source attempts retain the last reliable result.
    A warning-free fetch that observed raw roles may advance to an intentionally
    empty post-filter snapshot, allowing stricter matching to retire prior false
    positives. Only sources produced by the active saved-search version are
    current; after a search edit, Inbox stays empty until that version is scanned.
    Owners without any reliable source history retain the in-progress cumulative
    fallback, scoped only to matches from active searches.
    """

    reliable_source_history = (
        select(
            OpportunityScanSource.id.label("scan_source_id"),
            OpportunityScan.saved_search_id.label("saved_search_id"),
            OpportunityScanSource.company_slug.label("company_slug"),
            OpportunityScanSource.source.label("source"),
            OpportunityScanSource.completed_at.label("completed_at"),
            OpportunityScan.saved_search_version.label("scan_search_version"),
            SavedSearch.version.label("active_search_version"),
        )
        .join(
            OpportunityScan,
            (OpportunityScan.owner_id == OpportunityScanSource.owner_id)
            & (OpportunityScan.id == OpportunityScanSource.opportunity_scan_id),
        )
        .join(
            SavedSearch,
            (SavedSearch.owner_id == OpportunityScan.owner_id)
            & (SavedSearch.id == OpportunityScan.saved_search_id),
        )
        .where(
            OpportunityScanSource.owner_id == owner_id,
            OpportunityScanSource.status == "succeeded",
            # A successful fetch that observed raw source roles but persisted
            # none is still a trustworthy post-filter empty snapshot. This is
            # how stricter country/title filters retire earlier false positives
            # without treating a transport-level empty response as authoritative.
            OpportunityScanSource.observed_count > 0,
            # JSON equality is not available for PostgreSQL's ``json`` type;
            # json_array_length is portable across PostgreSQL and SQLite JSON1.
            func.json_array_length(OpportunityScanSource.warning_codes) == 0,
            OpportunityScanSource.completed_at <= snapshot_at,
            OpportunityScan.status.in_(("succeeded", "partial")),
            OpportunityScan.finalized_at.is_not(None),
            SavedSearch.active.is_(True),
        )
    )
    if saved_search_id is not None:
        reliable_source_history = reliable_source_history.where(
            OpportunityScan.saved_search_id == saved_search_id
        )
    history = reliable_source_history.cte("reliable_today_source_history")
    partition_rank = func.row_number().over(
        partition_by=(
            history.c.saved_search_id,
            history.c.company_slug,
            history.c.source,
        ),
        order_by=(
            history.c.completed_at.desc(),
            history.c.scan_source_id.desc(),
        ),
    ).label("partition_rank")
    ranked_sources = (
        select(history.c.scan_source_id, partition_rank)
        .where(history.c.scan_search_version == history.c.active_search_version)
        .cte("ranked_reliable_today_sources")
    )
    current_sources = (
        select(ranked_sources.c.scan_source_id)
        .where(ranked_sources.c.partition_rank == 1)
        .cte("current_today_sources")
    )
    current_posting_ids = select(JobObservation.job_posting_id).where(
        JobObservation.owner_id == owner_id,
        JobObservation.opportunity_scan_source_id.in_(
            select(current_sources.c.scan_source_id)
        ),
    )
    active_match_ids = (
        select(SavedSearchMatch.job_posting_id)
        .join(
            SavedSearch,
            (SavedSearch.owner_id == SavedSearchMatch.owner_id)
            & (SavedSearch.id == SavedSearchMatch.saved_search_id),
        )
        .where(
            SavedSearchMatch.owner_id == owner_id,
            SavedSearch.active.is_(True),
        )
    )
    if saved_search_id is not None:
        active_match_ids = active_match_ids.where(
            SavedSearchMatch.saved_search_id == saved_search_id
        )
    has_reliable_history = select(history.c.scan_source_id).exists()
    current_snapshot = OwnerOpportunity.job_posting_id.in_(current_posting_ids)
    in_progress_fallback = (
        ~has_reliable_history
        & OwnerOpportunity.job_posting_id.in_(active_match_ids)
    )
    return current_snapshot | in_progress_fallback


def _today_scan_health(session: Session, owner_id: str) -> TodayScanHealth:
    active_searches = int(
        session.scalar(
            select(func.count(SavedSearch.id)).where(
                SavedSearch.owner_id == owner_id,
                SavedSearch.active.is_(True),
            )
        )
        or 0
    )
    latest = session.scalar(
        select(OpportunityScan)
        .where(OpportunityScan.owner_id == owner_id)
        .order_by(OpportunityScan.created_at.desc(), OpportunityScan.id.desc())
        .limit(1)
    )
    if latest is None:
        return TodayScanHealth(
            state=ScanHealthState.never_run,
            active_searches=active_searches,
        )
    last_success = session.scalar(
        select(OpportunityScan.finalized_at)
        .where(
            OpportunityScan.owner_id == owner_id,
            OpportunityScan.status.in_({"succeeded", "partial"}),
        )
        .order_by(OpportunityScan.finalized_at.desc())
        .limit(1)
    )
    last_attempt = _as_utc(latest.finalized_at or latest.started_at or latest.created_at)
    normalized_last_success = _as_utc(last_success) if last_success else None
    if normalized_last_success is not None and normalized_last_success > last_attempt:
        last_attempt = normalized_last_success
    if latest.status in {"queued", "running"}:
        state = ScanHealthState.running
        running_scan_id = latest.id
        warnings: list[ScanWarning] = []
    elif latest.status == "succeeded":
        state = ScanHealthState.healthy
        running_scan_id = None
        warnings = []
    else:
        state = ScanHealthState.degraded
        running_scan_id = None
        warnings = [
            ScanWarning(
                scope=ScanWarningScope.scan,
                code=ScanWarningCode.scan_interrupted,
                message=(
                    "The latest scan was incomplete; previously confirmed roles "
                    "remain visible."
                ),
                retryable=latest.status in {"partial", "failed"},
                occurred_at=last_attempt,
                last_success_at=normalized_last_success,
            )
        ]
    return TodayScanHealth(
        state=state,
        active_searches=active_searches,
        running_scan_id=running_scan_id,
        last_attempt_at=last_attempt,
        last_success_at=normalized_last_success,
        warnings=warnings,
    )


def _changed_fields(
    previous: JobPostingVersion | None,
    current: JobPostingVersion,
) -> list[PostingChangedField]:
    if previous is None:
        return []
    mappings = (
        ("title", PostingChangedField.title),
        ("description", PostingChangedField.description),
        ("location", PostingChangedField.location),
        ("employment_type", PostingChangedField.employment_type),
        ("posted_at_text", PostingChangedField.posted_date),
        ("canonical_url", PostingChangedField.canonical_url),
    )
    changed = [
        field
        for attribute, field in mappings
        if getattr(previous, attribute) != getattr(current, attribute)
    ]
    if not changed:
        changed.append(PostingChangedField.description)
    return changed


def _encode_cursor(value: datetime, opportunity_id: str) -> str:
    raw = _canonical_json([_as_utc(value).isoformat(), opportunity_id]).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _encode_diverse_cursor(
    *,
    snapshot_at: datetime,
    company_position: int,
    surfaced_at: datetime,
    opportunity_id: str,
) -> str:
    raw = _canonical_json(
        [
            "company_round_robin_v1",
            _as_utc(snapshot_at).isoformat(),
            company_position,
            _as_utc(surfaced_at).isoformat(),
            opportunity_id,
        ]
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _recommended_today_candidates(
    session: Session,
    *,
    filters: list[Any],
    snapshot_at: datetime,
    assessment_context: _OpportunityAssessmentContext,
) -> list[_RecommendedTodayCandidate]:
    """Load every candidate's ranking inputs in bounded bulk queries."""

    opportunity_rows = list(
        session.execute(
            select(OwnerOpportunity, JobPosting)
            .join(
                JobPosting,
                (JobPosting.owner_id == OwnerOpportunity.owner_id)
                & (JobPosting.id == OwnerOpportunity.job_posting_id),
            )
            .where(*filters)
            .where(OwnerOpportunity.last_surfaced_at <= snapshot_at)
        )
    )
    posting_ids = [opportunity.job_posting_id for opportunity, _posting in opportunity_rows]
    if not posting_ids:
        return []

    latest_versions: dict[str, JobPostingVersion] = {}
    for version in session.scalars(
        select(JobPostingVersion)
        .where(
            JobPostingVersion.owner_id == assessment_context.owner_id,
            JobPostingVersion.job_posting_id.in_(posting_ids),
        )
        .order_by(
            JobPostingVersion.job_posting_id,
            JobPostingVersion.version_number.desc(),
        )
    ):
        latest_versions.setdefault(version.job_posting_id, version)
    assessment_context.preload_fit_rows(
        [version.id for version in latest_versions.values()]
    )

    matches_by_posting: dict[
        str,
        list[tuple[SavedSearchMatch, SavedSearch]],
    ] = {}
    for match, search in session.execute(
        select(SavedSearchMatch, SavedSearch)
        .join(
            SavedSearch,
            (SavedSearch.owner_id == SavedSearchMatch.owner_id)
            & (SavedSearch.id == SavedSearchMatch.saved_search_id),
        )
        .where(
            SavedSearchMatch.owner_id == assessment_context.owner_id,
            SavedSearchMatch.job_posting_id.in_(posting_ids),
        )
        .order_by(
            SavedSearchMatch.job_posting_id,
            SavedSearchMatch.first_matched_at,
            SavedSearchMatch.id,
        )
    ):
        matches_by_posting.setdefault(match.job_posting_id, []).append((match, search))

    preference_profile = _revealed_preference_profile(
        session,
        owner_id=assessment_context.owner_id,
        snapshot_at=snapshot_at,
    )
    candidates: list[_RecommendedTodayCandidate] = []
    for opportunity, posting in opportunity_rows:
        version = latest_versions.get(opportunity.job_posting_id)
        if version is None:
            raise OpportunityRepositoryError("opportunity posting graph is incomplete")
        match_rows = tuple(matches_by_posting.get(opportunity.job_posting_id, []))
        candidates.append(
            _RecommendedTodayCandidate(
                opportunity=opportunity,
                posting=posting,
                version=version,
                match_rows=match_rows,
                match=assessment_context.assess(
                    version=version,
                    match_rows=list(match_rows),
                ),
                recency=_posting_recency(
                    posting=posting,
                    version=version,
                    snapshot_at=snapshot_at,
                ),
                preference=_revealed_preference_signal(
                    title=version.title,
                    profile=preference_profile,
                ),
            )
        )
    return candidates


def _posting_recency(
    *,
    posting: JobPosting,
    version: JobPostingVersion,
    snapshot_at: datetime,
) -> _PostingRecency:
    """Categorize listing age without letting time drift between cursor pages."""

    snapshot_date = _as_utc(snapshot_at).date()
    source_date = _parse_posted_date(version.posted_at_text)
    if source_date is not None and source_date <= snapshot_date:
        effective_date = source_date
        source = "source_posted_date"
    else:
        # An unknown or impossible future source date is not treated as stale
        # or boosted as newly posted. The conservative fallback only ages from
        # when this owner first saw it.
        effective_date = _as_utc(posting.first_confirmed_at).date()
        source = "first_confirmed_at"
    age_days = max(0, (snapshot_date - effective_date).days)
    if age_days <= 7:
        freshness_priority = 0
    elif age_days <= 21:
        freshness_priority = 1
    elif age_days <= 45:
        freshness_priority = 2
    else:
        freshness_priority = 3
    return _PostingRecency(
        age_days=age_days,
        source=source,
        stale_priority=int(age_days > 45),
        freshness_priority=freshness_priority,
    )


def _title_role_tags(title: str) -> tuple[str, ...]:
    """Return explicit, human-readable role categories found in a public title."""

    return tuple(
        tag for tag, pattern in _TITLE_ROLE_TAG_PATTERNS if pattern.search(title)
    )


def _revealed_preference_profile(
    session: Session,
    *,
    owner_id: str,
    snapshot_at: datetime,
) -> _RevealedPreferenceProfile:
    """Learn only from the latest decisive event per opportunity at the snapshot.

    Watching and pursuing are positive examples. Only preference-shaped dismiss
    reasons are negative examples; location, compensation, duplicate, invalid,
    and already-applied dismissals must not teach a false dislike of a role type.
    """

    ranked_events = (
        select(
            OpportunityDecisionEventRow.owner_id.label("owner_id"),
            OpportunityDecisionEventRow.owner_opportunity_id.label(
                "owner_opportunity_id"
            ),
            OpportunityDecisionEventRow.job_posting_id.label("job_posting_id"),
            OpportunityDecisionEventRow.posting_version_id.label(
                "posting_version_id"
            ),
            OpportunityDecisionEventRow.new_decision.label("new_decision"),
            OpportunityDecisionEventRow.reason_code.label("reason_code"),
            func.row_number()
            .over(
                partition_by=OpportunityDecisionEventRow.owner_opportunity_id,
                order_by=(
                    OpportunityDecisionEventRow.occurred_at.desc(),
                    OpportunityDecisionEventRow.created_at.desc(),
                    OpportunityDecisionEventRow.id.desc(),
                ),
            )
            .label("event_rank"),
        )
        .where(
            OpportunityDecisionEventRow.owner_id == owner_id,
            OpportunityDecisionEventRow.occurred_at <= snapshot_at,
        )
        .subquery()
    )
    examples: list[tuple[str, tuple[str, ...]]] = []
    positive_count = 0
    negative_count = 0
    for decision, reason_code, title in session.execute(
        select(
            ranked_events.c.new_decision,
            ranked_events.c.reason_code,
            JobPostingVersion.title,
        )
        .join(
            JobPostingVersion,
            (JobPostingVersion.owner_id == ranked_events.c.owner_id)
            & (JobPostingVersion.job_posting_id == ranked_events.c.job_posting_id)
            & (JobPostingVersion.id == ranked_events.c.posting_version_id),
        )
        .where(ranked_events.c.event_rank == 1)
    ):
        tags = _title_role_tags(title)
        if not tags:
            continue
        if decision in {"watch", "pursued"}:
            examples.append(("positive", tags))
            positive_count += 1
        elif decision == "dismiss" and reason_code in _PREFERENCE_NEGATIVE_REASONS:
            examples.append(("negative", tags))
            negative_count += 1

    if (
        positive_count < _PREFERENCE_MIN_POSITIVE_DECISIONS
        or negative_count < _PREFERENCE_MIN_NEGATIVE_DECISIONS
    ):
        return _RevealedPreferenceProfile()

    counts: dict[str, list[int]] = {}
    for outcome, tags in examples:
        outcome_index = 0 if outcome == "positive" else 1
        for tag in tags:
            counts.setdefault(tag, [0, 0])[outcome_index] += 1

    preferred: set[str] = set()
    deprioritized: set[str] = set()
    for tag, (positive, negative) in counts.items():
        if (
            positive >= _PREFERENCE_MIN_POSITIVE_DECISIONS
            and positive - negative >= _PREFERENCE_MIN_TAG_MARGIN
        ):
            preferred.add(tag)
        elif (
            negative >= _PREFERENCE_MIN_NEGATIVE_DECISIONS
            and negative - positive >= _PREFERENCE_MIN_TAG_MARGIN
        ):
            deprioritized.add(tag)
    return _RevealedPreferenceProfile(
        preferred_tags=frozenset(preferred),
        deprioritized_tags=frozenset(deprioritized),
    )


def _revealed_preference_signal(
    *,
    title: str,
    profile: _RevealedPreferenceProfile,
) -> _RevealedPreferenceSignal:
    tags = frozenset(_title_role_tags(title))
    preferred = tuple(sorted(tags & profile.preferred_tags))
    deprioritized = tuple(sorted(tags & profile.deprioritized_tags))
    if preferred and not deprioritized:
        priority = 0
    elif deprioritized and not preferred:
        priority = 2
    else:
        # Sparse, unseen, generic, and conflicting title evidence is neutral.
        priority = 1
    return _RevealedPreferenceSignal(
        priority=priority,
        preferred_tags=preferred,
        deprioritized_tags=deprioritized,
    )


def _recommended_today_items(
    session: Session,
    *,
    candidates: list[_RecommendedTodayCandidate],
    keyring: DataKeyring,
) -> list[TodayOpportunityItem]:
    """Build one page from two bulk reads and the already-ranked public graph."""

    if not candidates:
        return []
    version_ids = [candidate.version.id for candidate in candidates]
    observations: dict[str, JobObservation] = {}
    for observation in session.scalars(
        select(JobObservation)
        .where(
            JobObservation.owner_id == candidates[0].opportunity.owner_id,
            JobObservation.job_posting_version_id.in_(version_ids),
        )
        .order_by(
            JobObservation.job_posting_version_id,
            JobObservation.observed_at.desc(),
            JobObservation.id.desc(),
        )
    ):
        observations.setdefault(observation.job_posting_version_id, observation)

    opportunity_ids = [candidate.opportunity.id for candidate in candidates]
    events: dict[str, OpportunityDecisionEventRow] = {}
    for decision_event in session.scalars(
        select(OpportunityDecisionEventRow)
        .where(
            OpportunityDecisionEventRow.owner_id
            == candidates[0].opportunity.owner_id,
            OpportunityDecisionEventRow.owner_opportunity_id.in_(opportunity_ids),
        )
        .order_by(
            OpportunityDecisionEventRow.owner_opportunity_id,
            OpportunityDecisionEventRow.occurred_at.desc(),
            OpportunityDecisionEventRow.created_at.desc(),
            OpportunityDecisionEventRow.id.desc(),
        )
    ):
        events.setdefault(decision_event.owner_opportunity_id, decision_event)

    items: list[TodayOpportunityItem] = []
    for candidate in candidates:
        observation = observations.get(candidate.version.id)
        if observation is None:
            raise OpportunityRepositoryError("posting version has no observation")
        items.append(
            _today_item_from_graph(
                opportunity=candidate.opportunity,
                posting=candidate.posting,
                latest=candidate.version,
                latest_observation=observation,
                match_rows=list(candidate.match_rows),
                latest_event=events.get(candidate.opportunity.id),
                match=candidate.match,
                keyring=keyring,
                recommendation=_today_recommendation_signals(candidate),
            )
        )
    return items


def _today_recommendation_signals(
    candidate: _RecommendedTodayCandidate,
) -> TodayRecommendationSignals:
    if candidate.recency.freshness_priority == 0:
        recency = "recent"
    elif candidate.recency.freshness_priority == 1:
        recency = "current"
    elif candidate.recency.freshness_priority == 2:
        recency = "aging"
    else:
        recency = "older_than_45_days"

    if candidate.preference.priority == 0:
        preference = "preferred"
        tags = list(candidate.preference.preferred_tags)
    elif candidate.preference.priority == 2:
        preference = "deprioritized"
        tags = list(candidate.preference.deprioritized_tags)
    else:
        preference = "neutral"
        tags = []
    return TodayRecommendationSignals(
        recency=recency,
        age_days=candidate.recency.age_days,
        age_basis=candidate.recency.source,
        preference=preference,
        preference_role_tags=tags,
    )


def _recommended_priority(candidate: _RecommendedTodayCandidate) -> tuple[int, ...]:
    """Return an inspectable, categorical recommendation order.

    No score is invented here: each component is already visible on the role
    card. Actionability, eligibility, fit band, and confidence remain hard gates.
    Within an equal assessment tier, listing recency and a conservative learned
    title preference are tie-breakers. Missing assessment or preference data is
    deliberately neutral instead of being guessed as a match.
    """

    posting_state = {"open": 0, "unknown": 1, "closed": 2}[
        candidate.posting.lifecycle_state
    ]
    decision_state = {"inbox": 0, "watch": 1, "pursued": 2, "dismiss": 3}[
        candidate.opportunity.decision
    ]
    match = candidate.match
    if match.state is MatchAssessmentState.not_assessed:
        return (
            posting_state,
            decision_state,
            1,
            3,
            2,
            candidate.recency.stale_priority,
            candidate.recency.freshness_priority,
            candidate.preference.priority,
            1,
        )
    assert match.eligibility is not None
    assert match.fit_band is not None
    assert match.confidence is not None
    eligibility = {
        OpportunityEligibility.eligible: 0,
        OpportunityEligibility.uncertain: 1,
        OpportunityEligibility.likely_ineligible: 2,
    }[match.eligibility]
    fit = {
        OpportunityFitBand.strong: 0,
        OpportunityFitBand.promising: 1,
        OpportunityFitBand.stretch: 2,
        OpportunityFitBand.insufficient_data: 3,
        OpportunityFitBand.low: 4,
    }[match.fit_band]
    confidence = {
        AssessmentConfidence.high: 0,
        AssessmentConfidence.medium: 1,
        AssessmentConfidence.low: 2,
    }[match.confidence]
    return (
        posting_state,
        decision_state,
        eligibility,
        fit,
        confidence,
        candidate.recency.stale_priority,
        candidate.recency.freshness_priority,
        candidate.preference.priority,
        0,
    )


def _rank_recommended_today(
    candidates: list[_RecommendedTodayCandidate],
) -> list[_RecommendedTodayCandidate]:
    """Rank first by recommendation tier, then diversify companies in that tier."""

    tiers: dict[
        tuple[int, ...],
        dict[str, list[_RecommendedTodayCandidate]],
    ] = {}
    for candidate in candidates:
        tiers.setdefault(_recommended_priority(candidate), {}).setdefault(
            candidate.posting.company_slug,
            [],
        ).append(candidate)

    ordered: list[_RecommendedTodayCandidate] = []
    for priority in sorted(tiers):
        companies = tiers[priority]
        for company_candidates in companies.values():
            company_candidates.sort(
                key=lambda candidate: (
                    _as_utc(candidate.opportunity.last_surfaced_at),
                    candidate.opportunity.id,
                ),
                reverse=True,
            )
        position = 0
        while True:
            company_round = [
                company_candidates[position]
                for company_candidates in companies.values()
                if position < len(company_candidates)
            ]
            if not company_round:
                break
            company_round.sort(
                key=lambda candidate: (
                    _as_utc(candidate.opportunity.last_surfaced_at),
                    candidate.opportunity.id,
                ),
                reverse=True,
            )
            ordered.extend(company_round)
            position += 1
    return ordered


def _recommended_query_fingerprint(query: TodayQuery) -> str:
    raw = _canonical_json(
        {
            "view": query.view.value,
            "sort": query.sort.value,
            "scan_id": query.scan_id,
            "saved_search_id": query.saved_search_id,
            "lane": query.lane.value if query.lane is not None else None,
        }
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _recommended_ordering_fingerprint(
    ordered: list[_RecommendedTodayCandidate],
) -> str:
    raw = _canonical_json(
        [
            [
                candidate.opportunity.id,
                _as_utc(candidate.opportunity.last_surfaced_at).isoformat(),
                list(_recommended_priority(candidate)),
                [
                    candidate.recency.age_days,
                    candidate.recency.source,
                ],
                [
                    list(candidate.preference.preferred_tags),
                    list(candidate.preference.deprioritized_tags),
                ],
                candidate.match.algorithm_version,
                candidate.match.assessment_input_fingerprint,
                candidate.match.not_assessed_reason.value
                if candidate.match.not_assessed_reason is not None
                else None,
            ]
            for candidate in ordered
        ]
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _encode_recommended_cursor(
    *,
    snapshot_at: datetime,
    offset: int,
    ordering_fingerprint: str,
    query_fingerprint: str,
) -> str:
    raw = _canonical_json(
        [
            "fit_company_round_robin_v1",
            _as_utc(snapshot_at).isoformat(),
            offset,
            ordering_fingerprint,
            query_fingerprint,
        ]
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(
    value: str,
) -> _LegacyTodayCursor | _DiverseTodayCursor | _RecommendedTodayCursor:
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if not isinstance(decoded, list):
            raise ValueError("cursor is invalid")
        if len(decoded) == 2:
            timestamp, opportunity_id = decoded
            return _LegacyTodayCursor(
                surfaced_at=_as_utc(datetime.fromisoformat(timestamp)),
                opportunity_id=_valid_cursor_opportunity_id(opportunity_id),
            )
        if len(decoded) == 5 and decoded[0] == "company_round_robin_v1":
            _, snapshot, company_position, timestamp, opportunity_id = decoded
            if (
                not isinstance(company_position, int)
                or isinstance(company_position, bool)
                or company_position < 1
            ):
                raise ValueError("cursor is invalid")
            return _DiverseTodayCursor(
                snapshot_at=_as_utc(datetime.fromisoformat(snapshot)),
                company_position=company_position,
                surfaced_at=_as_utc(datetime.fromisoformat(timestamp)),
                opportunity_id=_valid_cursor_opportunity_id(opportunity_id),
            )
        if len(decoded) == 5 and decoded[0] == "fit_company_round_robin_v1":
            _, snapshot, offset, ordering_fingerprint, query_fingerprint = decoded
            if (
                not isinstance(offset, int)
                or isinstance(offset, bool)
                or offset < 1
                or not _is_sha256(ordering_fingerprint)
                or not _is_sha256(query_fingerprint)
            ):
                raise ValueError("cursor is invalid")
            return _RecommendedTodayCursor(
                snapshot_at=_as_utc(datetime.fromisoformat(snapshot)),
                offset=offset,
                ordering_fingerprint=ordering_fingerprint,
                query_fingerprint=query_fingerprint,
            )
        raise ValueError("cursor is invalid")
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidTodayCursor("cursor is invalid") from exc


def _valid_cursor_opportunity_id(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("cursor is invalid")
    return value


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _trimmed(value: str | None, limit: int) -> str | None:
    if value is None or not value.strip():
        return None
    return value if len(value) <= limit else value[:limit].rstrip()


@dataclass(frozen=True)
class _AliasSpec:
    kind: str
    key: str
    key_hash: str
    source: str
    company_slug: str
    source_job_id: str | None
    normalized_url: str | None


def _find_or_create_posting(
    session: Session,
    *,
    owner_id: str,
    identity: PostingIdentity,
    aliases: list[_AliasSpec],
    now: datetime,
) -> tuple[JobPosting, bool]:
    posting = session.scalar(
        select(JobPosting).where(
            JobPosting.owner_id == owner_id,
            JobPosting.identity_key_hash == identity.key_hash,
        )
    )
    if posting is None:
        # A URL-identity posting may later gain a native source ID. Check the
        # exact incoming identity alias before considering URL enrichment.
        alias = session.scalar(
            select(JobPostingAlias).where(
                JobPostingAlias.owner_id == owner_id,
                JobPostingAlias.alias_key_hash == identity.key_hash,
            )
        )
        if alias is not None:
            posting = session.get(JobPosting, alias.job_posting_id)
    if posting is None:
        url_alias = next((spec for spec in aliases if spec.kind == "url"), None)
        alias = (
            session.scalar(
                select(JobPostingAlias).where(
                    JobPostingAlias.owner_id == owner_id,
                    JobPostingAlias.alias_key_hash == url_alias.key_hash,
                )
            )
            if url_alias is not None
            else None
        )
        candidate = session.get(JobPosting, alias.job_posting_id) if alias else None
        if candidate is not None and _can_merge_url_alias_candidate(
            session,
            candidate=candidate,
            identity=identity,
        ):
            posting = candidate
    if posting is not None:
        return posting, False

    candidate = JobPosting(
        owner_id=owner_id,
        identity_kind=identity.kind,
        identity_key=identity.key,
        identity_key_hash=identity.key_hash,
        source=identity.source,
        company_slug=identity.company_slug,
        source_job_id=identity.source_job_id,
        canonical_url=identity.canonical_url,
        lifecycle_state="open",
        consecutive_complete_omissions=0,
        first_confirmed_at=now,
        last_confirmed_at=now,
        version=1,
        created_at=now,
        updated_at=now,
    )
    try:
        with session.begin_nested():
            session.add(candidate)
            session.flush()
        return candidate, True
    except IntegrityError:
        posting = session.scalar(
            select(JobPosting)
            .where(
                JobPosting.owner_id == owner_id,
                JobPosting.identity_key_hash == identity.key_hash,
            )
            .with_for_update()
        )
        if posting is None:
            raise
        return posting, False


def _can_merge_url_alias_candidate(
    session: Session,
    *,
    candidate: JobPosting,
    identity: PostingIdentity,
) -> bool:
    if identity.kind == "url":
        return True
    if candidate.identity_kind == "native":
        return (
            candidate.source == identity.source
            and candidate.company_slug == identity.company_slug
            and candidate.source_job_id == identity.source_job_id
        )
    native_alias_hashes = set(
        session.scalars(
            select(JobPostingAlias.alias_key_hash).where(
                JobPostingAlias.owner_id == candidate.owner_id,
                JobPostingAlias.job_posting_id == candidate.id,
                JobPostingAlias.alias_kind == "native",
            )
        )
    )
    return not native_alias_hashes or identity.key_hash in native_alias_hashes


def _ensure_alias(
    session: Session,
    *,
    owner_id: str,
    posting: JobPosting,
    spec: _AliasSpec,
    now: datetime,
) -> JobPostingAlias:
    existing = session.scalar(
        select(JobPostingAlias).where(
            JobPostingAlias.owner_id == owner_id,
            JobPostingAlias.alias_key_hash == spec.key_hash,
        )
    )
    if existing is not None:
        if existing.job_posting_id != posting.id:
            raise PostingIdentityConflict("posting alias belongs to a different posting")
        existing.last_seen_at = max(_as_utc(existing.last_seen_at), now)
        return existing
    alias = JobPostingAlias(
        owner_id=owner_id,
        job_posting_id=posting.id,
        alias_kind=spec.kind,
        alias_key=spec.key,
        alias_key_hash=spec.key_hash,
        source=spec.source,
        company_slug=spec.company_slug,
        source_job_id=spec.source_job_id,
        normalized_url=spec.normalized_url,
        first_seen_at=now,
        last_seen_at=now,
        created_at=now,
    )
    try:
        with session.begin_nested():
            session.add(alias)
            session.flush()
        return alias
    except IntegrityError:
        existing = session.scalar(
            select(JobPostingAlias).where(
                JobPostingAlias.owner_id == owner_id,
                JobPostingAlias.alias_key_hash == spec.key_hash,
            )
        )
        if existing is None or existing.job_posting_id != posting.id:
            raise PostingIdentityConflict("concurrent posting alias conflict")
        return existing


def _upsert_saved_search_match(
    session: Session,
    *,
    owner_id: str,
    scan: OpportunityScan,
    posting: JobPosting,
    posting_version: JobPostingVersion,
    now: datetime,
) -> tuple[SavedSearchMatch, bool]:
    match = _owner_match(session, owner_id, scan.saved_search_id, posting.id)
    if match is not None:
        _advance_match(match, scan, posting_version, now)
        return match, False
    match = SavedSearchMatch(
        owner_id=owner_id,
        saved_search_id=scan.saved_search_id,
        job_posting_id=posting.id,
        first_scan_id=scan.id,
        last_scan_id=scan.id,
        last_posting_version_id=posting_version.id,
        match_count=1,
        first_matched_at=now,
        last_matched_at=now,
        created_at=now,
        updated_at=now,
    )
    try:
        with session.begin_nested():
            session.add(match)
            session.flush()
        return match, True
    except IntegrityError:
        existing = _owner_match(session, owner_id, scan.saved_search_id, posting.id)
        if existing is None:
            raise
        _advance_match(existing, scan, posting_version, now)
        return existing, False


def _advance_match(
    match: SavedSearchMatch,
    scan: OpportunityScan,
    posting_version: JobPostingVersion,
    now: datetime,
) -> None:
    if match.last_scan_id == scan.id:
        return
    match.match_count += 1
    if now >= _as_utc(match.last_matched_at):
        match.last_scan_id = scan.id
        match.last_posting_version_id = posting_version.id
        match.last_matched_at = now
    match.updated_at = max(_as_utc(match.updated_at), now)


def _upsert_owner_opportunity(
    session: Session,
    *,
    owner_id: str,
    posting: JobPosting,
    changed: bool,
    now: datetime,
) -> tuple[OwnerOpportunity, bool]:
    opportunity = _owner_opportunity(session, owner_id, posting.id)
    if opportunity is not None:
        if changed:
            opportunity.last_surfaced_at = max(
                _as_utc(opportunity.last_surfaced_at), now
            )
            opportunity.version += 1
            opportunity.updated_at = now
        return opportunity, False
    opportunity = OwnerOpportunity(
        owner_id=owner_id,
        job_posting_id=posting.id,
        decision="inbox",
        first_surfaced_at=now,
        last_surfaced_at=now,
        version=1,
        created_at=now,
        updated_at=now,
    )
    try:
        with session.begin_nested():
            session.add(opportunity)
            session.flush()
        return opportunity, True
    except IntegrityError:
        existing = _owner_opportunity(session, owner_id, posting.id)
        if existing is None:
            raise
        return existing, False


def _latest_posting_version(
    session: Session,
    *,
    owner_id: str,
    posting_id: str,
) -> JobPostingVersion | None:
    return session.scalar(
        select(JobPostingVersion)
        .where(
            JobPostingVersion.owner_id == owner_id,
            JobPostingVersion.job_posting_id == posting_id,
        )
        .order_by(
            JobPostingVersion.version_number.desc(),
            JobPostingVersion.created_at.desc(),
            JobPostingVersion.id.desc(),
        )
        .limit(1)
    )


def _owner_match(
    session: Session,
    owner_id: str,
    search_id: str,
    posting_id: str,
) -> SavedSearchMatch | None:
    return session.scalar(
        select(SavedSearchMatch)
        .where(
            SavedSearchMatch.owner_id == owner_id,
            SavedSearchMatch.saved_search_id == search_id,
            SavedSearchMatch.job_posting_id == posting_id,
        )
        .with_for_update()
    )


def _owner_opportunity(
    session: Session,
    owner_id: str,
    posting_id: str,
) -> OwnerOpportunity | None:
    return session.scalar(
        select(OwnerOpportunity)
        .where(
            OwnerOpportunity.owner_id == owner_id,
            OwnerOpportunity.job_posting_id == posting_id,
        )
        .with_for_update()
    )


def _public_role_snapshot(
    role: Role,
    canonical_url: str,
    apply_urls: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": role.source.value,
        "source_job_id": role.source_job_id,
        "company_name": role.company,
        "title": role.title,
        "canonical_url": canonical_url,
        "apply_urls": apply_urls,
        "location": role.location,
        "summary": role.summary,
        "description": role.raw_description,
        "employment_type": role.employment_type.value,
        "posted_at_text": role.posted_at,
        "source_updated_at_text": role.source_updated_at,
        "source_facts": {},
        "source_confidence": role.confidence,
    }


def _canonical_apply_urls(role: Role, canonical_url: str) -> list[str]:
    values = [canonical_url]
    for value in role.apply_urls:
        normalized = canonicalize_posting_url(value)
        if normalized not in values:
            values.append(normalized)
    return values


def _alias_specs(identity: PostingIdentity) -> list[_AliasSpec]:
    specs: list[_AliasSpec] = []
    if identity.kind == "native":
        specs.append(
            _AliasSpec(
                kind="native",
                key=identity.key,
                key_hash=identity.key_hash,
                source=identity.source,
                company_slug=identity.company_slug,
                source_job_id=identity.source_job_id,
                normalized_url=None,
            )
        )
    key = _identity_key("url", identity.company_slug, identity.canonical_url)
    specs.append(
        _AliasSpec(
            kind="url",
            key=key,
            key_hash=_sha256(key),
            source=identity.source,
            company_slug=identity.company_slug,
            source_job_id=None,
            normalized_url=identity.canonical_url,
        )
    )
    return list({spec.key_hash: spec for spec in specs}.values())


def _identity_key(*parts: str) -> str:
    return json.dumps(list(parts), ensure_ascii=False, separators=(",", ":"))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _decision_response(
    opportunity: OwnerOpportunity,
    event: OpportunityDecisionEventRow,
    keyring: DataKeyring,
) -> OpportunityDecisionResponse:
    decision_event = _decision_event_response(event, keyring)
    return OpportunityDecisionResponse(
        opportunity_id=opportunity.id,
        opportunity_version=opportunity.version,
        state=OpportunityDecisionState(opportunity.decision),
        event=decision_event,
    )


def _decision_event_response(
    row: OpportunityDecisionEventRow,
    keyring: DataKeyring,
) -> OpportunityDecisionEvent:
    note = None
    if row.encrypted_note is not None and row.note_key_id is not None:
        private = decrypt_private_payload(
            keyring,
            record_kind=_DECISION_NOTE_KIND,
            owner_id=row.owner_id,
            record_id=row.id,
            encryption_key_id=row.note_key_id,
            ciphertext=row.encrypted_note,
        )
        raw_note = private.get("note")
        if raw_note is not None and not isinstance(raw_note, str):
            raise OpportunityRepositoryError("decision note payload is invalid")
        note = raw_note
    if row.new_decision == "inbox":
        action = OpportunityDecisionAction.restore_to_inbox
    elif row.new_decision == "pursued":
        action = OpportunityDecisionAction.pursue
    else:
        action = OpportunityDecisionAction(row.new_decision)
    return OpportunityDecisionEvent(
        id=row.id,
        opportunity_id=row.owner_opportunity_id,
        action=action,
        previous_state=OpportunityDecisionState(row.previous_decision),
        state=OpportunityDecisionState(row.new_decision),
        dismiss_reason=DismissReason(row.reason_code) if row.reason_code else None,
        note=note,
        restores_event_id=row.compensates_event_id,
        created_at=_as_utc(row.occurred_at),
    )


__all__ = [
    "DecisionIdempotencyConflict",
    "OpportunityRepositoryError",
    "OpportunityNotFound",
    "PersistedRole",
    "PostingIdentity",
    "PostingIdentityConflict",
    "canonicalize_posting_url",
    "decide_owner_opportunity",
    "list_today_opportunities",
    "load_opportunity_detail",
    "posting_identity",
    "persist_scan_source_role",
]
