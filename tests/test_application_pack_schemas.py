"""Contract tests for the grounded application-pack workspace."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from job_hunt_agent.application_pack_schemas import (
    ApplicationPackEventCreate,
    ApplicationPackRequirementReview,
    ApplicationPackRevisionCreate,
    ApplicationPackRevisionResponse,
    ApplicationPackResponse,
)


NOW = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)
DESCRIPTION = "Requirements:\n- Five years of Python experience.\n- Kubernetes preferred."
FIRST = "Five years of Python experience."
FIRST_START = DESCRIPTION.index(FIRST)


def _requirement(**updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": "requirement_01",
        "ordinal": 1,
        "importance": "required",
        "text": FIRST,
        "source_start": FIRST_START,
        "source_end": FIRST_START + len(FIRST),
        "coverage": "unsupported",
        "evidence_refs": [],
    }
    values.update(updates)
    return values


def _revision(**updates: object) -> ApplicationPackRevisionResponse:
    response_requirement = _requirement()
    response_requirement.pop("evidence_refs")
    values: dict[str, object] = {
        "id": "revision1",
        "application_pack_id": "pack1",
        "parent_revision_id": None,
        "revision_number": 1,
        "source": "extracted",
        "extraction_version": "requirements-v1",
        "job_description_source": "persisted_description",
        "job_description": DESCRIPTION,
        "requirements": [
            {
                **response_requirement,
                "evidence": [],
            }
        ],
        "created_at": NOW,
    }
    values.update(updates)
    return ApplicationPackRevisionResponse.model_validate(values)


def test_requirement_review_requires_truthful_coverage_and_exact_types() -> None:
    unsupported = ApplicationPackRequirementReview.model_validate(_requirement())
    assert unsupported.coverage.value == "unsupported"

    with pytest.raises(ValidationError, match="require evidence_refs"):
        ApplicationPackRequirementReview.model_validate(
            _requirement(coverage="supported")
        )
    with pytest.raises(ValidationError, match="cannot include evidence_refs"):
        ApplicationPackRequirementReview.model_validate(
            _requirement(
                evidence_refs=[{"id": "evidence1", "version": 1}],
            )
        )
    with pytest.raises(ValidationError):
        ApplicationPackRequirementReview.model_validate(
            _requirement(source_start=True)
        )


def test_full_revision_rejects_duplicate_ids_ordinals_and_spans() -> None:
    base = _requirement()
    with pytest.raises(ValidationError, match="duplicate ids"):
        ApplicationPackRevisionCreate.model_validate(
            {
                "parent_revision_id": "revision1",
                "requirements": [base, {**base, "ordinal": 2}],
            }
        )
    with pytest.raises(ValidationError, match="duplicate ordinals"):
        ApplicationPackRevisionCreate.model_validate(
            {
                "parent_revision_id": "revision1",
                "requirements": [
                    base,
                    {
                        **base,
                        "id": "requirement_02",
                        "source_start": 0,
                        "source_end": 8,
                        "text": "Requirem",
                    },
                ],
            }
        )


@pytest.mark.parametrize("value", [False, 1, "true"])
def test_inline_review_confirmation_requires_json_boolean_true(value: object) -> None:
    with pytest.raises(ValidationError, match="boolean true"):
        ApplicationPackRevisionCreate.model_validate(
            {
                "parent_revision_id": "revision1",
                "requirements": [_requirement()],
                "confirm_requirements_reviewed": value,
            }
        )

    confirmed = ApplicationPackRevisionCreate.model_validate(
        {
            "parent_revision_id": "revision1",
            "requirements": [_requirement()],
            "confirm_requirements_reviewed": True,
        }
    )
    assert confirmed.confirm_requirements_reviewed is True


def test_revision_response_binds_requirement_to_exact_description_span() -> None:
    revision = _revision()
    requirement = revision.requirements[0]
    assert (
        revision.job_description[requirement.source_start : requirement.source_end]
        == FIRST
    )

    with pytest.raises(ValidationError, match="exact source span"):
        response_requirement = _requirement(text="Invented requirement")
        response_requirement.pop("evidence_refs")
        _revision(
            requirements=[
                {
                    **response_requirement,
                    "evidence": [],
                }
            ]
        )
    with pytest.raises(ValidationError):
        _revision(extraction_version="unversioned")


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_review_confirmation_requires_json_boolean_true(value: object) -> None:
    with pytest.raises(ValidationError, match="boolean true"):
        ApplicationPackEventCreate.model_validate(
            {
                "event_type": "reviewed",
                "revision_id": "revision1",
                "confirm_requirements_reviewed": value,
            }
        )


def test_pack_response_allows_prior_review_while_current_revision_is_draft() -> None:
    reviewed = _revision()
    current = _revision(
        id="revision2",
        parent_revision_id="revision1",
        revision_number=2,
        source="edited",
    )
    response = ApplicationPackResponse(
        application_id="application1",
        status="draft",
        pack={
            "id": "pack1",
            "version": 4,
            "application_id": "application1",
            "posting_version_id": "postingversion1",
            "base_resume_version_id": "resume1",
            "created_at": NOW,
            "updated_at": NOW,
        },
        current_revision=current,
        reviewed_revision=reviewed,
        review_event={
            "id": "event1",
            "application_pack_id": "pack1",
            "revision_id": "revision1",
            "sequence_number": 1,
            "event_type": "reviewed",
            "occurred_at": NOW,
        },
    )
    assert response.status.value == "draft"

    with pytest.raises(ValidationError, match="extra_forbidden"):
        ApplicationPackResponse.model_validate(
            {**response.model_dump(mode="json"), "generated_resume": "private"}
        )
