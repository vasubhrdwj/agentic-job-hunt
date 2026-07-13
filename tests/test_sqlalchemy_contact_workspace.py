"""Adapter tests for provider-free contact workspace reads."""

from __future__ import annotations

import socket
from contextlib import contextmanager

import pytest
from sqlalchemy.exc import SQLAlchemyError

import job_hunt_agent.sqlalchemy_contact_workspace as workspace_module
from job_hunt_agent.contact_repository import ContactRepositoryError
from job_hunt_agent.contact_schemas import ApplicationContactBenchResponse
from job_hunt_agent.owner_workspace import WorkspaceUnavailable
from job_hunt_agent.sqlalchemy_contact_workspace import SqlAlchemyContactWorkspaceStore


class FakeDatabase:
    @contextmanager
    def session(self):
        yield object()


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
