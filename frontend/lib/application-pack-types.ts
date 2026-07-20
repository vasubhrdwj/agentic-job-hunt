import type { AchievementEvidence } from "./workspace-types";

export type ApplicationPackStatus = "not_started" | "draft" | "reviewed";
export type ApplicationPackRevisionSource = "extracted" | "edited";
export type ApplicationPackDescriptionSource =
  | "persisted_description"
  | "owner_supplied";
export type ApplicationPackRequirementImportance = "required" | "preferred";
export type ApplicationPackRequirementCoverage =
  | "needs_review"
  | "supported"
  | "partial"
  | "unsupported";
export type ApplicationPackBlocker =
  | "base_resume_missing"
  | "approved_evidence_missing"
  | "owner_job_description_required"
  | "no_requirements_extracted"
  | "requirements_need_review"
  | "mapped_evidence_changed"
  | "posting_closed";

export interface ApplicationPackCreate {
  base_resume_version_id: string;
  require_sole_current_base_resume?: true;
  owner_job_description: string | null;
}

export interface ApplicationPackEvidenceReference {
  id: string;
  version: number;
}

export interface ApplicationPackEvidenceSnapshot {
  id: string;
  version: number;
  statement: string;
  source_resume_version_id: string | null;
  source_excerpt: string | null;
  skills: string[];
  approved_at: string;
}

export interface ApplicationPackRequirementReview {
  id: string;
  ordinal: number;
  importance: ApplicationPackRequirementImportance;
  text: string;
  source_start: number;
  source_end: number;
  coverage: ApplicationPackRequirementCoverage;
  evidence_refs: ApplicationPackEvidenceReference[];
}

export interface ApplicationPackRevisionCreate {
  parent_revision_id: string;
  requirements: ApplicationPackRequirementReview[];
  confirm_requirements_reviewed?: true;
}

export interface ApplicationPackReviewedEventCreate {
  event_type: "reviewed";
  revision_id: string;
  confirm_requirements_reviewed: true;
}

export type ApplicationPackEventCreate = ApplicationPackReviewedEventCreate;

export interface ApplicationPackSummary {
  id: string;
  version: number;
  application_id: string;
  posting_version_id: string;
  base_resume_version_id: string;
  created_at: string;
  updated_at: string;
}

export interface ApplicationPackRequirementResponse {
  id: string;
  ordinal: number;
  importance: ApplicationPackRequirementImportance;
  text: string;
  source_start: number;
  source_end: number;
  coverage: ApplicationPackRequirementCoverage;
  evidence: ApplicationPackEvidenceSnapshot[];
}

export interface ApplicationPackRevisionResponse {
  id: string;
  application_pack_id: string;
  parent_revision_id: string | null;
  revision_number: number;
  source: ApplicationPackRevisionSource;
  extraction_version: "requirements-v1";
  job_description_source: ApplicationPackDescriptionSource;
  job_description: string;
  requirements: ApplicationPackRequirementResponse[];
  created_at: string;
}

export interface ApplicationPackEventResponse {
  id: string;
  application_pack_id: string;
  revision_id: string;
  sequence_number: number;
  event_type: "reviewed";
  occurred_at: string;
}

export interface ApplicationPackResponse {
  data_source: "database";
  application_id: string;
  attributed_resume_version_id: string | null;
  status: ApplicationPackStatus;
  pack: ApplicationPackSummary | null;
  current_revision: ApplicationPackRevisionResponse | null;
  reviewed_revision: ApplicationPackRevisionResponse | null;
  review_event: ApplicationPackEventResponse | null;
  current_approved_evidence: AchievementEvidence[];
  blockers: ApplicationPackBlocker[];
}
