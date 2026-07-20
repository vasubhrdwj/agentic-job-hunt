import type {
  HuntCreatedResponse,
  OutcomesResponse,
  RunDetailResponse,
  RunStateResponse,
} from "./types";

type OwnerSessionCore = {
  owner_id: string;
  display_name: string;
  timezone: string;
  local_date: string;
  expires_at: string;
};

export type OwnerSession = OwnerSessionCore &
  (
    | { account_attached: true; account_email: string }
    | { account_attached: false; account_email: null }
  );

export type SessionStatus = {
  state: "ready" | "setup_required";
  signup_enabled: boolean;
};

const RUN_STATUSES = new Set([
  "queued",
  "running",
  "succeeded",
  "failed",
  "cancelled",
  "dead_letter",
]);

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((entry) => typeof entry === "string");
}

function validOutcome(value: unknown): boolean {
  const body = record(value);
  return Boolean(
    body &&
      typeof body.draft_id === "string" &&
      typeof body.outcome === "string" &&
      ["replied", "no_reply", "introduced", "rejected", "pending"].includes(
        body.outcome,
      ),
  );
}

function validPerson(value: unknown): boolean {
  const body = record(value);
  return Boolean(
    body &&
      typeof body.name === "string" &&
      typeof body.title === "string" &&
      typeof body.company === "string" &&
      typeof body.profile_url === "string" &&
      typeof body.source === "string" &&
      typeof body.why_relevant === "string" &&
      typeof body.verified_current_employer === "boolean" &&
      typeof body.confidence === "number",
  );
}

function validRole(value: unknown): boolean {
  const body = record(value);
  return Boolean(
    body &&
      typeof body.company === "string" &&
      typeof body.title === "string" &&
      typeof body.url === "string" &&
      typeof body.location === "string" &&
      typeof body.summary === "string" &&
      typeof body.match_reason === "string" &&
      typeof body.source === "string" &&
      strings(body.apply_urls) &&
      typeof body.employment_type === "string" &&
      typeof body.confidence === "number",
  );
}

function validDraft(value: unknown): boolean {
  const body = record(value);
  return Boolean(
    body &&
      typeof body.draft_id === "string" &&
      typeof body.message === "string" &&
      validRole(body.role) &&
      validPerson(body.person),
  );
}

function validHuntResult(value: unknown): boolean {
  const body = record(value);
  return Boolean(
    body &&
      typeof body.run_id === "string" &&
      Array.isArray(body.roles) &&
      body.roles.every(validRole) &&
      Array.isArray(body.outreach) &&
      body.outreach.every(validDraft),
  );
}

function validRunState(value: unknown): value is RunStateResponse {
  const body = record(value);
  return Boolean(
    body &&
      typeof body.run_id === "string" &&
      typeof body.status === "string" &&
      RUN_STATUSES.has(body.status) &&
      typeof body.stage === "string" &&
      Number.isInteger(body.attempt_count) &&
      Number.isInteger(body.max_attempts),
  );
}

function invalid(label: string): never {
  throw new Error(`The job-search service returned an invalid ${label} response.`);
}

export function parseHuntCreatedResponse(value: unknown): HuntCreatedResponse {
  const body = record(value);
  if (
    !validRunState(value) ||
    !body ||
    typeof body.access_token !== "string" ||
    typeof body.reused !== "boolean"
  ) {
    return invalid("hunt creation");
  }
  return value as HuntCreatedResponse;
}

export function parseRunDetailResponse(value: unknown): RunDetailResponse {
  const body = record(value);
  if (
    !validRunState(value) ||
    !body ||
    !Array.isArray(body.outcomes) ||
    !body.outcomes.every(validOutcome) ||
    !(body.hunt_result === null || validHuntResult(body.hunt_result))
  ) {
    return invalid("run detail");
  }
  return value as RunDetailResponse;
}

export function parseRunStateResponse(value: unknown): RunStateResponse {
  return validRunState(value) ? value : invalid("run state");
}

export function parseOutcomesResponse(value: unknown): OutcomesResponse {
  const body = record(value);
  if (
    !body ||
    body.ok !== true ||
    !Number.isInteger(body.inserted) ||
    !Array.isArray(body.outcomes) ||
    !body.outcomes.every(validOutcome)
  ) {
    return invalid("outcomes");
  }
  return value as OutcomesResponse;
}

export function parseOwnerSession(value: unknown): OwnerSession {
  const body = record(value);
  const accountAttached = body?.account_attached ?? false;
  const accountEmail = body?.account_email ?? null;
  if (
    !body ||
    typeof body.owner_id !== "string" ||
    typeof body.display_name !== "string" ||
    typeof body.timezone !== "string" ||
    typeof body.local_date !== "string" ||
    !/^\d{4}-\d{2}-\d{2}$/.test(body.local_date) ||
    typeof body.expires_at !== "string" ||
    typeof accountAttached !== "boolean" ||
    !(
      accountEmail === null ||
      (typeof accountEmail === "string" && accountEmail.length > 0)
    ) ||
    (accountAttached && accountEmail === null) ||
    (!accountAttached && accountEmail !== null)
  ) {
    return invalid("owner session");
  }
  return {
    ...body,
    account_attached: accountAttached,
    account_email: accountEmail,
  } as OwnerSession;
}

export function parseSessionStatus(value: unknown): SessionStatus {
  const body = record(value);
  const signupEnabled = body?.signup_enabled ?? false;
  if (
    !body ||
    (body.state !== "ready" && body.state !== "setup_required") ||
    typeof signupEnabled !== "boolean"
  ) {
    return invalid("session status");
  }
  return { ...body, signup_enabled: signupEnabled } as SessionStatus;
}
