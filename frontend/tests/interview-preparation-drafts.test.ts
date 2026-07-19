import assert from "node:assert/strict";
import test from "node:test";

import { insertGroundedDraftIntoEmptyFields } from "../lib/interview-preparation-drafts";

test("grounded starter fills only blank fields and preserves all owner text", () => {
  const current = {
    situation: "Owner situation",
    task: "Owner task",
    action: "Owner action",
    result: "",
  };
  const grounded = {
    situation: "",
    task: "",
    action: "",
    result: "Reduced failures by 40%.",
  };

  assert.deepEqual(insertGroundedDraftIntoEmptyFields(current, grounded), {
    ...current,
    result: "Reduced failures by 40%.",
  });
  assert.equal(current.result, "");
});

test("grounded starter never overwrites a saved or dirty result", () => {
  const current = {
    situation: "Owner situation",
    task: "Owner task",
    action: "Owner action",
    result: "Owner verified result",
  };
  const grounded = {
    situation: "",
    task: "",
    action: "",
    result: "Reduced failures by 40%.",
  };

  assert.deepEqual(insertGroundedDraftIntoEmptyFields(current, grounded), current);
});
