"""Focused contracts for exact manual application transitions."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from job_hunt_agent.application_submission_schemas import (
    ApplicationSubmissionProjection,
    ApplicationSubmissionResponse,
    ApplicationTransitionCreate,
)


NOW = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)
EXACT_IDS = {
    "application_pack_id": "pack1",
    "application_pack_revision_id": "grounding1",
    "application_pack_review_event_id": "groundingreview1",
    "application_artifact_revision_id": "artifact1",
    "application_artifact_approval_event_id": "artifactapproval1",
    "tailored_resume_version_id": "resume2",
}


def test_transition_requests_require_exact_ids_and_literal_confirmation() -> None:
    adapter = TypeAdapter(ApplicationTransitionCreate)
    ready = adapter.validate_python(
        {
            "to_stage": "ready_to_apply",
            **EXACT_IDS,
            "next_action_due_on": "2026-07-15",
            "confirm_ready": True,
        }
    )
    applied = adapter.validate_python(
        {
            "to_stage": "applied",
            **EXACT_IDS,
            "destination_url": "https://careers.example.com/jobs/1/apply",
            "applied_on": "2026-07-14",
            "next_action_due_on": "2026-07-21",
            "confirm_manual_submission": True,
        }
    )

    assert ready.application_pack_revision_id == "grounding1"
    assert applied.destination_url.endswith("/apply")
    with pytest.raises(ValidationError, match="boolean true"):
        adapter.validate_python(
            {
                "to_stage": "ready_to_apply",
                **EXACT_IDS,
                "next_action_due_on": "2026-07-15",
                "confirm_ready": 1,
            }
        )
    with pytest.raises(ValidationError, match="boolean true"):
        adapter.validate_python(
            {
                "to_stage": "applied",
                **EXACT_IDS,
                "destination_url": "https://careers.example.com/jobs/1/apply",
                "applied_on": "2026-07-14",
                "next_action_due_on": "2026-07-21",
                "confirm_manual_submission": False,
            }
        )


def test_reload_projection_is_database_only_stage_aware_and_owner_exact() -> None:
    submission = ApplicationSubmissionResponse(
        id="submission1",
        application_id="application1",
        **EXACT_IDS,
        destination_url="https://careers.example.com/jobs/1/apply",
        applied_on=date(2026, 7, 14),
        submission_method="manual",
        recorded_at=NOW,
        created_at=NOW,
    )
    projection = ApplicationSubmissionProjection(
        application_id="application1",
        stage="applied",
        available_destinations=["https://careers.example.com/jobs/1/apply"],
        first_party_verified=True,
        submission=submission,
    )

    assert projection.data_source == "database"
    assert projection.stage.value == "applied"
    assert projection.submission is not None
    with pytest.raises(ValidationError, match="duplicates"):
        ApplicationSubmissionProjection(
            application_id="application1",
            stage="pursuing",
            available_destinations=[
                "https://careers.example.com/jobs/1",
                "https://careers.example.com/jobs/1",
            ],
            first_party_verified=True,
        )
    with pytest.raises(ValidationError, match="requested application"):
        ApplicationSubmissionProjection(
            application_id="application2",
            stage="applied",
            available_destinations=[],
            first_party_verified=False,
            submission=submission,
        )
    with pytest.raises(ValidationError, match="must expose its submission"):
        ApplicationSubmissionProjection(
            application_id="application1",
            stage="applied",
            available_destinations=[],
            first_party_verified=False,
        )
    with pytest.raises(ValidationError, match="only an applied application"):
        ApplicationSubmissionProjection(
            application_id="application1",
            stage="ready_to_apply",
            available_destinations=[],
            first_party_verified=False,
            submission=submission,
        )
