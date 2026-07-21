// UI-strengthened Phase 1 views derived from the generated API contract.
// Pydantic always serializes several defaulted arrays, so the UI makes those
// fields required even where OpenAPI marks request defaults as optional.

import type { components } from "./api-generated";
import type { EmploymentType, JobCriteria, Seniority } from "./types";

type ApiSchemas = components["schemas"];

export type WorkMode = NonNullable<
  ApiSchemas["CandidateProfileWrite"]["work_modes"]
>[number];
export type AuthorizationStatus = ApiSchemas["WorkAuthorization"]["status"];
export type OnboardingStep = ApiSchemas["CandidateProfileWrite"]["onboarding_step"];
export type ScheduleCadence = ApiSchemas["SavedSearchSchedule"]["cadence"];
export type DayOfWeek = NonNullable<
  ApiSchemas["SavedSearchSchedule"]["days_of_week"]
>[number];

export interface WorkAuthorization {
  country_code: string;
  status: AuthorizationStatus;
}

export interface ResumeVersionSummary {
  id: string;
  label: string;
  source: ApiSchemas["ResumeVersionSummary"]["source"];
  parent_resume_version_id: string | null;
  is_base: boolean;
  character_count: number;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ResumeVersionDetail extends ResumeVersionSummary {
  content: string;
}

export interface ResumeImportReport
  extends Omit<
    ApiSchemas["ResumeUploadReport"],
    | "resume_version"
    | "imported_profile_fields"
    | "missing_profile_fields"
    | "warnings"
  > {
  resume_version: ResumeVersionSummary;
  imported_profile_fields: string[];
  missing_profile_fields: string[];
  warnings: string[];
}

export interface CandidateProfileWrite {
  career_thesis: string | null;
  current_title: string | null;
  current_location: string | null;
  years_of_experience: number | null;
  skills: string[];
  work_authorizations: WorkAuthorization[];
  work_modes: WorkMode[];
  employment_types: Exclude<EmploymentType, "unknown">[];
  notice_period_days: number | null;
  onboarding_step: OnboardingStep;
}

export interface CandidateProfile extends CandidateProfileWrite {
  id: string;
  base_resume: ResumeVersionSummary | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export function hasMeaningfulCandidateProfile(
  profile: CandidateProfileWrite,
): boolean {
  return Boolean(
    profile.career_thesis?.trim() ||
      profile.current_title?.trim() ||
      profile.current_location?.trim() ||
      profile.years_of_experience !== null ||
      profile.skills.length > 0 ||
      profile.work_authorizations.length > 0 ||
      profile.work_modes.length > 0 ||
      profile.notice_period_days !== null,
  );
}

export function parseYearsOfExperienceInput(value: string): number | null {
  if (!value.trim()) return null;
  const years = Number(value);
  if (!Number.isFinite(years) || years < 0 || years > 60) {
    throw new RangeError("experience must be between 0 and 60 years");
  }
  return years;
}

export interface CareerPriorities {
  compensation: number;
  scope: number;
  learning: number;
  company_quality: number;
  flexibility: number;
}

export interface CareerTrackCreate {
  name: string;
  role_families: string[];
  seniority_levels: Seniority[];
  target_locations: string[];
  priorities: CareerPriorities;
  active: boolean;
}

export interface CareerTrack extends CareerTrackCreate {
  id: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export type EvidenceApprovalState =
  ApiSchemas["AchievementEvidenceResponse"]["approval_state"];

export interface AchievementEvidence {
  id: string;
  statement: string;
  source_resume_version_id: string | null;
  source_excerpt: string | null;
  skills: string[];
  origin: "owner_entered" | "resume_suggestion";
  approval_state: EvidenceApprovalState;
  approved_at: string | null;
  rejected_at: string | null;
  retired_at: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface SavedSearchSchedule {
  cadence: ScheduleCadence;
  timezone: string;
  local_time: string | null;
  days_of_week: DayOfWeek[];
}

export type SavedSearchCriteria = JobCriteria & {
  employment_types: Exclude<EmploymentType, "unknown">[];
};

export interface SavedSearchCreate {
  name: string;
  career_track_id: string;
  resume_version_id: string | null;
  criteria: SavedSearchCriteria;
  schedule: SavedSearchSchedule;
  pack: string;
  use_self_rag: boolean;
  active: boolean;
}

export interface SavedSearch extends SavedSearchCreate {
  id: string;
  last_scan_at: string | null;
  next_scan_at: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface HuntInput {
  resume_text: string;
  criteria: SavedSearchCriteria;
  pack: string;
  use_self_rag: boolean;
  provider_consent_required: true;
}

export type HuntInputBlocker = NonNullable<
  ApiSchemas["SavedSearchHuntInputResponse"]["blockers"]
>[number];

export interface SavedSearchHuntInput {
  saved_search_id: string;
  saved_search_version: number;
  career_track_id: string;
  career_track_version: number;
  resume: ResumeVersionSummary | null;
  ready: boolean;
  blockers: HuntInputBlocker[];
  warnings: string[];
  input: HuntInput | null;
}

export interface FieldError {
  field: string;
  message: string;
}

export interface Versioned<T> {
  data: T;
  etag: string;
}
