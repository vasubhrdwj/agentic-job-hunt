"""Database invariants for append-only milestone-date correction chains."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    ApplicationActivityEvent,
    ApplicationMilestoneCorrection,
)
from tests.test_application_submission_models import NOW, submission_db


ORIGINAL_ON = date(2026, 7, 15)
FIRST_CORRECTED_ON = date(2026, 7, 14)
SECOND_CORRECTED_ON = date(2026, 7, 13)


def _milestone(
    event_id: str = "milestone1",
    *,
    sequence: int = 4,
    event_type: str = "application_screening",
) -> ApplicationActivityEvent:
    from_stage, to_stage = {
        "application_screening": ("applied", "screening"),
        "application_interviewing": ("applied", "interviewing"),
        "application_offer": ("applied", "offer"),
    }[event_type]
    return ApplicationActivityEvent(
        id=event_id,
        owner_id="owner1",
        application_id="application1",
        sequence_number=sequence,
        event_type=event_type,
        from_stage=from_stage,
        to_stage=to_stage,
        action_item_id="action1",
        previous_action_item_id="action3",
        effective_on=ORIGINAL_ON,
        occurred_at=NOW + timedelta(days=sequence),
        created_at=NOW + timedelta(days=sequence),
    )


def _correction(
    correction_id: str = "correction1",
    *,
    event_id: str = "milestone1",
    owner_id: str = "owner1",
    application_id: str = "application1",
    number: int = 1,
    supersedes_id: str | None = None,
    previous_on: date = ORIGINAL_ON,
    corrected_on: date = FIRST_CORRECTED_ON,
    recording_method: str = "manual",
) -> ApplicationMilestoneCorrection:
    recorded_at = NOW + timedelta(days=10, minutes=number)
    return ApplicationMilestoneCorrection(
        id=correction_id,
        owner_id=owner_id,
        application_id=application_id,
        activity_event_id=event_id,
        correction_number=number,
        supersedes_correction_id=supersedes_id,
        previous_effective_on=previous_on,
        corrected_effective_on=corrected_on,
        recording_method=recording_method,
        recorded_at=recorded_at,
        created_at=recorded_at,
    )


def test_corrections_persist_one_exact_append_only_chain(
    submission_db: Database,
) -> None:
    with submission_db.session() as session:
        session.add(_milestone())
        session.flush()
        session.add(_correction())
        session.flush()
        session.add(
            _correction(
                "correction2",
                number=2,
                supersedes_id="correction1",
                previous_on=FIRST_CORRECTED_ON,
                corrected_on=SECOND_CORRECTED_ON,
            )
        )

    with submission_db.session() as session:
        rows = list(
            session.scalars(
                select(ApplicationMilestoneCorrection).order_by(
                    ApplicationMilestoneCorrection.correction_number
                )
            )
        )
        assert [item.id for item in rows] == ["correction1", "correction2"]
        assert rows[1].supersedes_correction_id == rows[0].id
        assert rows[1].previous_effective_on == rows[0].corrected_effective_on
        milestone = session.get(ApplicationActivityEvent, "milestone1")
        assert milestone is not None and milestone.effective_on == ORIGINAL_ON
        assert "version" not in ApplicationMilestoneCorrection.__table__.columns
        assert "updated_at" not in ApplicationMilestoneCorrection.__table__.columns


@pytest.mark.parametrize(
    "values",
    [
        {"number": 0},
        {"number": 51},
        {"number": 1, "supersedes_id": "missingcorrection"},
        {"number": 2, "supersedes_id": None},
        {"corrected_on": ORIGINAL_ON},
        {"recording_method": "automatic"},
    ],
)
def test_correction_number_chain_date_and_recording_shape_are_enforced(
    submission_db: Database,
    values: dict[str, object],
) -> None:
    with submission_db.session() as session:
        session.add(_milestone())

    with pytest.raises(IntegrityError):
        with submission_db.session() as session:
            session.add(_correction(**values))


@pytest.mark.parametrize(
    "values",
    [
        {"event_id": "missingmilestone"},
        {"owner_id": "missingowner"},
        {"application_id": "missingapplication"},
        {"number": 2, "supersedes_id": "missingcorrection"},
    ],
)
def test_correction_requires_exact_owner_application_activity_and_predecessor(
    submission_db: Database,
    values: dict[str, object],
) -> None:
    with submission_db.session() as session:
        session.add(_milestone())

    with pytest.raises(IntegrityError):
        with submission_db.session() as session:
            session.add(_correction(**values))


def test_correction_predecessor_cannot_cross_milestone_roots(
    submission_db: Database,
) -> None:
    with submission_db.session() as session:
        session.add(_milestone())
        session.add(
            _milestone(
                "milestone2",
                sequence=5,
                event_type="application_offer",
            )
        )
        session.flush()
        session.add(_correction())

    with pytest.raises(IntegrityError):
        with submission_db.session() as session:
            session.add(
                _correction(
                    "crossroot",
                    event_id="milestone2",
                    number=2,
                    supersedes_id="correction1",
                    previous_on=ORIGINAL_ON,
                    corrected_on=FIRST_CORRECTED_ON,
                )
            )


def test_correction_number_and_superseded_leaf_are_unique(
    submission_db: Database,
) -> None:
    with submission_db.session() as session:
        session.add(_milestone())
        session.flush()
        session.add(_correction())

    with pytest.raises(IntegrityError):
        with submission_db.session() as session:
            session.add(_correction("duplicatenumber"))

    with submission_db.session() as session:
        session.add(
            _correction(
                "correction2",
                number=2,
                supersedes_id="correction1",
                previous_on=FIRST_CORRECTED_ON,
                corrected_on=SECOND_CORRECTED_ON,
            )
        )

    with pytest.raises(IntegrityError):
        with submission_db.session() as session:
            session.add(
                _correction(
                    "branchedcorrection",
                    number=3,
                    supersedes_id="correction1",
                    previous_on=FIRST_CORRECTED_ON,
                    corrected_on=date(2026, 7, 12),
                )
            )


def test_deleting_target_activity_cascades_its_corrections(
    submission_db: Database,
) -> None:
    with submission_db.session() as session:
        session.add(_milestone())
        session.flush()
        session.add(_correction())

    with submission_db.session() as session:
        milestone = session.get(ApplicationActivityEvent, "milestone1")
        assert milestone is not None
        session.delete(milestone)

    with submission_db.session() as session:
        assert session.scalar(
            select(func.count(ApplicationMilestoneCorrection.id))
        ) == 0
