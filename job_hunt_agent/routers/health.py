"""Liveness-adjacent readiness and owner-visible operational health."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyCookie
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import session_cookie_name
from ..contact_search_repository import CONTACT_SEARCH_JOB_KIND
from ..database import MIGRATION_HEAD, Database
from ..models import BackgroundJob
from ..worker_health import (
    DEFAULT_WORKER_HEARTBEAT_MAX_AGE_SECONDS,
    ROLE_SCAN_JOB_KIND,
    as_utc,
    heartbeat_age_seconds,
    load_worker_capability,
    load_worker_heartbeat_snapshot,
)
from .session import require_owner_session


ACTIVE_JOB_STATUSES = ("queued", "running")


def create_health_router(database: Database | None) -> APIRouter:
    router = APIRouter(tags=["operational-health"])
    owner_cookie = APIKeyCookie(
        name=session_cookie_name(),
        scheme_name="OwnerSessionCookie",
        description="Opaque HttpOnly session issued by POST /api/session.",
        auto_error=False,
    )

    @router.get("/ready", include_in_schema=False)
    def readiness() -> JSONResponse:
        snapshot = readiness_snapshot(database)
        return JSONResponse(
            status_code=200 if snapshot["ok"] else 503,
            content=snapshot,
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @router.get("/web-ready", include_in_schema=False)
    def web_readiness() -> JSONResponse:
        snapshot = web_readiness_snapshot(database)
        return JSONResponse(
            status_code=200 if snapshot["ok"] else 503,
            content=snapshot,
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @router.get("/api/health")
    def owner_health(
        request: Request,
        _session_cookie: str | None = Security(owner_cookie),
    ) -> dict[str, object]:
        owner = require_owner_session(database, request)
        assert database is not None
        snapshot = readiness_snapshot(database)
        with database.session() as session:
            counts = _owner_queue_counts(session, owner_id=owner.owner_id)
            role_scan = load_worker_capability(
                session,
                kind=ROLE_SCAN_JOB_KIND,
            )
            contact_search = load_worker_capability(
                session,
                kind=CONTACT_SEARCH_JOB_KIND,
            )
        return {
            **snapshot,
            "owner_id": owner.owner_id,
            "capabilities": {
                "role_scan": role_scan.public_payload(),
                "contact_search": contact_search.public_payload(),
            },
            "queue": {
                "counts": counts,
                "dead_letter": counts.get("dead_letter", 0),
            },
        }

    return router


def readiness_snapshot(
    database: Database | None,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    current = as_utc(now or datetime.now(timezone.utc))
    database_snapshot = _database_snapshot(database)
    reachable = bool(database_snapshot["database"]["reachable"])
    migrations_current = bool(database_snapshot["migrations"]["current"])
    worker_payload: dict[str, object] = {
        "fresh": False,
        "worker_id": None,
        "worker_ids": [],
        "fresh_worker_count": 0,
        "last_seen_at": None,
        "age_seconds": None,
        "supported_kinds": [],
        "active_job_kinds": [],
        "unsupported_active_kinds": [],
    }

    if migrations_current and database is not None:
        with database.session() as session:
            heartbeat_snapshot = load_worker_heartbeat_snapshot(
                session,
                now=current,
            )
            active_job_kinds = sorted(
                set(
                    session.scalars(
                        select(BackgroundJob.kind).where(
                            BackgroundJob.status.in_(ACTIVE_JOB_STATUSES)
                        )
                    )
                )
            )

        heartbeats = heartbeat_snapshot.heartbeats
        fresh_heartbeats = heartbeat_snapshot.fresh_heartbeats
        supported_kinds = sorted(
            {
                kind
                for heartbeat in fresh_heartbeats
                for kind in heartbeat.supported_kinds
                if isinstance(kind, str)
            }
        )
        unsupported_active_kinds = sorted(set(active_job_kinds) - set(supported_kinds))

        if heartbeats:
            latest_heartbeat = heartbeats[0]
            last_seen = as_utc(latest_heartbeat.last_seen_at)
            age_seconds = heartbeat_age_seconds(latest_heartbeat, now=current)
            worker_payload = {
                "fresh": bool(fresh_heartbeats),
                "worker_id": latest_heartbeat.worker_id,
                "worker_ids": [heartbeat.worker_id for heartbeat in fresh_heartbeats],
                "fresh_worker_count": len(fresh_heartbeats),
                "last_seen_at": last_seen.isoformat(),
                "age_seconds": round(age_seconds, 3),
                "supported_kinds": supported_kinds,
                "active_job_kinds": active_job_kinds,
                "unsupported_active_kinds": unsupported_active_kinds,
            }
        else:
            worker_payload["active_job_kinds"] = active_job_kinds
            worker_payload["unsupported_active_kinds"] = unsupported_active_kinds

    ok = bool(
        reachable
        and migrations_current
        and worker_payload["fresh"]
        and not worker_payload["unsupported_active_kinds"]
    )
    return {
        "ok": ok,
        **database_snapshot,
        "worker": worker_payload,
    }


def web_readiness_snapshot(database: Database | None) -> dict[str, object]:
    """Report whether the API can safely serve private workspace requests."""

    snapshot = _database_snapshot(database)
    return {
        "ok": bool(
            snapshot["database"]["reachable"]
            and snapshot["migrations"]["current"]
        ),
        **snapshot,
    }


def _database_snapshot(database: Database | None) -> dict[str, object]:
    configured = database is not None
    reachable = configured and database.reachable()
    revision = database.current_migration_revision() if reachable and database else None
    return {
        "database": {"configured": configured, "reachable": bool(reachable)},
        "migrations": {
            "current": revision == MIGRATION_HEAD,
            "revision": revision,
            "expected_revision": MIGRATION_HEAD,
        },
    }


def _owner_queue_counts(session: Session, *, owner_id: str) -> dict[str, int]:
    rows = session.execute(
        select(BackgroundJob.status, func.count(BackgroundJob.id))
        .where(BackgroundJob.owner_id == owner_id)
        .group_by(BackgroundJob.status)
    )
    return {str(status): int(count) for status, count in rows}
