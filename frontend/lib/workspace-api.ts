import type {
  AchievementEvidence,
  CandidateProfile,
  CandidateProfileWrite,
  CareerTrack,
  CareerTrackCreate,
  FieldError,
  ResumeVersionDetail,
  ResumeVersionSummary,
  SavedSearch,
  SavedSearchCreate,
  SavedSearchHuntInput,
  Versioned,
} from "./workspace-types";

export type WorkerCapabilityReason =
  | "no_fresh_worker"
  | "unsupported_kind"
  | "incompatible_build"
  | "health_unavailable"
  | null;

export interface WorkerCapability {
  available: boolean;
  reason: WorkerCapabilityReason;
  fresh_worker_count: number;
  compatible_worker_count: number;
}

export type RoleScanCapability = WorkerCapability;

export interface OwnerHealth {
  capabilities: {
    role_scan: RoleScanCapability;
    contact_search: WorkerCapability;
  };
}

export class WorkspaceApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryable = false,
    readonly fieldErrors: FieldError[] = [],
  ) {
    super(message);
  }

  get isConflict(): boolean {
    return this.status === 409 || this.status === 428;
  }
}

function versionEtag(version: number): string {
  return `"${version}"`;
}

function responseEtag(response: Response, version: number): string {
  return response.headers.get("etag") || versionEtag(version);
}

function mutationHeaders(extra: Record<string, string> = {}): HeadersInit {
  return { "Content-Type": "application/json", ...extra };
}

export async function apiError(response: Response): Promise<WorkspaceApiError> {
  let value: unknown;
  try {
    value = await response.json();
  } catch {
    value = null;
  }
  const body =
    value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const code = typeof body.code === "string" ? body.code : `http_${response.status}`;
  let message =
    typeof body.message === "string"
      ? body.message
      : response.statusText || "The workspace request failed.";
  const retryable =
    typeof body.retryable === "boolean"
      ? body.retryable
      : response.status === 429 || response.status >= 500;
  const fieldErrors: FieldError[] = [];
  if (Array.isArray(body.field_errors)) {
    for (const item of body.field_errors) {
      if (
        item &&
        typeof item === "object" &&
        typeof (item as Record<string, unknown>).field === "string" &&
        typeof (item as Record<string, unknown>).message === "string"
      ) {
        fieldErrors.push(item as FieldError);
      }
    }
  }
  if (Array.isArray(body.detail) && body.detail.length > 0) {
    const first = body.detail[0];
    if (first && typeof first === "object") {
      const detail = first as Record<string, unknown>;
      if (typeof detail.msg === "string") message = detail.msg;
      if (Array.isArray(detail.loc)) {
        fieldErrors.push({
          field: detail.loc.filter((part) => part !== "body").join("."),
          message,
        });
      }
    }
  }
  return new WorkspaceApiError(response.status, code, message, retryable, fieldErrors);
}

export async function expectJson<T>(response: Response): Promise<T> {
  if (!response.ok) throw await apiError(response);
  return (await response.json()) as T;
}

export function createIdempotencyKey(scope: string): string {
  return `${scope}:${crypto.randomUUID()}`;
}

export async function getOwnerHealth(): Promise<OwnerHealth> {
  const response = await fetch("/api/health", { cache: "no-store" });
  return expectJson<OwnerHealth>(response);
}

export async function getCandidateProfile(): Promise<Versioned<CandidateProfile> | null> {
  const response = await fetch("/api/me/profile", { cache: "no-store" });
  if (response.status === 404) return null;
  const data = await expectJson<CandidateProfile>(response);
  return { data, etag: responseEtag(response, data.version) };
}

export async function saveCandidateProfile(
  payload: CandidateProfileWrite,
  expectedVersion: number,
): Promise<Versioned<CandidateProfile>> {
  const response = await fetch("/api/me/profile", {
    method: "PUT",
    headers: mutationHeaders({ "If-Match": versionEtag(expectedVersion) }),
    body: JSON.stringify(payload),
  });
  const data = await expectJson<CandidateProfile>(response);
  return { data, etag: responseEtag(response, data.version) };
}

export async function listResumeVersions(): Promise<ResumeVersionSummary[]> {
  const response = await fetch("/api/me/resume-versions", { cache: "no-store" });
  return (await expectJson<{ items: ResumeVersionSummary[] }>(response)).items;
}

export async function getResumeVersion(
  resumeId: string,
): Promise<Versioned<ResumeVersionDetail>> {
  const response = await fetch(
    `/api/me/resume-versions/${encodeURIComponent(resumeId)}`,
    { cache: "no-store" },
  );
  const data = await expectJson<ResumeVersionDetail>(response);
  return { data, etag: responseEtag(response, data.version) };
}

export async function createResumeVersion(
  payload: {
    label: string;
    content: string;
    source: "pasted" | "edited";
    parent_resume_version_id: string | null;
    set_as_base: boolean;
  },
  idempotencyKey: string,
): Promise<Versioned<ResumeVersionDetail>> {
  const response = await fetch("/api/me/resume-versions", {
    method: "POST",
    headers: mutationHeaders({ "Idempotency-Key": idempotencyKey }),
    body: JSON.stringify(payload),
  });
  const data = await expectJson<ResumeVersionDetail>(response);
  return { data, etag: responseEtag(response, data.version) };
}

export async function makeBaseResume(
  resume: ResumeVersionSummary,
  idempotencyKey: string,
): Promise<Versioned<ResumeVersionSummary>> {
  const response = await fetch(
    `/api/me/resume-versions/${encodeURIComponent(resume.id)}/base`,
    {
      method: "POST",
      headers: mutationHeaders({
        "If-Match": versionEtag(resume.version),
        "Idempotency-Key": idempotencyKey,
      }),
    },
  );
  const data = await expectJson<ResumeVersionSummary>(response);
  return { data, etag: responseEtag(response, data.version) };
}

export async function listCareerTracks(): Promise<CareerTrack[]> {
  const response = await fetch("/api/career-tracks", { cache: "no-store" });
  return (await expectJson<{ items: CareerTrack[] }>(response)).items;
}

export async function createCareerTrack(
  payload: CareerTrackCreate,
  idempotencyKey: string,
): Promise<Versioned<CareerTrack>> {
  const response = await fetch("/api/career-tracks", {
    method: "POST",
    headers: mutationHeaders({ "Idempotency-Key": idempotencyKey }),
    body: JSON.stringify(payload),
  });
  const data = await expectJson<CareerTrack>(response);
  return { data, etag: responseEtag(response, data.version) };
}

export async function updateCareerTrack(
  track: CareerTrack,
  payload: CareerTrackCreate,
): Promise<Versioned<CareerTrack>> {
  const response = await fetch(
    `/api/career-tracks/${encodeURIComponent(track.id)}`,
    {
      method: "PATCH",
      headers: mutationHeaders({ "If-Match": versionEtag(track.version) }),
      body: JSON.stringify(payload),
    },
  );
  const data = await expectJson<CareerTrack>(response);
  return { data, etag: responseEtag(response, data.version) };
}

export async function listEvidence(): Promise<AchievementEvidence[]> {
  const response = await fetch("/api/me/evidence", { cache: "no-store" });
  return (await expectJson<{ items: AchievementEvidence[] }>(response)).items;
}

export async function createEvidence(
  payload: {
    statement: string;
    source_resume_version_id: string | null;
    source_excerpt: string | null;
    skills: string[];
    origin: "owner_entered";
  },
  idempotencyKey: string,
): Promise<Versioned<AchievementEvidence>> {
  const response = await fetch("/api/me/evidence", {
    method: "POST",
    headers: mutationHeaders({ "Idempotency-Key": idempotencyKey }),
    body: JSON.stringify(payload),
  });
  const data = await expectJson<AchievementEvidence>(response);
  return { data, etag: responseEtag(response, data.version) };
}

export async function reviewEvidence(
  evidence: AchievementEvidence,
  approvalState: "approved" | "rejected" | "retired",
): Promise<Versioned<AchievementEvidence>> {
  const response = await fetch(
    `/api/me/evidence/${encodeURIComponent(evidence.id)}`,
    {
      method: "PATCH",
      headers: mutationHeaders({ "If-Match": versionEtag(evidence.version) }),
      body: JSON.stringify({ approval_state: approvalState }),
    },
  );
  const data = await expectJson<AchievementEvidence>(response);
  return { data, etag: responseEtag(response, data.version) };
}

export async function listSavedSearches(): Promise<SavedSearch[]> {
  const response = await fetch("/api/saved-searches", { cache: "no-store" });
  return (await expectJson<{ items: SavedSearch[] }>(response)).items;
}

export async function createSavedSearch(
  payload: SavedSearchCreate,
  idempotencyKey: string,
): Promise<Versioned<SavedSearch>> {
  const response = await fetch("/api/saved-searches", {
    method: "POST",
    headers: mutationHeaders({ "Idempotency-Key": idempotencyKey }),
    body: JSON.stringify(payload),
  });
  const data = await expectJson<SavedSearch>(response);
  return { data, etag: responseEtag(response, data.version) };
}

export async function updateSavedSearch(
  search: SavedSearch,
  payload: SavedSearchCreate,
): Promise<Versioned<SavedSearch>> {
  const response = await fetch(
    `/api/saved-searches/${encodeURIComponent(search.id)}`,
    {
      method: "PATCH",
      headers: mutationHeaders({ "If-Match": versionEtag(search.version) }),
      body: JSON.stringify(payload),
    },
  );
  const data = await expectJson<SavedSearch>(response);
  return { data, etag: responseEtag(response, data.version) };
}

export async function deleteSavedSearch(search: SavedSearch): Promise<void> {
  const response = await fetch(
    `/api/saved-searches/${encodeURIComponent(search.id)}`,
    {
      method: "DELETE",
      headers: { "If-Match": versionEtag(search.version) },
    },
  );
  if (!response.ok) throw await apiError(response);
}

export async function getSavedSearchHuntInput(
  searchId: string,
): Promise<SavedSearchHuntInput> {
  const response = await fetch(
    `/api/saved-searches/${encodeURIComponent(searchId)}/hunt-input`,
    { cache: "no-store" },
  );
  return expectJson<SavedSearchHuntInput>(response);
}
