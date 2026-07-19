import type {
  OpportunityDecisionPayload,
  OpportunityDecisionResponse,
  OpportunityDetail,
  ScanStatusResponse,
  TodayQuery,
  TodayResponse,
} from "./opportunity-types";
import { WorkspaceApiError } from "./workspace-api";

function mutationHeaders(extra: Record<string, string>): HeadersInit {
  return { "Content-Type": "application/json", ...extra };
}

async function responseError(response: Response): Promise<WorkspaceApiError> {
  let value: unknown = null;
  try {
    value = await response.json();
  } catch {
    // The shared error still gives a useful status when an intermediary fails.
  }
  const body = value && typeof value === "object" ? value as Record<string, unknown> : {};
  return new WorkspaceApiError(
    response.status,
    typeof body.code === "string" ? body.code : `http_${response.status}`,
    typeof body.message === "string"
      ? body.message
      : response.statusText || "The opportunity request failed.",
    typeof body.retryable === "boolean" ? body.retryable : response.status >= 500,
  );
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<T>;
}

export async function createOpportunityScan(
  savedSearchId: string,
  savedSearchVersion: number,
  idempotencyKey: string,
): Promise<ScanStatusResponse> {
  const response = await fetch(
    `/api/saved-searches/${encodeURIComponent(savedSearchId)}/scans`,
    {
      method: "POST",
      headers: mutationHeaders({
        "If-Match": `"${savedSearchVersion}"`,
        "Idempotency-Key": idempotencyKey,
      }),
      body: JSON.stringify({ trigger: "manual" }),
    },
  );
  return json<ScanStatusResponse>(response);
}

export async function getOpportunityScan(scanId: string): Promise<ScanStatusResponse> {
  return json<ScanStatusResponse>(
    await fetch(`/api/scans/${encodeURIComponent(scanId)}`, { cache: "no-store" }),
  );
}

export async function getToday(query: TodayQuery): Promise<TodayResponse> {
  const params = new URLSearchParams({
    view: query.view,
    sort: query.sort ?? "recommended",
  });
  if (query.scanId) params.set("scan_id", query.scanId);
  if (query.savedSearchId) params.set("saved_search_id", query.savedSearchId);
  if (query.lane) params.set("lane", query.lane);
  if (query.cursor) params.set("cursor", query.cursor);
  params.set("limit", String(query.limit ?? 20));
  return json<TodayResponse>(
    await fetch(`/api/today?${params.toString()}`, { cache: "no-store" }),
  );
}

export async function getOpportunity(
  id: string,
  savedSearchId?: string | null,
): Promise<OpportunityDetail> {
  const params = new URLSearchParams();
  if (savedSearchId) params.set("saved_search_id", savedSearchId);
  const query = params.size ? `?${params.toString()}` : "";
  return json<OpportunityDetail>(
    await fetch(`/api/opportunities/${encodeURIComponent(id)}${query}`, { cache: "no-store" }),
  );
}

export async function decideOpportunity(
  opportunityId: string,
  version: number,
  payload: OpportunityDecisionPayload,
  idempotencyKey: string,
): Promise<OpportunityDecisionResponse> {
  const response = await fetch(
    `/api/opportunities/${encodeURIComponent(opportunityId)}/decision`,
    {
      method: "POST",
      headers: mutationHeaders({
        "If-Match": `"${version}"`,
        "Idempotency-Key": idempotencyKey,
      }),
      body: JSON.stringify(payload),
    },
  );
  return json<OpportunityDecisionResponse>(response);
}
