import assert from "node:assert/strict";
import test from "node:test";

import {
  APPLICATION_MATERIALS_GENERATOR_VERSION,
  buildInitialMaterialsGenerationPlan,
} from "../lib/application-materials-auto-generation";
import type { ApplicationArtifactsResponse } from "../lib/application-artifact-types";
import type { ApplicationStage } from "../lib/application-types";

const NOW = "2026-07-19T08:00:00Z";

function projection(): ApplicationArtifactsResponse {
  return {
    data_source: "database",
    application_id: "application-1",
    status: "not_started",
    pack: {
      id: "pack-1",
      version: 7,
      application_id: "application-1",
      posting_version_id: "posting-version-1",
      base_resume_version_id: "resume-1",
      created_at: NOW,
      updated_at: NOW,
    },
    current_revision: null,
    current_event: null,
    approved_revision: null,
    approval_event: null,
    tailored_resume_version: null,
    source_catalog: {
      reviewed_grounding_revision_id: "grounding-2",
      reviewed_grounding_revision_number: 2,
      reviewed_grounding_event_id: "grounding-event-1",
      evidence: Array.from({ length: 6 }, (_, index) => ({
        id: `evidence-${index + 1}`,
        version: index + 1,
        statement: `Approved achievement ${index + 1}`,
        source_resume_version_id: "resume-1",
        source_excerpt: null,
        skills: ["Python"],
        approved_at: NOW,
      })),
      unsupported_requirements: [],
    },
    blockers: [],
  };
}

function plan(overrides: {
  applicationStage?: ApplicationStage;
  projection?: ApplicationArtifactsResponse | null;
  selectedEvidenceIds?: string[];
  questionCount?: number;
  questionsValid?: boolean;
  inputsDirty?: boolean;
} = {}) {
  return buildInitialMaterialsGenerationPlan({
    applicationId: "application-1",
    applicationStage: overrides.applicationStage ?? "pursuing",
    projection: overrides.projection === undefined ? projection() : overrides.projection,
    selectedEvidenceIds: overrides.selectedEvidenceIds ?? [
      "evidence-1",
      "evidence-2",
      "evidence-3",
      "evidence-4",
      "evidence-5",
    ],
    questionCount: overrides.questionCount ?? 0,
    questionsValid: overrides.questionsValid ?? true,
    inputsDirty: overrides.inputsDirty ?? false,
  });
}

test("initial materials use the stable top five evidence set and a deterministic receipt", () => {
  const first = plan();
  const second = plan();

  assert.ok(first);
  assert.deepEqual(second, first);
  assert.equal(
    first.idempotencyKey,
    `application-artifacts:auto:application-1:grounding-2:${APPLICATION_MATERIALS_GENERATOR_VERSION}`,
  );
  assert.equal(first.packId, "pack-1");
  assert.equal(first.expectedPackVersion, 7);
  assert.deepEqual(first.payload, {
    operation: "generate",
    grounding_revision_id: "grounding-2",
    parent_artifact_revision_id: null,
    generation_mode: "deterministic",
    selected_evidence_refs: [
      { id: "evidence-1", version: 1 },
      { id: "evidence-2", version: 2 },
      { id: "evidence-3", version: 3 },
      { id: "evidence-4", version: 4 },
      { id: "evidence-5", version: 5 },
    ],
    questions: [],
  });
});

test("automatic generation fails closed for stage, saved-state, evidence, or input blockers", () => {
  const withRevision = projection();
  withRevision.current_revision = {} as never;
  const withPostingBlocker = projection();
  withPostingBlocker.blockers = ["posting_closed"];
  const withoutEvidence = projection();
  withoutEvidence.source_catalog!.evidence = [];
  const foreignProjection = projection();
  foreignProjection.application_id = "application-2";

  for (const blocked of [
    plan({ applicationStage: "ready_to_apply" }),
    plan({ projection: null }),
    plan({ projection: foreignProjection }),
    plan({ projection: withRevision }),
    plan({ projection: withPostingBlocker }),
    plan({ projection: withoutEvidence, selectedEvidenceIds: [] }),
    plan({ selectedEvidenceIds: ["evidence-2", "evidence-1"] }),
    plan({ questionCount: 1 }),
    plan({ questionsValid: false }),
    plan({ inputsDirty: true }),
  ]) assert.equal(blocked, null);
});
