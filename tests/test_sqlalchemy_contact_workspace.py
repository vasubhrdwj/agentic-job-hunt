"""Adapter tests for provider-free contact workspace reads."""

from __future__ import annotations

import socket
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError

import job_hunt_agent.sqlalchemy_contact_workspace as workspace_module
from job_hunt_agent.contact_repository import ContactRepositoryError
from job_hunt_agent.contact_search_repository import ContactSearchRepositoryError
from job_hunt_agent.contact_schemas import ApplicationContactBenchResponse
from job_hunt_agent.mutation_receipts import (
    MutationIdempotencyConflict,
    MutationPending,
)
from job_hunt_agent.owner_workspace import (
    WorkspaceCapabilityUnavailable,
    WorkspaceConflict,
    WorkspaceUnavailable,
)
from job_hunt_agent.repository_errors import ResourceConflict, VersionConflict
from job_hunt_agent.sqlalchemy_contact_workspace import SqlAlchemyContactWorkspaceStore


class FakeDatabase:
    @contextmanager
    def session(self):
        yield object()


@pytest.fixture(autouse=True)
def ready_contact_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workspace_module,
        "load_worker_capability",
        lambda _session, *, kind: SimpleNamespace(available=True, reason="available"),
    )


def test_contact_store_reads_only_the_database_and_forwards_owner_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = FakeDatabase()
    store = SqlAlchemyContactWorkspaceStore(database)  # type: ignore[arg-type]
    calls: list[tuple[object, str, str]] = []

    def fake_load(session, owner_id: str, application_id: str):
        calls.append((session, owner_id, application_id))
        return ApplicationContactBenchResponse(
            application_id=application_id,
            status="not_started",
            verified_count=0,
            coverage_status="not_started",
        )

    def fail_network(*_args, **_kwargs):
        raise AssertionError("contact reads must not invoke a provider")

    monkeypatch.setattr(workspace_module, "load_application_contact_bench", fake_load)
    monkeypatch.setattr(socket, "create_connection", fail_network)

    response = store.get_application_contacts(
        owner_id="owner-a",
        application_id="application1",
    )

    assert response is not None
    assert response.data_source == "database"
    assert calls and calls[0][1:] == ("owner-a", "application1")


def test_contact_store_sanitizes_repository_invariant_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SqlAlchemyContactWorkspaceStore(FakeDatabase())  # type: ignore[arg-type]

    def fail_projection(*_args, **_kwargs):
        raise ContactRepositoryError("PRIVATE_PROVIDER_PAYLOAD")

    monkeypatch.setattr(workspace_module, "load_application_contact_bench", fail_projection)

    with pytest.raises(WorkspaceUnavailable) as caught:
        store.get_application_contacts(
            owner_id="owner-a",
            application_id="application1",
        )

    assert str(caught.value) == "contact data is inconsistent"
    assert "PRIVATE_PROVIDER_PAYLOAD" not in str(caught.value)


def test_contact_store_sanitizes_database_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SqlAlchemyContactWorkspaceStore(FakeDatabase())  # type: ignore[arg-type]

    def fail_database(*_args, **_kwargs):
        raise SQLAlchemyError("PRIVATE_CONTACT_DATABASE_DSN")

    monkeypatch.setattr(workspace_module, "load_application_contact_bench", fail_database)

    with pytest.raises(WorkspaceUnavailable) as caught:
        store.get_application_contacts(
            owner_id="owner-a",
            application_id="application1",
        )

    assert str(caught.value) == "contact workspace database is unavailable"
    assert "PRIVATE_CONTACT_DATABASE_DSN" not in str(caught.value)


def test_contact_store_creates_and_projects_in_the_same_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SqlAlchemyContactWorkspaceStore(FakeDatabase())  # type: ignore[arg-type]
    calls: list[tuple[str, object]] = []

    def fake_create(session, **kwargs):
        calls.append(("create", session))
        assert kwargs == {
            "owner_id": "owner-a",
            "application_id": "application1",
            "expected_application_version": 3,
            "idempotency_key": "contacts-v3",
        }
        return object()

    def fake_load(session, owner_id: str, application_id: str):
        calls.append(("load", session))
        return ApplicationContactBenchResponse(
            application_id=application_id,
            status="queued",
            verified_count=0,
            coverage_status="pending",
            current_search={
                "id": "contactplan1",
                "version": 1,
                "plan_number": 1,
                "status": "queued",
                "job_stage": "queued",
                "target_count": 5,
                "candidate_limit": 12,
                "confidence_floor": 0.75,
                "discovered_count": 0,
                "evidence_verified_count": 0,
                "selected_count": 0,
                "coverage_status": "pending",
                "exhausted": False,
                "retryable": False,
                "shortfall_reasons": [],
                "created_at": "2026-07-14T08:00:00Z",
                "updated_at": "2026-07-14T08:00:00Z",
            },
        )

    monkeypatch.setattr(workspace_module, "create_contact_search", fake_create)
    monkeypatch.setattr(workspace_module, "load_application_contact_bench", fake_load)

    response = store.create_application_contact_search(
        owner_id="owner-a",
        application_id="application1",
        expected_application_version=3,
        idempotency_key="contacts-v3",
    )

    assert response is not None
    assert response.status.value == "queued"
    assert [name for name, _session in calls] == ["create", "load"]
    assert calls[0][1] is calls[1][1]


def test_contact_store_rejects_creation_before_mutation_when_worker_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SqlAlchemyContactWorkspaceStore(FakeDatabase())  # type: ignore[arg-type]
    create_called = False

    monkeypatch.setattr(
        workspace_module,
        "load_worker_capability",
        lambda _session, *, kind: SimpleNamespace(
            available=False,
            reason="unsupported_kind",
        ),
    )

    def fake_create(*_args, **_kwargs):
        nonlocal create_called
        create_called = True

    monkeypatch.setattr(workspace_module, "create_contact_search", fake_create)

    with pytest.raises(WorkspaceCapabilityUnavailable) as caught:
        store.create_application_contact_search(
            owner_id="owner-a",
            application_id="application1",
            expected_application_version=3,
            idempotency_key="contacts-unavailable",
        )

    assert caught.value.capability == "contact_search"
    assert caught.value.reason == "unsupported_kind"
    assert create_called is False


@pytest.mark.parametrize(
    ("repository_error", "code"),
    [
        (VersionConflict("application", "application1", 1, 2), "version_conflict"),
        (ResourceConflict("posting closed"), "resource_conflict"),
        (MutationIdempotencyConflict("key reused"), "idempotency_conflict"),
        (MutationPending("request pending"), "mutation_pending"),
    ],
)
def test_contact_store_maps_expected_create_conflicts(
    monkeypatch: pytest.MonkeyPatch,
    repository_error: Exception,
    code: str,
) -> None:
    store = SqlAlchemyContactWorkspaceStore(FakeDatabase())  # type: ignore[arg-type]

    def fail_create(*_args, **_kwargs):
        raise repository_error

    monkeypatch.setattr(workspace_module, "create_contact_search", fail_create)

    with pytest.raises(WorkspaceConflict) as caught:
        store.create_application_contact_search(
            owner_id="owner-a",
            application_id="application1",
            expected_application_version=1,
            idempotency_key="contacts-v1",
        )

    assert caught.value.code == code


def test_contact_store_sanitizes_contact_search_invariant_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SqlAlchemyContactWorkspaceStore(FakeDatabase())  # type: ignore[arg-type]

    def fail_create(*_args, **_kwargs):
        raise ContactSearchRepositoryError("PRIVATE_QUEUE_IDENTITY")

    monkeypatch.setattr(workspace_module, "create_contact_search", fail_create)

    with pytest.raises(WorkspaceUnavailable) as caught:
        store.create_application_contact_search(
            owner_id="owner-a",
            application_id="application1",
            expected_application_version=1,
            idempotency_key="contacts-v1",
        )

    assert str(caught.value) == "contact search data is inconsistent"
    assert "PRIVATE_QUEUE_IDENTITY" not in str(caught.value)
