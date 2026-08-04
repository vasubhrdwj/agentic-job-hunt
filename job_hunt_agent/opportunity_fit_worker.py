"""Durable queue plumbing for optional opportunity-fit model evaluation.

Role scans only enqueue opaque posting references.  The actual model/cache
adapter is injected by the worker so provider work happens after the claim
transaction has closed and while the generic renewable lease is active.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from .config import env_bool
from .database import Database
from .job_queue import EnqueueResult, complete_job, enqueue_job, lock_owned_running_job
from .security import DataKeyring


FIT_EVALUATION_JOB_KIND = "evaluate_opportunity_fit"
FIT_EVALUATION_JOB_PRIORITY = 110
FIT_EVALUATION_SUBJECT_TYPE = "job_posting_version"
FIT_EVALUATION_ENABLED_ENV = "ENABLE_LLM_FIT_EVALUATION"


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
    return enqueue_job(
        session,
        kind=FIT_EVALUATION_JOB_KIND,
        dedupe_key=f"posting-version:{posting_version_id}:search:{saved_search_id}",
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
    "OpportunityFitClaim",
    "OpportunityFitJobHandler",
    "OpportunityFitJobOutcome",
    "complete_opportunity_fit_evaluation",
    "enqueue_opportunity_fit_evaluation",
    "fit_evaluation_jobs_enabled",
]
