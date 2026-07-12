"""Contract tests for the first practical owner-workspace slice."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from job_hunt_agent.profile_schemas import (
    AchievementEvidenceCreate,
    AchievementEvidencePatch,
    CandidateProfileWrite,
    CareerPriorities,
    CareerTrackCreate,
    HuntInput,
    ResumeVersionSummary,
    SavedSearchCriteria,
    SavedSearchHuntInputResponse,
    SavedSearchSchedule,
)


def _criteria(**updates: object) -> SavedSearchCriteria:
    values: dict[str, object] = {
        "role_keywords": ["identity", "SCIM"],
        "seniority": "senior",
        "location": ["Remote-India"],
        "employment_types": ["full_time"],
        "country": "in",
    }
    values.update(updates)
    return SavedSearchCriteria.model_validate(values)


def _resume_summary() -> ResumeVersionSummary:
    now = datetime.now(timezone.utc)
    return ResumeVersionSummary(
        id="resume1",
        label="Base resume",
        source="pasted",
        parent_resume_version_id=None,
        is_base=True,
        character_count=100,
        version=2,
        created_at=now,
        updated_at=now,
    )


def test_saved_search_criteria_is_losslessly_job_criteria_compatible() -> None:
    criteria = _criteria(comp_min_lpa=35, comp_max_lpa=60, max_age_days=30)
    legacy = criteria.to_job_criteria()

    assert legacy.model_dump(mode="json") == criteria.model_dump(mode="json")
    assert legacy.comp_min_lpa == 35
    assert legacy.comp_max_lpa == 60


def test_saved_search_rejects_contradictory_comp_and_deferred_fields() -> None:
    with pytest.raises(ValidationError, match="comp_min_lpa"):
        _criteria(comp_min_lpa=61, comp_max_lpa=60)
    with pytest.raises(ValidationError):
        _criteria(employment_types=["unknown"])
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SavedSearchCriteria.model_validate(
            {
                **_criteria().model_dump(mode="json"),
                "salary_unknown_policy": "show",
            }
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"cadence": "manual", "timezone": "Asia/Kolkata", "local_time": "08:30"},
            "manual schedules",
        ),
        ({"cadence": "daily", "timezone": "Asia/Kolkata"}, "require local_time"),
        (
            {
                "cadence": "weekly",
                "timezone": "Asia/Kolkata",
                "local_time": "08:30",
            },
            "weekly schedules",
        ),
        (
            {"cadence": "daily", "timezone": "Not/A_Timezone", "local_time": "08:30"},
            "IANA timezone",
        ),
    ],
)
def test_schedule_rejects_invalid_timezone_and_cadence_shapes(
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        SavedSearchSchedule.model_validate(payload)


def test_schedule_accepts_timezone_correct_weekly_wall_time() -> None:
    schedule = SavedSearchSchedule.model_validate(
        {
            "cadence": "weekly",
            "timezone": "Asia/Kolkata",
            "local_time": "08:30",
            "days_of_week": ["mon", "thu"],
        }
    )
    assert schedule.local_time is not None
    assert schedule.local_time.isoformat() == "08:30:00"


def test_profile_preferences_exclude_unknown_employment_type() -> None:
    profile = CandidateProfileWrite(employment_types=["full_time", "contract"])
    assert profile.employment_types == ["full_time", "contract"]
    with pytest.raises(ValidationError):
        CandidateProfileWrite(employment_types=["unknown"])


def test_evidence_is_pending_by_contract_until_explicit_patch() -> None:
    created = AchievementEvidenceCreate(
        statement="Reduced identity sync failures by 40%.",
        skills=["SCIM", "Reliability"],
        origin="resume_suggestion",
    )
    assert "approval_state" not in created.model_dump()
    approved = AchievementEvidencePatch(approval_state="approved")
    assert approved.approval_state == "approved"
    with pytest.raises(ValidationError):
        AchievementEvidenceCreate.model_validate(
            {**created.model_dump(), "approval_state": "approved"}
        )


def test_career_track_supports_all_current_seniority_levels_without_duplicates() -> None:
    track = CareerTrackCreate(
        name="Identity platform",
        role_families=["Backend", "Platform"],
        seniority_levels=["junior", "mid", "senior", "staff"],
        target_locations=["Remote-India"],
    )
    assert len(track.seniority_levels) == 4
    with pytest.raises(ValidationError, match="duplicates"):
        CareerTrackCreate(
            name="Duplicate",
            role_families=["Backend", "backend"],
            seniority_levels=["senior"],
            target_locations=["Remote-India"],
        )
    with pytest.raises(ValidationError, match="at least one career priority"):
        CareerPriorities(
            compensation=0,
            scope=0,
            learning=0,
            company_quality=0,
            flexibility=0,
        )


def test_hunt_input_projection_requires_truthful_readiness() -> None:
    resume = _resume_summary()
    ready = SavedSearchHuntInputResponse(
        saved_search_id="search1",
        saved_search_version=1,
        career_track_id="track1",
        career_track_version=2,
        resume=resume,
        ready=True,
        blockers=[],
        warnings=[],
        input=HuntInput(
            resume_text="Built identity systems.",
            criteria=_criteria(),
            pack="backend_india",
            use_self_rag=True,
        ),
    )
    assert ready.input is not None
    assert ready.input.provider_consent_required is True

    with pytest.raises(ValidationError, match="ready hunt input"):
        SavedSearchHuntInputResponse(
            saved_search_id="search1",
            saved_search_version=1,
            career_track_id="track1",
            career_track_version=2,
            resume=resume,
            ready=True,
            blockers=["saved_search_inactive"],
            warnings=[],
            input=None,
        )
