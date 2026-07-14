"""Transaction-owning adapter for the private application workspace."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .application_repository import (
    ApplicationRepositoryError,
    list_application_activity,
    list_applications,
    load_application_detail,
)
from .application_artifact_repository import (
    ApplicationArtifactRepositoryError,
    create_application_artifact_revision,
    load_application_artifacts,
    record_application_artifact_event,
)
from .application_artifact_schemas import (
    ApplicationArtifactEventCreate,
    ApplicationArtifactRevisionCreate,
    ApplicationArtifactsResponse,
)
from .application_pack_repository import (
    ApplicationPackRepositoryError,
    create_application_pack,
    create_application_pack_revision,
    load_application_pack,
    record_application_pack_event,
)
from .application_pack_schemas import (
    ApplicationPackCreate,
    ApplicationPackEventCreate,
    ApplicationPackRevisionCreate,
    ApplicationPackResponse,
)
from .application_schemas import (
    ApplicationActivityListResponse,
    ApplicationDetailResponse,
    ApplicationListResponse,
    CursorToken,
)
from .database import Database
from .mutation_receipts import MutationIdempotencyConflict, MutationPending
from .outreach_repository import (
    OutreachRepositoryError,
    load_application_outreach,
    record_outreach_event,
    save_outreach_message,
    start_outreach_sequence,
)
from .outreach_schemas import (
    ApplicationOutreachResponse,
    OutreachEventCreate,
    OutreachMessageCreate,
)
from .owner_workspace import WorkspaceConflict, WorkspaceInputError, WorkspaceUnavailable
from .private_payloads import PrivatePayloadBindingError
from .repository_errors import ResourceConflict, VersionConflict
from .security import DataKeyring, DecryptionError
from .sqlalchemy_contact_workspace import SqlAlchemyContactWorkspaceStore


class SqlAlchemyApplicationWorkspaceStore(SqlAlchemyContactWorkspaceStore):
    """Serve application projections without invoking providers or workers."""

    def __init__(self, database: Database, keyring: DataKeyring) -> None:
        self.database = database
        self.keyring = keyring

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

    def get_application_pack(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationPackResponse | None:
        with _application_pack_errors(), self.database.session() as session:
            return load_application_pack(
                session,
                owner_id=owner_id,
                application_id=application_id,
                keyring=self.keyring,
            )

    def get_application_artifacts(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationArtifactsResponse | None:
        with _application_artifact_errors(), self.database.session() as session:
            return load_application_artifacts(
                session,
                owner_id=owner_id,
                application_id=application_id,
                keyring=self.keyring,
            )

    def create_application_artifact_revision(
        self,
        *,
        owner_id: str,
        application_id: str,
        pack_id: str,
        payload: ApplicationArtifactRevisionCreate,
        expected_pack_version: int,
        idempotency_key: str,
    ) -> ApplicationArtifactsResponse | None:
        with _application_artifact_errors(), self.database.session() as session:
            return create_application_artifact_revision(
                session,
                owner_id=owner_id,
                application_id=application_id,
                pack_id=pack_id,
                payload=payload,
                expected_pack_version=expected_pack_version,
                idempotency_key=idempotency_key,
                keyring=self.keyring,
            )

    def record_application_artifact_event(
        self,
        *,
        owner_id: str,
        application_id: str,
        pack_id: str,
        payload: ApplicationArtifactEventCreate,
        expected_pack_version: int,
        idempotency_key: str,
    ) -> ApplicationArtifactsResponse | None:
        with _application_artifact_errors(), self.database.session() as session:
            return record_application_artifact_event(
                session,
                owner_id=owner_id,
                application_id=application_id,
                pack_id=pack_id,
                payload=payload,
                expected_pack_version=expected_pack_version,
                idempotency_key=idempotency_key,
                keyring=self.keyring,
            )

    def create_application_pack(
        self,
        *,
        owner_id: str,
        application_id: str,
        payload: ApplicationPackCreate,
        expected_application_version: int,
        idempotency_key: str,
    ) -> ApplicationPackResponse | None:
        with _application_pack_errors(), self.database.session() as session:
            return create_application_pack(
                session,
                owner_id=owner_id,
                application_id=application_id,
                payload=payload,
                expected_application_version=expected_application_version,
                idempotency_key=idempotency_key,
                keyring=self.keyring,
            )

    def create_application_pack_revision(
        self,
        *,
        owner_id: str,
        application_id: str,
        pack_id: str,
        payload: ApplicationPackRevisionCreate,
        expected_pack_version: int,
        idempotency_key: str,
    ) -> ApplicationPackResponse | None:
        with _application_pack_errors(), self.database.session() as session:
            return create_application_pack_revision(
                session,
                owner_id=owner_id,
                application_id=application_id,
                pack_id=pack_id,
                payload=payload,
                expected_pack_version=expected_pack_version,
                idempotency_key=idempotency_key,
                keyring=self.keyring,
            )

    def record_application_pack_event(
        self,
        *,
        owner_id: str,
        application_id: str,
        pack_id: str,
        payload: ApplicationPackEventCreate,
        expected_pack_version: int,
        idempotency_key: str,
    ) -> ApplicationPackResponse | None:
        with _application_pack_errors(), self.database.session() as session:
            return record_application_pack_event(
                session,
                owner_id=owner_id,
                application_id=application_id,
                pack_id=pack_id,
                payload=payload,
                expected_pack_version=expected_pack_version,
                idempotency_key=idempotency_key,
                keyring=self.keyring,
            )

    def get_application_outreach(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationOutreachResponse | None:
        with _outreach_errors(), self.database.session() as session:
            return load_application_outreach(
                session,
                owner_id=owner_id,
                application_id=application_id,
                keyring=self.keyring,
            )

    def start_application_outreach(
        self,
        *,
        owner_id: str,
        application_id: str,
        expected_application_version: int,
        idempotency_key: str,
    ) -> ApplicationOutreachResponse | None:
        with _outreach_errors(), self.database.session() as session:
            return start_outreach_sequence(
                session,
                owner_id=owner_id,
                application_id=application_id,
                expected_application_version=expected_application_version,
                idempotency_key=idempotency_key,
                keyring=self.keyring,
            )

    def save_outreach_message(
        self,
        *,
        owner_id: str,
        application_id: str,
        sequence_id: str,
        payload: OutreachMessageCreate,
        expected_sequence_version: int,
        idempotency_key: str,
    ) -> ApplicationOutreachResponse | None:
        with _outreach_errors(), self.database.session() as session:
            return save_outreach_message(
                session,
                owner_id=owner_id,
                application_id=application_id,
                sequence_id=sequence_id,
                payload=payload,
                expected_sequence_version=expected_sequence_version,
                idempotency_key=idempotency_key,
                keyring=self.keyring,
            )

    def record_outreach_event(
        self,
        *,
        owner_id: str,
        application_id: str,
        sequence_id: str,
        payload: OutreachEventCreate,
        expected_sequence_version: int,
        idempotency_key: str,
    ) -> ApplicationOutreachResponse | None:
        with _outreach_errors(), self.database.session() as session:
            return record_outreach_event(
                session,
                owner_id=owner_id,
                application_id=application_id,
                sequence_id=sequence_id,
                payload=payload,
                expected_sequence_version=expected_sequence_version,
                idempotency_key=idempotency_key,
                keyring=self.keyring,
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


@contextmanager
def _outreach_errors() -> Iterator[None]:
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
    except (OutreachRepositoryError, PrivatePayloadBindingError, DecryptionError) as exc:
        raise WorkspaceUnavailable("outreach data is inconsistent") from exc
    except ValidationError as exc:
        raise WorkspaceUnavailable(
            "stored outreach data failed contract validation"
        ) from exc
    except IntegrityError as exc:
        raise WorkspaceConflict("outreach conflicts with existing state") from exc
    except SQLAlchemyError as exc:
        raise WorkspaceUnavailable(
            "application outreach database is unavailable"
        ) from exc


@contextmanager
def _application_pack_errors() -> Iterator[None]:
    try:
        yield
    except WorkspaceUnavailable:
        raise
    except WorkspaceConflict:
        raise
    except WorkspaceInputError:
        raise
    except VersionConflict as exc:
        raise WorkspaceConflict(str(exc), code="version_conflict") from exc
    except MutationIdempotencyConflict as exc:
        raise WorkspaceConflict(str(exc), code="idempotency_conflict") from exc
    except MutationPending as exc:
        raise WorkspaceConflict(str(exc), code="mutation_pending") from exc
    except ResourceConflict as exc:
        raise WorkspaceConflict(str(exc)) from exc
    except (ApplicationPackRepositoryError, PrivatePayloadBindingError, DecryptionError) as exc:
        raise WorkspaceUnavailable("application-pack data is inconsistent") from exc
    except ValidationError as exc:
        raise WorkspaceUnavailable(
            "stored application-pack data failed contract validation"
        ) from exc
    except ValueError as exc:
        raise WorkspaceInputError(str(exc)) from exc
    except IntegrityError as exc:
        raise WorkspaceConflict("application pack conflicts with existing state") from exc
    except SQLAlchemyError as exc:
        raise WorkspaceUnavailable(
            "application-pack database is unavailable"
        ) from exc


@contextmanager
def _application_artifact_errors() -> Iterator[None]:
    try:
        yield
    except WorkspaceUnavailable:
        raise
    except WorkspaceConflict:
        raise
    except WorkspaceInputError:
        raise
    except VersionConflict as exc:
        raise WorkspaceConflict(str(exc), code="version_conflict") from exc
    except MutationIdempotencyConflict as exc:
        raise WorkspaceConflict(str(exc), code="idempotency_conflict") from exc
    except MutationPending as exc:
        raise WorkspaceConflict(str(exc), code="mutation_pending") from exc
    except ResourceConflict as exc:
        raise WorkspaceConflict(str(exc)) from exc
    except (
        ApplicationArtifactRepositoryError,
        PrivatePayloadBindingError,
        DecryptionError,
    ) as exc:
        raise WorkspaceUnavailable("application-artifact data is inconsistent") from exc
    except ValidationError as exc:
        raise WorkspaceUnavailable(
            "stored application-artifact data failed contract validation"
        ) from exc
    except ValueError as exc:
        raise WorkspaceInputError(str(exc)) from exc
    except IntegrityError as exc:
        raise WorkspaceConflict("application artifacts conflict with existing state") from exc
    except SQLAlchemyError as exc:
        raise WorkspaceUnavailable(
            "application-artifact database is unavailable"
        ) from exc


__all__ = ["SqlAlchemyApplicationWorkspaceStore"]
