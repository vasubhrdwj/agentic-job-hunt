"""Contract tests for interview-preparation prompt-capacity safety."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from job_hunt_agent.interview_preparation_schemas import (
    ApplicationInterviewPreparationResponse,
)
from tests.test_interview_preparation_router import _projection


def _over_capacity_value() -> dict[str, object]:
    value = _projection().model_dump(mode="json")
    requirement = value["requirements"][0]
    value.update(
        {
            "status": "blocked",
            "requirements": [
                {
                    **requirement,
                    "id": f"required{index:02d}",
                    "ordinal": index,
                    "text": f"Required capability {index}",
                }
                for index in range(1, 14)
            ],
            "required_evidence_backed_count": 13,
            "prompt_capacity": 12,
            "blockers": ["required_prompt_capacity_exceeded"],
            "next_steps": [
                "13 required evidence-backed requirements exceed the 12-prompt safety limit."
            ],
        }
    )
    return value


def test_capacity_blocker_matches_exact_required_evidence_backed_count() -> None:
    response = ApplicationInterviewPreparationResponse.model_validate(
        _over_capacity_value()
    )
    assert response.required_evidence_backed_count == 13
    assert response.prompt_capacity == 12
    assert response.status.value == "blocked"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("blockers", ["application_closed"], "capacity blocker"),
        ("required_evidence_backed_count", 12, "must match"),
    ],
)
def test_capacity_contract_rejects_missing_blocker_or_false_count(
    field: str,
    value: object,
    message: str,
) -> None:
    payload = _over_capacity_value()
    payload[field] = value
    with pytest.raises(ValidationError, match=message):
        ApplicationInterviewPreparationResponse.model_validate(payload)
