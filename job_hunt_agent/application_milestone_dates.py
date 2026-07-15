"""Resolve current milestone dates without rewriting immutable activity."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    ApplicationActivityEvent,
    ApplicationInterviewRound,
    ApplicationMilestoneCorrection,
    ApplicationOutcome,
    ApplicationSubmission,
)


CORRECTABLE_MILESTONE_TYPES = frozenset(
    {
        "application_screening",
        "application_interviewing",
        "application_offer",
    }
)


class ApplicationMilestoneDateError(RuntimeError):
    """A saved milestone correction graph is inconsistent."""


def resolved_application_milestone_dates(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
) -> dict[str, date]:
    """Return each dated activity's latest corrected or original date."""

    activities = list(
        session.scalars(
            select(ApplicationActivityEvent)
            .where(
                ApplicationActivityEvent.owner_id == owner_id,
                ApplicationActivityEvent.application_id == application_id,
                ApplicationActivityEvent.effective_on.is_not(None),
            )
            .order_by(
                ApplicationActivityEvent.sequence_number,
                ApplicationActivityEvent.id,
            )
        )
    )
    activity_by_id = {item.id: item for item in activities}
    resolved = {
        item.id: item.effective_on
        for item in activities
        if item.effective_on is not None
    }
    previous_correction_by_event: dict[str, ApplicationMilestoneCorrection] = {}
    for correction in session.scalars(
        select(ApplicationMilestoneCorrection)
        .where(
            ApplicationMilestoneCorrection.owner_id == owner_id,
            ApplicationMilestoneCorrection.application_id == application_id,
        )
        .order_by(
            ApplicationMilestoneCorrection.activity_event_id,
            ApplicationMilestoneCorrection.correction_number,
            ApplicationMilestoneCorrection.recorded_at,
            ApplicationMilestoneCorrection.id,
        )
    ):
        activity = activity_by_id.get(correction.activity_event_id)
        previous = previous_correction_by_event.get(correction.activity_event_id)
        expected_number = 1 if previous is None else previous.correction_number + 1
        expected_supersedes = None if previous is None else previous.id
        expected_previous_on = (
            activity.effective_on
            if previous is None and activity is not None
            else previous.corrected_effective_on if previous is not None else None
        )
        if (
            activity is None
            or activity.event_type not in CORRECTABLE_MILESTONE_TYPES
            or activity.interview_round_id is not None
            or correction.correction_number != expected_number
            or correction.supersedes_correction_id != expected_supersedes
            or correction.previous_effective_on != expected_previous_on
            or correction.corrected_effective_on == expected_previous_on
            or correction.recording_method != "manual"
            or correction.created_at < correction.recorded_at
            or (
                previous is not None
                and correction.recorded_at < previous.recorded_at
            )
        ):
            raise ApplicationMilestoneDateError(
                "application milestone corrections do not form one valid chain"
            )
        resolved[activity.id] = correction.corrected_effective_on
        previous_correction_by_event[activity.id] = correction
    return resolved


def latest_resolved_milestone_on(
    session: Session,
    *,
    owner_id: str,
    application_id: str,
) -> date | None:
    """Return the latest effective application or completed-round fact."""

    values = list(
        resolved_application_milestone_dates(
            session,
            owner_id=owner_id,
            application_id=application_id,
        ).values()
    )
    values.extend(
        item
        for item in session.scalars(
            select(ApplicationInterviewRound.completed_on).where(
                ApplicationInterviewRound.owner_id == owner_id,
                ApplicationInterviewRound.application_id == application_id,
                ApplicationInterviewRound.status == "completed",
                ApplicationInterviewRound.completed_on.is_not(None),
            )
        )
        if item is not None
    )
    return max(values) if values else None


def milestone_correction_window(
    session: Session,
    *,
    target: ApplicationActivityEvent,
    owner_local_today: date,
) -> tuple[date, date]:
    """Return the inclusive safe date window for one coarse milestone."""

    if (
        target.event_type not in CORRECTABLE_MILESTONE_TYPES
        or target.effective_on is None
        or target.interview_round_id is not None
    ):
        raise ValueError(
            "only an unlinked screening, interviewing, or offer milestone "
            "can have its date corrected"
        )
    submission_on = session.scalar(
        select(ApplicationSubmission.applied_on).where(
            ApplicationSubmission.owner_id == target.owner_id,
            ApplicationSubmission.application_id == target.application_id,
        )
    )
    if submission_on is None:
        raise ApplicationMilestoneDateError(
            "a correctable milestone has no immutable application submission"
        )
    activities = list(
        session.scalars(
            select(ApplicationActivityEvent).where(
                ApplicationActivityEvent.owner_id == target.owner_id,
                ApplicationActivityEvent.application_id == target.application_id,
                ApplicationActivityEvent.effective_on.is_not(None),
            )
        )
    )
    resolved = resolved_application_milestone_dates(
        session,
        owner_id=target.owner_id,
        application_id=target.application_id,
    )
    by_type: dict[str, list[date]] = {}
    for activity in activities:
        if activity.id == target.id:
            continue
        effective_on = resolved.get(activity.id)
        if effective_on is not None:
            by_type.setdefault(activity.event_type, []).append(effective_on)
    completed_rounds = [
        value
        for value in session.scalars(
            select(ApplicationInterviewRound.completed_on).where(
                ApplicationInterviewRound.owner_id == target.owner_id,
                ApplicationInterviewRound.application_id == target.application_id,
                ApplicationInterviewRound.status == "completed",
                ApplicationInterviewRound.completed_on.is_not(None),
            )
        )
        if value is not None
    ]
    outcome_on = session.scalar(
        select(ApplicationOutcome.outcome_on).where(
            ApplicationOutcome.owner_id == target.owner_id,
            ApplicationOutcome.application_id == target.application_id,
        )
    )

    predecessors = [submission_on]
    successors: list[date] = []
    if target.event_type == "application_screening":
        successors.extend(by_type.get("application_interviewing", []))
        successors.extend(completed_rounds)
        successors.extend(by_type.get("application_offer", []))
    elif target.event_type == "application_interviewing":
        predecessors.extend(by_type.get("application_screening", []))
        successors.extend(completed_rounds)
        successors.extend(by_type.get("application_offer", []))
    else:
        predecessors.extend(by_type.get("application_screening", []))
        predecessors.extend(by_type.get("application_interviewing", []))
        predecessors.extend(completed_rounds)
    if outcome_on is not None:
        successors.append(outcome_on)
    successors.extend(by_type.get("application_closed", []))

    not_before = max(predecessors)
    not_after = min([owner_local_today, *successors])
    if not_before > not_after:
        raise ApplicationMilestoneDateError(
            "saved milestone dates cannot form a valid correction window"
        )
    return not_before, not_after


__all__ = [
    "ApplicationMilestoneDateError",
    "CORRECTABLE_MILESTONE_TYPES",
    "latest_resolved_milestone_on",
    "milestone_correction_window",
    "resolved_application_milestone_dates",
]
