"""Explicit SQLAlchemy database lifecycle for the durable product path.

Practical mode uses this database for owner sessions, encrypted hunt data,
outcomes, and background jobs. Importing this module never creates tables or
runs migrations, which keeps deploys and tests predictable. ``persistence.py``
remains only for the explicitly disabled practical-mode compatibility path.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker


DATABASE_URL_ENV = "DATABASE_URL"
MIGRATION_HEAD = "20260715_0014"


class DatabaseConfigError(RuntimeError):
    """Raised when the durable database configuration is unsafe or missing."""


def normalize_database_url(value: str) -> str:
    """Normalize hosted Postgres aliases onto the psycopg 3 SQLAlchemy dialect."""

    normalized = value.strip()
    if normalized.startswith("postgres://"):
        return "postgresql+psycopg://" + normalized.removeprefix("postgres://")
    if normalized.startswith("postgresql://"):
        return "postgresql+psycopg://" + normalized.removeprefix("postgresql://")
    return normalized


def resolve_database_url(
    value: str | None = None,
    *,
    required: bool = True,
    production: bool | None = None,
) -> str | None:
    """Resolve and validate the durable database URL without opening a socket."""

    raw = value if value is not None else os.getenv(DATABASE_URL_ENV, "")
    if not raw.strip():
        if required:
            raise DatabaseConfigError(f"{DATABASE_URL_ENV} is required")
        return None

    normalized = normalize_database_url(raw)
    try:
        url = make_url(normalized)
    except Exception as exc:  # noqa: BLE001 - normalize config errors.
        raise DatabaseConfigError(f"{DATABASE_URL_ENV} is invalid") from exc

    is_production = (
        os.getenv("ENVIRONMENT", "").strip().lower() == "production"
        if production is None
        else production
    )
    if is_production and url.get_backend_name() != "postgresql":
        raise DatabaseConfigError("production DATABASE_URL must use PostgreSQL")
    return normalized


@dataclass
class Database:
    """Owned engine and session factory; construct once per process."""

    url: str

    def __post_init__(self) -> None:
        connect_args: dict[str, object] = {}
        backend_name = make_url(self.url).get_backend_name()
        if backend_name == "sqlite":
            connect_args["check_same_thread"] = False
        elif backend_name == "postgresql":
            # Readiness must fail quickly instead of tying up the API while a
            # managed database is unavailable.
            connect_args["connect_timeout"] = 5
        self.engine: Engine = create_engine(
            self.url,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        if backend_name == "sqlite":
            event.listen(self.engine, "connect", _enable_sqlite_foreign_keys)
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
        )

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Commit successful work and roll back any failed unit of work."""

        db_session = self.session_factory()
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise
        finally:
            db_session.close()

    def reachable(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except SQLAlchemyError:
            return False

    def current_migration_revision(self) -> str | None:
        try:
            with self.engine.connect() as connection:
                value = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
            return str(value) if value is not None else None
        except SQLAlchemyError:
            return None

    def migrations_current(self) -> bool:
        return self.current_migration_revision() == MIGRATION_HEAD

    def dispose(self) -> None:
        self.engine.dispose()


def database_from_env(*, required: bool = True) -> Database | None:
    url = resolve_database_url(required=required)
    return Database(url) if url is not None else None


def _enable_sqlite_foreign_keys(dbapi_connection: object, _record: object) -> None:
    """Make SQLite tests enforce the same owner/cascade FKs as PostgreSQL."""

    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()
