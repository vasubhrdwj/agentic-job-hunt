"""Real PostgreSQL gate for the first owner workspace persistence path."""

from __future__ import annotations

import os
from datetime import time
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete
from sqlalchemy.engine import make_url

from job_hunt_agent.database import Database
from job_hunt_agent.models import Owner, ResumeVersion
from job_hunt_agent.profile_schemas import (
    CandidateProfileWrite,
    CareerTrackCreate,
    ResumeVersionCreate,
    SavedSearchCreate,
    SavedSearchCriteria,
    SavedSearchSchedule,
)
from job_hunt_agent.security import DataKeyring
from job_hunt_agent.sqlalchemy_owner_workspace import SqlAlchemyOwnerWorkspaceStore


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set TEST_DATABASE_URL to the migrated disposable Postgres database",
)


def test_postgres_owner_workspace_vertical_path_is_durable_and_private() -> None:
    if make_url(TEST_DATABASE_URL).get_backend_name() != "postgresql":
        pytest.skip("workspace persistence gate requires PostgreSQL")
    database = Database(TEST_DATABASE_URL)
    if not database.migrations_current():
        database.dispose()
        pytest.fail("TEST_DATABASE_URL must be migrated to current Alembic head")
    owner_id = f"pg-owner-workspace-{uuid4().hex}"
    marker = f"PRIVATE POSTGRES RESUME {uuid4().hex}"
    keyring = DataKeyring([("pg-v1", Fernet.generate_key().decode("ascii"))])
    store = SqlAlchemyOwnerWorkspaceStore(database, keyring)
    try:
        with database.session() as session:
            session.add(Owner(id=owner_id, display_name="PG Workspace", timezone="UTC"))
        store.put_profile(
            owner_id=owner_id,
            payload=CandidateProfileWrite(
                career_thesis="Move into higher-impact backend roles",
                employment_types=["full_time"],
            ),
            expected_version=0,
        )
        resume = store.create_resume_version(
            owner_id=owner_id,
            payload=ResumeVersionCreate(label="Base", content=marker),
            idempotency_key="resume-1",
        )
        track = store.create_career_track(
            owner_id=owner_id,
            payload=CareerTrackCreate(
                name="Backend",
                role_families=["Backend Engineer"],
                seniority_levels=["senior"],
                target_locations=["Remote"],
            ),
            idempotency_key="track-1",
        )
        search = store.create_saved_search(
            owner_id=owner_id,
            payload=SavedSearchCreate(
                name="Daily backend",
                career_track_id=track.id,
                resume_version_id=None,
                criteria=SavedSearchCriteria(
                    role_keywords=["backend"],
                    seniority="senior",
                    location=["Remote"],
                ),
                schedule=SavedSearchSchedule(
                    cadence="daily", timezone="UTC", local_time=time(8, 0)
                ),
            ),
            idempotency_key="search-1",
        )
        assert search.resume_version_id == resume.id
        built = store.build_hunt_input(owner_id=owner_id, saved_search_id=search.id)
        assert built is not None and built.ready and built.input is not None
        assert built.input.resume_text == marker
        with database.session() as session:
            resume_row = session.get(ResumeVersion, resume.id)
            assert resume_row is not None and marker not in resume_row.encrypted_content
    finally:
        with database.session() as session:
            session.execute(delete(Owner).where(Owner.id == owner_id))
        database.dispose()
