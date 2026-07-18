import assert from "node:assert/strict";
import test from "node:test";

import {
  opportunityFitPresentation,
  type OpportunityFitBand,
} from "../lib/opportunity-fit";

test("every automatic fit band has direct decision guidance", () => {
  const bands: readonly OpportunityFitBand[] = [
    "strong",
    "promising",
    "stretch",
    "low",
    "insufficient_data",
  ];

  for (const band of bands) {
    const presentation = opportunityFitPresentation(band);
    assert.ok(presentation.label.length > 0);
    assert.ok(presentation.guidance.length > 30);
    assert.match(presentation.badgeClasses, /bg-/);
    assert.match(presentation.panelClasses, /border-/);
  }
});

test("strong and low bands give unambiguous prioritization advice", () => {
  assert.match(opportunityFitPresentation("strong").guidance, /prioritize/i);
  assert.match(opportunityFitPresentation("low").guidance, /probably skip/i);
});
