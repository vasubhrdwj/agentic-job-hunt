import assert from "node:assert/strict";
import test from "node:test";

import type { ApplicationArtifactsResponse } from "../lib/application-artifact-types";
import { applicationDossierFlow } from "../lib/application-dossier-flow";
import type { ApplicationPackResponse } from "../lib/application-pack-types";

function pack(status: ApplicationPackResponse["status"]): ApplicationPackResponse {
  return {
    data_source: "database",
    application_id: "application-1",
    attributed_resume_version_id: "resume-1",
    status,
    pack: status === "not_started" ? null : {} as never,
    current_revision: status === "not_started" ? null : {} as never,
    reviewed_revision: status === "reviewed" ? {} as never : null,
    review_event: status === "reviewed" ? {} as never : null,
    current_approved_evidence: [],
    blockers: [],
  };
}

function artifacts(
  status: ApplicationArtifactsResponse["status"],
): ApplicationArtifactsResponse {
  return {
    data_source: "database",
    application_id: "application-1",
    status,
    pack: {} as never,
    source_catalog: {} as never,
    current_revision: status === "not_started" ? null : {} as never,
    current_event: status === "approved" ? {} as never : null,
    approved_revision: status === "approved" ? {} as never : null,
    approval_event: status === "approved" ? {} as never : null,
    tailored_resume_version: null,
    blockers: [],
  };
}

test("the default path leads to one complete dossier instead of exposing storage states", () => {
  const fit = applicationDossierFlow({
    stage: "pursuing",
    pack: pack("draft"),
    artifacts: null,
  });
  assert.equal(fit.primaryHref, "#application-materials");
  assert.equal(fit.primaryLabel, "Review complete dossier");
  assert.equal(fit.coverage, "prepared");
  assert.match(fit.guidance, /approve the exact package once/i);

  const review = applicationDossierFlow({
    stage: "pursuing",
    pack: pack("reviewed"),
    artifacts: artifacts("draft"),
  });
  assert.equal(review.primaryHref, "#application-materials");
  assert.equal(review.primaryLabel, "Review and approve dossier");
  assert.equal(review.materials, "needs_review");
  assert.match(review.guidance, /approve the package once/i);
});

test("approved materials move the user to five-person outreach without implying auto-send", () => {
  const ready = applicationDossierFlow({
    stage: "pursuing",
    pack: pack("reviewed"),
    artifacts: artifacts("approved"),
  });
  assert.equal(ready.primaryHref, "#application-people");
  assert.equal(ready.primaryLabel, "Prepare people and messages");
  assert.match(ready.guidance, /submit or send everything yourself/i);
});

test("later stages keep preparation read-only", () => {
  const result = applicationDossierFlow({
    stage: "applied",
    pack: pack("reviewed"),
    artifacts: artifacts("approved"),
  });
  assert.equal(result.primaryHref, "#hiring-progress-title");
  assert.match(result.guidance, /frozen/i);
});
