import type {
  ApplicationPackCreate,
  ApplicationPackResponse,
} from "./application-pack-types";
import type { ApplicationStage } from "./application-types";

export const APPLICATION_PACK_AUTO_START_VERSION =
  "grounded-review-auto-start-v1" as const;

export interface AutomaticApplicationPackStartPlan {
  idempotencyKey: string;
  expectedApplicationVersion: number;
  payload: ApplicationPackCreate;
}

/**
 * Starts only from durable inputs that need no owner judgment.
 *
 * A persisted posting description and the selected immutable base resume are
 * safe to pin automatically. Owner-supplied descriptions and alternate resume
 * choices remain explicit because either can materially change the review.
 */
export function buildAutomaticApplicationPackStartPlan({
  applicationId,
  applicationVersion,
  applicationStage,
  initialLoadComplete,
  projection,
  selectedResume,
  startingResumeChoiceCount,
}: {
  applicationId: string;
  applicationVersion: number;
  applicationStage: ApplicationStage;
  initialLoadComplete: boolean;
  projection: ApplicationPackResponse | null;
  selectedResume: { id: string; is_base: boolean } | null;
  startingResumeChoiceCount: number;
}): AutomaticApplicationPackStartPlan | null {
  if (
    !initialLoadComplete ||
    applicationStage !== "pursuing" ||
    !projection ||
    projection.application_id !== applicationId ||
    projection.status !== "not_started" ||
    projection.pack !== null ||
    projection.current_revision !== null ||
    !selectedResume?.is_base ||
    !selectedResume.id.trim() ||
    startingResumeChoiceCount !== 1 ||
    !Number.isInteger(applicationVersion) ||
    applicationVersion < 1 ||
    projection.blockers.some((blocker) =>
      blocker === "base_resume_missing" ||
      blocker === "owner_job_description_required" ||
      blocker === "posting_closed",
    )
  ) return null;

  return {
    idempotencyKey: [
      "application-pack:auto",
      applicationId,
      applicationVersion,
      selectedResume.id,
      APPLICATION_PACK_AUTO_START_VERSION,
    ].join(":"),
    expectedApplicationVersion: applicationVersion,
    payload: {
      base_resume_version_id: selectedResume.id,
      require_sole_current_base_resume: true,
      owner_job_description: null,
    },
  };
}
