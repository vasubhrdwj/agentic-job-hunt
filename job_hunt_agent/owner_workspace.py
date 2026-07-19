"""Narrow data boundary for the owner onboarding/search API.

The concrete Postgres adapter owns transaction boundaries, encryption, owner
predicates, optimistic-version checks, and create-operation idempotency. The
router depends only on this protocol so transport work does not leak ORM state.
"""

from __future__ import annotations

from typing import Protocol

from .profile_schemas import (
    AchievementEvidenceCreate,
    AchievementEvidenceList,
    AchievementEvidencePatch,
    AchievementEvidenceResponse,
    CandidateProfileResponse,
    CandidateProfileWrite,
    CareerTrackCreate,
    CareerTrackList,
    CareerTrackPatch,
    CareerTrackResponse,
    EvidenceApprovalState,
    ResumeVersionCreate,
    ResumeVersionDetail,
    ResumeVersionList,
    ResumeVersionSummary,
    SavedSearchCreate,
    SavedSearchHuntInputResponse,
    SavedSearchList,
    SavedSearchPatch,
    SavedSearchResponse,
)


class OwnerWorkspaceError(RuntimeError):
    """Base class for safe, expected owner-workspace failures."""


class WorkspaceNotFound(OwnerWorkspaceError):
    pass


class WorkspaceConflict(OwnerWorkspaceError):
    def __init__(self, message: str, *, code: str = "resource_conflict") -> None:
        super().__init__(message)
        self.code = code


class WorkspaceInputError(OwnerWorkspaceError):
    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        code: str = "invalid_request",
    ) -> None:
        super().__init__(message)
        self.field = field
        self.code = code


class WorkspaceUnavailable(OwnerWorkspaceError):
    pass


class WorkspaceCapabilityUnavailable(WorkspaceUnavailable):
    def __init__(self, capability: str, *, reason: str) -> None:
        super().__init__(f"{capability} is unavailable")
        self.capability = capability
        self.reason = reason


class OwnerWorkspaceStore(Protocol):
    """Owner-scoped operations required by the first usable onboarding slice."""

    def get_profile(self, *, owner_id: str) -> CandidateProfileResponse | None: ...

    def put_profile(
        self,
        *,
        owner_id: str,
        payload: CandidateProfileWrite,
        expected_version: int,
    ) -> CandidateProfileResponse: ...

    def list_resume_versions(self, *, owner_id: str) -> ResumeVersionList: ...

    def create_resume_version(
        self,
        *,
        owner_id: str,
        payload: ResumeVersionCreate,
        idempotency_key: str,
    ) -> ResumeVersionDetail: ...

    def get_resume_version(
        self,
        *,
        owner_id: str,
        resume_version_id: str,
    ) -> ResumeVersionDetail | None: ...

    def set_base_resume(
        self,
        *,
        owner_id: str,
        resume_version_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> ResumeVersionSummary: ...

    def list_evidence(
        self,
        *,
        owner_id: str,
        approval_state: EvidenceApprovalState | None,
    ) -> AchievementEvidenceList: ...

    def create_evidence(
        self,
        *,
        owner_id: str,
        payload: AchievementEvidenceCreate,
        idempotency_key: str,
    ) -> AchievementEvidenceResponse: ...

    def patch_evidence(
        self,
        *,
        owner_id: str,
        evidence_id: str,
        payload: AchievementEvidencePatch,
        expected_version: int,
    ) -> AchievementEvidenceResponse: ...

    def list_career_tracks(self, *, owner_id: str) -> CareerTrackList: ...

    def create_career_track(
        self,
        *,
        owner_id: str,
        payload: CareerTrackCreate,
        idempotency_key: str,
    ) -> CareerTrackResponse: ...

    def get_career_track(
        self,
        *,
        owner_id: str,
        career_track_id: str,
    ) -> CareerTrackResponse | None: ...

    def patch_career_track(
        self,
        *,
        owner_id: str,
        career_track_id: str,
        payload: CareerTrackPatch,
        expected_version: int,
    ) -> CareerTrackResponse: ...

    def delete_career_track(
        self,
        *,
        owner_id: str,
        career_track_id: str,
        expected_version: int,
    ) -> None: ...

    def list_saved_searches(self, *, owner_id: str) -> SavedSearchList: ...

    def create_saved_search(
        self,
        *,
        owner_id: str,
        payload: SavedSearchCreate,
        idempotency_key: str,
    ) -> SavedSearchResponse: ...

    def get_saved_search(
        self,
        *,
        owner_id: str,
        saved_search_id: str,
    ) -> SavedSearchResponse | None: ...

    def patch_saved_search(
        self,
        *,
        owner_id: str,
        saved_search_id: str,
        payload: SavedSearchPatch,
        expected_version: int,
    ) -> SavedSearchResponse: ...

    def delete_saved_search(
        self,
        *,
        owner_id: str,
        saved_search_id: str,
        expected_version: int,
    ) -> None: ...

    def build_hunt_input(
        self,
        *,
        owner_id: str,
        saved_search_id: str,
    ) -> SavedSearchHuntInputResponse | None: ...


__all__ = [
    "OwnerWorkspaceError",
    "OwnerWorkspaceStore",
    "WorkspaceConflict",
    "WorkspaceCapabilityUnavailable",
    "WorkspaceInputError",
    "WorkspaceNotFound",
    "WorkspaceUnavailable",
]
