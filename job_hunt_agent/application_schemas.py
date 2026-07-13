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


class ActionItemKind(str, Enum):
    review_and_prepare_application = "review_and_prepare_application"


class ActionItemStatus(str, Enum):
    open = "open"
    completed = "completed"
    cancelled = "cancelled"


class ApplicationActivityEventType(str, Enum):
    application_created = "application_created"


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
    occurred_at: UTCDateTime

    @model_validator(mode="after")
    def creation_event_is_immutable_and_complete(self) -> Self:
        if (
            self.sequence_number != 1
            or self.from_stage is not None
            or self.to_stage is not ApplicationStage.pursuing
            or self.action_item_id is None
        ):
            raise ValueError(
                "application_created must be the first event, enter pursuing, "
                "and name its initial action"
            )
        return self


class ApplicationSummary(ContractModel):
    id: OpaqueId
    version: int = Field(ge=1)
    opportunity_id: OpaqueId
    pursued_posting_version_id: OpaqueId
    stage: ApplicationStage
    posting: ApplicationPostingSummary
    current_action: ActionItemResponse
    created_at: UTCDateTime
    updated_at: UTCDateTime

    @model_validator(mode="after")
    def pursuing_application_has_an_open_action(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.current_action.application_id != self.id:
            raise ValueError("current_action must belong to the application")
        if self.current_action.status is not ActionItemStatus.open:
            raise ValueError("a pursuing application requires one open current_action")
        return self


class PursuitBundle(ContractModel):
    application: ApplicationSummary
    activity: ApplicationActivityEventResponse
    application_created: bool

    @model_validator(mode="after")
    def resources_form_one_atomic_pursuit(self) -> Self:
        if self.activity.application_id != self.application.id:
            raise ValueError("pursuit activity must belong to the application")
        if self.activity.action_item_id != self.application.current_action.id:
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
    "ApplicationActivityListResponse",
    "ApplicationDetailResponse",
    "ApplicationListResponse",
    "ApplicationPostingState",
    "ApplicationPostingSummary",
    "ApplicationStage",
    "ApplicationSummary",
    "CursorToken",
    "HttpsUrl",
    "OpaqueId",
    "PursuitBundle",
    "UTCDateTime",
]
