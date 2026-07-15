"""Transport boundary for the database-only weekly review."""

from __future__ import annotations

from typing import Protocol

from .weekly_review_schemas import (
    ApplicationActionReviewCreate,
    ApplicationActionReviewMutationResponse,
    WeeklyReviewResponse,
)


class WeeklyReviewWorkspaceStore(Protocol):
    def get_weekly_review(self, *, owner_id: str) -> WeeklyReviewResponse: ...

    def record_application_action_review(
        self,
        *,
        owner_id: str,
        application_id: str,
        action_id: str,
        payload: ApplicationActionReviewCreate,
        expected_application_version: int,
        idempotency_key: str,
    ) -> ApplicationActionReviewMutationResponse | None: ...


__all__ = ["WeeklyReviewWorkspaceStore"]
