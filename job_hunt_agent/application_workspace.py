"""Transport boundary for persisted applications and next actions."""

from __future__ import annotations

from typing import Protocol

from .application_schemas import (
    ApplicationActivityListResponse,
    ApplicationDetailResponse,
    ApplicationListResponse,
    CursorToken,
)


class ApplicationWorkspaceStore(Protocol):
    """Owner-scoped, database-only reads for the application workspace."""

    def list_applications(
        self,
        *,
        owner_id: str,
        limit: int = 50,
        cursor: CursorToken | None = None,
    ) -> ApplicationListResponse: ...

    def get_application(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationDetailResponse | None: ...

    def list_activity(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationActivityListResponse | None: ...


__all__ = ["ApplicationWorkspaceStore"]
