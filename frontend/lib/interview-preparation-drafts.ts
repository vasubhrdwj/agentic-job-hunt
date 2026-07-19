import type { InterviewPreparationStarDraft } from "./interview-preparation-types";

const STAR_FIELDS = ["situation", "task", "action", "result"] as const;

/** Insert only grounded text into empty fields; owner text always wins. */
export function insertGroundedDraftIntoEmptyFields(
  current: InterviewPreparationStarDraft,
  grounded: InterviewPreparationStarDraft,
): InterviewPreparationStarDraft {
  const next = { ...current };
  for (const field of STAR_FIELDS) {
    if (!next[field].trim() && grounded[field].trim()) {
      next[field] = grounded[field];
    }
  }
  return next;
}
