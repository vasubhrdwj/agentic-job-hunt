// Thin typed fetchers for the FastAPI surface in job_hunt_agent/api.py.
// All server-component fetches use { cache: "no-store" } so logged outcomes
// appear immediately after a POST. Next 16 defaults to uncached, but we set
// it explicitly so the intent survives future framework defaults.

import type {
  HuntCreatedResponse,
  JobCriteria,
  OutcomeLog,
  OutcomesResponse,
  RunDetailResponse,
} from "./types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/+$/, "") ??
  "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    public body: string,
  ) {
    super(`API error ${status}: ${body.slice(0, 200)}`);
  }
}

async function readError(res: Response): Promise<ApiError> {
  let body = "";
  try {
    body = await res.text();
  } catch {
    body = res.statusText;
  }
  return new ApiError(res.status, body);
}

export async function postHunt(
  resumeText: string,
  criteria: JobCriteria,
  pack: string,
): Promise<HuntCreatedResponse> {
  const res = await fetch(`${API_BASE}/api/hunt`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      resume_text: resumeText,
      criteria,
      pack,
      provider_consent: true,
    }),
  });
  if (!res.ok) throw await readError(res);
  return (await res.json()) as HuntCreatedResponse;
}

export async function getRun(
  runId: string,
  accessToken: string,
): Promise<RunDetailResponse | null> {
  const res = await fetch(`${API_BASE}/api/runs/${encodeURIComponent(runId)}`, {
    cache: "no-store",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (res.status === 404) return null;
  if (!res.ok) throw await readError(res);
  return (await res.json()) as RunDetailResponse;
}

export async function postOutcomes(
  runId: string,
  accessToken: string,
  outcomes: OutcomeLog[],
): Promise<OutcomesResponse> {
  const res = await fetch(
    `${API_BASE}/api/runs/${encodeURIComponent(runId)}/outcomes`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ outcomes }),
    },
  );
  if (!res.ok) throw await readError(res);
  return (await res.json()) as OutcomesResponse;
}

export async function deleteRun(
  runId: string,
  accessToken: string,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/runs/${encodeURIComponent(runId)}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw await readError(res);
}
