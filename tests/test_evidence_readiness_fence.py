"""Lock-contract coverage for evidence-dependent application readiness."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.dialects import postgresql

import job_hunt_agent.application_submission_repository as submission_repository
import job_hunt_agent.evidence_repository as evidence_repository
from job_hunt_agent.application_submission_schemas import ReadyToApplyTransitionCreate
from job_hunt_agent.profile_schemas import AchievementEvidencePatch
from job_hunt_agent.security import load_data_keyring


def _ready_payload() -> ReadyToApplyTransitionCreate:
    return ReadyToApplyTransitionCreate(
        to_stage="ready_to_apply",
        application_pack_id="pack1",
        application_pack_revision_id="grounding1",
        application_pack_review_event_id="groundingreview1",
        application_artifact_revision_id="artifact1",
        application_artifact_approval_event_id="artifactapproval1",
        tailored_resume_version_id="resume2",
        next_action_due_on=date(2026, 7, 21),
        confirm_ready=True,
    )


def test_owner_evidence_fence_is_a_postgres_row_lock() -> None:
    statements = []

    class CapturingSession:
        def scalar(self, statement):
            statements.append(statement)
            return object()

    evidence_repository._lock_owner_evidence_fence(  # noqa: SLF001
        CapturingSession(),  # type: ignore[arg-type]
        "owner-a",
    )

    assert len(statements) == 1
    sql = str(statements[0].compile(dialect=postgresql.dialect()))
    assert "owners.id" in sql
    assert sql.rstrip().endswith("FOR UPDATE")


def test_evidence_mutation_acquires_owner_fence_before_evidence_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class MissingEvidenceSession:
        def scalar(self, _statement):
            calls.append("evidence")
            return None

    def lock_owner(_session, owner_id: str):
        assert owner_id == "owner-a"
        calls.append("owner")
        return object()

    monkeypatch.setattr(
        evidence_repository,
        "_lock_owner_evidence_fence",
        lock_owner,
    )
    updated = evidence_repository.update_achievement_evidence(
        MissingEvidenceSession(),  # type: ignore[arg-type]
        owner_id="owner-a",
        evidence_id="evidence-a",
        patch=AchievementEvidencePatch(approval_state="retired"),
        expected_version=1,
        keyring=load_data_keyring(production=False),
    )

    assert updated is None
    assert calls == ["owner", "evidence"]


def test_ready_transition_acquires_owner_fence_before_application_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def lock_owner(_session, owner_id: str):
        assert owner_id == "owner-a"
        calls.append("owner")
        return object()

    def missing_application(
        _session,
        owner_id: str,
        application_id: str,
        *,
        lock: bool,
    ):
        assert (owner_id, application_id, lock) == (
            "owner-a",
            "application-a",
            True,
        )
        calls.append("application")
        return None

    monkeypatch.setattr(
        submission_repository,
        "_lock_owner_evidence_fence",
        lock_owner,
    )
    monkeypatch.setattr(
        submission_repository,
        "_owned_application",
        missing_application,
    )
    transitioned = submission_repository.transition_application(
        object(),  # type: ignore[arg-type]
        owner_id="owner-a",
        application_id="application-a",
        payload=_ready_payload(),
        expected_application_version=1,
        idempotency_key="ready-fenced",
        keyring=load_data_keyring(production=False),
    )

    assert transitioned is None
    assert calls == ["owner", "application"]
