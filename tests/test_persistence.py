"""Unit tests for the SQLite persistence layer."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from job_hunt_agent import persistence
from job_hunt_agent.security import DataKeyring, hash_access_token
from job_hunt_agent.schemas import (
    HuntResult,
    OutcomeLog,
    OutreachDraft,
    Person,
    Role,
)


def _make_role() -> Role:
    return Role(
        company="Okta",
        title="Senior Engineer, Identity",
        url="https://www.linkedin.com/jobs/view/123",
        location="Remote-India",
        summary="Build SCIM provisioning.",
        match_reason="Listing mentions SCIM 2.0.",
    )


def _make_person() -> Person:
    return Person(
        name="Priya Rao",
        title="Staff Engineer",
        company="Okta",
        profile_url="https://linkedin.com/in/priya",
        source="linkedin",
        why_relevant="Adjacent team.",
    )


def _make_hunt_result(run_id: str = "run-abc") -> HuntResult:
    role = _make_role()
    person = _make_person()
    drafts = [
        OutreachDraft(draft_id=f"draft-{i}", role=role, person=person, message=f"hi {i}")
        for i in range(3)
    ]
    return HuntResult(run_id=run_id, roles=[role], outreach=drafts)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    persistence.init_db(path)
    return path


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "test.db"
    persistence.init_db(path)
    persistence.init_db(path)  # second call must not raise


def test_save_and_load_run_round_trip(db_path: Path) -> None:
    result = _make_hunt_result()
    persistence.save_run(result, path=db_path)

    loaded = persistence.load_run("run-abc", path=db_path)
    assert loaded == result


def test_load_run_returns_none_for_missing_run(db_path: Path) -> None:
    assert persistence.load_run("nope", path=db_path) is None


def test_save_run_rejects_duplicate_run_id(db_path: Path) -> None:
    result = _make_hunt_result()
    persistence.save_run(result, path=db_path)
    with pytest.raises(sqlite3.IntegrityError):
        persistence.save_run(result, path=db_path)


def test_save_outcomes_stamps_logged_at_and_appends(db_path: Path) -> None:
    result = _make_hunt_result()
    persistence.save_run(result, path=db_path)

    first = persistence.save_outcomes(
        "run-abc",
        [
            OutcomeLog(draft_id="draft-0", outcome="replied"),
            OutcomeLog(draft_id="draft-1", outcome="no_reply"),
        ],
        path=db_path,
    )

    assert all(entry.logged_at is not None for entry in first)
    assert {entry.draft_id for entry in first} == {"draft-0", "draft-1"}

    # Ensure ordering by logged_at is non-degenerate for the same row.
    time.sleep(0.01)

    second = persistence.save_outcomes(
        "run-abc",
        [OutcomeLog(draft_id="draft-0", outcome="introduced", notes="forwarded")],
        path=db_path,
    )

    outcomes = persistence.load_outcomes("run-abc", path=db_path)
    assert len(outcomes) == 3
    # Newest first ordering.
    assert outcomes[0].outcome == "introduced"
    assert outcomes[0].notes == "forwarded"
    assert outcomes[0].logged_at >= second[0].logged_at  # type: ignore[operator]


def test_save_outcomes_overwrites_client_timestamp(db_path: Path) -> None:
    result = _make_hunt_result()
    persistence.save_run(result, path=db_path)

    from datetime import datetime, timezone

    spoofed = datetime(1999, 1, 1, tzinfo=timezone.utc)
    saved = persistence.save_outcomes(
        "run-abc",
        [OutcomeLog(draft_id="draft-0", outcome="replied", logged_at=spoofed)],
        path=db_path,
    )

    assert saved[0].logged_at is not None
    assert saved[0].logged_at > spoofed


def test_load_outcomes_for_missing_run_returns_empty(db_path: Path) -> None:
    assert persistence.load_outcomes("nope", path=db_path) == []


def test_save_outcomes_with_empty_list_is_noop(db_path: Path) -> None:
    persistence.save_run(_make_hunt_result(), path=db_path)
    assert persistence.save_outcomes("run-abc", [], path=db_path) == []
    assert persistence.load_outcomes("run-abc", path=db_path) == []


def test_env_var_is_used_when_path_arg_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "env-driven.db"
    monkeypatch.setenv("JOB_HUNT_DB_PATH", str(db))
    persistence.init_db()
    persistence.save_run(_make_hunt_result("env-run"))
    assert db.exists()
    assert persistence.load_run("env-run") == _make_hunt_result("env-run")


def test_load_draft_outcomes_prefers_real_success_over_no_reply(db_path: Path) -> None:
    result = _make_hunt_result()
    persistence.save_run(result, path=db_path)
    persistence.save_outcomes(
        result.run_id,
        [
            OutcomeLog(draft_id="draft-0", outcome="no_reply"),
            OutcomeLog(draft_id="draft-1", outcome="replied"),
            OutcomeLog(draft_id="draft-2", outcome="introduced"),
        ],
        path=db_path,
    )

    outcomes = persistence.load_draft_outcomes(path=db_path)

    assert outcomes[persistence.normalize_draft_message("hi 0")] == "no_reply"
    assert outcomes[persistence.normalize_draft_message("hi 1")] == "replied"
    assert outcomes[persistence.normalize_draft_message("hi 2")] == "introduced"


def test_load_draft_outcomes_returns_empty_for_missing_database(tmp_path: Path) -> None:
    assert persistence.load_draft_outcomes(path=tmp_path / "missing.db") == {}


def test_private_request_is_encrypted_authorized_and_cleared(db_path: Path) -> None:
    from cryptography.fernet import Fernet

    marker = "PRIVATE-RESUME-MARKER-91ac"
    keyring = DataKeyring([("v1", Fernet.generate_key().decode("ascii"))])
    envelope = keyring.encrypt(marker)
    token_hash = hash_access_token("secret-capability")
    now = datetime.now(timezone.utc)

    persistence.create_run_security(
        "private-run",
        access_hash=token_hash,
        encrypted_request=envelope.ciphertext,
        encryption_key_id=envelope.key_id,
        request_expires_at=now + timedelta(hours=1),
        access_expires_at=now + timedelta(days=1),
        path=db_path,
    )

    assert marker.encode() not in db_path.read_bytes()
    assert persistence.authorize_run(
        "private-run",
        token_hash,
        now=now,
        path=db_path,
    )
    assert not persistence.authorize_run(
        "private-run",
        hash_access_token("wrong"),
        now=now,
        path=db_path,
    )
    stored = persistence.load_encrypted_request("private-run", path=db_path)
    assert stored == (envelope.key_id, envelope.ciphertext)

    persistence.complete_run_security("private-run", path=db_path)

    assert persistence.load_encrypted_request("private-run", path=db_path) is None


def test_access_capability_expires(db_path: Path) -> None:
    now = datetime.now(timezone.utc)
    token_hash = hash_access_token("expired")
    persistence.create_run_security(
        "expired-run",
        access_hash=token_hash,
        encrypted_request="ciphertext",
        encryption_key_id="v1",
        request_expires_at=now - timedelta(hours=2),
        access_expires_at=now - timedelta(seconds=1),
        path=db_path,
    )

    assert not persistence.authorize_run(
        "expired-run",
        token_hash,
        now=now,
        path=db_path,
    )


def test_delete_run_removes_security_result_and_outcomes(db_path: Path) -> None:
    now = datetime.now(timezone.utc)
    result = _make_hunt_result("delete-me")
    persistence.create_run_security(
        result.run_id,
        access_hash=hash_access_token("delete-token"),
        encrypted_request="ciphertext",
        encryption_key_id="v1",
        request_expires_at=now + timedelta(hours=1),
        access_expires_at=now + timedelta(days=1),
        path=db_path,
    )
    persistence.save_run(result, path=db_path)
    persistence.save_outcomes(
        result.run_id,
        [OutcomeLog(draft_id="draft-0", outcome="replied")],
        path=db_path,
    )

    assert persistence.delete_run(result.run_id, path=db_path)
    assert persistence.load_run(result.run_id, path=db_path) is None
    assert persistence.load_outcomes(result.run_id, path=db_path) == []
    assert not persistence.authorize_run(
        result.run_id,
        hash_access_token("delete-token"),
        path=db_path,
    )


def test_purge_clears_expired_requests_and_deletes_expired_runs(
    db_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    for run_id, request_expiry, access_expiry in (
        (
            "clear-request",
            now - timedelta(seconds=1),
            now + timedelta(days=1),
        ),
        (
            "delete-run",
            now - timedelta(seconds=1),
            now - timedelta(seconds=1),
        ),
        (
            "keep-run",
            now + timedelta(hours=1),
            now + timedelta(days=1),
        ),
    ):
        persistence.create_run_security(
            run_id,
            access_hash=hash_access_token(run_id),
            encrypted_request=f"ciphertext-{run_id}",
            encryption_key_id="v1",
            request_expires_at=request_expiry,
            access_expires_at=access_expiry,
            path=db_path,
        )

    cleared, deleted = persistence.purge_expired_data(now=now, path=db_path)

    assert cleared == 2
    assert deleted == 1
    assert persistence.load_encrypted_request("clear-request", path=db_path) is None
    assert persistence.load_encrypted_request("delete-run", path=db_path) is None
    assert persistence.load_encrypted_request("keep-run", path=db_path) is not None
