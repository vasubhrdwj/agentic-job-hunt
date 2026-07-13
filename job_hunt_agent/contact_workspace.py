"""Transport boundary for database-only application contact benches."""

from __future__ import annotations

from typing import Protocol

from .contact_schemas import ApplicationContactBenchResponse


class ContactWorkspaceStore(Protocol):
    """Owner-scoped contact state with no provider calls in request threads."""

    def get_application_contacts(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationContactBenchResponse | None: ...

    def create_application_contact_search(
        self,
        *,
        owner_id: str,
        application_id: str,
        expected_application_version: int,
        idempotency_key: str,
    ) -> ApplicationContactBenchResponse | None: ...


__all__ = ["ContactWorkspaceStore"]
