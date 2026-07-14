"""Contract tests for deterministic application artifacts."""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from job_hunt_agent.application_artifact_repository import _line_diff
from job_hunt_agent.application_artifact_schemas import (
    ApplicationArtifactDocument,
    ApplicationArtifactEventCreate,
    ApplicationArtifactRevisionCreate,
)


def test_exact_questions_and_evidence_choices_are_strict_and_lossless() -> None:
    payload = ApplicationArtifactRevisionCreate.model_validate(
        {
            "grounding_revision_id": "grounding1",
            "selected_evidence_refs": [{"id": "evidence1", "version": 2}],
            "questions": [
                {
                    "id": "question1",
                    "text": "  Describe your Python impact.  ",
                    "character_limit": 300,
                    "evidence_refs": [{"id": "evidence1", "version": 2}],
                }
            ],
        }
    )
    assert payload.questions[0].text == "  Describe your Python impact.  "
    assert payload.generation_mode == "deterministic"

    with pytest.raises(ValidationError, match="multiple versions"):
        ApplicationArtifactRevisionCreate.model_validate(
            {
                **payload.model_dump(mode="json"),
                "questions": [
                    {
                        "id": "question1",
                        "text": "Question",
                        "evidence_refs": [{"id": "evidence1", "version": 3}],
                    }
                ],
            }
        )
    with pytest.raises(ValidationError):
        ApplicationArtifactRevisionCreate.model_validate(
            {"grounding_revision_id": "grounding1", "generation_mode": "model"}
        )


def test_document_claims_and_exact_line_diff_fail_closed() -> None:
    base = "Summary\nPython systems\n"
    tailored = "Relevant highlights\nSummary\nPython systems\n"
    diff = _line_diff(base, tailored)
    assert "".join(
        line.text for line in diff.lines if line.operation in {"equal", "delete"}
    ) == base
    assert "".join(
        line.text for line in diff.lines if line.operation in {"equal", "insert"}
    ) == tailored

    with pytest.raises(ValidationError, match="reconstruct"):
        diff.model_copy(
            update={"base_content_hash": hashlib.sha256(b"changed").hexdigest()}
        ).__class__.model_validate(
            {
                **diff.model_dump(mode="json"),
                "base_content_hash": hashlib.sha256(b"changed").hexdigest(),
            }
        )

    text = "Grounded evidence"
    with pytest.raises(ValidationError, match="exact document span"):
        ApplicationArtifactDocument.model_validate(
            {
                "text": text,
                "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                "claims": [
                    {
                        "id": "claim1",
                        "start": 0,
                        "end": len(text),
                        "text": "Invented evidence",
                        "sources": [
                            {
                                "kind": "evidence_snapshot",
                                "evidence_id": "evidence1",
                                "evidence_version": 1,
                                "quote": text,
                            }
                        ],
                    }
                ],
            }
        )


def test_approval_requires_json_true_and_rejection_cannot_claim_review() -> None:
    approved = ApplicationArtifactEventCreate.model_validate(
        {
            "event_type": "approved",
            "artifact_revision_id": "artifact1",
            "confirm_artifacts_reviewed": True,
        }
    )
    assert approved.confirm_artifacts_reviewed is True
    with pytest.raises(ValidationError, match="require"):
        ApplicationArtifactEventCreate.model_validate(
            {"event_type": "approved", "artifact_revision_id": "artifact1"}
        )
    with pytest.raises(ValidationError, match="do not accept"):
        ApplicationArtifactEventCreate.model_validate(
            {
                "event_type": "rejected",
                "artifact_revision_id": "artifact1",
                "confirm_artifacts_reviewed": True,
            }
        )
