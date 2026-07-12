"""Idempotent scheduler primitives; saved-search producers arrive in Slice 1."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from .job_queue import EnqueueResult, enqueue_job


@dataclass(frozen=True)
class ScheduledJobSpec:
    kind: str
    subject_type: str
    subject_id: str
    scheduled_for: datetime
    owner_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    priority: int = 100
    max_attempts: int = 3


ScheduleProducer = Callable[[datetime], Iterable[ScheduledJobSpec]]


def scheduled_slot_key(kind: str, subject_id: str, scheduled_for: datetime) -> str:
    normalized = _as_utc(scheduled_for).replace(microsecond=0)
    return f"{kind}:{subject_id}:{normalized.isoformat()}"


def enqueue_for_slot(session: Session, spec: ScheduledJobSpec) -> EnqueueResult:
    return enqueue_job(
        session,
        kind=spec.kind,
        dedupe_key=scheduled_slot_key(spec.kind, spec.subject_id, spec.scheduled_for),
        owner_id=spec.owner_id,
        subject_type=spec.subject_type,
        subject_id=spec.subject_id,
        payload=spec.payload,
        priority=spec.priority,
        max_attempts=spec.max_attempts,
        run_after=spec.scheduled_for,
        actor="scheduler",
    )


def run_scheduler_tick(
    session: Session,
    *,
    producers: Sequence[ScheduleProducer] = (),
    now: datetime | None = None,
) -> list[EnqueueResult]:
    current = now or datetime.now(timezone.utc)
    results: list[EnqueueResult] = []
    for producer in producers:
        for spec in producer(current):
            results.append(enqueue_for_slot(session, spec))
    return results


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
