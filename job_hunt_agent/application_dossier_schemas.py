"""One-screen preview and one-receipt approval contracts."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .application_artifact_schemas import (
    MAX_APPLICATION_ARTIFACT_QUESTIONS,
    MAX_APPLICATION_ARTIFACT_SELECTED_EVIDENCE,
    ApplicationArtifactBlocker,
    ApplicationArtifactQuestion,
    ApplicationArtifactRevisionResponse,
    ApplicationArtifactsResponse,
)
from .application_pack_schemas import (
    ApplicationPackEvidenceReference,
    ApplicationPackRequirementReview,
    ApplicationPackResponse,
    MAX_APPLICATION_PACK_REQUIREMENTS,
)
from .application_schemas import OpaqueId


class ApplicationDossierContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApplicationDossierPreparedInputs(ApplicationDossierContractModel):
    grounding_parent_revision_id: OpaqueId
    requirements: list[ApplicationPackRequirementReview] = Field(
        min_length=1,
        max_length=MAX_APPLICATION_PACK_REQUIREMENTS,
    )
    selected_evidence_refs: list[ApplicationPackEvidenceReference] = Field(
        min_length=1,
        max_length=MAX_APPLICATION_ARTIFACT_SELECTED_EVIDENCE,
    )
    questions: list[ApplicationArtifactQuestion] = Field(
        default_factory=list,
        max_length=MAX_APPLICATION_ARTIFACT_QUESTIONS,
    )

    @model_validator(mode="after")
    def ids_are_unique(self) -> Self:
        requirement_ids = [item.id for item in self.requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("requirements must not contain duplicate ids")
        evidence_ids = [item.id for item in self.selected_evidence_refs]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("selected_evidence_refs must not contain duplicate ids")
        question_ids = [item.id for item in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("questions must not contain duplicate ids")
        return self


class ApplicationDossierPreviewCreate(ApplicationDossierPreparedInputs):
    pass


class ApplicationDossierApproveCreate(ApplicationDossierPreparedInputs):
    preview_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_dossier_reviewed: Literal[True]

    @field_validator("confirm_dossier_reviewed", mode="before")
    @classmethod
    def confirmation_is_boolean_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("confirm_dossier_reviewed must be the boolean true")
        return value


class ApplicationDossierPreviewResponse(ApplicationDossierContractModel):
    data_source: Literal["database_preview"] = "database_preview"
    application_id: OpaqueId
    pack_id: OpaqueId
    pack_version: int = Field(ge=1)
    preview_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    materials: ApplicationArtifactRevisionResponse
    blockers: list[ApplicationArtifactBlocker] = Field(default_factory=list, max_length=10)


class ApplicationDossierApprovalResponse(ApplicationDossierContractModel):
    data_source: Literal["database"] = "database"
    application_id: OpaqueId
    pack: ApplicationPackResponse
    artifacts: ApplicationArtifactsResponse

    @model_validator(mode="after")
    def projections_name_one_application(self) -> Self:
        if (
            self.pack.application_id != self.application_id
            or self.artifacts.application_id != self.application_id
        ):
            raise ValueError("dossier projections must name the requested application")
        return self


__all__ = [
    "ApplicationDossierApprovalResponse",
    "ApplicationDossierApproveCreate",
    "ApplicationDossierPreparedInputs",
    "ApplicationDossierPreviewCreate",
    "ApplicationDossierPreviewResponse",
]
