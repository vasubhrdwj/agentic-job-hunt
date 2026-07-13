"""Transaction-owning SQLAlchemy adapter for the opportunity radar."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .database import Database
from .job_queue import enqueue_job, utcnow
from .models import (
    OpportunityScan,
    OpportunityScanSource,
    OwnerMutationReceipt,
    SavedSearch,
)
from .mutation_receipts import (
    MutationIdempotencyConflict,
    MutationPending,
    claim_owner_mutation,
    complete_owner_mutation,
)
from .opportunity_schemas import (
    OpportunityDecisionRequest,
    OpportunityDecisionResponse,
    OpportunityDetailResponse,
    ScanCounts,
    ScanCreateRequest,
    ScanCreateResponse,
    ScanStage,
    ScanStatusResponse,
    ScanWarning,
    ScanWarningCode,
    ScanWarningScope,
    TodayListResponse,
    TodayQuery,
)
from .opportunity_repository import (
    DecisionIdempotencyConflict,
    OpportunityNotFound,
    OpportunityRepositoryError,
    PostingIdentityConflict,
    decide_owner_opportunity,
    list_today_opportunities,
    load_opportunity_detail,
)
from .owner_workspace import (
    WorkspaceConflict,
    WorkspaceInputError,
    WorkspaceNotFound,
    WorkspaceUnavailable,
)
from .private_payloads import PrivatePayloadBindingError
from .profile_schemas import SavedSearchCriteria
from .repository_errors import ResourceConflict, VersionConflict, require_version
from .security import DataKeyring, DecryptionError
from .sources.registry import RegistryError, load_company_pack


_WARNING_MESSAGES: dict[ScanWarningCode, tuple[str, bool]] = {
    ScanWarningCode.source_timeout: (
        "This source timed out; previously saved opportunities were preserved.",
        True,
    ),
    ScanWarningCode.source_unavailable: (
        "This source was unavailable; previously saved opportunities were preserved.",
        True,
    ),
    ScanWarningCode.source_invalid_response: (
        "This source returned an invalid response; previously saved opportunities were preserved.",
        False,
    ),
    ScanWarningCode.source_incomplete: (
        "This source result was criteria-filtered or incomplete.",
        False,
    ),
    ScanWarningCode.source_rate_limited: (
        "This source rate-limited the scan; previously saved opportunities were preserved.",
        True,
    ),
    ScanWarningCode.source_fallback_used: (
        "A fallback source was used for this company.",
        False,
    ),
    ScanWarningCode.scan_interrupted: (
        "The scan was interrupted; previously saved opportunities were preserved.",
        True,
    ),
    ScanWarningCode.scan_retrying: (
        "The scan is retrying an incomplete source operation.",
        True,
    ),
}

_STORED_WARNING_CODES: dict[str, ScanWarningCode] = {
    code.value: code for code in ScanWarningCode
}
_STORED_WARNING_CODES.update(
    {
        "source_fetch_failed": ScanWarningCode.source_unavailable,
        "fallback_source_fetch_failed": ScanWarningCode.source_unavailable,
        "source_http_error": ScanWarningCode.source_unavailable,
        "source_timeout_error": ScanWarningCode.source_timeout,
        "source_parse_failed": ScanWarningCode.source_invalid_response,
        "fallback_used": ScanWarningCode.source_fallback_used,
        "untrusted_url_skipped": ScanWarningCode.source_invalid_response,
        "untrusted_apply_url_skipped": ScanWarningCode.source_invalid_response,
        "source_invalid_record": ScanWarningCode.source_invalid_response,
        "source_configuration_changed": ScanWarningCode.source_unavailable,
        "scan_processing_failed": ScanWarningCode.scan_interrupted,
    }
)


class SqlAlchemyOpportunityWorkspaceStore:
    """Owner-scoped opportunity adapter with one transaction per API operation."""

    def __init__(self, database: Database, keyring: DataKeyring) -> None:
        self.database = database
        self.keyring = keyring

    def create_scan(
        self,
        *,
        owner_id: str,
        saved_search_id: str,
        expected_saved_search_version: int,
        idempotency_key: str,
        payload: ScanCreateRequest,
    ) -> ScanCreateResponse:
        request = {
            "saved_search_id": saved_search_id,
            "expected_saved_search_version": expected_saved_search_version,
            "payload": payload.model_dump(mode="json"),
        }
        with _opportunity_errors(), self.database.session() as session:
            namespace = "opportunity_scan.create"
            key_hash = _sha256(idempotency_key.strip())
            claim = None
            existing_receipt = session.scalar(
                select(OwnerMutationReceipt.id).where(
                    OwnerMutationReceipt.owner_id == owner_id,
                    OwnerMutationReceipt.namespace == namespace,
                    OwnerMutationReceipt.idempotency_key_hash == key_hash,
                )
            )
            if existing_receipt is not None:
                claim = claim_owner_mutation(
                    session,
                    owner_id=owner_id,
                    namespace=namespace,
                    idempotency_key=idempotency_key,
                    request=request,
                )
                if claim.replay is not None:
                    return self._scan_replay_response(
                        session,
                        owner_id=owner_id,
                        resource_type=claim.replay.resource_type,
                        scan_id=claim.replay.resource_id,
                    )

            search = session.scalar(
                select(SavedSearch)
                .where(
                    SavedSearch.owner_id == owner_id,
                    SavedSearch.id == saved_search_id,
                )
                .with_for_update()
            )
            if search is None:
                raise WorkspaceNotFound("saved search not found")
            require_version(
                "saved_search",
                search.id,
                expected=expected_saved_search_version,
                actual=search.version,
            )
            if not search.active:
                raise WorkspaceConflict(
                    "saved search is inactive", code="inactive_saved_search"
                )

            criteria = SavedSearchCriteria.model_validate(search.criteria)
            companies = _active_pack_companies(search.pack)
            if claim is None:
                # Validate owner/search/config before inserting a pending
                # receipt. This avoids stranding a key when preconditions fail,
                # while the read-first path above preserves exact replay and
                # changed-request conflict semantics for an existing key.
                claim = claim_owner_mutation(
                    session,
                    owner_id=owner_id,
                    namespace=namespace,
                    idempotency_key=idempotency_key,
                    request=request,
                )
                if claim.replay is not None:
                    return self._scan_replay_response(
                        session,
                        owner_id=owner_id,
                        resource_type=claim.replay.resource_type,
                        scan_id=claim.replay.resource_id,
                    )
            current = utcnow()
            scan_id = uuid4().hex
            scan = OpportunityScan(
                id=scan_id,
                owner_id=owner_id,
                saved_search_id=search.id,
                saved_search_version=search.version,
                criteria_schema_version=search.criteria_schema_version,
                criteria_snapshot=criteria.model_dump(mode="json"),
                pack_snapshot=search.pack,
                trigger=payload.trigger.value,
                scheduled_for=current,
                dedupe_key=f"manual:{key_hash}",
                idempotency_key_hash=key_hash,
                request_hash=_request_hash(request),
                status="queued",
                stage="queued",
                source_count=len(companies),
                terminal_source_count=0,
                successful_source_count=0,
                failed_source_count=0,
                observed_count=0,
                new_posting_count=0,
                changed_posting_count=0,
                new_opportunity_count=0,
                version=1,
                created_at=current,
                updated_at=current,
            )
            session.add(scan)
            session.flush()
            session.add_all(
                [
                    OpportunityScanSource(
                        owner_id=owner_id,
                        opportunity_scan_id=scan.id,
                        company_slug=company.slug,
                        source=company.source.value,
                        status="pending",
                        fetch_scope="criteria_filtered",
                        completeness="unknown",
                        observed_count=0,
                        returned_count=0,
                        persisted_count=0,
                        warning_codes=[],
                        used_fallback=False,
                        cache_hit=False,
                        version=1,
                        created_at=current,
                        updated_at=current,
                    )
                    for company in companies
                ]
            )
            session.flush()

            queued = enqueue_job(
                session,
                kind="scan_saved_search",
                dedupe_key=f"scan:{scan.id}",
                owner_id=owner_id,
                subject_type="opportunity_scan",
                subject_id=scan.id,
                payload={
                    "opportunity_scan_id": scan.id,
                    "saved_search_id": search.id,
                    "saved_search_version": search.version,
                },
                run_after=current,
                actor="owner",
            )
            if queued.job.subject_id != scan.id:
                raise WorkspaceUnavailable("scan queue identity is inconsistent")
            scan.background_job_id = queued.job.id
            scan.updated_at = current
            session.flush()
            complete_owner_mutation(
                session,
                owner_id=owner_id,
                receipt_id=claim.receipt_id,
                resource_type="opportunity_scan",
                resource_id=scan.id,
                result_version=scan.version,
                now=current,
            )
            response = self._scan_response(
                session, owner_id=owner_id, scan_id=scan.id
            )
            assert response is not None
            return ScanCreateResponse.model_validate(response.model_dump())

    def get_scan(
        self,
        *,
        owner_id: str,
        scan_id: str,
    ) -> ScanStatusResponse | None:
        with _opportunity_errors(), self.database.session() as session:
            return self._scan_response(session, owner_id=owner_id, scan_id=scan_id)

    def list_today(
        self,
        *,
        owner_id: str,
        query: TodayQuery,
    ) -> TodayListResponse:
        with _opportunity_errors(), self.database.session() as session:
            return list_today_opportunities(
                session,
                owner_id=owner_id,
                query=query,
                keyring=self.keyring,
            )

    def get_opportunity(
        self,
        *,
        owner_id: str,
        opportunity_id: str,
    ) -> OpportunityDetailResponse | None:
        with _opportunity_errors(), self.database.session() as session:
            return load_opportunity_detail(
                session,
                owner_id=owner_id,
                opportunity_id=opportunity_id,
                keyring=self.keyring,
            )

    def decide_opportunity(
        self,
        *,
        owner_id: str,
        opportunity_id: str,
        expected_version: int,
        idempotency_key: str,
        payload: OpportunityDecisionRequest,
    ) -> OpportunityDecisionResponse:
        with _opportunity_errors(), self.database.session() as session:
            return decide_owner_opportunity(
                session,
                owner_id=owner_id,
                opportunity_id=opportunity_id,
                request=payload,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
                keyring=self.keyring,
            )

    def _scan_response(
        self,
        session,
        *,
        owner_id: str,
        scan_id: str,
    ) -> ScanStatusResponse | None:
        scan = session.scalar(
            select(OpportunityScan).where(
                OpportunityScan.owner_id == owner_id,
                OpportunityScan.id == scan_id,
            )
        )
        if scan is None:
            return None
        sources = list(
            session.scalars(
                select(OpportunityScanSource)
                .where(
                    OpportunityScanSource.owner_id == owner_id,
                    OpportunityScanSource.opportunity_scan_id == scan.id,
                )
                .order_by(
                    OpportunityScanSource.company_slug,
                    OpportunityScanSource.source,
                    OpportunityScanSource.id,
                )
            )
        )
        warnings = _scan_warnings(scan, sources)
        terminal = [
            source
            for source in sources
            if source.status in {"succeeded", "failed", "cancelled"}
        ]
        degraded = [source for source in terminal if _source_is_degraded(source)]
        succeeded = [
            source
            for source in terminal
            if source.status == "succeeded" and source not in degraded
        ]
        failed = [
            source for source in terminal if source.status in {"failed", "cancelled"}
        ]
        # A scan persists only roles that matched its pinned criteria.  The
        # cumulative SavedSearchMatch edge later moves to newer scans, so it
        # cannot truthfully reconstruct a historical scan's matched count.
        matched = scan.observed_count
        counts = ScanCounts(
            sources_total=max(scan.source_count, len(sources)),
            sources_completed=len(terminal),
            sources_succeeded=len(succeeded),
            sources_degraded=len(degraded),
            sources_failed=len(failed),
            observed_postings=scan.observed_count,
            matched_postings=matched,
            new_opportunities=min(scan.new_opportunity_count, matched),
            changed_postings=min(scan.changed_posting_count, scan.observed_count),
        )
        return ScanStatusResponse(
            id=scan.id,
            version=scan.version,
            saved_search_id=scan.saved_search_id,
            saved_search_version=scan.saved_search_version,
            trigger=scan.trigger,
            status=scan.status,
            stage=_public_scan_stage(scan.status, scan.stage),
            queued_at=_as_utc(scan.created_at),
            started_at=_optional_utc(scan.started_at),
            completed_at=_optional_utc(scan.finalized_at),
            counts=counts,
            warnings=warnings,
        )

    def _scan_replay_response(
        self,
        session,
        *,
        owner_id: str,
        resource_type: str,
        scan_id: str,
    ) -> ScanCreateResponse:
        if resource_type != "opportunity_scan":
            raise WorkspaceUnavailable(
                "idempotent scan result has an inconsistent resource type"
            )
        replay = self._scan_response(session, owner_id=owner_id, scan_id=scan_id)
        if replay is None:
            raise WorkspaceUnavailable("idempotent scan result is unavailable")
        return ScanCreateResponse.model_validate(replay.model_dump())


def _active_pack_companies(pack: str):
    try:
        registry = load_company_pack(pack)
    except RegistryError as exc:
        raise WorkspaceInputError(
            "saved search company pack is unavailable", field="pack"
        ) from exc
    companies = registry.active_companies
    if not companies:
        raise WorkspaceInputError(
            "saved search company pack has no active companies", field="pack"
        )
    return companies


def _source_is_degraded(source: OpportunityScanSource) -> bool:
    return source.status == "succeeded" and (
        source.fetch_scope != "board_snapshot"
        or source.completeness != "complete"
        or bool(source.warning_codes)
        or source.used_fallback
    )


def _scan_warnings(
    scan: OpportunityScan,
    sources: list[OpportunityScanSource],
) -> list[ScanWarning]:
    warnings: list[ScanWarning] = []
    seen: set[tuple[str, str, str, str]] = set()
    for source in sources:
        codes = [
            _STORED_WARNING_CODES.get(str(raw), ScanWarningCode.source_incomplete)
            for raw in source.warning_codes
        ]
        if source.status == "failed":
            codes.append(
                _STORED_WARNING_CODES.get(
                    source.error_code or "", ScanWarningCode.source_unavailable
                )
            )
        elif source.status == "cancelled":
            codes.append(ScanWarningCode.scan_interrupted)
        if source.status == "succeeded" and (
            source.fetch_scope != "board_snapshot"
            or source.completeness != "complete"
        ):
            codes.append(ScanWarningCode.source_incomplete)
        if source.used_fallback:
            codes.append(ScanWarningCode.source_fallback_used)
        occurred_at = _as_utc(source.completed_at or source.updated_at)
        for code in codes:
            key = ("source", code.value, source.company_slug, source.source)
            if key in seen:
                continue
            seen.add(key)
            message, retryable = _WARNING_MESSAGES[code]
            warnings.append(
                ScanWarning(
                    scope=ScanWarningScope.source,
                    code=code,
                    message=message,
                    retryable=retryable,
                    company_slug=source.company_slug,
                    source=source.source,
                    occurred_at=occurred_at,
                )
            )
    if scan.status == "failed" and not warnings:
        code = ScanWarningCode.scan_interrupted
        message, retryable = _WARNING_MESSAGES[code]
        warnings.append(
            ScanWarning(
                scope=ScanWarningScope.scan,
                code=code,
                message=message,
                retryable=retryable,
                occurred_at=_as_utc(scan.finalized_at or scan.updated_at),
            )
        )
    if scan.status == "partial" and not warnings:
        code = ScanWarningCode.source_incomplete
        message, retryable = _WARNING_MESSAGES[code]
        warnings.append(
            ScanWarning(
                scope=ScanWarningScope.scan,
                code=code,
                message=message,
                retryable=retryable,
                occurred_at=_as_utc(scan.finalized_at or scan.updated_at),
            )
        )
    return warnings


def _public_scan_stage(status: str, stage: str) -> ScanStage:
    if status == "queued":
        return ScanStage.queued
    if status in {"succeeded", "partial", "failed", "cancelled"}:
        return ScanStage.complete
    try:
        parsed = ScanStage(stage)
    except ValueError:
        return ScanStage.fetching
    return parsed if parsed not in {ScanStage.queued, ScanStage.complete} else ScanStage.fetching


def _request_hash(request: dict[str, object]) -> str:
    encoded = json.dumps(
        request,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_utc(value: datetime | None) -> datetime | None:
    return _as_utc(value) if value is not None else None


@contextmanager
def _opportunity_errors() -> Iterator[None]:
    try:
        yield
    except (WorkspaceNotFound, WorkspaceConflict, WorkspaceInputError, WorkspaceUnavailable):
        raise
    except VersionConflict as exc:
        raise WorkspaceConflict(str(exc), code="version_conflict") from exc
    except DecisionIdempotencyConflict as exc:
        raise WorkspaceConflict(str(exc), code="idempotency_conflict") from exc
    except MutationIdempotencyConflict as exc:
        raise WorkspaceConflict(str(exc), code="idempotency_conflict") from exc
    except MutationPending as exc:
        raise WorkspaceConflict(str(exc), code="mutation_pending") from exc
    except ResourceConflict as exc:
        raise WorkspaceConflict(str(exc)) from exc
    except OpportunityNotFound as exc:
        raise WorkspaceNotFound("opportunity not found") from exc
    except PostingIdentityConflict as exc:
        raise WorkspaceConflict(str(exc), code="posting_identity_conflict") from exc
    except OpportunityRepositoryError as exc:
        raise WorkspaceUnavailable("opportunity data is inconsistent") from exc
    except (PrivatePayloadBindingError, DecryptionError) as exc:
        raise WorkspaceUnavailable("private opportunity data could not be decrypted") from exc
    except ValidationError as exc:
        raise WorkspaceUnavailable("stored opportunity data failed contract validation") from exc
    except ValueError as exc:
        raise WorkspaceInputError(str(exc)) from exc
    except IntegrityError as exc:
        raise WorkspaceConflict("opportunity resource conflicts with existing state") from exc
    except SQLAlchemyError as exc:
        raise WorkspaceUnavailable("opportunity workspace database is unavailable") from exc


__all__ = ["SqlAlchemyOpportunityWorkspaceStore"]
