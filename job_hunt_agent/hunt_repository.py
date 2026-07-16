"""Encrypted PostgreSQL/SQLAlchemy repository for practical hunt runs.

The caller owns the SQLAlchemy transaction. Private request, result, and
outcome JSON is encrypted with :class:`DataKeyring` before it reaches mapped
columns; generic queue payloads contain only the opaque ``hunt_run_id``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .hunt_payloads import (
    decrypt_hunt_outcome,
    decrypt_hunt_request,
    decrypt_hunt_result,
    encrypt_hunt_outcome,
    encrypt_hunt_request,
    encrypt_hunt_result,
)
from .job_queue import (
    cancel_job,
    complete_job,
    enqueue_job,
    lock_owned_running_job,
    requeue_dead_letter_job,
    utcnow,
)
from .models import (
    BackgroundJob,
    BackgroundJobEvent,
    HuntOutcome,
    HuntRun,
    Owner,
    WorkerHeartbeat,
)
from .schemas import HuntResult, OutcomeLog
from .security import DataKeyring


HUNT_JOB_KIND = "legacy_hunt"
_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


class HuntRepositoryError(RuntimeError):
    """Base error for an invalid durable-hunt repository operation."""


class IdempotencyConflict(HuntRepositoryError):
    """An owner reused an idempotency key for a different request body."""


class HuntStateConflict(HuntRepositoryError):
    """The requested hunt transition is not valid in its current state."""


@dataclass(frozen=True)
class HuntState:
    """Public run metadata; intentionally excludes ciphertext and lease secrets."""

    run_id: str
    owner_id: str
    background_job_id: str
    status: str
    attempt_count: int
    max_attempts: int
    stage: str
    stage_checkpoint: str | None
    last_error: str | None
    request_available: bool
    result_available: bool
    run_after: datetime
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancel_requested_at: datetime | None
    cancelled_at: datetime | None
    failed_at: datetime | None
    dead_lettered_at: datetime | None
    request_expires_at: datetime
    access_expires_at: datetime


@dataclass(frozen=True)
class CreateHuntResult:
    state: HuntState
    created: bool

    @property
    def reused(self) -> bool:
        return not self.created


@dataclass(frozen=True)
class PurgeHuntsResult:
    requests_cleared: int
    runs_deleted: int


def create_or_reuse_hunt(
    session: Session,
    *,
    owner_id: str,
    request_json: str,
    request_hash: str,
    access_hash: str,
    keyring: DataKeyring,
    request_expires_at: datetime,
    access_expires_at: datetime,
    idempotency_key_hash: str | None = None,
    max_attempts: int = 3,
    run_id: str | None = None,
    actor: str = "api",
    now: datetime | None = None,
) -> CreateHuntResult:
    """Atomically enqueue a hunt or reuse the owner's identical request.

    An exact idempotent reuse rotates the capability hash and its expiry. A
    different request with the same owner/key raises :class:`IdempotencyConflict`.
    """

    current = now or utcnow()
    normalized_owner = owner_id.strip()
    if not normalized_owner or len(normalized_owner) > 64:
        raise ValueError("owner_id must be 1-64 characters")
    if session.get(Owner, normalized_owner) is None:
        raise ValueError("owner_id does not exist")
    if not isinstance(request_json, str) or not request_json:
        raise ValueError("request_json must be non-empty")
    try:
        parsed_request = json.loads(request_json)
    except (TypeError, ValueError) as exc:
        raise ValueError("request_json must contain valid JSON") from exc
    if not isinstance(parsed_request, dict):
        raise ValueError("request_json must contain a JSON object")

    normalized_request_hash = _normalize_hash(request_hash, "request_hash")
    calculated_hash = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(normalized_request_hash, calculated_hash):
        raise ValueError("request_hash does not match request_json")
    normalized_access_hash = _normalize_hash(access_hash, "access_hash")
    normalized_idempotency = (
        _normalize_hash(idempotency_key_hash, "idempotency_key_hash")
        if idempotency_key_hash is not None
        else None
    )
    _validate_future_expiry(request_expires_at, current, "request_expires_at")
    _validate_future_expiry(access_expires_at, current, "access_expires_at")
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")

    if normalized_idempotency is not None:
        existing = session.scalar(
            select(HuntRun)
            .where(
                HuntRun.owner_id == normalized_owner,
                HuntRun.idempotency_key_hash == normalized_idempotency,
            )
            .with_for_update()
        )
        if existing is not None:
            return _reuse_hunt(
                session,
                existing,
                request_hash=normalized_request_hash,
                access_hash=normalized_access_hash,
                now=current,
            )

    new_run_id = run_id or uuid4().hex
    if _RUN_ID_RE.fullmatch(new_run_id) is None:
        raise ValueError("run_id must be an opaque 1-32 character id")
    dedupe_key = (
        f"hunt-idempotency:{normalized_idempotency}"
        if normalized_idempotency is not None
        else f"hunt-run:{new_run_id}"
    )
    enqueue_result = enqueue_job(
        session,
        kind=HUNT_JOB_KIND,
        dedupe_key=dedupe_key,
        owner_id=normalized_owner,
        subject_type="hunt_run",
        subject_id=new_run_id,
        payload={"hunt_run_id": new_run_id},
        max_attempts=max_attempts,
        actor=actor,
    )
    if not enqueue_result.created:
        existing = session.scalar(
            select(HuntRun)
            .where(HuntRun.background_job_id == enqueue_result.job.id)
            .with_for_update()
        )
        if existing is None:
            raise HuntRepositoryError("deduplicated hunt job has no hunt run")
        if normalized_idempotency is None:
            raise HuntStateConflict("run_id is already in use")
        return _reuse_hunt(
            session,
            existing,
            request_hash=normalized_request_hash,
            access_hash=normalized_access_hash,
            now=current,
        )

    envelope = encrypt_hunt_request(
        keyring,
        owner_id=normalized_owner,
        hunt_run_id=new_run_id,
        request_json=request_json,
        request_hash=normalized_request_hash,
    )
    hunt = HuntRun(
        id=new_run_id,
        owner_id=normalized_owner,
        background_job_id=enqueue_result.job.id,
        access_hash=normalized_access_hash,
        idempotency_key_hash=normalized_idempotency,
        request_hash=normalized_request_hash,
        encrypted_request=envelope.ciphertext,
        request_key_id=envelope.key_id,
        request_expires_at=request_expires_at,
        access_expires_at=access_expires_at,
        created_at=current,
        updated_at=current,
    )
    session.add(hunt)
    session.flush()
    return CreateHuntResult(
        state=_state_from_models(hunt, enqueue_result.job),
        created=True,
    )


def authorize_hunt(
    session: Session,
    *,
    owner_id: str,
    hunt_run_id: str,
    access_hash: str,
    now: datetime | None = None,
) -> bool:
    """Check an already-hashed capability inside the authoritative owner scope."""

    try:
        candidate = _normalize_hash(access_hash, "access_hash")
    except ValueError:
        return False
    hunt = _get_owner_hunt(session, owner_id=owner_id, hunt_run_id=hunt_run_id)
    if hunt is None or _as_utc(hunt.access_expires_at) <= _as_utc(now or utcnow()):
        return False
    return hmac.compare_digest(hunt.access_hash, candidate)


def load_hunt_state(
    session: Session,
    *,
    owner_id: str,
    hunt_run_id: str,
) -> HuntState | None:
    hunt = _get_owner_hunt(session, owner_id=owner_id, hunt_run_id=hunt_run_id)
    if hunt is None:
        return None
    job = session.get(BackgroundJob, hunt.background_job_id)
    return _state_from_models(hunt, job) if job is not None else None


def load_hunt_request_for_worker(
    session: Session,
    *,
    hunt_run_id: str,
    worker_id: str,
    lease_token: str,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> str | None:
    """Decrypt a request only for the worker holding the generic live lease."""

    current = now or utcnow()
    hunt = session.get(HuntRun, hunt_run_id)
    if hunt is None:
        return None
    job = lock_owned_running_job(
        session,
        hunt.background_job_id,
        worker_id=worker_id,
        lease_token=lease_token,
        now=current,
    )
    if job is None:
        return None
    hunt = session.scalar(
        select(HuntRun).where(HuntRun.id == hunt_run_id).with_for_update()
    )
    if (
        hunt is None
        or hunt.encrypted_request is None
        or hunt.request_key_id is None
        or _as_utc(hunt.request_expires_at) <= _as_utc(current)
        or _as_utc(hunt.access_expires_at) <= _as_utc(current)
        or not _job_matches_hunt(job, hunt)
    ):
        return None
    return decrypt_hunt_request(
        keyring,
        owner_id=hunt.owner_id,
        hunt_run_id=hunt.id,
        request_hash=hunt.request_hash,
        encryption_key_id=hunt.request_key_id,
        ciphertext=hunt.encrypted_request,
    )


def load_hunt_result(
    session: Session,
    *,
    owner_id: str,
    hunt_run_id: str,
    keyring: DataKeyring,
) -> HuntResult | None:
    hunt = _get_owner_hunt(session, owner_id=owner_id, hunt_run_id=hunt_run_id)
    if hunt is None or hunt.encrypted_result is None or hunt.result_key_id is None:
        return None
    payload = decrypt_hunt_result(
        keyring,
        owner_id=hunt.owner_id,
        hunt_run_id=hunt.id,
        encryption_key_id=hunt.result_key_id,
        ciphertext=hunt.encrypted_result,
    )
    return HuntResult.model_validate(payload)


def load_hunt_outcomes(
    session: Session,
    *,
    owner_id: str,
    hunt_run_id: str,
    keyring: DataKeyring,
) -> list[OutcomeLog]:
    hunt = _get_owner_hunt(session, owner_id=owner_id, hunt_run_id=hunt_run_id)
    if hunt is None:
        return []
    rows = list(
        session.scalars(
            select(HuntOutcome)
            .where(HuntOutcome.hunt_run_id == hunt.id)
            .order_by(HuntOutcome.logged_at.desc(), HuntOutcome.id.desc())
        )
    )
    return [
        OutcomeLog.model_validate(
            decrypt_hunt_outcome(
                keyring,
                owner_id=hunt.owner_id,
                outcome_id=str(row.id),
                draft_id=row.draft_id,
                encryption_key_id=row.encryption_key_id,
                ciphertext=row.encrypted_payload,
            )
        )
        for row in rows
    ]


def rotate_hunt_access(
    session: Session,
    *,
    owner_id: str,
    hunt_run_id: str,
    access_hash: str,
    access_expires_at: datetime | None = None,
    now: datetime | None = None,
) -> HuntState | None:
    current = now or utcnow()
    normalized_hash = _normalize_hash(access_hash, "access_hash")
    if access_expires_at is not None:
        _validate_future_expiry(access_expires_at, current, "access_expires_at")
    hunt = _lock_owner_hunt(session, owner_id=owner_id, hunt_run_id=hunt_run_id)
    if hunt is None:
        return None
    hunt.access_hash = normalized_hash
    # Rotation never extends the original retention deadline. It may shorten it.
    if (
        access_expires_at is not None
        and _as_utc(access_expires_at) < _as_utc(hunt.access_expires_at)
    ):
        hunt.access_expires_at = access_expires_at
    hunt.updated_at = current
    session.flush()
    job = session.get(BackgroundJob, hunt.background_job_id)
    return _state_from_models(hunt, job) if job is not None else None


def cancel_hunt(
    session: Session,
    *,
    owner_id: str,
    hunt_run_id: str,
    actor: str = "user",
    now: datetime | None = None,
) -> HuntState | None:
    current = now or utcnow()
    initial = _get_owner_hunt(session, owner_id=owner_id, hunt_run_id=hunt_run_id)
    if initial is None:
        return None
    job = cancel_job(
        session,
        initial.background_job_id,
        actor=actor,
        reason="hunt_cancelled",
        now=current,
    )
    hunt = _lock_owner_hunt(session, owner_id=owner_id, hunt_run_id=hunt_run_id)
    if hunt is None or job is None:
        return None
    _clear_request_envelope(hunt, current)
    session.flush()
    return _state_from_models(hunt, job)


def clear_hunt_request(
    session: Session,
    *,
    owner_id: str,
    hunt_run_id: str,
    now: datetime | None = None,
) -> bool:
    hunt = _lock_owner_hunt(session, owner_id=owner_id, hunt_run_id=hunt_run_id)
    if hunt is None:
        return False
    job = session.get(BackgroundJob, hunt.background_job_id)
    if job is None:
        raise HuntRepositoryError("hunt run has no background job")
    if not _job_matches_hunt(job, hunt):
        raise HuntRepositoryError("hunt run and background job linkage do not match")
    changed = _clear_request_envelope(hunt, now or utcnow())
    session.flush()
    return changed


def clear_hunt_request_for_worker(
    session: Session,
    *,
    hunt_run_id: str,
    worker_id: str,
    lease_token: str,
    now: datetime | None = None,
) -> bool:
    current = now or utcnow()
    initial = session.get(HuntRun, hunt_run_id)
    if initial is None:
        return False
    job = lock_owned_running_job(
        session,
        initial.background_job_id,
        worker_id=worker_id,
        lease_token=lease_token,
        now=current,
    )
    if job is None:
        return False
    hunt = session.scalar(
        select(HuntRun).where(HuntRun.id == hunt_run_id).with_for_update()
    )
    if hunt is None:
        return False
    if not _job_matches_hunt(job, hunt):
        raise HuntRepositoryError("hunt run and background job linkage do not match")
    changed = _clear_request_envelope(hunt, current)
    session.flush()
    return changed


def store_hunt_success(
    session: Session,
    *,
    hunt_result: HuntResult,
    worker_id: str,
    lease_token: str,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> HuntState | None:
    """Atomically encrypt a result, clear the request, and complete its job.

    ``None`` means the run does not exist or the supplied generic lease is no
    longer live. A concurrent cancellation wins and returns ``cancelled`` state
    without storing a result.
    """

    current = now or utcnow()
    initial = session.get(HuntRun, hunt_result.run_id)
    if initial is None:
        return None
    job = lock_owned_running_job(
        session,
        initial.background_job_id,
        worker_id=worker_id,
        lease_token=lease_token,
        now=current,
    )
    if job is None:
        return None
    hunt = session.scalar(
        select(HuntRun).where(HuntRun.id == hunt_result.run_id).with_for_update()
    )
    if hunt is None:
        return None
    if not _job_matches_hunt(job, hunt):
        raise HuntRepositoryError("hunt run and background job linkage do not match")

    if _as_utc(hunt.access_expires_at) <= _as_utc(current):
        cancel_job(
            session,
            job.id,
            actor="retention",
            reason="access_expired",
            now=current,
        )
        completed = complete_job(
            session,
            job.id,
            worker_id=worker_id,
            lease_token=lease_token,
            now=current,
        )
        _clear_request_envelope(hunt, current)
        session.flush()
        return _state_from_models(hunt, completed) if completed is not None else None

    if job.cancel_requested_at is not None:
        completed = complete_job(
            session,
            job.id,
            worker_id=worker_id,
            lease_token=lease_token,
            now=current,
        )
        _clear_request_envelope(hunt, current)
        session.flush()
        return _state_from_models(hunt, completed) if completed is not None else None

    if hunt.encrypted_result is not None:
        raise HuntStateConflict("hunt result is already stored")
    envelope = encrypt_hunt_result(
        keyring,
        owner_id=hunt.owner_id,
        hunt_run_id=hunt.id,
        payload=hunt_result.model_dump(mode="json"),
    )
    completed = complete_job(
        session,
        job.id,
        worker_id=worker_id,
        lease_token=lease_token,
        now=current,
    )
    if completed is None or completed.status != "succeeded":
        return None
    hunt.encrypted_result = envelope.ciphertext
    hunt.result_key_id = envelope.key_id
    hunt.completed_at = current
    _clear_request_envelope(hunt, current)
    hunt.updated_at = current
    session.flush()
    return _state_from_models(hunt, completed)


def complete_existing_hunt_result_for_worker(
    session: Session,
    *,
    hunt_run_id: str,
    worker_id: str,
    lease_token: str,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> HuntState | None:
    """Complete imported/interrupted state that already has encrypted output.

    Normal writes use :func:`store_hunt_success` atomically. This recovery path
    still requires the worker's generic live lease and never decrypts output.
    """

    current = now or utcnow()
    initial = session.get(HuntRun, hunt_run_id)
    if initial is None:
        return None
    job = lock_owned_running_job(
        session,
        initial.background_job_id,
        worker_id=worker_id,
        lease_token=lease_token,
        now=current,
    )
    if job is None:
        return None
    hunt = session.scalar(
        select(HuntRun).where(HuntRun.id == hunt_run_id).with_for_update()
    )
    if (
        hunt is None
        or hunt.encrypted_result is None
        or hunt.result_key_id is None
    ):
        return None
    if not _job_matches_hunt(job, hunt):
        raise HuntRepositoryError("hunt run and background job linkage do not match")
    _decrypt_result(hunt, keyring)
    if _as_utc(hunt.access_expires_at) <= _as_utc(current):
        cancel_job(
            session,
            job.id,
            actor="retention",
            reason="access_expired",
            now=current,
        )
        completed = complete_job(
            session,
            job.id,
            worker_id=worker_id,
            lease_token=lease_token,
            now=current,
        )
        hunt.encrypted_result = None
        hunt.result_key_id = None
        hunt.completed_at = None
        _clear_request_envelope(hunt, current)
        session.flush()
        return _state_from_models(hunt, completed) if completed is not None else None
    completed = complete_job(
        session,
        job.id,
        worker_id=worker_id,
        lease_token=lease_token,
        now=current,
    )
    if completed is None:
        return None
    if completed.status == "cancelled":
        hunt.encrypted_result = None
        hunt.result_key_id = None
        hunt.completed_at = None
    else:
        hunt.completed_at = hunt.completed_at or current
    _clear_request_envelope(hunt, current)
    hunt.updated_at = current
    session.flush()
    return _state_from_models(hunt, completed)


def append_hunt_outcomes(
    session: Session,
    *,
    owner_id: str,
    hunt_run_id: str,
    outcomes: Iterable[OutcomeLog],
    keyring: DataKeyring,
    now: datetime | None = None,
) -> list[OutcomeLog]:
    entries = list(outcomes)
    if not entries:
        return []
    hunt = _lock_owner_hunt(session, owner_id=owner_id, hunt_run_id=hunt_run_id)
    if hunt is None:
        raise HuntStateConflict("hunt run does not exist")
    job = session.get(BackgroundJob, hunt.background_job_id)
    if job is None or job.status != "succeeded":
        raise HuntStateConflict("outcomes can only be appended after hunt success")
    result = _decrypt_result(hunt, keyring)
    if result is None:
        raise HuntStateConflict("succeeded hunt has no stored result")
    known_draft_ids = {draft.draft_id for draft in result.outreach}
    unknown = {entry.draft_id for entry in entries if entry.draft_id not in known_draft_ids}
    if unknown:
        raise HuntStateConflict("outcomes reference unknown draft ids")

    logged_at = now or utcnow()
    stamped: list[OutcomeLog] = []
    for entry in entries:
        if not entry.draft_id or len(entry.draft_id) > 128:
            raise ValueError("outcome draft_id must be 1-128 characters")
        stamped_entry = entry.model_copy(update={"logged_at": logged_at})
        payload = stamped_entry.model_dump(mode="json")
        placeholder = encrypt_hunt_outcome(
            keyring,
            owner_id=hunt.owner_id,
            outcome_id=f"pending:{uuid4().hex}",
            draft_id=stamped_entry.draft_id,
            payload=payload,
        )
        row = HuntOutcome(
            hunt_run_id=hunt.id,
            draft_id=stamped_entry.draft_id,
            encrypted_payload=placeholder.ciphertext,
            encryption_key_id=placeholder.key_id,
            logged_at=logged_at,
        )
        session.add(row)
        session.flush()
        envelope = encrypt_hunt_outcome(
            keyring,
            owner_id=hunt.owner_id,
            outcome_id=str(row.id),
            draft_id=row.draft_id,
            payload=payload,
        )
        row.encrypted_payload = envelope.ciphertext
        row.encryption_key_id = envelope.key_id
        stamped.append(stamped_entry)
    session.flush()
    return stamped


def delete_hunt(
    session: Session,
    *,
    owner_id: str,
    hunt_run_id: str,
) -> bool:
    hunt = _get_owner_hunt(session, owner_id=owner_id, hunt_run_id=hunt_run_id)
    if hunt is None:
        return False
    return _delete_hunt_rows(session, hunt.id, hunt.background_job_id)


def requeue_hunt_dead_letter(
    session: Session,
    *,
    owner_id: str,
    hunt_run_id: str,
    actor: str,
    reason: str | None = None,
    now: datetime | None = None,
) -> HuntState | None:
    current = now or utcnow()
    initial = _get_owner_hunt(session, owner_id=owner_id, hunt_run_id=hunt_run_id)
    if initial is None:
        return None
    job = session.scalar(
        select(BackgroundJob)
        .where(BackgroundJob.id == initial.background_job_id)
        .with_for_update()
    )
    hunt = _lock_owner_hunt(session, owner_id=owner_id, hunt_run_id=hunt_run_id)
    if hunt is None or job is None:
        return None
    if job.status != "dead_letter":
        return _state_from_models(hunt, job)
    if (
        hunt.encrypted_request is None
        or hunt.request_key_id is None
        or _as_utc(hunt.request_expires_at) <= _as_utc(current)
    ):
        _clear_request_envelope(hunt, current)
        session.flush()
        return _state_from_models(hunt, job)
    requeued = requeue_dead_letter_job(
        session,
        job.id,
        actor=actor,
        reason=reason,
        now=current,
    )
    hunt.updated_at = current
    session.flush()
    return _state_from_models(hunt, requeued) if requeued is not None else None


def purge_expired_hunts(
    session: Session,
    *,
    now: datetime | None = None,
) -> PurgeHuntsResult:
    """Clear expired requests and explicitly delete access-expired run graphs."""

    current = now or utcnow()
    expired_access = list(
        session.execute(
            select(HuntRun.id, HuntRun.background_job_id).where(
                HuntRun.access_expires_at <= current
            )
        )
    )
    deleted = 0
    for hunt_run_id, job_id in expired_access:
        if _delete_hunt_rows(session, str(hunt_run_id), str(job_id)):
            deleted += 1

    expired_requests = list(
        session.scalars(
            select(HuntRun.id).where(
                HuntRun.encrypted_request.is_not(None),
                HuntRun.request_expires_at <= current,
                HuntRun.access_expires_at > current,
            )
        )
    )
    cleared = 0
    for hunt_run_id in expired_requests:
        initial = session.get(HuntRun, hunt_run_id)
        if initial is None:
            continue
        cancel_job(
            session,
            initial.background_job_id,
            actor="retention",
            reason="request_expired",
            now=current,
        )
        hunt = session.scalar(
            select(HuntRun).where(HuntRun.id == hunt_run_id).with_for_update()
        )
        if (
            hunt is not None
            and hunt.encrypted_request is not None
            and _as_utc(hunt.request_expires_at) <= _as_utc(current)
            and _clear_request_envelope(hunt, current)
        ):
            cleared += 1
    session.flush()
    return PurgeHuntsResult(requests_cleared=cleared, runs_deleted=deleted)


def _reuse_hunt(
    session: Session,
    hunt: HuntRun,
    *,
    request_hash: str,
    access_hash: str,
    now: datetime,
) -> CreateHuntResult:
    if _as_utc(hunt.access_expires_at) <= _as_utc(now):
        raise HuntStateConflict("idempotent hunt retention has expired")
    if not hmac.compare_digest(hunt.request_hash, request_hash):
        raise IdempotencyConflict(
            "idempotency key was already used for a different request"
        )
    hunt.access_hash = access_hash
    hunt.updated_at = now
    session.flush()
    job = session.get(BackgroundJob, hunt.background_job_id)
    if job is None:
        raise HuntRepositoryError("hunt run has no background job")
    return CreateHuntResult(state=_state_from_models(hunt, job), created=False)


def _get_owner_hunt(
    session: Session,
    *,
    owner_id: str,
    hunt_run_id: str,
) -> HuntRun | None:
    return session.scalar(
        select(HuntRun).where(
            HuntRun.id == hunt_run_id,
            HuntRun.owner_id == owner_id,
            HuntRun.access_expires_at > utcnow(),
        )
    )


def _lock_owner_hunt(
    session: Session,
    *,
    owner_id: str,
    hunt_run_id: str,
) -> HuntRun | None:
    return session.scalar(
        select(HuntRun)
        .where(
            HuntRun.id == hunt_run_id,
            HuntRun.owner_id == owner_id,
            HuntRun.access_expires_at > utcnow(),
        )
        .with_for_update()
    )


def _state_from_models(hunt: HuntRun, job: BackgroundJob) -> HuntState:
    if not _job_matches_hunt(job, hunt):
        raise HuntRepositoryError("hunt run and background job linkage do not match")
    return HuntState(
        run_id=hunt.id,
        owner_id=hunt.owner_id,
        background_job_id=job.id,
        status=job.status,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        stage=job.stage,
        stage_checkpoint=job.stage_checkpoint,
        last_error=job.last_error,
        request_available=hunt.encrypted_request is not None,
        result_available=hunt.encrypted_result is not None,
        run_after=_as_utc(job.run_after),
        created_at=_as_utc(hunt.created_at),
        updated_at=max(_as_utc(hunt.updated_at), _as_utc(job.updated_at)),
        started_at=_optional_utc(job.started_at),
        completed_at=_optional_utc(hunt.completed_at or job.completed_at),
        cancel_requested_at=_optional_utc(job.cancel_requested_at),
        cancelled_at=_optional_utc(job.cancelled_at),
        failed_at=_optional_utc(job.failed_at),
        dead_lettered_at=_optional_utc(job.dead_lettered_at),
        request_expires_at=_as_utc(hunt.request_expires_at),
        access_expires_at=_as_utc(hunt.access_expires_at),
    )


def _decrypt_result(hunt: HuntRun, keyring: DataKeyring) -> HuntResult | None:
    if hunt.encrypted_result is None or hunt.result_key_id is None:
        return None
    payload = decrypt_hunt_result(
        keyring,
        owner_id=hunt.owner_id,
        hunt_run_id=hunt.id,
        encryption_key_id=hunt.result_key_id,
        ciphertext=hunt.encrypted_result,
    )
    return HuntResult.model_validate(payload)


def _job_matches_hunt(job: BackgroundJob, hunt: HuntRun) -> bool:
    return (
        job.kind == HUNT_JOB_KIND
        and job.owner_id == hunt.owner_id
        and job.subject_type == "hunt_run"
        and job.subject_id == hunt.id
        and job.payload == {"hunt_run_id": hunt.id}
    )


def _clear_request_envelope(hunt: HuntRun, now: datetime) -> bool:
    if hunt.encrypted_request is None and hunt.request_key_id is None:
        return False
    hunt.encrypted_request = None
    hunt.request_key_id = None
    hunt.request_cleared_at = now
    hunt.updated_at = now
    return True


def _delete_hunt_rows(session: Session, hunt_run_id: str, job_id: str) -> bool:
    job = session.scalar(
        select(BackgroundJob).where(BackgroundJob.id == job_id).with_for_update()
    )
    hunt = session.scalar(
        select(HuntRun).where(HuntRun.id == hunt_run_id).with_for_update()
    )
    if hunt is None:
        return False
    session.execute(
        delete(HuntOutcome).where(HuntOutcome.hunt_run_id == hunt_run_id)
    )
    session.execute(delete(HuntRun).where(HuntRun.id == hunt_run_id))
    session.flush()
    session.execute(
        update(WorkerHeartbeat)
        .where(WorkerHeartbeat.current_job_id == job_id)
        .values(current_job_id=None)
    )
    session.execute(
        delete(BackgroundJobEvent).where(BackgroundJobEvent.job_id == job_id)
    )
    if job is not None:
        session.execute(delete(BackgroundJob).where(BackgroundJob.id == job_id))
    session.flush()
    return True


def _normalize_hash(value: str, field: str) -> str:
    normalized = value.strip().lower()
    if _HASH_RE.fullmatch(normalized) is None:
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    return normalized


def _validate_future_expiry(value: datetime, now: datetime, field: str) -> None:
    if _as_utc(value) <= _as_utc(now):
        raise ValueError(f"{field} must be in the future")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_utc(value: datetime | None) -> datetime | None:
    return _as_utc(value) if value is not None else None


__all__ = [
    "CreateHuntResult",
    "HUNT_JOB_KIND",
    "HuntRepositoryError",
    "HuntState",
    "HuntStateConflict",
    "IdempotencyConflict",
    "PurgeHuntsResult",
    "append_hunt_outcomes",
    "authorize_hunt",
    "cancel_hunt",
    "clear_hunt_request",
    "clear_hunt_request_for_worker",
    "complete_existing_hunt_result_for_worker",
    "create_or_reuse_hunt",
    "delete_hunt",
    "load_hunt_outcomes",
    "load_hunt_request_for_worker",
    "load_hunt_result",
    "load_hunt_state",
    "purge_expired_hunts",
    "requeue_hunt_dead_letter",
    "rotate_hunt_access",
    "store_hunt_success",
]
