"""Transport boundary for deterministic application artifacts."""

from __future__ import annotations

from typing import Protocol

from .application_artifact_schemas import (
    ApplicationArtifactEventCreate,
    ApplicationArtifactRevisionCreate,
    ApplicationArtifactsResponse,
)


class ApplicationArtifactWorkspaceStore(Protocol):
    """Owner-scoped, database-backed application artifact operations."""

    def get_application_artifacts(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationArtifactsResponse | None: ...

    def create_application_artifact_revision(
        self,
        *,
        owner_id: str,
        application_id: str,
        pack_id: str,
        payload: ApplicationArtifactRevisionCreate,
        expected_pack_version: int,
        idempotency_key: str,
    ) -> ApplicationArtifactsResponse | None: ...

    def record_application_artifact_event(
        self,
        *,
        owner_id: str,
        application_id: str,
        pack_id: str,
        payload: ApplicationArtifactEventCreate,
        expected_pack_version: int,
        idempotency_key: str,
    ) -> ApplicationArtifactsResponse | None: ...


__all__ = ["ApplicationArtifactWorkspaceStore"]
