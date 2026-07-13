"""Owner-scoped, database-only projections for application contact benches."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

from sqlalchemy import and_, select
from sqlalchemy.orm import Session, aliased

from .contact_schemas import (
    ApplicationContactBenchResponse,
    ContactBenchCoverage,
    ContactBenchItem,
    ContactBenchResult,
    ContactBenchStatus,
    ContactSearchSnapshot,
    EmployerEvidenceResponse,
    MIN_VERIFIED_CONFIDENCE,
    RelevanceEvidenceResponse,
)
from .job_queue import utcnow
from .models import (
    Application,
    ApplicationContact,
    BackgroundJob,
    Contact,
    ContactPlan,
)


class ContactRepositoryError(RuntimeError):
    """A persisted contact graph is incomplete or internally inconsistent."""


def load_application_contact_bench(
    session: Session,
    owner_id: str,
    application_id: str,
    now: datetime | None = None,
) -> ApplicationContactBenchResponse | None:
    """Return the latest attempt and last completed bench without live calls.

    ``None`` deliberately covers both an unknown application and an application
    owned by somebody else, allowing the transport layer to mask ownership with
    one 404 response.
    """

    current_time = _as_utc(now or utcnow())
    application_exists = session.scalar(
        select(Application.id).where(
            Application.owner_id == owner_id,
            Application.id == application_id,
        )
    )
    if application_exists is None:
        return None

    current_plan, completed_plan = _current_and_completed_plans(
        session,
        owner_id=owner_id,
        application_id=application_id,
    )
    if current_plan is None:
        return ApplicationContactBenchResponse(
            data_source="database",
            application_id=application_id,
            status=ContactBenchStatus.not_started,
            target_count=5,
            verified_count=0,
            coverage_status=ContactBenchCoverage.not_started,
            current_search=None,
            last_completed_result=None,
        )

    _validate_current_search_job(session, current_plan, owner_id=owner_id)
    current_search = _search_snapshot(current_plan)
    completed_result = (
        _completed_result(
            session,
            completed_plan,
            owner_id=owner_id,
            application_id=application_id,
            now=current_time,
        )
        if completed_plan is not None
        else None
    )
    return ApplicationContactBenchResponse(
        data_source="database",
        application_id=application_id,
        status=current_plan.status,
        target_count=5,
        verified_count=(
            completed_result.verified_count if completed_result is not None else 0
        ),
        coverage_status=(
            completed_result.coverage_status.value
            if completed_result is not None
            else ContactBenchCoverage.pending
        ),
        current_search=current_search,
        last_completed_result=completed_result,
    )


def _current_and_completed_plans(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
) -> tuple[ContactPlan | None, ContactPlan | None]:
    """Read both plan pointers in one database snapshot."""

    current_id = (
        select(ContactPlan.id)
        .where(
            ContactPlan.owner_id == owner_id,
            ContactPlan.application_id == application_id,
        )
        .order_by(
            ContactPlan.plan_number.desc(),
            ContactPlan.created_at.desc(),
            ContactPlan.id.desc(),
        )
        .limit(1)
        .scalar_subquery()
    )
    completed_id = (
        select(ContactPlan.id)
        .where(
            ContactPlan.owner_id == owner_id,
            ContactPlan.application_id == application_id,
            ContactPlan.status == "completed",
        )
        .order_by(
            ContactPlan.plan_number.desc(),
            ContactPlan.finalized_at.desc(),
            ContactPlan.id.desc(),
        )
        .limit(1)
        .scalar_subquery()
    )
    current = aliased(ContactPlan, name="current_contact_plan")
    completed = aliased(ContactPlan, name="completed_contact_plan")
    row = session.execute(
        select(current, completed)
        .select_from(current)
        .outerjoin(
            completed,
            and_(
                completed.id == completed_id,
                completed.owner_id == owner_id,
                completed.application_id == application_id,
            ),
        )
        .where(
            current.id == current_id,
            current.owner_id == owner_id,
            current.application_id == application_id,
        )
    ).one_or_none()
    if row is None:
        return None, None
    return row[0], row[1]


def _search_snapshot(plan: ContactPlan) -> ContactSearchSnapshot:
    return ContactSearchSnapshot(
        id=plan.id,
        version=plan.version,
        plan_number=plan.plan_number,
        status=plan.status,
        target_count=plan.target_count,
        candidate_limit=plan.candidate_limit,
        confidence_floor=plan.confidence_floor,
        discovered_count=plan.discovered_count,
        evidence_verified_count=plan.verified_count,
        selected_count=plan.selected_count,
        coverage_status=plan.coverage_status,
        exhausted=plan.exhausted,
        retryable=plan.retryable,
        shortfall_reasons=_shortfall_reasons(plan.shortfall_reasons),
        error_code=plan.error_code,
        started_at=_optional_utc(plan.started_at),
        finalized_at=_optional_utc(plan.finalized_at),
        created_at=_as_utc(plan.created_at),
        updated_at=_as_utc(plan.updated_at),
    )


def _validate_current_search_job(
    session: Session,
    plan: ContactPlan,
    *,
    owner_id: str,
) -> None:
    if plan.status not in {"queued", "running"}:
        return
    job = (
        session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.id == plan.background_job_id,
                BackgroundJob.owner_id == owner_id,
            )
        )
        if plan.background_job_id is not None
        else None
    )
    if (
        job is None
        or job.owner_id != owner_id
        or job.kind != "discover_contacts"
        or job.subject_type != "contact_plan"
        or job.subject_id != plan.id
        or job.status not in {"queued", "running"}
    ):
        raise ContactRepositoryError("active contact search has no active queue job")


def _completed_result(
    session: Session,
    plan: ContactPlan,
    *,
    owner_id: str,
    application_id: str,
    now: datetime,
) -> ContactBenchResult:
    if plan.status != "completed" or plan.finalized_at is None:
        raise ContactRepositoryError("last completed contact plan is not complete")
    rows = list(
        session.execute(
            select(ApplicationContact, Contact)
            .join(
                Contact,
                and_(
                    Contact.owner_id == ApplicationContact.owner_id,
                    Contact.id == ApplicationContact.contact_id,
                ),
            )
            .where(
                ApplicationContact.owner_id == owner_id,
                ApplicationContact.application_id == application_id,
                ApplicationContact.contact_plan_id == plan.id,
                ApplicationContact.bench_rank.is_not(None),
            )
            .order_by(
                ApplicationContact.bench_rank,
                ApplicationContact.id,
            )
        )
    )
    if len(rows) != plan.selected_count:
        raise ContactRepositoryError(
            "completed contact plan selected count does not match its bench"
        )
    contacts = [
        _contact_item(
            application_contact,
            contact,
            confidence_floor=plan.confidence_floor,
            now=now,
        )
        for application_contact, contact in rows
    ]
    return ContactBenchResult(
        contact_plan_id=plan.id,
        plan_number=plan.plan_number,
        target_count=plan.target_count,
        verified_count=plan.selected_count,
        coverage_status=plan.coverage_status,
        exhausted=plan.exhausted,
        shortfall_reasons=_shortfall_reasons(plan.shortfall_reasons),
        contacts=contacts,
        completed_at=_as_utc(plan.finalized_at),
    )


def _contact_item(
    row: ApplicationContact,
    contact: Contact,
    *,
    confidence_floor: float,
    now: datetime,
) -> ContactBenchItem:
    if row.verification_status != "verified":
        raise ContactRepositoryError("selected contact is not verified")
    if row.confidence < max(MIN_VERIFIED_CONFIDENCE, confidence_floor):
        raise ContactRepositoryError("selected contact is below the evidence floor")
    if row.bench_rank is None or row.wave is None:
        raise ContactRepositoryError("selected contact has no bench rank or wave")
    if row.bench_state == "ready":
        if contact.lifecycle != "active":
            raise ContactRepositoryError("non-active contact cannot be ready")
        if row.cooldown_until is not None and _as_utc(row.cooldown_until) > now:
            raise ContactRepositoryError("contact cooldown has not elapsed")
        if row.unlocked_at is None or _as_utc(row.unlocked_at) > now:
            raise ContactRepositoryError("contact is not currently unlocked")
    if any(
        value is None
        for value in (
            row.verified_at,
            row.employer_evidence_excerpt,
            row.employer_evidence_url,
            row.employer_evidence_source,
            row.employer_evidence_observed_at,
        )
    ):
        raise ContactRepositoryError("selected contact has incomplete employer evidence")

    if not isinstance(row.score_components, dict):
        raise ContactRepositoryError("selected contact score components are invalid")

    verified_at = cast(datetime, row.verified_at)
    evidence_excerpt = cast(str, row.employer_evidence_excerpt)
    evidence_url = cast(str, row.employer_evidence_url)
    evidence_source = cast(str, row.employer_evidence_source)
    evidence_observed_at = cast(datetime, row.employer_evidence_observed_at)
    return ContactBenchItem(
        id=row.id,
        contact_id=contact.id,
        version=row.version,
        public_name=contact.public_name,
        profile_url=contact.normalized_profile_url,
        profile_source=contact.profile_source,
        lifecycle=contact.lifecycle,
        current_title=row.current_title,
        current_company=row.current_company,
        category=row.category,
        confidence=row.confidence,
        verified_at=_as_utc(verified_at),
        employer_evidence=EmployerEvidenceResponse(
            excerpt=evidence_excerpt,
            url=evidence_url,
            source=evidence_source,
            observed_at=_as_utc(evidence_observed_at),
        ),
        why_relevant=row.why_relevant,
        relationship=RelevanceEvidenceResponse(
            status=row.relationship_status,
            summary=row.relationship_evidence_summary,
            url=row.relationship_evidence_url,
        ),
        team_proximity=RelevanceEvidenceResponse(
            status=row.team_proximity_status,
            summary=row.team_evidence_summary,
            url=row.team_evidence_url,
        ),
        score_total=row.score_total,
        score_components=dict(row.score_components),
        scoring_version=row.scoring_version,
        bench_rank=row.bench_rank,
        wave=row.wave,
        bench_state=row.bench_state,
        cooldown_until=_optional_utc(row.cooldown_until),
        unlocked_at=_optional_utc(row.unlocked_at),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_utc(value: datetime | None) -> datetime | None:
    return _as_utc(value) if value is not None else None


def _shortfall_reasons(value: object) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise ContactRepositoryError("contact plan shortfall reasons are invalid")
    return list(value)


__all__ = ["ContactRepositoryError", "load_application_contact_bench"]
