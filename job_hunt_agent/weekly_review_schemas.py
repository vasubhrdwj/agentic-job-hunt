"""Strict transport contracts for the owner-local weekly review."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from .application_schemas import (
    ActionItemResponse,
    ApplicationPostingSummary,
    ApplicationSummary,
    ContractModel,
    OpaqueId,
    UTCDateTime,
)


class ApplicationActionReviewDecision(str, Enum):
    continue_search = "continue"
    waiting = "waiting"


class ApplicationActionReviewCreate(ContractModel):
    decision: ApplicationActionReviewDecision
    new_due_on: date
    confirm_current_action: Literal[True]

    @field_validator("confirm_current_action", mode="before")
    @classmethod
    def confirmation_is_boolean_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("confirm_current_action must be the boolean true")
        return value


class ApplicationActionReviewResponse(ContractModel):
    id: OpaqueId
    application_id: OpaqueId
    prior_action_item_id: OpaqueId
    action_item_id: OpaqueId
    decision: ApplicationActionReviewDecision
    prior_due_on: date
    new_due_on: date
    prior_action_version: int = Field(ge=1)
    action_version: int = Field(ge=2)
    prior_application_version: int = Field(ge=1)
    application_version: int = Field(ge=2)
    recording_method: Literal["manual"]
    recorded_at: UTCDateTime
    created_at: UTCDateTime

    @model_validator(mode="after")
    def exact_reschedule_is_consistent(self) -> Self:
        if self.prior_action_item_id != self.action_item_id:
            raise ValueError("an action review must reschedule the exact same action")
        if self.new_due_on <= self.prior_due_on:
            raise ValueError("new_due_on must be strictly after prior_due_on")
        if self.action_version != self.prior_action_version + 1:
            raise ValueError("action_version must increment exactly once")
        if self.application_version != self.prior_application_version + 1:
            raise ValueError("application_version must increment exactly once")
        if self.created_at < self.recorded_at:
            raise ValueError("created_at cannot precede recorded_at")
        return self


class ApplicationActionReviewMutationResponse(ContractModel):
    data_source: Literal["database"] = "database"
    application: ApplicationSummary
    action: ActionItemResponse
    review: ApplicationActionReviewResponse
    mutation_created: bool

    @model_validator(mode="after")
    def versions_and_identity_match(self) -> Self:
        if self.application.id != self.review.application_id:
            raise ValueError("application must match the review")
        if self.action.id != self.review.action_item_id:
            raise ValueError("action must match the review")
        if self.action.version < self.review.action_version:
            raise ValueError("action cannot predate the reviewed version")
        if self.application.version < self.review.application_version:
            raise ValueError("application cannot predate the reviewed version")
        if self.mutation_created and (
            self.action.version != self.review.action_version
            or self.application.version != self.review.application_version
            or self.application.current_action != self.action
            or self.action.due_on != self.review.new_due_on
        ):
            raise ValueError(
                "a newly created review must expose its exact current action result"
            )
        return self


class WeeklyReviewWindow(ContractModel):
    starts_on: date
    ends_on: date

    @model_validator(mode="after")
    def window_is_ordered(self) -> Self:
        if self.starts_on > self.ends_on:
            raise ValueError("review window starts_on cannot follow ends_on")
        return self


class WeeklyReviewPolicy(ContractModel):
    version: Literal["weekly-review-v1"] = "weekly-review-v1"
    observation_window_days: Literal[84] = 84
    application_maturity_days: Literal[14] = 14
    outreach_maturity_days: Literal[14] = 14
    stale_definition: Literal["overdue_open_action"] = "overdue_open_action"


class WeeklyReviewStaleApplication(ContractModel):
    application: ApplicationSummary
    posting: ApplicationPostingSummary
    current_action: ActionItemResponse
    days_overdue: int = Field(ge=1)

    @model_validator(mode="after")
    def identities_match(self) -> Self:
        if self.current_action.application_id != self.application.id:
            raise ValueError("current action must belong to the application")
        if self.posting != self.application.posting:
            raise ValueError("posting must match the application's pinned summary")
        return self


class FunnelStage(str, Enum):
    screen = "screen"
    interview = "interview"
    offer = "offer"


class FunnelStageMetric(ContractModel):
    stage: FunnelStage
    cohort_total: int = Field(ge=0)
    mature: int = Field(ge=0)
    evaluable: int = Field(ge=0)
    immature: int = Field(ge=0)
    censored_open: int = Field(ge=0)
    converted: int = Field(ge=0)
    late_converted: int = Field(default=0, ge=0)
    missing: int = Field(ge=0)
    rate: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def counts_and_rate_are_consistent(self) -> Self:
        if self.cohort_total != self.mature + self.immature:
            raise ValueError("cohort_total must partition into mature and immature")
        if self.mature != self.evaluable + self.missing:
            raise ValueError("mature must partition into evaluable and missing")
        if self.censored_open > self.immature:
            raise ValueError("censored_open cannot exceed immature")
        if self.converted > self.evaluable:
            raise ValueError("converted cannot exceed evaluable")
        if self.late_converted > self.evaluable - self.converted:
            raise ValueError("late_converted must be a disjoint evaluable subset")
        expected = None if self.evaluable == 0 else self.converted / self.evaluable
        if expected is None:
            if self.rate is not None:
                raise ValueError("rate must be null when no rows are evaluable")
        elif self.rate is None or abs(self.rate - expected) > 1e-9:
            raise ValueError("rate must equal converted divided by evaluable")
        return self


class FunnelSegmentMetric(ContractModel):
    key: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=120)
    cohort_total: int = Field(ge=0)
    missing: int = Field(ge=0)
    stages: list[FunnelStageMetric] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def stages_are_complete(self) -> Self:
        if [item.stage for item in self.stages] != list(FunnelStage):
            raise ValueError("segment stages must be screen, interview, then offer")
        if any(item.cohort_total != self.cohort_total for item in self.stages):
            raise ValueError("segment stage cohorts must match cohort_total")
        if self.missing > self.cohort_total:
            raise ValueError("missing cannot exceed cohort_total")
        return self


class WeeklyReviewFunnel(ContractModel):
    overall: list[FunnelStageMetric] = Field(min_length=3, max_length=3)
    by_acquisition_source: list[FunnelSegmentMetric] = Field(default_factory=list)
    by_career_track: list[FunnelSegmentMetric] = Field(default_factory=list)
    by_assessment_band: list[FunnelSegmentMetric] = Field(default_factory=list)
    attribution_missing: int = Field(ge=0)
    assessment_missing: int = Field(ge=0)

    @model_validator(mode="after")
    def dimensions_are_unique_and_overall_is_complete(self) -> Self:
        if [item.stage for item in self.overall] != list(FunnelStage):
            raise ValueError("overall stages must be screen, interview, then offer")
        for groups in (
            self.by_acquisition_source,
            self.by_career_track,
            self.by_assessment_band,
        ):
            keys = [group.key for group in groups]
            if len(keys) != len(set(keys)):
                raise ValueError("funnel segment keys must be unique per dimension")
        return self


class OutreachObservedMetric(ContractModel):
    key: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=120)
    reached: int = Field(ge=0)
    mature: int = Field(ge=0)
    evaluable: int = Field(ge=0)
    successes: int = Field(ge=0)
    censored_open: int = Field(ge=0)
    immature: int = Field(ge=0)
    ambiguity_excluded: int = Field(ge=0)
    observed_rate: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def counts_and_rate_are_consistent(self) -> Self:
        if self.mature != self.evaluable:
            raise ValueError("all mature outreach attempts must be evaluable")
        if self.censored_open != self.immature:
            raise ValueError("censored_open must equal immature")
        if self.reached != self.mature + self.immature + self.ambiguity_excluded:
            raise ValueError("reached must partition into evaluable, open, and ambiguous")
        if self.successes > self.evaluable:
            raise ValueError("successes cannot exceed evaluable")
        expected = None if self.evaluable == 0 else self.successes / self.evaluable
        if expected is None:
            if self.observed_rate is not None:
                raise ValueError("observed_rate must be null without evaluable attempts")
        elif self.observed_rate is None or abs(self.observed_rate - expected) > 1e-9:
            raise ValueError("observed_rate must equal successes divided by evaluable")
        return self


class OutreachRescueMetric(ContractModel):
    position: Literal[2, 3, 4, 5]
    reached: int = Field(ge=0)
    mature: int = Field(ge=0)
    evaluable: int = Field(ge=0)
    successes: int = Field(ge=0)
    censored_open: int = Field(ge=0)
    immature: int = Field(ge=0)
    ambiguity_excluded: int = Field(ge=0)
    observed_rate: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def counts_and_rate_are_consistent(self) -> Self:
        OutreachObservedMetric(
            key=str(self.position),
            label=f"Position {self.position}",
            reached=self.reached,
            mature=self.mature,
            evaluable=self.evaluable,
            successes=self.successes,
            censored_open=self.censored_open,
            immature=self.immature,
            ambiguity_excluded=self.ambiguity_excluded,
            observed_rate=self.observed_rate,
        )
        return self


class WeeklyReviewOutreach(ContractModel):
    noncausal_label: Literal[
        "Observed association only; outreach position and contact type are not causal."
    ] = "Observed association only; outreach position and contact type are not causal."
    by_contact_category: list[OutreachObservedMetric] = Field(default_factory=list)
    by_sequence_position: list[OutreachObservedMetric] = Field(default_factory=list)
    contacts_two_through_five: list[OutreachRescueMetric] = Field(
        min_length=4, max_length=4
    )
    unattributed_legacy_successes: int = Field(ge=0)

    @model_validator(mode="after")
    def outreach_groups_are_unique(self) -> Self:
        for groups in (self.by_contact_category, self.by_sequence_position):
            keys = [group.key for group in groups]
            if len(keys) != len(set(keys)):
                raise ValueError("outreach metric keys must be unique per grouping")
        if [item.position for item in self.contacts_two_through_five] != [2, 3, 4, 5]:
            raise ValueError("rescue metrics must cover positions two through five")
        return self


class WeeklyReviewResponse(ContractModel):
    data_source: Literal["database"] = "database"
    as_of: UTCDateTime
    owner_timezone: str = Field(min_length=1, max_length=64)
    owner_local_date: date
    window: WeeklyReviewWindow
    policy: WeeklyReviewPolicy
    stale_application_total: int = Field(ge=0)
    stale_applications: list[WeeklyReviewStaleApplication] = Field(
        default_factory=list, max_length=50
    )
    funnel: WeeklyReviewFunnel
    outreach: WeeklyReviewOutreach

    @model_validator(mode="after")
    def stale_page_is_bounded(self) -> Self:
        if self.stale_application_total < len(self.stale_applications):
            raise ValueError("stale_application_total cannot be smaller than the page")
        return self


__all__ = [
    "ApplicationActionReviewCreate",
    "ApplicationActionReviewDecision",
    "ApplicationActionReviewMutationResponse",
    "ApplicationActionReviewResponse",
    "FunnelSegmentMetric",
    "FunnelStage",
    "FunnelStageMetric",
    "OutreachObservedMetric",
    "OutreachRescueMetric",
    "WeeklyReviewFunnel",
    "WeeklyReviewOutreach",
    "WeeklyReviewPolicy",
    "WeeklyReviewResponse",
    "WeeklyReviewStaleApplication",
    "WeeklyReviewWindow",
]
