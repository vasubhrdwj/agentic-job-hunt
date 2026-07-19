import assert from "node:assert/strict";
import test from "node:test";

import {
  todayRecommendationReadinessIssues,
  type TodayRecommendationReadinessInput,
} from "../lib/today-recommendation-readiness";

function readinessInput(): TodayRecommendationReadinessInput {
  return {
    profile: {
      years_of_experience: 1,
      work_authorizations: [{ country_code: "IN", status: "citizen" }],
      base_resume: { id: "resume-1" },
    },
    resumeVersions: [{ is_base: true }],
    careerTracks: [{ active: true }],
    evidence: [{ approval_state: "approved" }],
  };
}

test("ready profiles produce no Today guidance", () => {
  assert.deepEqual(todayRecommendationReadinessIssues(readinessInput()), []);
});

test("readiness guidance returns every material missing ranking input", () => {
  assert.deepEqual(
    todayRecommendationReadinessIssues({
      profile: null,
      resumeVersions: [],
      careerTracks: [],
      evidence: [],
    }).map((issue) => issue.id),
    [
      "professional_experience",
      "work_authorization",
      "base_resume",
      "active_career_target",
      "approved_evidence",
    ],
  );
});

test("zero experience is known and an independently stored base resume counts", () => {
  const input = readinessInput();
  assert.ok(input.profile);
  input.profile.years_of_experience = 0;
  input.profile.base_resume = null;

  assert.deepEqual(todayRecommendationReadinessIssues(input), []);
});

test("pending or retired achievement evidence does not count as approved proof", () => {
  const input = readinessInput();
  input.evidence = [
    { approval_state: "pending" },
    { approval_state: "retired" },
  ];

  assert.deepEqual(
    todayRecommendationReadinessIssues(input).map((issue) => issue.id),
    ["approved_evidence"],
  );
});
