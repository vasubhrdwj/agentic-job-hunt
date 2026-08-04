"""Transport boundary for one-screen application dossier actions."""

from __future__ import annotations

from typing import Protocol

from .application_dossier_schemas import (
    ApplicationDossierApprovalResponse,
    ApplicationDossierApproveCreate,
    ApplicationDossierPreviewCreate,
    ApplicationDossierPreviewResponse,
)


class ApplicationDossierWorkspaceStore(Protocol):
    def preview_application_dossier(
        self,
        *,
        owner_id: str,
        application_id: str,
        pack_id: str,
        payload: ApplicationDossierPreviewCreate,
        expected_pack_version: int,
    ) -> ApplicationDossierPreviewResponse | None: ...

    def approve_application_dossier(
        self,
        *,
        owner_id: str,
        application_id: str,
        pack_id: str,
        payload: ApplicationDossierApproveCreate,
        expected_pack_version: int,
        idempotency_key: str,
    ) -> ApplicationDossierApprovalResponse | None: ...


__all__ = ["ApplicationDossierWorkspaceStore"]
