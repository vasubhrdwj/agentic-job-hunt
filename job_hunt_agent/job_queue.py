"""Generic durable background-job transitions for the practical product.

All functions operate inside a caller-owned SQLAlchemy transaction. PostgreSQL
workers claim with ``FOR UPDATE SKIP LOCKED``; SQLite remains useful for fast
state-machine tests but is not the production concurrency backend.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import BackgroundJob, BackgroundJobEvent, WorkerHeartbeat


ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "dead_letter"}
MAX_PAYLOAD_BYTES = 16_384
MAX_ERROR_CHARS = 200
_SAFE_VALUE_RE = re.compile(r"[^A-Za-z0-9_.:/ -]+")
_SAFE_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,99}")
_PAYLOAD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_REFERENCE_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/+~-]{0,127}$")
_CONFIG_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+~-]{0,127}$")
_CONFIG_TOKEN_KEYS = {
    "algorithm",
    "company_slug",
    "mode",
    "pack",
    "source",
}
_CONFIG_INTEGER_KEYS = {
    "candidate_limit",
    "priority",
    "target_count",
}
_CONFIG_BOOLEAN_KEYS = {
    "dry_run",
    "force",
    "full_refresh",
}
_MAX_REFERENCE_LIST_LENGTH = 100


@dataclass(frozen=True)
class EnqueueResult:
    job: BackgroundJob
    created: bool


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def validate_payload(payload: dict[str, Any]) -> None:
    """Allow only flat record references and tightly typed execution config.

    Queue rows are operational metadata, not a second persistence layer. Private
    resume, opportunity, or outreach content must live in its protected source
    table and be referenced here by an opaque id.
    """

    if not isinstance(payload, dict):
        raise ValueError("background job payload must be a JSON object")

    for key, value in payload.items():
        if not isinstance(key, str) or _PAYLOAD_KEY_RE.fullmatch(key) is None:
            raise ValueError("background job payload keys must use lower snake_case")
        if key.endswith("_ids"):
            _validate_reference_list(key, value)
        elif key.endswith("_id"):
            _validate_reference(key, value)
        elif key.endswith("_versions"):
            _validate_version_list(key, value)
        elif key.endswith("_version"):
            _validate_version(key, value)
        elif key in _CONFIG_TOKEN_KEYS:
            if not isinstance(value, str) or _CONFIG_TOKEN_RE.fullmatch(value) is None:
                raise ValueError(f"background job payload field {key!r} must be a short token")
        elif key == "country":
            if not isinstance(value, str) or re.fullmatch(r"[A-Za-z]{2,3}", value) is None:
                raise ValueError(
                    "background job payload field 'country' must be a 2-3 letter code"
                )
        elif key == "scheduled_for":
            _validate_scheduled_for(value)
        elif key in _CONFIG_INTEGER_KEYS:
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10_000:
                raise ValueError(
                    f"background job payload field {key!r} must be an integer from 0 to 10000"
                )
        elif key in _CONFIG_BOOLEAN_KEYS:
            if not isinstance(value, bool):
                raise ValueError(f"background job payload field {key!r} must be a boolean")
        else:
            raise ValueError(
                f"background job payload field {key!r} is not allowed; persist private text "
                "separately and enqueue only its id"
            )

    try:
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("background job payload must be JSON serializable") from exc
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"background job payload exceeds {MAX_PAYLOAD_BYTES} bytes")


def enqueue_job(
    session: Session,
    *,
    kind: str,
    dedupe_key: str,
    owner_id: str | None = None,
    subject_type: str | None = None,
    subject_id: str | None = None,
    payload: dict[str, Any] | None = None,
    priority: int = 100,
    max_attempts: int = 3,
    run_after: datetime | None = None,
    actor: str = "system",
) -> EnqueueResult:
    """Create one auditable job or return the existing deduplicated row."""

    normalized_kind = kind.strip()
    normalized_key = dedupe_key.strip()
    normalized_owner_id = owner_id.strip() if owner_id is not None else None
    if not normalized_kind or len(normalized_kind) > 64:
        raise ValueError("background job kind must be 1-64 characters")
    if not normalized_key or len(normalized_key) > 255:
        raise ValueError("background job dedupe_key must be 1-255 characters")
    if owner_id is not None and (not normalized_owner_id or len(normalized_owner_id) > 64):
        raise ValueError("background job owner_id must be 1-64 characters")
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    normalized_payload = {} if payload is None else payload
    validate_payload(normalized_payload)
    dedupe_scope = f"owner:{normalized_owner_id}" if normalized_owner_id else "system"

    existing = session.scalar(
        select(BackgroundJob).where(
            BackgroundJob.dedupe_scope == dedupe_scope,
            BackgroundJob.kind == normalized_kind,
            BackgroundJob.dedupe_key == normalized_key,
        )
    )
    if existing is not None:
        return EnqueueResult(job=existing, created=False)

    job = BackgroundJob(
        kind=normalized_kind,
        dedupe_key=normalized_key,
        owner_id=normalized_owner_id,
        dedupe_scope=dedupe_scope,
        subject_type=subject_type,
        subject_id=subject_id,
        payload=normalized_payload,
        priority=priority,
        max_attempts=max_attempts,
        run_after=run_after or utcnow(),
    )
    try:
        with session.begin_nested():
            session.add(job)
            session.flush()
    except IntegrityError:
        existing = session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.dedupe_scope == dedupe_scope,
                BackgroundJob.kind == normalized_kind,
                BackgroundJob.dedupe_key == normalized_key,
            )
        )
        if existing is None:
            raise
        return EnqueueResult(job=existing, created=False)

    _record_event(session, job, from_status=None, to_status="queued", actor=actor)
    return EnqueueResult(job=job, created=True)


def get_job(session: Session, job_id: str) -> BackgroundJob | None:
    return session.get(BackgroundJob, job_id)


def claim_next_job(
    session: Session,
    *,
    worker_id: str,
    lease_token: str | None = None,
    lease_seconds: int = 300,
    kinds: Iterable[str] | None = None,
    now: datetime | None = None,
) -> BackgroundJob | None:
    """Claim the next eligible job without blocking another Postgres worker."""

    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    current = now or utcnow()
    statement = (
        select(BackgroundJob)
        .where(
            BackgroundJob.status == "queued",
            BackgroundJob.run_after <= current,
            BackgroundJob.attempt_count < BackgroundJob.max_attempts,
        )
        .order_by(BackgroundJob.priority, BackgroundJob.run_after, BackgroundJob.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    normalized_kinds = {kind.strip() for kind in kinds or () if kind.strip()}
    if normalized_kinds:
        statement = statement.where(BackgroundJob.kind.in_(normalized_kinds))
    job = session.scalar(statement)
    if job is None:
        return None

    previous = job.status
    job.status = "running"
    job.attempt_count += 1
    job.lease_owner = worker_id
    job.lease_token = lease_token or uuid4().hex
    job.lease_expires_at = current + timedelta(seconds=lease_seconds)
    job.heartbeat_at = current
    job.started_at = job.started_at or current
    job.stage = "claimed"
    job.updated_at = current
    job.version += 1
    _record_event(session, job, from_status=previous, to_status="running", actor=worker_id)
    session.flush()
    return job


def heartbeat_job(
    session: Session,
    job_id: str,
    *,
    worker_id: str,
    lease_token: str,
    lease_seconds: int = 300,
    now: datetime | None = None,
) -> bool:
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    current = now or utcnow()
    job = _owned_running_job(session, job_id, worker_id, lease_token, current)
    if job is None:
        return False
    job.heartbeat_at = current
    job.lease_expires_at = current + timedelta(seconds=lease_seconds)
    job.updated_at = current
    job.version += 1
    session.flush()
    return True


def update_job_stage(
    session: Session,
    job_id: str,
    *,
    worker_id: str,
    lease_token: str,
    stage: str,
    checkpoint: str | None = None,
    now: datetime | None = None,
) -> bool:
    current = now or utcnow()
    job = _owned_running_job(session, job_id, worker_id, lease_token, current)
    if job is None:
        return False
    job.stage = _sanitize(stage, fallback="running", limit=100)
    job.stage_checkpoint = (
        _sanitize(checkpoint, fallback="checkpoint", limit=200)
        if checkpoint is not None
        else None
    )
    job.updated_at = current
    job.version += 1
    session.flush()
    return True


def complete_job(
    session: Session,
    job_id: str,
    *,
    worker_id: str,
    lease_token: str,
    now: datetime | None = None,
) -> BackgroundJob | None:
    current = now or utcnow()
    job = _owned_running_job(session, job_id, worker_id, lease_token, current)
    if job is None:
        return None
    previous = job.status
    if job.cancel_requested_at is not None:
        _finalize_cancelled(job, current)
        to_status = "cancelled"
        reason = "cancel_requested"
    else:
        job.status = "succeeded"
        job.stage = "succeeded"
        job.completed_at = current
        _clear_lease(job)
        to_status = "succeeded"
        reason = None
    job.updated_at = current
    job.version += 1
    _record_event(
        session,
        job,
        from_status=previous,
        to_status=to_status,
        actor=worker_id,
        reason=reason,
    )
    session.flush()
    return job


def fail_job_attempt(
    session: Session,
    job_id: str,
    *,
    worker_id: str,
    lease_token: str,
    error_code: str,
    retry_delay_seconds: int = 0,
    terminal: bool = False,
    now: datetime | None = None,
) -> BackgroundJob | None:
    current = now or utcnow()
    job = _owned_running_job(session, job_id, worker_id, lease_token, current)
    if job is None:
        return None
    previous = job.status
    if job.cancel_requested_at is not None:
        _finalize_cancelled(job, current)
        reason = "cancel_requested"
    else:
        job.last_error = _sanitize_code(error_code, fallback="job_failed")
        should_dead_letter = terminal or job.attempt_count >= job.max_attempts
        if should_dead_letter:
            job.status = "dead_letter"
            job.stage = "dead_letter"
            job.dead_lettered_at = current
        else:
            job.status = "queued"
            job.stage = "retry_scheduled"
            job.run_after = current + timedelta(seconds=max(0, retry_delay_seconds))
        job.failed_at = current
        _clear_lease(job)
        reason = job.last_error
    job.updated_at = current
    job.version += 1
    _record_event(
        session,
        job,
        from_status=previous,
        to_status=job.status,
        actor=worker_id,
        reason=reason,
    )
    session.flush()
    return job


def cancel_job(
    session: Session,
    job_id: str,
    *,
    actor: str,
    reason: str | None = None,
    now: datetime | None = None,
) -> BackgroundJob | None:
    current = now or utcnow()
    job = session.scalar(select(BackgroundJob).where(BackgroundJob.id == job_id).with_for_update())
    if job is None or job.status in TERMINAL_STATUSES:
        return job
    if job.status == "running":
        if job.cancel_requested_at is not None:
            return job
        job.cancel_requested_at = current
        job.updated_at = current
        job.version += 1
        _record_event(
            session,
            job,
            from_status="running",
            to_status="running",
            actor=actor,
            reason="cancel_requested",
        )
        session.flush()
        return job

    previous = job.status
    job.status = "cancelled"
    job.stage = "cancelled"
    job.cancel_requested_at = current
    job.cancelled_at = current
    _clear_lease(job)
    job.updated_at = current
    job.version += 1
    _record_event(
        session,
        job,
        from_status=previous,
        to_status="cancelled",
        actor=actor,
        reason=_sanitize_code(reason, fallback="cancelled") if reason else None,
    )
    session.flush()
    return job


def recover_stale_jobs(session: Session, *, now: datetime | None = None) -> int:
    current = now or utcnow()
    jobs = list(
        session.scalars(
            select(BackgroundJob)
            .where(
                BackgroundJob.status == "running",
                BackgroundJob.lease_expires_at.is_not(None),
                BackgroundJob.lease_expires_at <= current,
            )
            .with_for_update(skip_locked=True)
        )
    )
    for job in jobs:
        previous = job.status
        if job.cancel_requested_at is not None:
            _finalize_cancelled(job, current)
            reason = "cancel_requested"
        elif job.attempt_count >= job.max_attempts:
            job.status = "dead_letter"
            job.stage = "dead_letter"
            job.dead_lettered_at = current
            job.last_error = "lease_expired"
            _clear_lease(job)
            reason = "lease_expired"
        else:
            job.status = "queued"
            job.stage = "lease_recovered"
            job.run_after = current
            job.last_error = "lease_expired"
            _clear_lease(job)
            reason = "lease_expired"
        job.updated_at = current
        job.version += 1
        _record_event(
            session,
            job,
            from_status=previous,
            to_status=job.status,
            actor="lease-recovery",
            reason=reason,
        )
    session.flush()
    return len(jobs)


def lock_owned_running_job(
    session: Session,
    job_id: str,
    *,
    worker_id: str,
    lease_token: str,
    now: datetime | None = None,
) -> BackgroundJob | None:
    """Lock and return a job only while this worker owns its live lease.

    Durable domain repositories use this before writing private output so the
    output write and the generic queue transition share one transaction.
    """

    return _owned_running_job(
        session,
        job_id,
        worker_id,
        lease_token,
        now or utcnow(),
    )


def requeue_dead_letter_job(
    session: Session,
    job_id: str,
    *,
    actor: str,
    reason: str | None = None,
    now: datetime | None = None,
) -> BackgroundJob | None:
    """Reset a dead-lettered job for one explicit operator retry."""

    current = now or utcnow()
    job = session.scalar(
        select(BackgroundJob).where(BackgroundJob.id == job_id).with_for_update()
    )
    if job is None or job.status != "dead_letter":
        return job

    job.status = "queued"
    job.stage = "operator_requeued"
    job.stage_checkpoint = None
    job.attempt_count = 0
    job.run_after = current
    job.last_error = None
    job.failed_at = None
    job.dead_lettered_at = None
    job.cancel_requested_at = None
    job.cancelled_at = None
    job.completed_at = None
    _clear_lease(job)
    job.updated_at = current
    job.version += 1
    _record_event(
        session,
        job,
        from_status="dead_letter",
        to_status="queued",
        actor=actor,
        reason=_sanitize_code(reason, fallback="operator_requeue") if reason else "operator_requeue",
    )
    session.flush()
    return job


def record_worker_heartbeat(
    session: Session,
    *,
    worker_id: str,
    supported_kinds: Iterable[str],
    current_job_id: str | None = None,
    build_version: str | None = None,
    started_at: datetime | None = None,
    now: datetime | None = None,
) -> WorkerHeartbeat:
    current = now or utcnow()
    heartbeat = session.get(WorkerHeartbeat, worker_id)
    if heartbeat is None:
        heartbeat = WorkerHeartbeat(
            worker_id=worker_id,
            supported_kinds=sorted(set(supported_kinds)),
            current_job_id=current_job_id,
            build_version=build_version,
            started_at=started_at or current,
            last_seen_at=current,
        )
        session.add(heartbeat)
    else:
        heartbeat.supported_kinds = sorted(set(supported_kinds))
        heartbeat.current_job_id = current_job_id
        heartbeat.build_version = build_version
        heartbeat.last_seen_at = current
    session.flush()
    return heartbeat


def latest_worker_heartbeat(session: Session) -> WorkerHeartbeat | None:
    return session.scalar(
        select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(1)
    )


def queue_counts(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(BackgroundJob.status, func.count(BackgroundJob.id)).group_by(BackgroundJob.status)
    )
    return {str(status): int(count) for status, count in rows}


def _owned_running_job(
    session: Session,
    job_id: str,
    worker_id: str,
    lease_token: str,
    now: datetime,
) -> BackgroundJob | None:
    return session.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.id == job_id,
            BackgroundJob.status == "running",
            BackgroundJob.lease_owner == worker_id,
            BackgroundJob.lease_token == lease_token,
            BackgroundJob.lease_expires_at.is_not(None),
            BackgroundJob.lease_expires_at > now,
        )
        .with_for_update()
    )


def _record_event(
    session: Session,
    job: BackgroundJob,
    *,
    from_status: str | None,
    to_status: str,
    actor: str,
    reason: str | None = None,
) -> None:
    session.add(
        BackgroundJobEvent(
            job_id=job.id,
            from_status=from_status,
            to_status=to_status,
            actor=_sanitize(actor, fallback="system", limit=200),
            reason=reason,
        )
    )


def _clear_lease(job: BackgroundJob) -> None:
    job.lease_owner = None
    job.lease_token = None
    job.lease_expires_at = None
    job.heartbeat_at = None


def _finalize_cancelled(job: BackgroundJob, current: datetime) -> None:
    job.status = "cancelled"
    job.stage = "cancelled"
    job.cancelled_at = current
    _clear_lease(job)


def _validate_reference(key: str, value: Any) -> None:
    if not isinstance(value, str) or _REFERENCE_VALUE_RE.fullmatch(value) is None:
        raise ValueError(f"background job payload field {key!r} must be one opaque id")


def _validate_reference_list(key: str, value: Any) -> None:
    if not isinstance(value, list) or len(value) > _MAX_REFERENCE_LIST_LENGTH:
        raise ValueError(
            f"background job payload field {key!r} must be a list of at most "
            f"{_MAX_REFERENCE_LIST_LENGTH} ids"
        )
    for item in value:
        _validate_reference(key, item)


def _validate_version(key: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"background job payload field {key!r} must be a version token")
    if isinstance(value, int):
        if not 0 <= value <= 2_147_483_647:
            raise ValueError(f"background job payload field {key!r} is outside version range")
    elif _CONFIG_TOKEN_RE.fullmatch(value) is None:
        raise ValueError(f"background job payload field {key!r} must be a version token")


def _validate_version_list(key: str, value: Any) -> None:
    if not isinstance(value, list) or len(value) > _MAX_REFERENCE_LIST_LENGTH:
        raise ValueError(
            f"background job payload field {key!r} must be a list of at most "
            f"{_MAX_REFERENCE_LIST_LENGTH} versions"
        )
    for item in value:
        _validate_version(key, item)


def _validate_scheduled_for(value: Any) -> None:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("background job payload field 'scheduled_for' must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "background job payload field 'scheduled_for' must be an ISO timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise ValueError("background job payload field 'scheduled_for' must include a timezone")


def _sanitize(value: str | None, *, fallback: str, limit: int) -> str:
    normalized = _SAFE_VALUE_RE.sub("_", (value or "").strip())[:limit]
    return normalized or fallback


def _sanitize_code(value: str | None, *, fallback: str) -> str:
    """Keep only the leading machine-safe code; discard exception details."""

    match = _SAFE_CODE_RE.match((value or "").strip())
    return match.group(0)[:MAX_ERROR_CHARS] if match is not None else fallback
