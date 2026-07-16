"""Transport boundary for owner-scoped interview preparation."""

from __future__ import annotations

from typing import Protocol

from .interview_preparation_schemas import (
    ApplicationInterviewPreparationResponse,
    InterviewPreparationRevisionCreate,
)


class InterviewPreparationWorkspaceStore(Protocol):
    def get_application_interview_preparation(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationInterviewPreparationResponse | None: ...

    def create_interview_preparation_revision(
        self,
        *,
        owner_id: str,
        application_id: str,
        payload: InterviewPreparationRevisionCreate,
        expected_version: int,
        idempotency_key: str,
    ) -> ApplicationInterviewPreparationResponse | None: ...


__all__ = ["InterviewPreparationWorkspaceStore"]
