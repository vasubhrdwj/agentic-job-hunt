"""Transport boundary for manual, owner-scoped application outreach."""

from __future__ import annotations

from typing import Protocol

from .outreach_schemas import (
    ApplicationOutreachResponse,
    OutreachEventCreate,
    OutreachMessageCreate,
)


class OutreachWorkspaceStore(Protocol):
    """Database-only outreach operations; no method sends a message."""

    def get_application_outreach(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationOutreachResponse | None: ...

    def start_application_outreach(
        self,
        *,
        owner_id: str,
        application_id: str,
        expected_application_version: int,
        idempotency_key: str,
    ) -> ApplicationOutreachResponse | None: ...

    def save_outreach_message(
        self,
        *,
        owner_id: str,
        application_id: str,
        sequence_id: str,
        payload: OutreachMessageCreate,
        expected_sequence_version: int,
        idempotency_key: str,
    ) -> ApplicationOutreachResponse | None: ...

    def record_outreach_event(
        self,
        *,
        owner_id: str,
        application_id: str,
        sequence_id: str,
        payload: OutreachEventCreate,
        expected_sequence_version: int,
        idempotency_key: str,
    ) -> ApplicationOutreachResponse | None: ...


__all__ = ["OutreachWorkspaceStore"]
