"""Transport boundary for grounded, owner-scoped application packs."""

from __future__ import annotations

from typing import Protocol

from .application_pack_schemas import (
    ApplicationPackCreate,
    ApplicationPackEventCreate,
    ApplicationPackRevisionCreate,
    ApplicationPackResponse,
)


class ApplicationPackWorkspaceStore(Protocol):
    """Database-only requirement/evidence review operations."""

    def get_application_pack(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationPackResponse | None: ...

    def create_application_pack(
        self,
        *,
        owner_id: str,
        application_id: str,
        payload: ApplicationPackCreate,
        expected_application_version: int,
        idempotency_key: str,
    ) -> ApplicationPackResponse | None: ...

    def create_application_pack_revision(
        self,
        *,
        owner_id: str,
        application_id: str,
        pack_id: str,
        payload: ApplicationPackRevisionCreate,
        expected_pack_version: int,
        idempotency_key: str,
    ) -> ApplicationPackResponse | None: ...

    def record_application_pack_event(
        self,
        *,
        owner_id: str,
        application_id: str,
        pack_id: str,
        payload: ApplicationPackEventCreate,
        expected_pack_version: int,
        idempotency_key: str,
    ) -> ApplicationPackResponse | None: ...


__all__ = ["ApplicationPackWorkspaceStore"]
