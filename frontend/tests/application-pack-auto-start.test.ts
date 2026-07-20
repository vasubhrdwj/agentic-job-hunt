import assert from "node:assert/strict";
import test from "node:test";

import {
  APPLICATION_PACK_AUTO_START_VERSION,
  buildAutomaticApplicationPackStartPlan,
} from "../lib/application-pack-auto-start";
import type { ApplicationPackResponse } from "../lib/application-pack-types";
import type { ApplicationStage } from "../lib/application-types";

function projection(): ApplicationPackResponse {
  return {
    data_source: "database",
    application_id: "application-1",
    attributed_resume_version_id: null,
    status: "not_started",
    pack: null,
    current_revision: null,
    reviewed_revision: null,
    review_event: null,
    current_approved_evidence: [],
    blockers: [],
  };
}

function plan(overrides: {
  applicationId?: string;
  applicationVersion?: number;
  applicationStage?: ApplicationStage;
  initialLoadComplete?: boolean;
  projection?: ApplicationPackResponse | null;
  selectedResume?: { id: string; is_base: boolean } | null;
  startingResumeChoiceCount?: number;
} = {}) {
  return buildAutomaticApplicationPackStartPlan({
    applicationId: overrides.applicationId ?? "application-1",
    applicationVersion: overrides.applicationVersion ?? 4,
    applicationStage: overrides.applicationStage ?? "pursuing",
    initialLoadComplete: overrides.initialLoadComplete ?? true,
    projection: overrides.projection === undefined ? projection() : overrides.projection,
    selectedResume: overrides.selectedResume === undefined
      ? { id: "resume-base", is_base: true }
      : overrides.selectedResume,
    startingResumeChoiceCount: overrides.startingResumeChoiceCount ?? 1,
  });
}

test("persisted role inputs and the selected base resume produce one stable start plan", () => {
  const first = plan();
  const second = plan();

  assert.ok(first);
  assert.deepEqual(second, first);
  assert.equal(
    first.idempotencyKey,
    `application-pack:auto:application-1:4:resume-base:${APPLICATION_PACK_AUTO_START_VERSION}`,
  );
  assert.equal(first.expectedApplicationVersion, 4);
  assert.deepEqual(first.payload, {
    base_resume_version_id: "resume-base",
    require_sole_current_base_resume: true,
    owner_job_description: null,
  });
});

test("automatic start fails closed when an input needs owner judgment", () => {
  const ownerDescriptionRequired = projection();
  ownerDescriptionRequired.blockers = ["owner_job_description_required"];
  const missingBase = projection();
  missingBase.blockers = ["base_resume_missing"];
  const closedPosting = projection();
  closedPosting.blockers = ["posting_closed"];
  const foreignProjection = projection();
  foreignProjection.application_id = "application-2";
  const alreadyStarted = projection();
  alreadyStarted.status = "draft";

  for (const blocked of [
    plan({ initialLoadComplete: false }),
    plan({ applicationStage: "ready_to_apply" }),
    plan({ applicationVersion: 0 }),
    plan({ projection: null }),
    plan({ projection: foreignProjection }),
    plan({ projection: alreadyStarted }),
    plan({ projection: ownerDescriptionRequired }),
    plan({ projection: missingBase }),
    plan({ projection: closedPosting }),
    plan({ selectedResume: null }),
    plan({ selectedResume: { id: "resume-alternate", is_base: false } }),
    plan({ selectedResume: { id: "   ", is_base: true } }),
    plan({ startingResumeChoiceCount: 2 }),
  ]) assert.equal(blocked, null);
});

test("a meaningful alternate resume keeps the owner choice visible", () => {
  assert.equal(plan({ startingResumeChoiceCount: 2 }), null);
});

test("missing approved evidence does not block deterministic extraction", () => {
  const withoutEvidence = projection();
  withoutEvidence.blockers = ["approved_evidence_missing"];

  assert.ok(plan({ projection: withoutEvidence }));
});
