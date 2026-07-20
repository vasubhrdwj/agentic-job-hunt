"""Authenticated privacy export, retention, and workspace deletion routes."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, Security, status
from fastapi.security import APIKeyCookie

from ..auth import session_cookie_name
from ..database import Database
from ..privacy_repository import (
    PrivacyConflict,
    delete_owner_workspace,
    export_owner_workspace,
    get_retention_report,
    preview_owner_deletion,
    update_retention_setting,
)
from ..privacy_schemas import (
    DeletionPreviewResponse,
    RetentionReportResponse,
    RetentionSettingsPatch,
    WorkspaceDeletionReceipt,
    WorkspaceDeletionRequest,
    WorkspaceExportResponse,
)
from ..profile_schemas import ProblemResponse
from ..security import DataKeyring
from .session import (
    AuthenticatedOwner,
    require_migrated_database,
    require_owner_mutation,
    require_owner_session,
)
from .workspace import (
    COMMON_ERROR_RESPONSES,
    WorkspaceApiError,
    _expected_version,
    _required_idempotency_key,
)


MAX_EXPORT_BYTES = 32 * 1024 * 1024
PRIVACY_RECEIPT_SECRET_ENV = "JOB_HUNT_PRIVACY_RECEIPT_SECRET"
PRIVACY_ERROR_RESPONSES = {
    **COMMON_ERROR_RESPONSES,
    413: {"model": ProblemResponse},
}


def create_privacy_router(
    database: Database | None,
    keyring: DataKeyring,
    *,
    allowed_origins: list[str],
    production: bool,
) -> APIRouter:
    """Build practical-only privacy routes on the authoritative database."""

    def prevent_caching(response: Response) -> None:
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"

    router = APIRouter(
        tags=["owner-privacy"],
        dependencies=[Depends(prevent_caching)],
    )
    owner_cookie = APIKeyCookie(
        name=session_cookie_name(),
        scheme_name="OwnerSessionCookie",
        description="Opaque HttpOnly session issued by POST /api/session.",
        auto_error=False,
    )

    def require_read_owner(
        request: Request,
        _session_cookie: str | None = Security(owner_cookie),
    ) -> AuthenticatedOwner:
        try:
            return require_owner_session(database, request)
        except HTTPException as exc:
            _raise_auth_problem(exc)

    def require_mutation_owner(
        request: Request,
        _session_cookie: str | None = Security(owner_cookie),
    ) -> AuthenticatedOwner:
        try:
            return require_owner_mutation(
                database,
                request,
                allowed_origins=allowed_origins,
                production=production,
            )
        except HTTPException as exc:
            _raise_auth_problem(exc)

    @router.get(
        "/api/privacy/export",
        response_model=WorkspaceExportResponse,
        responses=PRIVACY_ERROR_RESPONSES,
    )
    def export_workspace(
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> Response:
        db = require_migrated_database(database)
        with db.session() as session:
            result = export_owner_workspace(
                session,
                owner_id=owner.owner_id,
                keyring=keyring,
            )
        encoded = result.model_dump_json().encode("utf-8")
        if len(encoded) > MAX_EXPORT_BYTES:
            raise WorkspaceApiError(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "privacy_export_too_large",
                "Workspace export exceeds 32 MiB; shorten retained legacy hunts and retry",
            )
        return Response(
            content=encoded,
            media_type="application/json",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "Content-Disposition": 'attachment; filename="job-hunt-workspace.json"',
            },
        )

    @router.get(
        "/api/privacy/deletion-preview",
        response_model=DeletionPreviewResponse,
        responses=PRIVACY_ERROR_RESPONSES,
    )
    def deletion_preview(
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> DeletionPreviewResponse:
        db = require_migrated_database(database)
        with db.session() as session:
            return preview_owner_deletion(session, owner_id=owner.owner_id)

    @router.get(
        "/api/privacy/retention",
        response_model=RetentionReportResponse,
        responses=PRIVACY_ERROR_RESPONSES,
    )
    def retention_report(
        response: Response,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> RetentionReportResponse:
        db = require_migrated_database(database)
        with db.session() as session:
            result = get_retention_report(session, owner_id=owner.owner_id)
        response.headers["ETag"] = f'"{result.version}"'
        return result

    @router.patch(
        "/api/privacy/retention",
        response_model=RetentionReportResponse,
        responses=PRIVACY_ERROR_RESPONSES,
    )
    def update_retention(
        payload: RetentionSettingsPatch,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> RetentionReportResponse:
        db = require_migrated_database(database)
        expected_version = _expected_version(if_match)
        try:
            with db.session() as session:
                result = update_retention_setting(
                    session,
                    owner_id=owner.owner_id,
                    hunt_run_retention_days=payload.hunt_run_retention_days,
                    expected_version=expected_version,
                )
        except PrivacyConflict as exc:
            raise WorkspaceApiError(409, "version_conflict", str(exc)) from exc
        response.headers["ETag"] = f'"{result.version}"'
        return result

    @router.delete(
        "/api/privacy/workspace",
        response_model=WorkspaceDeletionReceipt,
        responses=PRIVACY_ERROR_RESPONSES,
    )
    def delete_workspace(
        payload: WorkspaceDeletionRequest,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> WorkspaceDeletionReceipt:
        required_idempotency_key = _required_idempotency_key(idempotency_key)
        secret = _receipt_secret(production=production)
        db = require_migrated_database(database)
        try:
            with db.session() as session:
                receipt = delete_owner_workspace(
                    session,
                    owner_id=owner.owner_id,
                    confirmation=payload.confirmation,
                    idempotency_key=required_idempotency_key,
                    receipt_secret=secret,
                )
        except PrivacyConflict as exc:
            message = str(exc)
            code = (
                "idempotency_conflict"
                if "idempotency key" in message
                else "deletion_confirmation_mismatch"
            )
            raise WorkspaceApiError(409, code, message) from exc
        response.delete_cookie(
            key=session_cookie_name(),
            path="/",
            secure=production,
            httponly=True,
            samesite="strict",
        )
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return receipt

    return router


def _receipt_secret(*, production: bool) -> str:
    """Require a rotation-independent receipt key in production."""

    secret = os.getenv(PRIVACY_RECEIPT_SECRET_ENV, "").strip()
    if not secret and not production:
        secret = "local-development-privacy-receipt-secret"
    if len(secret) < 32:
        raise WorkspaceApiError(
            503,
            "privacy_unavailable",
            "Workspace deletion receipt protection is not configured",
            retryable=False,
        )
    return secret


def _raise_auth_problem(exc: HTTPException) -> None:
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        raise WorkspaceApiError(401, "owner_session_required", "owner session required") from exc
    if exc.status_code == status.HTTP_403_FORBIDDEN:
        raise WorkspaceApiError(403, "origin_forbidden", "request origin is not allowed") from exc
    if exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
        raise WorkspaceApiError(
            503,
            "workspace_unavailable",
            "owner workspace storage is unavailable",
            retryable=True,
        ) from exc
    raise WorkspaceApiError(500, "internal_error", "owner authorization failed") from exc


__all__ = ["MAX_EXPORT_BYTES", "create_privacy_router"]
