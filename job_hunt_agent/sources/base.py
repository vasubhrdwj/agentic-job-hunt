"""Shared contracts implemented by job-source adapters and their resolver."""

from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable
from urllib.parse import unquote
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from job_hunt_agent.schemas import Company, CompanySource, JobCriteria, Role


class FetchScope(str, Enum):
    """How much of a source inventory a fetch attempted to observe."""

    criteria_filtered = "criteria_filtered"
    board_snapshot = "board_snapshot"


class FetchCompleteness(str, Enum):
    """Whether the claimed fetch scope was observed in full."""

    complete = "complete"
    partial = "partial"
    unknown = "unknown"


class SourceFetchResult(BaseModel):
    """Roles plus honest, machine-readable metadata about their fetch.

    Existing adapters filter internally and collapse transport failures into an
    empty list. The resolver therefore labels their results as
    ``criteria_filtered`` and ``partial``. Only a future criteria-free adapter
    that accounts for every page may return an authoritative complete board
    snapshot.
    """

    fetch_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=64)
    company_slug: str = Field(min_length=1, max_length=120)
    source: CompanySource
    started_at: datetime
    completed_at: datetime
    scope: FetchScope = FetchScope.criteria_filtered
    completeness: FetchCompleteness = FetchCompleteness.partial
    roles: list[Role] = Field(default_factory=list)
    observed_count: int = Field(default=0, ge=0)
    returned_count: int = Field(default=0, ge=0)
    cache_hit: bool = False
    used_fallback: bool = False
    warning_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def metadata_is_consistent(self) -> "SourceFetchResult":
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        if self.returned_count != len(self.roles):
            raise ValueError("returned_count must equal the number of returned roles")
        if self.observed_count < self.returned_count:
            raise ValueError("observed_count cannot be lower than returned_count")
        if len(self.warning_codes) != len(set(self.warning_codes)):
            raise ValueError("warning_codes must not contain duplicates")
        return self

    @property
    def authoritative_for_closure(self) -> bool:
        """Whether absence from this result may contribute to closure logic."""

        return (
            self.scope is FetchScope.board_snapshot
            and self.completeness is FetchCompleteness.complete
        )


def safe_url_path_parts(path: str) -> tuple[str, ...] | None:
    """Return decoded path segments, rejecting browser-normalized traversal."""
    parts: list[str] = []
    for raw_part in path.split("/"):
        if not raw_part:
            continue
        decoded = raw_part
        for _ in range(2):
            decoded = unquote(decoded)
        if (
            decoded in {".", ".."}
            or "/" in decoded
            or "\\" in decoded
            or any(ord(character) < 32 for character in decoded)
        ):
            return None
        parts.append(decoded)
    return tuple(parts)


@runtime_checkable
class SourceAdapter(Protocol):
    """Structural contract for fetching roles from a company job source."""

    name: str

    def supports(self, company: Company) -> bool:
        """Return whether this adapter can fetch roles for ``company``."""
        ...

    def fetch_open_roles(
        self,
        company: Company,
        criteria: JobCriteria,
    ) -> list[Role]:
        """Fetch open roles matching ``criteria`` without fabricating results."""
        ...


@runtime_checkable
class BroadSourceAdapter(Protocol):
    """Optional future contract for an authoritative criteria-free inventory."""

    name: str

    def supports(self, company: Company) -> bool:
        """Return whether this adapter can fetch roles for ``company``."""
        ...

    def fetch_open_snapshot(self, company: Company) -> SourceFetchResult:
        """Fetch a board inventory without applying user search criteria."""
        ...


__all__ = [
    "BroadSourceAdapter",
    "FetchCompleteness",
    "FetchScope",
    "SourceAdapter",
    "SourceFetchResult",
    "safe_url_path_parts",
]
