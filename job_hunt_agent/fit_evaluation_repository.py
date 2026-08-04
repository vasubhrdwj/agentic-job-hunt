"""Owner-scoped inputs and encrypted cache for hybrid opportunity fit.

Provider calls deliberately live outside this repository. Callers prepare one
immutable snapshot in a short transaction, make at most one provider request
without holding a database connection, then persist only a validated verdict in
a second short transaction.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .evidence_repository import list_approved_evidence_for_use
from .fit_evaluation import (
    FIT_EVALUATION_POLICY_VERSION,
    FitEvaluationAuthorization,
    FitEvaluationEvidence,
    FitEvaluationInput,
    FitEvaluationPosting,
    FitEvaluationProfile,
    FitEvaluationTarget,
    FitVerdict,
    ResolvedFitEvaluation,
    merge_fit_verdict,
)
from .job_queue import utcnow
from .models import (
    JobPostingVersion,
    OpportunityFitEvaluation,
    SavedSearch,
    SavedSearchMatch,
)
from .opportunity_assessment import (
    ASSESSMENT_ALGORITHM_VERSION,
    AssessmentAuthorization,
    AssessmentEvidence,
    AssessmentPosting,
    AssessmentProfile,
    AssessmentTarget,
    OpportunityAssessment,
    assess_opportunity,
)
from .private_payloads import decrypt_private_payload, encrypt_private_payload
from .profile_repository import (
    load_candidate_profile,
    load_career_track,
    load_resume_version,
)
from .security import DataKeyring


FIT_RESULT_SCHEMA_VERSION = 1
FIT_CACHE_RECORD_KIND = "opportunity_fit_evaluation"


class FitEvaluationUnavailable(ValueError):
    """The referenced owner snapshot is absent, stale, or not assessable."""


class FitEvaluationCacheError(RuntimeError):
    """A persisted fit verdict failed its binding or schema checks."""


@dataclass(frozen=True)
class FitEvaluatorIdentity:
    provider: str
    model: str
    prompt_version: str


@dataclass(frozen=True)
class PreparedFitEvaluation:
    owner_id: str
    job_posting_id: str
    posting_version_id: str
    saved_search_id: str
    posting_hash: str
    profile_input_fingerprint: str
    input_fingerprint: str
    evaluator_version: str
    identity: FitEvaluatorIdentity
    inputs: FitEvaluationInput
    deterministic: OpportunityAssessment


@dataclass(frozen=True)
class CachedFitVerdict:
    record_id: str
    verdict: FitVerdict
    created: bool


def prepare_fit_evaluation(
    session: Session,
    *,
    owner_id: str,
    posting_version_id: str,
    saved_search_id: str,
    identity: FitEvaluatorIdentity,
    keyring: DataKeyring,
) -> PreparedFitEvaluation:
    """Load and bind the exact private/public inputs for one evaluation."""

    normalized_identity = _normalized_identity(identity)
    posting = session.scalar(
        select(JobPostingVersion).where(
            JobPostingVersion.owner_id == owner_id,
            JobPostingVersion.id == posting_version_id,
        )
    )
    search = session.scalar(
        select(SavedSearch).where(
            SavedSearch.owner_id == owner_id,
            SavedSearch.id == saved_search_id,
        )
    )
    if posting is None or search is None:
        raise FitEvaluationUnavailable("fit evaluation references are unavailable")
    matched = session.scalar(
        select(SavedSearchMatch.id).where(
            SavedSearchMatch.owner_id == owner_id,
            SavedSearchMatch.saved_search_id == search.id,
            SavedSearchMatch.job_posting_id == posting.job_posting_id,
        )
    )
    if matched is None:
        raise FitEvaluationUnavailable("posting is not matched to the saved search")

    profile = load_candidate_profile(session, owner_id=owner_id, keyring=keyring)
    track = load_career_track(
        session,
        owner_id=owner_id,
        career_track_id=search.career_track_id,
    )
    resume = load_resume_version(
        session,
        owner_id=owner_id,
        resume_version_id=search.resume_version_id,
        keyring=keyring,
    )
    if profile is None or track is None or resume is None:
        raise FitEvaluationUnavailable("candidate fit inputs are unavailable")

    description = _assessment_description(posting.description)
    if description is None:
        raise FitEvaluationUnavailable("posting description is unavailable")
    approved = list_approved_evidence_for_use(
        session,
        owner_id=owner_id,
        keyring=keyring,
    )

    target = AssessmentTarget(
        role_families=tuple(track.data.role_families),
        seniority_levels=tuple(track.data.seniority_levels),
        target_locations=tuple(track.data.target_locations),
    )
    deterministic_profile = AssessmentProfile(
        current_location=profile.data.current_location,
        work_modes=tuple(profile.data.work_modes),
        employment_types=tuple(profile.data.employment_types),
        years_of_experience=profile.data.years_of_experience,
        work_authorizations=tuple(
            AssessmentAuthorization(
                country_code=item.country_code,
                status=item.status,
            )
            for item in profile.data.work_authorizations
        ),
    )
    deterministic_evidence = tuple(
        AssessmentEvidence(
            id=item.id,
            statement=item.statement,
            skills=tuple(item.skills),
        )
        for item in approved
    )
    assessment_posting = AssessmentPosting(
        title=posting.title,
        description=description,
        location=_known_location(posting.location),
        employment_type=(
            posting.employment_type
            if posting.employment_type != "unknown"
            else None
        ),
    )
    deterministic = assess_opportunity(
        posting=assessment_posting,
        target=target,
        profile=deterministic_profile,
        resume_text=resume.content,
        evidence=deterministic_evidence,
    )
    inputs = FitEvaluationInput(
        posting=FitEvaluationPosting(
            title=posting.title,
            description=description,
            location=assessment_posting.location,
            employment_type=assessment_posting.employment_type,
        ),
        target=FitEvaluationTarget(
            role_families=target.role_families,
            seniority_levels=target.seniority_levels,
            target_locations=target.target_locations,
        ),
        profile=FitEvaluationProfile(
            career_thesis=profile.data.career_thesis,
            current_title=profile.data.current_title,
            current_location=profile.data.current_location,
            years_of_experience=profile.data.years_of_experience,
            skills=tuple(profile.data.skills),
            work_authorizations=tuple(
                FitEvaluationAuthorization(
                    country_code=item.country_code,
                    status=item.status,
                )
                for item in profile.data.work_authorizations
            ),
            work_modes=tuple(profile.data.work_modes),
            employment_types=tuple(profile.data.employment_types),
        ),
        evidence=tuple(
            FitEvaluationEvidence(
                id=item.id,
                statement=item.statement,
                skills=tuple(item.skills),
            )
            for item in approved
        ),
    )
    profile_fingerprint = _sha256_json(
        {
            "saved_search": [search.id, search.version],
            "career_track": [track.id, track.version],
            "resume": [
                resume.metadata.id,
                resume.metadata.version,
                resume.metadata.content_hash,
            ],
            "candidate_profile": [profile.id, profile.version],
            "evidence_versions": [
                [item.id, item.version]
                for item in approved
            ],
            # Hash the validated values as a defense against any future writer
            # that accidentally forgets to advance a row version.
            "private_inputs": {
                "target": inputs.target.model_dump(mode="json"),
                "profile": inputs.profile.model_dump(mode="json"),
                "evidence": [
                    item.model_dump(mode="json")
                    for item in inputs.evidence
                ],
            },
        }
    )
    evaluator_version = _evaluator_version(normalized_identity)
    input_fingerprint = _sha256_json(
        {
            "owner_scope": owner_id,
            "posting": [
                posting.job_posting_id,
                posting.id,
                posting.version_number,
                posting.content_hash,
            ],
            "profile_input_fingerprint": profile_fingerprint,
            "deterministic_algorithm": ASSESSMENT_ALGORITHM_VERSION,
            "fit_policy": FIT_EVALUATION_POLICY_VERSION,
            "provider": normalized_identity.provider,
            "model": normalized_identity.model,
            "prompt_version": normalized_identity.prompt_version,
            "result_schema_version": FIT_RESULT_SCHEMA_VERSION,
        }
    )
    return PreparedFitEvaluation(
        owner_id=owner_id,
        job_posting_id=posting.job_posting_id,
        posting_version_id=posting.id,
        saved_search_id=search.id,
        posting_hash=posting.content_hash,
        profile_input_fingerprint=profile_fingerprint,
        input_fingerprint=input_fingerprint,
        evaluator_version=evaluator_version,
        identity=normalized_identity,
        inputs=inputs,
        deterministic=deterministic,
    )


def load_cached_fit_verdict(
    session: Session,
    *,
    prepared: PreparedFitEvaluation,
    keyring: DataKeyring,
) -> CachedFitVerdict | None:
    row = session.scalar(
        select(OpportunityFitEvaluation).where(
            OpportunityFitEvaluation.owner_id == prepared.owner_id,
            OpportunityFitEvaluation.input_fingerprint == prepared.input_fingerprint,
        )
    )
    if row is None:
        return None
    _require_row_matches(row, prepared)
    try:
        private = decrypt_private_payload(
            keyring,
            record_kind=FIT_CACHE_RECORD_KIND,
            owner_id=row.owner_id,
            record_id=row.id,
            encryption_key_id=row.encryption_key_id,
            ciphertext=row.encrypted_payload,
        )
        verdict = FitVerdict.model_validate(private.get("verdict"))
        # Re-run grounding checks whenever a cached result is consumed.
        merge_fit_verdict(
            deterministic=prepared.deterministic,
            inputs=prepared.inputs,
            verdict=verdict,
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise FitEvaluationCacheError("cached fit verdict is invalid") from exc
    return CachedFitVerdict(record_id=row.id, verdict=verdict, created=False)


def store_fit_verdict(
    session: Session,
    *,
    prepared: PreparedFitEvaluation,
    verdict: FitVerdict,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> CachedFitVerdict:
    """Persist a validated raw verdict once; never store fallback/error output."""

    # This rejects unknown evidence IDs and applies the same safety gate used
    # by the read path before any encrypted cache row is created.
    merge_fit_verdict(
        deterministic=prepared.deterministic,
        inputs=prepared.inputs,
        verdict=verdict,
    )
    existing = load_cached_fit_verdict(
        session,
        prepared=prepared,
        keyring=keyring,
    )
    if existing is not None:
        return existing

    record_id = uuid4().hex
    envelope = encrypt_private_payload(
        keyring,
        record_kind=FIT_CACHE_RECORD_KIND,
        owner_id=prepared.owner_id,
        record_id=record_id,
        payload={"verdict": verdict.model_dump(mode="json")},
    )
    row = OpportunityFitEvaluation(
        id=record_id,
        owner_id=prepared.owner_id,
        job_posting_id=prepared.job_posting_id,
        posting_version_id=prepared.posting_version_id,
        posting_hash=prepared.posting_hash,
        profile_input_fingerprint=prepared.profile_input_fingerprint,
        input_fingerprint=prepared.input_fingerprint,
        evaluator_version=prepared.evaluator_version,
        provider=prepared.identity.provider,
        model=prepared.identity.model,
        result_schema_version=FIT_RESULT_SCHEMA_VERSION,
        encrypted_payload=envelope.ciphertext,
        encryption_key_id=envelope.key_id,
        version=1,
        created_at=now or utcnow(),
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        concurrent = load_cached_fit_verdict(
            session,
            prepared=prepared,
            keyring=keyring,
        )
        if concurrent is None:
            raise
        return concurrent
    return CachedFitVerdict(record_id=row.id, verdict=verdict, created=True)


def resolve_cached_fit(
    session: Session,
    *,
    prepared: PreparedFitEvaluation,
    keyring: DataKeyring,
) -> ResolvedFitEvaluation | None:
    cached = load_cached_fit_verdict(session, prepared=prepared, keyring=keyring)
    if cached is None:
        return None
    return merge_fit_verdict(
        deterministic=prepared.deterministic,
        inputs=prepared.inputs,
        verdict=cached.verdict,
    )


def _normalized_identity(identity: FitEvaluatorIdentity) -> FitEvaluatorIdentity:
    values = (
        identity.provider.strip(),
        identity.model.strip(),
        identity.prompt_version.strip(),
    )
    if any(not value for value in values):
        raise ValueError("fit evaluator identity values must not be blank")
    if len(values[0]) > 64 or len(values[1]) > 120 or len(values[2]) > 64:
        raise ValueError("fit evaluator identity value is too long")
    return FitEvaluatorIdentity(*values)


def _evaluator_version(identity: FitEvaluatorIdentity) -> str:
    digest = _sha256_json(
        {
            "deterministic": ASSESSMENT_ALGORITHM_VERSION,
            "policy": FIT_EVALUATION_POLICY_VERSION,
            "prompt": identity.prompt_version,
        }
    )[:16]
    return f"hybrid-fit-v1-{digest}"


def _require_row_matches(
    row: OpportunityFitEvaluation,
    prepared: PreparedFitEvaluation,
) -> None:
    expected = (
        prepared.job_posting_id,
        prepared.posting_version_id,
        prepared.posting_hash,
        prepared.profile_input_fingerprint,
        prepared.evaluator_version,
        prepared.identity.provider,
        prepared.identity.model,
        FIT_RESULT_SCHEMA_VERSION,
        1,
    )
    actual = (
        row.job_posting_id,
        row.posting_version_id,
        row.posting_hash,
        row.profile_input_fingerprint,
        row.evaluator_version,
        row.provider,
        row.model,
        row.result_schema_version,
        row.version,
    )
    if actual != expected:
        raise FitEvaluationCacheError("cached fit verdict metadata is inconsistent")


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


def _known_location(value: str) -> str | None:
    normalized = value.strip()
    if not normalized or normalized.casefold() == "location not specified":
        return None
    return normalized


def _sha256_json(payload: object) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = [
    "FIT_CACHE_RECORD_KIND",
    "FIT_RESULT_SCHEMA_VERSION",
    "CachedFitVerdict",
    "FitEvaluationCacheError",
    "FitEvaluationUnavailable",
    "FitEvaluatorIdentity",
    "PreparedFitEvaluation",
    "load_cached_fit_verdict",
    "prepare_fit_evaluation",
    "resolve_cached_fit",
    "store_fit_verdict",
]
