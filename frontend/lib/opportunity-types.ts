import type { PursuitBundle } from "./application-types";

export type ScanStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "partial"
  | "failed"
  | "cancelled";

export type ScanStage =
  | "queued"
  | "fetching"
  | "persisting"
  | "matching"
  | "finalizing"
  | "complete";

export type OpportunityDecisionState = "inbox" | "watch" | "dismiss" | "pursued";
export type OpportunityDecisionAction =
  | "pursue"
  | "watch"
  | "dismiss"
  | "restore_to_inbox";
export type OpportunityLane = "reach" | "core" | "hedge" | "unassigned";
export type TodayView = "inbox" | "watching" | "dismissed" | "all";
export type EvidenceState = "verified" | "inferred" | "unknown";
export type ApplicationAcquisitionSource =
  | "job_hunt_search"
  | "referral"
  | "recruiter_inbound"
  | "direct_company"
  | "job_board"
  | "other";
export type DismissReason =
  | "not_relevant"
  | "seniority_mismatch"
  | "location_or_mode"
  | "compensation"
  | "not_a_better_move"
  | "company"
  | "already_applied"
  | "closed_or_invalid"
  | "duplicate"
  | "other";

export interface ScanWarning {
  scope: "scan" | "source";
  code: string;
  message: string;
  retryable: boolean;
  company_slug: string | null;
  source: string | null;
  occurred_at: string;
  last_success_at: string | null;
}

export interface ScanCounts {
  sources_total: number;
  sources_completed: number;
  sources_succeeded: number;
  sources_degraded: number;
  sources_failed: number;
  observed_postings: number;
  matched_postings: number;
  new_opportunities: number;
  changed_postings: number;
}

export interface ScanStatusResponse {
  id: string;
  version: number;
  saved_search_id: string;
  saved_search_version: number;
  trigger: "manual";
  status: ScanStatus;
  stage: ScanStage;
  queued_at: string;
  started_at: string | null;
  completed_at: string | null;
  counts: ScanCounts;
  warnings: ScanWarning[];
}

export interface EvidenceFact<T> {
  state: EvidenceState;
  value: T | null;
  source_label: string | null;
  observed_at: string | null;
}

export interface CompensationValue {
  currency: string;
  period: "annual" | "monthly" | "hourly";
  minimum: number | null;
  maximum: number | null;
}

export interface OpportunityFacts {
  location: EvidenceFact<string>;
  employment_type: EvidenceFact<"full_time" | "contract" | "intern">;
  posted_date: EvidenceFact<string>;
  compensation: EvidenceFact<CompensationValue>;
}

export interface OpportunityUnknown {
  field: "location" | "employment_type" | "posted_date" | "compensation";
  reason_code: string;
  message: string;
}

export interface SavedSearchProvenance {
  saved_search_id: string;
  saved_search_name: string;
  first_matched_at: string;
  last_matched_at: string;
}

export interface TransparentMatchSummary {
  state: "assessed" | "not_assessed";
  algorithm_version: string | null;
  resume_version_id: string | null;
  matched_terms: string[];
  representative_requirement: string | null;
  approved_evidence_ids: string[];
  not_assessed_reason:
    | "assessment_pending"
    | "resume_unavailable"
    | "description_unavailable"
    | "not_requested"
    | null;
}

export interface OpportunityDecisionEvent {
  id: string;
  opportunity_id: string;
  action: OpportunityDecisionAction;
  previous_state: OpportunityDecisionState;
  state: OpportunityDecisionState;
  dismiss_reason: DismissReason | null;
  note: string | null;
  restores_event_id: string | null;
  created_at: string;
}

export interface OpportunityPosting {
  id: string;
  company: string;
  company_slug: string;
  title: string;
  summary: string;
  canonical_url: string;
  source: string;
  source_job_id: string | null;
  first_party: boolean;
  state: "open" | "closed" | "unknown";
  change_kind: "new" | "changed" | "unchanged" | "closed" | "reopened";
  first_seen_at: string;
  last_confirmed_at: string;
  changed_at: string | null;
}

export interface TodayOpportunityItem {
  id: string;
  version: number;
  state: OpportunityDecisionState;
  lane: OpportunityLane;
  posting: OpportunityPosting;
  facts: OpportunityFacts;
  unknowns: OpportunityUnknown[];
  discovered_by: SavedSearchProvenance[];
  match: TransparentMatchSummary;
  latest_decision: OpportunityDecisionEvent | null;
  created_at: string;
  updated_at: string;
}

export interface TodayResponse {
  data_source: "database";
  as_of: string;
  summary: {
    needs_decision: number;
    watching: number;
    dismissed: number;
  };
  scan_health: {
    state: "never_run" | "healthy" | "degraded" | "running";
    active_searches: number;
    running_scan_id: string | null;
    last_attempt_at: string | null;
    last_success_at: string | null;
    warnings: ScanWarning[];
  };
  items: TodayOpportunityItem[];
  next_cursor: string | null;
}

export interface PostingVersionSummary {
  version: number;
  observed_at: string;
  change_kind: OpportunityPosting["change_kind"];
  changed_fields: string[];
}

export interface OpportunityDetail extends TodayOpportunityItem {
  data_source: "database";
  description: string | null;
  apply_urls: string[];
  posting_versions: PostingVersionSummary[];
  decision_history: OpportunityDecisionEvent[];
}

export interface OpportunityDecisionPayload {
  action: OpportunityDecisionAction;
  dismiss_reason?: DismissReason;
  note?: string;
  restore_decision_event_id?: string;
  initial_action_due_on?: string;
  acquisition_source?: ApplicationAcquisitionSource;
  selected_saved_search_id?: string;
}

export interface OpportunityDecisionResponse {
  opportunity_id: string;
  opportunity_version: number;
  state: OpportunityDecisionState;
  event: OpportunityDecisionEvent;
  pursuit: PursuitBundle | null;
}

export interface TodayQuery {
  view: TodayView;
  scanId?: string;
  savedSearchId?: string;
  lane?: OpportunityLane;
  cursor?: string;
  limit?: number;
}
