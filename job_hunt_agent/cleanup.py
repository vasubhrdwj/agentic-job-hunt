"""Delete expired private requests and run data from the active backend."""

from __future__ import annotations

from . import persistence
from .config import practical_mode_enabled
from .database import database_from_env
from .hunt_repository import purge_expired_hunts
from .privacy_repository import purge_configured_hunts


def main() -> None:
    if practical_mode_enabled():
        database = database_from_env(required=True)
        if database is None:  # pragma: no cover - required=True is fail-closed.
            raise RuntimeError("practical cleanup requires DATABASE_URL")
        if not database.migrations_current():
            raise RuntimeError("practical cleanup requires current database migrations")
        try:
            with database.session() as session:
                result = purge_expired_hunts(session)
                policy_deleted = purge_configured_hunts(session)
            cleared = result.requests_cleared
            deleted = result.runs_deleted + policy_deleted
        finally:
            database.dispose()
    else:
        persistence.init_db()
        cleared, deleted = persistence.purge_expired_data()
    print(f"cleared_request_payloads={cleared} deleted_runs={deleted}")


if __name__ == "__main__":
    main()
