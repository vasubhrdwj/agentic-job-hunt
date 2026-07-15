"""SQL workspace adapter coverage for exact-attempt outreach replies."""

from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.exc import OperationalError

import job_hunt_agent.sqlalchemy_application_workspace as workspace_module
from job_hunt_agent.database import Database
from job_hunt_agent.mutation_receipts import MutationIdempotencyConflict
from job_hunt_agent.outreach_repository import OutreachRepositoryError
from job_hunt_agent.outreach_schemas import (
    ApplicationOutreachResponse,
    OutreachReplyCreate,
)
from job_hunt_agent.owner_workspace import WorkspaceConflict, WorkspaceUnavailable
from job_hunt_agent.repository_errors import ResourceConflict, VersionConflict
from job_hunt_agent.security import DataKeyring
from job_hunt_agent.sqlalchemy_application_workspace import (
    SqlAlchemyApplicationWorkspaceStore,
)


def _store() -> tuple[SqlAlchemyApplicationWorkspaceStore, Database, DataKeyring]:
    database = Database("sqlite+pysqlite:///:memory:")
    keyring = DataKeyring([("test-v1", Fernet.generate_key().decode("ascii"))])
    return SqlAlchemyApplicationWorkspaceStore(database, keyring), database, keyring


def _payload() -> OutreachReplyCreate:
    return OutreachReplyCreate(
        marked_sent_event_id="markedsentevent1",
        reply_kind="reply_received",
        received_on="2026-07-15",
        note="Exact private note",
        confirm_exact_sent_attempt=True,
    )


def test_workspace_forwards_exact_reply_contract_and_owns_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, database, keyring = _store()
    expected = ApplicationOutreachResponse(
        application_id="application1",
        status="not_started",
    )
    captured: dict[str, Any] = {}

    def fake_record(session: object, **kwargs: Any) -> ApplicationOutreachResponse:
        captured["session"] = session
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(workspace_module, "record_outreach_reply", fake_record)
    payload = _payload()
    try:
        result = store.record_outreach_reply(
            owner_id="owner1",
            application_id="application1",
            sequence_id="sequence1",
            payload=payload,
            expected_sequence_version=7,
            idempotency_key="reply-once",
        )
    finally:
        database.dispose()

    assert result is expected
    assert captured["session"] is not None
    assert captured["owner_id"] == "owner1"
    assert captured["application_id"] == "application1"
    assert captured["sequence_id"] == "sequence1"
    assert captured["payload"] is payload
    assert captured["expected_sequence_version"] == 7
    assert captured["idempotency_key"] == "reply-once"
    assert captured["keyring"] is keyring


@pytest.mark.parametrize(
    ("repository_error", "workspace_type", "code"),
    [
        (
            VersionConflict("outreach_sequence", "sequence1", 3, 4),
            WorkspaceConflict,
            "version_conflict",
        ),
        (
            MutationIdempotencyConflict("same key, different payload"),
            WorkspaceConflict,
            "idempotency_conflict",
        ),
        (ResourceConflict("not an exact send"), WorkspaceConflict, "resource_conflict"),
        (OutreachRepositoryError("broken graph"), WorkspaceUnavailable, None),
        (
            OperationalError("SELECT 1", {}, RuntimeError("database offline")),
            WorkspaceUnavailable,
            None,
        ),
    ],
)
def test_workspace_maps_reply_repository_failures_to_safe_errors(
    monkeypatch: pytest.MonkeyPatch,
    repository_error: Exception,
    workspace_type: type[Exception],
    code: str | None,
) -> None:
    store, database, _keyring = _store()

    def fail(_session: object, **_kwargs: Any) -> None:
        raise repository_error

    monkeypatch.setattr(workspace_module, "record_outreach_reply", fail)
    try:
        with pytest.raises(workspace_type) as captured:
            store.record_outreach_reply(
                owner_id="owner1",
                application_id="application1",
                sequence_id="sequence1",
                payload=_payload(),
                expected_sequence_version=7,
                idempotency_key="reply-once",
            )
    finally:
        database.dispose()

    if code is not None:
        assert isinstance(captured.value, WorkspaceConflict)
        assert captured.value.code == code
    assert "Exact private note" not in str(captured.value)
