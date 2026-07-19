"""Transaction-owning adapter for persisted contact-bench reads."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .contact_repository import ContactRepositoryError, load_application_contact_bench
from .contact_search_repository import (
    CONTACT_SEARCH_JOB_KIND,
    ContactSearchRepositoryError,
    create_contact_search,
)
from .contact_schemas import ApplicationContactBenchResponse
from .database import Database
from .mutation_receipts import MutationIdempotencyConflict, MutationPending
from .owner_workspace import (
    WorkspaceCapabilityUnavailable,
    WorkspaceConflict,
    WorkspaceUnavailable,
)
from .repository_errors import ResourceConflict, VersionConflict
from .worker_health import load_worker_capability


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

    def create_application_contact_search(
        self,
        *,
        owner_id: str,
        application_id: str,
        expected_application_version: int,
        idempotency_key: str,
    ) -> ApplicationContactBenchResponse | None:
        """Queue or replay a search and project its bench in one transaction."""

        with _contact_errors(), self.database.session() as session:
            capability = load_worker_capability(
                session,
                kind=CONTACT_SEARCH_JOB_KIND,
            )
            if not capability.available:
                raise WorkspaceCapabilityUnavailable(
                    "contact_search",
                    reason=capability.reason,
                )
            created = create_contact_search(
                session,
                owner_id=owner_id,
                application_id=application_id,
                expected_application_version=expected_application_version,
                idempotency_key=idempotency_key,
            )
            if created is None:
                return None
            response = load_application_contact_bench(
                session,
                owner_id=owner_id,
                application_id=application_id,
            )
            if response is None:
                raise ContactSearchRepositoryError(
                    "created contact search has no application projection"
                )
            return response


@contextmanager
def _contact_errors() -> Iterator[None]:
    try:
        yield
    except WorkspaceUnavailable:
        raise
    except WorkspaceConflict:
        raise
    except VersionConflict as exc:
        raise WorkspaceConflict(str(exc), code="version_conflict") from exc
    except MutationIdempotencyConflict as exc:
        raise WorkspaceConflict(str(exc), code="idempotency_conflict") from exc
    except MutationPending as exc:
        raise WorkspaceConflict(str(exc), code="mutation_pending") from exc
    except ResourceConflict as exc:
        raise WorkspaceConflict(str(exc)) from exc
    except ContactSearchRepositoryError as exc:
        raise WorkspaceUnavailable("contact search data is inconsistent") from exc
    except ContactRepositoryError as exc:
        raise WorkspaceUnavailable("contact data is inconsistent") from exc
    except ValidationError as exc:
        raise WorkspaceUnavailable(
            "stored contact data failed contract validation"
        ) from exc
    except IntegrityError as exc:
        raise WorkspaceConflict(
            "contact search conflicts with existing state"
        ) from exc
    except SQLAlchemyError as exc:
        raise WorkspaceUnavailable(
            "contact workspace database is unavailable"
        ) from exc


__all__ = ["SqlAlchemyContactWorkspaceStore"]
