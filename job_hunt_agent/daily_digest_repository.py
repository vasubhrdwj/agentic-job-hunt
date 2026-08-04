"""Database-only daily digest projection for one owner.

The digest deliberately reuses the exact Today assessment path. It does not
call a source or model: optional model verdicts are read only when an exact
cached fingerprint already exists, and the deterministic assessment remains
the fallback.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .daily_digest_schemas import (
    DailyDigestHighlight,
    DailyDigestResponse,
    DailyDigestScanSummary,
)
from .models import OpportunityScan, OwnerOpportunity, SavedSearch
from .opportunity_repository import (
    _build_assessment_context,
    _recommended_today_candidates,
    _recommended_today_items,
)
from .opportunity_schemas import (
    AssessmentConfidence,
    MatchAssessmentState,
    OpportunityEligibility,
    OpportunityFitBand,
    OpportunityDecisionState,
    PostingState,
)
from .security import DataKeyring


MAX_DAILY_DIGEST_ASSESSMENTS = 2_000
MAX_DAILY_DIGEST_HIGHLIGHTS = 3


def build_daily_digest(
    session: Session,
    *,
    owner_id: str,
    owner_timezone: str,
    owner_local_date: date,
    keyring: DataKeyring,
    now: datetime | None = None,
) -> DailyDigestResponse:
    """Summarize today's durable discoveries without invoking live providers."""

    current = _as_utc(now or datetime.now(timezone.utc))
    try:
        zone = ZoneInfo(owner_timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("owner timezone is invalid") from exc
    start = datetime.combine(owner_local_date, time.min, tzinfo=zone).astimezone(
        timezone.utc
    )
    tomorrow = datetime.combine(
        owner_local_date + timedelta(days=1),
        time.min,
        tzinfo=zone,
    ).astimezone(timezone.utc)
    end = min(current, tomorrow)
    if end < start:
        raise ValueError("owner local date cannot be in the future")

    new_filters = (
        OwnerOpportunity.owner_id == owner_id,
        OwnerOpportunity.created_at >= start,
        OwnerOpportunity.created_at < end,
    )
    new_count = int(
        session.scalar(select(func.count()).select_from(OwnerOpportunity).where(*new_filters))
        or 0
    )
    opportunity_ids = list(
        session.scalars(
            select(OwnerOpportunity.id)
            .where(*new_filters)
            .order_by(OwnerOpportunity.created_at.desc(), OwnerOpportunity.id.desc())
            .limit(MAX_DAILY_DIGEST_ASSESSMENTS)
        )
    )

    items = []
    if opportunity_ids:
        assessment_context = _build_assessment_context(
            session,
            owner_id=owner_id,
            keyring=keyring,
            selected_saved_search_id=None,
        )
        candidates = _recommended_today_candidates(
            session,
            filters=[
                OwnerOpportunity.owner_id == owner_id,
                OwnerOpportunity.id.in_(opportunity_ids),
            ],
            snapshot_at=current,
            assessment_context=assessment_context,
        )
        items = _recommended_today_items(
            session,
            candidates=candidates,
            keyring=keyring,
        )

    worth = [item for item in items if _worth_your_time(item)]
    worth.sort(key=_highlight_sort_key)
    highlights = [
        DailyDigestHighlight(
            opportunity_id=item.id,
            company=item.posting.company,
            title=item.posting.title,
            fit_band=item.match.fit_band,
            confidence=item.match.confidence,
            reasons=_digest_reasons(item),
            discovered_at=item.created_at,
        )
        for item in worth[:MAX_DAILY_DIGEST_HIGHLIGHTS]
    ]

    scan_rows = list(
        session.scalars(
            select(OpportunityScan).where(
                OpportunityScan.owner_id == owner_id,
                OpportunityScan.trigger == "scheduled",
                OpportunityScan.scheduled_for >= start,
                OpportunityScan.scheduled_for < tomorrow,
            )
        )
    )
    scan_summary = DailyDigestScanSummary(
        scheduled=len(scan_rows),
        running=sum(row.status in {"queued", "running"} for row in scan_rows),
        succeeded=sum(row.status == "succeeded" for row in scan_rows),
        partial=sum(row.status == "partial" for row in scan_rows),
        failed=sum(row.status in {"failed", "cancelled"} for row in scan_rows),
    )
    active_scheduled_searches = int(
        session.scalar(
            select(func.count())
            .select_from(SavedSearch)
            .where(
                SavedSearch.owner_id == owner_id,
                SavedSearch.active.is_(True),
                SavedSearch.cadence != "manual",
                SavedSearch.next_scan_at.is_not(None),
            )
        )
        or 0
    )
    next_scan_at = session.scalar(
        select(func.min(SavedSearch.next_scan_at)).where(
            SavedSearch.owner_id == owner_id,
            SavedSearch.active.is_(True),
            SavedSearch.cadence != "manual",
            SavedSearch.next_scan_at.is_not(None),
        )
    )
    evaluated_count = len(items)
    return DailyDigestResponse(
        local_date=owner_local_date,
        timezone=owner_timezone,
        period_started_at=start,
        generated_at=current,
        headline=_digest_headline(
            new_count=new_count,
            worth_count=len(worth),
            assessment_complete=evaluated_count == new_count,
        ),
        new_opportunities=new_count,
        evaluated_opportunities=evaluated_count,
        worth_your_time=len(worth),
        assessment_complete=evaluated_count == new_count,
        highlights=highlights,
        scans=scan_summary,
        active_scheduled_searches=active_scheduled_searches,
        next_scan_at=_as_utc(next_scan_at) if next_scan_at is not None else None,
    )


def _worth_your_time(item) -> bool:
    return bool(
        item.posting.state is PostingState.open
        and item.state is not OpportunityDecisionState.dismiss
        and item.match.state is MatchAssessmentState.assessed
        and item.match.eligibility is OpportunityEligibility.eligible
        and item.match.fit_band
        in {OpportunityFitBand.strong, OpportunityFitBand.promising}
    )


def _digest_reasons(item) -> list[str]:
    reasons = list(item.match.strengths[:3])
    if reasons:
        return reasons
    if item.match.matched_terms:
        terms = ", ".join(item.match.matched_terms[:3])
        return [f"Matches terms in your saved profile: {terms}."[:200].rstrip()]
    return ["Clears your saved role, eligibility, and fit gates."]


def _highlight_sort_key(item) -> tuple[int, int, float, str]:
    band = 0 if item.match.fit_band is OpportunityFitBand.strong else 1
    confidence = {
        AssessmentConfidence.high: 0,
        AssessmentConfidence.medium: 1,
        AssessmentConfidence.low: 2,
    }.get(item.match.confidence, 3)
    return (band, confidence, -item.created_at.timestamp(), item.id)


def _digest_headline(
    *,
    new_count: int,
    worth_count: int,
    assessment_complete: bool,
) -> str:
    role_word = "role" if new_count == 1 else "roles"
    if assessment_complete:
        return f"{new_count} new {role_word}, {worth_count} worth your time"
    return f"{new_count} new {role_word}, at least {worth_count} worth your time"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "MAX_DAILY_DIGEST_ASSESSMENTS",
    "build_daily_digest",
]
