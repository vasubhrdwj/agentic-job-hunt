import assert from "node:assert/strict";
import test from "node:test";

import {
  canUndoApplicationPursuit,
  shouldRetainUndoPursuitRequest,
  undoPursuitErrorText,
  undoPursuitRequest,
} from "../lib/application-undo-pursuit";

test("the accidental-pursuit action is limited to pre-submission stages", () => {
  assert.equal(canUndoApplicationPursuit("pursuing"), true);
  assert.equal(canUndoApplicationPursuit("ready_to_apply"), true);
  for (const stage of [
    "applied",
    "screening",
    "interviewing",
    "offer",
    "closed",
  ] as const) {
    assert.equal(canUndoApplicationPursuit(stage), false);
  }
});

test("the undo request uses the version and one safe same-origin mutation receipt", () => {
  const request = undoPursuitRequest("application/one", 7, "undo-key");
  assert.equal(request.url, "/api/applications/application%2Fone/undo-pursuit");
  assert.equal(request.init.method, "POST");
  assert.equal(request.init.credentials, "same-origin");
  assert.deepEqual(request.init.headers, {
    "If-Match": '"7"',
    "Idempotency-Key": "undo-key",
  });
});

test("durable blockers remain visible while ambiguous retries keep their receipt", () => {
  const blocker = {
    status: 409,
    code: "resource_conflict",
    message: "applications with sent outreach or replies cannot be undone",
    retryable: false,
  };
  assert.equal(undoPursuitErrorText(blocker), blocker.message);
  assert.equal(shouldRetainUndoPursuitRequest(blocker), false);

  const pending = {
    status: 409,
    code: "mutation_pending",
    message: "pending",
    retryable: false,
  };
  assert.match(undoPursuitErrorText(pending), /still being confirmed/i);
  assert.equal(shouldRetainUndoPursuitRequest(pending), true);

  const unavailable = {
    status: 503,
    code: "workspace_unavailable",
    message: "temporarily unavailable",
    retryable: true,
  };
  assert.equal(shouldRetainUndoPursuitRequest(unavailable), true);
});
