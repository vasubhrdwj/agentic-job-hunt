// UI-strengthened application views derived from the generated FastAPI contract.
// Pydantic serializes defaulted arrays and nullable lifecycle fields, so the UI
// makes those fields required where OpenAPI describes response defaults as optional.

import type { components } from "./api-generated";

type ApiSchemas = components["schemas"];

export type ApplicationStage = ApiSchemas["ApplicationStage"];
export type ActionItemKind = ApiSchemas["ActionItemKind"];
export type ActionItemStatus = ApiSchemas["ActionItemStatus"];
export type ApplicationActivityEventType =
  ApiSchemas["ApplicationActivityEventType"];
export type ApplicationPostingState = ApiSchemas["ApplicationPostingState"];

export type ApplicationPostingSummary = ApiSchemas["ApplicationPostingSummary"];

export type ActionItem = Omit<
  ApiSchemas["ActionItemResponse"],
  "completed_at" | "cancelled_at"
> & {
  completed_at: string | null;
  cancelled_at: string | null;
};

export type ApplicationActivityEvent = Omit<
  ApiSchemas["ApplicationActivityEventResponse"],
  "action_item_id" | "from_stage" | "to_stage"
> & {
  action_item_id: string | null;
  from_stage: ApplicationStage | null;
  to_stage: ApplicationStage | null;
};

export type ApplicationSummary = Omit<
  ApiSchemas["ApplicationSummary"],
  "current_action"
> & {
  current_action: ActionItem;
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
