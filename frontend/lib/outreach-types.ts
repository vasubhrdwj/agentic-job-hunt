// UI-strengthened manual-outreach views derived from the generated FastAPI
// contract. Pydantic serializes defaulted arrays and nullable lifecycle fields,
// so the browser treats those response fields as present rather than optional.

import type { components } from "./api-generated";

type ApiSchemas = components["schemas"];

export type ApplicationOutreachStatus =
  ApiSchemas["ApplicationOutreachStatus"];
export type OutreachSequenceStatus = ApiSchemas["OutreachSequenceStatus"];
export type OutreachMessageKind = ApiSchemas["OutreachMessageKind"];
export type OutreachChannel = ApiSchemas["OutreachChannel"];
export type OutreachOutcome = ApiSchemas["OutreachOutcome"];

export type OutreachMessageCreate = ApiSchemas["OutreachMessageCreate"];
export type OutreachCopiedEventCreate =
  ApiSchemas["OutreachCopiedEventCreate"];
export type OutreachMarkedSentEventCreate =
  ApiSchemas["OutreachMarkedSentEventCreate"];
export type OutreachOutcomeEventCreate =
  ApiSchemas["OutreachOutcomeEventCreate"];
export type OutreachPauseEventCreate =
  ApiSchemas["OutreachPauseEventCreate"];
export type OutreachResumeEventCreate =
  ApiSchemas["OutreachResumeEventCreate"];
export type OutreachStopEventCreate =
  ApiSchemas["OutreachStopEventCreate"];

export type OutreachEventCreate =
  | OutreachCopiedEventCreate
  | OutreachMarkedSentEventCreate
  | OutreachOutcomeEventCreate
  | OutreachPauseEventCreate
  | OutreachResumeEventCreate
  | OutreachStopEventCreate;
export type OutreachEventType = OutreachEventCreate["event_type"];

export type OutreachTimelineEvent =
  | ApiSchemas["OutreachSequenceStartedTimelineEvent"]
  | ApiSchemas["OutreachMessageSavedTimelineEvent"]
  | ApiSchemas["OutreachCopiedTimelineEvent"]
  | ApiSchemas["OutreachMarkedSentTimelineEvent"]
  | ApiSchemas["OutreachOutcomeTimelineEvent"]
  | ApiSchemas["OutreachPausedTimelineEvent"]
  | ApiSchemas["OutreachResumedTimelineEvent"]
  | ApiSchemas["OutreachStoppedTimelineEvent"]
  | ApiSchemas["OutreachWaveAdvancedTimelineEvent"];
export type OutreachTimelineEventType = OutreachTimelineEvent["event_type"];

export type OutreachMessageVersion = Omit<
  ApiSchemas["OutreachMessageVersionResponse"],
  "copied_at" | "sent_at" | "sent_channel"
> & {
  copied_at: string | null;
  sent_at: string | null;
  sent_channel: OutreachChannel | null;
};

export type OutreachRecipient = Omit<
  ApiSchemas["OutreachRecipientResponse"],
  | "follow_up_due_at"
  | "follow_up_message"
  | "initial_message"
  | "outcome"
  | "outcome_at"
> & {
  follow_up_due_at: string | null;
  follow_up_message: OutreachMessageVersion | null;
  initial_message: OutreachMessageVersion | null;
  outcome: OutreachOutcome | null;
  outcome_at: string | null;
};

export type OutreachSequence = Omit<
  ApiSchemas["OutreachSequenceResponse"],
  | "active_wave"
  | "completed_at"
  | "paused_at"
  | "reason"
  | "stopped_at"
> & {
  active_wave: number | null;
  completed_at: string | null;
  paused_at: string | null;
  reason: string | null;
  stopped_at: string | null;
};

export type ApplicationOutreachResponse = Omit<
  ApiSchemas["ApplicationOutreachResponse"],
  "recipients" | "sequence" | "timeline"
> & {
  recipients: OutreachRecipient[];
  sequence: OutreachSequence | null;
  timeline: OutreachTimelineEvent[];
};

export type OutreachMessageVersionResponse = OutreachMessageVersion;
export type OutreachRecipientResponse = OutreachRecipient;
export type OutreachSequenceResponse = OutreachSequence;
