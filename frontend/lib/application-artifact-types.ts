import type { ApplicationPackSummary } from "./application-pack-types";
import type { ResumeVersionSummary } from "./workspace-types";

export type ApplicationArtifactStatus =
  | "not_started"
  | "draft"
  | "approved";

export type ApplicationArtifactBlocker =
  | "application_pack_missing"
  | "grounding_review_required"
  | "posting_closed"
  | "grounded_evidence_missing"
  | "grounding_evidence_changed"
  | "questions_need_owner_input"
  | "tailored_resume_unchanged"
  | "current_revision_rejected";

export interface ApplicationArtifactEvidenceReference {
  id: string;
  version: number;
}

export interface ApplicationArtifactEvidenceSnapshot
  extends ApplicationArtifactEvidenceReference {
  statement: string;
  source_resume_version_id: string | null;
  source_excerpt: string | null;
  skills: string[];
  approved_at: string;
}

export interface ApplicationArtifactUnsupportedRequirement {
  id: string;
  ordinal: number;
  importance: "required" | "preferred";
  text: string;
  source_start: number;
  source_end: number;
  coverage: "needs_review" | "supported" | "partial" | "unsupported";
  evidence: ApplicationArtifactEvidenceSnapshot[];
}

export interface ApplicationArtifactSourceCatalog {
  reviewed_grounding_revision_id: string;
  reviewed_grounding_revision_number: number;
  reviewed_grounding_event_id: string;
  evidence: ApplicationArtifactEvidenceSnapshot[];
  unsupported_requirements: ApplicationArtifactUnsupportedRequirement[];
}

export interface ApplicationArtifactQuestionInput {
  id: string;
  text: string;
  character_limit: number | null;
  evidence_refs: ApplicationArtifactEvidenceReference[];
}

export interface ApplicationArtifactRevisionCreate {
  operation: "generate";
  grounding_revision_id: string;
  parent_artifact_revision_id: string | null;
  generation_mode: "deterministic";
  selected_evidence_refs: ApplicationArtifactEvidenceReference[] | null;
  questions: ApplicationArtifactQuestionInput[];
}

export type ApplicationArtifactQuestionResponse = ApplicationArtifactQuestionInput;

export type ApplicationArtifactClaimSource =
  | {
      kind: "evidence_snapshot";
      evidence_id: string;
      evidence_version: number;
      quote: string;
    }
  | {
      kind: "job_description_span";
      grounding_revision_id: string;
      source_start: number;
      source_end: number;
      quote: string;
    }
  | {
      kind: "posting_field";
      posting_version_id: string;
      field: "company_name" | "title";
      value: string;
    };

export interface ApplicationArtifactClaim {
  id: string;
  start: number;
  end: number;
  text: string;
  derivation: "verbatim";
  sources: ApplicationArtifactClaimSource[];
}

export interface ApplicationArtifactText {
  text: string;
  content_hash: string;
  claims: ApplicationArtifactClaim[];
}

export interface ApplicationArtifactAnswerResponse
  extends ApplicationArtifactText {
  id: string;
  question_id: string;
  status: "answered" | "needs_owner_input";
}

export interface ApplicationArtifactDiffLine {
  operation: "equal" | "delete" | "insert";
  text: string;
  base_line_number: number | null;
  tailored_line_number: number | null;
}

export interface ApplicationArtifactResumeDiff {
  algorithm_version: "line-diff-v1";
  base_content_hash: string;
  tailored_content_hash: string;
  lines: ApplicationArtifactDiffLine[];
}

export interface ApplicationArtifactRevisionResponse {
  id: string;
  application_pack_id: string;
  grounding_revision_id: string;
  grounding_review_event_id: string;
  parent_artifact_revision_id: string | null;
  revision_number: number;
  source: "deterministic";
  generator_version: string;
  selected_evidence: ApplicationArtifactEvidenceSnapshot[];
  questions: ApplicationArtifactQuestionResponse[];
  tailored_resume: ApplicationArtifactText;
  company_note: ApplicationArtifactText;
  answers: ApplicationArtifactAnswerResponse[];
  diff: ApplicationArtifactResumeDiff;
  created_at: string;
}

export interface ApplicationArtifactApprovedEventCreate {
  event_type: "approved";
  artifact_revision_id: string;
  confirm_artifacts_reviewed: true;
}

export interface ApplicationArtifactRejectedEventCreate {
  event_type: "rejected";
  artifact_revision_id: string;
}

export type ApplicationArtifactEventCreate =
  | ApplicationArtifactApprovedEventCreate
  | ApplicationArtifactRejectedEventCreate;

export interface ApplicationArtifactEventResponse {
  id: string;
  application_pack_id: string;
  artifact_revision_id: string;
  sequence_number: number;
  event_type: "approved" | "rejected";
  tailored_resume_version_id: string | null;
  occurred_at: string;
}

export interface ApplicationArtifactsResponse {
  data_source: "database";
  application_id: string;
  status: ApplicationArtifactStatus;
  pack: ApplicationPackSummary | null;
  current_revision: ApplicationArtifactRevisionResponse | null;
  current_event: ApplicationArtifactEventResponse | null;
  approved_revision: ApplicationArtifactRevisionResponse | null;
  approval_event: ApplicationArtifactEventResponse | null;
  tailored_resume_version: ResumeVersionSummary | null;
  source_catalog: ApplicationArtifactSourceCatalog | null;
  blockers: ApplicationArtifactBlocker[];
}
