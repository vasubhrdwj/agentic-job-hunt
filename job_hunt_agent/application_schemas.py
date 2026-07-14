"""Provider-free contracts for the first practical application workflow.

Pursuing an opportunity creates one durable application, one dated next action,
and one immutable creation activity.  These transport models deliberately keep
that initial boundary small; later pipeline stages and activity types should be
added only alongside their persistence and transition rules.

This module does not import :mod:`opportunity_schemas`.  The opportunity
contracts may embed a :class:`PursuitBundle` without creating a circular import.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


MAX_APPLICATION_PAGE_SIZE = 50
MAX_ACTIVITY_ITEMS = 500
MAX_APPLICATION_CURSOR_CHARS = 512


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a UTC offset")
    return value.astimezone(timezone.utc)


def _https_url(value: str) -> str:
    if any(character.isspace() for character in value) or "\\" in value:
        raise ValueError("URL must be a valid HTTPS URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL must be a valid HTTPS URL") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
    ):
        raise ValueError(
            "URL must be a valid HTTPS URL without credentials or fragments"
        )
    return value


UTCDateTime = Annotated[datetime, AfterValidator(_as_utc)]
OpaqueId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")]
CursorToken = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_APPLICATION_CURSOR_CHARS,
        pattern=r"^[A-Za-z0-9_-]+$",
    ),
]
HttpsUrl = Annotated[
    str,
    Field(min_length=1, max_length=2_048),
    AfterValidator(_https_url),
]


class ContractModel(BaseModel):
    """Reject silently ignored input and normalize surrounding whitespace."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ApplicationStage(str, Enum):
    pursuing = "pursuing"
    ready_to_apply = "ready_to_apply"
    applied = "applied"
    screening = "screening"
    interviewing = "interviewing"
    offer = "offer"
    closed = "closed"


ACTIVE_APPLICATION_STAGE_VALUES = frozenset(
    stage.value for stage in ApplicationStage if stage is not ApplicationStage.closed
)
CONTACTABLE_APPLICATION_STAGE_VALUES = frozenset(
    {
        ApplicationStage.pursuing.value,
        ApplicationStage.ready_to_apply.value,
        ApplicationStage.applied.value,
    }
)


class ActionItemKind(str, Enum):
    review_and_prepare_application = "review_and_prepare_application"
    submit_application = "submit_application"
    follow_up_application = "follow_up_application"
    prepare_recruiter_screen = "prepare_recruiter_screen"
    prepare_interview = "prepare_interview"
    review_offer = "review_offer"


class ActionItemStatus(str, Enum):
    open = "open"
    completed = "completed"
    cancelled = "cancelled"


class ApplicationActivityEventType(str, Enum):
    application_created = "application_created"
    application_ready_to_apply = "application_ready_to_apply"
    application_applied = "application_applied"
    application_screening = "application_screening"
    application_interviewing = "application_interviewing"
    application_offer = "application_offer"
    application_closed = "application_closed"


class ApplicationOutcome(str, Enum):
    rejected = "rejected"
    withdrawn = "withdrawn"
    offer_accepted = "offer_accepted"
    offer_declined = "offer_declined"
    no_response = "no_response"
    posting_closed = "posting_closed"


class ApplicationPostingState(str, Enum):
    open = "open"
    closed = "closed"
    unknown = "unknown"


class ApplicationPostingSummary(ContractModel):
    id: OpaqueId
    company: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    canonical_url: HttpsUrl
    first_party: bool
    state: ApplicationPostingState


class ActionItemResponse(ContractModel):
    id: OpaqueId
    version: int = Field(ge=1)
    application_id: OpaqueId
    kind: ActionItemKind
    status: ActionItemStatus
    title: str = Field(min_length=1, max_length=240)
    due_on: date
    completed_at: UTCDateTime | None = None
    cancelled_at: UTCDateTime | None = None
    created_at: UTCDateTime
    updated_at: UTCDateTime

    @model_validator(mode="after")
    def lifecycle_is_consistent(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.completed_at is not None and self.completed_at < self.created_at:
            raise ValueError("completed_at cannot precede created_at")
        if self.cancelled_at is not None and self.cancelled_at < self.created_at:
            raise ValueError("cancelled_at cannot precede created_at")

        if self.status is ActionItemStatus.open:
            if self.completed_at is not None or self.cancelled_at is not None:
                raise ValueError("open actions cannot be completed or cancelled")
        elif self.status is ActionItemStatus.completed:
            if self.completed_at is None or self.cancelled_at is not None:
                raise ValueError(
                    "completed actions require completed_at and no cancelled_at"
                )
        elif self.cancelled_at is None or self.completed_at is not None:
            raise ValueError(
                "cancelled actions require cancelled_at and no completed_at"
            )
        return self


class ApplicationActivityEventResponse(ContractModel):
    id: OpaqueId
    application_id: OpaqueId
    sequence_number: int = Field(ge=1)
    event_type: ApplicationActivityEventType
    from_stage: ApplicationStage | None = None
    to_stage: ApplicationStage | None = None
    action_item_id: OpaqueId | None = None
    previous_action_item_id: OpaqueId | None = None
    submission_id: OpaqueId | None = None
    effective_on: date | None = None
    outcome_id: OpaqueId | None = None
    occurred_at: UTCDateTime

    @model_validator(mode="after")
    def event_shape_is_immutable_and_complete(self) -> Self:
        if self.event_type is ApplicationActivityEventType.application_created:
            if (
                self.sequence_number != 1
                or self.from_stage is not None
                or self.to_stage is not ApplicationStage.pursuing
                or self.action_item_id is None
                or self.previous_action_item_id is not None
                or self.submission_id is not None
                or self.effective_on is not None
                or self.outcome_id is not None
            ):
                raise ValueError(
                    "application_created must be the first event, enter pursuing, "
                    "name its initial action, and have no submission"
                )
        elif self.event_type is ApplicationActivityEventType.application_ready_to_apply:
            if (
                self.sequence_number != 2
                or self.from_stage is not ApplicationStage.pursuing
                or self.to_stage is not ApplicationStage.ready_to_apply
                or self.action_item_id is None
                or self.previous_action_item_id is None
                or self.previous_action_item_id == self.action_item_id
                or self.submission_id is not None
                or self.effective_on is not None
                or self.outcome_id is not None
            ):
                raise ValueError(
                    "application_ready_to_apply must replace the pursuing action "
                    "without creating a submission"
                )
        elif self.event_type is ApplicationActivityEventType.application_applied:
            if (
                self.sequence_number != 3
                or self.from_stage is not ApplicationStage.ready_to_apply
                or self.to_stage is not ApplicationStage.applied
                or self.action_item_id is None
                or self.previous_action_item_id is None
                or self.previous_action_item_id == self.action_item_id
                or self.submission_id is None
                or self.effective_on is not None
                or self.outcome_id is not None
            ):
                raise ValueError(
                    "application_applied must replace the submit action and name "
                    "the exact submission"
                )
        elif self.event_type is ApplicationActivityEventType.application_screening:
            self._require_progress_shape(
                allowed_from={ApplicationStage.applied},
                to_stage=ApplicationStage.screening,
            )
        elif self.event_type is ApplicationActivityEventType.application_interviewing:
            self._require_progress_shape(
                allowed_from={ApplicationStage.applied, ApplicationStage.screening},
                to_stage=ApplicationStage.interviewing,
            )
        elif self.event_type is ApplicationActivityEventType.application_offer:
            self._require_progress_shape(
                allowed_from={
                    ApplicationStage.applied,
                    ApplicationStage.screening,
                    ApplicationStage.interviewing,
                },
                to_stage=ApplicationStage.offer,
            )
        elif (
            self.sequence_number < 2
            or self.from_stage
            not in {
                ApplicationStage.pursuing,
                ApplicationStage.ready_to_apply,
                ApplicationStage.applied,
                ApplicationStage.screening,
                ApplicationStage.interviewing,
                ApplicationStage.offer,
            }
            or self.to_stage is not ApplicationStage.closed
            or self.action_item_id is not None
            or self.previous_action_item_id is None
            or self.submission_id is not None
            or self.effective_on is None
            or self.outcome_id is None
        ):
            raise ValueError(
                "application_closed must close the current action and name the "
                "exact terminal outcome"
            )
        return self

    def _require_progress_shape(
        self,
        *,
        allowed_from: set[ApplicationStage],
        to_stage: ApplicationStage,
    ) -> None:
        if (
            self.sequence_number < 4
            or self.from_stage not in allowed_from
            or self.to_stage is not to_stage
            or self.action_item_id is None
            or self.previous_action_item_id is None
            or self.previous_action_item_id == self.action_item_id
            or self.submission_id is not None
            or self.effective_on is None
            or self.outcome_id is not None
        ):
            raise ValueError(
                f"{self.event_type.value} must record one forward milestone and "
                "replace the current action"
            )


class ApplicationOutcomeResponse(ContractModel):
    id: OpaqueId
    application_id: OpaqueId
    application_submission_id: OpaqueId | None = None
    stage_at_outcome: ApplicationStage
    outcome: ApplicationOutcome
    outcome_on: date
    recording_method: Literal["manual"]
    recorded_at: UTCDateTime
    created_at: UTCDateTime

    @model_validator(mode="after")
    def outcome_shape_is_truthful(self) -> Self:
        if self.created_at < self.recorded_at:
            raise ValueError("created_at cannot precede recorded_at")
        pre_submission = {
            ApplicationStage.pursuing,
            ApplicationStage.ready_to_apply,
        }
        post_submission = {
            ApplicationStage.applied,
            ApplicationStage.screening,
            ApplicationStage.interviewing,
            ApplicationStage.offer,
        }
        if self.stage_at_outcome in pre_submission:
            if (
                self.application_submission_id is not None
                or self.outcome
                not in {ApplicationOutcome.withdrawn, ApplicationOutcome.posting_closed}
            ):
                raise ValueError(
                    "pre-submission outcomes must be withdrawal or posting closure "
                    "without a submission"
                )
        elif self.stage_at_outcome in post_submission:
            if self.application_submission_id is None:
                raise ValueError("post-submission outcomes require the exact submission")
        else:
            raise ValueError("stage_at_outcome must be an active application stage")
        if (
            self.outcome
            in {ApplicationOutcome.offer_accepted, ApplicationOutcome.offer_declined}
            and self.stage_at_outcome is not ApplicationStage.offer
        ):
            raise ValueError("offer acceptance or decline requires the offer stage")
        return self


class ApplicationSummary(ContractModel):
    id: OpaqueId
    version: int = Field(ge=1)
    opportunity_id: OpaqueId
    pursued_posting_version_id: OpaqueId
    stage: ApplicationStage
    posting: ApplicationPostingSummary
    current_action: ActionItemResponse | None = None
    outcome: ApplicationOutcomeResponse | None = None
    created_at: UTCDateTime
    updated_at: UTCDateTime

    @model_validator(mode="after")
    def active_application_has_an_open_action(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.stage is ApplicationStage.closed:
            if self.current_action is not None:
                raise ValueError("closed applications cannot have a current_action")
            if self.outcome is None or self.outcome.application_id != self.id:
                raise ValueError("closed applications require their exact outcome")
            return self
        if self.outcome is not None:
            raise ValueError("active applications cannot expose an outcome")
        if self.current_action is None:
            raise ValueError("an active application requires one open current_action")
        if self.current_action.application_id != self.id:
            raise ValueError("current_action must belong to the application")
        if self.current_action.status is not ActionItemStatus.open:
            raise ValueError("an active application requires one open current_action")
        expected_kind = {
            ApplicationStage.pursuing: ActionItemKind.review_and_prepare_application,
            ApplicationStage.ready_to_apply: ActionItemKind.submit_application,
            ApplicationStage.applied: ActionItemKind.follow_up_application,
            ApplicationStage.screening: ActionItemKind.prepare_recruiter_screen,
            ApplicationStage.interviewing: ActionItemKind.prepare_interview,
            ApplicationStage.offer: ActionItemKind.review_offer,
        }[self.stage]
        if self.current_action.kind is not expected_kind:
            raise ValueError(
                f"{self.stage.value} applications require a {expected_kind.value} "
                "current_action"
            )
        return self


class PursuitBundle(ContractModel):
    application: ApplicationSummary
    activity: ApplicationActivityEventResponse
    application_created: bool

    @model_validator(mode="after")
    def resources_form_one_atomic_pursuit(self) -> Self:
        if self.activity.application_id != self.application.id:
            raise ValueError("pursuit activity must belong to the application")
        if self.application.current_action is None or (
            self.activity.action_item_id != self.application.current_action.id
        ):
            raise ValueError("pursuit activity must name the current action")
        if self.activity.occurred_at != self.application.created_at:
            raise ValueError("pursuit activity must occur when the application is created")
        return self


class ApplicationListResponse(ContractModel):
    data_source: Literal["database"] = "database"
    items: list[ApplicationSummary] = Field(
        default_factory=list,
        max_length=MAX_APPLICATION_PAGE_SIZE,
    )
    total: int = Field(ge=0)
    next_cursor: CursorToken | None = None

    @model_validator(mode="after")
    def total_covers_returned_items(self) -> Self:
        if self.total < len(self.items):
            raise ValueError("total cannot be smaller than the returned item count")
        application_ids = [item.id for item in self.items]
        if len(application_ids) != len(set(application_ids)):
            raise ValueError("application items must not contain duplicates")
        return self


class ApplicationDetailResponse(ContractModel):
    data_source: Literal["database"] = "database"
    application: ApplicationSummary
    activity: list[ApplicationActivityEventResponse] = Field(
        min_length=1,
        max_length=MAX_ACTIVITY_ITEMS,
    )

    @model_validator(mode="after")
    def activity_belongs_to_application(self) -> Self:
        if any(
            event.application_id != self.application.id for event in self.activity
        ):
            raise ValueError("activity must belong to the application")
        _validate_activity_order(self.activity)
        return self


class ApplicationActivityListResponse(ContractModel):
    data_source: Literal["database"] = "database"
    items: list[ApplicationActivityEventResponse] = Field(
        default_factory=list,
        max_length=MAX_ACTIVITY_ITEMS,
    )

    @model_validator(mode="after")
    def activity_is_one_ordered_stream(self) -> Self:
        if self.items:
            application_ids = {event.application_id for event in self.items}
            if len(application_ids) != 1:
                raise ValueError("activity items must belong to one application")
            _validate_activity_order(self.items)
        return self


def _validate_activity_order(items: list[ApplicationActivityEventResponse]) -> None:
    sequence_numbers = [event.sequence_number for event in items]
    if sequence_numbers != sorted(sequence_numbers) or len(sequence_numbers) != len(
        set(sequence_numbers)
    ):
        raise ValueError("activity sequence numbers must be unique and ordered")
    occurred_at = [event.occurred_at for event in items]
    if occurred_at != sorted(occurred_at):
        raise ValueError("activity must be chronological")


__all__ = [
    "ActionItemKind",
    "ActionItemResponse",
    "ActionItemStatus",
    "ApplicationActivityEventResponse",
    "ApplicationActivityEventType",
    "ACTIVE_APPLICATION_STAGE_VALUES",
    "CONTACTABLE_APPLICATION_STAGE_VALUES",
    "ApplicationActivityListResponse",
    "ApplicationDetailResponse",
    "ApplicationListResponse",
    "ApplicationPostingState",
    "ApplicationPostingSummary",
    "ApplicationOutcome",
    "ApplicationOutcomeResponse",
    "ApplicationStage",
    "ApplicationSummary",
    "CursorToken",
    "HttpsUrl",
    "OpaqueId",
    "PursuitBundle",
    "UTCDateTime",
]
