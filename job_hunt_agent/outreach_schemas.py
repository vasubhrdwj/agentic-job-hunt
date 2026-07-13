"""Strict provider-free contracts for manual staged application outreach.

The practical outreach API is an audit and coordination surface only.  Reads
are projected exclusively from stored rows, message bodies are exact immutable
versions, and every copy/send/outcome transition is an explicit owner action.
Nothing in these contracts represents an automated send.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from .application_schemas import HttpsUrl, OpaqueId, UTCDateTime
from .contact_schemas import ContactBenchState, ContactCategory, ContactLifecycle


MAX_OUTREACH_RECIPIENTS = 5
MAX_OUTREACH_MESSAGE_CHARS = 4_000
MAX_OUTREACH_REASON_CHARS = 100
MAX_OUTREACH_TIMELINE_EVENTS = 200


class OutreachContractModel(BaseModel):
    """Reject silently ignored input without altering exact message text."""

    model_config = ConfigDict(extra="forbid")


class OutreachSequenceStatus(str, Enum):
    active = "active"
    paused = "paused"
    stopped = "stopped"
    completed = "completed"


class ApplicationOutreachStatus(str, Enum):
    not_started = "not_started"
    active = "active"
    paused = "paused"
    stopped = "stopped"
    completed = "completed"


class OutreachMessageKind(str, Enum):
    initial = "initial"
    follow_up = "follow_up"


class OutreachChannel(str, Enum):
    linkedin = "linkedin"
    email = "email"
    other = "other"


class OutreachOutcome(str, Enum):
    no_reply = "no_reply"
    declined = "declined"
    unreachable = "unreachable"
    useful_reply = "useful_reply"
    introduced = "introduced"
    referred = "referred"
    do_not_contact = "do_not_contact"


class OutreachMessageCreate(OutreachContractModel):
    """Create the next immutable version for one recipient and message kind."""

    application_contact_id: OpaqueId
    kind: OutreachMessageKind
    body: str = Field(min_length=1, max_length=MAX_OUTREACH_MESSAGE_CHARS)

    @field_validator("body")
    @classmethod
    def body_contains_visible_text(cls, value: str) -> str:
        # Preserve the exact submitted whitespace while rejecting an empty
        # message disguised as spaces or newlines.
        if not value.strip():
            raise ValueError("message body must contain non-whitespace text")
        return value


class OutreachCopiedEventCreate(OutreachContractModel):
    event_type: Literal["copied"]
    message_version_id: OpaqueId


class OutreachMarkedSentEventCreate(OutreachContractModel):
    event_type: Literal["marked_sent"]
    message_version_id: OpaqueId
    channel: OutreachChannel
    confirm_exact_version: Literal[True]

    @field_validator("confirm_exact_version", mode="before")
    @classmethod
    def confirmation_is_boolean_true(cls, value: object) -> object:
        # ``Literal[True]`` considers integer ``1`` equal to ``True`` in
        # Python.  Sending is consequential enough to require the JSON boolean
        # itself rather than a truthy coercion.
        if value is not True:
            raise ValueError("confirm_exact_version must be the boolean true")
        return value


class OutreachOutcomeEventCreate(OutreachContractModel):
    event_type: Literal["outcome"]
    application_contact_id: OpaqueId
    outcome: OutreachOutcome


class _ReasonedOutreachEventCreate(OutreachContractModel):
    reason: str = Field(min_length=1, max_length=MAX_OUTREACH_REASON_CHARS)

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("reason must contain non-whitespace text")
        return normalized


class OutreachPauseEventCreate(_ReasonedOutreachEventCreate):
    event_type: Literal["pause"]


class OutreachResumeEventCreate(_ReasonedOutreachEventCreate):
    event_type: Literal["resume"]


class OutreachStopEventCreate(_ReasonedOutreachEventCreate):
    event_type: Literal["stop"]


OutreachEventCreate = Annotated[
    OutreachCopiedEventCreate
    | OutreachMarkedSentEventCreate
    | OutreachOutcomeEventCreate
    | OutreachPauseEventCreate
    | OutreachResumeEventCreate
    | OutreachStopEventCreate,
    Field(discriminator="event_type"),
]


class OutreachMessageVersionResponse(OutreachContractModel):
    """Latest exact stored body for one initial or follow-up message."""

    id: OpaqueId
    version_number: StrictInt = Field(ge=1)
    kind: OutreachMessageKind
    body: str = Field(min_length=1, max_length=MAX_OUTREACH_MESSAGE_CHARS)
    copied_at: UTCDateTime | None = None
    sent_at: UTCDateTime | None = None
    sent_channel: OutreachChannel | None = None
    created_at: UTCDateTime

    @field_validator("body")
    @classmethod
    def stored_body_contains_visible_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("stored message body must contain non-whitespace text")
        return value

    @model_validator(mode="after")
    def manual_timestamps_are_consistent(self) -> Self:
        if self.copied_at is not None and self.copied_at < self.created_at:
            raise ValueError("copied_at cannot precede message creation")
        if self.sent_at is not None and self.sent_at < self.created_at:
            raise ValueError("sent_at cannot precede message creation")
        if (
            self.copied_at is not None
            and self.sent_at is not None
            and self.sent_at < self.copied_at
        ):
            raise ValueError("sent_at cannot precede copied_at")
        if (self.sent_at is None) != (self.sent_channel is None):
            raise ValueError("sent_at and sent_channel must be recorded together")
        return self


class OutreachRecipientResponse(OutreachContractModel):
    """One pinned verified recipient and their latest message projections."""

    sequence_id: OpaqueId
    application_contact_id: OpaqueId
    contact_id: OpaqueId
    public_name: str = Field(min_length=1, max_length=200)
    profile_url: HttpsUrl
    lifecycle: ContactLifecycle
    current_title: str = Field(min_length=1, max_length=300)
    current_company: str = Field(min_length=1, max_length=200)
    category: ContactCategory
    bench_rank: StrictInt = Field(ge=1, le=MAX_OUTREACH_RECIPIENTS)
    wave: StrictInt = Field(ge=1, le=MAX_OUTREACH_RECIPIENTS)
    bench_state: ContactBenchState
    initial_message: OutreachMessageVersionResponse | None = None
    follow_up_message: OutreachMessageVersionResponse | None = None
    follow_up_due_at: UTCDateTime | None = None
    outcome: OutreachOutcome | None = None
    outcome_at: UTCDateTime | None = None

    @model_validator(mode="after")
    def recipient_message_state_is_consistent(self) -> Self:
        if (
            self.initial_message is not None
            and self.initial_message.kind is not OutreachMessageKind.initial
        ):
            raise ValueError("initial_message must have initial kind")
        if (
            self.follow_up_message is not None
            and self.follow_up_message.kind is not OutreachMessageKind.follow_up
        ):
            raise ValueError("follow_up_message must have follow_up kind")
        if self.follow_up_message is not None:
            if self.initial_message is None or self.initial_message.sent_at is None:
                raise ValueError("a follow-up requires a sent initial message")
            if self.follow_up_message.id == self.initial_message.id:
                raise ValueError("initial and follow-up versions must be distinct")
        if self.follow_up_due_at is not None:
            if self.initial_message is None or self.initial_message.sent_at is None:
                raise ValueError("follow_up_due_at requires a sent initial message")
            if self.follow_up_due_at < self.initial_message.sent_at:
                raise ValueError("follow_up_due_at cannot precede the initial send")
        if (self.outcome is None) != (self.outcome_at is None):
            raise ValueError("outcome and outcome_at must be recorded together")
        return self


class OutreachSequenceResponse(OutreachContractModel):
    """One pinned, versioned manual outreach sequence for an application."""

    id: OpaqueId
    version: StrictInt = Field(ge=1)
    application_id: OpaqueId
    contact_plan_id: OpaqueId
    status: OutreachSequenceStatus
    active_wave: StrictInt | None = Field(default=None, ge=1, le=MAX_OUTREACH_RECIPIENTS)
    reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_OUTREACH_REASON_CHARS,
    )
    manual_only: Literal[True] = True
    started_at: UTCDateTime
    paused_at: UTCDateTime | None = None
    stopped_at: UTCDateTime | None = None
    completed_at: UTCDateTime | None = None
    created_at: UTCDateTime
    updated_at: UTCDateTime

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_optional_reason(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("reason must contain non-whitespace text")
        return normalized

    @field_validator("manual_only", mode="before")
    @classmethod
    def manual_only_is_boolean_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("manual_only must be the boolean true")
        return value

    @model_validator(mode="after")
    def lifecycle_is_consistent(self) -> Self:
        if self.started_at < self.created_at:
            raise ValueError("started_at cannot precede sequence creation")
        if self.updated_at < self.created_at or self.updated_at < self.started_at:
            raise ValueError("updated_at cannot precede sequence creation or start")
        for name, value in (
            ("paused_at", self.paused_at),
            ("stopped_at", self.stopped_at),
            ("completed_at", self.completed_at),
        ):
            if value is not None and value < self.started_at:
                raise ValueError(f"{name} cannot precede sequence start")
            if value is not None and value > self.updated_at:
                raise ValueError(f"{name} cannot follow updated_at")

        if self.status is OutreachSequenceStatus.active:
            if self.active_wave is None:
                raise ValueError("an active sequence requires active_wave")
            if self.reason is not None:
                raise ValueError("an active sequence cannot have a state reason")
            if any((self.paused_at, self.stopped_at, self.completed_at)):
                raise ValueError("an active sequence cannot have terminal timestamps")
        elif self.status is OutreachSequenceStatus.paused:
            if self.active_wave is None or self.reason is None or self.paused_at is None:
                raise ValueError(
                    "a paused sequence requires active_wave, reason, and paused_at"
                )
            if self.stopped_at is not None or self.completed_at is not None:
                raise ValueError("a paused sequence cannot be stopped or completed")
        elif self.status is OutreachSequenceStatus.stopped:
            if self.active_wave is not None or self.reason is None or self.stopped_at is None:
                raise ValueError(
                    "a stopped sequence requires reason and stopped_at, with no active wave"
                )
            if self.paused_at is not None or self.completed_at is not None:
                raise ValueError("a stopped sequence cannot be paused or completed")
        else:
            if self.active_wave is not None or self.completed_at is None:
                raise ValueError(
                    "a completed sequence requires completed_at and no active wave"
                )
            if self.paused_at is not None or self.stopped_at is not None:
                raise ValueError("a completed sequence cannot be paused or stopped")
        return self


class _OutreachTimelineEventBase(OutreachContractModel):
    id: OpaqueId
    sequence_id: OpaqueId
    occurred_at: UTCDateTime


class OutreachCopiedTimelineEvent(_OutreachTimelineEventBase):
    event_type: Literal["copied"]
    message_version_id: OpaqueId


class OutreachMarkedSentTimelineEvent(_OutreachTimelineEventBase):
    event_type: Literal["marked_sent"]
    message_version_id: OpaqueId
    channel: OutreachChannel


class OutreachSequenceStartedTimelineEvent(_OutreachTimelineEventBase):
    event_type: Literal["sequence_started"]
    wave: Literal[1]


class OutreachMessageSavedTimelineEvent(_OutreachTimelineEventBase):
    event_type: Literal["message_saved"]
    application_contact_id: OpaqueId
    message_version_id: OpaqueId
    kind: OutreachMessageKind


class OutreachOutcomeTimelineEvent(_OutreachTimelineEventBase):
    event_type: Literal["outcome_recorded"]
    application_contact_id: OpaqueId
    outcome: OutreachOutcome


class OutreachPausedTimelineEvent(_OutreachTimelineEventBase):
    event_type: Literal["paused"]
    reason: str = Field(min_length=1, max_length=MAX_OUTREACH_REASON_CHARS)


class OutreachResumedTimelineEvent(_OutreachTimelineEventBase):
    event_type: Literal["resumed"]
    reason: str = Field(min_length=1, max_length=MAX_OUTREACH_REASON_CHARS)


class OutreachStoppedTimelineEvent(_OutreachTimelineEventBase):
    event_type: Literal["stopped"]
    reason: str = Field(min_length=1, max_length=MAX_OUTREACH_REASON_CHARS)


class OutreachWaveAdvancedTimelineEvent(_OutreachTimelineEventBase):
    event_type: Literal["wave_advanced"]
    wave: StrictInt = Field(ge=2, le=MAX_OUTREACH_RECIPIENTS)


OutreachTimelineEvent = Annotated[
    OutreachSequenceStartedTimelineEvent
    | OutreachMessageSavedTimelineEvent
    | OutreachCopiedTimelineEvent
    | OutreachMarkedSentTimelineEvent
    | OutreachOutcomeTimelineEvent
    | OutreachPausedTimelineEvent
    | OutreachResumedTimelineEvent
    | OutreachStoppedTimelineEvent
    | OutreachWaveAdvancedTimelineEvent,
    Field(discriminator="event_type"),
]


class ApplicationOutreachResponse(OutreachContractModel):
    """Database-only state for an application's one manual outreach sequence."""

    data_source: Literal["database"] = "database"
    application_id: OpaqueId
    status: ApplicationOutreachStatus
    sequence: OutreachSequenceResponse | None = None
    recipients: list[OutreachRecipientResponse] = Field(
        default_factory=list,
        max_length=MAX_OUTREACH_RECIPIENTS,
    )
    timeline: list[OutreachTimelineEvent] = Field(
        default_factory=list,
        max_length=MAX_OUTREACH_TIMELINE_EVENTS,
    )

    @model_validator(mode="after")
    def projection_is_one_consistent_sequence(self) -> Self:
        if self.status is ApplicationOutreachStatus.not_started:
            if self.sequence is not None or self.recipients or self.timeline:
                raise ValueError(
                    "not_started outreach cannot contain a sequence, recipients, or timeline"
                )
            return self

        if self.sequence is None:
            raise ValueError("started outreach requires a sequence")
        if self.sequence.application_id != self.application_id:
            raise ValueError("sequence must belong to the requested application")
        if self.status.value != self.sequence.status.value:
            raise ValueError("outreach status must match sequence status")
        if not self.recipients:
            raise ValueError("a started sequence requires at least one recipient")

        recipient_ids = [item.application_contact_id for item in self.recipients]
        contact_ids = [item.contact_id for item in self.recipients]
        ranks = [item.bench_rank for item in self.recipients]
        waves = [item.wave for item in self.recipients]
        profile_urls = [str(item.profile_url) for item in self.recipients]
        if len(recipient_ids) != len(set(recipient_ids)):
            raise ValueError("outreach recipients must have distinct application contacts")
        if len(contact_ids) != len(set(contact_ids)):
            raise ValueError("outreach recipients must identify distinct contacts")
        if len(profile_urls) != len(set(profile_urls)):
            raise ValueError("outreach recipients must have distinct profile URLs")
        expected_ranks = set(range(1, len(self.recipients) + 1))
        if set(ranks) != expected_ranks or len(ranks) != len(set(ranks)):
            raise ValueError("recipient bench ranks must be distinct and consecutive")
        if list(zip(waves, ranks)) != sorted(zip(waves, ranks)):
            raise ValueError("recipients must be ordered by wave and bench rank")
        distinct_waves = list(dict.fromkeys(waves))
        if distinct_waves != list(range(1, max(waves) + 1)):
            raise ValueError("recipient waves must start at one and be consecutive")
        if waves.count(1) > 2 or any(waves.count(wave) > 1 for wave in distinct_waves[1:]):
            raise ValueError(
                "wave one permits at most two recipients and later waves only one"
            )
        if any(item.sequence_id != self.sequence.id for item in self.recipients):
            raise ValueError("all recipients must belong to the returned sequence")
        if (
            self.sequence.active_wave is not None
            and self.sequence.active_wave not in set(waves)
        ):
            raise ValueError("active_wave must identify a returned recipient")

        event_ids = [event.id for event in self.timeline]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("timeline events must be distinct")
        if any(event.sequence_id != self.sequence.id for event in self.timeline):
            raise ValueError("all timeline events must belong to the returned sequence")
        if any(
            event.occurred_at < self.sequence.started_at
            or event.occurred_at > self.sequence.updated_at
            for event in self.timeline
        ):
            raise ValueError("timeline events must fall within the sequence lifecycle")
        if any(
            isinstance(event, OutreachOutcomeTimelineEvent)
            and event.application_contact_id not in set(recipient_ids)
            for event in self.timeline
        ):
            raise ValueError("outcome events must identify a returned recipient")
        if any(
            later.occurred_at < earlier.occurred_at
            for earlier, later in zip(self.timeline, self.timeline[1:])
        ):
            raise ValueError("timeline events must be ordered by occurred_at")
        return self


__all__ = [
    "ApplicationOutreachResponse",
    "ApplicationOutreachStatus",
    "MAX_OUTREACH_MESSAGE_CHARS",
    "MAX_OUTREACH_REASON_CHARS",
    "MAX_OUTREACH_RECIPIENTS",
    "MAX_OUTREACH_TIMELINE_EVENTS",
    "OutreachChannel",
    "OutreachCopiedEventCreate",
    "OutreachCopiedTimelineEvent",
    "OutreachEventCreate",
    "OutreachMarkedSentEventCreate",
    "OutreachMarkedSentTimelineEvent",
    "OutreachMessageSavedTimelineEvent",
    "OutreachMessageCreate",
    "OutreachMessageKind",
    "OutreachMessageVersionResponse",
    "OutreachOutcome",
    "OutreachOutcomeEventCreate",
    "OutreachOutcomeTimelineEvent",
    "OutreachPauseEventCreate",
    "OutreachPausedTimelineEvent",
    "OutreachRecipientResponse",
    "OutreachResumeEventCreate",
    "OutreachResumedTimelineEvent",
    "OutreachSequenceResponse",
    "OutreachSequenceStartedTimelineEvent",
    "OutreachSequenceStatus",
    "OutreachStopEventCreate",
    "OutreachStoppedTimelineEvent",
    "OutreachTimelineEvent",
    "OutreachWaveAdvancedTimelineEvent",
]
