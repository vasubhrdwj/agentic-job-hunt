"""Durable queue plumbing for optional opportunity-fit model evaluation.

Role scans only enqueue opaque posting references.  The actual model/cache
adapter is injected by the worker so provider work happens after the claim
transaction has closed and while the generic renewable lease is active.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import env_bool
from .database import Database
from .job_queue import EnqueueResult, complete_job, enqueue_job, lock_owned_running_job
from .fit_evaluation import FIT_EVALUATION_POLICY_VERSION
from .gemini_fit_provider import DEFAULT_FIT_MODEL, FIT_PROMPT_VERSION, FIT_PROVIDER_NAME
from .models import (
    AchievementEvidence,
    BackgroundJob,
    CandidateProfile,
    CareerTrack,
    ResumeVersion,
    SavedSearch,
    SavedSearchMatch,
)
from .opportunity_assessment import ASSESSMENT_ALGORITHM_VERSION
from .security import DataKeyring


FIT_EVALUATION_JOB_KIND = "evaluate_opportunity_fit"
FIT_EVALUATION_JOB_PRIORITY = 110
FIT_EVALUATION_SUBJECT_TYPE = "job_posting_version"
FIT_EVALUATION_ENABLED_ENV = "ENABLE_LLM_FIT_EVALUATION"
DEFAULT_FIT_BACKFILL_BATCH_SIZE = 20
MAX_FIT_BACKFILL_CANDIDATES = 500


class OpportunityFitClaim(Protocol):
    job_id: str
    posting_id: str
    posting_version_id: str
    saved_search_id: str
    lease_token: str


class OpportunityFitJobHandler(Protocol):
    """Provider/cache adapter invoked without an open claim transaction.

    The adapter may use short database sessions to load and publish data, but
    it must perform the provider call between those sessions.  It should use
    the claim's lease identifiers to guard any derived cache write.
    """

    def __call__(
        self,
        claim: OpportunityFitClaim,
        *,
        database: Database,
        worker_id: str,
        keyring: DataKeyring,
    ) -> "OpportunityFitJobOutcome": ...


@dataclass(frozen=True)
class OpportunityFitJobOutcome:
    """Non-sensitive outcome metadata used only for queue observability."""

    cache_written: bool


@dataclass(frozen=True)
class OpportunityFitBackfillBatch:
    inspected_count: int
    enqueued_count: int


DETERMINISTIC_FALLBACK_OUTCOME = OpportunityFitJobOutcome(
    cache_written=False,
)


def fit_evaluation_jobs_enabled() -> bool:
    """Return whether new posting versions should enqueue optional model work."""

    return env_bool(FIT_EVALUATION_ENABLED_ENV, default=False)


def enqueue_opportunity_fit_evaluation(
    session: Session,
    *,
    owner_id: str,
    posting_id: str,
    posting_version_id: str,
    saved_search_id: str,
    profile_revision_token: str | None = None,
    enabled: bool | None = None,
) -> EnqueueResult | None:
    """Enqueue one owner-scoped evaluation for an immutable posting version.

    Callers are responsible for invoking this only when that posting version
    was newly created.  The feature flag prevents queue churn while the
    optional model integration is disabled.
    """

    should_enqueue = fit_evaluation_jobs_enabled() if enabled is None else enabled
    if not should_enqueue:
        return None
    revision_token = profile_revision_token or fit_profile_revision_token(
        session,
        owner_id=owner_id,
        saved_search_id=saved_search_id,
    )
    return enqueue_job(
        session,
        kind=FIT_EVALUATION_JOB_KIND,
        dedupe_key=_fit_job_dedupe_key(
            posting_version_id=posting_version_id,
            saved_search_id=saved_search_id,
            profile_revision_token=revision_token,
        ),
        owner_id=owner_id,
        subject_type=FIT_EVALUATION_SUBJECT_TYPE,
        subject_id=posting_version_id,
        payload={
            "job_posting_id": posting_id,
            "posting_version_id": posting_version_id,
            "saved_search_id": saved_search_id,
        },
        priority=FIT_EVALUATION_JOB_PRIORITY,
        max_attempts=3,
        actor="opportunity-scan",
    )


def fit_profile_revision_token(
    session: Session,
    *,
    owner_id: str,
    saved_search_id: str,
) -> str:
    """Hash cleartext row versions that can change a private fit snapshot."""

    search = session.scalar(
        select(SavedSearch).where(
            SavedSearch.owner_id == owner_id,
            SavedSearch.id == saved_search_id,
        )
    )
    if search is None:
        raise ValueError("saved search is unavailable for fit evaluation")
    track_version = session.scalar(
        select(CareerTrack.version).where(
            CareerTrack.owner_id == owner_id,
            CareerTrack.id == search.career_track_id,
        )
    )
    resume = session.execute(
        select(ResumeVersion.version, ResumeVersion.content_hash).where(
            ResumeVersion.owner_id == owner_id,
            ResumeVersion.id == search.resume_version_id,
        )
    ).one_or_none()
    profile = session.execute(
        select(CandidateProfile.id, CandidateProfile.version).where(
            CandidateProfile.owner_id == owner_id,
        )
    ).one_or_none()
    evidence_versions = list(
        session.execute(
            select(AchievementEvidence.id, AchievementEvidence.version)
            .where(
                AchievementEvidence.owner_id == owner_id,
                AchievementEvidence.approval_state == "approved",
            )
            .order_by(AchievementEvidence.id)
        )
    )
    payload = {
        "saved_search": [search.id, search.version],
        "career_track": [search.career_track_id, track_version],
        "resume": [
            search.resume_version_id,
            resume[0] if resume is not None else None,
            resume[1] if resume is not None else None,
        ],
        "candidate_profile": list(profile) if profile is not None else None,
        "evidence_versions": [list(item) for item in evidence_versions],
        "deterministic_algorithm": ASSESSMENT_ALGORITHM_VERSION,
        "fit_policy": FIT_EVALUATION_POLICY_VERSION,
        "provider": FIT_PROVIDER_NAME,
        "model": os.getenv("GEMINI_FIT_MODEL", "").strip() or DEFAULT_FIT_MODEL,
        "prompt_version": FIT_PROMPT_VERSION,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(canonical).hexdigest()


def enqueue_missing_opportunity_fit_evaluations(
    session: Session,
    *,
    limit: int = DEFAULT_FIT_BACKFILL_BATCH_SIZE,
) -> OpportunityFitBackfillBatch:
    """Backfill existing matches and re-evaluate them after profile changes."""

    if limit < 1 or limit > MAX_FIT_BACKFILL_CANDIDATES:
        raise ValueError("fit backfill limit is outside the supported range")
    if not fit_evaluation_jobs_enabled():
        return OpportunityFitBackfillBatch(inspected_count=0, enqueued_count=0)
    matches = list(
        session.execute(
            select(SavedSearchMatch, SavedSearch)
            .join(
                SavedSearch,
                (SavedSearch.owner_id == SavedSearchMatch.owner_id)
                & (SavedSearch.id == SavedSearchMatch.saved_search_id),
            )
            .where(SavedSearch.active.is_(True))
            .order_by(
                SavedSearchMatch.last_matched_at.desc(),
                SavedSearchMatch.id.desc(),
            )
            .limit(MAX_FIT_BACKFILL_CANDIDATES)
        )
    )
    profile_tokens: dict[tuple[str, str], str] = {}
    candidates: list[tuple[SavedSearchMatch, str, str]] = []
    for match, search in matches:
        key = (match.owner_id, search.id)
        token = profile_tokens.get(key)
        if token is None:
            token = fit_profile_revision_token(
                session,
                owner_id=match.owner_id,
                saved_search_id=search.id,
            )
            profile_tokens[key] = token
        candidates.append(
            (
                match,
                token,
                _fit_job_dedupe_key(
                    posting_version_id=match.last_posting_version_id,
                    saved_search_id=search.id,
                    profile_revision_token=token,
                ),
            )
        )
    if not candidates:
        return OpportunityFitBackfillBatch(inspected_count=0, enqueued_count=0)
    existing = {
        (owner_id, dedupe_key)
        for owner_id, dedupe_key in session.execute(
            select(BackgroundJob.owner_id, BackgroundJob.dedupe_key).where(
                BackgroundJob.kind == FIT_EVALUATION_JOB_KIND,
                BackgroundJob.dedupe_key.in_([item[2] for item in candidates]),
            )
        )
    }
    enqueued_count = 0
    for match, token, dedupe_key in candidates:
        if (match.owner_id, dedupe_key) in existing:
            continue
        result = enqueue_opportunity_fit_evaluation(
            session,
            owner_id=match.owner_id,
            posting_id=match.job_posting_id,
            posting_version_id=match.last_posting_version_id,
            saved_search_id=match.saved_search_id,
            profile_revision_token=token,
            enabled=True,
        )
        if result is not None and result.created:
            enqueued_count += 1
            existing.add((match.owner_id, dedupe_key))
        if enqueued_count >= limit:
            break
    return OpportunityFitBackfillBatch(
        inspected_count=len(candidates),
        enqueued_count=enqueued_count,
    )


def _fit_job_dedupe_key(
    *,
    posting_version_id: str,
    saved_search_id: str,
    profile_revision_token: str,
) -> str:
    return (
        f"posting-version:{posting_version_id}:search:{saved_search_id}:"
        f"profile:{profile_revision_token}"
    )


def complete_opportunity_fit_evaluation(
    database: Database,
    claim: OpportunityFitClaim,
    *,
    worker_id: str,
    outcome: OpportunityFitJobOutcome,
) -> bool:
    """Complete derived work only while the worker still owns its live lease."""

    with database.session() as session:
        owned = lock_owned_running_job(
            session,
            claim.job_id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
        )
        if owned is None:
            return False
        owned.stage = "fit_evaluation_complete"
        owned.stage_checkpoint = (
            "model_cache_written"
            if outcome.cache_written
            else "deterministic_fallback"
        )
        completed = complete_job(
            session,
            owned.id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
        )
        return completed is not None


__all__ = [
    "DETERMINISTIC_FALLBACK_OUTCOME",
    "FIT_EVALUATION_ENABLED_ENV",
    "FIT_EVALUATION_JOB_KIND",
    "FIT_EVALUATION_JOB_PRIORITY",
    "FIT_EVALUATION_SUBJECT_TYPE",
    "DEFAULT_FIT_BACKFILL_BATCH_SIZE",
    "MAX_FIT_BACKFILL_CANDIDATES",
    "OpportunityFitBackfillBatch",
    "OpportunityFitClaim",
    "OpportunityFitJobHandler",
    "OpportunityFitJobOutcome",
    "complete_opportunity_fit_evaluation",
    "enqueue_opportunity_fit_evaluation",
    "enqueue_missing_opportunity_fit_evaluations",
    "fit_profile_revision_token",
    "fit_evaluation_jobs_enabled",
]
