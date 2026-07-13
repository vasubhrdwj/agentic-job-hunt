"""Transaction-owning adapter for the private application workspace."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from .application_repository import (
    ApplicationRepositoryError,
    list_application_activity,
    list_applications,
    load_application_detail,
)
from .application_schemas import (
    ApplicationActivityListResponse,
    ApplicationDetailResponse,
    ApplicationListResponse,
    CursorToken,
)
from .database import Database
from .owner_workspace import WorkspaceUnavailable
from .sqlalchemy_contact_workspace import SqlAlchemyContactWorkspaceStore


class SqlAlchemyApplicationWorkspaceStore(SqlAlchemyContactWorkspaceStore):
    """Serve application projections without invoking providers or workers."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def list_applications(
        self,
        *,
        owner_id: str,
        limit: int = 50,
        cursor: CursorToken | None = None,
    ) -> ApplicationListResponse:
        with _application_errors(), self.database.session() as session:
            return list_applications(
                session,
                owner_id=owner_id,
                limit=limit,
                cursor=cursor,
            )

    def get_application(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationDetailResponse | None:
        with _application_errors(), self.database.session() as session:
            return load_application_detail(
                session,
                owner_id=owner_id,
                application_id=application_id,
            )

    def list_activity(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationActivityListResponse | None:
        with _application_errors(), self.database.session() as session:
            return list_application_activity(
                session,
                owner_id=owner_id,
                application_id=application_id,
            )


@contextmanager
def _application_errors() -> Iterator[None]:
    try:
        yield
    except WorkspaceUnavailable:
        raise
    except ApplicationRepositoryError as exc:
        raise WorkspaceUnavailable("application data is inconsistent") from exc
    except ValidationError as exc:
        raise WorkspaceUnavailable(
            "stored application data failed contract validation"
        ) from exc
    except SQLAlchemyError as exc:
        raise WorkspaceUnavailable(
            "application workspace database is unavailable"
        ) from exc


__all__ = ["SqlAlchemyApplicationWorkspaceStore"]
