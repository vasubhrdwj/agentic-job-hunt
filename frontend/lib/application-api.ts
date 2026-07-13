import type {
  ApplicationActivityListResponse,
  ApplicationDetailResponse,
  ApplicationListResponse,
} from "./application-types";
import { WorkspaceApiError } from "./workspace-api";

async function responseError(response: Response): Promise<WorkspaceApiError> {
  let value: unknown = null;
  try {
    value = await response.json();
  } catch {
    // The status still provides a useful fallback when an intermediary fails.
  }
  const body = value && typeof value === "object"
    ? value as Record<string, unknown>
    : {};
  return new WorkspaceApiError(
    response.status,
    typeof body.code === "string" ? body.code : `http_${response.status}`,
    typeof body.message === "string"
      ? body.message
      : response.statusText || "The application request failed.",
    typeof body.retryable === "boolean" ? body.retryable : response.status >= 500,
  );
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<T>;
}

export async function listApplications(
  limit = 50,
  cursor?: string,
): Promise<ApplicationListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return json<ApplicationListResponse>(
    await fetch(`/api/applications?${params.toString()}`, { cache: "no-store" }),
  );
}

export async function getApplication(id: string): Promise<ApplicationDetailResponse> {
  return json<ApplicationDetailResponse>(
    await fetch(`/api/applications/${encodeURIComponent(id)}`, { cache: "no-store" }),
  );
}

export async function getApplicationActivity(
  id: string,
): Promise<ApplicationActivityListResponse> {
  return json<ApplicationActivityListResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(id)}/activity`,
      { cache: "no-store" },
    ),
  );
}
