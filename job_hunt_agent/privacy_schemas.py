"""Stable API contracts for workspace privacy controls."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr


PRIVACY_SCHEMA_VERSION = 1


class PrivacyOmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    field: str | None = None
    reason: Literal[
        "security_metadata",
        "operational_metadata",
        "decryption_failed",
        "expired_or_cleared",
    ]
    row_count: int = Field(ge=0)


class ExternalDataLimit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    category: str
    summary: str
    source_url: str
    verified_on: date


class WorkspaceExportResponse(BaseModel):
    """Portable, deterministic JSON export with no stored secrets/ciphertext."""

    model_config = ConfigDict(extra="forbid")

    schema_name: Literal["job_hunt_workspace_export"] = "job_hunt_workspace_export"
    schema_version: Literal[1] = PRIVACY_SCHEMA_VERSION
    generated_at: datetime
    owner_id: str
    counts: dict[str, int]
    tables: dict[str, list[dict[str, Any]]]
    omissions: list[PrivacyOmission]
    external_data_limits: list[ExternalDataLimit]


class DeletionPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = PRIVACY_SCHEMA_VERSION
    owner_id: str
    confirmation_phrase: str
    row_counts: dict[str, int]
    total_rows: int = Field(ge=0)
    active_sessions: int = Field(ge=0)
    export_recommended: bool = True
    external_data_limits: list[ExternalDataLimit]


class WorkspaceDeletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    confirmation: StrictStr = Field(min_length=8, max_length=160)


class WorkspaceDeletionReceipt(BaseModel):
    """Minimal result returned only after the transaction commits its delete."""

    model_config = ConfigDict(extra="forbid")

    deletion_id: str
    deleted_at: datetime
    replayed: bool


class RetentionSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    hunt_run_retention_days: int = Field(ge=1, le=30)


class RetentionReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = PRIVACY_SCHEMA_VERSION
    hunt_run_retention_days: int = Field(ge=1, le=30)
    version: int = Field(ge=1)
    eligible_hunt_runs: int = Field(ge=0)
    retained_hunt_runs: int = Field(ge=0)
    purged_hunt_runs: int = Field(ge=0)
    policy_applies_to: list[Literal["legacy_hunt_runs"]] = Field(
        default_factory=lambda: ["legacy_hunt_runs"]
    )
    retained_until_explicit_deletion: list[str] = Field(
        default_factory=lambda: [
            "profile_and_resumes",
            "saved_searches_and_opportunities",
            "applications_and_interviews",
            "contacts_and_outreach",
        ]
    )
    as_of: datetime
    updated_at: datetime | None = None


__all__ = [
    "DeletionPreviewResponse",
    "ExternalDataLimit",
    "PRIVACY_SCHEMA_VERSION",
    "PrivacyOmission",
    "RetentionReportResponse",
    "RetentionSettingsPatch",
    "WorkspaceDeletionReceipt",
    "WorkspaceDeletionRequest",
    "WorkspaceExportResponse",
]
