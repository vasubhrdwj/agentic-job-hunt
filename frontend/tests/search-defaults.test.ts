import assert from "node:assert/strict";
import test from "node:test";

import {
  careerTrackSearchPrefill,
  DEFAULT_CAREER_TARGET_SENIORITIES,
  preferredSavedSearchSeniority,
} from "../lib/search-defaults";

test("new career targets start with junior and mid levels", () => {
  assert.deepEqual(DEFAULT_CAREER_TARGET_SENIORITIES, ["junior", "mid"]);
});

test("saved searches prefer junior for a new or early-career user", () => {
  assert.equal(preferredSavedSearchSeniority(["junior", "mid"], null), "junior");
  assert.equal(preferredSavedSearchSeniority(["junior", "mid"], 1), "junior");
});

test("saved searches respect the levels allowed by the selected target", () => {
  assert.equal(preferredSavedSearchSeniority(["mid", "senior"], 1), "mid");
  assert.equal(preferredSavedSearchSeniority(["junior", "mid"], 3), "mid");
  assert.equal(preferredSavedSearchSeniority(["staff"], 1), "staff");
});

test("career target values become clean saved-search prefills", () => {
  assert.equal(
    careerTrackSearchPrefill([" Backend Engineer ", "Platform   Engineer", ""]),
    "Backend Engineer, Platform Engineer",
  );
});
