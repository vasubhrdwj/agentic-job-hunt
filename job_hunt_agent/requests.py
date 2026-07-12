"""Shared request contracts for queued hunt creation and worker execution."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .schemas import JobCriteria
from .security import MAX_RESUME_CHARS


class HuntRequestPayload(BaseModel):
    """Validated user request stored in the encrypted queue envelope."""

    resume_text: str = Field(
        min_length=1,
        max_length=MAX_RESUME_CHARS,
        description="Plain-text resume body.",
    )
    criteria: JobCriteria = Field(description="Filters for the hunt.")
    provider_consent: Literal[True] = Field(
        description=(
            "Explicit consent to send bounded resume excerpts to the configured "
            "paid model provider under the disclosed retention terms."
        ),
    )
    use_self_rag: bool = Field(
        default=True,
        description=(
            "Toggle V8 self-RAG. Set False for the V10 round-1 baseline so the "
            "demo can compare drafts with and without past-trace exemplars."
        ),
    )
    pack: str = Field(
        default="backend_india",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
        description="Curated company pack used for first-party job discovery.",
    )

    @field_validator("resume_text")
    @classmethod
    def resume_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("resume_text must not be blank")
        return value


def canonical_request_json(payload: HuntRequestPayload) -> str:
    """Stable JSON used for encrypted storage and idempotency hashing."""

    return json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
