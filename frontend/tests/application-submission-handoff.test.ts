import assert from "node:assert/strict";
import test from "node:test";

import { persistedVerifiedDestination } from "../lib/application-submission-handoff";

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
