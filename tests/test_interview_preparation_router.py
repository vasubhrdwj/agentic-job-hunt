"""Authenticated transport coverage for interview preparation."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient

from job_hunt_agent.database import Database
from job_hunt_agent.interview_preparation_schemas import (
    ApplicationInterviewPreparationResponse,
    InterviewPreparationRevisionCreate,
)
from job_hunt_agent.routers.applications import create_application_router
from job_hunt_agent.routers.session import create_session_router
from job_hunt_agent.routers.workspace import install_workspace_error_handler
from job_hunt_agent.security import hash_access_token


OWNER_TOKEN = "interview-preparation-owner-token-more-than-thirty-two-characters"
ORIGIN = "http://localhost:3000"
NOW = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)
SOURCE_HASH = "a" * 64


def _projection(*, saved: bool = False) -> ApplicationInterviewPreparationResponse:
    draft = {
        "situation": "Owner fact" if saved else "",
        "task": "",
        "action": "",
        "result": "",
    }
    return ApplicationInterviewPreparationResponse.model_validate(
        {
            "application_id": "application1",
            "application_version": 2,
            "application_submission_id": "submission1",
            "preparation_id": "preparation1" if saved else None,
            "preparation_version": 1 if saved else None,
            "write_version_scope": "preparation" if saved else "application",
            "write_version": 1 if saved else 2,
            "status": "in_progress" if saved else "not_started",
            "source_fingerprint": SOURCE_HASH,
            "role": {
                "job_posting_id": "posting1",
                "posting_version_id": "postingversion1",
                "company": "Example",
                "title": "Engineer",
                "summary": "Build systems.",
            },
            "target": {
                "kind": "recruiter_screen",
                "label": "Recruiter screen",
            },
            "grounding_revision_id": "grounding1",
            "latest_revision": (
                {
                    "id": "revision1",
                    "revision_number": 1,
                    "parent_revision_id": None,
                    "source_fingerprint": SOURCE_HASH,
                    "recording_method": "owner_authored",
                    "created_at": NOW,
                }
                if saved
                else None
            ),
            "requirements": [
                {
                    "id": "requirement1",
                    "ordinal": 1,
                    "importance": "required",
                    "text": "Python experience required.",
                    "coverage": "supported",
                    "evidence": [_evidence()],
                }
            ],
            "required_evidence_backed_count": 1,
            "prompt_capacity": 12,
            "evidence_gaps": [],
            "prompts": [
                {
                    "id": "prompt1",
                    "category": "key_requirement",
                    "question": "Prepare one concrete example.",
                    "requirement_id": "requirement1",
                    "requirement_text": "Python experience required.",
                    "evidence": [_evidence()],
                    "draft": draft,
                    "missing_sections": (
                        ["task", "action", "result"]
                        if saved
                        else ["situation", "task", "action", "result"]
                    ),
                }
            ],
            "blockers": [],
            "next_steps": [],
        }
    )


def _capacity_projection() -> ApplicationInterviewPreparationResponse:
    value = _projection().model_dump(mode="json")
    requirement = value["requirements"][0]
    value.update(
        {
            "application_id": "capacityapp",
            "status": "blocked",
            "requirements": [
                {
                    **requirement,
                    "id": f"requirement{index:02d}",
                    "ordinal": index,
                    "text": f"Required capability {index}",
                }
                for index in range(1, 14)
            ],
            "required_evidence_backed_count": 13,
            "blockers": ["required_prompt_capacity_exceeded"],
            "next_steps": [
                "13 required evidence-backed requirements exceed the 12-prompt safety limit."
            ],
        }
    )
    return ApplicationInterviewPreparationResponse.model_validate(value)


def _evidence() -> dict[str, object]:
    return {
        "id": "evidence1",
        "version": 2,
        "statement": "Reduced failures by 40%.",
        "source_resume_version_id": "resume1",
        "source_excerpt": "Reduced failures by 40%.",
        "skills": ["Python"],
        "approved_at": NOW,
    }


@dataclass
class FakePreparationStore:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def get_application_interview_preparation(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> ApplicationInterviewPreparationResponse | None:
        self.calls.append(("get", {"owner_id": owner_id, "application_id": application_id}))
        if application_id == "application1":
            return _projection()
        if application_id == "capacityapp":
            return _capacity_projection()
        return None

    def create_interview_preparation_revision(
        self,
        *,
        owner_id: str,
        application_id: str,
        payload: InterviewPreparationRevisionCreate,
        expected_version: int,
        idempotency_key: str,
    ) -> ApplicationInterviewPreparationResponse | None:
        self.calls.append(
            (
                "save",
                {
                    "owner_id": owner_id,
                    "application_id": application_id,
                    "payload": payload,
                    "expected_version": expected_version,
                    "idempotency_key": idempotency_key,
                },
            )
        )
        return _projection(saved=True) if application_id == "application1" else None


@pytest.fixture
def preparation_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakePreparationStore]]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'preparation-router.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("JOB_HUNT_OWNER_ID", "owner")
    monkeypatch.setenv("JOB_HUNT_OWNER_TOKEN_HASH", hash_access_token(OWNER_TOKEN))
    monkeypatch.setenv("JOB_HUNT_SESSION_TTL_DAYS", "30")
    command.upgrade(Config("alembic.ini"), "head")
    database = Database(database_url)
    store = FakePreparationStore()
    app = FastAPI()
    app.include_router(
        create_session_router(database, allowed_origins=[ORIGIN], production=False)
    )
    app.include_router(
        create_application_router(
            database,
            store,  # type: ignore[arg-type]
            allowed_origins=[ORIGIN],
            production=False,
        )
    )
    install_workspace_error_handler(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, store
    database.dispose()


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/session",
        headers={"Origin": ORIGIN},
        json={"owner_token": OWNER_TOKEN},
    )
    assert response.status_code == 200, response.text


def _payload() -> dict[str, object]:
    return {
        "source_fingerprint": SOURCE_HASH,
        "parent_revision_id": None,
        "prompt_drafts": [
            {
                "prompt_id": "prompt1",
                "situation": "Owner fact",
                "task": "",
                "action": "",
                "result": "",
            }
        ],
        "confirm_owner_authored": True,
    }


def test_read_is_authenticated_owner_scoped_and_sets_write_etag(
    preparation_client,
) -> None:
    client, store = preparation_client
    path = "/api/applications/application1/interview-preparation"
    assert client.get(path).status_code == 401
    assert store.calls == []
    _login(client)
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.headers["etag"] == '"2"'
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.json()["truth_policy"] == "owner_authored_only"
    assert store.calls[-1][1]["owner_id"] == "owner"
    assert client.get("/api/applications/missing/interview-preparation").status_code == 404


def test_save_requires_origin_version_and_retry_key_then_forwards_exact_contract(
    preparation_client,
) -> None:
    client, store = preparation_client
    path = "/api/applications/application1/interview-preparation/revisions"
    _login(client)
    assert client.post(path, json=_payload()).status_code == 428
    assert client.post(
        path,
        headers={"Origin": "https://untrusted.example"},
        json=_payload(),
    ).status_code == 403
    assert client.post(path, headers={"Origin": ORIGIN}, json=_payload()).status_code == 428
    response = client.post(
        path,
        headers={
            "Origin": ORIGIN,
            "If-Match": '"2"',
            "Idempotency-Key": "prep-save-v2",
        },
        json=_payload(),
    )
    assert response.status_code == 201, response.text
    assert response.headers["etag"] == '"1"'
    assert response.json()["status"] == "in_progress"
    call = store.calls[-1]
    assert call[0] == "save"
    assert call[1]["expected_version"] == 2
    assert call[1]["idempotency_key"] == "prep-save-v2"
    assert call[1]["payload"].confirm_owner_authored is True


def test_read_exposes_exact_required_prompt_capacity_blocker(
    preparation_client,
) -> None:
    client, _store = preparation_client
    _login(client)
    response = client.get(
        "/api/applications/capacityapp/interview-preparation"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "blocked"
    assert body["required_evidence_backed_count"] == 13
    assert body["prompt_capacity"] == 12
    assert body["blockers"] == ["required_prompt_capacity_exceeded"]
    assert "13 required" in body["next_steps"][0]
