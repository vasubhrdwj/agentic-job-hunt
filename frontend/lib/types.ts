// UI-ready views derived from the generated FastAPI contract. Fields with
// server defaults are strengthened only where the API always serializes them.

import type { components } from "./api-generated";

type ApiSchemas = components["schemas"];

export type Seniority = ApiSchemas["JobCriteria"]["seniority"];
export type OutcomeKind = ApiSchemas["OutcomeLog"]["outcome"];
export type RunStatus = ApiSchemas["RunStateResponse"]["status"];
export type PersonSource = ApiSchemas["Person"]["source"];
export type EmploymentType = ApiSchemas["EmploymentType"];
export type CompanySource = ApiSchemas["CompanySource"];

export type JobCriteria = Omit<ApiSchemas["JobCriteria"], "employment_types"> & {
  employment_types: EmploymentType[];
};

export type Role = Omit<ApiSchemas["Role"], "apply_urls"> & {
  apply_urls: string[];
};

export type Person = ApiSchemas["Person"];

export type OutreachDraft = Omit<
  ApiSchemas["OutreachDraft"],
  "draft_id" | "role" | "person"
> & {
  draft_id: string;
  role: Role;
  person: Person;
};

export type HuntResult = Omit<ApiSchemas["HuntResult"], "roles" | "outreach"> & {
  roles: Role[];
  outreach: OutreachDraft[];
};

export type RunStateResponse = ApiSchemas["RunStateResponse"];

export type HuntCreatedResponse = ApiSchemas["HuntCreatedResponse"];

export type OutcomeLog = ApiSchemas["OutcomeLog"];

export type OutcomesResponse = ApiSchemas["OutcomesResponse"];

export type RunDetailResponse = Omit<
  ApiSchemas["RunDetailResponse"],
  "hunt_result" | "outcomes"
> & {
  hunt_result?: HuntResult | null;
  outcomes: OutcomeLog[];
};
