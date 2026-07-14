"""Transport boundary for owner-scoped application interview rounds."""

from __future__ import annotations

from typing import Protocol

from .interview_round_schemas import (
    ApplicationInterviewRoundsResponse,
    InterviewRoundCreate,
    InterviewRoundEventCreate,
    InterviewRoundMutationResponse,
)


class InterviewRoundWorkspaceStore(Protocol):
    """Database-only interview schedule and lifecycle operations."""

    def get_application_interview_rounds(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationInterviewRoundsResponse | None: ...

    def schedule_interview_round(
        self,
        *,
        owner_id: str,
        application_id: str,
        payload: InterviewRoundCreate,
        expected_application_version: int,
        idempotency_key: str,
    ) -> InterviewRoundMutationResponse | None: ...

    def record_interview_round_event(
        self,
        *,
        owner_id: str,
        application_id: str,
        interview_round_id: str,
        payload: InterviewRoundEventCreate,
        expected_round_version: int,
        idempotency_key: str,
    ) -> InterviewRoundMutationResponse | None: ...


__all__ = ["InterviewRoundWorkspaceStore"]
