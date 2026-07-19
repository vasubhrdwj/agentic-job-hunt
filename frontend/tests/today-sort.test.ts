import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_TODAY_SORT,
  parseTodaySort,
  todaySortExplanation,
} from "../lib/today-sort";

test("Today defaults unknown and absent sort values to Recommended", () => {
  assert.equal(DEFAULT_TODAY_SORT, "recommended");
  assert.equal(parseTodaySort(null), "recommended");
  assert.equal(parseTodaySort("recommended"), "recommended");
  assert.equal(parseTodaySort("unsupported"), "recommended");
});

test("Today exposes Newest as an explicit alternative", () => {
  assert.equal(parseTodaySort("newest"), "newest");
  assert.match(todaySortExplanation("newest"), /recently surfaced/i);
});

test("Recommended explains its categorical server-side order without a score", () => {
  const explanation = todaySortExplanation("recommended");
  assert.match(explanation, /before pagination/i);
  assert.match(explanation, /eligibility/i);
  assert.doesNotMatch(explanation, /percent|score/i);
});
