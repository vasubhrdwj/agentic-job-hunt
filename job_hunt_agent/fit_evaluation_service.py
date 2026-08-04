"""Background orchestration for one optional model-backed fit evaluation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .database import Database
from .fit_evaluation import FitEvaluationProvider, should_request_model
from .fit_evaluation_repository import (
    FitEvaluatorIdentity,
    load_cached_fit_verdict,
    prepare_fit_evaluation,
    store_fit_verdict,
)
from .gemini_fit_provider import gemini_fit_provider_from_env
from .job_queue import lock_owned_running_job
from .opportunity_fit_worker import (
    DETERMINISTIC_FALLBACK_OUTCOME,
    OpportunityFitJobOutcome,
)
from .security import DataKeyring


class FitEvaluationClaim(Protocol):
    job_id: str
    posting_id: str
    posting_version_id: str
    saved_search_id: str
    lease_token: str


class IdentifiedFitProvider(FitEvaluationProvider, Protocol):
    provider_name: str
    model: str
    prompt_version: str


FitProviderFactory = Callable[[], IdentifiedFitProvider | None]


def process_opportunity_fit_job(
    claim: FitEvaluationClaim,
    *,
    database: Database,
    worker_id: str,
    keyring: DataKeyring,
    provider_factory: FitProviderFactory = gemini_fit_provider_from_env,
) -> OpportunityFitJobOutcome:
    """Use two short transactions around exactly one optional provider call.

    Returning the deterministic fallback is a successful outcome for this
    derived job. Provider/configuration failures are intentionally allowed to
    bubble to the outer worker boundary, which records only their type and then
    completes the queue item on that same safe fallback path.
    """

    provider = provider_factory()
    if provider is None:
        return DETERMINISTIC_FALLBACK_OUTCOME
    identity = FitEvaluatorIdentity(
        provider=provider.provider_name,
        model=provider.model,
        prompt_version=provider.prompt_version,
    )

    with database.session() as session:
        prepared = prepare_fit_evaluation(
            session,
            owner_id=_claim_owner_id(session, claim, worker_id=worker_id),
            posting_version_id=claim.posting_version_id,
            saved_search_id=claim.saved_search_id,
            identity=identity,
            keyring=keyring,
        )
        cached = load_cached_fit_verdict(
            session,
            prepared=prepared,
            keyring=keyring,
        )
    if cached is not None:
        return OpportunityFitJobOutcome(cache_written=True)
    if not should_request_model(prepared.deterministic, prepared.inputs):
        return DETERMINISTIC_FALLBACK_OUTCOME

    # No database transaction is open here. The worker's renewable lease
    # remains alive in its independent heartbeat thread.
    verdict = provider.evaluate(prepared.inputs)

    with database.session() as session:
        owned = lock_owned_running_job(
            session,
            claim.job_id,
            worker_id=worker_id,
            lease_token=claim.lease_token,
        )
        if owned is None or owned.owner_id is None:
            return DETERMINISTIC_FALLBACK_OUTCOME
        refreshed = prepare_fit_evaluation(
            session,
            owner_id=owned.owner_id,
            posting_version_id=claim.posting_version_id,
            saved_search_id=claim.saved_search_id,
            identity=identity,
            keyring=keyring,
        )
        if refreshed.input_fingerprint != prepared.input_fingerprint:
            # Profile/track/evidence/posting changed during the model call.
            # Never publish a verdict against a different snapshot.
            return DETERMINISTIC_FALLBACK_OUTCOME
        store_fit_verdict(
            session,
            prepared=refreshed,
            verdict=verdict,
            keyring=keyring,
        )
    return OpportunityFitJobOutcome(cache_written=True)


def _claim_owner_id(
    session,
    claim: FitEvaluationClaim,
    *,
    worker_id: str,
) -> str:
    owned = lock_owned_running_job(
        session,
        claim.job_id,
        worker_id=worker_id,
        lease_token=claim.lease_token,
    )
    if owned is None or owned.owner_id is None:
        raise ValueError("fit evaluation lease is unavailable")
    if (
        owned.subject_id != claim.posting_version_id
        or owned.payload.get("job_posting_id") != claim.posting_id
        or owned.payload.get("saved_search_id") != claim.saved_search_id
    ):
        raise ValueError("fit evaluation claim binding is invalid")
    return owned.owner_id


__all__ = [
    "FitEvaluationClaim",
    "FitProviderFactory",
    "IdentifiedFitProvider",
    "process_opportunity_fit_job",
]
