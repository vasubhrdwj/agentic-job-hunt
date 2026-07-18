"""Transaction-owning adapter for the private application workspace."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

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
from .application_correction_repository import (
    ApplicationCorrectionRepositoryError,
    record_application_milestone_correction,
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
from .application_repository import (
    ApplicationRepositoryError,
    list_application_activity,
    list_applications,
    list_today_application_actions,
    load_application_detail,
    undo_application_pursuit,
)
from .application_schemas import (
    ApplicationActivityListResponse,
    ApplicationDetailResponse,
    ApplicationListResponse,
    ApplicationMilestoneCorrectionCreate,
    ApplicationMilestoneCorrectionMutationResponse,
    CursorToken,
    TodayApplicationActionsResponse,
)
from .application_submission_repository import (
    ApplicationSubmissionRepositoryError,
    load_application_submission,
    transition_application,
)
from .application_submission_schemas import (
    ApplicationSubmissionProjection,
    ApplicationTransitionCreate,
    ApplicationTransitionResponse,
)
from .database import Database
from .interview_round_repository import (
    InterviewRoundRepositoryError,
    load_application_interview_rounds,
    record_interview_round_event,
    schedule_interview_round,
)
from .interview_round_schemas import (
    ApplicationInterviewRoundsResponse,
    InterviewRoundCreate,
    InterviewRoundEventCreate,
    InterviewRoundMutationResponse,
)
from .interview_preparation_repository import (
    InterviewPreparationRepositoryError,
    create_interview_preparation_revision,
    load_application_interview_preparation,
)
from .interview_preparation_schemas import (
    ApplicationInterviewPreparationResponse,
    InterviewPreparationRevisionCreate,
)
from .mutation_receipts import MutationIdempotencyConflict, MutationPending
from .outreach_repository import (
    OutreachRepositoryError,
    load_application_outreach,
    record_outreach_event,
    record_outreach_reply,
    save_outreach_message,
    start_outreach_sequence,
)
from .outreach_schemas import (
    ApplicationOutreachResponse,
    OutreachEventCreate,
    OutreachMessageCreate,
    OutreachReplyCreate,
)
from .owner_workspace import (
    WorkspaceConflict,
    WorkspaceInputError,
    WorkspaceUnavailable,
)
from .opportunity_schemas import OpportunityDecisionResponse
from .private_payloads import PrivatePayloadBindingError
from .repository_errors import ResourceConflict, VersionConflict
from .security import DataKeyring, DecryptionError
from .sqlalchemy_contact_workspace import SqlAlchemyContactWorkspaceStore
from .weekly_review_repository import (
    WeeklyReviewRepositoryError,
    load_weekly_review,
    record_application_action_review,
)
from .weekly_review_schemas import (
    ApplicationActionReviewCreate,
    ApplicationActionReviewMutationResponse,
    WeeklyReviewResponse,
)


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

    def list_today_application_actions(
        self,
        *,
        owner_id: str,
        limit: int = 20,
    ) -> TodayApplicationActionsResponse:
        with _application_errors(), self.database.session() as session:
            return list_today_application_actions(
                session,
                owner_id=owner_id,
                limit=limit,
            )

    def get_weekly_review(self, *, owner_id: str) -> WeeklyReviewResponse:
        with _weekly_review_errors(), self.database.session() as session:
            return load_weekly_review(session, owner_id=owner_id)

    def record_application_action_review(
        self,
        *,
        owner_id: str,
        application_id: str,
        action_id: str,
        payload: ApplicationActionReviewCreate,
        expected_application_version: int,
        idempotency_key: str,
    ) -> ApplicationActionReviewMutationResponse | None:
        with _weekly_review_errors(), self.database.session() as session:
            return record_application_action_review(
                session,
                owner_id=owner_id,
                application_id=application_id,
                action_id=action_id,
                payload=payload,
                expected_application_version=expected_application_version,
                idempotency_key=idempotency_key,
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

    def undo_application_pursuit(
        self,
        *,
        owner_id: str,
        application_id: str,
        expected_application_version: int,
        idempotency_key: str,
    ) -> OpportunityDecisionResponse | None:
        with _application_undo_errors(), self.database.session() as session:
            return undo_application_pursuit(
                session,
                owner_id=owner_id,
                application_id=application_id,
                expected_application_version=expected_application_version,
                idempotency_key=idempotency_key,
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

    def get_application_submission(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationSubmissionProjection | None:
        with _application_submission_errors(), self.database.session() as session:
            return load_application_submission(
                session,
                owner_id=owner_id,
                application_id=application_id,
            )

    def transition_application(
        self,
        *,
        owner_id: str,
        application_id: str,
        payload: ApplicationTransitionCreate,
        expected_application_version: int,
        idempotency_key: str,
    ) -> ApplicationTransitionResponse | None:
        with _application_submission_errors(), self.database.session() as session:
            return transition_application(
                session,
                owner_id=owner_id,
                application_id=application_id,
                payload=payload,
                expected_application_version=expected_application_version,
                idempotency_key=idempotency_key,
                keyring=self.keyring,
            )

    def record_application_milestone_correction(
        self,
        *,
        owner_id: str,
        application_id: str,
        activity_event_id: str,
        payload: ApplicationMilestoneCorrectionCreate,
        expected_application_version: int,
        idempotency_key: str,
    ) -> ApplicationMilestoneCorrectionMutationResponse | None:
        with _application_correction_errors(), self.database.session() as session:
            return record_application_milestone_correction(
                session,
                owner_id=owner_id,
                application_id=application_id,
                activity_event_id=activity_event_id,
                payload=payload,
                expected_application_version=expected_application_version,
                idempotency_key=idempotency_key,
            )

    def get_application_interview_rounds(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationInterviewRoundsResponse | None:
        with _interview_round_errors(), self.database.session() as session:
            return load_application_interview_rounds(
                session,
                owner_id=owner_id,
                application_id=application_id,
            )

    def get_application_interview_preparation(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationInterviewPreparationResponse | None:
        with _interview_preparation_errors(), self.database.session() as session:
            return load_application_interview_preparation(
                session,
                owner_id=owner_id,
                application_id=application_id,
                keyring=self.keyring,
            )

    def create_interview_preparation_revision(
        self,
        *,
        owner_id: str,
        application_id: str,
        payload: InterviewPreparationRevisionCreate,
        expected_version: int,
        idempotency_key: str,
    ) -> ApplicationInterviewPreparationResponse | None:
        with _interview_preparation_errors(), self.database.session() as session:
            return create_interview_preparation_revision(
                session,
                owner_id=owner_id,
                application_id=application_id,
                payload=payload,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
                keyring=self.keyring,
            )

    def schedule_interview_round(
        self,
        *,
        owner_id: str,
        application_id: str,
        payload: InterviewRoundCreate,
        expected_application_version: int,
        idempotency_key: str,
    ) -> InterviewRoundMutationResponse | None:
        with _interview_round_errors(), self.database.session() as session:
            return schedule_interview_round(
                session,
                owner_id=owner_id,
                application_id=application_id,
                payload=payload,
                expected_application_version=expected_application_version,
                idempotency_key=idempotency_key,
            )

    def record_interview_round_event(
        self,
        *,
        owner_id: str,
        application_id: str,
        interview_round_id: str,
        payload: InterviewRoundEventCreate,
        expected_round_version: int,
        idempotency_key: str,
    ) -> InterviewRoundMutationResponse | None:
        with _interview_round_errors(), self.database.session() as session:
            return record_interview_round_event(
                session,
                owner_id=owner_id,
                application_id=application_id,
                interview_round_id=interview_round_id,
                payload=payload,
                expected_round_version=expected_round_version,
                idempotency_key=idempotency_key,
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

    def record_outreach_reply(
        self,
        *,
        owner_id: str,
        application_id: str,
        sequence_id: str,
        payload: OutreachReplyCreate,
        expected_sequence_version: int,
        idempotency_key: str,
    ) -> ApplicationOutreachResponse | None:
        with _outreach_errors(), self.database.session() as session:
            return record_outreach_reply(
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
def _application_undo_errors() -> Iterator[None]:
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
    except ApplicationRepositoryError as exc:
        raise WorkspaceUnavailable("application data is inconsistent") from exc
    except ValidationError as exc:
        raise WorkspaceUnavailable(
            "stored application data failed contract validation"
        ) from exc
    except ValueError as exc:
        raise WorkspaceInputError(str(exc)) from exc
    except IntegrityError as exc:
        raise WorkspaceConflict(
            "undo pursuit conflicts with existing application state"
        ) from exc
    except SQLAlchemyError as exc:
        raise WorkspaceUnavailable(
            "application workspace database is unavailable"
        ) from exc


@contextmanager
def _weekly_review_errors() -> Iterator[None]:
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
    except WeeklyReviewRepositoryError as exc:
        raise WorkspaceUnavailable("weekly-review data is inconsistent") from exc
    except ValidationError as exc:
        raise WorkspaceUnavailable(
            "stored weekly-review data failed contract validation"
        ) from exc
    except ValueError as exc:
        raise WorkspaceInputError(str(exc)) from exc
    except IntegrityError as exc:
        raise WorkspaceConflict("weekly review conflicts with existing state") from exc
    except SQLAlchemyError as exc:
        raise WorkspaceUnavailable("weekly-review database is unavailable") from exc


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
def _application_submission_errors() -> Iterator[None]:
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
    except ApplicationSubmissionRepositoryError as exc:
        raise WorkspaceUnavailable(
            "application-submission data is inconsistent"
        ) from exc
    except ValidationError as exc:
        raise WorkspaceUnavailable(
            "stored application-submission data failed contract validation"
        ) from exc
    except ValueError as exc:
        raise WorkspaceInputError(str(exc)) from exc
    except IntegrityError as exc:
        raise WorkspaceConflict(
            "application submission conflicts with existing state"
        ) from exc
    except SQLAlchemyError as exc:
        raise WorkspaceUnavailable(
            "application-submission database is unavailable"
        ) from exc


@contextmanager
def _application_correction_errors() -> Iterator[None]:
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
    except ApplicationCorrectionRepositoryError as exc:
        raise WorkspaceUnavailable(
            "application milestone-correction data is inconsistent"
        ) from exc
    except ValidationError as exc:
        raise WorkspaceUnavailable(
            "stored milestone-correction data failed contract validation"
        ) from exc
    except ValueError as exc:
        raise WorkspaceInputError(str(exc)) from exc
    except IntegrityError as exc:
        raise WorkspaceConflict(
            "application milestone correction conflicts with existing state"
        ) from exc
    except SQLAlchemyError as exc:
        raise WorkspaceUnavailable(
            "application milestone-correction database is unavailable"
        ) from exc


@contextmanager
def _interview_round_errors() -> Iterator[None]:
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
    except InterviewRoundRepositoryError as exc:
        raise WorkspaceUnavailable("interview-round data is inconsistent") from exc
    except ValidationError as exc:
        raise WorkspaceUnavailable(
            "stored interview-round data failed contract validation"
        ) from exc
    except ValueError as exc:
        raise WorkspaceInputError(str(exc)) from exc
    except IntegrityError as exc:
        raise WorkspaceConflict(
            "interview round conflicts with existing state"
        ) from exc
    except SQLAlchemyError as exc:
        raise WorkspaceUnavailable(
            "interview-round database is unavailable"
        ) from exc


@contextmanager
def _interview_preparation_errors() -> Iterator[None]:
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
        InterviewPreparationRepositoryError,
        PrivatePayloadBindingError,
        DecryptionError,
    ) as exc:
        raise WorkspaceUnavailable(
            "interview-preparation data is inconsistent"
        ) from exc
    except ValidationError as exc:
        raise WorkspaceUnavailable(
            "stored interview-preparation data failed contract validation"
        ) from exc
    except ValueError as exc:
        raise WorkspaceInputError(str(exc)) from exc
    except IntegrityError as exc:
        raise WorkspaceConflict(
            "interview preparation conflicts with existing state"
        ) from exc
    except SQLAlchemyError as exc:
        raise WorkspaceUnavailable(
            "interview-preparation database is unavailable"
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
