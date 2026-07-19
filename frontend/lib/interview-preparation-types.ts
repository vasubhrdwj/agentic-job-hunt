import type {
  ApplicationPackEvidenceReference,
  ApplicationPackEvidenceSnapshot,
} from "./application-pack-types";

export type InterviewPreparationStatus =
  | "blocked"
  | "not_started"
  | "in_progress"
  | "ready";

export type InterviewPreparationPromptCategory =
  | "role_motivation"
  | "key_requirement"
  | "impact"
  | "conflict_ambiguity"
  | "failure_learning"
  | "leadership_collaboration";

export type InterviewPreparationBlocker =
  | "application_not_submitted"
  | "application_closed"
  | "reviewed_application_pack_missing"
  | "approved_evidence_missing"
  | "evidence_snapshot_changed"
  | "required_requirement_evidence_missing"
  | "required_prompt_capacity_exceeded";

export interface InterviewPreparationStarDraft {
  situation: string;
  task: string;
  action: string;
  result: string;
}

export type InterviewPreparationMissingFact =
  | "situation_context"
  | "personal_responsibility"
  | "specific_actions"
  | "verified_result"
  | "motivation_connection"
  | "conflict_or_ambiguity_details"
  | "setback_and_learning_details"
  | "leadership_or_collaboration_details";

export interface InterviewPreparationStartingDraft {
  generation_method: "exact_sources_v1";
  source_requirement_id: string;
  source_evidence: ApplicationPackEvidenceReference[];
  result_evidence: ApplicationPackEvidenceReference | null;
  draft: InterviewPreparationStarDraft;
  missing_facts: InterviewPreparationMissingFact[];
}

export interface InterviewPreparationPromptDraftCreate
  extends InterviewPreparationStarDraft {
  prompt_id: string;
}

export interface InterviewPreparationPrompt {
  id: string;
  category: InterviewPreparationPromptCategory;
  question: string;
  requirement_id: string | null;
  requirement_text: string | null;
  evidence: ApplicationPackEvidenceSnapshot[];
  starting_draft: InterviewPreparationStartingDraft | null;
  draft: InterviewPreparationStarDraft;
  missing_sections: Array<"situation" | "task" | "action" | "result">;
}

export interface InterviewPreparationRevisionCreate {
  source_fingerprint: string;
  parent_revision_id: string | null;
  prompt_drafts: InterviewPreparationPromptDraftCreate[];
  confirm_owner_authored: true;
}

export interface ApplicationInterviewPreparationResponse {
  data_source: "database";
  generation_method: "deterministic_scaffold";
  truth_policy: "owner_authored_only";
  application_id: string;
  application_version: number;
  application_submission_id: string | null;
  preparation_id: string | null;
  preparation_version: number | null;
  write_version_scope: "application" | "preparation";
  write_version: number;
  status: InterviewPreparationStatus;
  source_fingerprint: string | null;
  role: {
    job_posting_id: string;
    posting_version_id: string;
    company: string;
    title: string;
    summary: string;
  };
  target: {
    kind: "recruiter_screen" | "interview_round";
    label: string;
    interview_round_id: string | null;
    interview_round_version: number | null;
    interview_round_kind: string | null;
    scheduled_start_at: string | null;
    scheduled_timezone: string | null;
  };
  grounding_revision_id: string | null;
  latest_revision: {
    id: string;
    revision_number: number;
    parent_revision_id: string | null;
    source_fingerprint: string;
    recording_method: "owner_authored";
    created_at: string;
  } | null;
  requirements: Array<{
    id: string;
    ordinal: number;
    importance: "required" | "preferred";
    text: string;
    coverage: "supported" | "partial" | "unsupported" | "needs_review";
    evidence: ApplicationPackEvidenceSnapshot[];
  }>;
  required_evidence_backed_count: number;
  prompt_capacity: 12;
  evidence_gaps: Array<{
    requirement_id: string;
    importance: "required" | "preferred";
    requirement_text: string;
    reason: "no_approved_evidence" | "evidence_changed";
  }>;
  prompts: InterviewPreparationPrompt[];
  previous_context_stale: boolean;
  previous_prompts: InterviewPreparationPrompt[];
  blockers: InterviewPreparationBlocker[];
  next_steps: string[];
  disclaimer: string;
}
