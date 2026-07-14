"""Adapter tests for manual application submission persistence."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pytest

import job_hunt_agent.sqlalchemy_application_workspace as workspace_module
from job_hunt_agent.application_submission_repository import (
    ApplicationSubmissionRepositoryError,
)
from job_hunt_agent.application_submission_schemas import (
    ApplicationSubmissionProjection,
    ReadyToApplyTransitionCreate,
)
from job_hunt_agent.mutation_receipts import (
    MutationIdempotencyConflict,
    MutationPending,
)
from job_hunt_agent.owner_workspace import (
    WorkspaceConflict,
    WorkspaceInputError,
    WorkspaceUnavailable,
)
from job_hunt_agent.repository_errors import ResourceConflict, VersionConflict
from job_hunt_agent.security import load_data_keyring
from job_hunt_agent.sqlalchemy_application_workspace import (
    SqlAlchemyApplicationWorkspaceStore,
)


EXACT_IDS = {
    "application_pack_id": "pack1",
    "application_pack_revision_id": "grounding1",
    "application_pack_review_event_id": "groundingreview1",
    "application_artifact_revision_id": "artifact1",
    "application_artifact_approval_event_id": "artifactapproval1",
    "tailored_resume_version_id": "resume2",
}


class FakeDatabase:
    @contextmanager
    def session(self):
        yield object()


def _store() -> SqlAlchemyApplicationWorkspaceStore:
    return SqlAlchemyApplicationWorkspaceStore(
        FakeDatabase(),  # type: ignore[arg-type]
        load_data_keyring(production=False),
    )


def _ready() -> ReadyToApplyTransitionCreate:
    return ReadyToApplyTransitionCreate(
        to_stage="ready_to_apply",
        **EXACT_IDS,
        next_action_due_on=date(2026, 7, 15),
        confirm_ready=True,
    )


def test_submission_store_reads_database_and_forwards_transition_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    projection = ApplicationSubmissionProjection(
        application_id="application1",
        stage="pursuing",
        available_destinations=["https://careers.example.com/jobs/1/apply"],
        first_party_verified=True,
    )
    transition_result = object()
    calls: list[tuple[str, object, dict[str, object]]] = []

    def fake_load(session, **kwargs):
        calls.append(("load", session, kwargs))
        return projection

    def fake_transition(session, **kwargs):
        calls.append(("transition", session, kwargs))
        return transition_result

    monkeypatch.setattr(workspace_module, "load_application_submission", fake_load)
    monkeypatch.setattr(workspace_module, "transition_application", fake_transition)

    loaded = store.get_application_submission(
        owner_id="owner-a",
        application_id="application1",
    )
    transitioned = store.transition_application(
        owner_id="owner-a",
        application_id="application1",
        payload=_ready(),
        expected_application_version=4,
        idempotency_key="ready-v4",
    )

    assert loaded is projection
    assert transitioned is transition_result
    assert calls[0][0] == "load"
    assert calls[0][2] == {
        "owner_id": "owner-a",
        "application_id": "application1",
    }
    assert calls[1][0] == "transition"
    assert calls[1][2]["owner_id"] == "owner-a"
    assert calls[1][2]["application_id"] == "application1"
    assert calls[1][2]["payload"] == _ready()
    assert calls[1][2]["expected_application_version"] == 4
    assert calls[1][2]["idempotency_key"] == "ready-v4"
    assert calls[1][2]["keyring"] is store.keyring


@pytest.mark.parametrize(
    ("repository_error", "code"),
    [
        (VersionConflict("application", "application1", 1, 2), "version_conflict"),
        (ResourceConflict("stage changed"), "resource_conflict"),
        (MutationIdempotencyConflict("key reused"), "idempotency_conflict"),
        (MutationPending("request pending"), "mutation_pending"),
    ],
)
def test_submission_store_maps_expected_transition_conflicts(
    monkeypatch: pytest.MonkeyPatch,
    repository_error: Exception,
    code: str,
) -> None:
    store = _store()

    def fail(*_args, **_kwargs):
        raise repository_error

    monkeypatch.setattr(workspace_module, "transition_application", fail)
    with pytest.raises(WorkspaceConflict) as caught:
        store.transition_application(
            owner_id="owner-a",
            application_id="application1",
            payload=_ready(),
            expected_application_version=1,
            idempotency_key="ready-v1",
        )

    assert caught.value.code == code


def test_submission_store_sanitizes_corruption_and_maps_owner_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()

    def corrupt(*_args, **_kwargs):
        raise ApplicationSubmissionRepositoryError("PRIVATE_SUBMISSION_GRAPH")

    monkeypatch.setattr(workspace_module, "load_application_submission", corrupt)
    with pytest.raises(WorkspaceUnavailable) as caught:
        store.get_application_submission(
            owner_id="owner-a",
            application_id="application1",
        )
    assert str(caught.value) == "application-submission data is inconsistent"
    assert "PRIVATE_SUBMISSION_GRAPH" not in str(caught.value)

    def invalid(*_args, **_kwargs):
        raise ValueError("next_action_due_on is outside the allowed window")

    monkeypatch.setattr(workspace_module, "transition_application", invalid)
    with pytest.raises(WorkspaceInputError, match="next_action_due_on"):
        store.transition_application(
            owner_id="owner-a",
            application_id="application1",
            payload=_ready(),
            expected_application_version=1,
            idempotency_key="ready-invalid",
        )
