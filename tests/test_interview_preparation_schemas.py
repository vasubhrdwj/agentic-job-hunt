"""Contract tests for interview-preparation prompt-capacity safety."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from job_hunt_agent.interview_preparation_schemas import (
    ApplicationInterviewPreparationResponse,
    InterviewPreparationPrompt,
    InterviewPreparationPromptCategory,
    build_grounded_starting_draft,
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


def test_grounded_starting_draft_uses_exact_result_and_marks_unknown_star_facts() -> None:
    prompt_value = _projection().prompts[0].model_dump(mode="json")
    evidence = _projection().prompts[0].evidence
    starting_draft = build_grounded_starting_draft(
        category=InterviewPreparationPromptCategory.key_requirement,
        requirement_id="requirement1",
        evidence=evidence,
    )

    assert starting_draft.draft.situation == ""
    assert starting_draft.draft.task == ""
    assert starting_draft.draft.action == ""
    assert starting_draft.draft.result == "Reduced failures by 40%."
    assert [item.value for item in starting_draft.missing_facts] == [
        "situation_context",
        "personal_responsibility",
        "specific_actions",
    ]
    assert starting_draft.result_evidence is not None
    assert starting_draft.result_evidence.id == "evidence1"

    prompt_value["starting_draft"] = starting_draft.model_dump(mode="json")
    prompt = InterviewPreparationPrompt.model_validate(prompt_value)
    assert prompt.starting_draft == starting_draft


def test_grounded_starting_draft_rejects_unpinned_or_rewritten_result() -> None:
    prompt_value = _projection().prompts[0].model_dump(mode="json")
    starting_draft = build_grounded_starting_draft(
        category=InterviewPreparationPromptCategory.key_requirement,
        requirement_id="requirement1",
        evidence=_projection().prompts[0].evidence,
    ).model_dump(mode="json")
    starting_draft["draft"]["result"] = "Invented 10x outcome."
    prompt_value["starting_draft"] = starting_draft

    with pytest.raises(ValidationError, match="exact pinned requirement and evidence"):
        InterviewPreparationPrompt.model_validate(prompt_value)


def test_grounded_starting_draft_leaves_result_blank_without_explicit_outcome() -> None:
    prompt = _projection().prompts[0]
    evidence = prompt.evidence[0].model_copy(
        update={"statement": "Owned the Python service and its on-call rotation."}
    )
    starting_draft = build_grounded_starting_draft(
        category=InterviewPreparationPromptCategory.key_requirement,
        requirement_id="requirement1",
        evidence=[evidence],
    )

    assert starting_draft.draft.result == ""
    assert starting_draft.result_evidence is None
    assert "verified_result" in {item.value for item in starting_draft.missing_facts}


def test_result_detection_accepts_metric_change_but_not_architecture_arrows() -> None:
    prompt = _projection().prompts[0]
    metric = prompt.evidence[0].model_copy(
        update={"statement": "Hard-task performance moved from 0.35 → 0.90."}
    )
    architecture = prompt.evidence[0].model_copy(
        update={"statement": "Debugged the CloudFlare → ALB → nginx → ECS path."}
    )

    metric_draft = build_grounded_starting_draft(
        category=InterviewPreparationPromptCategory.key_requirement,
        requirement_id="requirement1",
        evidence=[metric],
    )
    architecture_draft = build_grounded_starting_draft(
        category=InterviewPreparationPromptCategory.key_requirement,
        requirement_id="requirement1",
        evidence=[architecture],
    )

    assert metric_draft.draft.result == metric.statement
    assert architecture_draft.draft.result == ""
