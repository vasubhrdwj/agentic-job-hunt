import assert from "node:assert/strict";
import test from "node:test";

import { recommendationSignalLabels } from "../lib/today-recommendation";

test("recommended cards expose source-reported recency and learned role direction", () => {
  assert.deepEqual(
    recommendationSignalLabels({
      recency: "recent",
      age_days: 3,
      age_basis: "source_posted_date",
      preference: "preferred",
      preference_role_tags: ["backend"],
    }),
    [
      { kind: "recency", label: "Posted in the last 7 days" },
      { kind: "preferred", label: "Matches your preferred backend roles" },
    ],
  );
});

test("unknown source dates are described as discovery age, not posting age", () => {
  assert.deepEqual(
    recommendationSignalLabels({
      recency: "older_than_45_days",
      age_days: 60,
      age_basis: "first_confirmed_at",
      preference: "neutral",
      preference_role_tags: [],
    }),
    [{ kind: "recency", label: "First seen over 45 days ago" }],
  );
});

test("deprioritized categories are inspectable without exposing decision counts", () => {
  const labels = recommendationSignalLabels({
    recency: "current",
    age_days: 12,
    age_basis: "source_posted_date",
    preference: "deprioritized",
    preference_role_tags: ["frontend"],
  });

  assert.deepEqual(labels[1], {
    kind: "deprioritized",
    label: "Your prior decisions deprioritize frontend roles",
  });
  assert.doesNotMatch(labels.map(({ label }) => label).join(" "), /\b\d+ decisions\b/i);
});
