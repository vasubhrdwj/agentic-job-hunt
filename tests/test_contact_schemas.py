"""Focused validation tests for the database-only contact bench contract."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from job_hunt_agent.contact_schemas import (
    ApplicationContactBenchResponse,
    ContactBenchItem,
    ContactBenchResult,
    ContactSearchSnapshot,
)


NOW = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)
SHORTFALL = {
    "code": "insufficient_verified_profiles",
    "count": 2,
    "detail": "Only three profiles had current-employer evidence above the threshold.",
}


def _contact(rank: int = 1, **updates: object) -> ContactBenchItem:
    values: dict[str, object] = {
        "id": f"applicationcontact{rank}",
        "contact_id": f"contact{rank}",
        "version": 1,
        "public_name": f"Person {rank}",
        "profile_url": f"https://www.linkedin.com/in/person-{rank}",
        "profile_source": "linkedin",
        "lifecycle": "active",
        "current_title": "Staff Engineer",
        "current_company": "Example",
        "category": "team_peer",
        "confidence": 0.9,
        "verified_at": NOW,
        "employer_evidence": {
            "excerpt": "Currently a Staff Engineer at Example.",
            "url": f"https://www.linkedin.com/in/person-{rank}",
            "source": "linkedin",
            "observed_at": NOW,
        },
        "why_relevant": "Works on the team adjacent to this role.",
        "relationship": {"status": "unknown", "summary": None, "url": None},
        "team_proximity": {
            "status": "verified",
            "summary": "Public team page lists the same engineering group.",
            "url": "https://example.com/company/team",
        },
        "score_total": 810,
        "score_components": {"employer": 400, "relevance": 410},
        "scoring_version": "contact-score-v1",
        "bench_rank": rank,
        "wave": rank,
        "bench_state": "reserve",
        "cooldown_until": None,
        "unlocked_at": None,
    }
    values.update(updates)
    return ContactBenchItem.model_validate(values)


def _search(**updates: object) -> ContactSearchSnapshot:
    values: dict[str, object] = {
        "id": "contactplan1",
        "version": 1,
        "plan_number": 1,
        "status": "completed",
        "target_count": 5,
        "candidate_limit": 12,
        "confidence_floor": 0.75,
        "discovered_count": 7,
        "evidence_verified_count": 3,
        "selected_count": 3,
        "coverage_status": "partial",
        "exhausted": True,
        "retryable": False,
        "shortfall_reasons": [SHORTFALL],
        "error_code": None,
        "started_at": NOW - timedelta(minutes=2),
        "finalized_at": NOW,
        "created_at": NOW - timedelta(minutes=3),
        "updated_at": NOW,
    }
    values.update(updates)
    return ContactSearchSnapshot.model_validate(values)


def _result(**updates: object) -> ContactBenchResult:
    contacts = [_contact(rank) for rank in range(1, 4)]
    values: dict[str, object] = {
        "contact_plan_id": "contactplan1",
        "plan_number": 1,
        "target_count": 5,
        "verified_count": 3,
        "coverage_status": "partial",
        "exhausted": True,
        "shortfall_reasons": [SHORTFALL],
        "contacts": contacts,
        "completed_at": NOW,
    }
    values.update(updates)
    return ContactBenchResult.model_validate(values)


def test_not_started_is_explicit_and_cannot_contain_phantom_results() -> None:
    response = ApplicationContactBenchResponse(
        application_id="application1",
        status="not_started",
        verified_count=0,
        coverage_status="not_started",
    )

    assert response.data_source == "database"
    assert response.current_search is None
    assert response.last_completed_result is None

    with pytest.raises(ValidationError, match="not_started"):
        ApplicationContactBenchResponse(
            application_id="application1",
            status="not_started",
            verified_count=3,
            coverage_status="partial",
            last_completed_result=_result(),
        )


def test_partial_result_returns_only_verified_people_and_requires_reasons() -> None:
    response = ApplicationContactBenchResponse(
        application_id="application1",
        status="completed",
        verified_count=3,
        coverage_status="partial",
        current_search=_search(),
        last_completed_result=_result(),
    )

    assert response.verified_count == 3
    assert len(response.last_completed_result.contacts) == 3  # type: ignore[union-attr]

    with pytest.raises(ValidationError, match="shortfall reason"):
        _result(shortfall_reasons=[])
    with pytest.raises(ValidationError, match="verified_count"):
        _result(verified_count=5)
    with pytest.raises(ValidationError):
        _contact(confidence=0.74)


def test_retry_progress_preserves_the_last_completed_bench() -> None:
    retry = _search(
        id="contactplan2",
        plan_number=2,
        status="queued",
        discovered_count=0,
        evidence_verified_count=0,
        selected_count=0,
        coverage_status="pending",
        exhausted=False,
        retryable=False,
        shortfall_reasons=[],
        started_at=None,
        finalized_at=None,
        created_at=NOW + timedelta(minutes=1),
        updated_at=NOW + timedelta(minutes=1),
    )
    response = ApplicationContactBenchResponse(
        application_id="application1",
        status="queued",
        verified_count=3,
        coverage_status="partial",
        current_search=retry,
        last_completed_result=_result(),
    )

    assert response.current_search.plan_number == 2  # type: ignore[union-attr]
    assert response.last_completed_result.plan_number == 1  # type: ignore[union-attr]
    assert response.verified_count == 3


def test_completed_current_search_must_be_the_returned_result() -> None:
    with pytest.raises(ValidationError, match="completed current search"):
        ApplicationContactBenchResponse(
            application_id="application1",
            status="completed",
            verified_count=3,
            coverage_status="partial",
            current_search=_search(id="contactplan2", plan_number=2),
            last_completed_result=_result(),
        )


def test_contract_rejects_untrusted_urls_extra_fields_and_invalid_progress() -> None:
    with pytest.raises(ValidationError, match="HTTPS URL"):
        _contact(profile_url="javascript:alert(1)")
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ContactBenchItem.model_validate(
            {**_contact().model_dump(), "private_provider_payload": {"secret": True}}
        )
    with pytest.raises(ValidationError, match="ordered"):
        _search(
            status="running",
            discovered_count=1,
            evidence_verified_count=2,
            selected_count=0,
            coverage_status="pending",
            exhausted=False,
            shortfall_reasons=[],
            finalized_at=None,
        )


def test_shortfall_reasons_are_strict_counted_and_distinct() -> None:
    with pytest.raises(ValidationError, match="shortfall reasons must be distinct"):
        _result(shortfall_reasons=[SHORTFALL, {**SHORTFALL, "count": 1}])
    with pytest.raises(ValidationError):
        _result(shortfall_reasons=[{**SHORTFALL, "count": 0}])
    with pytest.raises(ValidationError):
        _result(shortfall_reasons=[{**SHORTFALL, "detail": " "}])
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _result(shortfall_reasons=[{**SHORTFALL, "provider_payload": "private"}])
