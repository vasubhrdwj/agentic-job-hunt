"""Shared worker freshness and capability checks.

The web process uses these checks both for operational reporting and before it
creates work that only a background worker can complete. Keeping the rules in
one place prevents the UI health signal and the mutation gate from disagreeing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import WorkerHeartbeat


DEFAULT_WORKER_HEARTBEAT_MAX_AGE_SECONDS = 90
ROLE_SCAN_JOB_KIND = "scan_saved_search"
WorkerCapabilityReason = Literal[
    "available",
    "no_fresh_worker",
    "unsupported_kind",
    "incompatible_build",
]


@dataclass(frozen=True)
class WorkerHeartbeatSnapshot:
    heartbeats: tuple[WorkerHeartbeat, ...]
    fresh_heartbeats: tuple[WorkerHeartbeat, ...]
    checked_at: datetime
    max_age_seconds: int


@dataclass(frozen=True)
class WorkerCapability:
    available: bool
    reason: WorkerCapabilityReason
    fresh_worker_count: int
    compatible_worker_count: int

    def public_payload(self) -> dict[str, object]:
        return {
            "available": self.available,
            "reason": None if self.available else self.reason,
            "fresh_worker_count": self.fresh_worker_count,
            "compatible_worker_count": self.compatible_worker_count,
        }


def load_worker_heartbeat_snapshot(
    session: Session,
    *,
    now: datetime | None = None,
) -> WorkerHeartbeatSnapshot:
    current = as_utc(now or datetime.now(timezone.utc))
    heartbeats = tuple(
        session.scalars(
            select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc())
        )
    )
    max_age_seconds = worker_heartbeat_max_age_seconds()
    fresh = tuple(
        heartbeat
        for heartbeat in heartbeats
        if heartbeat_age_seconds(heartbeat, now=current) <= max_age_seconds
    )
    return WorkerHeartbeatSnapshot(
        heartbeats=heartbeats,
        fresh_heartbeats=fresh,
        checked_at=current,
        max_age_seconds=max_age_seconds,
    )


def assess_worker_capability(
    snapshot: WorkerHeartbeatSnapshot,
    *,
    kind: str,
    expected_build_version: str | None = None,
) -> WorkerCapability:
    fresh = snapshot.fresh_heartbeats
    if not fresh:
        return WorkerCapability(
            available=False,
            reason="no_fresh_worker",
            fresh_worker_count=0,
            compatible_worker_count=0,
        )

    supporting = tuple(
        heartbeat
        for heartbeat in fresh
        if kind in {
            value
            for value in heartbeat.supported_kinds
            if isinstance(value, str)
        }
    )
    if not supporting:
        return WorkerCapability(
            available=False,
            reason="unsupported_kind",
            fresh_worker_count=len(fresh),
            compatible_worker_count=0,
        )

    expected_build = _normalize_build_version(expected_build_version)
    compatible = (
        supporting
        if expected_build is None
        else tuple(
            heartbeat
            for heartbeat in supporting
            if _normalize_build_version(heartbeat.build_version) == expected_build
        )
    )
    if not compatible:
        return WorkerCapability(
            available=False,
            reason="incompatible_build",
            fresh_worker_count=len(fresh),
            compatible_worker_count=0,
        )

    return WorkerCapability(
        available=True,
        reason="available",
        fresh_worker_count=len(fresh),
        compatible_worker_count=len(compatible),
    )


def load_worker_capability(
    session: Session,
    *,
    kind: str,
    now: datetime | None = None,
) -> WorkerCapability:
    return assess_worker_capability(
        load_worker_heartbeat_snapshot(session, now=now),
        kind=kind,
        expected_build_version=current_build_version(),
    )


def current_build_version() -> str | None:
    return _normalize_build_version(
        os.getenv("RENDER_GIT_COMMIT") or os.getenv("APP_VERSION")
    )


def worker_heartbeat_max_age_seconds() -> int:
    raw = os.getenv(
        "JOB_HUNT_WORKER_HEARTBEAT_MAX_AGE_SECONDS",
        str(DEFAULT_WORKER_HEARTBEAT_MAX_AGE_SECONDS),
    ).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_WORKER_HEARTBEAT_MAX_AGE_SECONDS


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def heartbeat_age_seconds(
    heartbeat: WorkerHeartbeat,
    *,
    now: datetime,
) -> float:
    return max(0.0, (as_utc(now) - as_utc(heartbeat.last_seen_at)).total_seconds())


def _normalize_build_version(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


__all__ = [
    "DEFAULT_WORKER_HEARTBEAT_MAX_AGE_SECONDS",
    "ROLE_SCAN_JOB_KIND",
    "WorkerCapability",
    "WorkerCapabilityReason",
    "WorkerHeartbeatSnapshot",
    "as_utc",
    "assess_worker_capability",
    "current_build_version",
    "heartbeat_age_seconds",
    "load_worker_capability",
    "load_worker_heartbeat_snapshot",
    "worker_heartbeat_max_age_seconds",
]
