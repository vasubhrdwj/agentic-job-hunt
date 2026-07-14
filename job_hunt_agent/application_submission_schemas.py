"""Strict contracts for manual application stage transitions and submissions."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .application_schemas import (
    ApplicationActivityEventResponse,
    ApplicationActivityEventType,
    ApplicationStage,
    ApplicationSummary,
    HttpsUrl,
    OpaqueId,
    UTCDateTime,
)


class ApplicationSubmissionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _ExactApplicationMaterials(ApplicationSubmissionContract):
    application_pack_id: OpaqueId
    application_pack_revision_id: OpaqueId
    application_pack_review_event_id: OpaqueId
    application_artifact_revision_id: OpaqueId
    application_artifact_approval_event_id: OpaqueId
    tailored_resume_version_id: OpaqueId


class ReadyToApplyTransitionCreate(_ExactApplicationMaterials):
    to_stage: Literal["ready_to_apply"]
    next_action_due_on: date
    confirm_ready: Literal[True]

    @field_validator("confirm_ready", mode="before")
    @classmethod
    def confirmation_is_boolean_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("confirm_ready must be the boolean true")
        return value


class AppliedTransitionCreate(_ExactApplicationMaterials):
    to_stage: Literal["applied"]
    destination_url: HttpsUrl
    applied_on: date
    next_action_due_on: date
    confirm_manual_submission: Literal[True]

    @field_validator("confirm_manual_submission", mode="before")
    @classmethod
    def confirmation_is_boolean_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("confirm_manual_submission must be the boolean true")
        return value


ApplicationTransitionCreate = Annotated[
    ReadyToApplyTransitionCreate | AppliedTransitionCreate,
    Field(discriminator="to_stage"),
]


class ApplicationSubmissionResponse(ApplicationSubmissionContract):
    id: OpaqueId
    application_id: OpaqueId
    application_pack_id: OpaqueId
    application_pack_revision_id: OpaqueId
    application_pack_review_event_id: OpaqueId
    application_artifact_revision_id: OpaqueId
    application_artifact_approval_event_id: OpaqueId
    tailored_resume_version_id: OpaqueId
    destination_url: HttpsUrl
    applied_on: date
    submission_method: Literal["manual"]
    recorded_at: UTCDateTime
    created_at: UTCDateTime

    @model_validator(mode="after")
    def timestamps_are_consistent(self) -> Self:
        if self.created_at < self.recorded_at:
            raise ValueError("created_at cannot precede recorded_at")
        return self


class ApplicationSubmissionProjection(ApplicationSubmissionContract):
    data_source: Literal["database"] = "database"
    application_id: OpaqueId
    stage: ApplicationStage
    available_destinations: list[HttpsUrl] = Field(default_factory=list, max_length=50)
    first_party_verified: bool
    submission: ApplicationSubmissionResponse | None = None

    @model_validator(mode="before")
    @classmethod
    def destinations_are_unique(cls, value: object) -> object:
        if isinstance(value, dict):
            destinations = value.get("available_destinations")
            if isinstance(destinations, list) and len(destinations) != len(
                set(destinations)
            ):
                raise ValueError("available_destinations must not contain duplicates")
        return value

    @model_validator(mode="after")
    def submission_belongs_to_application(self) -> Self:
        if (
            self.submission is not None
            and self.submission.application_id != self.application_id
        ):
            raise ValueError("submission must belong to the requested application")
        if self.stage is ApplicationStage.applied and self.submission is None:
            raise ValueError("an applied application must expose its submission")
        if self.stage is not ApplicationStage.applied and self.submission is not None:
            raise ValueError("only an applied application can expose a submission")
        return self


class ApplicationTransitionResponse(ApplicationSubmissionContract):
    data_source: Literal["database"] = "database"
    application: ApplicationSummary
    activity_event: ApplicationActivityEventResponse
    submission: ApplicationSubmissionResponse | None = None
    transition_created: bool

    @model_validator(mode="after")
    def resources_form_one_transition(self) -> Self:
        event = self.activity_event
        if event.application_id != self.application.id:
            raise ValueError("transition activity must belong to the application")
        if event.action_item_id != self.application.current_action.id:
            raise ValueError("transition activity must name the current action")
        if event.to_stage is not self.application.stage:
            raise ValueError("transition activity must enter the current stage")
        if self.application.stage is ApplicationStage.ready_to_apply:
            if (
                event.event_type
                is not ApplicationActivityEventType.application_ready_to_apply
                or event.submission_id is not None
                or self.submission is not None
            ):
                raise ValueError("ready_to_apply cannot expose a submission")
        elif self.application.stage is ApplicationStage.applied:
            if (
                event.event_type is not ApplicationActivityEventType.application_applied
                or event.submission_id is None
                or self.submission is None
                or event.submission_id != self.submission.id
                or self.submission.application_id != self.application.id
            ):
                raise ValueError("applied must expose the exact activity submission")
        else:
            raise ValueError("a transition response must be ready_to_apply or applied")
        return self


__all__ = [
    "AppliedTransitionCreate",
    "ApplicationSubmissionProjection",
    "ApplicationSubmissionResponse",
    "ApplicationTransitionCreate",
    "ApplicationTransitionResponse",
    "ReadyToApplyTransitionCreate",
]
