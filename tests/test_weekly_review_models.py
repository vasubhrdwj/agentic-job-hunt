"""Relational invariants for Phase 6B attribution and action reviews."""

from __future__ import annotations

from datetime import timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from job_hunt_agent.database import Database
from job_hunt_agent.models import (
    ApplicationActionReview,
    ApplicationMetricSnapshot,
    JobPosting,
    JobPostingVersion,
)
from tests.test_application_models import (
    DUE_ON,
    NOW,
    _add_pursuit_graph,
    application_db,
)


def _snapshot(**overrides: object) -> ApplicationMetricSnapshot:
    values: dict[str, object] = {
        "id": "snapshot-a",
        "owner_id": "owner-a",
        "application_id": "application-a",
        "job_posting_id": "posting-a",
        "pursued_posting_version_id": "posting-version-a",
        "acquisition_source": "job_hunt_search",
        "attribution_status": "attribution_missing",
        "saved_search_id": None,
        "saved_search_version": None,
        "saved_search_name": None,
        "career_track_id": None,
        "career_track_version": None,
        "career_track_name": None,
        "assessment_state": "not_assessed",
        "assessment_reason": "assessment_pending",
        "recorded_at": NOW,
    }
    values.update(overrides)
    return ApplicationMetricSnapshot(**values)


def _review(**overrides: object) -> ApplicationActionReview:
    values: dict[str, object] = {
        "id": "review-a",
        "owner_id": "owner-a",
        "application_id": "application-a",
        "action_item_id": "action-a",
        "decision": "continue",
        "prior_due_on": DUE_ON,
        "new_due_on": DUE_ON + timedelta(days=7),
        "prior_action_version": 1,
        "new_action_version": 2,
        "prior_application_version": 1,
        "new_application_version": 2,
        "recording_method": "manual",
        "recorded_at": NOW,
        "idempotency_key_hash": "a" * 64,
    }
    values.update(overrides)
    return ApplicationActionReview(**values)


def _add_unrelated_posting_version(database: Database) -> None:
    with database.session() as session:
        session.add(
            JobPosting(
                id="posting-b",
                owner_id="owner-a",
                identity_kind="native",
                identity_key="source:greenhouse:other:456",
                identity_key_hash="8" * 64,
                source="greenhouse",
                company_slug="other",
                source_job_id="456",
                canonical_url="https://boards.greenhouse.io/other/jobs/456",
                lifecycle_state="open",
                consecutive_complete_omissions=0,
                first_confirmed_at=NOW,
                last_confirmed_at=NOW,
                version=1,
            )
        )
        session.flush()
        session.add(
            JobPostingVersion(
                id="posting-version-b",
                owner_id="owner-a",
                job_posting_id="posting-b",
                version_number=1,
                content_hash="9" * 64,
                source="greenhouse",
                source_job_id="456",
                company_name="Other",
                title="Unrelated role",
                canonical_url="https://boards.greenhouse.io/other/jobs/456",
                apply_urls=["https://boards.greenhouse.io/other/jobs/456"],
                location="Remote",
                summary="An unrelated role.",
                description="An unrelated role and posting version.",
                employment_type="full_time",
                source_facts={},
                source_confidence=1.0,
                observed_at=NOW,
            )
        )


def test_metric_snapshot_is_one_immutable_record_per_application(
    application_db: Database,
) -> None:
    with application_db.session() as session:
        _add_pursuit_graph(session)
        session.add(_snapshot())

    with application_db.session() as session:
        snapshot = session.scalar(select(ApplicationMetricSnapshot))

    assert snapshot is not None
    assert snapshot.acquisition_source == "job_hunt_search"
    assert snapshot.attribution_status == "attribution_missing"
    assert snapshot.recorded_at.replace(tzinfo=timezone.utc) == NOW
    assert "updated_at" not in ApplicationMetricSnapshot.__table__.columns
    assert "version" not in ApplicationMetricSnapshot.__table__.columns

    with pytest.raises(IntegrityError):
        with application_db.session() as session:
            session.add(_snapshot(id="snapshot-duplicate"))


def test_non_search_capture_has_no_fabricated_search_or_track(
    application_db: Database,
) -> None:
    with application_db.session() as session:
        _add_pursuit_graph(session)
        session.add(
            _snapshot(
                acquisition_source="referral",
                attribution_status="captured",
            )
        )

    with application_db.session() as session:
        snapshot = session.get(ApplicationMetricSnapshot, "snapshot-a")

    assert snapshot is not None
    assert snapshot.acquisition_source == "referral"
    assert snapshot.saved_search_id is None
    assert snapshot.career_track_id is None


def test_assessed_snapshot_requires_a_frozen_band_and_algorithm_version(
    application_db: Database,
) -> None:
    with application_db.session() as session:
        _add_pursuit_graph(session)
        session.add(
            _snapshot(
                assessment_state="assessed",
                assessment_band="strong",
                assessment_algorithm_version="fit-v1",
                assessment_reason=None,
            )
        )

    with application_db.session() as session:
        snapshot = session.get(ApplicationMetricSnapshot, "snapshot-a")

    assert snapshot is not None
    assert snapshot.assessment_band == "strong"
    assert snapshot.assessment_algorithm_version == "fit-v1"
    assert snapshot.assessment_reason is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"acquisition_source": "invented"},
        {"attribution_status": "inferred"},
        {"attribution_status": "captured"},
        {"saved_search_id": "search-without-capture"},
        {
            "acquisition_source": "referral",
            "attribution_status": "captured",
            "saved_search_id": "search-a",
        },
        {"saved_search_version": 0},
        {"career_track_version": 0},
        {"assessment_state": "assessed"},
        {"assessment_band": "strong"},
        {"assessment_algorithm_version": "fit-v1"},
        {
            "assessment_state": "assessed",
            "assessment_band": "invented",
            "assessment_algorithm_version": "fit-v1",
            "assessment_reason": None,
        },
        {
            "assessment_state": "assessed",
            "assessment_band": "strong",
            "assessment_algorithm_version": None,
            "assessment_reason": None,
        },
        {"assessment_reason": "fit_unknown"},
    ],
)
def test_metric_snapshot_shape_and_honest_unknowns_fail_closed(
    application_db: Database,
    overrides: dict[str, object],
) -> None:
    with application_db.session() as session:
        _add_pursuit_graph(session)

    with pytest.raises(IntegrityError):
        with application_db.session() as session:
            session.add(_snapshot(**overrides))


def test_metric_snapshot_rejects_cross_owner_and_unrelated_pinned_version(
    application_db: Database,
) -> None:
    with application_db.session() as session:
        _add_pursuit_graph(session)

    with pytest.raises(IntegrityError):
        with application_db.session() as session:
            session.add(_snapshot(owner_id="owner-b"))

    _add_unrelated_posting_version(application_db)
    with pytest.raises(IntegrityError):
        with application_db.session() as session:
            session.add(
                _snapshot(
                    job_posting_id="posting-b",
                    pursued_posting_version_id="posting-version-b",
                )
            )


def test_action_review_is_append_only_and_names_the_exact_action(
    application_db: Database,
) -> None:
    with application_db.session() as session:
        _add_pursuit_graph(session)
        session.add(_review())

    with application_db.session() as session:
        review = session.scalar(select(ApplicationActionReview))

    assert review is not None
    assert review.prior_due_on == DUE_ON
    assert review.new_due_on == DUE_ON + timedelta(days=7)
    assert review.new_action_version == review.prior_action_version + 1
    assert review.new_application_version == review.prior_application_version + 1
    assert "updated_at" not in ApplicationActionReview.__table__.columns
    assert "version" not in ApplicationActionReview.__table__.columns

    with pytest.raises(IntegrityError):
        with application_db.session() as session:
            session.add(_review(id="review-b"))

    with application_db.session() as session:
        assert session.scalar(select(func.count(ApplicationActionReview.id))) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"owner_id": "owner-b"},
        {"application_id": "application-missing"},
        {"action_item_id": "action-missing"},
        {"decision": "reject"},
        {"new_due_on": DUE_ON},
        {"new_due_on": DUE_ON - timedelta(days=1)},
        {"prior_action_version": 0, "new_action_version": 1},
        {"new_action_version": 3},
        {"prior_application_version": 0, "new_application_version": 1},
        {"new_application_version": 3},
        {"recording_method": "automatic"},
        {"idempotency_key_hash": "short"},
    ],
)
def test_action_review_graph_decision_dates_versions_and_audit_fail_closed(
    application_db: Database,
    overrides: dict[str, object],
) -> None:
    with application_db.session() as session:
        _add_pursuit_graph(session)

    with pytest.raises(IntegrityError):
        with application_db.session() as session:
            session.add(_review(**overrides))
