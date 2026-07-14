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
    ApplicationOutcome,
    ApplicationOutcomeResponse,
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


class ScreeningTransitionCreate(ApplicationSubmissionContract):
    to_stage: Literal["screening"]
    reached_on: date
    next_action_due_on: date
    confirm_progress: Literal[True]

    @field_validator("confirm_progress", mode="before")
    @classmethod
    def confirmation_is_boolean_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("confirm_progress must be the boolean true")
        return value


class InterviewingTransitionCreate(ApplicationSubmissionContract):
    to_stage: Literal["interviewing"]
    reached_on: date
    next_action_due_on: date
    confirm_progress: Literal[True]

    @field_validator("confirm_progress", mode="before")
    @classmethod
    def confirmation_is_boolean_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("confirm_progress must be the boolean true")
        return value


class OfferTransitionCreate(ApplicationSubmissionContract):
    to_stage: Literal["offer"]
    received_on: date
    next_action_due_on: date
    confirm_offer: Literal[True]

    @field_validator("confirm_offer", mode="before")
    @classmethod
    def confirmation_is_boolean_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("confirm_offer must be the boolean true")
        return value


class ClosedTransitionCreate(ApplicationSubmissionContract):
    to_stage: Literal["closed"]
    outcome: ApplicationOutcome
    outcome_on: date
    confirm_close: Literal[True]

    @field_validator("confirm_close", mode="before")
    @classmethod
    def confirmation_is_boolean_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("confirm_close must be the boolean true")
        return value


ApplicationTransitionCreate = Annotated[
    ReadyToApplyTransitionCreate
    | AppliedTransitionCreate
    | ScreeningTransitionCreate
    | InterviewingTransitionCreate
    | OfferTransitionCreate
    | ClosedTransitionCreate,
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
        post_submission = {
            ApplicationStage.applied,
            ApplicationStage.screening,
            ApplicationStage.interviewing,
            ApplicationStage.offer,
        }
        if self.stage in post_submission and self.submission is None:
            raise ValueError("a post-application stage must expose its submission")
        if self.stage in {
            ApplicationStage.pursuing,
            ApplicationStage.ready_to_apply,
        } and self.submission is not None:
            raise ValueError(
                "only an applied application or later stage can expose a submission"
            )
        return self


class ApplicationTransitionResponse(ApplicationSubmissionContract):
    data_source: Literal["database"] = "database"
    application: ApplicationSummary
    activity_event: ApplicationActivityEventResponse
    submission: ApplicationSubmissionResponse | None = None
    outcome: ApplicationOutcomeResponse | None = None
    transition_created: bool

    @model_validator(mode="after")
    def resources_form_one_transition(self) -> Self:
        event = self.activity_event
        if event.application_id != self.application.id:
            raise ValueError("transition activity must belong to the application")
        if event.to_stage is not self.application.stage:
            raise ValueError("transition activity must enter the current stage")
        if self.application.stage is ApplicationStage.closed:
            if (
                self.application.current_action is not None
                or event.action_item_id is not None
                or event.event_type is not ApplicationActivityEventType.application_closed
                or event.outcome_id is None
                or self.outcome is None
                or self.application.outcome is None
                or event.outcome_id != self.outcome.id
                or self.application.outcome.id != self.outcome.id
            ):
                raise ValueError("closed must expose the exact terminal outcome")
            if self.outcome.application_submission_id is None:
                if self.submission is not None:
                    raise ValueError("pre-submission closure cannot expose a submission")
            elif (
                self.submission is None
                or self.submission.id != self.outcome.application_submission_id
            ):
                raise ValueError("post-submission closure must expose its submission")
            return self
        if self.application.current_action is None or (
            event.action_item_id != self.application.current_action.id
        ):
            raise ValueError("transition activity must name the current action")
        if self.outcome is not None or self.application.outcome is not None:
            raise ValueError("active transitions cannot expose an outcome")
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
        elif self.application.stage in {
            ApplicationStage.screening,
            ApplicationStage.interviewing,
            ApplicationStage.offer,
        }:
            expected_event = {
                ApplicationStage.screening: (
                    ApplicationActivityEventType.application_screening
                ),
                ApplicationStage.interviewing: (
                    ApplicationActivityEventType.application_interviewing
                ),
                ApplicationStage.offer: ApplicationActivityEventType.application_offer,
            }[self.application.stage]
            if (
                event.event_type is not expected_event
                or event.effective_on is None
                or event.submission_id is not None
                or self.submission is None
                or self.submission.application_id != self.application.id
            ):
                raise ValueError(
                    "post-application progress must expose its immutable submission"
                )
        else:
            raise ValueError("unsupported application transition response")
        return self


__all__ = [
    "AppliedTransitionCreate",
    "ApplicationSubmissionProjection",
    "ApplicationSubmissionResponse",
    "ApplicationTransitionCreate",
    "ApplicationTransitionResponse",
    "ClosedTransitionCreate",
    "InterviewingTransitionCreate",
    "OfferTransitionCreate",
    "ReadyToApplyTransitionCreate",
    "ScreeningTransitionCreate",
]
