"""Strict transport contracts for append-only milestone date corrections."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from job_hunt_agent.application_schemas import (
    ApplicationActivityEventResponse,
    ApplicationMilestoneCorrectionCreate,
    ApplicationMilestoneCorrectionResponse,
)


NOW = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)


def _correction(
    correction_number: int = 1,
    *,
    correction_id: str = "correction1",
    supersedes: str | None = None,
    previous_on: date = date(2026, 7, 15),
    corrected_on: date = date(2026, 7, 14),
    recorded_at: datetime = NOW,
) -> ApplicationMilestoneCorrectionResponse:
    return ApplicationMilestoneCorrectionResponse(
        id=correction_id,
        application_id="application1",
        activity_event_id="screeningevent1",
        correction_number=correction_number,
        supersedes_correction_id=supersedes,
        previous_effective_on=previous_on,
        corrected_effective_on=corrected_on,
        recording_method="manual",
        recorded_at=recorded_at,
        created_at=recorded_at,
    )


def _screening_event(
    *,
    corrections: list[ApplicationMilestoneCorrectionResponse] | None = None,
    resolved_on: date | None = None,
    interview_round_id: str | None = None,
) -> ApplicationActivityEventResponse:
    return ApplicationActivityEventResponse(
        id="screeningevent1",
        application_id="application1",
        sequence_number=4,
        event_type="application_screening",
        from_stage="applied",
        to_stage="screening",
        action_item_id="screeningaction1",
        previous_action_item_id="action3",
        effective_on=date(2026, 7, 15),
        resolved_effective_on=(
            date(2026, 7, 15) if resolved_on is None else resolved_on
        ),
        interview_round_id=interview_round_id,
        occurred_at=NOW,
        corrections=corrections or [],
    )


def test_correction_create_is_date_only_confirmed_and_fail_closed() -> None:
    payload = ApplicationMilestoneCorrectionCreate.model_validate(
        {
            "corrected_effective_on": "2026-07-14",
            "confirm_correction": True,
        }
    )

    assert payload.corrected_effective_on == date(2026, 7, 14)
    assert payload.model_dump(mode="json") == {
        "corrected_effective_on": "2026-07-14",
        "confirm_correction": True,
    }
    for invalid in (False, 1, "true"):
        with pytest.raises(ValidationError):
            ApplicationMilestoneCorrectionCreate.model_validate(
                {
                    "corrected_effective_on": "2026-07-14",
                    "confirm_correction": invalid,
                }
            )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ApplicationMilestoneCorrectionCreate.model_validate(
            {
                "corrected_effective_on": "2026-07-14",
                "confirm_correction": True,
                "outcome": "rejected",
            }
        )


def test_activity_exposes_original_date_and_one_continuous_correction_chain() -> None:
    first = _correction()
    second = _correction(
        2,
        correction_id="correction2",
        supersedes=first.id,
        previous_on=first.corrected_effective_on,
        corrected_on=date(2026, 7, 13),
        recorded_at=NOW + timedelta(minutes=1),
    )

    activity = _screening_event(
        corrections=[first, second],
        resolved_on=second.corrected_effective_on,
    )

    assert activity.effective_on == date(2026, 7, 15)
    assert activity.resolved_effective_on == date(2026, 7, 13)
    assert [item.correction_number for item in activity.corrections] == [1, 2]
    assert activity.corrections[1].supersedes_correction_id == first.id


@pytest.mark.parametrize(
    "corrections,resolved_on,error",
    [
        (
            [
                _correction(
                    2,
                    correction_id="correction2",
                    supersedes="missing",
                )
            ],
            date(2026, 7, 14),
            "ordered, continuous",
        ),
        ([_correction()], date(2026, 7, 13), "latest correction"),
        (
            [
                _correction(),
                _correction(
                    2,
                    correction_id="correction2",
                    supersedes="correction1",
                    previous_on=date(2026, 7, 12),
                    corrected_on=date(2026, 7, 13),
                ),
            ],
            date(2026, 7, 13),
            "ordered, continuous",
        ),
    ],
)
def test_activity_rejects_broken_or_misresolved_correction_chains(
    corrections: list[ApplicationMilestoneCorrectionResponse],
    resolved_on: date,
    error: str,
) -> None:
    with pytest.raises(ValidationError, match=error):
        _screening_event(corrections=corrections, resolved_on=resolved_on)


def test_corrections_cannot_be_attached_to_round_linked_or_terminal_activity() -> None:
    with pytest.raises(ValidationError, match="unlinked"):
        _screening_event(
            corrections=[_correction()],
            resolved_on=date(2026, 7, 14),
            interview_round_id="round1",
        )

    with pytest.raises(ValidationError, match="unlinked"):
        ApplicationActivityEventResponse(
            id="closedevent1",
            application_id="application1",
            sequence_number=5,
            event_type="application_closed",
            from_stage="screening",
            to_stage="closed",
            previous_action_item_id="screeningaction1",
            effective_on=date(2026, 7, 16),
            resolved_effective_on=date(2026, 7, 14),
            outcome_id="outcome1",
            occurred_at=NOW,
            corrections=[
                _correction().model_copy(
                    update={"activity_event_id": "closedevent1"}
                )
            ],
        )


def test_correction_rows_require_manual_changed_monotonic_shape() -> None:
    with pytest.raises(ValidationError, match="first correction"):
        _correction(supersedes="correction0")
    with pytest.raises(ValidationError, match="later corrections"):
        _correction(2)
    with pytest.raises(ValidationError, match="change"):
        _correction(corrected_on=date(2026, 7, 15))
    with pytest.raises(ValidationError, match="recording_method"):
        ApplicationMilestoneCorrectionResponse.model_validate(
            {
                **_correction().model_dump(),
                "recording_method": "automatic",
            }
        )
    with pytest.raises(ValidationError, match="created_at"):
        ApplicationMilestoneCorrectionResponse.model_validate(
            {
                **_correction().model_dump(),
                "created_at": NOW - timedelta(seconds=1),
            }
        )
