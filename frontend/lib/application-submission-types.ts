import type {
  ApplicationActivityEvent,
  ApplicationStage,
  ApplicationSummary,
} from "./application-types";

export interface ExactApplicationMaterials {
  application_pack_id: string;
  application_pack_revision_id: string;
  application_pack_review_event_id: string;
  application_artifact_revision_id: string;
  application_artifact_approval_event_id: string;
  tailored_resume_version_id: string;
}

export interface ReadyToApplyTransitionCreate
  extends ExactApplicationMaterials {
  to_stage: "ready_to_apply";
  next_action_due_on: string;
  confirm_ready: true;
}

export interface AppliedTransitionCreate extends ExactApplicationMaterials {
  to_stage: "applied";
  destination_url: string;
  applied_on: string;
  next_action_due_on: string;
  confirm_manual_submission: true;
}

export type ApplicationTransitionCreate =
  | ReadyToApplyTransitionCreate
  | AppliedTransitionCreate;

export interface ApplicationSubmissionRecord extends ExactApplicationMaterials {
  id: string;
  application_id: string;
  destination_url: string;
  applied_on: string;
  submission_method: "manual";
  recorded_at: string;
  created_at: string;
}

export interface ApplicationTransitionResponse {
  data_source: "database";
  application: ApplicationSummary;
  activity_event: ApplicationActivityEvent;
  submission: ApplicationSubmissionRecord | null;
  transition_created: boolean;
}

export interface ApplicationSubmissionResponse {
  data_source: "database";
  application_id: string;
  stage: ApplicationStage;
  available_destinations: string[];
  first_party_verified: boolean;
  submission: ApplicationSubmissionRecord | null;
}
