import type { ApplicationStage } from "./application-types";

export type ApplicationDossierSection =
  | "application_pack"
  | "application_materials"
  | "application_submission"
  | "people"
  | "interview_rounds"
  | "interview_preparation"
  | "application_progress";

export interface ApplicationDossierLayout {
  primary: readonly ApplicationDossierSection[];
  secondary: readonly ApplicationDossierSection[];
}

const LAYOUT_BY_STAGE: Record<ApplicationStage, ApplicationDossierLayout> = {
  pursuing: {
    primary: [
      "application_pack",
      "application_materials",
      "application_submission",
      "people",
    ],
    secondary: ["application_progress"],
  },
  ready_to_apply: {
    primary: ["application_materials", "application_submission", "people"],
    secondary: ["application_pack", "application_progress"],
  },
  applied: {
    primary: [
      "people",
      "interview_rounds",
      "interview_preparation",
      "application_progress",
    ],
    secondary: ["application_pack", "application_materials", "application_submission"],
  },
  screening: {
    primary: ["interview_rounds", "interview_preparation", "application_progress"],
    secondary: [
      "people",
      "application_pack",
      "application_materials",
      "application_submission",
    ],
  },
  interviewing: {
    primary: ["interview_rounds", "interview_preparation", "application_progress"],
    secondary: [
      "people",
      "application_pack",
      "application_materials",
      "application_submission",
    ],
  },
  offer: {
    primary: ["application_progress"],
    secondary: [
      "interview_rounds",
      "interview_preparation",
      "people",
      "application_pack",
      "application_materials",
      "application_submission",
    ],
  },
  closed: {
    primary: [],
    secondary: [
      "interview_rounds",
      "interview_preparation",
      "people",
      "application_pack",
      "application_materials",
      "application_submission",
      "application_progress",
    ],
  },
};

export function applicationDossierLayout(
  stage: ApplicationStage,
): ApplicationDossierLayout {
  return LAYOUT_BY_STAGE[stage];
}

export function interviewHistoryIsKnownEmpty(stage: ApplicationStage): boolean {
  return stage === "pursuing" || stage === "ready_to_apply";
}
