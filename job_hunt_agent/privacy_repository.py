"""Transactional, owner-scoped privacy export, retention, and deletion."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import Table, delete, func, select, update
from sqlalchemy.orm import Session

from .hunt_payloads import (
    decrypt_hunt_outcome,
    decrypt_hunt_request,
    decrypt_hunt_result,
)
from .models import (
    BackgroundJob,
    Base,
    HuntOutcome,
    HuntRun,
    Owner,
    OwnerPrivacySetting,
    OwnerSession,
    PrivacyDeletionReceipt,
)
from .private_payloads import (
    PrivatePayloadBindingError,
    decrypt_private_payload,
)
from .privacy_schemas import (
    DeletionPreviewResponse,
    ExternalDataLimit,
    PrivacyOmission,
    RetentionReportResponse,
    WorkspaceDeletionReceipt,
    WorkspaceExportResponse,
)
from .security import DataKeyring, DecryptionError, hash_access_token


DEFAULT_HUNT_RETENTION_DAYS = 30
DELETION_CONFIRMATION_PREFIX = "DELETE WORKSPACE"

_EXPORT_EXCLUDED_TABLES = {
    "owner_sessions": "security_metadata",
    "owner_mutation_receipts": "security_metadata",
    "background_jobs": "operational_metadata",
    "background_job_events": "operational_metadata",
}
_SENSITIVE_COLUMNS = {
    "access_hash",
    "content_hash",
    "dedupe_key",
    "idempotency_key_hash",
    "identity_key_hash",
    "input_fingerprint",
    "last_error",
    "lease_owner",
    "lease_token",
    "mutation_hash",
    "password_hash",
    "posting_hash",
    "profile_input_fingerprint",
    "request_hash",
    "source_fingerprint",
    "token_hash",
}
_PRIVATE_ENVELOPES: dict[str, tuple[str, str, str]] = {
    "candidate_profiles": (
        "encrypted_payload",
        "encryption_key_id",
        "candidate_profile",
    ),
    "resume_versions": (
        "encrypted_content",
        "encryption_key_id",
        "resume_version",
    ),
    "resume_imports": (
        "encrypted_payload",
        "encryption_key_id",
        "resume_import",
    ),
    "opportunity_fit_evaluations": (
        "encrypted_payload",
        "encryption_key_id",
        "opportunity_fit_evaluation",
    ),
    "achievement_evidence": (
        "encrypted_payload",
        "encryption_key_id",
        "achievement_evidence",
    ),
    "opportunity_decision_events": (
        "encrypted_note",
        "note_key_id",
        "opportunity_decision_note",
    ),
    "application_pack_revisions": (
        "encrypted_payload",
        "encryption_key_id",
        "application_pack_revision",
    ),
    "application_artifact_revisions": (
        "encrypted_payload",
        "encryption_key_id",
        "application_artifact_revision",
    ),
    "outreach_message_versions": (
        "encrypted_body",
        "encryption_key_id",
        "outreach_message",
    ),
    "outreach_replies": (
        "encrypted_note",
        "note_key_id",
        "outreach_reply_note",
    ),
    "outreach_events": (
        "encrypted_note",
        "note_key_id",
        "outreach_event_note",
    ),
    "application_interview_preparation_revisions": (
        "encrypted_payload",
        "encryption_key_id",
        "application_interview_preparation_revision",
    ),
}
_SECRET_JSON_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "ciphertext",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
)


class PrivacyConflict(RuntimeError):
    """A privacy mutation cannot safely proceed against current state."""


def external_data_limits() -> list[ExternalDataLimit]:
    """Provider-side limits verified for every configured production provider."""

    gemini_verified_on = datetime(2026, 7, 15, tzinfo=timezone.utc).date()
    search_and_trace_verified_on = datetime(2026, 7, 16, tzinfo=timezone.utc).date()
    return [
        ExternalDataLimit(
            provider="Google Gemini API",
            category="paid_service_data_use",
            summary=(
                "Under the current Additional Terms, paid-service prompts and "
                "responses are not used to improve Google's products. Other "
                "mandatory policy-enforcement retention still applies."
            ),
            source_url="https://ai.google.dev/gemini-api/terms",
            verified_on=gemini_verified_on,
        ),
        ExternalDataLimit(
            provider="Google Gemini API",
            category="mandatory_abuse_monitoring",
            summary=(
                "Mandatory abuse-monitoring logs may retain prompts, context, and "
                "output for up to 55 days. Local deletion cannot erase those logs."
            ),
            source_url="https://ai.google.dev/gemini-api/docs/usage-policies",
            verified_on=gemini_verified_on,
        ),
        ExternalDataLimit(
            provider="Google Gemini API",
            category="optional_logs",
            summary=(
                "Optional project logs are separate from mandatory abuse monitoring "
                "and can be configured for 7, 14, 28, or 55 days. Dataset or feedback "
                "sharing changes the applicable data-use terms."
            ),
            source_url="https://ai.google.dev/gemini-api/docs/logs-policy",
            verified_on=gemini_verified_on,
        ),
        ExternalDataLimit(
            provider="Google Gemini API",
            category="zero_data_retention_and_grounding",
            summary=(
                "Zero Data Retention requires approval and sanitizes identifiable "
                "content before abuse logging. Search or Maps grounding has separate "
                "storage terms, currently including a 30-day period."
            ),
            source_url="https://ai.google.dev/gemini-api/docs/zdr",
            verified_on=gemini_verified_on,
        ),
        ExternalDataLimit(
            provider="SerpAPI",
            category="search_archive_retention",
            summary=(
                "Standard searches retain their JSON and raw HTML files for 31 "
                "days. Local workspace deletion cannot remove that provider "
                "archive. Enterprise ZeroTrace is a separate opt-in mode and is "
                "not enabled by this product."
            ),
            source_url="https://serpapi.com/faq",
            verified_on=search_and_trace_verified_on,
        ),
        ExternalDataLimit(
            provider="Arize Phoenix",
            category="trace_retention",
            summary=(
                "Phoenix stores traces under the configured project retention "
                "policy; its default policy is indefinite. Operators can configure "
                "retention or delete traces in Phoenix, but deleting this local "
                "workspace does not remove remote trace data."
            ),
            source_url="https://arize.com/docs/phoenix/settings/data-retention",
            verified_on=search_and_trace_verified_on,
        ),
    ]


def export_owner_workspace(
    session: Session,
    *,
    owner_id: str,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> WorkspaceExportResponse:
    """Return deterministic portable JSON without secrets or ciphertext."""

    generated_at = _as_utc(now or datetime.now(timezone.utc))
    owner = session.get(Owner, owner_id)
    if owner is None:
        raise PrivacyConflict("owner workspace does not exist")

    tables: dict[str, list[dict[str, Any]]] = {
        "owners": [
            {
                "id": owner.id,
                "display_name": owner.display_name,
                "timezone": owner.timezone,
                "created_at": owner.created_at,
                "updated_at": owner.updated_at,
            }
        ]
    }
    omissions: dict[tuple[str, str | None, str], int] = defaultdict(int)

    for table_name, reason in sorted(_EXPORT_EXCLUDED_TABLES.items()):
        count = _count_excluded_table(session, table_name=table_name, owner_id=owner_id)
        if count:
            omissions[(table_name, None, reason)] += count

    for table_name, table in sorted(Base.metadata.tables.items()):
        if (
            table_name in _EXPORT_EXCLUDED_TABLES
            or table_name in {"owners", "privacy_deletion_receipts", "hunt_outcomes"}
            or "owner_id" not in table.c
        ):
            continue
        rows = _owned_rows(session, table, owner_id)
        if table_name == "hunt_runs":
            exported = [
                _export_hunt_run(row, keyring=keyring, omissions=omissions)
                for row in rows
            ]
        else:
            exported = [
                _export_regular_row(
                    table_name,
                    row,
                    owner_id=owner_id,
                    keyring=keyring,
                    omissions=omissions,
                )
                for row in rows
            ]
        tables[table_name] = exported

    outcome_rows = list(
        session.execute(
            select(HuntOutcome.__table__)
            .join(HuntRun, HuntOutcome.hunt_run_id == HuntRun.id)
            .where(HuntRun.owner_id == owner_id)
            .order_by(HuntOutcome.hunt_run_id, HuntOutcome.id)
        ).mappings()
    )
    tables["hunt_outcomes"] = [
        _export_hunt_outcome(
            row,
            owner_id=owner_id,
            keyring=keyring,
            omissions=omissions,
        )
        for row in outcome_rows
    ]

    ordered_tables = {name: tables[name] for name in sorted(tables)}
    counts = {name: len(rows) for name, rows in ordered_tables.items()}
    omission_models = [
        PrivacyOmission(table=table, field=field, reason=reason, row_count=count)
        for (table, field, reason), count in sorted(
            omissions.items(), key=lambda item: tuple(str(part) for part in item[0])
        )
    ]
    return WorkspaceExportResponse(
        generated_at=generated_at,
        owner_id=owner_id,
        counts=counts,
        tables=ordered_tables,
        omissions=omission_models,
        external_data_limits=external_data_limits(),
    )


def preview_owner_deletion(
    session: Session,
    *,
    owner_id: str,
    now: datetime | None = None,
) -> DeletionPreviewResponse:
    current = _as_utc(now or datetime.now(timezone.utc))
    counts = owner_row_counts(session, owner_id=owner_id)
    active_sessions = int(
        session.scalar(
            select(func.count())
            .select_from(OwnerSession)
            .where(
                OwnerSession.owner_id == owner_id,
                OwnerSession.revoked_at.is_(None),
                OwnerSession.expires_at > current,
            )
        )
        or 0
    )
    return DeletionPreviewResponse(
        owner_id=owner_id,
        confirmation_phrase=deletion_confirmation_phrase(owner_id),
        row_counts=counts,
        total_rows=sum(counts.values()),
        active_sessions=active_sessions,
        external_data_limits=external_data_limits(),
    )


def delete_owner_workspace(
    session: Session,
    *,
    owner_id: str,
    confirmation: str,
    idempotency_key: str,
    receipt_secret: str,
    now: datetime | None = None,
) -> WorkspaceDeletionReceipt:
    """Delete one owner's graph atomically and persist only a keyed tombstone."""

    expected_confirmation = deletion_confirmation_phrase(owner_id)
    if not hmac.compare_digest(confirmation, expected_confirmation):
        raise PrivacyConflict("workspace deletion confirmation does not match")
    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise ValueError("idempotency key is required")
    if len(normalized_key) > 200:
        raise ValueError("idempotency key is too long")
    if len(receipt_secret) < 32:
        raise ValueError("deletion receipt secret is not configured")

    owner_hash = _keyed_owner_hash(owner_id, receipt_secret)
    key_hash = hash_access_token(normalized_key)
    request_hash = hashlib.sha256(
        json.dumps(
            {"confirmation": confirmation},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    existing = session.scalar(
        select(PrivacyDeletionReceipt)
        .where(
            PrivacyDeletionReceipt.owner_id_hash == owner_hash,
            PrivacyDeletionReceipt.idempotency_key_hash == key_hash,
        )
        .with_for_update()
    )
    if existing is not None:
        if not hmac.compare_digest(existing.request_hash, request_hash):
            raise PrivacyConflict("idempotency key was used for another deletion request")
        return WorkspaceDeletionReceipt(
            deletion_id=existing.id,
            deleted_at=_as_utc(existing.deleted_at),
            replayed=True,
        )

    owner = session.scalar(
        select(Owner).where(Owner.id == owner_id).with_for_update()
    )
    if owner is None:
        raise PrivacyConflict("owner workspace does not exist")
    current = _as_utc(now or datetime.now(timezone.utc))
    receipt = PrivacyDeletionReceipt(
        id=uuid4().hex,
        owner_id_hash=owner_hash,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
        deleted_at=current,
    )
    session.add(receipt)
    session.flush()
    _delete_owner_graph_child_first(session, owner_id=owner_id)
    session.execute(delete(Owner).where(Owner.id == owner_id))
    session.flush()

    remaining = owner_row_counts(session, owner_id=owner_id)
    if any(remaining.values()):
        raise PrivacyConflict("workspace deletion did not remove the complete owner graph")
    return WorkspaceDeletionReceipt(
        deletion_id=receipt.id,
        deleted_at=current,
        replayed=False,
    )


def get_retention_report(
    session: Session,
    *,
    owner_id: str,
    now: datetime | None = None,
    purged_hunt_runs: int = 0,
) -> RetentionReportResponse:
    current = _as_utc(now or datetime.now(timezone.utc))
    setting = session.get(OwnerPrivacySetting, owner_id)
    retention_days = (
        setting.hunt_run_retention_days if setting else DEFAULT_HUNT_RETENTION_DAYS
    )
    cutoff = current - timedelta(days=retention_days)
    eligible = int(
        session.scalar(
            select(func.count())
            .select_from(HuntRun)
            .where(HuntRun.owner_id == owner_id, HuntRun.created_at <= cutoff)
        )
        or 0
    )
    retained = int(
        session.scalar(
            select(func.count()).select_from(HuntRun).where(HuntRun.owner_id == owner_id)
        )
        or 0
    )
    return RetentionReportResponse(
        hunt_run_retention_days=retention_days,
        version=setting.version if setting else 1,
        eligible_hunt_runs=eligible,
        retained_hunt_runs=retained,
        purged_hunt_runs=purged_hunt_runs,
        as_of=current,
        updated_at=_as_utc(setting.updated_at) if setting else None,
    )


def get_owner_hunt_retention_days(session: Session, *, owner_id: str) -> int:
    """Resolve the bounded policy used when creating new legacy hunt runs."""

    setting = session.get(OwnerPrivacySetting, owner_id)
    return setting.hunt_run_retention_days if setting else DEFAULT_HUNT_RETENTION_DAYS


def update_retention_setting(
    session: Session,
    *,
    owner_id: str,
    hunt_run_retention_days: int,
    expected_version: int,
    now: datetime | None = None,
) -> RetentionReportResponse:
    if not 1 <= hunt_run_retention_days <= DEFAULT_HUNT_RETENTION_DAYS:
        raise ValueError("hunt run retention must be between 1 and 30 days")
    current = _as_utc(now or datetime.now(timezone.utc))
    setting = session.scalar(
        select(OwnerPrivacySetting)
        .where(OwnerPrivacySetting.owner_id == owner_id)
        .with_for_update()
    )
    actual_version = setting.version if setting else 1
    if expected_version != actual_version:
        raise PrivacyConflict("retention settings changed; refresh and try again")
    if setting is None:
        if session.get(Owner, owner_id) is None:
            raise PrivacyConflict("owner workspace does not exist")
        setting = OwnerPrivacySetting(
            owner_id=owner_id,
            hunt_run_retention_days=hunt_run_retention_days,
            version=2,
            created_at=current,
            updated_at=current,
        )
        session.add(setting)
    else:
        setting.hunt_run_retention_days = hunt_run_retention_days
        setting.version += 1
        setting.updated_at = current
    session.flush()
    purged = _purge_owner_hunts(
        session,
        owner_id=owner_id,
        retention_days=hunt_run_retention_days,
        now=current,
    )
    return get_retention_report(
        session,
        owner_id=owner_id,
        now=current,
        purged_hunt_runs=purged,
    )


def purge_configured_hunts(
    session: Session,
    *,
    now: datetime | None = None,
) -> int:
    """Apply all owner-specific shorter retention policies during cleanup."""

    current = _as_utc(now or datetime.now(timezone.utc))
    settings = list(session.scalars(select(OwnerPrivacySetting)))
    return sum(
        _purge_owner_hunts(
            session,
            owner_id=setting.owner_id,
            retention_days=setting.hunt_run_retention_days,
            now=current,
        )
        for setting in settings
    )


def owner_row_counts(session: Session, *, owner_id: str) -> dict[str, int]:
    """Count exactly the rows removed by the owner's cascade graph."""

    counts: dict[str, int] = {}
    owner_table = Base.metadata.tables["owners"]
    counts["owners"] = int(
        session.scalar(
            select(func.count()).select_from(owner_table).where(owner_table.c.id == owner_id)
        )
        or 0
    )
    for table_name, table in sorted(Base.metadata.tables.items()):
        if table_name in {"owners", "privacy_deletion_receipts"}:
            continue
        if "owner_id" in table.c:
            count = _count_owned_table(session, table, owner_id)
            if count:
                counts[table_name] = count

    job_events = Base.metadata.tables["background_job_events"]
    background_jobs = Base.metadata.tables["background_jobs"]
    count = int(
        session.scalar(
            select(func.count())
            .select_from(
                job_events.join(
                    background_jobs,
                    job_events.c.job_id == background_jobs.c.id,
                )
            )
            .where(background_jobs.c.owner_id == owner_id)
        )
        or 0
    )
    if count:
        counts["background_job_events"] = count

    hunt_outcomes = Base.metadata.tables["hunt_outcomes"]
    hunt_runs = Base.metadata.tables["hunt_runs"]
    count = int(
        session.scalar(
            select(func.count())
            .select_from(
                hunt_outcomes.join(
                    hunt_runs,
                    hunt_outcomes.c.hunt_run_id == hunt_runs.c.id,
                )
            )
            .where(hunt_runs.c.owner_id == owner_id)
        )
        or 0
    )
    if count:
        counts["hunt_outcomes"] = count
    return {name: counts[name] for name in sorted(counts)}


def deletion_confirmation_phrase(owner_id: str) -> str:
    return f"{DELETION_CONFIRMATION_PREFIX} {owner_id}"


def _export_regular_row(
    table_name: str,
    row: dict[str, Any],
    *,
    owner_id: str,
    keyring: DataKeyring,
    omissions: dict[tuple[str, str | None, str], int],
) -> dict[str, Any]:
    result = dict(row)
    envelope = _PRIVATE_ENVELOPES.get(table_name)
    if envelope is not None:
        cipher_field, key_field, record_kind = envelope
        ciphertext = result.pop(cipher_field, None)
        key_id = result.pop(key_field, None)
        if ciphertext is None or key_id is None:
            if ciphertext is not None or key_id is not None:
                omissions[(table_name, cipher_field, "decryption_failed")] += 1
        else:
            try:
                result["private_payload"] = decrypt_private_payload(
                    keyring,
                    record_kind=record_kind,
                    owner_id=owner_id,
                    record_id=str(result["id"]),
                    encryption_key_id=str(key_id),
                    ciphertext=str(ciphertext),
                )
            except (DecryptionError, PrivatePayloadBindingError, KeyError, ValueError):
                omissions[(table_name, cipher_field, "decryption_failed")] += 1
    return _sanitize_row(table_name, result, omissions)


def _export_hunt_run(
    row: dict[str, Any],
    *,
    keyring: DataKeyring,
    omissions: dict[tuple[str, str | None, str], int],
) -> dict[str, Any]:
    result = dict(row)
    for cipher_field, key_field, output_field in (
        ("encrypted_request", "request_key_id", "request_payload"),
        ("encrypted_result", "result_key_id", "result_payload"),
    ):
        ciphertext = result.pop(cipher_field, None)
        key_id = result.pop(key_field, None)
        if ciphertext is None and key_id is None:
            omissions[("hunt_runs", cipher_field, "expired_or_cleared")] += 1
            continue
        if ciphertext is None or key_id is None:
            omissions[("hunt_runs", cipher_field, "decryption_failed")] += 1
            continue
        try:
            if cipher_field == "encrypted_request":
                plaintext = decrypt_hunt_request(
                    keyring,
                    owner_id=str(result["owner_id"]),
                    hunt_run_id=str(result["id"]),
                    request_hash=str(result["request_hash"]),
                    encryption_key_id=str(key_id),
                    ciphertext=str(ciphertext),
                )
                result[output_field] = json.loads(plaintext)
            else:
                result[output_field] = decrypt_hunt_result(
                    keyring,
                    owner_id=str(result["owner_id"]),
                    hunt_run_id=str(result["id"]),
                    encryption_key_id=str(key_id),
                    ciphertext=str(ciphertext),
                )
        except (DecryptionError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            omissions[("hunt_runs", cipher_field, "decryption_failed")] += 1
    return _sanitize_row("hunt_runs", result, omissions)


def _export_hunt_outcome(
    row: dict[str, Any],
    *,
    owner_id: str,
    keyring: DataKeyring,
    omissions: dict[tuple[str, str | None, str], int],
) -> dict[str, Any]:
    result = dict(row)
    ciphertext = result.pop("encrypted_payload", None)
    key_id = result.pop("encryption_key_id", None)
    if ciphertext is not None and key_id is not None:
        try:
            result["outcome"] = decrypt_hunt_outcome(
                keyring,
                owner_id=owner_id,
                outcome_id=str(result["id"]),
                draft_id=str(result["draft_id"]),
                encryption_key_id=str(key_id),
                ciphertext=str(ciphertext),
            )
        except (DecryptionError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            omissions[("hunt_outcomes", "encrypted_payload", "decryption_failed")] += 1
    else:
        omissions[("hunt_outcomes", "encrypted_payload", "decryption_failed")] += 1
    return _sanitize_row("hunt_outcomes", result, omissions)


def _sanitize_row(
    table_name: str,
    row: dict[str, Any],
    omissions: dict[tuple[str, str | None, str], int],
) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in row.items():
        normalized = key.lower()
        if (
            key in _SENSITIVE_COLUMNS
            or normalized.startswith("encrypted_")
            or normalized.endswith("_key_id")
            or normalized.startswith("lease_")
        ):
            omissions[(table_name, key, "security_metadata")] += 1
            continue
        cleaned, redactions = _sanitize_json(value)
        if redactions:
            omissions[(table_name, key, "security_metadata")] += redactions
        sanitized[key] = cleaned
    return sanitized


def _sanitize_json(value: Any) -> tuple[Any, int]:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        redactions = 0
        for key, item in value.items():
            normalized = str(key).lower()
            if any(marker in normalized for marker in _SECRET_JSON_MARKERS):
                redactions += 1
                continue
            cleaned_item, nested = _sanitize_json(item)
            cleaned[str(key)] = cleaned_item
            redactions += nested
        return cleaned, redactions
    if isinstance(value, list):
        cleaned_list: list[Any] = []
        redactions = 0
        for item in value:
            cleaned_item, nested = _sanitize_json(item)
            cleaned_list.append(cleaned_item)
            redactions += nested
        return cleaned_list, redactions
    return value, 0


def _owned_rows(session: Session, table: Table, owner_id: str) -> list[dict[str, Any]]:
    order = list(table.primary_key.columns)
    query = select(table).where(table.c.owner_id == owner_id)
    if order:
        query = query.order_by(*order)
    return [dict(row) for row in session.execute(query).mappings()]


def _count_owned_table(session: Session, table: Table, owner_id: str) -> int:
    if "owner_id" not in table.c:
        return 0
    return int(
        session.scalar(
            select(func.count()).select_from(table).where(table.c.owner_id == owner_id)
        )
        or 0
    )


def _count_excluded_table(session: Session, *, table_name: str, owner_id: str) -> int:
    table = Base.metadata.tables[table_name]
    if "owner_id" in table.c:
        return _count_owned_table(session, table, owner_id)
    if table_name == "background_job_events":
        jobs = Base.metadata.tables["background_jobs"]
        return int(
            session.scalar(
                select(func.count())
                .select_from(table.join(jobs, table.c.job_id == jobs.c.id))
                .where(jobs.c.owner_id == owner_id)
            )
            or 0
        )
    return 0


def _purge_owner_hunts(
    session: Session,
    *,
    owner_id: str,
    retention_days: int,
    now: datetime,
) -> int:
    cutoff = now - timedelta(days=retention_days)
    job_ids = list(
        session.scalars(
            select(HuntRun.background_job_id).where(
                HuntRun.owner_id == owner_id,
                HuntRun.created_at <= cutoff,
            )
        )
    )
    if not job_ids:
        return 0
    result = session.execute(
        delete(BackgroundJob).where(
            BackgroundJob.owner_id == owner_id,
            BackgroundJob.id.in_(job_ids),
        )
    )
    session.flush()
    deleted_jobs = int(result.rowcount or 0)
    if deleted_jobs != len(job_ids):
        raise PrivacyConflict("retention cleanup did not remove the complete hunt graph")
    return len(job_ids)


def _delete_owner_graph_child_first(
    session: Session,
    *,
    owner_id: str,
) -> None:
    """Remove the owner's RESTRICT/NO ACTION graph in dependency order.

    The product intentionally pins submitted sources and immutable revision
    parents with restrictive foreign keys during normal workflows.  A direct
    owner cascade therefore cannot implement explicit whole-workspace deletion.
    Delete owner-scoped children before their pinned parents, and delete
    self-referential revision leaves before ancestors.  The receipt and every
    delete remain in the caller's single transaction.
    """

    owned_tables = {
        name: table
        for name, table in Base.metadata.tables.items()
        if name not in {"owners", "privacy_deletion_receipts"}
        and "owner_id" in table.c
    }
    applications = owned_tables.get("applications")
    if applications is not None:
        # A closed application points at its outcome while that outcome also
        # belongs to the application. Break this nullable lifecycle cycle only
        # inside the irreversible owner-deletion transaction.
        session.execute(
            update(applications)
            .where(
                applications.c.owner_id == owner_id,
                applications.c.outcome_id.is_not(None),
            )
            .values(stage="applied", outcome_id=None)
        )
    dependencies: dict[str, set[str]] = {
        name: set() for name in owned_tables
    }
    for name, table in owned_tables.items():
        for constraint in table.foreign_key_constraints:
            parent_name = constraint.referred_table.name
            action = (constraint.ondelete or "NO ACTION").upper()
            if (
                parent_name in owned_tables
                and parent_name != name
                and action != "SET NULL"
                and not (
                    name == "applications"
                    and parent_name == "application_outcomes"
                )
            ):
                dependencies[name].add(parent_name)

    remaining_tables = set(owned_tables)
    while remaining_tables:
        referenced_parents = {
            parent
            for child in remaining_tables
            for parent in dependencies[child]
            if parent in remaining_tables
        }
        child_tables = sorted(remaining_tables - referenced_parents)
        if not child_tables:
            raise PrivacyConflict("workspace owner graph contains a dependency cycle")
        for name in child_tables:
            _delete_owned_table_rows(
                session,
                table=owned_tables[name],
                owner_id=owner_id,
            )
            remaining_tables.remove(name)

    session.flush()


def _delete_owned_table_rows(
    session: Session,
    *,
    table: Table,
    owner_id: str,
) -> None:
    """Delete one owner's table rows, respecting restrictive self references."""

    restrictive_self_constraints = [
        constraint
        for constraint in table.foreign_key_constraints
        if constraint.referred_table is table
        and (constraint.ondelete or "NO ACTION").upper()
        not in {"CASCADE", "SET NULL"}
    ]
    if not restrictive_self_constraints:
        session.execute(delete(table).where(table.c.owner_id == owner_id))
        return

    primary_keys = list(table.primary_key.columns)
    if len(primary_keys) != 1:
        raise PrivacyConflict(
            f"workspace deletion cannot safely order {table.name} rows"
        )
    primary_key = primary_keys[0]
    parent_reference_columns = []
    for constraint in restrictive_self_constraints:
        for element in constraint.elements:
            if element.column is primary_key:
                parent_reference_columns.append(element.parent)
    parent_reference_columns = list(dict.fromkeys(parent_reference_columns))
    if not parent_reference_columns:
        raise PrivacyConflict(
            f"workspace deletion cannot resolve {table.name} parent references"
        )

    rows = session.execute(
        select(primary_key, *parent_reference_columns)
        .where(table.c.owner_id == owner_id)
        .with_for_update()
    ).all()
    remaining = {
        str(row[0]): tuple(
            str(value) for value in row[1:] if value is not None
        )
        for row in rows
    }
    while remaining:
        referenced_parents = {
            parent_id
            for parent_ids in remaining.values()
            for parent_id in parent_ids
            if parent_id in remaining
        }
        leaves = sorted(set(remaining) - referenced_parents)
        if not leaves:
            raise PrivacyConflict(
                f"workspace {table.name} history contains a parent cycle"
            )
        result = session.execute(
            delete(table).where(
                table.c.owner_id == owner_id,
                primary_key.in_(leaves),
            )
        )
        if result.rowcount is not None and result.rowcount >= 0:
            if int(result.rowcount) != len(leaves):
                raise PrivacyConflict(
                    f"workspace deletion did not remove complete {table.name} history"
                )
        for row_id in leaves:
            remaining.pop(row_id)


def _keyed_owner_hash(owner_id: str, receipt_secret: str) -> str:
    return hmac.new(
        receipt_secret.encode("utf-8"),
        f"job-hunt:privacy-deletion-owner:v1:{owner_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "DEFAULT_HUNT_RETENTION_DAYS",
    "PrivacyConflict",
    "delete_owner_workspace",
    "deletion_confirmation_phrase",
    "export_owner_workspace",
    "external_data_limits",
    "get_owner_hunt_retention_days",
    "get_retention_report",
    "owner_row_counts",
    "preview_owner_deletion",
    "purge_configured_hunts",
    "update_retention_setting",
]
