// UI-strengthened interview-round views derived from the generated FastAPI
// contract. Pydantic serializes nullable lifecycle fields and defaulted arrays,
// so the browser treats those response fields as present rather than optional.

import type { components } from "./api-generated";
import type { ApplicationSummary } from "./application-types";

type ApiSchemas = components["schemas"];

export type InterviewHistoryState =
  | "checking"
  | "none"
  | "recorded"
  | "unavailable";

export type InterviewRoundKind = ApiSchemas["InterviewRoundKind"];
export type InterviewMeetingFormat = ApiSchemas["InterviewMeetingFormat"];
export type InterviewRoundStatus = ApiSchemas["InterviewRoundStatus"];
export type InterviewRoundEventType = ApiSchemas["InterviewRoundEventType"];
export type InterviewCancellationParty =
  ApiSchemas["InterviewCancellationParty"];

export type InterviewRoundCreate = ApiSchemas["InterviewRoundCreate"];
export type InterviewRoundRescheduledCreate =
  ApiSchemas["InterviewRoundRescheduledCreate"];
export type InterviewRoundCompletedCreate =
  ApiSchemas["InterviewRoundCompletedCreate"];
export type InterviewRoundCancelledCreate =
  ApiSchemas["InterviewRoundCancelledCreate"];

export type InterviewRoundEventCreate =
  | InterviewRoundRescheduledCreate
  | InterviewRoundCompletedCreate
  | InterviewRoundCancelledCreate;

export type InterviewRoundEventResponse = Omit<
  ApiSchemas["InterviewRoundEventResponse"],
  "cancelled_by" | "effective_on" | "from_status"
> & {
  cancelled_by: InterviewCancellationParty | null;
  effective_on: string | null;
  from_status: InterviewRoundStatus | null;
};

export type InterviewRoundResponse = Omit<
  ApiSchemas["InterviewRoundResponse"],
  "cancelled_by" | "cancelled_on" | "completed_on" | "events"
> & {
  cancelled_by: InterviewCancellationParty | null;
  cancelled_on: string | null;
  completed_on: string | null;
  events: InterviewRoundEventResponse[];
};

export type ApplicationInterviewRoundsResponse = Omit<
  ApiSchemas["ApplicationInterviewRoundsResponse"],
  "application" | "rounds"
> & {
  application: ApplicationSummary;
  rounds: InterviewRoundResponse[];
};

export type InterviewRoundMutationResponse = Omit<
  ApiSchemas["InterviewRoundMutationResponse"],
  "application" | "event" | "round"
> & {
  application: ApplicationSummary;
  event: InterviewRoundEventResponse;
  round: InterviewRoundResponse;
};
