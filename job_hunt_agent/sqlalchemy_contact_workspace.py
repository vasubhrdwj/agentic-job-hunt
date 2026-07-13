"""Transaction-owning adapter for persisted contact-bench reads."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from .contact_repository import ContactRepositoryError, load_application_contact_bench
from .contact_schemas import ApplicationContactBenchResponse
from .database import Database
from .owner_workspace import WorkspaceUnavailable


class SqlAlchemyContactWorkspaceStore:
    """Serve contact projections without invoking discovery or outreach."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def get_application_contacts(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationContactBenchResponse | None:
        with _contact_errors(), self.database.session() as session:
            return load_application_contact_bench(
                session,
                owner_id=owner_id,
                application_id=application_id,
            )


@contextmanager
def _contact_errors() -> Iterator[None]:
    try:
        yield
    except WorkspaceUnavailable:
        raise
    except ContactRepositoryError as exc:
        raise WorkspaceUnavailable("contact data is inconsistent") from exc
    except ValidationError as exc:
        raise WorkspaceUnavailable(
            "stored contact data failed contract validation"
        ) from exc
    except SQLAlchemyError as exc:
        raise WorkspaceUnavailable(
            "contact workspace database is unavailable"
        ) from exc


__all__ = ["SqlAlchemyContactWorkspaceStore"]
