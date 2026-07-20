"""Durable creation of due, provider-free saved-search scan work.

Scheduling is one database transaction: lock due searches, pin their exact
criteria and company-source inventory, enqueue the scan, then advance the next
slot. PostgreSQL ``SKIP LOCKED`` lets multiple workers tick safely, while the
scan and queue uniqueness constraints remain the durable replay backstop.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from .job_queue import enqueue_job, utcnow
from .models import OpportunityScan, OpportunityScanSource, SavedSearch
from .opportunity_scan_worker import SCAN_JOB_KIND
from .profile_schemas import SavedSearchCriteria, SavedSearchSchedule
from .saved_search_repository import calculate_next_scan_at
from .scheduler import scheduled_slot_key
from .schemas import Company
from .sources.registry import CompanyRegistry, load_company_pack


DEFAULT_SCHEDULED_SCAN_BATCH_SIZE = 10
MAX_SCHEDULED_SCAN_BATCH_SIZE = 100


@dataclass(frozen=True)
class ScheduledScanEnqueue:
    saved_search_id: str
    scan_id: str
    job_id: str
    scheduled_for: datetime
    created: bool


@dataclass(frozen=True)
class ScheduledScanBatch:
    items: tuple[ScheduledScanEnqueue, ...]
    invalid_search_count: int = 0

    @property
    def created_count(self) -> int:
        return sum(item.created for item in self.items)


RegistryLoader = Callable[[str], CompanyRegistry]


def enqueue_due_saved_search_scans(
    session: Session,
    *,
    now: datetime | None = None,
    limit: int = DEFAULT_SCHEDULED_SCAN_BATCH_SIZE,
    registry_loader: RegistryLoader = load_company_pack,
) -> ScheduledScanBatch:
    """Atomically enqueue a bounded batch of due automatic saved searches.

    Invalid persisted configuration is isolated and durably pauses that search,
    so one broken row cannot fail silently or starve every later owner. The
    owner can review, save, and reactivate it from the search workspace.
    """

    current = _as_utc(now or utcnow())
    batch_limit = max(1, min(limit, MAX_SCHEDULED_SCAN_BATCH_SIZE))
    searches = list(
        session.scalars(
            due_saved_search_statement(now=current, limit=batch_limit)
        )
    )
    enqueued: list[ScheduledScanEnqueue] = []
    invalid_search_count = 0
    for search in searches:
        try:
            criteria, companies = _validated_scan_inputs(
                search,
                registry_loader=registry_loader,
            )
        except (AttributeError, TypeError, ValueError):
            invalid_search_count += 1
            _pause_invalid_search(search, now=current)
            continue
        enqueued.append(
            _enqueue_locked_search_slot(
                session,
                search=search,
                criteria=criteria,
                companies=companies,
                now=current,
            )
        )
    return ScheduledScanBatch(
        items=tuple(enqueued),
        invalid_search_count=invalid_search_count,
    )


def due_saved_search_statement(*, now: datetime, limit: int) -> Select[tuple[SavedSearch]]:
    """Build the portable due-row claim used by embedded scheduler ticks."""

    return (
        select(SavedSearch)
        .where(
            SavedSearch.active.is_(True),
            SavedSearch.cadence != "manual",
            SavedSearch.next_scan_at.is_not(None),
            SavedSearch.next_scan_at <= _as_utc(now),
        )
        .order_by(SavedSearch.next_scan_at, SavedSearch.id)
        .limit(max(1, min(limit, MAX_SCHEDULED_SCAN_BATCH_SIZE)))
        .with_for_update(skip_locked=True)
    )


def _validated_scan_inputs(
    search: SavedSearch,
    *,
    registry_loader: RegistryLoader,
) -> tuple[dict[str, Any], tuple[Company, ...]]:
    _schedule_from_search(search)
    criteria = SavedSearchCriteria.model_validate(search.criteria).model_dump(mode="json")
    registry = registry_loader(search.pack)
    companies = registry.active_companies
    if not companies:
        raise ValueError("saved search company pack has no active companies")
    return criteria, companies


def _enqueue_locked_search_slot(
    session: Session,
    *,
    search: SavedSearch,
    criteria: dict[str, Any],
    companies: tuple[Company, ...],
    now: datetime,
) -> ScheduledScanEnqueue:
    if (
        not search.active
        or search.cadence == "manual"
        or search.next_scan_at is None
        or _as_utc(search.next_scan_at) > now
    ):
        raise ValueError("saved search is not due for automatic scanning")

    scheduled_for = _as_utc(search.next_scan_at)
    slot_key = scheduled_slot_key(
        SCAN_JOB_KIND,
        f"{search.id}:v{search.version}",
        scheduled_for,
    )
    request_hash = _scheduled_request_hash(
        search=search,
        criteria=criteria,
        companies=companies,
        scheduled_for=scheduled_for,
    )
    scan = session.scalar(
        select(OpportunityScan)
        .where(
            OpportunityScan.owner_id == search.owner_id,
            OpportunityScan.saved_search_id == search.id,
            OpportunityScan.dedupe_key == slot_key,
        )
        .with_for_update()
    )
    created = scan is None
    if scan is None:
        candidate = OpportunityScan(
            id=uuid4().hex,
            owner_id=search.owner_id,
            saved_search_id=search.id,
            saved_search_version=search.version,
            criteria_schema_version=search.criteria_schema_version,
            criteria_snapshot=criteria,
            pack_snapshot=search.pack,
            trigger="scheduled",
            scheduled_for=scheduled_for,
            dedupe_key=slot_key,
            idempotency_key_hash=None,
            request_hash=request_hash,
            status="queued",
            stage="queued",
            source_count=len(companies),
            terminal_source_count=0,
            successful_source_count=0,
            failed_source_count=0,
            observed_count=0,
            new_posting_count=0,
            changed_posting_count=0,
            new_opportunity_count=0,
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(candidate)
        session.flush()
        scan = candidate

    assert scan is not None
    _require_exact_replay(
        scan,
        search=search,
        scheduled_for=scheduled_for,
        request_hash=request_hash,
    )
    if created:
        session.add_all(
            [
                OpportunityScanSource(
                    owner_id=search.owner_id,
                    opportunity_scan_id=scan.id,
                    company_slug=company.slug,
                    source=company.source.value,
                    status="pending",
                    fetch_scope="criteria_filtered",
                    completeness="unknown",
                    observed_count=0,
                    returned_count=0,
                    persisted_count=0,
                    warning_codes=[],
                    used_fallback=False,
                    cache_hit=False,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
                for company in companies
            ]
        )
        session.flush()

    queued = enqueue_job(
        session,
        kind=SCAN_JOB_KIND,
        dedupe_key=slot_key,
        owner_id=search.owner_id,
        subject_type="opportunity_scan",
        subject_id=scan.id,
        payload={
            "opportunity_scan_id": scan.id,
            "saved_search_id": search.id,
            "saved_search_version": search.version,
        },
        run_after=now,
        actor="scheduler",
    )
    if queued.job.subject_id != scan.id:
        raise RuntimeError("scheduled scan queue identity is inconsistent")
    if scan.background_job_id not in {None, queued.job.id}:
        raise RuntimeError("scheduled scan background job is inconsistent")
    scan.background_job_id = queued.job.id
    scan.updated_at = now

    # ``next_scan_at`` and ``last_scan_at`` are operational metadata, not
    # owner-authored search content. Keep the content version pinned so this
    # scan remains the current assessment input until the owner edits it.
    _advance_operational_slot(search, after=now)
    session.flush()
    return ScheduledScanEnqueue(
        saved_search_id=search.id,
        scan_id=scan.id,
        job_id=queued.job.id,
        scheduled_for=scheduled_for,
        created=created,
    )


def _require_exact_replay(
    scan: OpportunityScan,
    *,
    search: SavedSearch,
    scheduled_for: datetime,
    request_hash: str,
) -> None:
    if (
        scan.trigger != "scheduled"
        or scan.saved_search_version != search.version
        or scan.criteria_schema_version != search.criteria_schema_version
        or scan.pack_snapshot != search.pack
        or _as_utc(scan.scheduled_for) != scheduled_for
        or scan.request_hash != request_hash
    ):
        raise RuntimeError("scheduled scan slot replay is inconsistent")


def _scheduled_request_hash(
    *,
    search: SavedSearch,
    criteria: dict[str, Any],
    companies: tuple[Company, ...],
    scheduled_for: datetime,
) -> str:
    request = {
        "criteria": criteria,
        "criteria_schema_version": search.criteria_schema_version,
        "pack": search.pack,
        "saved_search_id": search.id,
        "saved_search_version": search.version,
        "scheduled_for": scheduled_for.replace(microsecond=0).isoformat(),
        "sources": [
            {"company_slug": company.slug, "source": company.source.value}
            for company in companies
        ],
        "trigger": "scheduled",
    }
    encoded = json.dumps(request, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _schedule_from_search(search: SavedSearch) -> SavedSearchSchedule:
    raw_schedule = search.schedule
    if not isinstance(raw_schedule, Mapping):
        raise ValueError("saved search schedule must be an object")
    local_time = raw_schedule.get("local_time")
    return SavedSearchSchedule(
        cadence=search.cadence,
        timezone=search.timezone,
        local_time=time.fromisoformat(local_time) if local_time else None,
        days_of_week=raw_schedule.get("days_of_week", []),
    )


def _pause_invalid_search(search: SavedSearch, *, now: datetime) -> None:
    search.active = False
    search.next_scan_at = None
    search.updated_at = now


def _advance_operational_slot(search: SavedSearch, *, after: datetime) -> None:
    try:
        next_scan_at = calculate_next_scan_at(
            _schedule_from_search(search),
            after=after,
        )
    except (AttributeError, TypeError, ValueError):
        # Callers validate before enqueue. Keep this helper fail-safe for a
        # legacy row that changes between validation and advancement.
        _pause_invalid_search(search, now=_as_utc(after))
        return
    search.next_scan_at = next_scan_at
    search.updated_at = after


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "DEFAULT_SCHEDULED_SCAN_BATCH_SIZE",
    "MAX_SCHEDULED_SCAN_BATCH_SIZE",
    "ScheduledScanBatch",
    "ScheduledScanEnqueue",
    "due_saved_search_statement",
    "enqueue_due_saved_search_scans",
]
