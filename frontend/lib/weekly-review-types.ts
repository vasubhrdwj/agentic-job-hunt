// UI-strengthened weekly-review views derived from the generated FastAPI contract.
// Pydantic always serializes defaulted arrays and nullable rates, so those fields
// are required here even when OpenAPI marks their defaults as optional.

import type { components } from "./api-generated";
import type {
  ActionItem,
  ApplicationPostingSummary,
  ApplicationSummary,
} from "./application-types";

type ApiSchemas = components["schemas"];

export type WeeklyReviewDecision = ApiSchemas["ApplicationActionReviewDecision"];
export type FunnelStage = ApiSchemas["FunnelStage"];
export type VersionedActionItem = ActionItem & { version: number };

export type WeeklyReviewPolicy = ApiSchemas["WeeklyReviewPolicy"];
export type WeeklyReviewWindow = ApiSchemas["WeeklyReviewWindow"];

export type StaleApplicationReviewItem = Omit<
  ApiSchemas["WeeklyReviewStaleApplication"],
  "application" | "current_action" | "posting"
> & {
  application: ApplicationSummary;
  posting: ApplicationPostingSummary;
  current_action: VersionedActionItem;
};

export type FunnelStageMetric = Omit<
  ApiSchemas["FunnelStageMetric"],
  "rate" | "stage"
> & {
  stage: FunnelStage;
  rate: number | null;
};

export type FunnelSegmentMetric = Omit<
  ApiSchemas["FunnelSegmentMetric"],
  "stages"
> & {
  stages: FunnelStageMetric[];
};

export type OutreachObservedMetric = Omit<
  ApiSchemas["OutreachObservedMetric"],
  "observed_rate"
> & {
  observed_rate: number | null;
};

export type ContactRescueMetric = Omit<
  ApiSchemas["OutreachRescueMetric"],
  "observed_rate"
> & {
  observed_rate: number | null;
};

export type WeeklyReviewResponse = Omit<
  ApiSchemas["WeeklyReviewResponse"],
  "funnel" | "outreach" | "stale_applications"
> & {
  stale_applications: StaleApplicationReviewItem[];
  funnel: Omit<
    ApiSchemas["WeeklyReviewFunnel"],
    | "by_acquisition_source"
    | "by_assessment_band"
    | "by_career_track"
    | "overall"
  > & {
    overall: FunnelStageMetric[];
    by_acquisition_source: FunnelSegmentMetric[];
    by_career_track: FunnelSegmentMetric[];
    by_assessment_band: FunnelSegmentMetric[];
  };
  outreach: Omit<
    ApiSchemas["WeeklyReviewOutreach"],
    | "by_contact_category"
    | "by_sequence_position"
    | "contacts_two_through_five"
  > & {
    by_contact_category: OutreachObservedMetric[];
    by_sequence_position: OutreachObservedMetric[];
    contacts_two_through_five: ContactRescueMetric[];
  };
};

export type ApplicationActionReviewCreate =
  ApiSchemas["ApplicationActionReviewCreate"];
export type ApplicationActionReviewRecord =
  ApiSchemas["ApplicationActionReviewResponse"];

export type ApplicationActionReviewMutationResponse = Omit<
  ApiSchemas["ApplicationActionReviewMutationResponse"],
  "action" | "application" | "review"
> & {
  application: ApplicationSummary;
  action: VersionedActionItem;
  review: ApplicationActionReviewRecord;
};
