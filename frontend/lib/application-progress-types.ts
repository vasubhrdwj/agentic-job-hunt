import type { ApplicationOutcomeKind } from "./application-types";

export interface ScreeningTransitionCreate {
  to_stage: "screening";
  reached_on: string;
  next_action_due_on: string;
  confirm_progress: true;
}

export interface InterviewingTransitionCreate {
  to_stage: "interviewing";
  reached_on: string;
  next_action_due_on: string;
  confirm_progress: true;
}

export interface OfferTransitionCreate {
  to_stage: "offer";
  received_on: string;
  next_action_due_on: string;
  confirm_offer: true;
}

export interface ClosedTransitionCreate {
  to_stage: "closed";
  outcome: ApplicationOutcomeKind;
  outcome_on: string;
  confirm_close: true;
}

export type ApplicationProgressTransitionCreate =
  | ScreeningTransitionCreate
  | InterviewingTransitionCreate
  | OfferTransitionCreate
  | ClosedTransitionCreate;
