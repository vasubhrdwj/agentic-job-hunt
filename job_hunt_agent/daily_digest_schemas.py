"""Owner-scoped, provider-free daily job-search digest contracts."""

from __future__ import annotations

from datetime import date
from typing import Literal, Self

from pydantic import Field, model_validator

from .opportunity_schemas import (
    AssessmentConfidence,
    ContractModel,
    OpaqueId,
    OpportunityFitBand,
    ShortText,
    UTCDateTime,
)


class DailyDigestHighlight(ContractModel):
    """One newly discovered role that cleared the product's fit gates."""

    opportunity_id: OpaqueId
    company: str = Field(min_length=1, max_length=240)
    title: str = Field(min_length=1, max_length=300)
    fit_band: Literal[OpportunityFitBand.strong, OpportunityFitBand.promising]
    confidence: AssessmentConfidence
    reasons: list[ShortText] = Field(min_length=1, max_length=3)
    discovered_at: UTCDateTime


class DailyDigestScanSummary(ContractModel):
    """Automatic scan outcomes for the owner's current local day."""

    scheduled: int = Field(ge=0)
    running: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    partial: int = Field(ge=0)
    failed: int = Field(ge=0)

    @model_validator(mode="after")
    def states_cover_scheduled_scans(self) -> Self:
        if self.running + self.succeeded + self.partial + self.failed != self.scheduled:
            raise ValueError("daily scan states must cover every scheduled scan")
        return self


class DailyDigestResponse(ContractModel):
    """A live projection over durable scans, opportunities, and fit evidence."""

    data_source: Literal["database"] = "database"
    local_date: date
    timezone: str = Field(min_length=1, max_length=64)
    period_started_at: UTCDateTime
    generated_at: UTCDateTime
    headline: str = Field(min_length=1, max_length=200)
    new_opportunities: int = Field(ge=0)
    evaluated_opportunities: int = Field(ge=0)
    worth_your_time: int = Field(ge=0)
    assessment_complete: bool
    highlights: list[DailyDigestHighlight] = Field(default_factory=list, max_length=3)
    scans: DailyDigestScanSummary
    active_scheduled_searches: int = Field(ge=0)
    next_scan_at: UTCDateTime | None = None

    @model_validator(mode="after")
    def counts_are_truthful(self) -> Self:
        if self.evaluated_opportunities > self.new_opportunities:
            raise ValueError("evaluated opportunities cannot exceed new opportunities")
        if self.worth_your_time > self.evaluated_opportunities:
            raise ValueError("worth-your-time count cannot exceed evaluated opportunities")
        if len(self.highlights) > self.worth_your_time:
            raise ValueError("digest highlights cannot exceed worth-your-time count")
        if self.assessment_complete != (
            self.evaluated_opportunities == self.new_opportunities
        ):
            raise ValueError("assessment_complete must match evaluated coverage")
        return self


__all__ = [
    "DailyDigestHighlight",
    "DailyDigestResponse",
    "DailyDigestScanSummary",
]
