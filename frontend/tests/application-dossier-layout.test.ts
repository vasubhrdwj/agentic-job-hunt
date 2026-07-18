import assert from "node:assert/strict";
import test from "node:test";

import {
  applicationDossierLayout,
  interviewHistoryIsKnownEmpty,
  type ApplicationDossierSection,
} from "../lib/application-dossier-layout";
import type { ApplicationStage } from "../lib/application-types";

const stages: readonly ApplicationStage[] = [
  "pursuing",
  "ready_to_apply",
  "applied",
  "screening",
  "interviewing",
  "offer",
  "closed",
];

const interviewSections: readonly ApplicationDossierSection[] = [
  "interview_rounds",
  "interview_preparation",
];

test("a pursuing dossier leads with application preparation and outreach", () => {
  assert.deepEqual(applicationDossierLayout("pursuing").primary, [
    "application_pack",
    "application_materials",
    "application_submission",
    "people",
  ]);
});

test("pre-submission dossiers do not expose interview-only sections", () => {
  for (const stage of ["pursuing", "ready_to_apply"] as const) {
    const layout = applicationDossierLayout(stage);
    const allSections = [...layout.primary, ...layout.secondary];
    for (const section of interviewSections) {
      assert.equal(allSections.includes(section), false, `${stage} includes ${section}`);
    }
    assert.equal(interviewHistoryIsKnownEmpty(stage), true);
  }
});

test("interview workflows become available once an application is submitted", () => {
  for (const stage of ["applied", "screening", "interviewing", "offer", "closed"] as const) {
    const layout = applicationDossierLayout(stage);
    const allSections = [...layout.primary, ...layout.secondary];
    for (const section of interviewSections) {
      assert.equal(allSections.includes(section), true, `${stage} omits ${section}`);
    }
    assert.equal(interviewHistoryIsKnownEmpty(stage), false);
  }
});

test("each stage assigns every visible workflow to only one priority tier", () => {
  for (const stage of stages) {
    const layout = applicationDossierLayout(stage);
    const allSections = [...layout.primary, ...layout.secondary];
    assert.equal(new Set(allSections).size, allSections.length, `${stage} duplicates a section`);
  }
});

test("closed applications keep workflows in history instead of the main path", () => {
  assert.deepEqual(applicationDossierLayout("closed").primary, []);
});
