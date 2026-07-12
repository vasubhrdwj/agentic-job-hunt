// Thin typed fetchers for the FastAPI surface in job_hunt_agent/api.py.
// All private run fetches use { cache: "no-store" } so status and outcomes
// appear immediately after worker/cancel/outcome transitions.

import type {
  HuntCreatedResponse,
  JobCriteria,
  OutcomeLog,
  OutcomesResponse,
  RunDetailResponse,
  RunStateResponse,
} from "./types";
import {
  parseHuntCreatedResponse,
  parseOutcomesResponse,
  parseRunDetailResponse,
  parseRunStateResponse,
} from "./api-contract";

// Browser traffic stays same-origin. The Next.js Route Handler forwards only
// allowlisted headers and the private owner-session cookie to FastAPI.
const API_BASE = "";

function runHeaders(json = false): HeadersInit {
  const headers: Record<string, string> = {};
  if (json) headers["Content-Type"] = "application/json";
  return headers;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public body: string,
    public code: string,
    public retryable: boolean,
    message: string,
  ) {
    super(message);
  }
}

async function readError(res: Response): Promise<ApiError> {
  let body = "";
  try {
    body = await res.text();
  } catch {
    body = res.statusText;
  }
  let code = `http_${res.status}`;
  let message = res.statusText || "The job-search request failed.";
  let retryable = res.status === 429 || res.status >= 500;
  try {
    const problem = JSON.parse(body) as Record<string, unknown>;
    if (typeof problem.code === "string") code = problem.code;
    if (typeof problem.message === "string") message = problem.message;
    if (typeof problem.retryable === "boolean") retryable = problem.retryable;
    if (typeof problem.detail === "string") message = problem.detail;
  } catch {
    // Do not show arbitrary upstream response bodies in the UI.
  }
  return new ApiError(res.status, body, code, retryable, message);
}

export async function postHunt(
  resumeText: string,
  criteria: JobCriteria,
  pack: string,
  idempotencyKey: string,
): Promise<HuntCreatedResponse> {
  const res = await fetch(`${API_BASE}/api/hunt`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify({
      resume_text: resumeText,
      criteria,
      pack,
      provider_consent: true,
    }),
  });
  if (!res.ok) throw await readError(res);
  return parseHuntCreatedResponse(await res.json());
}

export async function getRun(runId: string): Promise<RunDetailResponse | null> {
  const res = await fetch(`${API_BASE}/api/runs/${encodeURIComponent(runId)}`, {
    cache: "no-store",
    headers: runHeaders(),
  });
  if (res.status === 404) return null;
  if (!res.ok) throw await readError(res);
  return parseRunDetailResponse(await res.json());
}

export async function postOutcomes(
  runId: string,
  outcomes: OutcomeLog[],
): Promise<OutcomesResponse> {
  const res = await fetch(
    `${API_BASE}/api/runs/${encodeURIComponent(runId)}/outcomes`,
    {
      method: "POST",
      headers: runHeaders(true),
      body: JSON.stringify({ outcomes }),
    },
  );
  if (!res.ok) throw await readError(res);
  return parseOutcomesResponse(await res.json());
}

export async function cancelRun(runId: string): Promise<RunStateResponse> {
  const res = await fetch(
    `${API_BASE}/api/runs/${encodeURIComponent(runId)}/cancel`,
    {
      method: "POST",
      headers: runHeaders(),
    },
  );
  if (!res.ok) throw await readError(res);
  return parseRunStateResponse(await res.json());
}

export async function deleteRun(runId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/runs/${encodeURIComponent(runId)}`, {
    method: "DELETE",
    headers: runHeaders(),
  });
  if (!res.ok) throw await readError(res);
}
