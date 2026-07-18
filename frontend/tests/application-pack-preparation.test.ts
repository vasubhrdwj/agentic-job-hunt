import assert from "node:assert/strict";
import test from "node:test";

import {
  buildPreparedAssessmentReviewRows,
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
  assert.equal(
    containsExactSkillTag("Strong data structures, algorithms skills", "Data Structures and Algorithms"),
    true,
  );
  assert.equal(containsExactSkillTag("Deploy services on AWS", "AWS Lambda"), true);
  assert.equal(containsExactSkillTag("Deploy services on AWS", "AWS consulting"), false);
  assert.equal(containsExactSkillTag("Good communication", "Go"), false);
  assert.equal(containsExactSkillTag("Built AWSome systems", "AWS"), false);
  assert.equal(containsExactSkillTag("Built AWSome systems", "AWS Lambda"), false);
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

test("approval preview shows each exact requirement outcome and concise linked proof", () => {
  const plan = prepareRequirementProposals(request());
  const longStatement = `Owned the Kafka pipeline ${"with durable delivery ".repeat(8)}`;

  const rows = buildPreparedAssessmentReviewRows(
    plan.proposals,
    request().requirements,
    [{ id: "evidence-b", statement: longStatement }],
  );

  assert.deepEqual(rows.map((row) => ({
    ordinal: row.ordinal,
    requirementExcerpt: row.requirementExcerpt,
    coverage: row.coverage,
    linkedEvidenceCount: row.linkedEvidenceCount,
  })), [
    {
      ordinal: 1,
      requirementExcerpt: "Build event-driven services with AWS Lambda and Kafka.",
      coverage: "partial",
      linkedEvidenceCount: 1,
    },
    {
      ordinal: 2,
      requirementExcerpt: "Five years of professional leadership experience.",
      coverage: "unsupported",
      linkedEvidenceCount: 0,
    },
  ]);
  assert.equal(rows[0]?.linkedEvidenceStatements.length, 1);
  assert.equal(rows[0]?.linkedEvidenceStatements[0]?.endsWith("…"), true);
  assert.deepEqual(rows[1]?.linkedEvidenceStatements, []);

  const adjustedRows = buildPreparedAssessmentReviewRows(
    [{
      requirementId: "requirement-2",
      ordinal: 2,
      coverage: "supported",
      evidenceIds: ["manual-proof"],
    }],
    request().requirements,
    [{ id: "manual-proof", statement: "Led an incident-response project." }],
  );
  assert.deepEqual(adjustedRows, [{
    requirementId: "requirement-2",
    ordinal: 2,
    requirementExcerpt: "Five years of professional leadership experience.",
    coverage: "supported",
    linkedEvidenceCount: 1,
    linkedEvidenceStatements: ["Led an incident-response project."],
  }]);
});

test("Stable Money-like requirements link exact concepts and AWS evidence without guessing", () => {
  const result = prepareRequirementProposals(request({
    requirements: [
      {
        id: "algorithms",
        ordinal: 1,
        text: "Excellent knowledge of data structures, algorithms, and problem solving.",
        coverage: "needs_review",
      },
      {
        id: "cloud",
        ordinal: 2,
        text: "Hands-on experience designing and operating services on AWS.",
        coverage: "needs_review",
      },
      {
        id: "critical-software",
        ordinal: 3,
        text: "Experience building critical software used by customers.",
        coverage: "needs_review",
      },
      {
        id: "product-company",
        ordinal: 4,
        text: "Prior experience working at a product company.",
        coverage: "needs_review",
      },
    ],
    evidence: [
      {
        id: "competitive-programming",
        approvalState: "approved",
        skills: ["Data Structures and Algorithms"],
      },
      {
        id: "production-aws",
        approvalState: "approved",
        skills: ["AWS Lambda", "AWS MSK"],
      },
      {
        id: "unrelated",
        approvalState: "approved",
        skills: ["Go", "Product development", "Critical systems"],
      },
    ],
  }));

  assert.deepEqual(result.proposals, [
    {
      requirementId: "algorithms",
      ordinal: 1,
      coverage: "partial",
      evidenceIds: ["competitive-programming"],
      matchedSkillTags: ["data structures and algorithms"],
    },
    {
      requirementId: "cloud",
      ordinal: 2,
      coverage: "partial",
      evidenceIds: ["production-aws"],
      matchedSkillTags: ["aws lambda", "aws msk"],
    },
    {
      requirementId: "critical-software",
      ordinal: 3,
      coverage: "unsupported",
      evidenceIds: [],
      matchedSkillTags: [],
    },
    {
      requirementId: "product-company",
      ordinal: 4,
      coverage: "unsupported",
      evidenceIds: [],
      matchedSkillTags: [],
    },
  ]);
  assert.equal(result.partialCount, 2);
  assert.equal(result.unsupportedCount, 2);
  assert.equal(result.matchedEvidenceCount, 2);
});
