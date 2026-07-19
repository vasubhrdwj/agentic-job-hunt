import assert from "node:assert/strict";
import test from "node:test";

import { careerTrackMatchesPayload } from "../lib/career-track-update";
import type { CareerTrack, CareerTrackCreate } from "../lib/workspace-types";

const payload: CareerTrackCreate = {
  name: "Backend Software Engineering",
  role_families: ["Backend Software Engineer", "Software Development Engineer"],
  seniority_levels: ["junior", "mid"],
  target_locations: ["India", "Remote India"],
  priorities: {
    compensation: 3,
    scope: 4,
    learning: 5,
    company_quality: 3,
    flexibility: 2,
  },
  active: true,
};

const saved: CareerTrack = {
  ...payload,
  id: "track-1",
  version: 2,
  created_at: "2026-07-19T10:00:00Z",
  updated_at: "2026-07-19T11:00:00Z",
};

test("confirms an ambiguous update only when every saved field matches", () => {
  assert.equal(careerTrackMatchesPayload(saved, payload), true);
});

test("does not mistake a partial or conflicting update for success", () => {
  assert.equal(
    careerTrackMatchesPayload(
      { ...saved, role_families: ["Backend Software Engineer", "Site Reliability Engineer"] },
      payload,
    ),
    false,
  );
  assert.equal(
    careerTrackMatchesPayload(
      { ...saved, priorities: { ...saved.priorities, learning: 4 } },
      payload,
    ),
    false,
  );
  assert.equal(careerTrackMatchesPayload({ ...saved, active: false }, payload), false);
});
