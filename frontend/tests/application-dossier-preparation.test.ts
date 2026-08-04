import assert from "node:assert/strict";
import test from "node:test";

import {
  buildDossierPreparedInputs,
  type DossierQuestionDraft,
  type DossierRequirementDraft,
} from "../lib/application-dossier-preparation";
import type { ApplicationPackResponse } from "../lib/application-pack-types";

const NOW = "2026-08-05T08:00:00Z";

function projection(): ApplicationPackResponse {
  const evidence = Array.from({ length: 6 }, (_, index) => ({
    id: `evidence-${index + 1}`,
    statement: `Approved backend achievement ${index + 1}`,
    skills: ["Python"],
    origin: "owner_entered" as const,
    approval_state: "approved" as const,
    source_resume_version_id: "resume-1",
    source_excerpt: null,
    approved_at: NOW,
    rejected_at: null,
    retired_at: null,
    version: index + 1,
    created_at: NOW,
    updated_at: NOW,
  }));
  return {
    data_source: "database",
    application_id: "application-1",
    attributed_resume_version_id: "resume-1",
    status: "draft",
    pack: {} as never,
    current_revision: {
      id: "grounding-1",
      application_pack_id: "pack-1",
      parent_revision_id: null,
      revision_number: 1,
      source: "extracted",
      extraction_version: "requirements-v1",
      job_description_source: "persisted_description",
      job_description: "Python\nKubernetes",
      requirements: [
        {
          id: "requirement-1",
          ordinal: 1,
          importance: "required",
          text: "Python",
          source_start: 0,
          source_end: 6,
          coverage: "needs_review",
          evidence: [],
        },
        {
          id: "requirement-2",
          ordinal: 2,
          importance: "preferred",
          text: "Kubernetes",
          source_start: 7,
          source_end: 17,
          coverage: "needs_review",
          evidence: [],
        },
      ],
      created_at: NOW,
    },
    reviewed_revision: null,
    review_event: null,
    current_approved_evidence: evidence,
    blockers: ["requirements_need_review"],
  };
}

function drafts(): Record<string, DossierRequirementDraft> {
  return {
    "requirement-1": {
      included: true,
      importance: "required",
      coverage: "partial",
      evidenceIds: [
        "evidence-1",
        "evidence-2",
        "evidence-3",
        "evidence-4",
        "evidence-5",
        "evidence-6",
      ],
    },
    "requirement-2": {
      included: true,
      importance: "preferred",
      coverage: "unsupported",
      evidenceIds: [],
    },
  };
}

test("one dossier request binds reviewed coverage, five exact evidence refs, and questions", () => {
  const questions: DossierQuestionDraft[] = [{
    id: "question-1",
    text: "Describe your Python impact.",
    characterLimit: "500",
    evidenceIds: ["evidence-1"],
  }];
  const result = buildDossierPreparedInputs(projection(), drafts(), questions);

  assert.ok(result);
  assert.equal(result.grounding_parent_revision_id, "grounding-1");
  assert.deepEqual(result.selected_evidence_refs, [
    { id: "evidence-1", version: 1 },
    { id: "evidence-2", version: 2 },
    { id: "evidence-3", version: 3 },
    { id: "evidence-4", version: 4 },
    { id: "evidence-5", version: 5 },
  ]);
  assert.deepEqual(result.questions, [{
    id: "question-1",
    text: "Describe your Python impact.",
    character_limit: 500,
    evidence_refs: [{ id: "evidence-1", version: 1 }],
  }]);
  assert.equal(result.requirements[1]?.coverage, "unsupported");
});

test("the preview plan fails closed for unreviewed coverage or invalid question inputs", () => {
  const unreviewed = drafts();
  unreviewed["requirement-1"]!.coverage = "needs_review";
  assert.equal(buildDossierPreparedInputs(projection(), unreviewed, []), null);

  const invalidLimit: DossierQuestionDraft[] = [{
    id: "question-1",
    text: "Describe your Python impact.",
    characterLimit: "0",
    evidenceIds: [],
  }];
  assert.equal(buildDossierPreparedInputs(projection(), drafts(), invalidLimit), null);

  const unmappedEvidence: DossierQuestionDraft[] = [{
    id: "question-1",
    text: "Describe your Python impact.",
    characterLimit: "",
    evidenceIds: ["not-mapped"],
  }];
  assert.equal(buildDossierPreparedInputs(projection(), drafts(), unmappedEvidence), null);
});
