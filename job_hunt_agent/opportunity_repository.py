"""Owner-scoped persistence for normalized job postings and Today decisions.

The repository accepts already-fetched :class:`Role` records. It never calls a
source adapter or model provider. Public posting facts are versioned in clear
text; resume-derived ``match_reason`` and ``fit_score`` are deliberately not
included in the persisted snapshot.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .job_queue import utcnow
from .models import (
    JobObservation,
    JobPosting,
    JobPostingAlias,
    JobPostingVersion,
    OpportunityDecisionEvent as OpportunityDecisionEventRow,
    OpportunityScan,
    OpportunityScanSource,
    OwnerOpportunity,
    SavedSearch,
    SavedSearchMatch,
)
from .opportunity_schemas import (
    CompensationEvidenceFact,
    DateEvidenceFact,
    DismissReason,
    EmploymentTypeEvidenceFact,
    EvidenceState,
    MatchAssessmentState,
    NotAssessedReason,
    OpportunityDecisionAction,
    OpportunityDecisionEvent,
    OpportunityDecisionRequest,
    OpportunityDecisionResponse,
    OpportunityDecisionState,
    OpportunityDetailResponse,
    OpportunityFactField,
    OpportunityFacts,
    OpportunityLane,
    OpportunityPosting,
    OpportunityUnknown,
    PostingChangeKind,
    PostingChangedField,
    PostingState,
    PostingVersionSummary,
    SavedSearchProvenance,
    ScanHealthState,
    ScanWarning,
    ScanWarningCode,
    ScanWarningScope,
    TextEvidenceFact,
    TodayListResponse,
    TodayOpportunityItem,
    TodayQuery,
    TodayScanHealth,
    TodaySummary,
    TodayView,
    TransparentMatchSummary,
    UnknownReasonCode,
)
from .private_payloads import decrypt_private_payload, encrypt_private_payload
from .repository_errors import ResourceConflict, require_version
from .schemas import Role
from .security import DataKeyring
from .sources.base import safe_url_path_parts


_TRACKING_QUERY_KEYS = frozenset(
    {
        "dclid",
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "msclkid",
    }
)
_DECISION_NOTE_KIND = "opportunity_decision_note"


class OpportunityRepositoryError(RuntimeError):
    """Base class for safe opportunity persistence failures."""


class PostingIdentityConflict(OpportunityRepositoryError):
    """Two stable aliases unexpectedly point at different postings."""


class OpportunityNotFound(OpportunityRepositoryError):
    """An owner-scoped opportunity is absent without revealing other owners."""


class DecisionIdempotencyConflict(ResourceConflict):
    """An idempotency key was reused with a different decision request."""


@dataclass(frozen=True)
class PostingIdentity:
    kind: str
    key: str
    key_hash: str
    source: str
    company_slug: str
    source_job_id: str | None
    canonical_url: str


@dataclass(frozen=True)
class PersistedRole:
    posting_id: str
    posting_version_id: str
    observation_id: str
    saved_search_match_id: str
    opportunity_id: str
    posting_created: bool
    version_created: bool
    posting_changed: bool
    match_created: bool
    opportunity_created: bool
    replayed: bool


def canonicalize_posting_url(value: str) -> str:
    """Return a stable HTTPS identity URL or reject an unsafe posting URL.

    Host casing, default HTTPS ports, fragments, trailing slashes, query order,
    and known tracking parameters are normalized. Path casing and meaningful
    query parameters (for example ``gh_jid``) remain intact.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError("posting URL is required")
    cleaned = value.strip()
    if (
        "\\" in cleaned
        or any(character.isspace() for character in cleaned)
        or any(ord(character) < 32 for character in cleaned)
    ):
        raise ValueError("posting URL must be a safe HTTPS URL")
    try:
        parsed = urlsplit(cleaned)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("posting URL must be a safe HTTPS URL") from exc
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or safe_url_path_parts(parsed.path) is None
    ):
        raise ValueError("posting URL must be HTTPS without credentials or traversal")

    hostname = parsed.hostname.casefold().rstrip(".")
    if not hostname:
        raise ValueError("posting URL hostname is required")
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    path = parsed.path.rstrip("/") or "/"
    query_pairs: list[tuple[str, str]] = []
    for key, item_value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = key.casefold()
        if normalized_key.startswith("utm_") or normalized_key in _TRACKING_QUERY_KEYS:
            continue
        if any(ord(character) < 32 for character in key + item_value):
            raise ValueError("posting URL query contains control characters")
        query_pairs.append((key, item_value))
    query = urlencode(sorted(query_pairs), doseq=True)
    return urlunsplit(("https", netloc, path, query, ""))


def posting_identity(
    role: Role,
    *,
    canonical_url: str | None = None,
    company_slug: str | None = None,
) -> PostingIdentity:
    """Build identity from native source facts, falling back only to URL."""

    normalized_url = canonical_url or canonicalize_posting_url(role.url)
    resolved_slug = (role.company_slug or company_slug or "").strip().casefold()
    if not resolved_slug:
        raise ValueError("company_slug is required for durable posting identity")
    source = role.source.value
    source_job_id = role.source_job_id.strip() if role.source_job_id else None
    if source_job_id:
        kind = "native"
        key = _identity_key("native", source, resolved_slug, source_job_id)
    else:
        kind = "url"
        key = _identity_key("url", resolved_slug, normalized_url)
    return PostingIdentity(
        kind=kind,
        key=key,
        key_hash=_sha256(key),
        source=source,
        company_slug=resolved_slug,
        source_job_id=source_job_id,
        canonical_url=normalized_url,
    )


def persist_scan_source_role(
    session: Session,
    *,
    owner_id: str,
    scan_source_id: str,
    role: Role,
    first_party_url_verified: bool,
    now: datetime | None = None,
) -> PersistedRole:
    """Idempotently persist one observed Role through the normalized graph."""

    current = _as_utc(now or utcnow())
    source_row = session.scalar(
        select(OpportunityScanSource)
        .where(
            OpportunityScanSource.owner_id == owner_id,
            OpportunityScanSource.id == scan_source_id,
        )
        .with_for_update()
    )
    if source_row is None:
        raise ValueError("scan source does not exist for owner")
    scan = session.scalar(
        select(OpportunityScan)
        .where(
            OpportunityScan.owner_id == owner_id,
            OpportunityScan.id == source_row.opportunity_scan_id,
        )
        .with_for_update()
    )
    if scan is None:
        raise OpportunityRepositoryError("scan source has no owner scan")
    if role.source.value != source_row.source:
        raise ValueError("role source does not match scan source")
    if (
        role.company_slug is not None
        and role.company_slug.casefold() != source_row.company_slug.casefold()
    ):
        raise ValueError("role company_slug does not match scan source")

    canonical_url = canonicalize_posting_url(role.url)
    identity = posting_identity(
        role,
        canonical_url=canonical_url,
        company_slug=source_row.company_slug,
    )
    canonical_apply_urls = _canonical_apply_urls(role, canonical_url)
    aliases = _alias_specs(identity)

    posting, posting_created = _find_or_create_posting(
        session,
        owner_id=owner_id,
        identity=identity,
        aliases=aliases,
        now=current,
    )
    posting = session.scalar(
        select(JobPosting)
        .where(JobPosting.owner_id == owner_id, JobPosting.id == posting.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert posting is not None
    if (
        identity.kind == "native"
        and posting.identity_kind == "native"
        and posting.identity_key_hash != identity.key_hash
    ):
        # A concurrent first native enrichment may have claimed a URL-fallback
        # posting while this transaction waited. Resolve the now-distinct
        # requisition again instead of merging through the shared URL.
        posting, posting_created = _find_or_create_posting(
            session,
            owner_id=owner_id,
            identity=identity,
            aliases=aliases,
            now=current,
        )
        posting = session.scalar(
            select(JobPosting)
            .where(JobPosting.owner_id == owner_id, JobPosting.id == posting.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert posting is not None
    # A scan can wait behind another scan after capturing its observation
    # timestamp. Once the posting lock is ours, keep every downstream record
    # monotonic with the posting that may have committed while we waited.
    current = max(
        current,
        _as_utc(posting.first_confirmed_at),
        _as_utc(posting.last_confirmed_at),
    )

    existing_observation = session.scalar(
        select(JobObservation).where(
            JobObservation.owner_id == owner_id,
            JobObservation.opportunity_scan_source_id == source_row.id,
            JobObservation.job_posting_id == posting.id,
        )
    )
    if existing_observation is not None:
        match = _owner_match(session, owner_id, scan.saved_search_id, posting.id)
        opportunity = _owner_opportunity(session, owner_id, posting.id)
        if match is None or opportunity is None:
            raise OpportunityRepositoryError("observation graph is incomplete")
        return PersistedRole(
            posting_id=posting.id,
            posting_version_id=existing_observation.job_posting_version_id,
            observation_id=existing_observation.id,
            saved_search_match_id=match.id,
            opportunity_id=opportunity.id,
            posting_created=False,
            version_created=False,
            posting_changed=False,
            match_created=False,
            opportunity_created=False,
            replayed=True,
        )

    if identity.kind == "native" and posting.identity_kind == "url":
        # The first stable source ID upgrades a URL fallback. Retain the URL
        # alias for lookup, but make subsequent differing requisition IDs
        # distinct by promoting the posting's primary identity.
        posting.identity_kind = "native"
        posting.identity_key = identity.key
        posting.identity_key_hash = identity.key_hash
        posting.source = identity.source
        posting.company_slug = identity.company_slug
        posting.source_job_id = identity.source_job_id

    alias_rows: list[JobPostingAlias] = []
    for spec in aliases:
        try:
            alias_rows.append(
                _ensure_alias(
                    session,
                    owner_id=owner_id,
                    posting=posting,
                    spec=spec,
                    now=current,
                )
            )
        except PostingIdentityConflict:
            # A generic canonical URL may legitimately be shared by distinct
            # native requisitions. Native identity remains authoritative; the
            # URL simply cannot be an alias for both postings.
            if identity.kind == "native" and spec.kind == "url":
                continue
            raise
    if not alias_rows:
        raise OpportunityRepositoryError("posting has no durable identity alias")
    observation_alias = next(
        (row for row in alias_rows if row.alias_key_hash == identity.key_hash),
        alias_rows[0],
    )

    snapshot = _public_role_snapshot(role, canonical_url, canonical_apply_urls)
    content_hash = _sha256(_canonical_json(snapshot))
    latest_version = _latest_posting_version(
        session,
        owner_id=owner_id,
        posting_id=posting.id,
    )
    version_created = False
    posting_changed = False
    if latest_version is None or latest_version.content_hash != content_hash:
        version_row = JobPostingVersion(
            owner_id=owner_id,
            job_posting_id=posting.id,
            version_number=(latest_version.version_number + 1 if latest_version else 1),
            content_hash=content_hash,
            source=snapshot["source"],
            source_job_id=snapshot["source_job_id"],
            company_name=snapshot["company_name"],
            title=snapshot["title"],
            canonical_url=snapshot["canonical_url"],
            apply_urls=snapshot["apply_urls"],
            location=snapshot["location"],
            summary=snapshot["summary"],
            description=snapshot["description"],
            employment_type=snapshot["employment_type"],
            posted_at_text=snapshot["posted_at_text"],
            source_updated_at_text=snapshot["source_updated_at_text"],
            source_facts=snapshot["source_facts"],
            source_confidence=snapshot["source_confidence"],
            observed_at=current,
        )
        try:
            with session.begin_nested():
                session.add(version_row)
                session.flush()
            version_created = True
        except IntegrityError:
            winner = _latest_posting_version(
                session,
                owner_id=owner_id,
                posting_id=posting.id,
            )
            if winner is None or winner.content_hash != content_hash:
                raise PostingIdentityConflict(
                    "concurrent posting version changed; retry the sighting"
                )
            version_row = winner
        posting_changed = latest_version is not None
    else:
        version_row = latest_version

    if posting.lifecycle_state == "closed":
        posting.lifecycle_state = "open"
        posting.closed_at = None
        posting.closure_reason = None
        posting_changed = True
    posting.consecutive_complete_omissions = 0
    posting.last_confirmed_at = max(_as_utc(posting.last_confirmed_at), current)
    posting.canonical_url = canonical_url
    if posting_changed:
        posting.last_changed_at = current
        posting.version += 1
    posting.updated_at = current

    observation = JobObservation(
        owner_id=owner_id,
        opportunity_scan_id=scan.id,
        opportunity_scan_source_id=source_row.id,
        job_posting_id=posting.id,
        job_posting_version_id=version_row.id,
        job_posting_alias_id=observation_alias.id,
        first_party_url_verified=first_party_url_verified,
        observed_at=current,
    )
    session.add(observation)
    session.flush()

    match, match_created = _upsert_saved_search_match(
        session,
        owner_id=owner_id,
        scan=scan,
        posting=posting,
        posting_version=version_row,
        now=current,
    )
    opportunity, opportunity_created = _upsert_owner_opportunity(
        session,
        owner_id=owner_id,
        posting=posting,
        changed=posting_changed or match_created,
        now=current,
    )

    source_row.persisted_count += 1
    source_row.version += 1
    source_row.updated_at = current
    scan.observed_count += 1
    scan.new_posting_count += int(posting_created)
    scan.changed_posting_count += int(posting_changed and not posting_created)
    scan.new_opportunity_count += int(opportunity_created)
    scan.version += 1
    scan.updated_at = current
    session.flush()
    return PersistedRole(
        posting_id=posting.id,
        posting_version_id=version_row.id,
        observation_id=observation.id,
        saved_search_match_id=match.id,
        opportunity_id=opportunity.id,
        posting_created=posting_created,
        version_created=version_created,
        posting_changed=posting_changed,
        match_created=match_created,
        opportunity_created=opportunity_created,
        replayed=False,
    )


def list_today_opportunities(
    session: Session,
    *,
    owner_id: str,
    query: TodayQuery,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> TodayListResponse:
    """Build the Today inbox exclusively from persisted owner-scoped rows."""

    current = _as_utc(now or utcnow())
    decision_by_view = {
        TodayView.inbox: "inbox",
        TodayView.watching: "watch",
        TodayView.dismissed: "dismiss",
    }
    statement = select(OwnerOpportunity).where(OwnerOpportunity.owner_id == owner_id)
    if query.view is not TodayView.all:
        statement = statement.where(
            OwnerOpportunity.decision == decision_by_view[query.view]
        )
    if query.saved_search_id is not None:
        statement = statement.where(
            OwnerOpportunity.job_posting_id.in_(
                select(SavedSearchMatch.job_posting_id).where(
                    SavedSearchMatch.owner_id == owner_id,
                    SavedSearchMatch.saved_search_id == query.saved_search_id,
                )
            )
        )
    cursor = _decode_cursor(query.cursor) if query.cursor else None
    if cursor is not None:
        cursor_time, cursor_id = cursor
        statement = statement.where(
            (OwnerOpportunity.last_surfaced_at < cursor_time)
            | (
                (OwnerOpportunity.last_surfaced_at == cursor_time)
                & (OwnerOpportunity.id < cursor_id)
            )
        )
    statement = statement.order_by(
        OwnerOpportunity.last_surfaced_at.desc(),
        OwnerOpportunity.id.desc(),
    ).limit(query.limit + 1)
    rows = list(session.scalars(statement))
    if query.lane not in (None, OpportunityLane.unassigned):
        rows = []
    has_more = len(rows) > query.limit
    selected = rows[: query.limit]
    items = [
        _today_item(session, opportunity=row, keyring=keyring)
        for row in selected
    ]
    next_cursor = (
        _encode_cursor(selected[-1].last_surfaced_at, selected[-1].id)
        if has_more and selected
        else None
    )
    return TodayListResponse(
        data_source="database",
        as_of=current,
        summary=_today_summary(session, owner_id),
        scan_health=_today_scan_health(session, owner_id),
        items=items,
        next_cursor=next_cursor,
    )


def load_opportunity_detail(
    session: Session,
    *,
    owner_id: str,
    opportunity_id: str,
    keyring: DataKeyring,
) -> OpportunityDetailResponse | None:
    """Return one database-only review projection, or None across owner scope."""

    opportunity = session.scalar(
        select(OwnerOpportunity).where(
            OwnerOpportunity.owner_id == owner_id,
            OwnerOpportunity.id == opportunity_id,
        )
    )
    if opportunity is None:
        return None
    base = _today_item(session, opportunity=opportunity, keyring=keyring)
    versions = list(
        session.scalars(
            select(JobPostingVersion)
            .where(
                JobPostingVersion.owner_id == owner_id,
                JobPostingVersion.job_posting_id == opportunity.job_posting_id,
            )
            .order_by(JobPostingVersion.version_number, JobPostingVersion.id)
        )
    )
    history = list(
        session.scalars(
            select(OpportunityDecisionEventRow)
            .where(
                OpportunityDecisionEventRow.owner_id == owner_id,
                OpportunityDecisionEventRow.owner_opportunity_id == opportunity.id,
            )
            .order_by(
                OpportunityDecisionEventRow.occurred_at,
                OpportunityDecisionEventRow.created_at,
                OpportunityDecisionEventRow.id,
            )
        )
    )
    summaries: list[PostingVersionSummary] = []
    previous: JobPostingVersion | None = None
    for version in versions:
        changed_fields = _changed_fields(previous, version)
        summaries.append(
            PostingVersionSummary(
                version=version.version_number,
                observed_at=_as_utc(version.observed_at),
                change_kind=(
                    PostingChangeKind.new
                    if previous is None
                    else PostingChangeKind.changed
                ),
                changed_fields=changed_fields,
            )
        )
        previous = version
    latest = versions[-1]
    return OpportunityDetailResponse(
        **base.model_dump(),
        data_source="database",
        description=_trimmed(latest.description, 100_000),
        apply_urls=list(latest.apply_urls),
        posting_versions=summaries,
        decision_history=[_decision_event_response(row, keyring) for row in history],
    )


def decide_owner_opportunity(
    session: Session,
    *,
    owner_id: str,
    opportunity_id: str,
    request: OpportunityDecisionRequest,
    expected_version: int,
    idempotency_key: str,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> OpportunityDecisionResponse:
    """Apply one optimistic, idempotent decision and append its audit event."""

    current = _as_utc(now or utcnow())
    normalized_key = idempotency_key.strip()
    if not normalized_key or len(normalized_key) > 200:
        raise ValueError("idempotency key must be 1-200 characters")
    key_hash = _sha256(normalized_key)
    request_payload = request.model_dump(mode="json")
    # Phase 2B added a pursue-only field to this shared transport model. Keep
    # ordinary decision hashes byte-for-byte compatible with Phase 2A so an
    # already accepted Watch/Dismiss/Restore key still replays after upgrade.
    if request.action is not OpportunityDecisionAction.pursue:
        request_payload.pop("initial_action_due_on", None)
        request_payload.pop("acquisition_source", None)
        request_payload.pop("selected_saved_search_id", None)
    request_hash = _sha256(_canonical_json(request_payload))
    opportunity = session.scalar(
        select(OwnerOpportunity)
        .where(
            OwnerOpportunity.owner_id == owner_id,
            OwnerOpportunity.id == opportunity_id,
        )
        .with_for_update()
    )
    if opportunity is None:
        raise OpportunityNotFound("opportunity not found")
    replay = session.scalar(
        select(OpportunityDecisionEventRow).where(
            OpportunityDecisionEventRow.owner_id == owner_id,
            OpportunityDecisionEventRow.owner_opportunity_id == opportunity.id,
            OpportunityDecisionEventRow.idempotency_key_hash == key_hash,
        )
    )
    if replay is not None:
        if not hmac.compare_digest(replay.request_hash, request_hash):
            raise DecisionIdempotencyConflict(
                "idempotency key was already used for another decision"
            )
        if opportunity.decision != replay.new_decision:
            raise ResourceConflict("decision replay was superseded by a newer decision")
        return _decision_response(opportunity, replay, keyring)

    if request.action is OpportunityDecisionAction.pursue:
        raise ValueError("pursue must use the atomic application boundary")
    if opportunity.decision == "pursued":
        raise ResourceConflict(
            "pursued opportunities are managed through their application"
        )

    require_version(
        "opportunity",
        opportunity.id,
        expected=expected_version,
        actual=opportunity.version,
    )
    latest_version = _latest_posting_version(
        session,
        owner_id=owner_id,
        posting_id=opportunity.job_posting_id,
    )
    if latest_version is None:
        raise OpportunityRepositoryError("opportunity posting has no version")

    action = request.action
    if action is OpportunityDecisionAction.watch:
        target = "watch"
        reason = None
        compensates = None
    elif action is OpportunityDecisionAction.dismiss:
        target = "dismiss"
        reason = request.dismiss_reason.value if request.dismiss_reason else None
        compensates = None
    elif action is OpportunityDecisionAction.restore_to_inbox:
        target = "inbox"
        reason = None
        compensates = session.scalar(
            select(OpportunityDecisionEventRow)
            .where(
                OpportunityDecisionEventRow.owner_id == owner_id,
                OpportunityDecisionEventRow.owner_opportunity_id == opportunity.id,
            )
            .order_by(
                OpportunityDecisionEventRow.occurred_at.desc(),
                OpportunityDecisionEventRow.created_at.desc(),
                OpportunityDecisionEventRow.id.desc(),
            )
            .limit(1)
            .with_for_update()
        )
        if (
            compensates is None
            or compensates.id != request.restore_decision_event_id
            or compensates.new_decision != opportunity.decision
        ):
            raise ResourceConflict("restore target is not the current opportunity decision")
    else:  # pragma: no cover - enum exhaustiveness is defended above.
        raise ValueError("unsupported opportunity decision action")
    if opportunity.decision == target:
        raise ResourceConflict("opportunity is already in the requested decision state")

    event_id = uuid4().hex
    encrypted_note = None
    note_key_id = None
    if request.note is not None:
        envelope = encrypt_private_payload(
            keyring,
            record_kind=_DECISION_NOTE_KIND,
            owner_id=owner_id,
            record_id=event_id,
            payload={"note": request.note},
        )
        encrypted_note = envelope.ciphertext
        note_key_id = envelope.key_id
    event = OpportunityDecisionEventRow(
        id=event_id,
        owner_id=owner_id,
        owner_opportunity_id=opportunity.id,
        job_posting_id=opportunity.job_posting_id,
        posting_version_id=latest_version.id,
        previous_decision=opportunity.decision,
        new_decision=target,
        reason_code=reason,
        encrypted_note=encrypted_note,
        note_key_id=note_key_id,
        compensates_event_id=compensates.id if compensates is not None else None,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
        occurred_at=current,
    )
    session.add(event)
    opportunity.decision = target
    opportunity.decision_reason_code = reason
    opportunity.reviewed_posting_version_id = latest_version.id
    opportunity.decision_updated_at = current
    opportunity.version += 1
    opportunity.updated_at = current
    session.flush()
    return _decision_response(opportunity, event, keyring)


def _today_item(
    session: Session,
    *,
    opportunity: OwnerOpportunity,
    keyring: DataKeyring,
) -> TodayOpportunityItem:
    posting = session.scalar(
        select(JobPosting).where(
            JobPosting.owner_id == opportunity.owner_id,
            JobPosting.id == opportunity.job_posting_id,
        )
    )
    latest = _latest_posting_version(
        session,
        owner_id=opportunity.owner_id,
        posting_id=opportunity.job_posting_id,
    )
    if posting is None or latest is None:
        raise OpportunityRepositoryError("opportunity posting graph is incomplete")
    latest_observation = session.scalar(
        select(JobObservation)
        .where(
            JobObservation.owner_id == opportunity.owner_id,
            JobObservation.job_posting_id == posting.id,
            JobObservation.job_posting_version_id == latest.id,
        )
        .order_by(JobObservation.observed_at.desc(), JobObservation.id.desc())
        .limit(1)
    )
    if latest_observation is None:
        raise OpportunityRepositoryError("posting version has no observation")
    match_rows = list(
        session.execute(
            select(SavedSearchMatch, SavedSearch)
            .join(
                SavedSearch,
                (SavedSearch.owner_id == SavedSearchMatch.owner_id)
                & (SavedSearch.id == SavedSearchMatch.saved_search_id),
            )
            .where(
                SavedSearchMatch.owner_id == opportunity.owner_id,
                SavedSearchMatch.job_posting_id == posting.id,
            )
            .order_by(SavedSearchMatch.first_matched_at, SavedSearchMatch.id)
        )
    )
    latest_event = session.scalar(
        select(OpportunityDecisionEventRow)
        .where(
            OpportunityDecisionEventRow.owner_id == opportunity.owner_id,
            OpportunityDecisionEventRow.owner_opportunity_id == opportunity.id,
        )
        .order_by(
            OpportunityDecisionEventRow.occurred_at.desc(),
            OpportunityDecisionEventRow.created_at.desc(),
            OpportunityDecisionEventRow.id.desc(),
        )
        .limit(1)
    )
    facts, unknowns = _facts_and_unknowns(latest)
    if posting.lifecycle_state == "closed":
        change_kind = PostingChangeKind.closed
        changed_at = _as_utc(posting.closed_at) if posting.closed_at else None
    elif opportunity.reviewed_posting_version_id == latest.id:
        change_kind = PostingChangeKind.unchanged
        changed_at = None
    elif latest.version_number == 1:
        change_kind = PostingChangeKind.new
        changed_at = None
    else:
        change_kind = PostingChangeKind.changed
        changed_at = _as_utc(posting.last_changed_at or latest.observed_at)
    return TodayOpportunityItem(
        id=opportunity.id,
        version=opportunity.version,
        state=OpportunityDecisionState(opportunity.decision),
        lane=OpportunityLane.unassigned,
        posting=OpportunityPosting(
            id=posting.id,
            company=latest.company_name,
            company_slug=posting.company_slug,
            title=latest.title,
            summary=_trimmed(latest.summary, 2_000) or "Summary unavailable.",
            canonical_url=posting.canonical_url,
            source=latest.source,
            source_job_id=latest.source_job_id,
            first_party=latest_observation.first_party_url_verified,
            state=PostingState(posting.lifecycle_state),
            change_kind=change_kind,
            first_seen_at=_as_utc(posting.first_confirmed_at),
            last_confirmed_at=_as_utc(posting.last_confirmed_at),
            changed_at=changed_at,
        ),
        facts=facts,
        unknowns=unknowns,
        discovered_by=[
            SavedSearchProvenance(
                saved_search_id=match.saved_search_id,
                saved_search_name=search.name,
                first_matched_at=_as_utc(match.first_matched_at),
                last_matched_at=_as_utc(match.last_matched_at),
            )
            for match, search in match_rows
        ],
        match=TransparentMatchSummary(
            state=MatchAssessmentState.not_assessed,
            not_assessed_reason=NotAssessedReason.not_requested,
        ),
        latest_decision=(
            _decision_event_response(latest_event, keyring)
            if latest_event is not None
            else None
        ),
        created_at=_as_utc(opportunity.created_at),
        updated_at=_as_utc(opportunity.updated_at),
    )


def _facts_and_unknowns(
    version: JobPostingVersion,
) -> tuple[OpportunityFacts, list[OpportunityUnknown]]:
    observed = _as_utc(version.observed_at)
    source_label = version.source
    unknowns: list[OpportunityUnknown] = []
    location_known = bool(
        version.location.strip()
        and version.location.strip().casefold() != "location not specified"
    )
    if not location_known:
        unknowns.append(_unknown(OpportunityFactField.location))
    employment_known = version.employment_type != "unknown"
    if not employment_known:
        unknowns.append(_unknown(OpportunityFactField.employment_type))
    posted_date = _parse_posted_date(version.posted_at_text)
    if posted_date is None:
        unknowns.append(_unknown(OpportunityFactField.posted_date))
    unknowns.append(_unknown(OpportunityFactField.compensation))
    return (
        OpportunityFacts(
            location=TextEvidenceFact(
                value=version.location if location_known else None,
                state=EvidenceState.verified if location_known else EvidenceState.unknown,
                source_label=source_label if location_known else None,
                observed_at=observed if location_known else None,
            ),
            employment_type=EmploymentTypeEvidenceFact(
                value=version.employment_type if employment_known else None,
                state=EvidenceState.verified if employment_known else EvidenceState.unknown,
                source_label=source_label if employment_known else None,
                observed_at=observed if employment_known else None,
            ),
            posted_date=DateEvidenceFact(
                value=posted_date,
                state=EvidenceState.verified if posted_date else EvidenceState.unknown,
                source_label=source_label if posted_date else None,
                observed_at=observed if posted_date else None,
            ),
            compensation=CompensationEvidenceFact(
                value=None,
                state=EvidenceState.unknown,
            ),
        ),
        unknowns,
    )


def _unknown(field: OpportunityFactField) -> OpportunityUnknown:
    return OpportunityUnknown(
        field=field,
        reason_code=UnknownReasonCode.not_reported_by_source,
        message=f"{field.value.replace('_', ' ').title()} was not reported by the source.",
    )


def _parse_posted_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _today_summary(session: Session, owner_id: str) -> TodaySummary:
    counts = dict(
        session.execute(
            select(OwnerOpportunity.decision, func.count(OwnerOpportunity.id))
            .where(OwnerOpportunity.owner_id == owner_id)
            .group_by(OwnerOpportunity.decision)
        ).all()
    )
    return TodaySummary(
        needs_decision=int(counts.get("inbox", 0)),
        watching=int(counts.get("watch", 0)),
        dismissed=int(counts.get("dismiss", 0)),
    )


def _today_scan_health(session: Session, owner_id: str) -> TodayScanHealth:
    active_searches = int(
        session.scalar(
            select(func.count(SavedSearch.id)).where(
                SavedSearch.owner_id == owner_id,
                SavedSearch.active.is_(True),
            )
        )
        or 0
    )
    latest = session.scalar(
        select(OpportunityScan)
        .where(OpportunityScan.owner_id == owner_id)
        .order_by(OpportunityScan.created_at.desc(), OpportunityScan.id.desc())
        .limit(1)
    )
    if latest is None:
        return TodayScanHealth(
            state=ScanHealthState.never_run,
            active_searches=active_searches,
        )
    last_success = session.scalar(
        select(OpportunityScan.finalized_at)
        .where(
            OpportunityScan.owner_id == owner_id,
            OpportunityScan.status.in_({"succeeded", "partial"}),
        )
        .order_by(OpportunityScan.finalized_at.desc())
        .limit(1)
    )
    last_attempt = _as_utc(latest.finalized_at or latest.started_at or latest.created_at)
    normalized_last_success = _as_utc(last_success) if last_success else None
    if normalized_last_success is not None and normalized_last_success > last_attempt:
        last_attempt = normalized_last_success
    if latest.status in {"queued", "running"}:
        state = ScanHealthState.running
        running_scan_id = latest.id
        warnings: list[ScanWarning] = []
    elif latest.status == "succeeded":
        state = ScanHealthState.healthy
        running_scan_id = None
        warnings = []
    else:
        state = ScanHealthState.degraded
        running_scan_id = None
        warnings = [
            ScanWarning(
                scope=ScanWarningScope.scan,
                code=ScanWarningCode.scan_interrupted,
                message=(
                    "The latest scan was incomplete; previously confirmed roles "
                    "remain visible."
                ),
                retryable=latest.status in {"partial", "failed"},
                occurred_at=last_attempt,
                last_success_at=normalized_last_success,
            )
        ]
    return TodayScanHealth(
        state=state,
        active_searches=active_searches,
        running_scan_id=running_scan_id,
        last_attempt_at=last_attempt,
        last_success_at=normalized_last_success,
        warnings=warnings,
    )


def _changed_fields(
    previous: JobPostingVersion | None,
    current: JobPostingVersion,
) -> list[PostingChangedField]:
    if previous is None:
        return []
    mappings = (
        ("title", PostingChangedField.title),
        ("description", PostingChangedField.description),
        ("location", PostingChangedField.location),
        ("employment_type", PostingChangedField.employment_type),
        ("posted_at_text", PostingChangedField.posted_date),
        ("canonical_url", PostingChangedField.canonical_url),
    )
    changed = [
        field
        for attribute, field in mappings
        if getattr(previous, attribute) != getattr(current, attribute)
    ]
    if not changed:
        changed.append(PostingChangedField.description)
    return changed


def _encode_cursor(value: datetime, opportunity_id: str) -> str:
    raw = _canonical_json([_as_utc(value).isoformat(), opportunity_id]).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, str]:
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        timestamp, opportunity_id = decoded
        parsed = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("cursor is invalid") from exc
    if not isinstance(opportunity_id, str) or not opportunity_id:
        raise ValueError("cursor is invalid")
    return _as_utc(parsed), opportunity_id


def _trimmed(value: str | None, limit: int) -> str | None:
    if value is None or not value.strip():
        return None
    return value if len(value) <= limit else value[:limit].rstrip()


@dataclass(frozen=True)
class _AliasSpec:
    kind: str
    key: str
    key_hash: str
    source: str
    company_slug: str
    source_job_id: str | None
    normalized_url: str | None


def _find_or_create_posting(
    session: Session,
    *,
    owner_id: str,
    identity: PostingIdentity,
    aliases: list[_AliasSpec],
    now: datetime,
) -> tuple[JobPosting, bool]:
    posting = session.scalar(
        select(JobPosting).where(
            JobPosting.owner_id == owner_id,
            JobPosting.identity_key_hash == identity.key_hash,
        )
    )
    if posting is None:
        # A URL-identity posting may later gain a native source ID. Check the
        # exact incoming identity alias before considering URL enrichment.
        alias = session.scalar(
            select(JobPostingAlias).where(
                JobPostingAlias.owner_id == owner_id,
                JobPostingAlias.alias_key_hash == identity.key_hash,
            )
        )
        if alias is not None:
            posting = session.get(JobPosting, alias.job_posting_id)
    if posting is None:
        url_alias = next((spec for spec in aliases if spec.kind == "url"), None)
        alias = (
            session.scalar(
                select(JobPostingAlias).where(
                    JobPostingAlias.owner_id == owner_id,
                    JobPostingAlias.alias_key_hash == url_alias.key_hash,
                )
            )
            if url_alias is not None
            else None
        )
        candidate = session.get(JobPosting, alias.job_posting_id) if alias else None
        if candidate is not None and _can_merge_url_alias_candidate(
            session,
            candidate=candidate,
            identity=identity,
        ):
            posting = candidate
    if posting is not None:
        return posting, False

    candidate = JobPosting(
        owner_id=owner_id,
        identity_kind=identity.kind,
        identity_key=identity.key,
        identity_key_hash=identity.key_hash,
        source=identity.source,
        company_slug=identity.company_slug,
        source_job_id=identity.source_job_id,
        canonical_url=identity.canonical_url,
        lifecycle_state="open",
        consecutive_complete_omissions=0,
        first_confirmed_at=now,
        last_confirmed_at=now,
        version=1,
        created_at=now,
        updated_at=now,
    )
    try:
        with session.begin_nested():
            session.add(candidate)
            session.flush()
        return candidate, True
    except IntegrityError:
        posting = session.scalar(
            select(JobPosting)
            .where(
                JobPosting.owner_id == owner_id,
                JobPosting.identity_key_hash == identity.key_hash,
            )
            .with_for_update()
        )
        if posting is None:
            raise
        return posting, False


def _can_merge_url_alias_candidate(
    session: Session,
    *,
    candidate: JobPosting,
    identity: PostingIdentity,
) -> bool:
    if identity.kind == "url":
        return True
    if candidate.identity_kind == "native":
        return (
            candidate.source == identity.source
            and candidate.company_slug == identity.company_slug
            and candidate.source_job_id == identity.source_job_id
        )
    native_alias_hashes = set(
        session.scalars(
            select(JobPostingAlias.alias_key_hash).where(
                JobPostingAlias.owner_id == candidate.owner_id,
                JobPostingAlias.job_posting_id == candidate.id,
                JobPostingAlias.alias_kind == "native",
            )
        )
    )
    return not native_alias_hashes or identity.key_hash in native_alias_hashes


def _ensure_alias(
    session: Session,
    *,
    owner_id: str,
    posting: JobPosting,
    spec: _AliasSpec,
    now: datetime,
) -> JobPostingAlias:
    existing = session.scalar(
        select(JobPostingAlias).where(
            JobPostingAlias.owner_id == owner_id,
            JobPostingAlias.alias_key_hash == spec.key_hash,
        )
    )
    if existing is not None:
        if existing.job_posting_id != posting.id:
            raise PostingIdentityConflict("posting alias belongs to a different posting")
        existing.last_seen_at = max(_as_utc(existing.last_seen_at), now)
        return existing
    alias = JobPostingAlias(
        owner_id=owner_id,
        job_posting_id=posting.id,
        alias_kind=spec.kind,
        alias_key=spec.key,
        alias_key_hash=spec.key_hash,
        source=spec.source,
        company_slug=spec.company_slug,
        source_job_id=spec.source_job_id,
        normalized_url=spec.normalized_url,
        first_seen_at=now,
        last_seen_at=now,
        created_at=now,
    )
    try:
        with session.begin_nested():
            session.add(alias)
            session.flush()
        return alias
    except IntegrityError:
        existing = session.scalar(
            select(JobPostingAlias).where(
                JobPostingAlias.owner_id == owner_id,
                JobPostingAlias.alias_key_hash == spec.key_hash,
            )
        )
        if existing is None or existing.job_posting_id != posting.id:
            raise PostingIdentityConflict("concurrent posting alias conflict")
        return existing


def _upsert_saved_search_match(
    session: Session,
    *,
    owner_id: str,
    scan: OpportunityScan,
    posting: JobPosting,
    posting_version: JobPostingVersion,
    now: datetime,
) -> tuple[SavedSearchMatch, bool]:
    match = _owner_match(session, owner_id, scan.saved_search_id, posting.id)
    if match is not None:
        _advance_match(match, scan, posting_version, now)
        return match, False
    match = SavedSearchMatch(
        owner_id=owner_id,
        saved_search_id=scan.saved_search_id,
        job_posting_id=posting.id,
        first_scan_id=scan.id,
        last_scan_id=scan.id,
        last_posting_version_id=posting_version.id,
        match_count=1,
        first_matched_at=now,
        last_matched_at=now,
        created_at=now,
        updated_at=now,
    )
    try:
        with session.begin_nested():
            session.add(match)
            session.flush()
        return match, True
    except IntegrityError:
        existing = _owner_match(session, owner_id, scan.saved_search_id, posting.id)
        if existing is None:
            raise
        _advance_match(existing, scan, posting_version, now)
        return existing, False


def _advance_match(
    match: SavedSearchMatch,
    scan: OpportunityScan,
    posting_version: JobPostingVersion,
    now: datetime,
) -> None:
    if match.last_scan_id == scan.id:
        return
    match.match_count += 1
    if now >= _as_utc(match.last_matched_at):
        match.last_scan_id = scan.id
        match.last_posting_version_id = posting_version.id
        match.last_matched_at = now
    match.updated_at = max(_as_utc(match.updated_at), now)


def _upsert_owner_opportunity(
    session: Session,
    *,
    owner_id: str,
    posting: JobPosting,
    changed: bool,
    now: datetime,
) -> tuple[OwnerOpportunity, bool]:
    opportunity = _owner_opportunity(session, owner_id, posting.id)
    if opportunity is not None:
        opportunity.last_surfaced_at = max(_as_utc(opportunity.last_surfaced_at), now)
        if changed:
            opportunity.version += 1
            opportunity.updated_at = now
        return opportunity, False
    opportunity = OwnerOpportunity(
        owner_id=owner_id,
        job_posting_id=posting.id,
        decision="inbox",
        first_surfaced_at=now,
        last_surfaced_at=now,
        version=1,
        created_at=now,
        updated_at=now,
    )
    try:
        with session.begin_nested():
            session.add(opportunity)
            session.flush()
        return opportunity, True
    except IntegrityError:
        existing = _owner_opportunity(session, owner_id, posting.id)
        if existing is None:
            raise
        return existing, False


def _latest_posting_version(
    session: Session,
    *,
    owner_id: str,
    posting_id: str,
) -> JobPostingVersion | None:
    return session.scalar(
        select(JobPostingVersion)
        .where(
            JobPostingVersion.owner_id == owner_id,
            JobPostingVersion.job_posting_id == posting_id,
        )
        .order_by(
            JobPostingVersion.version_number.desc(),
            JobPostingVersion.created_at.desc(),
            JobPostingVersion.id.desc(),
        )
        .limit(1)
    )


def _owner_match(
    session: Session,
    owner_id: str,
    search_id: str,
    posting_id: str,
) -> SavedSearchMatch | None:
    return session.scalar(
        select(SavedSearchMatch)
        .where(
            SavedSearchMatch.owner_id == owner_id,
            SavedSearchMatch.saved_search_id == search_id,
            SavedSearchMatch.job_posting_id == posting_id,
        )
        .with_for_update()
    )


def _owner_opportunity(
    session: Session,
    owner_id: str,
    posting_id: str,
) -> OwnerOpportunity | None:
    return session.scalar(
        select(OwnerOpportunity)
        .where(
            OwnerOpportunity.owner_id == owner_id,
            OwnerOpportunity.job_posting_id == posting_id,
        )
        .with_for_update()
    )


def _public_role_snapshot(
    role: Role,
    canonical_url: str,
    apply_urls: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": role.source.value,
        "source_job_id": role.source_job_id,
        "company_name": role.company,
        "title": role.title,
        "canonical_url": canonical_url,
        "apply_urls": apply_urls,
        "location": role.location,
        "summary": role.summary,
        "description": role.raw_description,
        "employment_type": role.employment_type.value,
        "posted_at_text": role.posted_at,
        "source_updated_at_text": role.source_updated_at,
        "source_facts": {},
        "source_confidence": role.confidence,
    }


def _canonical_apply_urls(role: Role, canonical_url: str) -> list[str]:
    values = [canonical_url]
    for value in role.apply_urls:
        normalized = canonicalize_posting_url(value)
        if normalized not in values:
            values.append(normalized)
    return values


def _alias_specs(identity: PostingIdentity) -> list[_AliasSpec]:
    specs: list[_AliasSpec] = []
    if identity.kind == "native":
        specs.append(
            _AliasSpec(
                kind="native",
                key=identity.key,
                key_hash=identity.key_hash,
                source=identity.source,
                company_slug=identity.company_slug,
                source_job_id=identity.source_job_id,
                normalized_url=None,
            )
        )
    key = _identity_key("url", identity.company_slug, identity.canonical_url)
    specs.append(
        _AliasSpec(
            kind="url",
            key=key,
            key_hash=_sha256(key),
            source=identity.source,
            company_slug=identity.company_slug,
            source_job_id=None,
            normalized_url=identity.canonical_url,
        )
    )
    return list({spec.key_hash: spec for spec in specs}.values())


def _identity_key(*parts: str) -> str:
    return json.dumps(list(parts), ensure_ascii=False, separators=(",", ":"))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _decision_response(
    opportunity: OwnerOpportunity,
    event: OpportunityDecisionEventRow,
    keyring: DataKeyring,
) -> OpportunityDecisionResponse:
    decision_event = _decision_event_response(event, keyring)
    return OpportunityDecisionResponse(
        opportunity_id=opportunity.id,
        opportunity_version=opportunity.version,
        state=OpportunityDecisionState(opportunity.decision),
        event=decision_event,
    )


def _decision_event_response(
    row: OpportunityDecisionEventRow,
    keyring: DataKeyring,
) -> OpportunityDecisionEvent:
    note = None
    if row.encrypted_note is not None and row.note_key_id is not None:
        private = decrypt_private_payload(
            keyring,
            record_kind=_DECISION_NOTE_KIND,
            owner_id=row.owner_id,
            record_id=row.id,
            encryption_key_id=row.note_key_id,
            ciphertext=row.encrypted_note,
        )
        raw_note = private.get("note")
        if raw_note is not None and not isinstance(raw_note, str):
            raise OpportunityRepositoryError("decision note payload is invalid")
        note = raw_note
    if row.new_decision == "inbox":
        action = OpportunityDecisionAction.restore_to_inbox
    elif row.new_decision == "pursued":
        action = OpportunityDecisionAction.pursue
    else:
        action = OpportunityDecisionAction(row.new_decision)
    return OpportunityDecisionEvent(
        id=row.id,
        opportunity_id=row.owner_opportunity_id,
        action=action,
        previous_state=OpportunityDecisionState(row.previous_decision),
        state=OpportunityDecisionState(row.new_decision),
        dismiss_reason=DismissReason(row.reason_code) if row.reason_code else None,
        note=note,
        restores_event_id=row.compensates_event_id,
        created_at=_as_utc(row.occurred_at),
    )


__all__ = [
    "DecisionIdempotencyConflict",
    "OpportunityRepositoryError",
    "OpportunityNotFound",
    "PersistedRole",
    "PostingIdentity",
    "PostingIdentityConflict",
    "canonicalize_posting_url",
    "decide_owner_opportunity",
    "list_today_opportunities",
    "load_opportunity_detail",
    "posting_identity",
    "persist_scan_source_role",
]
