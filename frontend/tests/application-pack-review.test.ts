import assert from "node:assert/strict";
import test from "node:test";

import {
  preparedReviewDecisionsFromPayload,
  preparedRevisionWasReviewed,
  revisionMatchesPayload,
} from "../lib/application-pack-review";
import { buildPreparedAssessmentReviewRows } from "../lib/application-pack-preparation";
import type {
  ApplicationPackResponse,
  ApplicationPackRevisionCreate,
} from "../lib/application-pack-types";

const NOW = "2026-07-19T08:00:00Z";

const payload: ApplicationPackRevisionCreate = {
  parent_revision_id: "revision-1",
  confirm_requirements_reviewed: true,
  requirements: [
    {
      id: "requirement-1",
      ordinal: 1,
      importance: "required",
      text: "Python experience required.",
      source_start: 16,
      source_end: 43,
      coverage: "unsupported",
      evidence_refs: [],
    },
  ],
};

function reviewedResponse(): ApplicationPackResponse {
  const revision = {
    id: "revision-2",
    application_pack_id: "pack-1",
    parent_revision_id: "revision-1",
    revision_number: 2,
    source: "edited" as const,
    extraction_version: "requirements-v1" as const,
    job_description_source: "persisted_description" as const,
    job_description: "Requirements:\n- Python experience required.",
    requirements: [
      {
        id: "requirement-1",
        ordinal: 1,
        importance: "required" as const,
        text: "Python experience required.",
        source_start: 16,
        source_end: 43,
        coverage: "unsupported" as const,
        evidence: [],
      },
    ],
    created_at: NOW,
  };
  return {
    data_source: "database",
    application_id: "application-1",
    attributed_resume_version_id: "resume-1",
    status: "reviewed",
    pack: {
      id: "pack-1",
      version: 2,
      application_id: "application-1",
      posting_version_id: "posting-version-1",
      base_resume_version_id: "resume-1",
      created_at: NOW,
      updated_at: NOW,
    },
    current_revision: revision,
    reviewed_revision: revision,
    review_event: {
      id: "event-1",
      application_pack_id: "pack-1",
      revision_id: revision.id,
      sequence_number: 1,
      event_type: "reviewed",
      occurred_at: NOW,
    },
    current_approved_evidence: [],
    blockers: [],
  };
}

test("an atomic prepared review is confirmed only by its exact revision and event", () => {
  const response = reviewedResponse();

  assert.equal(revisionMatchesPayload(response, payload), true);
  assert.equal(preparedRevisionWasReviewed(response, payload), true);

  assert.equal(preparedRevisionWasReviewed(response, {
    ...payload,
    confirm_requirements_reviewed: undefined,
  }), false);
  assert.equal(preparedRevisionWasReviewed({
    ...response,
    review_event: { ...response.review_event!, revision_id: "different-revision" },
  }, payload), false);
  assert.equal(preparedRevisionWasReviewed({
    ...response,
    status: "draft",
    reviewed_revision: null,
    review_event: null,
  }, payload), false);
});

test("a newer or differently grounded revision cannot resolve an ambiguous approval", () => {
  const response = reviewedResponse();

  assert.equal(revisionMatchesPayload(response, {
    ...payload,
    parent_revision_id: "different-parent",
  }), false);
  assert.equal(revisionMatchesPayload(response, {
    ...payload,
    requirements: [{ ...payload.requirements[0]!, coverage: "partial", evidence_refs: [
      { id: "evidence-1", version: 1 },
    ] }],
  }), false);
});

test("the visible approval preview is derived from the exact adjusted mutation payload", () => {
  const adjustedPayload: ApplicationPackRevisionCreate = {
    ...payload,
    requirements: [{
      ...payload.requirements[0]!,
      coverage: "supported",
      evidence_refs: [{ id: "evidence-2", version: 7 }],
    }],
  };
  const decisions = preparedReviewDecisionsFromPayload(adjustedPayload);
  const rows = buildPreparedAssessmentReviewRows(
    decisions,
    reviewedResponse().current_revision!.requirements,
    [{ id: "evidence-2", statement: "Reduced Python service failures by 40%." }],
  );

  assert.deepEqual(decisions, [{
    requirementId: "requirement-1",
    ordinal: 1,
    coverage: "supported",
    evidenceIds: ["evidence-2"],
  }]);
  assert.equal(rows[0]?.coverage, adjustedPayload.requirements[0]?.coverage);
  assert.equal(rows[0]?.linkedEvidenceCount, 1);
  assert.deepEqual(rows[0]?.linkedEvidenceStatements, [
    "Reduced Python service failures by 40%.",
  ]);
});
