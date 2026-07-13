"""Transport boundary for database-only application contact benches."""

from __future__ import annotations

from typing import Protocol

from .contact_schemas import ApplicationContactBenchResponse


class ContactWorkspaceStore(Protocol):
    """Owner-scoped contact reads with no provider or worker side effects."""

    def get_application_contacts(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationContactBenchResponse | None: ...


__all__ = ["ContactWorkspaceStore"]
