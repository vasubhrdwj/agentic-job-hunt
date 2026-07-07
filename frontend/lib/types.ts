// Mirrors job_hunt_agent/schemas.py and job_hunt_agent/api.py.
// Update both sides together when fields change.

export type Seniority = "junior" | "mid" | "senior" | "staff";
export type OutcomeKind =
  | "replied"
  | "no_reply"
  | "introduced"
  | "rejected"
  | "pending";
export type PersonSource = "linkedin" | "github" | "company_page" | "other";
export type EmploymentType = "full_time" | "contract" | "intern" | "unknown";
export type CompanySource =
  | "greenhouse"
  | "lever"
  | "ashby"
  | "workday"
  | "smartrecruiters"
  | "workable"
  | "bespoke"
  | "google_jobs"
  | "scrape";

export type JobCriteria = {
  role_keywords: string[];
  seniority: Seniority;
  location: string[];
  comp_min_lpa?: number | null;
  comp_max_lpa?: number | null;
  employment_types: EmploymentType[];
  max_age_days?: number | null;
  country: string;
};

export type Role = {
  company: string;
  title: string;
  url: string;
  location: string;
  summary: string;
  match_reason: string;
  source: CompanySource;
  apply_urls: string[];
  posted_at?: string | null;
  source_updated_at?: string | null;
  employment_type: EmploymentType;
  raw_description?: string | null;
  fit_score?: number | null;
  confidence: number;
};

export type Person = {
  name: string;
  title: string;
  company: string;
  profile_url: string;
  source: PersonSource;
  why_relevant: string;
  verified_current_employer: boolean;
  confidence: number;
};

export type OutreachDraft = {
  draft_id: string;
  role: Role;
  person: Person;
  message: string;
  /** Composite 1-5 LLM-judge score (V9). Null when the judge was unavailable. */
  eval_score?: number | null;
};

export type HuntResult = {
  run_id: string;
  roles: Role[];
  outreach: OutreachDraft[];
};

export type HuntCreatedResponse = HuntResult & {
  status: "succeeded";
  access_token: string;
};

export type OutcomeLog = {
  draft_id: string;
  outcome: OutcomeKind;
  notes?: string | null;
  logged_at?: string | null; // server-set on insert
};

export type OutcomesResponse = {
  ok: boolean;
  inserted: number;
  outcomes: OutcomeLog[];
};

export type RunDetailResponse = {
  hunt_result: HuntResult;
  outcomes: OutcomeLog[];
};
