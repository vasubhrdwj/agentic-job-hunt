import assert from "node:assert/strict";
import test from "node:test";

import {
  sourceEvidenceThresholdLabel,
  sourceQualifiedRationale,
} from "../lib/contact-source-evidence";

test("shows an evidence threshold instead of inventing confidence bands", () => {
  assert.equal(sourceEvidenceThresholdLabel(0.95), "Meets source-evidence threshold");
  assert.equal(sourceEvidenceThresholdLabel(0.84), "Meets source-evidence threshold");
  assert.equal(sourceEvidenceThresholdLabel(0.75), "Meets source-evidence threshold");
  assert.equal(sourceEvidenceThresholdLabel(0.74), "Below source-evidence threshold");
});

test("fails closed for malformed confidence values", () => {
  assert.equal(sourceEvidenceThresholdLabel(Number.NaN), "Unknown source-evidence result");
  assert.equal(sourceEvidenceThresholdLabel(-0.1), "Unknown source-evidence result");
  assert.equal(sourceEvidenceThresholdLabel(1.1), "Unknown source-evidence result");
});

test("does not repeat legacy current-employer claims without source qualification", () => {
  assert.equal(
    sourceQualifiedRationale(
      "Their current Staff Engineer role at Acme places them near hiring.",
      "Team peer",
    ),
    "This team peer lead was selected from public search evidence for this role. Review the source before relying on the title or employer.",
  );
  assert.equal(
    sourceQualifiedRationale(
      "The saved public-search result describes a Staff Engineer role at Acme.",
      "Team peer",
    ),
    "The saved public-search result describes a Staff Engineer role at Acme.",
  );
});
