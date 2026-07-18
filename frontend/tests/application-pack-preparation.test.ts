import assert from "node:assert/strict";
import test from "node:test";

import {
  containsExactSkillTag,
  prepareRequirementProposals,
  type RequirementPreparationRequest,
} from "../lib/application-pack-preparation";

function request(
  overrides: Partial<RequirementPreparationRequest> = {},
): RequirementPreparationRequest {
  return {
    packStatus: "draft",
    revisionSource: "extracted",
    hasReviewEvent: false,
    requirements: [
      {
        id: "requirement-1",
        ordinal: 1,
        text: "Build event-driven services with AWS Lambda and Kafka.",
        coverage: "needs_review",
      },
      {
        id: "requirement-2",
        ordinal: 2,
        text: "Five years of professional leadership experience.",
        coverage: "needs_review",
      },
    ],
    evidence: [
      {
        id: "evidence-b",
        approvalState: "approved",
        skills: ["Kafka", "AWS Lambda"],
      },
    ],
    ...overrides,
  };
}

test("exact approved skill tags prepare Partial evidence mappings and honest gaps", () => {
  const result = prepareRequirementProposals(request());

  assert.deepEqual(result, {
    proposals: [
      {
        requirementId: "requirement-1",
        ordinal: 1,
        coverage: "partial",
        evidenceIds: ["evidence-b"],
        matchedSkillTags: ["aws lambda", "kafka"],
      },
      {
        requirementId: "requirement-2",
        ordinal: 2,
        coverage: "unsupported",
        evidenceIds: [],
        matchedSkillTags: [],
      },
    ],
    partialCount: 1,
    unsupportedCount: 1,
    matchedEvidenceCount: 1,
  });
  assert.equal(result.proposals.some((proposal) => String(proposal.coverage) === "supported"), false);
});

test("matching is case-insensitive but requires a whole exact tag phrase", () => {
  assert.equal(containsExactSkillTag("Production KAFKA pipelines", "Kafka"), true);
  assert.equal(containsExactSkillTag("Built with AWS\n  Lambda", "AWS Lambda"), true);
  assert.equal(containsExactSkillTag("Good communication", "Go"), false);
  assert.equal(containsExactSkillTag("Built AWSome systems", "AWS"), false);
  assert.equal(containsExactSkillTag("Used SQLAlchemy", "SQL"), false);
  assert.equal(containsExactSkillTag("Modern C++ services", "C++"), true);
  assert.equal(containsExactSkillTag("Modern C++17 services", "C++"), false);
  assert.equal(containsExactSkillTag("Any backend stack", "   "), false);
});

test("only approved evidence skill tags participate", () => {
  const result = prepareRequirementProposals(request({
    requirements: [{
      id: "requirement-1",
      ordinal: 1,
      text: "Python and Kubernetes experience.",
      coverage: "needs_review",
    }],
    evidence: [
      { id: "pending", approvalState: "pending", skills: ["Python"] },
      { id: "rejected", approvalState: "rejected", skills: ["Python"] },
      { id: "retired", approvalState: "retired", skills: ["Python"] },
      { id: "approved", approvalState: "approved", skills: ["Kubernetes"] },
    ],
  }));

  assert.deepEqual(result.proposals[0], {
    requirementId: "requirement-1",
    ordinal: 1,
    coverage: "partial",
    evidenceIds: ["approved"],
    matchedSkillTags: ["kubernetes"],
  });
});

test("proposals are limited to unreviewed extracted Needs review requirements", () => {
  for (const overrides of [
    { packStatus: "reviewed" as const },
    { packStatus: "not_started" as const },
    { revisionSource: "edited" as const },
    { revisionSource: null },
    { hasReviewEvent: true },
  ]) {
    assert.deepEqual(prepareRequirementProposals(request(overrides)).proposals, []);
  }

  const alreadyDecided = prepareRequirementProposals(request({
    requirements: [{
      id: "requirement-1",
      ordinal: 1,
      text: "Kafka experience.",
      coverage: "partial",
    }],
  }));
  assert.deepEqual(alreadyDecided.proposals, []);
});

test("proposal ordering and references are stable and deduplicated", () => {
  const input = request({
    requirements: [
      {
        id: "requirement-2",
        ordinal: 2,
        text: "Kafka experience.",
        coverage: "needs_review",
      },
      {
        id: "requirement-1",
        ordinal: 1,
        text: "AWS experience.",
        coverage: "needs_review",
      },
      {
        id: "requirement-1",
        ordinal: 1,
        text: "This duplicate must not create another proposal.",
        coverage: "needs_review",
      },
    ],
    evidence: [
      { id: "evidence-z", approvalState: "approved", skills: ["Kafka", "kafka"] },
      { id: "evidence-a", approvalState: "approved", skills: ["AWS"] },
      { id: "evidence-z", approvalState: "approved", skills: ["AWS", "Kafka"] },
    ],
  });
  const snapshot = structuredClone(input);

  const result = prepareRequirementProposals(input);

  assert.deepEqual(result.proposals.map((proposal) => proposal.requirementId), [
    "requirement-1",
    "requirement-2",
  ]);
  assert.deepEqual(result.proposals[0]?.evidenceIds, ["evidence-a", "evidence-z"]);
  assert.deepEqual(result.proposals[1]?.evidenceIds, ["evidence-z"]);
  assert.deepEqual(result.proposals[1]?.matchedSkillTags, ["kafka"]);
  assert.deepEqual(input, snapshot, "preparation must not mutate transport data");
});
