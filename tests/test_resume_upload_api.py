"""Authenticated multipart resume-upload boundary tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from job_hunt_agent.routers import workspace as workspace_router
from job_hunt_agent.database import Database
from job_hunt_agent.models import AchievementEvidence, CandidateProfile, ResumeVersion
from job_hunt_agent.resume_ingestion import MAX_RESUME_FILE_BYTES
from job_hunt_agent.routers.session import create_session_router
from job_hunt_agent.routers.workspace import (
    MAX_RESUME_UPLOAD_REQUEST_BYTES,
    ResumeUploadBodyLimitMiddleware,
    create_workspace_router,
    install_workspace_error_handler,
)
from job_hunt_agent.security import DataKeyring
from job_hunt_agent.sqlalchemy_owner_workspace import SqlAlchemyOwnerWorkspaceStore
from tests.auth_helpers import login_test_account, seed_test_account


ORIGIN = "http://localhost:3000"
DIRECT_SESSION_COOKIE = b"job_hunt_session=" + b"A" * 43
RESUME_TEXT = """Vasu Bhardwaj

PROFESSIONAL EXPERIENCE
Software Engineer
Jan 2024 - Present
• Built a reliable event pipeline with retries and dead-letter handling for production workloads.

SKILLS
Python, Kafka, AWS, SQL
"""


@pytest.fixture
def upload_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, Database]]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'resume-upload.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("JOB_HUNT_SESSION_TTL_DAYS", "30")
    command.upgrade(Config("alembic.ini"), "head")
    database = Database(database_url)
    seed_test_account(database)
    keyring = DataKeyring([("test-v1", Fernet.generate_key().decode("ascii"))])
    app = FastAPI()
    app.include_router(
        create_session_router(database, allowed_origins=[ORIGIN], production=False)
    )
    app.include_router(
        create_workspace_router(
            database,
            SqlAlchemyOwnerWorkspaceStore(database, keyring),
            allowed_origins=[ORIGIN],
            production=False,
        )
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[ORIGIN],
        allow_credentials=True,
        allow_methods=["POST"],
        allow_headers=["Content-Type", "Idempotency-Key", "Origin"],
    )
    install_workspace_error_handler(
        app,
        allowed_origins=[ORIGIN],
        production=False,
        database=database,
    )
    with TestClient(app) as client:
        yield client, database
    database.dispose()


def _upload(
    client: TestClient,
    *,
    key: str = "resume-upload-1",
    origin: str = ORIGIN,
    filename: str = "Vasu_Backend_Resume.txt",
    content: bytes | str = RESUME_TEXT,
    content_type: str = "text/plain",
):
    return client.post(
        "/api/me/resume-versions/upload",
        headers={"Origin": origin, "Idempotency-Key": key},
        data={"set_as_base": "true"},
        files={"file": (filename, content, content_type)},
    )


def _direct_upload_scope(
    *,
    origin: str | None = ORIGIN,
    cookie: bytes | None = DIRECT_SESSION_COOKIE,
    content_length: int | None = None,
) -> dict[str, object]:
    headers = [(b"content-type", b"multipart/form-data; boundary=test")]
    if origin is not None:
        headers.append((b"origin", origin.encode("ascii")))
    if cookie is not None:
        headers.append((b"cookie", cookie))
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/api/me/resume-versions/upload",
        "raw_path": b"/api/me/resume-versions/upload",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 443),
        "root_path": "",
    }


def _sent_response_headers(message: dict[str, object]) -> dict[str, str]:
    return {
        name.decode("latin-1"): value.decode("latin-1")
        for name, value in message["headers"]  # type: ignore[union-attr]
    }


def _authenticated_direct_cookie(client: TestClient) -> bytes:
    response = login_test_account(client, origin=ORIGIN)
    assert response.status_code == 200
    token = client.cookies.get("job_hunt_session")
    assert token is not None
    return f"job_hunt_session={token}".encode("ascii")


def test_resume_upload_requires_session_origin_and_idempotency(
    upload_client: tuple[TestClient, Database],
) -> None:
    client, _database = upload_client
    logged_out = _upload(client)
    assert logged_out.status_code == 401
    assert logged_out.json()["code"] == "owner_session_required"

    client.cookies.set("job_hunt_session", "A" * 43)
    forged_session = _upload(client, key="forged-session")
    assert forged_session.status_code == 401
    assert forged_session.json()["code"] == "owner_session_required"
    client.cookies.clear()

    login = login_test_account(client, origin=ORIGIN)
    assert login.status_code == 200
    wrong_origin = _upload(client, origin="https://attacker.invalid")
    assert wrong_origin.status_code == 403
    assert wrong_origin.json()["code"] == "origin_forbidden"
    missing_key = client.post(
        "/api/me/resume-versions/upload",
        headers={"Origin": ORIGIN},
        files={"file": ("resume.txt", RESUME_TEXT, "text/plain")},
    )
    assert missing_key.status_code == 400
    assert missing_key.json()["code"] == "idempotency_key_required"


def test_resume_upload_imports_private_data_and_replays_without_duplicates(
    upload_client: tuple[TestClient, Database],
) -> None:
    client, database = upload_client
    assert login_test_account(client, origin=ORIGIN).status_code == 200

    first = _upload(client)
    assert first.status_code == 201, first.text
    body = first.json()
    assert first.headers["etag"] == '"1"'
    assert body["resume_version"]["label"] == "Vasu Backend Resume"
    assert body["resume_version"]["source"] == "uploaded"
    assert body["resume_version"]["is_base"] is True
    assert body["imported_profile_fields"] == [
        "current_title",
        "years_of_experience",
        "skills",
    ]
    assert body["missing_profile_fields"] == ["current_location"]
    assert body["achievement_suggestions_created"] == 1
    assert "experience" in body["parsed_sections"]

    replay = _upload(client)
    assert replay.status_code == 201
    assert replay.json() == body
    assert client.get("/api/me/profile").json()["current_title"] == "Software Engineer"
    assert client.get("/api/me/profile").json()["skills"] == [
        "Python",
        "Kafka",
        "AWS",
        "SQL",
    ]
    approved = client.get("/api/me/evidence", params={"approval_state": "approved"})
    assert approved.status_code == 200
    assert len(approved.json()["items"]) == 1
    assert approved.json()["items"][0]["source_excerpt"] in RESUME_TEXT

    changed = _upload(
        client,
        content="PROFESSIONAL EXPERIENCE\nDifferent resume content that is long enough to parse.",
    )
    assert changed.status_code == 409
    assert changed.json()["code"] == "idempotency_conflict"

    with database.session() as session:
        raw_resume = session.get(ResumeVersion, body["resume_version"]["id"])
        raw_profile = session.scalar(select(CandidateProfile))
        assert raw_resume is not None and raw_profile is not None
        assert RESUME_TEXT not in raw_resume.encrypted_content
        assert "Software Engineer" not in raw_profile.encrypted_payload
        assert session.scalar(select(func.count(ResumeVersion.id))) == 1
        assert session.scalar(select(func.count(AchievementEvidence.id))) == 1


def test_resume_upload_rejects_bad_signature_and_oversized_file_safely(
    upload_client: tuple[TestClient, Database],
) -> None:
    client, database = upload_client
    assert login_test_account(client, origin=ORIGIN).status_code == 200

    bad_pdf = _upload(
        client,
        key="bad-pdf",
        filename="resume.pdf",
        content=b"not a real PDF",
        content_type="application/pdf",
    )
    assert bad_pdf.status_code == 422
    assert bad_pdf.json()["code"] == "resume_pdf_invalid"
    assert "not a real PDF" not in str(bad_pdf.json())

    oversized = _upload(
        client,
        key="oversized",
        content=b"x" * (MAX_RESUME_FILE_BYTES + 1),
    )
    assert oversized.status_code == 413
    assert oversized.json()["code"] == "resume_too_large"

    rejected_before_multipart_parsing = _upload(
        client,
        key="request-envelope-too-large",
        content=b"x" * (MAX_RESUME_UPLOAD_REQUEST_BYTES + 1),
    )
    assert rejected_before_multipart_parsing.status_code == 413
    assert rejected_before_multipart_parsing.json()["code"] == "resume_too_large"
    assert rejected_before_multipart_parsing.headers["access-control-allow-origin"] == ORIGIN
    assert (
        rejected_before_multipart_parsing.headers["access-control-allow-credentials"]
        == "true"
    )
    assert rejected_before_multipart_parsing.headers["vary"] == "Origin"
    with database.session() as session:
        assert session.scalar(select(func.count(ResumeVersion.id))) == 0


@pytest.mark.parametrize(
    "cookie",
    [None, b"job_hunt_session=too-short"],
    ids=["missing", "malformed"],
)
def test_resume_body_limit_rejects_bad_session_cookie_without_reading_body(
    cookie: bytes | None,
) -> None:
    downstream_called = False
    receive_called = False
    sent: list[dict[str, object]] = []

    async def downstream(scope, receive, send) -> None:
        nonlocal downstream_called
        downstream_called = True

    async def receive() -> dict[str, object]:
        nonlocal receive_called
        receive_called = True
        return {"type": "http.request", "body": b"private resume", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope = _direct_upload_scope(
        cookie=cookie,
        content_length=MAX_RESUME_UPLOAD_REQUEST_BYTES + 1,
    )
    asyncio.run(
        ResumeUploadBodyLimitMiddleware(
            downstream,
            allowed_origins=[ORIGIN],
        )(scope, receive, send)  # type: ignore[arg-type]
    )

    assert downstream_called is False
    assert receive_called is False
    assert sent[0]["status"] == 401
    assert json.loads(sent[1]["body"])["code"] == "owner_session_required"
    headers = _sent_response_headers(sent[0])
    assert headers["access-control-allow-origin"] == ORIGIN
    assert headers["access-control-allow-credentials"] == "true"
    if cookie is not None:
        assert cookie not in sent[1]["body"]


def test_resume_body_limit_rejects_forged_shaped_session_without_reading_body(
    upload_client: tuple[TestClient, Database],
) -> None:
    _client, database = upload_client
    downstream_called = False
    receive_called = False
    sent: list[dict[str, object]] = []

    async def downstream(scope, receive, send) -> None:
        nonlocal downstream_called
        downstream_called = True

    async def receive() -> dict[str, object]:
        nonlocal receive_called
        receive_called = True
        return {"type": "http.request", "body": b"private resume", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope = _direct_upload_scope(
        content_length=MAX_RESUME_UPLOAD_REQUEST_BYTES + 1,
    )
    asyncio.run(
        ResumeUploadBodyLimitMiddleware(
            downstream,
            allowed_origins=[ORIGIN],
            database=database,
        )(scope, receive, send)  # type: ignore[arg-type]
    )

    assert downstream_called is False
    assert receive_called is False
    assert sent[0]["status"] == 401
    assert json.loads(sent[1]["body"])["code"] == "owner_session_required"
    assert DIRECT_SESSION_COOKIE not in sent[1]["body"]


def test_resume_body_limit_returns_safe_503_without_reading_on_session_store_failure(
    upload_client: tuple[TestClient, Database],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _client, database = upload_client
    downstream_called = False
    receive_called = False
    sent: list[dict[str, object]] = []

    def unavailable_session_store(_database: Database, _token: str) -> bool:
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(
        workspace_router,
        "_owner_session_is_valid",
        unavailable_session_store,
    )

    async def downstream(scope, receive, send) -> None:
        nonlocal downstream_called
        downstream_called = True

    async def receive() -> dict[str, object]:
        nonlocal receive_called
        receive_called = True
        return {"type": "http.request", "body": b"private resume", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope = _direct_upload_scope()
    asyncio.run(
        ResumeUploadBodyLimitMiddleware(
            downstream,
            allowed_origins=[ORIGIN],
            database=database,
        )(scope, receive, send)  # type: ignore[arg-type]
    )

    assert downstream_called is False
    assert receive_called is False
    assert sent[0]["status"] == 503
    problem = json.loads(sent[1]["body"])
    assert problem["code"] == "workspace_unavailable"
    assert problem["retryable"] is True
    assert DIRECT_SESSION_COOKIE not in sent[1]["body"]
    assert _sent_response_headers(sent[0])["access-control-allow-origin"] == ORIGIN


def test_resume_body_limit_fails_closed_before_reading_without_session_store() -> None:
    downstream_called = False
    receive_called = False
    sent: list[dict[str, object]] = []

    async def downstream(scope, receive, send) -> None:
        nonlocal downstream_called
        downstream_called = True

    async def receive() -> dict[str, object]:
        nonlocal receive_called
        receive_called = True
        return {"type": "http.request", "body": b"private resume", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    asyncio.run(
        ResumeUploadBodyLimitMiddleware(
            downstream,
            allowed_origins=[ORIGIN],
            database=None,
        )(_direct_upload_scope(), receive, send)  # type: ignore[arg-type]
    )

    assert downstream_called is False
    assert receive_called is False
    assert sent[0]["status"] == 503
    problem = json.loads(sent[1]["body"])
    assert problem["code"] == "workspace_unavailable"
    assert problem["retryable"] is True
    assert DIRECT_SESSION_COOKIE not in sent[1]["body"]


def test_resume_body_limit_rejects_wrong_origin_without_reading_body() -> None:
    downstream_called = False
    receive_called = False
    sent: list[dict[str, object]] = []

    async def downstream(scope, receive, send) -> None:
        nonlocal downstream_called
        downstream_called = True

    async def receive() -> dict[str, object]:
        nonlocal receive_called
        receive_called = True
        return {"type": "http.request", "body": b"private resume", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope = _direct_upload_scope(
        origin="https://attacker.invalid",
        content_length=MAX_RESUME_UPLOAD_REQUEST_BYTES + 1,
    )
    asyncio.run(
        ResumeUploadBodyLimitMiddleware(
            downstream,
            allowed_origins=[ORIGIN],
        )(scope, receive, send)  # type: ignore[arg-type]
    )

    assert downstream_called is False
    assert receive_called is False
    assert sent[0]["status"] == 403
    assert json.loads(sent[1]["body"])["code"] == "origin_forbidden"
    assert "access-control-allow-origin" not in _sent_response_headers(sent[0])
    assert DIRECT_SESSION_COOKIE not in sent[1]["body"]


def test_resume_body_limit_rejects_chunked_requests_before_downstream_parsing(
    upload_client: tuple[TestClient, Database],
) -> None:
    client, database = upload_client
    downstream_called = False
    sent: list[dict[str, object]] = []
    chunks = [
        {
            "type": "http.request",
            "body": b"x" * (MAX_RESUME_UPLOAD_REQUEST_BYTES // 2),
            "more_body": True,
        },
        {
            "type": "http.request",
            "body": b"y" * (MAX_RESUME_UPLOAD_REQUEST_BYTES // 2 + 1),
            "more_body": False,
        },
    ]

    async def downstream(scope, receive, send) -> None:
        nonlocal downstream_called
        downstream_called = True

    async def receive() -> dict[str, object]:
        return chunks.pop(0)

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope = _direct_upload_scope(cookie=_authenticated_direct_cookie(client))

    asyncio.run(
        ResumeUploadBodyLimitMiddleware(
            downstream,
            allowed_origins=[ORIGIN],
            database=database,
        )(scope, receive, send)  # type: ignore[arg-type]
    )

    assert downstream_called is False
    assert sent[0]["status"] == 413
    response_body = json.loads(sent[1]["body"])
    assert response_body["code"] == "resume_too_large"
    assert response_body["retryable"] is False
    assert _sent_response_headers(sent[0])["access-control-allow-origin"] == ORIGIN


def test_resume_body_limit_rejects_declared_oversize_without_reading_body(
    upload_client: tuple[TestClient, Database],
) -> None:
    client, database = upload_client
    downstream_called = False
    receive_called = False
    sent: list[dict[str, object]] = []

    async def downstream(scope, receive, send) -> None:
        nonlocal downstream_called
        downstream_called = True

    async def receive() -> dict[str, object]:
        nonlocal receive_called
        receive_called = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope = _direct_upload_scope(
        cookie=_authenticated_direct_cookie(client),
        content_length=MAX_RESUME_UPLOAD_REQUEST_BYTES + 1,
    )

    asyncio.run(
        ResumeUploadBodyLimitMiddleware(
            downstream,
            allowed_origins=[ORIGIN],
            database=database,
        )(scope, receive, send)  # type: ignore[arg-type]
    )

    assert downstream_called is False
    assert receive_called is False
    assert sent[0]["status"] == 413
    headers = _sent_response_headers(sent[0])
    assert headers["access-control-allow-origin"] == ORIGIN
    assert headers["access-control-allow-credentials"] == "true"
    assert headers["vary"] == "Origin"


def test_resume_body_limit_replays_normal_chunked_body_exactly_once(
    upload_client: tuple[TestClient, Database],
) -> None:
    client, database = upload_client
    original = [b"multipart chunk one", b" and chunk two"]
    chunks = [
        {"type": "http.request", "body": original[0], "more_body": True},
        {"type": "http.request", "body": original[1], "more_body": False},
    ]
    downstream_messages: list[dict[str, object]] = []

    async def downstream(scope, receive, send) -> None:
        downstream_messages.append(await receive())
        downstream_messages.append(await receive())

    async def receive() -> dict[str, object]:
        return chunks.pop(0)

    async def send(message: dict[str, object]) -> None:
        raise AssertionError("normal replay should not synthesize a response")

    scope = _direct_upload_scope(cookie=_authenticated_direct_cookie(client))

    asyncio.run(
        ResumeUploadBodyLimitMiddleware(
            downstream,
            allowed_origins=[ORIGIN],
            database=database,
        )(scope, receive, send)  # type: ignore[arg-type]
    )

    assert downstream_messages == [
        {
            "type": "http.request",
            "body": b"".join(original),
            "more_body": False,
        },
        {"type": "http.disconnect"},
    ]
