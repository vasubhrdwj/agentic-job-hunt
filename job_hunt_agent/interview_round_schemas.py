"""Strict contracts for scheduled and completed application interview rounds."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .application_schemas import (
    ApplicationStage,
    ApplicationSummary,
    OpaqueId,
    UTCDateTime,
)


MAX_INTERVIEW_ROUNDS = 100
MAX_INTERVIEW_ROUND_EVENTS = 500


def _as_local_datetime(value: datetime) -> datetime:
    if value.tzinfo is not None and value.utcoffset() is not None:
        raise ValueError("scheduled_local must not include a UTC offset")
    return value.replace(tzinfo=None)


LocalDateTime = Annotated[datetime, AfterValidator(_as_local_datetime)]


class InterviewRoundContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InterviewRoundKind(str, Enum):
    hiring_manager = "hiring_manager"
    technical = "technical"
    system_design = "system_design"
    behavioral = "behavioral"
    case_study = "case_study"
    panel = "panel"
    final = "final"
    other = "other"


class InterviewMeetingFormat(str, Enum):
    video = "video"
    phone = "phone"
    onsite = "onsite"
    unspecified = "unspecified"


class InterviewRoundStatus(str, Enum):
    scheduled = "scheduled"
    completed = "completed"
    cancelled = "cancelled"


class InterviewRoundEventType(str, Enum):
    scheduled = "scheduled"
    rescheduled = "rescheduled"
    completed = "completed"
    cancelled = "cancelled"


class InterviewCancellationParty(str, Enum):
    employer = "employer"
    candidate = "candidate"
    mutual = "mutual"
    unknown = "unknown"


def _validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("scheduled_timezone must be a valid IANA timezone") from exc
    return value


class _AppointmentWrite(InterviewRoundContract):
    scheduled_local: LocalDateTime
    scheduled_timezone: str = Field(min_length=1, max_length=64)
    duration_minutes: int = Field(ge=15, le=480)
    meeting_format: InterviewMeetingFormat = InterviewMeetingFormat.unspecified
    next_action_due_on: date

    @field_validator("scheduled_timezone")
    @classmethod
    def timezone_is_iana(cls, value: str) -> str:
        return _validate_timezone(value)


class InterviewRoundCreate(_AppointmentWrite):
    kind: InterviewRoundKind
    title: str = Field(min_length=1, max_length=160)
    confirm_schedule: Literal[True]

    @field_validator("confirm_schedule", mode="before")
    @classmethod
    def confirmation_is_boolean_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("confirm_schedule must be the boolean true")
        return value


class InterviewRoundRescheduledCreate(_AppointmentWrite):
    event_type: Literal["rescheduled"]
    confirm_reschedule: Literal[True]

    @field_validator("confirm_reschedule", mode="before")
    @classmethod
    def confirmation_is_boolean_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("confirm_reschedule must be the boolean true")
        return value


class InterviewRoundCompletedCreate(InterviewRoundContract):
    event_type: Literal["completed"]
    completed_on: date
    next_action_due_on: date
    confirm_complete: Literal[True]

    @field_validator("confirm_complete", mode="before")
    @classmethod
    def confirmation_is_boolean_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("confirm_complete must be the boolean true")
        return value


class InterviewRoundCancelledCreate(InterviewRoundContract):
    event_type: Literal["cancelled"]
    cancelled_on: date
    cancelled_by: InterviewCancellationParty
    next_action_due_on: date
    confirm_cancel: Literal[True]

    @field_validator("confirm_cancel", mode="before")
    @classmethod
    def confirmation_is_boolean_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("confirm_cancel must be the boolean true")
        return value


InterviewRoundEventCreate = Annotated[
    InterviewRoundRescheduledCreate
    | InterviewRoundCompletedCreate
    | InterviewRoundCancelledCreate,
    Field(discriminator="event_type"),
]


class InterviewRoundEventResponse(InterviewRoundContract):
    id: OpaqueId
    application_id: OpaqueId
    interview_round_id: OpaqueId
    sequence_number: int = Field(ge=1)
    event_type: InterviewRoundEventType
    from_status: InterviewRoundStatus | None = None
    to_status: InterviewRoundStatus
    scheduled_start_at: UTCDateTime
    scheduled_timezone: str = Field(min_length=1, max_length=64)
    duration_minutes: int = Field(ge=15, le=480)
    meeting_format: InterviewMeetingFormat
    effective_on: date | None = None
    cancelled_by: InterviewCancellationParty | None = None
    previous_action_item_id: OpaqueId
    action_item_id: OpaqueId
    recording_method: Literal["manual"]
    occurred_at: UTCDateTime
    created_at: UTCDateTime

    @field_validator("scheduled_timezone")
    @classmethod
    def timezone_is_iana(cls, value: str) -> str:
        return _validate_timezone(value)

    @model_validator(mode="after")
    def event_shape_is_complete(self) -> Self:
        if self.previous_action_item_id == self.action_item_id:
            raise ValueError("interview-round events must replace the current action")
        if self.created_at < self.occurred_at:
            raise ValueError("created_at cannot precede occurred_at")
        if self.event_type is InterviewRoundEventType.scheduled:
            valid = (
                self.sequence_number == 1
                and self.from_status is None
                and self.to_status is InterviewRoundStatus.scheduled
                and self.effective_on is None
                and self.cancelled_by is None
            )
        elif self.event_type is InterviewRoundEventType.rescheduled:
            valid = (
                self.sequence_number >= 2
                and self.from_status is InterviewRoundStatus.scheduled
                and self.to_status is InterviewRoundStatus.scheduled
                and self.effective_on is None
                and self.cancelled_by is None
            )
        elif self.event_type is InterviewRoundEventType.completed:
            valid = (
                self.sequence_number >= 2
                and self.from_status is InterviewRoundStatus.scheduled
                and self.to_status is InterviewRoundStatus.completed
                and self.effective_on is not None
                and self.cancelled_by is None
            )
        else:
            valid = (
                self.sequence_number >= 2
                and self.from_status is InterviewRoundStatus.scheduled
                and self.to_status is InterviewRoundStatus.cancelled
                and self.effective_on is not None
                and self.cancelled_by is not None
            )
        if not valid:
            raise ValueError("interview-round event lifecycle is inconsistent")
        return self


class InterviewRoundResponse(InterviewRoundContract):
    id: OpaqueId
    version: int = Field(ge=1)
    application_id: OpaqueId
    application_submission_id: OpaqueId
    round_number: int = Field(ge=1)
    kind: InterviewRoundKind
    title: str = Field(min_length=1, max_length=160)
    status: InterviewRoundStatus
    scheduled_start_at: UTCDateTime
    scheduled_timezone: str = Field(min_length=1, max_length=64)
    duration_minutes: int = Field(ge=15, le=480)
    meeting_format: InterviewMeetingFormat
    completed_on: date | None = None
    cancelled_on: date | None = None
    cancelled_by: InterviewCancellationParty | None = None
    created_at: UTCDateTime
    updated_at: UTCDateTime
    events: list[InterviewRoundEventResponse] = Field(
        min_length=1,
        max_length=MAX_INTERVIEW_ROUND_EVENTS,
    )

    @field_validator("scheduled_timezone")
    @classmethod
    def timezone_is_iana(cls, value: str) -> str:
        return _validate_timezone(value)

    @model_validator(mode="after")
    def lifecycle_and_events_are_consistent(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.status is InterviewRoundStatus.scheduled:
            terminal_shape = (
                self.completed_on is None
                and self.cancelled_on is None
                and self.cancelled_by is None
            )
        elif self.status is InterviewRoundStatus.completed:
            terminal_shape = (
                self.completed_on is not None
                and self.cancelled_on is None
                and self.cancelled_by is None
            )
        else:
            terminal_shape = (
                self.completed_on is None
                and self.cancelled_on is not None
                and self.cancelled_by is not None
            )
        if not terminal_shape:
            raise ValueError("interview-round status timestamps are inconsistent")
        sequences = [event.sequence_number for event in self.events]
        if sequences != list(range(1, len(self.events) + 1)):
            raise ValueError("interview-round event sequence must be contiguous")
        if self.version != len(self.events):
            raise ValueError("interview-round version must equal its event sequence")
        if any(
            event.application_id != self.application_id
            or event.interview_round_id != self.id
            for event in self.events
        ):
            raise ValueError("interview-round events must belong to their round")
        latest = self.events[-1]
        if (
            latest.to_status is not self.status
            or latest.scheduled_start_at != self.scheduled_start_at
            or latest.scheduled_timezone != self.scheduled_timezone
            or latest.duration_minutes != self.duration_minutes
            or latest.meeting_format is not self.meeting_format
            or latest.effective_on != (self.completed_on or self.cancelled_on)
            or latest.cancelled_by is not self.cancelled_by
        ):
            raise ValueError("interview-round projection must match its latest event")
        return self


class ApplicationInterviewRoundsResponse(InterviewRoundContract):
    data_source: Literal["database"] = "database"
    application: ApplicationSummary
    rounds: list[InterviewRoundResponse] = Field(
        default_factory=list,
        max_length=MAX_INTERVIEW_ROUNDS,
    )

    @model_validator(mode="after")
    def rounds_belong_to_application(self) -> Self:
        numbers = [round_.round_number for round_ in self.rounds]
        if numbers != sorted(numbers) or len(numbers) != len(set(numbers)):
            raise ValueError("interview rounds must have unique ascending numbers")
        if any(round_.application_id != self.application.id for round_ in self.rounds):
            raise ValueError("interview rounds must belong to the application")
        scheduled = [
            round_
            for round_ in self.rounds
            if round_.status is InterviewRoundStatus.scheduled
        ]
        if len(scheduled) > 1:
            raise ValueError("an application can have only one scheduled round")
        if scheduled:
            if self.application.stage not in {
                ApplicationStage.applied,
                ApplicationStage.screening,
                ApplicationStage.interviewing,
            }:
                raise ValueError("only active post-application stages may have a round")
            action = self.application.current_action
            if (
                action is None
                or action.interview_round_id != scheduled[0].id
                or action.id != scheduled[0].events[-1].action_item_id
            ):
                raise ValueError("the scheduled round must own the current action")
        return self


class InterviewRoundMutationResponse(InterviewRoundContract):
    data_source: Literal["database"] = "database"
    application: ApplicationSummary
    round: InterviewRoundResponse
    event: InterviewRoundEventResponse
    mutation_created: bool

    @model_validator(mode="after")
    def resources_form_one_mutation(self) -> Self:
        if (
            self.round.application_id != self.application.id
            or self.event.application_id != self.application.id
            or self.event.interview_round_id != self.round.id
            or self.round.events[-1].id != self.event.id
        ):
            raise ValueError("interview-round mutation resources do not match")
        return self


__all__ = [
    "ApplicationInterviewRoundsResponse",
    "InterviewCancellationParty",
    "InterviewMeetingFormat",
    "InterviewRoundCancelledCreate",
    "InterviewRoundCompletedCreate",
    "InterviewRoundCreate",
    "InterviewRoundEventCreate",
    "InterviewRoundEventResponse",
    "InterviewRoundEventType",
    "InterviewRoundKind",
    "InterviewRoundMutationResponse",
    "InterviewRoundRescheduledCreate",
    "InterviewRoundResponse",
    "InterviewRoundStatus",
]
