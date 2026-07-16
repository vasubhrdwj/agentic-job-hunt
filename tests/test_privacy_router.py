"""Authenticated HTTP contracts for practical privacy controls."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from job_hunt_agent.auth import create_owner_session
from job_hunt_agent.database import Database
from job_hunt_agent.models import HuntRun, Owner, OwnerSession, PrivacyDeletionReceipt
from job_hunt_agent.routers.privacy import _receipt_secret
from job_hunt_agent.routers.workspace import WorkspaceApiError
from job_hunt_agent.security import hash_access_token
from tests.test_practical_api import (
    ALLOWED_ORIGIN,
    _hunt_payload,
    _login,
    practical_client,
)


def _problem(response, status_code: int, code: str) -> dict:
    assert response.status_code == status_code, response.text
    body = response.json()
    assert body["code"] == code
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    return body


def test_privacy_reads_require_owner_and_export_is_downloadable_no_store(
    practical_client: tuple[TestClient, Database],
) -> None:
    client, _database = practical_client
    for path in (
        "/api/privacy/export",
        "/api/privacy/deletion-preview",
        "/api/privacy/retention",
    ):
        _problem(client.get(path), 401, "owner_session_required")

    _login(client)
    exported = client.get("/api/privacy/export")
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith("application/json")
    assert exported.headers["content-disposition"] == (
        'attachment; filename="job-hunt-workspace.json"'
    )
    assert exported.headers["cache-control"] == "no-store, max-age=0"
    assert exported.headers["pragma"] == "no-cache"
    assert exported.json()["schema_name"] == "job_hunt_workspace_export"
    assert exported.json()["schema_version"] == 1

    preview = client.get("/api/privacy/deletion-preview")
    assert preview.status_code == 200
    assert preview.json()["confirmation_phrase"] == "DELETE WORKSPACE owner"
    assert preview.json()["active_sessions"] == 1


def test_retention_requires_origin_and_etag_and_controls_new_run_expiry(
    practical_client: tuple[TestClient, Database],
) -> None:
    client, database = practical_client
    _login(client)
    initial = client.get("/api/privacy/retention")
    assert initial.status_code == 200
    assert initial.json()["hunt_run_retention_days"] == 30
    assert initial.headers["etag"] == '"1"'

    _problem(
        client.patch(
            "/api/privacy/retention",
            headers={"Origin": "https://attacker.invalid", "If-Match": '"1"'},
            json={"hunt_run_retention_days": 7},
        ),
        403,
        "origin_forbidden",
    )
    _problem(
        client.patch(
            "/api/privacy/retention",
            headers={"Origin": ALLOWED_ORIGIN},
            json={"hunt_run_retention_days": 7},
        ),
        428,
        "precondition_required",
    )
    updated = client.patch(
        "/api/privacy/retention",
        headers={"Origin": ALLOWED_ORIGIN, "If-Match": '"1"'},
        json={"hunt_run_retention_days": 7},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["hunt_run_retention_days"] == 7
    assert updated.headers["etag"] == '"2"'

    created = client.post(
        "/api/hunt",
        headers={"Origin": ALLOWED_ORIGIN},
        json=_hunt_payload(),
    )
    assert created.status_code == 202, created.text
    with database.session() as session:
        hunt = session.get(HuntRun, created.json()["run_id"])
        assert hunt is not None
        assert timedelta(days=6, hours=23) < (
            hunt.access_expires_at - hunt.created_at
        ) < timedelta(days=7, minutes=1)


def test_workspace_delete_requires_confirmation_and_revokes_all_sessions(
    practical_client: tuple[TestClient, Database],
) -> None:
    client, database = practical_client
    _login(client)
    with database.session() as session:
        session.add(Owner(id="other-owner", display_name="Other", timezone="UTC"))
        create_owner_session(session, "owner")
        create_owner_session(session, "other-owner")

    path = "/api/privacy/workspace"
    _problem(
        client.request(
            "DELETE",
            path,
            headers={"Origin": ALLOWED_ORIGIN},
            json={"confirmation": "DELETE WORKSPACE owner"},
        ),
        400,
        "idempotency_key_required",
    )
    _problem(
        client.request(
            "DELETE",
            path,
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Idempotency-Key": "delete-owner-v1",
            },
            json={"confirmation": "DELETE WORKSPACE other-owner"},
        ),
        409,
        "deletion_confirmation_mismatch",
    )

    deleted = client.request(
        "DELETE",
        path,
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Idempotency-Key": "delete-owner-v1",
        },
        json={"confirmation": "DELETE WORKSPACE owner"},
    )
    assert deleted.status_code == 200, deleted.text
    assert set(deleted.json()) == {"deletion_id", "deleted_at", "replayed"}
    assert deleted.json()["replayed"] is False
    assert "job_hunt_session=" in deleted.headers["set-cookie"]
    assert "Max-Age=0" in deleted.headers["set-cookie"]
    assert client.get("/api/session").status_code == 401

    with database.session() as session:
        assert session.get(Owner, "owner") is None
        assert session.get(Owner, "other-owner") is not None
        assert session.scalar(
            select(func.count())
            .select_from(OwnerSession)
            .where(OwnerSession.owner_id == "owner")
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(OwnerSession)
            .where(OwnerSession.owner_id == "other-owner")
        ) == 1
        receipt = session.scalar(select(PrivacyDeletionReceipt))
        assert receipt is not None
        assert receipt.owner_id_hash != "owner"
        assert len(receipt.owner_id_hash) == 64


def test_privacy_openapi_declares_cookie_auth_and_destructive_headers(
    practical_client: tuple[TestClient, Database],
) -> None:
    client, _database = practical_client
    schema = client.get("/openapi.json").json()
    export = schema["paths"]["/api/privacy/export"]["get"]
    assert export["security"] == [{"OwnerSessionCookie": []}]
    assert export["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/WorkspaceExportResponse")

    deletion = schema["paths"]["/api/privacy/workspace"]["delete"]
    assert deletion["security"] == [{"OwnerSessionCookie": []}]
    parameters = {item["name"]: item for item in deletion["parameters"]}
    assert parameters["Idempotency-Key"]["in"] == "header"
    assert deletion["requestBody"]["required"] is True


def test_receipt_secret_fallback_is_development_only(
    monkeypatch,
) -> None:
    owner_hash = hash_access_token(
        "development-owner-token-with-more-than-thirty-two-characters"
    )
    monkeypatch.delenv("JOB_HUNT_PRIVACY_RECEIPT_SECRET", raising=False)
    monkeypatch.setenv("JOB_HUNT_OWNER_TOKEN_HASH", owner_hash)

    assert _receipt_secret(production=False) == owner_hash
    with pytest.raises(WorkspaceApiError) as raised:
        _receipt_secret(production=True)
    assert raised.value.status_code == 503

    dedicated = "stable-receipt-secret-with-more-than-thirty-two-characters"
    monkeypatch.setenv("JOB_HUNT_PRIVACY_RECEIPT_SECRET", dedicated)
    assert _receipt_secret(production=True) == dedicated
