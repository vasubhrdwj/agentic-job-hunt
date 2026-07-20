"""Authenticated database-only application workspace routes."""

from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    Security,
    status,
)
from fastapi.security import APIKeyCookie

from ..application_artifact_schemas import (
    ApplicationArtifactEventCreate,
    ApplicationArtifactRevisionCreate,
    ApplicationArtifactsResponse,
)
from ..application_pack_schemas import (
    ApplicationPackCreate,
    ApplicationPackEventCreate,
    ApplicationPackRevisionCreate,
    ApplicationPackResponse,
)
from ..application_schemas import (
    ApplicationActivityListResponse,
    ApplicationDetailResponse,
    ApplicationListResponse,
    ApplicationMilestoneCorrectionCreate,
    ApplicationMilestoneCorrectionMutationResponse,
    CursorToken,
    OpaqueId,
    TodayApplicationActionsResponse,
)
from ..application_submission_schemas import (
    ApplicationSubmissionProjection,
    ApplicationTransitionCreate,
    ApplicationTransitionResponse,
)
from ..application_workspace import ApplicationWorkspaceStore
from ..auth import session_cookie_name
from ..contact_schemas import ApplicationContactBenchResponse
from ..database import Database
from ..interview_round_schemas import (
    ApplicationInterviewRoundsResponse,
    InterviewRoundCreate,
    InterviewRoundEventCreate,
    InterviewRoundMutationResponse,
)
from ..interview_preparation_schemas import (
    ApplicationInterviewPreparationResponse,
    InterviewPreparationRevisionCreate,
)
from ..outreach_schemas import (
    ApplicationOutreachResponse,
    OutreachEventCreate,
    OutreachMessageCreate,
    OutreachReplyCreate,
)
from ..opportunity_schemas import OpportunityDecisionResponse
from ..resume_docx import DOCX_MEDIA_TYPE
from ..weekly_review_schemas import (
    ApplicationActionReviewCreate,
    ApplicationActionReviewMutationResponse,
    WeeklyReviewResponse,
)
from .session import AuthenticatedOwner, require_owner_mutation, require_owner_session
from .workspace import (
    COMMON_ERROR_RESPONSES,
    WorkspaceApiError,
    _expected_version,
    _invoke,
    _not_found,
    _raise_auth_problem,
    _required_idempotency_key,
    _set_etag,
)


def create_application_router(
    database: Database | None,
    store: ApplicationWorkspaceStore | None,
    *,
    allowed_origins: list[str],
    production: bool,
) -> APIRouter:
    """Build owner-scoped application reads and durable contact-search starts."""

    def prevent_private_caching(response: Response) -> None:
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"

    router = APIRouter(
        tags=["applications"],
        dependencies=[Depends(prevent_private_caching)],
    )
    owner_cookie = APIKeyCookie(
        name=session_cookie_name(),
        scheme_name="OwnerSessionCookie",
        description="Opaque HttpOnly session issued by POST /api/session.",
        auto_error=False,
    )

    def require_read_owner(
        request: Request,
        _session_cookie: str | None = Security(owner_cookie),
    ) -> AuthenticatedOwner:
        try:
            return require_owner_session(database, request)
        except HTTPException as exc:
            _raise_auth_problem(exc)

    def require_mutation_owner(
        request: Request,
        _session_cookie: str | None = Security(owner_cookie),
    ) -> AuthenticatedOwner:
        try:
            return require_owner_mutation(
                database,
                request,
                allowed_origins=allowed_origins,
                production=production,
            )
        except HTTPException as exc:
            _raise_auth_problem(exc)

    @router.get(
        "/api/today/application-actions",
        response_model=TodayApplicationActionsResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def list_today_application_action_items(
        limit: int = Query(default=20, ge=1, le=50),
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> TodayApplicationActionsResponse:
        return _invoke(
            _store(store).list_today_application_actions,
            owner_id=owner.owner_id,
            limit=limit,
        )

    @router.get(
        "/api/review/weekly",
        response_model=WeeklyReviewResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def get_owner_weekly_review(
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> WeeklyReviewResponse:
        return _invoke(_store(store).get_weekly_review, owner_id=owner.owner_id)

    @router.get(
        "/api/applications",
        response_model=ApplicationListResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def list_owner_applications(
        limit: int = Query(default=50, ge=1, le=50),
        cursor: CursorToken | None = Query(default=None),
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> ApplicationListResponse:
        return _invoke(
            _store(store).list_applications,
            owner_id=owner.owner_id,
            limit=limit,
            cursor=cursor,
        )

    @router.get(
        "/api/applications/{application_id}",
        response_model=ApplicationDetailResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def get_owner_application(
        application_id: OpaqueId,
        response: Response,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> ApplicationDetailResponse:
        application = _invoke(
            _store(store).get_application,
            owner_id=owner.owner_id,
            application_id=application_id,
        )
        if application is None:
            _not_found("application")
        _set_etag(response, application.application.version)
        return application

    @router.post(
        "/api/applications/{application_id}/undo-pursuit",
        response_model=OpportunityDecisionResponse,
        description=(
            "Undo an accidental, pre-submission pursuit. This discards only the "
            "application-owned preparation graph and restores the retained "
            "opportunity to the inbox. It fails closed after submission, sent "
            "outreach or replies, a hiring milestone, or an outcome."
        ),
        responses=COMMON_ERROR_RESPONSES,
    )
    def undo_owner_application_pursuit(
        application_id: OpaqueId,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> OpportunityDecisionResponse:
        decision = _invoke(
            _store(store).undo_application_pursuit,
            owner_id=owner.owner_id,
            application_id=application_id,
            expected_application_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        if decision is None:
            _not_found("application")
        _set_etag(response, decision.opportunity_version)
        return decision

    @router.get(
        "/api/applications/{application_id}/activity",
        response_model=ApplicationActivityListResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def list_owner_application_activity(
        application_id: OpaqueId,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> ApplicationActivityListResponse:
        activity = _invoke(
            _store(store).list_activity,
            owner_id=owner.owner_id,
            application_id=application_id,
        )
        if activity is None:
            _not_found("application")
        return activity

    @router.get(
        "/api/applications/{application_id}/submission",
        response_model=ApplicationSubmissionProjection,
        responses=COMMON_ERROR_RESPONSES,
    )
    def get_owner_application_submission(
        application_id: OpaqueId,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> ApplicationSubmissionProjection:
        submission = _invoke(
            _store(store).get_application_submission,
            owner_id=owner.owner_id,
            application_id=application_id,
        )
        if submission is None:
            _not_found("application")
        return submission

    @router.post(
        "/api/applications/{application_id}/transitions",
        response_model=ApplicationTransitionResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def transition_owner_application(
        application_id: OpaqueId,
        payload: ApplicationTransitionCreate,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApplicationTransitionResponse:
        transition = _invoke(
            _store(store).transition_application,
            owner_id=owner.owner_id,
            application_id=application_id,
            payload=payload,
            expected_application_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        if transition is None:
            _not_found("application")
        _set_etag(response, transition.application.version)
        return transition

    @router.post(
        "/api/applications/{application_id}/actions/{action_id}/reviews",
        response_model=ApplicationActionReviewMutationResponse,
        status_code=status.HTTP_201_CREATED,
        responses=COMMON_ERROR_RESPONSES,
    )
    def review_owner_application_action(
        application_id: OpaqueId,
        action_id: OpaqueId,
        payload: ApplicationActionReviewCreate,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(
            default=None, alias="Idempotency-Key"
        ),
    ) -> ApplicationActionReviewMutationResponse:
        expected_application_version = _expected_version(if_match)
        required_idempotency_key = _required_idempotency_key(idempotency_key)
        mutation = _invoke(
            _store(store).record_application_action_review,
            owner_id=owner.owner_id,
            application_id=application_id,
            action_id=action_id,
            payload=payload,
            expected_application_version=expected_application_version,
            idempotency_key=required_idempotency_key,
        )
        if mutation is None:
            _not_found("application action")
        _set_etag(response, mutation.application.version)
        return mutation

    @router.post(
        "/api/applications/{application_id}/activity/"
        "{activity_event_id}/corrections",
        response_model=ApplicationMilestoneCorrectionMutationResponse,
        status_code=status.HTTP_201_CREATED,
        description=(
            "Correct only the effective date of an original, manually recorded "
            "screening, unlinked interview, or offer milestone. The immutable "
            "original and every correction remain visible; the application stage, "
            "current task, submission, and outcome do not change."
        ),
        responses=COMMON_ERROR_RESPONSES,
    )
    def correct_owner_application_milestone_date(
        application_id: OpaqueId,
        activity_event_id: OpaqueId,
        payload: ApplicationMilestoneCorrectionCreate,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(
            default=None,
            alias="If-Match",
            description="Required strong ETag for the application being updated.",
        ),
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            description="Required retry key for this correction attempt.",
        ),
    ) -> ApplicationMilestoneCorrectionMutationResponse:
        mutation = _invoke(
            _store(store).record_application_milestone_correction,
            owner_id=owner.owner_id,
            application_id=application_id,
            activity_event_id=activity_event_id,
            payload=payload,
            expected_application_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        if mutation is None:
            _not_found("application milestone")
        _set_etag(response, mutation.application.version)
        return mutation

    @router.get(
        "/api/applications/{application_id}/interview-rounds",
        response_model=ApplicationInterviewRoundsResponse,
        description=(
            "Load the saved interview-round timeline. The response ETag names the "
            "current application version used when scheduling a new round."
        ),
        responses=COMMON_ERROR_RESPONSES,
    )
    def get_owner_application_interview_rounds(
        application_id: OpaqueId,
        response: Response,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> ApplicationInterviewRoundsResponse:
        interview_rounds = _invoke(
            _store(store).get_application_interview_rounds,
            owner_id=owner.owner_id,
            application_id=application_id,
        )
        if interview_rounds is None:
            _not_found("application")
        _set_etag(response, interview_rounds.application.version)
        return interview_rounds

    @router.get(
        "/api/applications/{application_id}/interview-preparation",
        response_model=ApplicationInterviewPreparationResponse,
        description=(
            "Load deterministic, database-only interview prompts pinned to the exact "
            "submitted application, reviewed evidence, posting, and scheduled round."
        ),
        responses=COMMON_ERROR_RESPONSES,
    )
    def get_owner_application_interview_preparation(
        application_id: OpaqueId,
        response: Response,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> ApplicationInterviewPreparationResponse:
        preparation = _invoke(
            _store(store).get_application_interview_preparation,
            owner_id=owner.owner_id,
            application_id=application_id,
        )
        if preparation is None:
            _not_found("application")
        _set_etag(response, preparation.write_version)
        return preparation

    @router.post(
        "/api/applications/{application_id}/interview-preparation/revisions",
        response_model=ApplicationInterviewPreparationResponse,
        status_code=status.HTTP_201_CREATED,
        description=(
            "Append encrypted owner-authored STAR fields. If-Match names the "
            "application version before the first save and the preparation version "
            "afterward; Idempotency-Key makes exact retries safe."
        ),
        responses=COMMON_ERROR_RESPONSES,
    )
    def create_owner_interview_preparation_revision(
        application_id: OpaqueId,
        payload: InterviewPreparationRevisionCreate,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApplicationInterviewPreparationResponse:
        preparation = _invoke(
            _store(store).create_interview_preparation_revision,
            owner_id=owner.owner_id,
            application_id=application_id,
            payload=payload,
            expected_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        if preparation is None:
            _not_found("application")
        _set_etag(response, preparation.write_version)
        return preparation

    @router.post(
        "/api/applications/{application_id}/interview-rounds",
        response_model=InterviewRoundMutationResponse,
        status_code=status.HTTP_201_CREATED,
        description=(
            "Schedule one interview round. If-Match must contain the application "
            "ETag; the response ETag names the created round version."
        ),
        responses=COMMON_ERROR_RESPONSES,
    )
    def schedule_owner_application_interview_round(
        application_id: OpaqueId,
        payload: InterviewRoundCreate,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(
            default=None,
            alias="If-Match",
            description="Required strong ETag for the application being updated.",
        ),
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            description="Required retry key for this scheduling attempt.",
        ),
    ) -> InterviewRoundMutationResponse:
        mutation = _invoke(
            _store(store).schedule_interview_round,
            owner_id=owner.owner_id,
            application_id=application_id,
            payload=payload,
            expected_application_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        if mutation is None:
            _not_found("application")
        _set_etag(response, mutation.round.version)
        return mutation

    @router.post(
        "/api/applications/{application_id}/interview-rounds/"
        "{interview_round_id}/events",
        response_model=InterviewRoundMutationResponse,
        description=(
            "Reschedule, complete, or cancel one scheduled round. If-Match must "
            "contain that round's ETag; the response ETag names its new version."
        ),
        responses=COMMON_ERROR_RESPONSES,
    )
    def record_owner_application_interview_round_event(
        application_id: OpaqueId,
        interview_round_id: OpaqueId,
        payload: InterviewRoundEventCreate,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(
            default=None,
            alias="If-Match",
            description="Required strong ETag for the interview round being updated.",
        ),
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            description="Required retry key for this round update.",
        ),
    ) -> InterviewRoundMutationResponse:
        mutation = _invoke(
            _store(store).record_interview_round_event,
            owner_id=owner.owner_id,
            application_id=application_id,
            interview_round_id=interview_round_id,
            payload=payload,
            expected_round_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        if mutation is None:
            _not_found("interview round")
        _set_etag(response, mutation.round.version)
        return mutation

    @router.get(
        "/api/applications/{application_id}/application-pack",
        response_model=ApplicationPackResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def get_owner_application_pack(
        application_id: OpaqueId,
        response: Response,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> ApplicationPackResponse:
        application_pack = _invoke(
            _store(store).get_application_pack,
            owner_id=owner.owner_id,
            application_id=application_id,
        )
        if application_pack is None:
            _not_found("application")
        if application_pack.pack is not None:
            _set_etag(response, application_pack.pack.version)
        return application_pack

    @router.post(
        "/api/applications/{application_id}/application-packs",
        response_model=ApplicationPackResponse,
        status_code=status.HTTP_201_CREATED,
        responses=COMMON_ERROR_RESPONSES,
    )
    def create_owner_application_pack(
        application_id: OpaqueId,
        payload: ApplicationPackCreate,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApplicationPackResponse:
        application_pack = _invoke(
            _store(store).create_application_pack,
            owner_id=owner.owner_id,
            application_id=application_id,
            payload=payload,
            expected_application_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        if application_pack is None:
            _not_found("application")
        if application_pack.pack is None:
            raise WorkspaceApiError(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "workspace_unavailable",
                "application-pack storage is unavailable",
                retryable=True,
            )
        _set_etag(response, application_pack.pack.version)
        return application_pack

    @router.post(
        "/api/applications/{application_id}/application-packs/{pack_id}/revisions",
        response_model=ApplicationPackResponse,
        status_code=status.HTTP_201_CREATED,
        responses=COMMON_ERROR_RESPONSES,
    )
    def create_owner_application_pack_revision(
        application_id: OpaqueId,
        pack_id: OpaqueId,
        payload: ApplicationPackRevisionCreate,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApplicationPackResponse:
        application_pack = _invoke(
            _store(store).create_application_pack_revision,
            owner_id=owner.owner_id,
            application_id=application_id,
            pack_id=pack_id,
            payload=payload,
            expected_pack_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        if application_pack is None:
            _not_found("application pack")
        if application_pack.pack is None:
            raise WorkspaceApiError(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "workspace_unavailable",
                "application-pack storage is unavailable",
                retryable=True,
            )
        _set_etag(response, application_pack.pack.version)
        return application_pack

    @router.post(
        "/api/applications/{application_id}/application-packs/{pack_id}/events",
        response_model=ApplicationPackResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def record_owner_application_pack_event(
        application_id: OpaqueId,
        pack_id: OpaqueId,
        payload: ApplicationPackEventCreate,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApplicationPackResponse:
        application_pack = _invoke(
            _store(store).record_application_pack_event,
            owner_id=owner.owner_id,
            application_id=application_id,
            pack_id=pack_id,
            payload=payload,
            expected_pack_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        if application_pack is None:
            _not_found("application pack")
        if application_pack.pack is None:
            raise WorkspaceApiError(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "workspace_unavailable",
                "application-pack storage is unavailable",
                retryable=True,
            )
        _set_etag(response, application_pack.pack.version)
        return application_pack

    @router.get(
        "/api/applications/{application_id}/application-artifacts",
        response_model=ApplicationArtifactsResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def get_owner_application_artifacts(
        application_id: OpaqueId,
        response: Response,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> ApplicationArtifactsResponse:
        artifacts = _invoke(
            _store(store).get_application_artifacts,
            owner_id=owner.owner_id,
            application_id=application_id,
        )
        if artifacts is None:
            _not_found("application")
        if artifacts.pack is not None:
            _set_etag(response, artifacts.pack.version)
        return artifacts

    @router.get(
        "/api/applications/{application_id}/application-artifacts/approved-resume.docx",
        response_class=Response,
        description=(
            "Download a single-column, upload-ready DOCX built only from the exact "
            "current approved tailored-resume artifact. Draft and superseded "
            "approvals fail closed."
        ),
        responses={
            200: {
                "description": "Exact current approved tailored resume",
                "content": {DOCX_MEDIA_TYPE: {}},
            },
            **COMMON_ERROR_RESPONSES,
        },
    )
    def download_owner_approved_tailored_resume(
        application_id: OpaqueId,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> Response:
        export = _invoke(
            _store(store).get_approved_tailored_resume_docx,
            owner_id=owner.owner_id,
            application_id=application_id,
        )
        if export is None:
            _not_found("application")
        return Response(
            content=export.content,
            media_type=DOCX_MEDIA_TYPE,
            headers={
                "Cache-Control": "private, no-store, max-age=0",
                "Pragma": "no-cache",
                "Content-Disposition": f'attachment; filename="{export.filename}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post(
        "/api/applications/{application_id}/application-packs/{pack_id}/artifact-revisions",
        response_model=ApplicationArtifactsResponse,
        status_code=status.HTTP_201_CREATED,
        responses=COMMON_ERROR_RESPONSES,
    )
    def create_owner_application_artifact_revision(
        application_id: OpaqueId,
        pack_id: OpaqueId,
        payload: ApplicationArtifactRevisionCreate,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApplicationArtifactsResponse:
        artifacts = _invoke(
            _store(store).create_application_artifact_revision,
            owner_id=owner.owner_id,
            application_id=application_id,
            pack_id=pack_id,
            payload=payload,
            expected_pack_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        if artifacts is None:
            _not_found("application pack")
        if artifacts.pack is None:
            raise WorkspaceApiError(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "workspace_unavailable",
                "application-artifact storage is unavailable",
                retryable=True,
            )
        _set_etag(response, artifacts.pack.version)
        return artifacts

    @router.post(
        "/api/applications/{application_id}/application-packs/{pack_id}/artifact-events",
        response_model=ApplicationArtifactsResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def record_owner_application_artifact_event(
        application_id: OpaqueId,
        pack_id: OpaqueId,
        payload: ApplicationArtifactEventCreate,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApplicationArtifactsResponse:
        artifacts = _invoke(
            _store(store).record_application_artifact_event,
            owner_id=owner.owner_id,
            application_id=application_id,
            pack_id=pack_id,
            payload=payload,
            expected_pack_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        if artifacts is None:
            _not_found("application pack")
        if artifacts.pack is None:
            raise WorkspaceApiError(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "workspace_unavailable",
                "application-artifact storage is unavailable",
                retryable=True,
            )
        _set_etag(response, artifacts.pack.version)
        return artifacts

    @router.get(
        "/api/applications/{application_id}/contacts",
        response_model=ApplicationContactBenchResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def get_owner_application_contacts(
        application_id: OpaqueId,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> ApplicationContactBenchResponse:
        contacts = _invoke(
            _store(store).get_application_contacts,
            owner_id=owner.owner_id,
            application_id=application_id,
        )
        if contacts is None:
            _not_found("application")
        return contacts

    @router.post(
        "/api/applications/{application_id}/contact-searches",
        response_model=ApplicationContactBenchResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses=COMMON_ERROR_RESPONSES,
    )
    def create_owner_application_contact_search(
        application_id: OpaqueId,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApplicationContactBenchResponse:
        contacts = _invoke(
            _store(store).create_application_contact_search,
            owner_id=owner.owner_id,
            application_id=application_id,
            expected_application_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        if contacts is None:
            _not_found("application")
        return contacts

    @router.get(
        "/api/applications/{application_id}/outreach",
        response_model=ApplicationOutreachResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def get_owner_application_outreach(
        application_id: OpaqueId,
        response: Response,
        owner: AuthenticatedOwner = Security(require_read_owner),
    ) -> ApplicationOutreachResponse:
        outreach = _invoke(
            _store(store).get_application_outreach,
            owner_id=owner.owner_id,
            application_id=application_id,
        )
        if outreach is None:
            _not_found("application")
        if outreach.sequence is not None:
            _set_etag(response, outreach.sequence.version)
        return outreach

    @router.post(
        "/api/applications/{application_id}/outreach-sequences",
        response_model=ApplicationOutreachResponse,
        status_code=status.HTTP_201_CREATED,
        responses=COMMON_ERROR_RESPONSES,
    )
    def start_owner_application_outreach(
        application_id: OpaqueId,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApplicationOutreachResponse:
        outreach = _invoke(
            _store(store).start_application_outreach,
            owner_id=owner.owner_id,
            application_id=application_id,
            expected_application_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        if outreach is None:
            _not_found("application")
        if outreach.sequence is None:
            raise WorkspaceApiError(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "workspace_unavailable",
                "application outreach storage is unavailable",
                retryable=True,
            )
        _set_etag(response, outreach.sequence.version)
        return outreach

    @router.post(
        "/api/applications/{application_id}/outreach-sequences/{sequence_id}/messages",
        response_model=ApplicationOutreachResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def save_owner_application_outreach_message(
        application_id: OpaqueId,
        sequence_id: OpaqueId,
        payload: OutreachMessageCreate,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApplicationOutreachResponse:
        outreach = _invoke(
            _store(store).save_outreach_message,
            owner_id=owner.owner_id,
            application_id=application_id,
            sequence_id=sequence_id,
            payload=payload,
            expected_sequence_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        if outreach is None:
            _not_found("outreach sequence")
        if outreach.sequence is None:
            raise WorkspaceApiError(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "workspace_unavailable",
                "application outreach storage is unavailable",
                retryable=True,
            )
        _set_etag(response, outreach.sequence.version)
        return outreach

    @router.post(
        "/api/applications/{application_id}/outreach-sequences/{sequence_id}/replies",
        response_model=ApplicationOutreachResponse,
        status_code=status.HTTP_201_CREATED,
        responses=COMMON_ERROR_RESPONSES,
    )
    def record_owner_application_outreach_reply(
        application_id: OpaqueId,
        sequence_id: OpaqueId,
        payload: OutreachReplyCreate,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApplicationOutreachResponse:
        outreach = _invoke(
            _store(store).record_outreach_reply,
            owner_id=owner.owner_id,
            application_id=application_id,
            sequence_id=sequence_id,
            payload=payload,
            expected_sequence_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        if outreach is None:
            _not_found("outreach sequence")
        if outreach.sequence is None:
            raise WorkspaceApiError(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "workspace_unavailable",
                "application outreach storage is unavailable",
                retryable=True,
            )
        _set_etag(response, outreach.sequence.version)
        return outreach

    @router.post(
        "/api/applications/{application_id}/outreach-sequences/{sequence_id}/events",
        response_model=ApplicationOutreachResponse,
        responses=COMMON_ERROR_RESPONSES,
    )
    def record_owner_application_outreach_event(
        application_id: OpaqueId,
        sequence_id: OpaqueId,
        payload: OutreachEventCreate,
        response: Response,
        owner: AuthenticatedOwner = Security(require_mutation_owner),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApplicationOutreachResponse:
        outreach = _invoke(
            _store(store).record_outreach_event,
            owner_id=owner.owner_id,
            application_id=application_id,
            sequence_id=sequence_id,
            payload=payload,
            expected_sequence_version=_expected_version(if_match),
            idempotency_key=_required_idempotency_key(idempotency_key),
        )
        if outreach is None:
            _not_found("outreach sequence")
        if outreach.sequence is None:
            raise WorkspaceApiError(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "workspace_unavailable",
                "application outreach storage is unavailable",
                retryable=True,
            )
        _set_etag(response, outreach.sequence.version)
        return outreach

    return router


def _store(store: ApplicationWorkspaceStore | None) -> ApplicationWorkspaceStore:
    if store is None:
        raise WorkspaceApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "workspace_unavailable",
            "application workspace storage is unavailable",
            retryable=True,
        )
    return store


__all__ = ["create_application_router"]
