"""Transport-facing boundary for the durable opportunity radar.

The concrete adapter owns transactions, queue creation, owner predicates,
idempotency, and optimistic version checks.  The API router depends only on
this protocol so opening Today can never accidentally invoke a live source.
"""

from __future__ import annotations

from typing import Protocol

from .opportunity_schemas import (
    OpportunityDecisionRequest,
    OpportunityDecisionResponse,
    OpportunityDetailResponse,
    ScanCreateRequest,
    ScanCreateResponse,
    ScanStatusResponse,
    TodayListResponse,
    TodayQuery,
)


class OpportunityWorkspaceStore(Protocol):
    """Owner-scoped operations required by the manual radar and Today inbox."""

    def create_scan(
        self,
        *,
        owner_id: str,
        saved_search_id: str,
        expected_saved_search_version: int,
        idempotency_key: str,
        payload: ScanCreateRequest,
    ) -> ScanCreateResponse: ...

    def get_scan(
        self,
        *,
        owner_id: str,
        scan_id: str,
    ) -> ScanStatusResponse | None: ...

    def list_today(
        self,
        *,
        owner_id: str,
        query: TodayQuery,
    ) -> TodayListResponse: ...

    def get_opportunity(
        self,
        *,
        owner_id: str,
        opportunity_id: str,
    ) -> OpportunityDetailResponse | None: ...

    def decide_opportunity(
        self,
        *,
        owner_id: str,
        opportunity_id: str,
        expected_version: int,
        idempotency_key: str,
        payload: OpportunityDecisionRequest,
    ) -> OpportunityDecisionResponse: ...


__all__ = ["OpportunityWorkspaceStore"]
