"""Concrete transaction-owning Postgres/SQLAlchemy owner workspace adapter."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from typing import Iterator

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .database import Database
from .evidence_repository import (
    create_approved_resume_evidence,
    create_achievement_evidence,
    list_achievement_evidence,
    update_achievement_evidence,
)
from .mutation_receipts import (
    MutationIdempotencyConflict,
    MutationPending,
    claim_owner_mutation,
    complete_owner_mutation,
)
from .owner_workspace import (
    WorkspaceConflict,
    WorkspaceInputError,
    WorkspaceNotFound,
    WorkspaceUnavailable,
)
from .private_payloads import PrivatePayloadBindingError
from .profile_repository import (
    CandidateProfileRecord,
    CareerTrackInput,
    CareerTrackRecord,
    ResumeVersionMetadata,
    create_career_track,
    create_or_reuse_resume_version,
    delete_career_track,
    list_career_tracks,
    list_resume_versions,
    load_candidate_profile,
    load_career_track,
    load_resume_version,
    set_base_resume_version,
    update_career_track,
    upsert_candidate_profile,
)
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
    HuntInput,
    ResumeVersionCreate,
    ResumeVersionDetail,
    ResumeVersionList,
    ResumeVersionSummary,
    ResumeUploadReport,
    SavedSearchCreate,
    SavedSearchHuntInputResponse,
    SavedSearchList,
    SavedSearchPatch,
    SavedSearchResponse,
)
from .resume_ingestion import ParsedResume
from .resume_import_repository import create_resume_import, load_resume_import
from .repository_errors import (
    ResourceConflict,
    ResourceInUse,
    VersionConflict,
)
from .saved_search_repository import (
    create_saved_search,
    delete_saved_search,
    list_saved_searches,
    load_saved_search,
    update_saved_search,
)
from .security import DataKeyring, DecryptionError


class SqlAlchemyOwnerWorkspaceStore:
    """Thin transport adapter; repository functions flush and this class commits."""

    def __init__(self, database: Database, keyring: DataKeyring) -> None:
        self.database = database
        self.keyring = keyring

    def get_profile(self, *, owner_id: str) -> CandidateProfileResponse | None:
        with _workspace_errors(), self.database.session() as session:
            record = load_candidate_profile(session, owner_id=owner_id, keyring=self.keyring)
            return self._profile_response(session, record) if record is not None else None

    def put_profile(
        self,
        *,
        owner_id: str,
        payload: CandidateProfileWrite,
        expected_version: int,
    ) -> CandidateProfileResponse:
        with _workspace_errors(), self.database.session() as session:
            if "skills" not in payload.model_fields_set:
                current = load_candidate_profile(
                    session,
                    owner_id=owner_id,
                    keyring=self.keyring,
                )
                if current is not None:
                    payload = CandidateProfileWrite.model_validate(
                        {
                            **payload.model_dump(mode="python"),
                            "skills": current.data.skills,
                        }
                    )
            record = upsert_candidate_profile(
                session,
                owner_id=owner_id,
                data=payload,
                keyring=self.keyring,
                expected_version=expected_version,
            )
            return self._profile_response(session, record)

    def list_resume_versions(self, *, owner_id: str) -> ResumeVersionList:
        with _workspace_errors(), self.database.session() as session:
            items = [
                self._resume_summary(session, row)
                for row in list_resume_versions(session, owner_id=owner_id)
            ]
            return ResumeVersionList(items=items)

    def create_resume_version(
        self,
        *,
        owner_id: str,
        payload: ResumeVersionCreate,
        idempotency_key: str,
    ) -> ResumeVersionDetail:
        with _workspace_errors(), self.database.session() as session:
            claim = claim_owner_mutation(
                session,
                owner_id=owner_id,
                namespace="resume_version.create",
                idempotency_key=idempotency_key,
                request=payload,
            )
            if claim.replay is not None:
                _require_replay_type(claim.replay.resource_type, "resume_version")
                detail = self._resume_detail(session, owner_id, claim.replay.resource_id)
                if detail is None:
                    raise WorkspaceUnavailable("idempotent resume result is unavailable")
                return detail
            created = create_or_reuse_resume_version(
                session,
                owner_id=owner_id,
                label=payload.label,
                content=payload.content,
                source=payload.source,
                keyring=self.keyring,
                parent_id=payload.parent_resume_version_id,
                make_base=payload.set_as_base,
            )
            complete_owner_mutation(
                session,
                owner_id=owner_id,
                receipt_id=claim.receipt_id,
                resource_type="resume_version",
                resource_id=created.resume.id,
                result_version=created.resume.version,
            )
            detail = self._resume_detail(session, owner_id, created.resume.id)
            assert detail is not None
            return detail

    def upload_resume_version(
        self,
        *,
        owner_id: str,
        parsed: ParsedResume,
        label: str,
        set_as_base: bool,
        idempotency_key: str,
    ) -> ResumeUploadReport:
        """Atomically retain normalized text and conservative resume-backed facts."""

        request = {
            # Idempotency receipts remain payload-free. The parser version binds
            # this digest to the deterministic extraction rules used here.
            "content_digest": hashlib.sha256(
                f"{owner_id}\0{parsed.content}".encode("utf-8")
            ).hexdigest(),
            "label": " ".join(label.split()),
            "media_type": parsed.media_type,
            "page_count": parsed.page_count,
            "parser_version": parsed.parser_version,
            "set_as_base": set_as_base,
        }
        with _workspace_errors(), self.database.session() as session:
            claim = claim_owner_mutation(
                session,
                owner_id=owner_id,
                namespace="resume_version.upload",
                idempotency_key=idempotency_key,
                request=request,
            )
            if claim.replay is not None:
                _require_replay_type(claim.replay.resource_type, "resume_import")
                imported = load_resume_import(
                    session,
                    owner_id=owner_id,
                    resume_import_id=claim.replay.resource_id,
                    keyring=self.keyring,
                )
                if imported is None:
                    raise WorkspaceUnavailable("idempotent resume upload is unavailable")
                if claim.replay.result_version != imported.report.resume_version.version:
                    raise WorkspaceUnavailable(
                        "idempotent resume upload version is inconsistent"
                    )
                return imported.report

            created = create_or_reuse_resume_version(
                session,
                owner_id=owner_id,
                label=label,
                content=parsed.content,
                source="uploaded",
                keyring=self.keyring,
                make_base=set_as_base,
            )
            report = self._resume_upload_report(
                session,
                owner_id=owner_id,
                metadata=created.resume,
                parsed=parsed,
            )
            imported = create_resume_import(
                session,
                owner_id=owner_id,
                resume_version_id=created.resume.id,
                parser_version=parsed.parser_version,
                media_type=parsed.media_type,
                page_count=parsed.page_count,
                report=report,
                keyring=self.keyring,
            )
            complete_owner_mutation(
                session,
                owner_id=owner_id,
                receipt_id=claim.receipt_id,
                resource_type="resume_import",
                resource_id=imported.id,
                result_version=report.resume_version.version,
            )
            return report

    def get_resume_version(
        self, *, owner_id: str, resume_version_id: str
    ) -> ResumeVersionDetail | None:
        with _workspace_errors(), self.database.session() as session:
            return self._resume_detail(session, owner_id, resume_version_id)

    def set_base_resume(
        self,
        *,
        owner_id: str,
        resume_version_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> ResumeVersionSummary:
        request = {"resume_version_id": resume_version_id, "expected_version": expected_version}
        with _workspace_errors(), self.database.session() as session:
            claim = claim_owner_mutation(
                session,
                owner_id=owner_id,
                namespace="resume_version.set_base",
                idempotency_key=idempotency_key,
                request=request,
            )
            if claim.replay is not None:
                _require_replay_type(claim.replay.resource_type, "resume_version")
                detail = self._resume_detail(session, owner_id, claim.replay.resource_id)
                if detail is None:
                    raise WorkspaceUnavailable("idempotent base-resume result is unavailable")
                return ResumeVersionSummary.model_validate(detail.model_dump())
            metadata = set_base_resume_version(
                session,
                owner_id=owner_id,
                resume_version_id=resume_version_id,
                expected_version=expected_version,
            )
            if metadata is None:
                raise WorkspaceNotFound("resume version not found")
            complete_owner_mutation(
                session,
                owner_id=owner_id,
                receipt_id=claim.receipt_id,
                resource_type="resume_version",
                resource_id=metadata.id,
                result_version=metadata.version,
            )
            return self._resume_summary(session, metadata)

    def list_evidence(
        self, *, owner_id: str, approval_state: EvidenceApprovalState | None
    ) -> AchievementEvidenceList:
        with _workspace_errors(), self.database.session() as session:
            items = list_achievement_evidence(
                session,
                owner_id=owner_id,
                keyring=self.keyring,
                approval_state=approval_state,
            )
            return AchievementEvidenceList(items=items)

    def create_evidence(
        self,
        *,
        owner_id: str,
        payload: AchievementEvidenceCreate,
        idempotency_key: str,
    ) -> AchievementEvidenceResponse:
        with _workspace_errors(), self.database.session() as session:
            claim = claim_owner_mutation(
                session,
                owner_id=owner_id,
                namespace="achievement_evidence.create",
                idempotency_key=idempotency_key,
                request=payload,
            )
            if claim.replay is not None:
                _require_replay_type(claim.replay.resource_type, "achievement_evidence")
                items = list_achievement_evidence(
                    session, owner_id=owner_id, keyring=self.keyring
                )
                match = next((item for item in items if item.id == claim.replay.resource_id), None)
                if match is None:
                    raise WorkspaceUnavailable("idempotent evidence result is unavailable")
                return match
            record = create_achievement_evidence(
                session,
                owner_id=owner_id,
                payload=payload,
                keyring=self.keyring,
            )
            complete_owner_mutation(
                session,
                owner_id=owner_id,
                receipt_id=claim.receipt_id,
                resource_type="achievement_evidence",
                resource_id=record.id,
                result_version=record.version,
            )
            return record

    def patch_evidence(
        self,
        *,
        owner_id: str,
        evidence_id: str,
        payload: AchievementEvidencePatch,
        expected_version: int,
    ) -> AchievementEvidenceResponse:
        if "statement" in payload.model_fields_set and payload.statement is None:
            raise WorkspaceInputError("statement cannot be null", field="statement")
        if "skills" in payload.model_fields_set and payload.skills is None:
            raise WorkspaceInputError("skills cannot be null", field="skills")
        with _workspace_errors(), self.database.session() as session:
            record = update_achievement_evidence(
                session,
                owner_id=owner_id,
                evidence_id=evidence_id,
                patch=payload,
                expected_version=expected_version,
                keyring=self.keyring,
            )
            if record is None:
                raise WorkspaceNotFound("achievement evidence not found")
            return record

    def list_career_tracks(self, *, owner_id: str) -> CareerTrackList:
        with _workspace_errors(), self.database.session() as session:
            return CareerTrackList(
                items=[_career_response(row) for row in list_career_tracks(session, owner_id=owner_id)]
            )

    def create_career_track(
        self,
        *,
        owner_id: str,
        payload: CareerTrackCreate,
        idempotency_key: str,
    ) -> CareerTrackResponse:
        with _workspace_errors(), self.database.session() as session:
            claim = claim_owner_mutation(
                session,
                owner_id=owner_id,
                namespace="career_track.create",
                idempotency_key=idempotency_key,
                request=payload,
            )
            if claim.replay is not None:
                _require_replay_type(claim.replay.resource_type, "career_track")
                record = load_career_track(
                    session,
                    owner_id=owner_id,
                    career_track_id=claim.replay.resource_id,
                )
                if record is None:
                    raise WorkspaceUnavailable("idempotent career-track result is unavailable")
                return _career_response(record)
            record = create_career_track(session, owner_id=owner_id, data=payload)
            complete_owner_mutation(
                session,
                owner_id=owner_id,
                receipt_id=claim.receipt_id,
                resource_type="career_track",
                resource_id=record.id,
                result_version=record.version,
            )
            return _career_response(record)

    def get_career_track(
        self, *, owner_id: str, career_track_id: str
    ) -> CareerTrackResponse | None:
        with _workspace_errors(), self.database.session() as session:
            record = load_career_track(
                session, owner_id=owner_id, career_track_id=career_track_id
            )
            return _career_response(record) if record is not None else None

    def patch_career_track(
        self,
        *,
        owner_id: str,
        career_track_id: str,
        payload: CareerTrackPatch,
        expected_version: int,
    ) -> CareerTrackResponse:
        with _workspace_errors(), self.database.session() as session:
            current = load_career_track(
                session, owner_id=owner_id, career_track_id=career_track_id
            )
            if current is None:
                raise WorkspaceNotFound("career track not found")
            merged = CareerTrackInput.model_validate(
                {
                    **current.data.model_dump(mode="json"),
                    **payload.model_dump(mode="json", exclude_unset=True),
                }
            )
            updated = update_career_track(
                session,
                owner_id=owner_id,
                career_track_id=career_track_id,
                data=merged,
                expected_version=expected_version,
            )
            assert updated is not None
            return _career_response(updated)

    def delete_career_track(
        self, *, owner_id: str, career_track_id: str, expected_version: int
    ) -> None:
        with _workspace_errors(), self.database.session() as session:
            deleted = delete_career_track(
                session,
                owner_id=owner_id,
                career_track_id=career_track_id,
                expected_version=expected_version,
            )
            if not deleted:
                raise WorkspaceNotFound("career track not found")

    def list_saved_searches(self, *, owner_id: str) -> SavedSearchList:
        with _workspace_errors(), self.database.session() as session:
            return SavedSearchList(items=list_saved_searches(session, owner_id=owner_id))

    def create_saved_search(
        self,
        *,
        owner_id: str,
        payload: SavedSearchCreate,
        idempotency_key: str,
    ) -> SavedSearchResponse:
        with _workspace_errors(), self.database.session() as session:
            claim = claim_owner_mutation(
                session,
                owner_id=owner_id,
                namespace="saved_search.create",
                idempotency_key=idempotency_key,
                request=payload,
            )
            if claim.replay is not None:
                _require_replay_type(claim.replay.resource_type, "saved_search")
                result = load_saved_search(
                    session, owner_id=owner_id, saved_search_id=claim.replay.resource_id
                )
                if result is None:
                    raise WorkspaceUnavailable("idempotent saved-search result is unavailable")
                return result
            result = create_saved_search(session, owner_id=owner_id, payload=payload)
            complete_owner_mutation(
                session,
                owner_id=owner_id,
                receipt_id=claim.receipt_id,
                resource_type="saved_search",
                resource_id=result.id,
                result_version=result.version,
            )
            return result

    def get_saved_search(
        self, *, owner_id: str, saved_search_id: str
    ) -> SavedSearchResponse | None:
        with _workspace_errors(), self.database.session() as session:
            return load_saved_search(
                session, owner_id=owner_id, saved_search_id=saved_search_id
            )

    def patch_saved_search(
        self,
        *,
        owner_id: str,
        saved_search_id: str,
        payload: SavedSearchPatch,
        expected_version: int,
    ) -> SavedSearchResponse:
        with _workspace_errors(), self.database.session() as session:
            current = load_saved_search(
                session, owner_id=owner_id, saved_search_id=saved_search_id
            )
            if current is None:
                raise WorkspaceNotFound("saved search not found")
            base = current.model_dump(
                mode="json",
                include={
                    "name", "career_track_id", "resume_version_id", "criteria",
                    "schedule", "pack", "use_self_rag", "active",
                },
            )
            merged = SavedSearchCreate.model_validate(
                {**base, **payload.model_dump(mode="json", exclude_unset=True)}
            )
            updated = update_saved_search(
                session,
                owner_id=owner_id,
                saved_search_id=saved_search_id,
                payload=merged,
                expected_version=expected_version,
                reschedule=bool({"schedule", "active"} & payload.model_fields_set),
            )
            assert updated is not None
            return updated

    def delete_saved_search(
        self, *, owner_id: str, saved_search_id: str, expected_version: int
    ) -> None:
        with _workspace_errors(), self.database.session() as session:
            deleted = delete_saved_search(
                session,
                owner_id=owner_id,
                saved_search_id=saved_search_id,
                expected_version=expected_version,
            )
            if not deleted:
                raise WorkspaceNotFound("saved search not found")

    def build_hunt_input(
        self, *, owner_id: str, saved_search_id: str
    ) -> SavedSearchHuntInputResponse | None:
        with _workspace_errors(), self.database.session() as session:
            search = load_saved_search(
                session, owner_id=owner_id, saved_search_id=saved_search_id
            )
            if search is None:
                return None
            track = load_career_track(
                session,
                owner_id=owner_id,
                career_track_id=search.career_track_id,
            )
            profile = load_candidate_profile(
                session, owner_id=owner_id, keyring=self.keyring
            )
            resume_detail = (
                self._resume_detail(session, owner_id, search.resume_version_id)
                if search.resume_version_id is not None
                else None
            )
            blockers: list[str] = []
            if profile is None:
                blockers.append("profile_missing")
            if resume_detail is None:
                blockers.append("selected_resume_missing")
            if track is None or not track.data.active:
                blockers.append("career_track_inactive")
            if not search.active:
                blockers.append("saved_search_inactive")
            ready = not blockers
            hunt_input = (
                HuntInput(
                    resume_text=resume_detail.content,
                    criteria=search.criteria,
                    pack=search.pack,
                    use_self_rag=search.use_self_rag,
                    provider_consent_required=True,
                )
                if ready and resume_detail is not None
                else None
            )
            summary = (
                ResumeVersionSummary.model_validate(
                    resume_detail.model_dump(exclude={"content"})
                )
                if resume_detail is not None
                else None
            )
            return SavedSearchHuntInputResponse(
                saved_search_id=search.id,
                saved_search_version=search.version,
                career_track_id=search.career_track_id,
                career_track_version=track.version if track is not None else 1,
                resume=summary,
                ready=ready,
                blockers=blockers,
                warnings=[],
                input=hunt_input,
            )

    def _profile_response(
        self, session, record: CandidateProfileRecord
    ) -> CandidateProfileResponse:
        base = next(
            (resume for resume in list_resume_versions(session, owner_id=record.owner_id) if resume.is_base),
            None,
        )
        return CandidateProfileResponse(
            **record.data.model_dump(),
            id=record.id,
            base_resume=self._resume_summary(session, base) if base is not None else None,
            version=record.version,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def _resume_summary(self, session, metadata: ResumeVersionMetadata) -> ResumeVersionSummary:
        detail = load_resume_version(
            session,
            owner_id=metadata.owner_id,
            resume_version_id=metadata.id,
            keyring=self.keyring,
        )
        if detail is None:
            raise WorkspaceUnavailable("resume content is unavailable")
        return ResumeVersionSummary(
            id=metadata.id,
            label=metadata.label,
            source=metadata.source,
            parent_resume_version_id=metadata.parent_id,
            is_base=metadata.is_base,
            character_count=len(detail.content),
            version=metadata.version,
            created_at=metadata.created_at,
            updated_at=metadata.updated_at,
        )

    def _resume_detail(
        self, session, owner_id: str, resume_version_id: str
    ) -> ResumeVersionDetail | None:
        detail = load_resume_version(
            session,
            owner_id=owner_id,
            resume_version_id=resume_version_id,
            keyring=self.keyring,
        )
        if detail is None:
            return None
        return ResumeVersionDetail(
            **self._resume_summary(session, detail.metadata).model_dump(),
            content=detail.content,
        )

    def _resume_upload_report(
        self,
        session,
        *,
        owner_id: str,
        metadata: ResumeVersionMetadata,
        parsed: ParsedResume,
    ) -> ResumeUploadReport:
        imported_fields, missing_fields, profile_warnings = self._merge_resume_profile(
            session,
            owner_id=owner_id,
            parsed=parsed,
            apply_changes=True,
        )
        achievement_count = self._ensure_resume_evidence(
            session,
            owner_id=owner_id,
            resume_version_id=metadata.id,
            parsed=parsed,
            apply_changes=True,
        )
        return ResumeUploadReport(
            resume_version=self._resume_summary(session, metadata),
            imported_profile_fields=imported_fields,
            achievement_suggestions_created=achievement_count,
            missing_profile_fields=missing_fields,
            warnings=_unique_strings([*parsed.warnings, *profile_warnings]),
            parsed_sections=list(parsed.sections),
        )

    def _merge_resume_profile(
        self,
        session,
        *,
        owner_id: str,
        parsed: ParsedResume,
        apply_changes: bool,
    ) -> tuple[list[str], list[str], list[str]]:
        current = load_candidate_profile(session, owner_id=owner_id, keyring=self.keyring)
        candidate_values: dict[str, str | float | None] = {
            "current_title": parsed.current_title,
            "current_location": parsed.current_location,
            "years_of_experience": parsed.years_of_experience,
        }
        merged = current.data.model_dump(mode="python") if current is not None else {}
        imported: list[str] = []
        warnings: list[str] = []
        changed = False
        for field, candidate in candidate_values.items():
            if candidate is None:
                continue
            existing = merged.get(field)
            if existing is None:
                merged[field] = candidate
                imported.append(field)
                changed = True
            elif existing == candidate:
                imported.append(field)
            else:
                warnings.append(
                    f"Your existing {_profile_field_label(field)} was kept because it differs from the resume."
                )

        candidate_skills = list(parsed.skills)
        if candidate_skills:
            existing_skills = list(merged.get("skills") or [])
            if not existing_skills:
                merged["skills"] = candidate_skills
                imported.append("skills")
                changed = True
            elif _same_skill_list(existing_skills, candidate_skills):
                imported.append("skills")
            else:
                warnings.append(
                    "Your existing skills were kept because they differ from the resume."
                )

        if current is None:
            merged.setdefault("onboarding_step", "career_track")
        elif current.data.onboarding_step in {"profile", "resume"}:
            merged["onboarding_step"] = "career_track"
            changed = True

        if apply_changes and (current is None or changed) and imported:
            profile_payload = CandidateProfileWrite.model_validate(merged)
            upsert_candidate_profile(
                session,
                owner_id=owner_id,
                data=profile_payload,
                keyring=self.keyring,
                expected_version=current.version if current is not None else 0,
            )
            current = load_candidate_profile(
                session,
                owner_id=owner_id,
                keyring=self.keyring,
            )

        final_values = current.data if current is not None else None
        if not apply_changes:
            # A replay reports the fields represented by the completed import
            # when they are still present, without mutating newer profile edits.
            imported = [
                field
                for field, candidate in candidate_values.items()
                if candidate is not None
                and final_values is not None
                and getattr(final_values, field) == candidate
            ]
            if (
                candidate_skills
                and final_values is not None
                and _same_skill_list(final_values.skills, candidate_skills)
            ):
                imported.append("skills")
        missing = [
            field
            for field in candidate_values
            if final_values is None or getattr(final_values, field) is None
        ]
        if final_values is None or not final_values.skills:
            missing.append("skills")
        return imported, missing, warnings

    def _ensure_resume_evidence(
        self,
        session,
        *,
        owner_id: str,
        resume_version_id: str,
        parsed: ParsedResume,
        apply_changes: bool,
    ) -> int:
        items = list_achievement_evidence(
            session,
            owner_id=owner_id,
            keyring=self.keyring,
        )
        by_excerpt = {
            item.source_excerpt: item
            for item in items
            if item.source_resume_version_id == resume_version_id
            and item.origin == "resume_suggestion"
            and item.source_excerpt is not None
        }
        approved = 0
        for suggestion in parsed.evidence:
            existing = by_excerpt.get(suggestion.source_excerpt)
            if existing is None and apply_changes:
                existing = create_approved_resume_evidence(
                    session,
                    owner_id=owner_id,
                    payload=AchievementEvidenceCreate(
                        statement=suggestion.statement,
                        source_resume_version_id=resume_version_id,
                        source_excerpt=suggestion.source_excerpt,
                        skills=list(suggestion.skills),
                        origin="resume_suggestion",
                    ),
                    keyring=self.keyring,
                )
                by_excerpt[suggestion.source_excerpt] = existing
            elif (
                existing is not None
                and apply_changes
                and existing.origin == "resume_suggestion"
                and existing.statement == suggestion.statement
                and existing.approval_state == "pending"
            ):
                existing = update_achievement_evidence(
                    session,
                    owner_id=owner_id,
                    evidence_id=existing.id,
                    patch=AchievementEvidencePatch(approval_state="approved"),
                    expected_version=existing.version,
                    keyring=self.keyring,
                )
                assert existing is not None
                by_excerpt[suggestion.source_excerpt] = existing
            if (
                existing is not None
                and existing.origin == "resume_suggestion"
                and existing.statement == suggestion.statement
                and existing.approval_state == "approved"
            ):
                approved += 1
        return approved


def _career_response(record: CareerTrackRecord) -> CareerTrackResponse:
    return CareerTrackResponse(
        **record.data.model_dump(),
        id=record.id,
        version=record.version,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _profile_field_label(field: str) -> str:
    return {
        "current_title": "current title",
        "current_location": "home location",
        "years_of_experience": "experience estimate",
    }.get(field, field.replace("_", " "))


def _same_skill_list(first: list[str], second: list[str]) -> bool:
    return [value.casefold() for value in first] == [
        value.casefold() for value in second
    ]


def _unique_strings(values: list[str] | tuple[str, ...]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(value.split())
        key = normalized.casefold()
        if normalized and key not in seen:
            unique.append(normalized)
            seen.add(key)
    return unique


def _require_replay_type(actual: str, expected: str) -> None:
    if actual != expected:
        raise WorkspaceUnavailable("idempotent mutation resource type is inconsistent")


@contextmanager
def _workspace_errors() -> Iterator[None]:
    try:
        yield
    except WorkspaceNotFound:
        raise
    except WorkspaceConflict:
        raise
    except WorkspaceInputError:
        raise
    except WorkspaceUnavailable:
        raise
    except VersionConflict as exc:
        raise WorkspaceConflict(str(exc), code="version_conflict") from exc
    except MutationIdempotencyConflict as exc:
        raise WorkspaceConflict(str(exc), code="idempotency_conflict") from exc
    except ResourceInUse as exc:
        raise WorkspaceConflict(str(exc), code="resource_in_use") from exc
    except MutationPending as exc:
        raise WorkspaceConflict(str(exc), code="mutation_pending") from exc
    except ResourceConflict as exc:
        raise WorkspaceConflict(str(exc)) from exc
    except (PrivatePayloadBindingError, DecryptionError) as exc:
        raise WorkspaceUnavailable("private workspace data could not be decrypted") from exc
    except ValidationError as exc:
        raise WorkspaceUnavailable("stored workspace data failed contract validation") from exc
    except ValueError as exc:
        raise WorkspaceInputError(str(exc)) from exc
    except IntegrityError as exc:
        raise WorkspaceConflict("workspace resource conflicts with existing state") from exc
    except SQLAlchemyError as exc:
        raise WorkspaceUnavailable("owner workspace database is unavailable") from exc


__all__ = ["SqlAlchemyOwnerWorkspaceStore"]
