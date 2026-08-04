"""Sanitized contracts for an external production cadence wake."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .opportunity_schemas import ContractModel, UTCDateTime


class CadenceTickResponse(ContractModel):
    data_source: Literal["database"] = "database"
    ticked_at: UTCDateTime
    batches: int = Field(ge=0, le=100)
    considered_searches: int = Field(ge=0)
    created_scans: int = Field(ge=0)
    replayed_scans: int = Field(ge=0)
    paused_invalid_searches: int = Field(ge=0)
    saturated: bool
    embedded_worker_alive: bool


__all__ = ["CadenceTickResponse"]
