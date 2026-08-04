"""Transport boundary for persisted applications and next actions."""

from __future__ import annotations

from typing import Protocol

from .application_artifact_workspace import ApplicationArtifactWorkspaceStore
from .application_dossier_workspace import ApplicationDossierWorkspaceStore
from .application_pack_workspace import ApplicationPackWorkspaceStore
from .application_schemas import (
    ApplicationActivityListResponse,
    ApplicationDetailResponse,
    ApplicationListResponse,
    ApplicationMilestoneCorrectionCreate,
    ApplicationMilestoneCorrectionMutationResponse,
    CursorToken,
    TodayApplicationActionsResponse,
)
from .application_submission_schemas import (
    ApplicationSubmissionProjection,
    ApplicationTransitionCreate,
    ApplicationTransitionResponse,
)
from .contact_workspace import ContactWorkspaceStore
from .interview_round_workspace import InterviewRoundWorkspaceStore
from .interview_preparation_workspace import InterviewPreparationWorkspaceStore
from .outreach_workspace import OutreachWorkspaceStore
from .opportunity_schemas import OpportunityDecisionResponse
from .weekly_review_workspace import WeeklyReviewWorkspaceStore


class ApplicationWorkspaceStore(
    ApplicationArtifactWorkspaceStore,
    ApplicationDossierWorkspaceStore,
    ApplicationPackWorkspaceStore,
    ContactWorkspaceStore,
    InterviewRoundWorkspaceStore,
    InterviewPreparationWorkspaceStore,
    OutreachWorkspaceStore,
    WeeklyReviewWorkspaceStore,
    Protocol,
):
    """Owner-scoped, database-only reads for the application workspace."""

    def list_applications(
        self,
        *,
        owner_id: str,
        limit: int = 50,
        cursor: CursorToken | None = None,
    ) -> ApplicationListResponse: ...

    def list_today_application_actions(
        self,
        *,
        owner_id: str,
        limit: int = 20,
    ) -> TodayApplicationActionsResponse: ...

    def get_application(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationDetailResponse | None: ...

    def undo_application_pursuit(
        self,
        *,
        owner_id: str,
        application_id: str,
        expected_application_version: int,
        idempotency_key: str,
    ) -> OpportunityDecisionResponse | None: ...

    def list_activity(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationActivityListResponse | None: ...

    def get_application_submission(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationSubmissionProjection | None: ...

    def transition_application(
        self,
        *,
        owner_id: str,
        application_id: str,
        payload: ApplicationTransitionCreate,
        expected_application_version: int,
        idempotency_key: str,
    ) -> ApplicationTransitionResponse | None: ...

    def record_application_milestone_correction(
        self,
        *,
        owner_id: str,
        application_id: str,
        activity_event_id: str,
        payload: ApplicationMilestoneCorrectionCreate,
        expected_application_version: int,
        idempotency_key: str,
    ) -> ApplicationMilestoneCorrectionMutationResponse | None: ...


__all__ = ["ApplicationWorkspaceStore"]
