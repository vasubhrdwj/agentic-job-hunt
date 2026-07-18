import assert from "node:assert/strict";
import test from "node:test";

import type {
  ApplicationArtifactClaim,
  ApplicationArtifactClaimSource,
} from "../lib/application-artifact-types";
import {
  buildGroundedFitStory,
  type GroundedFitStoryInput,
} from "../lib/grounded-fit-story";

function groundedInput(): GroundedFitStoryInput {
  let text = "Application note for ";
  const claims: ApplicationArtifactClaim[] = [];
  function appendClaim(value: string, sources: ApplicationArtifactClaimSource[]) {
    const start = text.length;
    text += value;
    claims.push({
      id: `claim-${claims.length + 1}`,
      start,
      end: text.length,
      text: value,
      derivation: "verbatim",
      sources,
    });
  }

  appendClaim("Backend Engineer", [{
    kind: "posting_field",
    posting_version_id: "posting-v1",
    field: "title",
    value: "Backend Engineer",
  }]);
  text += " at ";
  appendClaim("StableCo", [{
    kind: "posting_field",
    posting_version_id: "posting-v1",
    field: "company_name",
    value: "StableCo",
  }]);
  text += ".\nThe role emphasizes: ";
  appendClaim("Build reliable event-driven services.", [{
    kind: "job_description_span",
    grounding_revision_id: "grounding-v1",
    source_start: 20,
    source_end: 57,
    quote: "Build reliable event-driven services.",
  }]);
  text += "\nRelevant evidence:\n- ";
  appendClaim("Owned an AWS Lambda event pipeline in production.", [{
    kind: "evidence_snapshot",
    evidence_id: "evidence-1",
    evidence_version: 2,
    quote: "Owned an AWS Lambda event pipeline in production.",
  }]);
  text += "\n- ";
  appendClaim("Shipped Kafka retry and DLQ handling.", [{
    kind: "evidence_snapshot",
    evidence_id: "evidence-2",
    evidence_version: 1,
    quote: "Shipped Kafka retry and DLQ handling.",
  }]);

  return {
    companyNote: { text, claims },
    selectedEvidence: [
      {
        id: "evidence-1",
        version: 2,
        statement: "Owned an AWS Lambda event pipeline in production.",
      },
      {
        id: "evidence-2",
        version: 1,
        statement: "Shipped Kafka retry and DLQ handling.",
      },
      {
        id: "not-in-note",
        version: 1,
        statement: "This unlinked claim must never appear.",
      },
    ],
    unsupportedRequirements: [
      {
        id: "gap-preferred",
        ordinal: 1,
        importance: "preferred",
        text: "Prior stablecoin experience.",
        coverage: "unsupported",
      },
      {
        id: "gap-required",
        ordinal: 2,
        importance: "required",
        text: "Five years of engineering experience.",
        coverage: "unsupported",
      },
      {
        id: "not-a-gap",
        ordinal: 3,
        importance: "required",
        text: "Python experience.",
        coverage: "partial",
      },
    ],
  };
}

test("creates a concise role-specific story only from exact pinned sources", () => {
  const story = buildGroundedFitStory(groundedInput());

  assert.ok(story);
  assert.equal(story.companyName, "StableCo");
  assert.equal(story.roleTitle, "Backend Engineer");
  assert.equal(
    story.highlightedRequirement,
    "Build reliable event-driven services.",
  );
  assert.equal(story.evidence.length, 2);
  assert.match(story.message, /Backend Engineer role at StableCo/);
  assert.match(story.message, /Build reliable event-driven services/);
  assert.match(story.message, /Owned an AWS Lambda event pipeline/);
  assert.match(story.message, /Shipped Kafka retry and DLQ handling/);
  assert.doesNotMatch(story.message, /unlinked claim/);
  assert.deepEqual(story.unclaimedGaps, [
    {
      id: "gap-required",
      importance: "required",
      text: "Five years of engineering experience.",
    },
    {
      id: "gap-preferred",
      importance: "preferred",
      text: "Prior stablecoin experience.",
    },
  ]);
});

test("fails closed when posting or approved-evidence provenance is not exact", () => {
  const wrongPosting = groundedInput();
  const titleClaim = wrongPosting.companyNote.claims[0];
  assert.ok(titleClaim);
  titleClaim.start += 1;
  assert.equal(buildGroundedFitStory(wrongPosting), null);

  const unlinkedEvidence = groundedInput();
  unlinkedEvidence.selectedEvidence = [{
    id: "different-evidence",
    version: 1,
    statement: "A claim absent from the immutable source note.",
  }];
  assert.equal(buildGroundedFitStory(unlinkedEvidence), null);
});

test("caps evidence without mutating source data", () => {
  const input = groundedInput();
  for (const [id, statement] of [
    ["evidence-3", "Improved a production service's reliability."],
    ["evidence-4", "Debugged a multi-layer cloud request path."],
  ] as const) {
    const start = input.companyNote.text.length;
    input.companyNote.text += `\n- ${statement}`;
    input.companyNote.claims.push({
      id: `claim-${id}`,
      start: start + 3,
      end: input.companyNote.text.length,
      text: statement,
      derivation: "verbatim",
      sources: [{
        kind: "evidence_snapshot",
        evidence_id: id,
        evidence_version: 1,
        quote: statement,
      }],
    });
    input.selectedEvidence.push({ id, version: 1, statement });
  }
  const first = input.selectedEvidence[0];
  assert.ok(first);
  input.selectedEvidence.splice(1, 0, first);
  const original = structuredClone(input);

  const story = buildGroundedFitStory(input);

  assert.ok(story);
  assert.deepEqual(story.evidence.map((item) => item.id), [
    "evidence-1",
    "evidence-2",
    "evidence-3",
  ]);
  assert.doesNotMatch(story.message, /multi-layer cloud/);
  assert.deepEqual(original, input, "story preparation must not mutate transport data");
});
