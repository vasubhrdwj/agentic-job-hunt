import assert from "node:assert/strict";
import test from "node:test";

import { getCandidateProfile } from "../lib/workspace-api";

test("legacy profile responses normalize a missing skills field safely", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => Response.json({
    id: "profile-1",
    career_thesis: null,
    current_title: "Backend Engineer",
    current_location: null,
    years_of_experience: 1.5,
    work_authorizations: [],
    work_modes: [],
    employment_types: ["full_time"],
    notice_period_days: null,
    onboarding_step: "career_track",
    base_resume: null,
    version: 2,
    created_at: "2026-07-20T00:00:00Z",
    updated_at: "2026-07-20T00:00:00Z",
  })) as typeof fetch;

  try {
    const profile = await getCandidateProfile();
    assert.ok(profile);
    assert.deepEqual(profile.data.skills, []);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
