"""Model metadata invariants for immutable application artifacts."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, UniqueConstraint

from job_hunt_agent.models import ApplicationArtifactEvent, ApplicationArtifactRevision


def test_artifact_revisions_and_events_are_append_only_and_owner_scoped() -> None:
    revision_columns = ApplicationArtifactRevision.__table__.columns
    event_columns = ApplicationArtifactEvent.__table__.columns
    assert "updated_at" not in revision_columns
    assert "version" not in revision_columns
    assert "updated_at" not in event_columns
    assert "version" not in event_columns

    revision_uniques = {
        item.name: tuple(column.name for column in item.columns)
        for item in ApplicationArtifactRevision.__table__.constraints
        if isinstance(item, UniqueConstraint)
    }
    assert revision_uniques["uq_application_artifact_revisions_owner_number"] == (
        "owner_id",
        "application_pack_id",
        "revision_number",
    )
    event_uniques = {
        item.name: tuple(column.name for column in item.columns)
        for item in ApplicationArtifactEvent.__table__.constraints
        if isinstance(item, UniqueConstraint)
    }
    assert event_uniques["uq_application_artifact_events_owner_terminal"] == (
        "owner_id",
        "application_pack_id",
        "artifact_revision_id",
    )
    assert event_uniques["uq_application_artifact_events_submission_ref"] == (
        "owner_id",
        "application_id",
        "application_pack_id",
        "artifact_revision_id",
        "id",
    )
    checks = {
        item.name
        for item in ApplicationArtifactEvent.__table__.constraints
        if isinstance(item, CheckConstraint)
    }
    assert "ck_application_artifact_events_event_resume_shape" in checks
