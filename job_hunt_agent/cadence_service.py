"""Bounded, idempotent production scheduler tick orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .database import Database
from .scheduled_scan_repository import (
    DEFAULT_SCHEDULED_SCAN_BATCH_SIZE,
    MAX_SCHEDULED_SCAN_BATCH_SIZE,
    RegistryLoader,
    enqueue_due_saved_search_scans,
)


DEFAULT_CADENCE_MAX_BATCHES = 10
MAX_CADENCE_MAX_BATCHES = 100


@dataclass(frozen=True)
class CadenceTick:
    ticked_at: datetime
    batches: int
    considered_searches: int
    created_scans: int
    replayed_scans: int
    paused_invalid_searches: int
    saturated: bool


def run_cadence_tick(
    database: Database,
    *,
    now: datetime | None = None,
    batch_size: int = DEFAULT_SCHEDULED_SCAN_BATCH_SIZE,
    max_batches: int = DEFAULT_CADENCE_MAX_BATCHES,
    registry_loader: RegistryLoader | None = None,
) -> CadenceTick:
    """Enqueue all due scans up to explicit bounds, committing per batch.

    Each saved-search slot is already protected by a stable dedupe key and
    database uniqueness. Repeated or concurrent external wakes therefore
    converge on the same scan and queue job.
    """

    current = _as_utc(now or datetime.now(timezone.utc))
    resolved_batch_size = max(1, min(batch_size, MAX_SCHEDULED_SCAN_BATCH_SIZE))
    resolved_max_batches = max(1, min(max_batches, MAX_CADENCE_MAX_BATCHES))
    considered = created = replayed = invalid = batches = 0
    saturated = False
    for index in range(resolved_max_batches):
        with database.session() as session:
            if registry_loader is None:
                batch = enqueue_due_saved_search_scans(
                    session,
                    now=current,
                    limit=resolved_batch_size,
                )
            else:
                batch = enqueue_due_saved_search_scans(
                    session,
                    now=current,
                    limit=resolved_batch_size,
                    registry_loader=registry_loader,
                )
        batches += 1
        batch_considered = len(batch.items) + batch.invalid_search_count
        considered += batch_considered
        created += batch.created_count
        replayed += len(batch.items) - batch.created_count
        invalid += batch.invalid_search_count
        if batch_considered < resolved_batch_size:
            break
        if index == resolved_max_batches - 1:
            saturated = True
    return CadenceTick(
        ticked_at=current,
        batches=batches,
        considered_searches=considered,
        created_scans=created,
        replayed_scans=replayed,
        paused_invalid_searches=invalid,
        saturated=saturated,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "CadenceTick",
    "DEFAULT_CADENCE_MAX_BATCHES",
    "run_cadence_tick",
]
