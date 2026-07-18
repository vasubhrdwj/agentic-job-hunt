import assert from "node:assert/strict";
import test from "node:test";

import {
  hasMeaningfulCandidateProfile,
  parseYearsOfExperienceInput,
  type CandidateProfileWrite,
} from "../lib/workspace-types";

function profile(
  updates: Partial<CandidateProfileWrite> = {},
): CandidateProfileWrite {
  return {
    career_thesis: null,
    current_title: null,
    current_location: null,
    years_of_experience: null,
    work_authorizations: [],
    work_modes: [],
    employment_types: ["full_time"],
    notice_period_days: null,
    onboarding_step: "profile",
    ...updates,
  };
}

test("experience alone is a meaningful profile detail, including zero", () => {
  assert.equal(
    hasMeaningfulCandidateProfile(profile({ years_of_experience: 0 })),
    true,
  );
  assert.equal(
    hasMeaningfulCandidateProfile(profile({ years_of_experience: 1.5 })),
    true,
  );
});

test("an omitted experience value does not make an otherwise blank profile meaningful", () => {
  assert.equal(hasMeaningfulCandidateProfile(profile()), false);
});

test("experience input supports decimals and rejects values outside the API range", () => {
  assert.equal(parseYearsOfExperienceInput(""), null);
  assert.equal(parseYearsOfExperienceInput("  "), null);
  assert.equal(parseYearsOfExperienceInput("0"), 0);
  assert.equal(parseYearsOfExperienceInput("1.5"), 1.5);
  assert.throws(() => parseYearsOfExperienceInput("-0.1"), RangeError);
  assert.throws(() => parseYearsOfExperienceInput("60.1"), RangeError);
  assert.throws(() => parseYearsOfExperienceInput("not a number"), RangeError);
});
