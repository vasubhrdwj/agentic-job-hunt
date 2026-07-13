"""Owner-scoped saved-search persistence and timezone schedule calculations."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .job_queue import utcnow
from .models import (
    CareerTrack,
    OpportunityScan,
    ResumeVersion,
    SavedSearch,
    SavedSearchMatch,
)
from .profile_schemas import SavedSearchCreate, SavedSearchResponse, SavedSearchSchedule
from .repository_errors import ResourceConflict, ResourceInUse, require_version


_DAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def create_saved_search(
    session: Session,
    *,
    owner_id: str,
    payload: SavedSearchCreate,
    now: datetime | None = None,
) -> SavedSearchResponse:
    current = now or utcnow()
    track = _owner_track(session, owner_id, payload.career_track_id)
    if track is None:
        raise ValueError("career_track_id does not exist for owner")
    resume = _resolve_resume(session, owner_id, payload.resume_version_id)
    if resume is None:
        raise ValueError("resume_version_id is required when no base resume exists")
    _validate_search_links(track, resume, payload)
    if session.scalar(
        select(SavedSearch.id).where(
            SavedSearch.owner_id == owner_id,
            SavedSearch.name == payload.name,
        )
    ) is not None:
        raise ResourceConflict("saved search name is already in use")
    next_scan_at = (
        calculate_next_scan_at(payload.schedule, after=current)
        if payload.active and payload.schedule.cadence != "manual"
        else None
    )
    row = SavedSearch(
        owner_id=owner_id,
        career_track_id=track.id,
        resume_version_id=resume.id,
        name=payload.name,
        criteria_schema_version=1,
        criteria=payload.criteria.model_dump(mode="json"),
        pack=payload.pack,
        use_self_rag=payload.use_self_rag,
        cadence=payload.schedule.cadence,
        schedule=_schedule_json(payload.schedule),
        timezone=payload.schedule.timezone,
        active=payload.active,
        next_scan_at=next_scan_at,
        version=1,
        created_at=current,
        updated_at=current,
    )
    session.add(row)
    session.flush()
    return _saved_search_response(row)


def list_saved_searches(session: Session, *, owner_id: str) -> list[SavedSearchResponse]:
    rows = session.scalars(
        select(SavedSearch)
        .where(SavedSearch.owner_id == owner_id)
        .order_by(SavedSearch.created_at, SavedSearch.id)
    )
    return [_saved_search_response(row) for row in rows]


def load_saved_search(
    session: Session, *, owner_id: str, saved_search_id: str
) -> SavedSearchResponse | None:
    row = _owner_search(session, owner_id, saved_search_id)
    return _saved_search_response(row) if row is not None else None


def update_saved_search(
    session: Session,
    *,
    owner_id: str,
    saved_search_id: str,
    payload: SavedSearchCreate,
    expected_version: int,
    reschedule: bool = True,
    now: datetime | None = None,
) -> SavedSearchResponse | None:
    current = now or utcnow()
    row = session.scalar(
        select(SavedSearch)
        .where(SavedSearch.owner_id == owner_id, SavedSearch.id == saved_search_id)
        .with_for_update()
    )
    if row is None:
        return None
    require_version("saved_search", row.id, expected=expected_version, actual=row.version)
    track = _owner_track(session, owner_id, payload.career_track_id)
    resume = _resolve_resume(session, owner_id, payload.resume_version_id)
    if track is None or resume is None:
        raise ValueError("saved search references an unavailable owner resource")
    _validate_search_links(track, resume, payload)
    duplicate = session.scalar(
        select(SavedSearch.id).where(
            SavedSearch.owner_id == owner_id,
            SavedSearch.name == payload.name,
            SavedSearch.id != row.id,
        )
    )
    if duplicate is not None:
        raise ResourceConflict("saved search name is already in use")
    row.career_track_id = track.id
    row.resume_version_id = resume.id
    row.name = payload.name
    row.criteria = payload.criteria.model_dump(mode="json")
    row.pack = payload.pack
    row.use_self_rag = payload.use_self_rag
    row.cadence = payload.schedule.cadence
    row.schedule = _schedule_json(payload.schedule)
    row.timezone = payload.schedule.timezone
    row.active = payload.active
    if reschedule:
        row.next_scan_at = (
            calculate_next_scan_at(payload.schedule, after=current)
            if payload.active and payload.schedule.cadence != "manual"
            else None
        )
    row.version += 1
    row.updated_at = current
    session.flush()
    return _saved_search_response(row)


def delete_saved_search(
    session: Session,
    *,
    owner_id: str,
    saved_search_id: str,
    expected_version: int,
) -> bool:
    row = session.scalar(
        select(SavedSearch)
        .where(SavedSearch.owner_id == owner_id, SavedSearch.id == saved_search_id)
        .with_for_update()
    )
    if row is None:
        return False
    require_version("saved_search", row.id, expected=expected_version, actual=row.version)
    has_history = session.scalar(
        select(OpportunityScan.id)
        .where(
            OpportunityScan.owner_id == owner_id,
            OpportunityScan.saved_search_id == saved_search_id,
        )
        .limit(1)
    ) or session.scalar(
        select(SavedSearchMatch.id)
        .where(
            SavedSearchMatch.owner_id == owner_id,
            SavedSearchMatch.saved_search_id == saved_search_id,
        )
        .limit(1)
    )
    if has_history is not None:
        raise ResourceInUse(
            "saved search has scan history; deactivate it to preserve opportunity provenance"
        )
    session.delete(row)
    session.flush()
    return True


def mark_saved_search_scanned(
    session: Session,
    *,
    owner_id: str,
    saved_search_id: str,
    expected_version: int,
    scanned_at: datetime | None = None,
) -> SavedSearchResponse | None:
    current = scanned_at or utcnow()
    row = session.scalar(
        select(SavedSearch)
        .where(SavedSearch.owner_id == owner_id, SavedSearch.id == saved_search_id)
        .with_for_update()
    )
    if row is None:
        return None
    require_version("saved_search", row.id, expected=expected_version, actual=row.version)
    row.last_scan_at = current
    row.next_scan_at = (
        calculate_next_scan_at(_schedule_from_row(row), after=current)
        if row.active and row.cadence != "manual"
        else None
    )
    row.version += 1
    row.updated_at = current
    session.flush()
    return _saved_search_response(row)


def list_due_saved_searches(
    session: Session, *, now: datetime | None = None, limit: int = 100
) -> list[SavedSearchResponse]:
    """Read due schedules only; Slice 1 deliberately enqueues no scan jobs."""

    current = now or utcnow()
    rows = session.scalars(
        select(SavedSearch)
        .where(
            SavedSearch.active.is_(True),
            SavedSearch.cadence != "manual",
            SavedSearch.next_scan_at.is_not(None),
            SavedSearch.next_scan_at <= current,
        )
        .order_by(SavedSearch.next_scan_at, SavedSearch.id)
        .limit(max(1, min(limit, 1_000)))
    )
    return [_saved_search_response(row) for row in rows]


def calculate_next_scan_at(
    schedule: SavedSearchSchedule,
    *,
    after: datetime,
) -> datetime | None:
    if schedule.cadence == "manual":
        return None
    zone = ZoneInfo(schedule.timezone)
    after_utc = _as_utc(after)
    after_local = after_utc.astimezone(zone)
    assert schedule.local_time is not None
    allowed_days = _allowed_weekdays(schedule)
    for offset in range(0, 15):
        candidate_date = after_local.date() + timedelta(days=offset)
        if candidate_date.weekday() not in allowed_days:
            continue
        candidate = _resolve_local_slot(candidate_date, schedule.local_time, zone)
        if candidate.astimezone(timezone.utc) > after_utc:
            return candidate.astimezone(timezone.utc)
    raise ValueError("schedule has no next slot within two weeks")


def _saved_search_response(row: SavedSearch) -> SavedSearchResponse:
    return SavedSearchResponse(
        id=row.id,
        name=row.name,
        career_track_id=row.career_track_id,
        resume_version_id=row.resume_version_id,
        criteria=row.criteria,
        schedule=_schedule_from_row(row),
        pack=row.pack,
        use_self_rag=row.use_self_rag,
        active=row.active,
        last_scan_at=_optional_utc(row.last_scan_at),
        next_scan_at=_optional_utc(row.next_scan_at),
        version=row.version,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
    )


def _schedule_json(schedule: SavedSearchSchedule) -> dict[str, object]:
    return {
        "local_time": schedule.local_time.strftime("%H:%M") if schedule.local_time else None,
        "days_of_week": list(schedule.days_of_week),
    }


def _schedule_from_row(row: SavedSearch) -> SavedSearchSchedule:
    local_time_value = row.schedule.get("local_time")
    return SavedSearchSchedule(
        cadence=row.cadence,
        timezone=row.timezone,
        local_time=time.fromisoformat(local_time_value) if local_time_value else None,
        days_of_week=row.schedule.get("days_of_week", []),
    )


def _owner_search(session: Session, owner_id: str, search_id: str) -> SavedSearch | None:
    return session.scalar(
        select(SavedSearch).where(SavedSearch.owner_id == owner_id, SavedSearch.id == search_id)
    )


def _owner_track(session: Session, owner_id: str, track_id: str) -> CareerTrack | None:
    return session.scalar(
        select(CareerTrack).where(CareerTrack.owner_id == owner_id, CareerTrack.id == track_id)
    )


def _resolve_resume(
    session: Session, owner_id: str, resume_id: str | None
) -> ResumeVersion | None:
    statement = select(ResumeVersion).where(ResumeVersion.owner_id == owner_id)
    statement = (
        statement.where(ResumeVersion.id == resume_id)
        if resume_id is not None
        else statement.where(ResumeVersion.is_base.is_(True))
    )
    return session.scalar(statement)


def _validate_search_links(
    track: CareerTrack, resume: ResumeVersion, payload: SavedSearchCreate
) -> None:
    if payload.active and not track.active:
        raise ResourceConflict("active saved search requires an active career track")
    if payload.criteria.seniority not in track.seniority_levels:
        raise ValueError("saved search seniority must be enabled by its career track")
    if resume.owner_id != track.owner_id:
        raise ValueError("saved search resources must belong to one owner")


def _allowed_weekdays(schedule: SavedSearchSchedule) -> set[int]:
    if schedule.cadence == "daily":
        return set(range(7))
    if schedule.cadence == "weekdays":
        return set(range(5))
    return {_DAY_INDEX[day] for day in schedule.days_of_week}


def _resolve_local_slot(day: date, local_time: time, zone: ZoneInfo) -> datetime:
    naive = datetime.combine(day, local_time)
    for minutes in range(0, 181):
        local_probe = naive + timedelta(minutes=minutes)
        candidate = local_probe.replace(tzinfo=zone, fold=0)
        round_trip = candidate.astimezone(timezone.utc).astimezone(zone)
        if round_trip.replace(tzinfo=None) == local_probe:
            # Ambiguous fall-back times deliberately use fold=0, producing one
            # scheduled slot. Spring-forward gaps advance to the first valid
            # local minute rather than drifting by the gap length.
            return candidate
    raise ValueError("timezone has no valid local time near scheduled slot")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_utc(value: datetime | None) -> datetime | None:
    return _as_utc(value) if value is not None else None


__all__ = [
    "calculate_next_scan_at",
    "create_saved_search",
    "delete_saved_search",
    "list_due_saved_searches",
    "list_saved_searches",
    "load_saved_search",
    "mark_saved_search_scanned",
    "update_saved_search",
]
