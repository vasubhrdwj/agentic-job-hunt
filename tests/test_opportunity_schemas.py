"""Focused validation tests for the durable opportunity-radar contracts."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from job_hunt_agent.application_schemas import (
    ActionItemResponse,
    ApplicationActivityEventResponse,
    ApplicationPostingSummary,
    ApplicationSummary,
    PursuitBundle,
)
from job_hunt_agent.opportunity_schemas import (
    CompensationEvidenceFact,
    DateEvidenceFact,
    EmploymentTypeEvidenceFact,
    OpportunityDecisionEvent,
    OpportunityDecisionRequest,
    OpportunityDecisionResponse,
    OpportunityDetailResponse,
    OpportunityFacts,
    OpportunityPosting,
    OpportunityUnknown,
    PostingVersionSummary,
    PursueOpportunityRequest,
    SavedSearchProvenance,
    ScanCounts,
    ScanCreateRequest,
    ScanStatusResponse,
    ScanWarning,
    TextEvidenceFact,
    TodayListResponse,
    TodayOpportunityItem,
    TodayQuery,
    TodayScanHealth,
    TodaySummary,
    TransparentMatchSummary,
)


NOW = datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)


def _warning(**updates: object) -> ScanWarning:
    values: dict[str, object] = {
        "scope": "source",
        "code": "source_timeout",
        "message": "This source could not be refreshed.",
        "retryable": True,
        "company_slug": "example",
        "source": "workday",
        "occurred_at": NOW,
        "last_success_at": NOW - timedelta(days=1),
    }
    values.update(updates)
    return ScanWarning.model_validate(values)


def _facts() -> OpportunityFacts:
    return OpportunityFacts(
        location=TextEvidenceFact(
            state="verified",
            value="Bengaluru",
            source_label="Workday",
            observed_at=NOW,
        ),
        employment_type=EmploymentTypeEvidenceFact(
            state="inferred",
            value="full_time",
            source_label="Job description",
            observed_at=NOW,
        ),
        posted_date=DateEvidenceFact(state="unknown", value=None),
        compensation=CompensationEvidenceFact(state="unknown", value=None),
    )


def _posting(**updates: object) -> OpportunityPosting:
    values: dict[str, object] = {
        "id": "posting1",
        "company": "Example",
        "company_slug": "example",
        "title": "Senior Backend Engineer",
        "summary": "Build reliable identity services.",
        "canonical_url": "https://careers.example.com/jobs/123",
        "source": "workday",
        "source_job_id": "REQ-123",
        "first_party": True,
        "state": "open",
        "change_kind": "new",
        "first_seen_at": NOW - timedelta(hours=1),
        "last_confirmed_at": NOW,
        "changed_at": None,
    }
    values.update(updates)
    return OpportunityPosting.model_validate(values)


def _provenance(search_id: str = "search1") -> SavedSearchProvenance:
    return SavedSearchProvenance(
        saved_search_id=search_id,
        saved_search_name=f"Search {search_id}",
        first_matched_at=NOW - timedelta(hours=1),
        last_matched_at=NOW,
    )


def _not_assessed() -> TransparentMatchSummary:
    return TransparentMatchSummary(
        state="not_assessed",
        not_assessed_reason="assessment_pending",
    )


def _item(**updates: object) -> TodayOpportunityItem:
    values: dict[str, object] = {
        "id": "opportunity1",
        "version": 1,
        "state": "inbox",
        "lane": "core",
        "posting": _posting(),
        "facts": _facts(),
        "unknowns": [
            OpportunityUnknown(
                field="posted_date",
                reason_code="not_reported_by_source",
                message="The source did not report an original posting date.",
            ),
            OpportunityUnknown(
                field="compensation",
                reason_code="not_reported_by_source",
                message="The source did not report compensation.",
            ),
        ],
        "discovered_by": [_provenance()],
        "match": _not_assessed(),
        "latest_decision": None,
        "created_at": NOW - timedelta(hours=1),
        "updated_at": NOW,
    }
    values.update(updates)
    return TodayOpportunityItem.model_validate(values)


def _pursuit_bundle(
    *,
    opportunity_id: str = "opportunity1",
    application_id: str = "application1",
) -> PursuitBundle:
    action = ActionItemResponse(
        id="action1",
        version=1,
        application_id=application_id,
        kind="review_and_prepare_application",
        status="open",
        title="Review the role and prepare the application",
        due_on=date(2026, 7, 15),
        created_at=NOW,
        updated_at=NOW,
    )
    application = ApplicationSummary(
        id=application_id,
        version=1,
        opportunity_id=opportunity_id,
        pursued_posting_version_id="postingversion1",
        stage="pursuing",
        posting=ApplicationPostingSummary(
            id="posting1",
            company="Example",
            title="Senior Backend Engineer",
            canonical_url="https://careers.example.com/jobs/123",
            first_party=True,
            state="open",
        ),
        current_action=action,
        created_at=NOW,
        updated_at=NOW,
    )
    activity = ApplicationActivityEventResponse(
        id="activity1",
        application_id=application_id,
        sequence_number=1,
        event_type="application_created",
        to_stage="pursuing",
        action_item_id=action.id,
        occurred_at=NOW,
    )
    return PursuitBundle(
        application=application,
        activity=activity,
        application_created=True,
    )


def test_posting_closure_can_follow_last_positive_confirmation() -> None:
    closed_at = NOW + timedelta(days=1)
    posting = _posting(
        state="closed",
        change_kind="closed",
        changed_at=closed_at,
    )
    assert posting.changed_at == closed_at

    with pytest.raises(ValidationError, match="non-closure changed_at"):
        _posting(
            change_kind="changed",
            changed_at=closed_at,
        )

    with pytest.raises(ValidationError, match="cannot precede first_seen_at"):
        _posting(
            state="closed",
            change_kind="closed",
            changed_at=NOW - timedelta(hours=2),
        )


def test_scan_create_is_manual_and_rejects_unowned_input() -> None:
    assert ScanCreateRequest().trigger.value == "manual"
    with pytest.raises(ValidationError):
        ScanCreateRequest.model_validate({"trigger": "scheduled"})
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ScanCreateRequest.model_validate({"provider": "gemini"})


def test_scan_counts_are_bounded_and_internally_consistent() -> None:
    counts = ScanCounts(
        sources_total=3,
        sources_completed=3,
        sources_succeeded=2,
        sources_degraded=1,
        observed_postings=12,
        matched_postings=5,
        new_opportunities=2,
        changed_postings=1,
    )
    assert counts.sources_failed == 0

    with pytest.raises(ValidationError, match="succeeded.*degraded.*failed"):
        ScanCounts(
            sources_total=3,
            sources_completed=3,
            sources_succeeded=3,
            sources_degraded=1,
        )
    with pytest.raises(ValidationError, match="matched_postings"):
        ScanCounts(observed_postings=1, matched_postings=2)


def test_scan_status_enforces_lifecycle_degradation_and_utc() -> None:
    response = ScanStatusResponse(
        id="scan1",
        version=3,
        saved_search_id="search1",
        saved_search_version=2,
        status="partial",
        stage="complete",
        queued_at=NOW.astimezone(timezone(timedelta(hours=5, minutes=30))),
        started_at=NOW + timedelta(seconds=1),
        completed_at=NOW + timedelta(seconds=2),
        counts=ScanCounts(
            sources_total=2,
            sources_completed=2,
            sources_succeeded=1,
            sources_degraded=1,
        ),
        warnings=[_warning(occurred_at=NOW + timedelta(seconds=2))],
    )
    assert response.version == 3
    assert response.queued_at.tzinfo is timezone.utc

    with pytest.raises(ValidationError, match="UTC offset"):
        ScanStatusResponse(
            id="scan1",
            version=1,
            saved_search_id="search1",
            saved_search_version=1,
            status="queued",
            stage="queued",
            queued_at=NOW.replace(tzinfo=None),
        )
    with pytest.raises(ValidationError, match="partial scans"):
        ScanStatusResponse(
            id="scan1",
            version=1,
            saved_search_id="search1",
            saved_search_version=1,
            status="partial",
            stage="complete",
            queued_at=NOW,
            started_at=NOW,
            completed_at=NOW,
        )


def test_scan_warnings_use_fixed_safe_codes_and_scoped_metadata() -> None:
    assert _warning().code.value == "source_timeout"
    with pytest.raises(ValidationError):
        _warning(code="TimeoutError: token=secret")
    with pytest.raises(ValidationError, match="company_slug and source"):
        _warning(company_slug=None)
    with pytest.raises(ValidationError, match="cannot name"):
        _warning(scope="scan")


def test_evidence_facts_never_encode_unknown_as_a_value() -> None:
    with pytest.raises(ValidationError, match="unknown facts"):
        TextEvidenceFact(state="unknown", value="Unknown")
    with pytest.raises(ValidationError, match="source evidence"):
        TextEvidenceFact(state="verified", value="Remote")
    with pytest.raises(ValidationError):
        EmploymentTypeEvidenceFact(state="verified", value="unknown")


def test_every_unknown_fact_requires_one_explicit_reason() -> None:
    assert len(_item().unknowns) == 2
    with pytest.raises(ValidationError, match="every unknown fact"):
        _item(unknowns=[])
    with pytest.raises(ValidationError, match="duplicates"):
        _item(
            unknowns=[
                OpportunityUnknown(
                    field="posted_date",
                    reason_code="not_reported_by_source",
                    message="Not reported.",
                ),
                OpportunityUnknown(
                    field="posted_date",
                    reason_code="source_refresh_degraded",
                    message="Refresh degraded.",
                ),
                OpportunityUnknown(
                    field="compensation",
                    reason_code="not_reported_by_source",
                    message="Not reported.",
                ),
            ]
        )


def test_saved_search_provenance_is_ordered_and_deduplicated() -> None:
    with pytest.raises(ValidationError, match="last_matched_at"):
        SavedSearchProvenance(
            saved_search_id="search1",
            saved_search_name="Search",
            first_matched_at=NOW,
            last_matched_at=NOW - timedelta(seconds=1),
        )
    with pytest.raises(ValidationError, match="provenance"):
        _item(discovered_by=[_provenance(), _provenance()])


def test_match_summary_is_transparent_or_explicitly_not_assessed() -> None:
    assessed = TransparentMatchSummary(
        state="assessed",
        algorithm_version="backend-opportunity-fit-v1",
        resume_version_id="resume1",
        assessment_saved_search_id="search1",
        fit_band="strong",
        confidence="high",
        matched_terms=["SCIM", "identity"],
        representative_requirement="Build identity lifecycle services.",
        approved_evidence_ids=["evidence1"],
        strengths=["Approved SCIM evidence supports this requirement."],
        gaps=["Work authorization was not provided."],
    )
    assert assessed.fit_band.value == "strong"
    assert assessed.matched_terms == ["SCIM", "identity"]

    with pytest.raises(ValidationError, match="not-assessed"):
        TransparentMatchSummary(
            state="not_assessed",
            not_assessed_reason="assessment_pending",
            matched_terms=["invented"],
        )
    with pytest.raises(ValidationError, match="algorithm, input versions"):
        TransparentMatchSummary(state="assessed")
    with pytest.raises(ValidationError, match="not-assessed"):
        TransparentMatchSummary(
            state="not_assessed",
            not_assessed_reason="resume_unavailable",
            fit_band="insufficient_data",
        )
    with pytest.raises(ValidationError, match="strengths must not contain duplicates"):
        TransparentMatchSummary(
            state="assessed",
            algorithm_version="backend-opportunity-fit-v1",
            resume_version_id="resume1",
            assessment_saved_search_id="search1",
            fit_band="promising",
            confidence="medium",
            strengths=["Supported by AWS evidence.", "supported by aws evidence."],
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "pursue", "note": "not part of the atomic pursue command"},
        {"action": "pursue", "dismiss_reason": "company"},
        {"action": "dismiss"},
        {"action": "dismiss", "dismiss_reason": "other"},
        {
            "action": "dismiss",
            "dismiss_reason": "company",
            "initial_action_due_on": "2026-07-15",
        },
        {
            "action": "dismiss",
            "dismiss_reason": "company",
            "acquisition_source": "referral",
        },
        {"action": "watch", "dismiss_reason": "company"},
        {"action": "watch", "initial_action_due_on": "2026-07-15"},
        {"action": "watch", "selected_saved_search_id": "search1"},
        {"action": "restore_to_inbox"},
        {
            "action": "restore_to_inbox",
            "restore_decision_event_id": "event1",
            "note": "not allowed",
        },
        {"action": "watch", "note": "x" * 501},
    ],
)
def test_decision_request_rejects_ambiguous_or_unbounded_mutations(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        OpportunityDecisionRequest.model_validate(payload)


def test_watch_dismiss_and_restore_decisions_have_exact_shapes() -> None:
    watch = OpportunityDecisionRequest(action="watch", note="Research the team.")
    dismiss = OpportunityDecisionRequest(
        action="dismiss",
        dismiss_reason="not_a_better_move",
    )
    restore = OpportunityDecisionRequest(
        action="restore_to_inbox",
        restore_decision_event_id="event1",
    )
    assert watch.action.value == "watch"
    assert dismiss.dismiss_reason is not None
    assert restore.restore_decision_event_id == "event1"


def test_pursue_request_has_exact_due_date_and_acquisition_attribution() -> None:
    pursue = OpportunityDecisionRequest(
        action="pursue",
        initial_action_due_on=date(2026, 7, 15),
    )
    narrow = PursueOpportunityRequest(initial_action_due_on=date(2026, 7, 16))

    assert pursue.initial_action_due_on == date(2026, 7, 15)
    assert narrow.action.value == "pursue"
    assert narrow.acquisition_source.value == "job_hunt_search"
    assert narrow.selected_saved_search_id is None
    selected = PursueOpportunityRequest(
        selected_saved_search_id="search1",
    )
    referral = PursueOpportunityRequest(acquisition_source="referral")
    assert selected.selected_saved_search_id == "search1"
    assert referral.acquisition_source.value == "referral"
    with pytest.raises(ValidationError, match="only job_hunt_search"):
        PursueOpportunityRequest(
            acquisition_source="referral",
            selected_saved_search_id="search1",
        )
    with pytest.raises(ValidationError):
        PursueOpportunityRequest.model_validate({"action": "watch"})
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PursueOpportunityRequest.model_validate(
            {"action": "pursue", "note": "not accepted"}
        )


@pytest.mark.parametrize("previous_state", ["inbox", "watch", "dismiss"])
def test_pursue_event_enters_pursued_from_a_practical_prior_state(
    previous_state: str,
) -> None:
    event = OpportunityDecisionEvent(
        id="decision1",
        opportunity_id="opportunity1",
        action="pursue",
        previous_state=previous_state,
        state="pursued",
        created_at=NOW,
    )
    assert event.state.value == "pursued"


def test_pursue_event_and_response_require_the_atomic_pursuit_bundle() -> None:
    event = OpportunityDecisionEvent(
        id="decision1",
        opportunity_id="opportunity1",
        action="pursue",
        previous_state="inbox",
        state="pursued",
        created_at=NOW,
    )
    response = OpportunityDecisionResponse(
        opportunity_id="opportunity1",
        opportunity_version=2,
        state="pursued",
        event=event,
        pursuit=_pursuit_bundle(),
    )
    assert response.pursuit is not None
    assert response.pursuit.application.current_action.due_on == date(2026, 7, 15)

    with pytest.raises(ValidationError, match="pursuit bundle"):
        OpportunityDecisionResponse(
            opportunity_id="opportunity1",
            opportunity_version=2,
            state="pursued",
            event=event,
        )
    with pytest.raises(ValidationError, match="belong"):
        OpportunityDecisionResponse(
            opportunity_id="opportunity1",
            opportunity_version=2,
            state="pursued",
            event=event,
            pursuit=_pursuit_bundle(opportunity_id="opportunity2"),
        )


def test_non_pursue_response_cannot_smuggle_a_pursuit_bundle() -> None:
    event = OpportunityDecisionEvent(
        id="event1",
        opportunity_id="opportunity1",
        action="watch",
        previous_state="inbox",
        state="watch",
        created_at=NOW,
    )
    with pytest.raises(ValidationError, match="only pursue"):
        OpportunityDecisionResponse(
            opportunity_id="opportunity1",
            opportunity_version=2,
            state="watch",
            event=event,
            pursuit=_pursuit_bundle(),
        )


def test_restore_can_compensate_an_accidental_pursuit() -> None:
    event = OpportunityDecisionEvent(
        id="event2",
        opportunity_id="opportunity1",
        action="restore_to_inbox",
        previous_state="pursued",
        state="inbox",
        restores_event_id="decision1",
        created_at=NOW,
    )
    assert event.previous_state.value == "pursued"
    assert event.restores_event_id == "decision1"


def test_decision_response_is_bound_to_one_opportunity_and_state() -> None:
    event = OpportunityDecisionEvent(
        id="event1",
        opportunity_id="opportunity1",
        action="watch",
        previous_state="inbox",
        state="watch",
        note="Review after the earnings call.",
        created_at=NOW,
    )
    response = OpportunityDecisionResponse(
        opportunity_id="opportunity1",
        opportunity_version=2,
        state="watch",
        event=event,
    )
    assert response.event.id == "event1"
    with pytest.raises(ValidationError, match="belong"):
        OpportunityDecisionResponse(
            opportunity_id="opportunity2",
            opportunity_version=2,
            state="watch",
            event=event,
        )


def test_today_query_enforces_opaque_filters_cursor_and_page_limit() -> None:
    query = TodayQuery(
        view="watching",
        scan_id="scan_123-Z",
        cursor="abc_123-Z",
        limit=50,
    )
    assert query.limit == 50
    assert query.scan_id == "scan_123-Z"
    with pytest.raises(ValidationError):
        TodayQuery(cursor="not a cursor")
    with pytest.raises(ValidationError):
        TodayQuery(limit=51)
    with pytest.raises(ValidationError):
        TodayQuery(saved_search_id="../../other-owner")
    with pytest.raises(ValidationError):
        TodayQuery(scan_id="../../other-owner")


def test_today_list_is_explicitly_database_only() -> None:
    response = TodayListResponse(
        as_of=NOW,
        summary=TodaySummary(needs_decision=1, watching=0, dismissed=0),
        scan_health=TodayScanHealth(
            state="healthy",
            active_searches=1,
            last_attempt_at=NOW,
            last_success_at=NOW,
        ),
        items=[_item()],
    )
    assert response.data_source == "database"
    with pytest.raises(ValidationError, match="literal_error"):
        TodayListResponse.model_validate(
            {**response.model_dump(mode="json"), "data_source": "provider"}
        )


def test_opportunity_detail_rejects_unsafe_urls_and_inconsistent_history() -> None:
    item = _item()
    detail = OpportunityDetailResponse(
        **item.model_dump(),
        description="Full job description.",
        apply_urls=["https://careers.example.com/jobs/123/apply"],
        posting_versions=[
            PostingVersionSummary(
                version=1,
                observed_at=NOW,
                change_kind="new",
            )
        ],
    )
    assert detail.data_source == "database"

    with pytest.raises(ValidationError, match="HTTPS URL"):
        _posting(canonical_url="javascript:alert(1)")
    with pytest.raises(ValidationError, match="ordered"):
        OpportunityDetailResponse(
            **item.model_dump(),
            apply_urls=["https://careers.example.com/jobs/123"],
            posting_versions=[
                PostingVersionSummary(
                    version=2,
                    observed_at=NOW,
                    change_kind="new",
                ),
                PostingVersionSummary(
                    version=1,
                    observed_at=NOW,
                    change_kind="new",
                ),
            ],
        )


def test_opaque_resource_ids_are_rejected_at_every_boundary() -> None:
    with pytest.raises(ValidationError):
        ScanStatusResponse(
            id="scan/id",
            version=1,
            saved_search_id="search1",
            saved_search_version=1,
            status="queued",
            stage="queued",
            queued_at=NOW,
        )
