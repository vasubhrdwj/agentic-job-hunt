import assert from "node:assert/strict";
import test from "node:test";

import {
  clearPendingSubmissionHandoffs,
  inspectAmbiguousTransitionReadback,
  parsePendingSubmissionHandoff,
  pendingSubmissionHandoffStorageKey,
  persistedVerifiedDestination,
  serializePendingSubmissionHandoff,
  transitionNavigationDestination,
  type PendingSubmissionHandoff,
} from "../lib/application-submission-handoff";
import type {
  ApplicationSubmissionResponse,
  AppliedTransitionCreate,
} from "../lib/application-submission-types";

const destination = "https://careers.example.com/jobs/backend-engineer/apply";

test("accepts only the exact persisted HTTPS destination after first-party verification", () => {
  const projection = {
    first_party_verified: true,
    available_destinations: [destination],
  };

  assert.equal(persistedVerifiedDestination(projection, destination), destination);
  assert.equal(
    persistedVerifiedDestination(projection, "https://careers.example.com/jobs/other/apply"),
    null,
  );
});

test("fails closed when verification, persistence, or HTTPS safety is absent", () => {
  assert.equal(persistedVerifiedDestination(null, destination), null);
  assert.equal(
    persistedVerifiedDestination({
      first_party_verified: false,
      available_destinations: [destination],
    }, destination),
    null,
  );
  assert.equal(
    persistedVerifiedDestination({
      first_party_verified: true,
      available_destinations: ["http://careers.example.com/apply"],
    }, "http://careers.example.com/apply"),
    null,
  );
});

const exactMaterials = {
  application_pack_id: "pack-1",
  application_pack_revision_id: "grounding-1",
  application_pack_review_event_id: "grounding-review-1",
  application_artifact_revision_id: "artifact-1",
  application_artifact_approval_event_id: "artifact-approval-1",
  tailored_resume_version_id: "resume-1",
};

const appliedProjection: ApplicationSubmissionResponse = {
  data_source: "database",
  application_id: "application-1",
  stage: "applied",
  available_destinations: [destination],
  first_party_verified: true,
  submission: {
    id: "submission-1",
    application_id: "application-1",
    ...exactMaterials,
    destination_url: destination,
    applied_on: "2026-07-20",
    submission_method: "manual",
    recorded_at: "2026-07-20T10:00:00Z",
    created_at: "2026-07-20T10:00:00Z",
  },
};

function appliedRequest(nextActionDueOn: string): AppliedTransitionCreate {
  return {
    ...exactMaterials,
    to_stage: "applied",
    destination_url: destination,
    applied_on: "2026-07-20",
    next_action_due_on: nextActionDueOn,
    confirm_manual_submission: true,
  };
}

test("an applied projection cannot confirm either of two competing follow-up dates", () => {
  const first = inspectAmbiguousTransitionReadback(
    appliedProjection,
    appliedRequest("2026-07-24"),
  );
  const competing = inspectAmbiguousTransitionReadback(
    appliedProjection,
    appliedRequest("2026-07-27"),
  );

  assert.equal(first.targetStageVisible, true);
  assert.equal(competing.targetStageVisible, true);
  assert.equal(first.exactRequestConfirmed, false);
  assert.equal(competing.exactRequestConfirmed, false);
});

test("only a successful same-receipt result can release employer navigation", () => {
  assert.equal(
    transitionNavigationDestination("ambiguous", destination),
    null,
  );
  assert.equal(
    transitionNavigationDestination("rejected", destination),
    null,
  );
  assert.equal(
    transitionNavigationDestination("same_receipt_confirmed", destination),
    destination,
  );
});

test("an ambiguous handoff round-trips its exact retry receipt across a reload", () => {
  const payload = appliedRequest("2026-07-24");
  const pending: PendingSubmissionHandoff = {
    key: "application-transition:application-1:receipt-1",
    fingerprint: JSON.stringify({ payload, navigateTo: null }),
    payload,
    expectedVersion: 7,
    navigateTo: null,
  };
  const serialized = serializePendingSubmissionHandoff("application-1", pending);

  assert.deepEqual(
    parsePendingSubmissionHandoff(serialized, "application-1"),
    pending,
  );
  assert.equal(
    pendingSubmissionHandoffStorageKey("application-1"),
    "job-hunt:pending-submission-handoff:v1:application-1",
  );
});

test("stored handoffs reject cross-application, changed, or unsafe retries", () => {
  const payload = {
    ...exactMaterials,
    to_stage: "ready_to_apply" as const,
    next_action_due_on: "2026-07-21",
    confirm_ready: true as const,
  };
  const pending: PendingSubmissionHandoff = {
    key: "application-transition:application-1:receipt-1",
    fingerprint: JSON.stringify({ payload, navigateTo: destination }),
    payload,
    expectedVersion: 7,
    navigateTo: destination,
  };
  const serialized = serializePendingSubmissionHandoff("application-1", pending);
  const changed = JSON.parse(serialized) as {
    pending: PendingSubmissionHandoff;
  };
  changed.pending.navigateTo = "http://attacker.example/apply";

  assert.equal(parsePendingSubmissionHandoff(serialized, "application-2"), null);
  assert.equal(
    parsePendingSubmissionHandoff(JSON.stringify(changed), "application-1"),
    null,
  );
  assert.equal(parsePendingSubmissionHandoff("not-json", "application-1"), null);
});

test("explicit privacy cleanup removes only pending handoff records", () => {
  const values = new Map([
    [pendingSubmissionHandoffStorageKey("application-1"), "one"],
    [pendingSubmissionHandoffStorageKey("application-2"), "two"],
    ["unrelated", "keep"],
  ]);
  const storage = {
    get length() {
      return values.size;
    },
    key(index: number) {
      return [...values.keys()][index] ?? null;
    },
    removeItem(key: string) {
      values.delete(key);
    },
  };

  clearPendingSubmissionHandoffs(storage);

  assert.deepEqual([...values], [["unrelated", "keep"]]);
});
