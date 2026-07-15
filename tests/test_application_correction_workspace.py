"""Workspace adapter coverage for milestone correction persistence."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pytest

import job_hunt_agent.sqlalchemy_application_workspace as workspace_module
from job_hunt_agent.application_correction_repository import (
    ApplicationCorrectionRepositoryError,
)
from job_hunt_agent.application_schemas import ApplicationMilestoneCorrectionCreate
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


class FakeDatabase:
    @contextmanager
    def session(self):
        yield object()


def _store() -> SqlAlchemyApplicationWorkspaceStore:
    return SqlAlchemyApplicationWorkspaceStore(
        FakeDatabase(),  # type: ignore[arg-type]
        load_data_keyring(production=False),
    )


def _payload() -> ApplicationMilestoneCorrectionCreate:
    return ApplicationMilestoneCorrectionCreate(
        corrected_effective_on=date(2026, 7, 14),
        confirm_correction=True,
    )


def test_store_forwards_the_exact_owner_event_version_and_retry_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    expected = object()
    captured: dict[str, object] = {}

    def fake_record(session, **kwargs):
        captured["session"] = session
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(
        workspace_module,
        "record_application_milestone_correction",
        fake_record,
    )

    result = store.record_application_milestone_correction(
        owner_id="owner-a",
        application_id="application1",
        activity_event_id="screeningevent1",
        payload=_payload(),
        expected_application_version=4,
        idempotency_key="correct-screen-v4",
    )

    assert result is expected
    assert captured == {
        "session": captured["session"],
        "owner_id": "owner-a",
        "application_id": "application1",
        "activity_event_id": "screeningevent1",
        "payload": _payload(),
        "expected_application_version": 4,
        "idempotency_key": "correct-screen-v4",
    }


@pytest.mark.parametrize(
    ("repository_error", "code"),
    [
        (VersionConflict("application", "application1", 4, 5), "version_conflict"),
        (ResourceConflict("date unchanged"), "resource_conflict"),
        (MutationIdempotencyConflict("key reused"), "idempotency_conflict"),
        (MutationPending("still pending"), "mutation_pending"),
    ],
)
def test_store_maps_expected_correction_conflicts(
    monkeypatch: pytest.MonkeyPatch,
    repository_error: Exception,
    code: str,
) -> None:
    store = _store()

    def fail(*_args, **_kwargs):
        raise repository_error

    monkeypatch.setattr(
        workspace_module,
        "record_application_milestone_correction",
        fail,
    )
    with pytest.raises(WorkspaceConflict) as caught:
        store.record_application_milestone_correction(
            owner_id="owner-a",
            application_id="application1",
            activity_event_id="screeningevent1",
            payload=_payload(),
            expected_application_version=4,
            idempotency_key="correct-screen-v4",
        )

    assert caught.value.code == code


def test_store_sanitizes_corruption_and_preserves_safe_input_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()

    def corrupt(*_args, **_kwargs):
        raise ApplicationCorrectionRepositoryError("PRIVATE_CORRECTION_GRAPH")

    monkeypatch.setattr(
        workspace_module,
        "record_application_milestone_correction",
        corrupt,
    )
    with pytest.raises(WorkspaceUnavailable) as caught:
        store.record_application_milestone_correction(
            owner_id="owner-a",
            application_id="application1",
            activity_event_id="screeningevent1",
            payload=_payload(),
            expected_application_version=4,
            idempotency_key="corrupt-correction",
        )
    assert str(caught.value) == "application milestone-correction data is inconsistent"
    assert "PRIVATE_CORRECTION_GRAPH" not in str(caught.value)

    def invalid(*_args, **_kwargs):
        raise ValueError(
            "corrected_effective_on must be between 2026-07-14 and 2026-07-15"
        )

    monkeypatch.setattr(
        workspace_module,
        "record_application_milestone_correction",
        invalid,
    )
    with pytest.raises(WorkspaceInputError, match="corrected_effective_on"):
        store.record_application_milestone_correction(
            owner_id="owner-a",
            application_id="application1",
            activity_event_id="screeningevent1",
            payload=_payload(),
            expected_application_version=4,
            idempotency_key="invalid-correction",
        )
