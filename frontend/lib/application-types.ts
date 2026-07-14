// UI-strengthened application views derived from the generated FastAPI contract.
// Pydantic serializes defaulted arrays and nullable lifecycle fields, so the UI
// makes those fields required where OpenAPI describes response defaults as optional.

import type { components } from "./api-generated";

type ApiSchemas = components["schemas"];

export type ApplicationStage =
  | "pursuing"
  | "ready_to_apply"
  | "applied"
  | "screening"
  | "interviewing"
  | "offer"
  | "closed";
export type ActionItemKind =
  | "review_and_prepare_application"
  | "submit_application"
  | "follow_up_application"
  | "prepare_recruiter_screen"
  | "prepare_interview"
  | "review_offer";
export type ActionItemStatus = ApiSchemas["ActionItemStatus"];
export type ApplicationActivityEventType =
  | "application_created"
  | "application_ready_to_apply"
  | "application_applied"
  | "application_screening"
  | "application_interviewing"
  | "application_offer"
  | "application_closed";
export type ApplicationOutcomeKind =
  | "rejected"
  | "withdrawn"
  | "offer_accepted"
  | "offer_declined"
  | "no_response"
  | "posting_closed";
export type ApplicationPostingState = ApiSchemas["ApplicationPostingState"];
export type ContactBenchCoverage = ApiSchemas["ContactBenchCoverage"];
export type ContactBenchState = ApiSchemas["ContactBenchState"];
export type ContactBenchStatus = ApiSchemas["ContactBenchStatus"];
export type ContactCategory = ApiSchemas["ContactCategory"];
export type ContactCoverageStatus = ApiSchemas["ContactCoverageStatus"];
export type ContactEvidenceStatus = ApiSchemas["ContactEvidenceStatus"];
export type ContactLifecycle = ApiSchemas["ContactLifecycle"];
export type ContactProfileSource = ApiSchemas["ContactProfileSource"];
export type ContactSearchStatus = ApiSchemas["ContactSearchStatus"];

export type ApplicationPostingSummary = ApiSchemas["ApplicationPostingSummary"];

export type ActionItem = Omit<
  ApiSchemas["ActionItemResponse"],
  "completed_at" | "cancelled_at" | "interview_round_id" | "kind"
> & {
  completed_at: string | null;
  cancelled_at: string | null;
  interview_round_id: string | null;
  kind: ActionItemKind;
};

export type TodayApplicationActionApplication = Omit<
  ApiSchemas["TodayApplicationActionApplication"],
  "stage"
> & {
  stage: ApplicationStage;
};

export type TodayApplicationActionItem = Omit<
  ApiSchemas["TodayApplicationActionItem"],
  "action" | "application"
> & {
  application: TodayApplicationActionApplication;
  action: ActionItem;
};

export type TodayApplicationActionGroup = Omit<
  ApiSchemas["TodayApplicationActionGroup"],
  "items"
> & {
  items: TodayApplicationActionItem[];
};

export type TodayApplicationActionsResponse = Omit<
  ApiSchemas["TodayApplicationActionsResponse"],
  "next_7_days" | "overdue" | "today"
> & {
  overdue: TodayApplicationActionGroup;
  today: TodayApplicationActionGroup;
  next_7_days: TodayApplicationActionGroup;
};

export interface ApplicationOutcome {
  id: string;
  application_id: string;
  application_submission_id: string | null;
  stage_at_outcome: ApplicationStage;
  outcome: ApplicationOutcomeKind;
  outcome_on: string;
  recording_method: "manual";
  recorded_at: string;
  created_at: string;
}

export type ApplicationActivityEvent = Omit<
  ApiSchemas["ApplicationActivityEventResponse"],
  | "action_item_id"
  | "event_type"
  | "from_stage"
  | "interview_round_id"
  | "to_stage"
> & {
  action_item_id: string | null;
  event_type: ApplicationActivityEventType;
  effective_on: string | null;
  from_stage: ApplicationStage | null;
  interview_round_id: string | null;
  outcome_id: string | null;
  to_stage: ApplicationStage | null;
  previous_action_item_id: string | null;
  submission_id: string | null;
};

export type ApplicationSummary = Omit<
  ApiSchemas["ApplicationSummary"],
  "current_action" | "outcome" | "stage"
> & {
  current_action: ActionItem | null;
  outcome: ApplicationOutcome | null;
  stage: ApplicationStage;
};

export type PursuitBundle = Omit<
  ApiSchemas["PursuitBundle"],
  "activity" | "application"
> & {
  activity: ApplicationActivityEvent;
  application: ApplicationSummary;
};

export type ApplicationListResponse = Omit<
  ApiSchemas["ApplicationListResponse"],
  "items" | "next_cursor"
> & {
  items: ApplicationSummary[];
  next_cursor: string | null;
};

export type ApplicationDetailResponse = Omit<
  ApiSchemas["ApplicationDetailResponse"],
  "activity" | "application"
> & {
  activity: ApplicationActivityEvent[];
  application: ApplicationSummary;
};

export type ApplicationActivityListResponse = Omit<
  ApiSchemas["ApplicationActivityListResponse"],
  "items"
> & {
  items: ApplicationActivityEvent[];
};

export type ContactShortfallReason = ApiSchemas["ContactShortfallReason"];

export type RelevanceEvidenceResponse = Omit<
  ApiSchemas["RelevanceEvidenceResponse"],
  "summary" | "url"
> & {
  summary: string | null;
  url: string | null;
};

export type ContactBenchItem = Omit<
  ApiSchemas["ContactBenchItem"],
  | "cooldown_until"
  | "relationship"
  | "score_components"
  | "team_proximity"
  | "unlocked_at"
> & {
  cooldown_until: string | null;
  relationship: RelevanceEvidenceResponse;
  score_components: Record<string, number>;
  team_proximity: RelevanceEvidenceResponse;
  unlocked_at: string | null;
};

export type ContactSearchSnapshot = Omit<
  ApiSchemas["ContactSearchSnapshot"],
  | "error_code"
  | "finalized_at"
  | "job_stage"
  | "shortfall_reasons"
  | "started_at"
> & {
  error_code: string | null;
  finalized_at: string | null;
  job_stage: string | null;
  shortfall_reasons: ContactShortfallReason[];
  started_at: string | null;
};

export type ContactBenchResult = Omit<
  ApiSchemas["ContactBenchResult"],
  "contacts" | "shortfall_reasons"
> & {
  contacts: ContactBenchItem[];
  shortfall_reasons: ContactShortfallReason[];
};

export type ApplicationContactBenchResponse = Omit<
  ApiSchemas["ApplicationContactBenchResponse"],
  "current_search" | "last_completed_result"
> & {
  current_search: ContactSearchSnapshot | null;
  last_completed_result: ContactBenchResult | null;
};
