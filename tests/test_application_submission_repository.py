"""Repository tests for atomic ready-to-apply and applied transitions."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

import job_hunt_agent.application_submission_repository as repository
from job_hunt_agent.application_artifact_schemas import (
    ApplicationArtifactBlocker,
    ApplicationArtifactStatus,
)
from job_hunt_agent.application_pack_schemas import (
    ApplicationPackBlocker,
    ApplicationPackStatus,
)
from job_hunt_agent.application_submission_schemas import (
    AppliedTransitionCreate,
    ReadyToApplyTransitionCreate,
)
from job_hunt_agent.models import ActionItem, Application, ApplicationActivityEvent
from job_hunt_agent.models.application_submission import ApplicationSubmission
from job_hunt_agent.models.opportunity import JobPosting, JobPostingVersion
from job_hunt_agent.repository_errors import ResourceConflict, VersionConflict
from job_hunt_agent.security import load_data_keyring
from tests.test_application_submission_models import NOW, submission_db


DESTINATION = "https://careers.example.com/jobs/1/apply"
EXACT_IDS = {
    "application_pack_id": "pack1",
    "application_pack_revision_id": "grounding1",
    "application_pack_review_event_id": "groundingreview1",
    "application_artifact_revision_id": "artifact1",
    "application_artifact_approval_event_id": "artifactapproval1",
    "tailored_resume_version_id": "resume2",
}


def _reset_to_pursuing(database) -> None:
    with database.session() as session:
        for event in session.scalars(
            select(ApplicationActivityEvent).where(
                ApplicationActivityEvent.sequence_number > 1
            )
        ):
            session.delete(event)
        session.flush()
        submission = session.get(ApplicationSubmission, "submission1")
        if submission is not None:
            session.delete(submission)
        session.flush()
        for action in session.scalars(
            select(ActionItem).where(
                ActionItem.kind.in_(("submit_application", "follow_up_application"))
            )
        ):
            session.delete(action)
        initial = session.get(ActionItem, "action1")
        assert initial is not None
        initial.status = "open"
        initial.completed_at = None
        initial.version = 1
        initial.updated_at = NOW
        application = session.get(Application, "application1")
        assert application is not None
        application.stage = "pursuing"
        application.version = 1
        application.updated_at = NOW


def _install_approved_projections(
    monkeypatch: pytest.MonkeyPatch,
    *,
    first_party: bool = True,
) -> None:
    def posting_state(session, *, application, lock):
        del lock
        posting = session.get(JobPosting, application.job_posting_id)
        version = session.get(JobPostingVersion, application.pursued_posting_version_id)
        assert posting is not None and version is not None
        return posting, version, [DESTINATION], first_party

    monkeypatch.setattr(repository, "_posting_state", posting_state)
    monkeypatch.setattr(
        repository,
        "load_application_pack",
        lambda *args, **kwargs: SimpleNamespace(
            status=ApplicationPackStatus.reviewed,
            pack=SimpleNamespace(id="pack1"),
            current_revision=SimpleNamespace(id="grounding1"),
            review_event=SimpleNamespace(id="groundingreview1"),
            blockers=[],
        ),
    )
    monkeypatch.setattr(
        repository,
        "load_application_artifacts",
        lambda *args, **kwargs: SimpleNamespace(
            status=ApplicationArtifactStatus.approved,
            pack=SimpleNamespace(id="pack1"),
            current_revision=SimpleNamespace(id="artifact1"),
            approved_revision=SimpleNamespace(id="artifact1"),
            current_event=SimpleNamespace(id="artifactapproval1"),
            approval_event=SimpleNamespace(id="artifactapproval1"),
            tailored_resume_version=SimpleNamespace(id="resume2"),
            blockers=[],
        ),
    )


def _ready() -> ReadyToApplyTransitionCreate:
    return ReadyToApplyTransitionCreate(
        to_stage="ready_to_apply",
        **EXACT_IDS,
        next_action_due_on=date(2026, 7, 15),
        confirm_ready=True,
    )


def _applied(**updates) -> AppliedTransitionCreate:
    values = {
        "to_stage": "applied",
        **EXACT_IDS,
        "destination_url": DESTINATION,
        "applied_on": date(2026, 7, 14),
        "next_action_due_on": date(2026, 7, 21),
        "confirm_manual_submission": True,
    }
    values.update(updates)
    return AppliedTransitionCreate(**values)


def test_ready_and_applied_are_atomic_reloadable_and_idempotent(
    submission_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_to_pursuing(submission_db)
    _install_approved_projections(monkeypatch)
    keyring = load_data_keyring(production=False)

    with submission_db.session() as session:
        ready = repository.transition_application(
            session,
            owner_id="owner1",
            application_id="application1",
            payload=_ready(),
            expected_application_version=1,
            idempotency_key="ready-once",
            keyring=keyring,
            now=NOW + timedelta(hours=1),
        )
        assert ready is not None and ready.application.stage.value == "ready_to_apply"
        assert ready.activity_event.sequence_number == 2
        replay = repository.transition_application(
            session,
            owner_id="owner1",
            application_id="application1",
            payload=_ready(),
            expected_application_version=1,
            idempotency_key="ready-once",
            keyring=keyring,
            now=NOW + timedelta(hours=1, minutes=1),
        )
        assert replay is not None and replay.transition_created is False
        assert replay.application.version == 2

    with submission_db.session() as session:
        applied = repository.transition_application(
            session,
            owner_id="owner1",
            application_id="application1",
            payload=_applied(),
            expected_application_version=2,
            idempotency_key="applied-once",
            keyring=keyring,
            now=NOW + timedelta(hours=2),
        )
        assert applied is not None and applied.application.stage.value == "applied"
        assert applied.submission is not None
        assert applied.submission.destination_url == DESTINATION
        assert applied.activity_event.submission_id == applied.submission.id
        replay = repository.transition_application(
            session,
            owner_id="owner1",
            application_id="application1",
            payload=_applied(),
            expected_application_version=2,
            idempotency_key="applied-once",
            keyring=keyring,
            now=NOW + timedelta(hours=2, minutes=1),
        )
        assert replay is not None and replay.transition_created is False
        with pytest.raises(ResourceConflict, match="progressed"):
            repository.transition_application(
                session,
                owner_id="owner1",
                application_id="application1",
                payload=_ready(),
                expected_application_version=1,
                idempotency_key="ready-once",
                keyring=keyring,
                now=NOW + timedelta(hours=2, minutes=2),
            )

    with submission_db.session() as session:
        projection = repository.load_application_submission(
            session,
            owner_id="owner1",
            application_id="application1",
        )
        assert projection is not None and projection.stage.value == "applied"
        assert projection.available_destinations == [DESTINATION]
        assert projection.first_party_verified is True
        assert projection.submission is not None
        assert session.scalar(select(func.count(ApplicationSubmission.id))) == 1
        assert session.scalar(select(func.count(ApplicationActivityEvent.id))) == 3
        open_actions = list(
            session.scalars(
                select(ActionItem).where(
                    ActionItem.application_id == "application1",
                    ActionItem.status == "open",
                )
            )
        )
        assert [(item.kind, item.version) for item in open_actions] == [
            ("follow_up_application", 1)
        ]


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        ("stale_version", VersionConflict),
        ("first_party", ResourceConflict),
        ("materials", ResourceConflict),
    ],
)
def test_ready_failures_leave_the_application_graph_unchanged(
    submission_db,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    error: type[Exception],
) -> None:
    _reset_to_pursuing(submission_db)
    _install_approved_projections(
        monkeypatch,
        first_party=failure != "first_party",
    )
    payload = _ready()
    if failure == "materials":
        payload = payload.model_copy(
            update={"application_artifact_revision_id": "missingartifact"}
        )
    expected_version = 99 if failure == "stale_version" else 1

    with pytest.raises(error):
        with submission_db.session() as session:
            repository.transition_application(
                session,
                owner_id="owner1",
                application_id="application1",
                payload=payload,
                expected_application_version=expected_version,
                idempotency_key=f"ready-{failure}",
                keyring=load_data_keyring(production=False),
                now=NOW + timedelta(hours=1),
            )

    with submission_db.session() as session:
        application = session.get(Application, "application1")
        assert application is not None
        assert (application.stage, application.version) == ("pursuing", 1)
        assert session.scalar(select(func.count(ApplicationActivityEvent.id))) == 1
        assert session.scalar(select(func.count(ApplicationSubmission.id))) == 0
        open_action = session.scalar(
            select(ActionItem).where(ActionItem.status == "open")
        )
        assert open_action is not None
        assert open_action.kind == "review_and_prepare_application"


def test_invalid_destination_rolls_back_only_the_applied_transition(
    submission_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_to_pursuing(submission_db)
    _install_approved_projections(monkeypatch)
    keyring = load_data_keyring(production=False)
    with submission_db.session() as session:
        repository.transition_application(
            session,
            owner_id="owner1",
            application_id="application1",
            payload=_ready(),
            expected_application_version=1,
            idempotency_key="ready-before-invalid-destination",
            keyring=keyring,
            now=NOW + timedelta(hours=1),
        )

    with pytest.raises(ResourceConflict, match="destination_url"):
        with submission_db.session() as session:
            repository.transition_application(
                session,
                owner_id="owner1",
                application_id="application1",
                payload=_applied(
                    destination_url="https://careers.example.com/jobs/another"
                ),
                expected_application_version=2,
                idempotency_key="invalid-destination",
                keyring=keyring,
                now=NOW + timedelta(hours=2),
            )

    with submission_db.session() as session:
        application = session.get(Application, "application1")
        assert application is not None
        assert (application.stage, application.version) == ("ready_to_apply", 2)
        assert session.scalar(select(func.count(ApplicationActivityEvent.id))) == 2
        assert session.scalar(select(func.count(ApplicationSubmission.id))) == 0
        action = session.scalar(select(ActionItem).where(ActionItem.status == "open"))
        assert action is not None and action.kind == "submit_application"


def test_applied_accepts_exact_frozen_materials_after_evidence_freshness_changes(
    submission_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_to_pursuing(submission_db)
    _install_approved_projections(monkeypatch)
    keyring = load_data_keyring(production=False)
    with submission_db.session() as session:
        ready = repository.transition_application(
            session,
            owner_id="owner1",
            application_id="application1",
            payload=_ready(),
            expected_application_version=1,
            idempotency_key="ready-before-evidence-retired",
            keyring=keyring,
            now=NOW + timedelta(hours=1),
        )
        assert ready is not None

    monkeypatch.setattr(
        repository,
        "load_application_pack",
        lambda *args, **kwargs: SimpleNamespace(
            status=ApplicationPackStatus.reviewed,
            pack=SimpleNamespace(id="pack1"),
            current_revision=SimpleNamespace(id="grounding1"),
            review_event=SimpleNamespace(id="groundingreview1"),
            blockers=[ApplicationPackBlocker.mapped_evidence_changed],
        ),
    )
    monkeypatch.setattr(
        repository,
        "load_application_artifacts",
        lambda *args, **kwargs: SimpleNamespace(
            status=ApplicationArtifactStatus.approved,
            pack=SimpleNamespace(id="pack1"),
            current_revision=SimpleNamespace(id="artifact1"),
            approved_revision=SimpleNamespace(id="artifact1"),
            current_event=SimpleNamespace(id="artifactapproval1"),
            approval_event=SimpleNamespace(id="artifactapproval1"),
            tailored_resume_version=SimpleNamespace(id="resume2"),
            blockers=[ApplicationArtifactBlocker.grounding_evidence_changed],
        ),
    )

    with submission_db.session() as session:
        applied = repository.transition_application(
            session,
            owner_id="owner1",
            application_id="application1",
            payload=_applied(),
            expected_application_version=2,
            idempotency_key="applied-after-evidence-retired",
            keyring=keyring,
            now=NOW + timedelta(hours=2),
        )

    assert applied is not None
    assert applied.application.stage.value == "applied"
    assert applied.submission is not None


@pytest.mark.parametrize("stage", ["pursuing", "ready_to_apply"])
def test_submission_projection_fails_closed_for_stage_record_corruption(
    submission_db,
    stage: str,
) -> None:
    with pytest.raises(ValidationError):
        with submission_db.session() as session:
            application = session.get(Application, "application1")
            assert application is not None
            application.stage = stage
            session.flush()
            repository.load_application_submission(
                session,
                owner_id="owner1",
                application_id="application1",
            )

    _reset_to_pursuing(submission_db)
    with submission_db.session() as session:
        application = session.get(Application, "application1")
        assert application is not None
        application.stage = "applied"
        session.flush()
        with pytest.raises(ValidationError):
            repository.load_application_submission(
                session,
                owner_id="owner1",
                application_id="application1",
            )
