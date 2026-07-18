import type {
  ApplicationPackResponse,
  ApplicationPackRevisionCreate,
} from "./application-pack-types";
import type { PreparedAssessmentReviewDecision } from "./application-pack-preparation";

export function preparedReviewDecisionsFromPayload(
  payload: ApplicationPackRevisionCreate | null,
): PreparedAssessmentReviewDecision[] {
  return (payload?.requirements ?? []).flatMap((requirement) => {
    if (requirement.coverage === "needs_review") return [];
    return [{
      requirementId: requirement.id,
      ordinal: requirement.ordinal,
      coverage: requirement.coverage,
      evidenceIds: requirement.evidence_refs.map((item) => item.id),
    }];
  });
}

export function revisionMatchesPayload(
  response: ApplicationPackResponse,
  payload: ApplicationPackRevisionCreate,
): boolean {
  const revision = response.current_revision;
  if (
    !revision ||
    revision.parent_revision_id !== payload.parent_revision_id ||
    revision.source !== "edited" ||
    revision.requirements.length !== payload.requirements.length
  ) return false;
  return revision.requirements.every((requirement, index) => {
    const expected = payload.requirements[index];
    if (
      !expected ||
      requirement.id !== expected.id ||
      requirement.ordinal !== expected.ordinal ||
      requirement.importance !== expected.importance ||
      requirement.text !== expected.text ||
      requirement.source_start !== expected.source_start ||
      requirement.source_end !== expected.source_end ||
      requirement.coverage !== expected.coverage
    ) return false;
    const actualRefs = requirement.evidence.map((item) => `${item.id}:${item.version}`).sort();
    const expectedRefs = expected.evidence_refs.map((item) => `${item.id}:${item.version}`).sort();
    return JSON.stringify(actualRefs) === JSON.stringify(expectedRefs);
  });
}

export function preparedRevisionWasReviewed(
  response: ApplicationPackResponse,
  payload: ApplicationPackRevisionCreate,
): boolean {
  const revision = response.current_revision;
  return Boolean(
    payload.confirm_requirements_reviewed === true &&
    revision &&
    revisionMatchesPayload(response, payload) &&
    response.status === "reviewed" &&
    response.reviewed_revision?.id === revision.id &&
    response.review_event?.revision_id === revision.id
  );
}
