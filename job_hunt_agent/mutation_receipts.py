"""Payload-free idempotency claims for owner workspace mutations."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .job_queue import utcnow
from .models import OwnerMutationReceipt
from .repository_errors import ProductRepositoryError, ResourceConflict


class MutationIdempotencyConflict(ResourceConflict):
    pass


class MutationPending(ProductRepositoryError):
    pass


@dataclass(frozen=True)
class MutationReplay:
    resource_type: str
    resource_id: str
    result_version: int | None
    deleted: bool


@dataclass(frozen=True)
class MutationClaim:
    receipt_id: str
    replay: MutationReplay | None


def claim_owner_mutation(
    session: Session,
    *,
    owner_id: str,
    namespace: str,
    idempotency_key: str,
    request: BaseModel | dict[str, object],
    now: datetime | None = None,
) -> MutationClaim:
    _ensure_sqlite_outer_write_transaction(session)
    normalized_namespace = namespace.strip()
    normalized_key = idempotency_key.strip()
    if not normalized_namespace or len(normalized_namespace) > 100:
        raise ValueError("mutation namespace must be 1-100 characters")
    if not normalized_key or len(normalized_key) > 200:
        raise ValueError("idempotency key must be 1-200 characters")
    key_hash = hashlib.sha256(normalized_key.encode("utf-8")).hexdigest()
    request_hash = _request_hash(request)
    existing = session.scalar(
        select(OwnerMutationReceipt)
        .where(
            OwnerMutationReceipt.owner_id == owner_id,
            OwnerMutationReceipt.namespace == normalized_namespace,
            OwnerMutationReceipt.idempotency_key_hash == key_hash,
        )
        .with_for_update()
    )
    if existing is not None:
        return _existing_claim(existing, request_hash)

    current = now or utcnow()
    receipt = OwnerMutationReceipt(
        owner_id=owner_id,
        namespace=normalized_namespace,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
        status="pending",
        version=1,
        created_at=current,
        updated_at=current,
    )
    try:
        with session.begin_nested():
            session.add(receipt)
            session.flush()
    except IntegrityError:
        existing = session.scalar(
            select(OwnerMutationReceipt)
            .where(
                OwnerMutationReceipt.owner_id == owner_id,
                OwnerMutationReceipt.namespace == normalized_namespace,
                OwnerMutationReceipt.idempotency_key_hash == key_hash,
            )
            .with_for_update()
        )
        if existing is None:
            raise
        return _existing_claim(existing, request_hash)
    return MutationClaim(receipt_id=receipt.id, replay=None)


def _ensure_sqlite_outer_write_transaction(session: Session) -> None:
    """Keep SAVEPOINT claims inside the caller transaction under SQLite.

    SQLite otherwise defers ``BEGIN`` until the first write and can treat the
    receipt SAVEPOINT as the outer transaction. Releasing that savepoint would
    make a pending receipt survive a later caller rollback. A no-op DML starts
    the real outer transaction without changing durable data.
    """

    if session.get_bind().dialect.name != "sqlite":
        return
    session.execute(
        update(OwnerMutationReceipt)
        .where(OwnerMutationReceipt.id == "")
        .values(version=OwnerMutationReceipt.version)
    )


def load_owner_mutation_replay(
    session: Session,
    *,
    owner_id: str,
    namespace: str,
    idempotency_key: str,
    request: BaseModel | dict[str, object],
) -> MutationReplay | None:
    """Return an existing completed mutation without creating a pending claim."""

    normalized_namespace = namespace.strip()
    normalized_key = idempotency_key.strip()
    if not normalized_namespace or len(normalized_namespace) > 100:
        raise ValueError("mutation namespace must be 1-100 characters")
    if not normalized_key or len(normalized_key) > 200:
        raise ValueError("idempotency key must be 1-200 characters")
    existing = session.scalar(
        select(OwnerMutationReceipt)
        .where(
            OwnerMutationReceipt.owner_id == owner_id,
            OwnerMutationReceipt.namespace == normalized_namespace,
            OwnerMutationReceipt.idempotency_key_hash
            == hashlib.sha256(normalized_key.encode("utf-8")).hexdigest(),
        )
        .with_for_update()
    )
    if existing is None:
        return None
    return _existing_claim(existing, _request_hash(request)).replay


def complete_owner_mutation(
    session: Session,
    *,
    owner_id: str,
    receipt_id: str,
    resource_type: str,
    resource_id: str,
    result_version: int | None,
    deleted: bool = False,
    now: datetime | None = None,
) -> MutationReplay:
    receipt = session.scalar(
        select(OwnerMutationReceipt)
        .where(
            OwnerMutationReceipt.owner_id == owner_id,
            OwnerMutationReceipt.id == receipt_id,
        )
        .with_for_update()
    )
    if receipt is None:
        raise ValueError("mutation receipt does not exist for owner")
    if receipt.status == "completed":
        replay = _replay(receipt)
        if (
            replay.resource_type != resource_type
            or replay.resource_id != resource_id
            or replay.result_version != result_version
            or replay.deleted != deleted
        ):
            raise MutationIdempotencyConflict("completed mutation result does not match")
        return replay
    current = now or utcnow()
    receipt.status = "completed"
    receipt.resource_type = resource_type
    receipt.resource_id = resource_id
    receipt.result_version = result_version
    receipt.deleted = deleted
    receipt.completed_at = current
    receipt.updated_at = current
    receipt.version += 1
    session.flush()
    return _replay(receipt)


def _existing_claim(receipt: OwnerMutationReceipt, request_hash: str) -> MutationClaim:
    if not hmac.compare_digest(receipt.request_hash, request_hash):
        raise MutationIdempotencyConflict(
            "idempotency key was already used for a different mutation request"
        )
    if receipt.status != "completed":
        raise MutationPending("matching mutation is still pending")
    return MutationClaim(receipt_id=receipt.id, replay=_replay(receipt))


def _replay(receipt: OwnerMutationReceipt) -> MutationReplay:
    if receipt.resource_type is None or receipt.resource_id is None:
        raise MutationPending("completed mutation receipt has no resource result")
    return MutationReplay(
        resource_type=receipt.resource_type,
        resource_id=receipt.resource_id,
        result_version=receipt.result_version,
        deleted=receipt.deleted,
    )


def _request_hash(request: BaseModel | dict[str, object]) -> str:
    payload = request.model_dump(mode="json") if isinstance(request, BaseModel) else request
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "MutationClaim",
    "MutationIdempotencyConflict",
    "MutationPending",
    "MutationReplay",
    "claim_owner_mutation",
    "complete_owner_mutation",
    "load_owner_mutation_replay",
]
