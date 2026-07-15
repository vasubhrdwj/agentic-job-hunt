import type {
  ApplicationMilestoneCorrectionMutationResponse,
  ApplicationActivityListResponse,
  ApplicationContactBenchResponse,
  ApplicationDetailResponse,
  ApplicationListResponse,
  TodayApplicationActionsResponse,
} from "./application-types";
import type {
  ApplicationMilestoneCorrectionCreate,
} from "./application-correction-types";
import type {
  ApplicationInterviewRoundsResponse,
  InterviewRoundCreate,
  InterviewRoundEventCreate,
  InterviewRoundMutationResponse,
} from "./application-interview-types";
import type {
  ApplicationPackCreate,
  ApplicationPackEventCreate,
  ApplicationPackRevisionCreate,
  ApplicationPackResponse,
} from "./application-pack-types";
import type {
  ApplicationArtifactEventCreate,
  ApplicationArtifactRevisionCreate,
  ApplicationArtifactsResponse,
} from "./application-artifact-types";
import type {
  ApplicationOutreachResponse,
  OutreachEventCreate,
  OutreachMessageCreate,
  OutreachReplyCreate,
} from "./outreach-types";
import type {
  ApplicationSubmissionResponse,
  ApplicationTransitionCreate,
  ApplicationTransitionResponse,
} from "./application-submission-types";
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
  let message = typeof body.message === "string"
    ? body.message
    : response.statusText || "The application request failed.";
  const fieldErrors: Array<{ field: string; message: string }> = [];
  if (Array.isArray(body.field_errors)) {
    for (const item of body.field_errors) {
      if (
        item &&
        typeof item === "object" &&
        typeof (item as Record<string, unknown>).field === "string" &&
        typeof (item as Record<string, unknown>).message === "string"
      ) {
        fieldErrors.push(item as { field: string; message: string });
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
  return new WorkspaceApiError(
    response.status,
    typeof body.code === "string" ? body.code : `http_${response.status}`,
    message,
    typeof body.retryable === "boolean"
      ? body.retryable
      : response.status === 429 || response.status >= 500,
    fieldErrors,
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

export async function getTodayApplicationActions(
  limit = 50,
): Promise<TodayApplicationActionsResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  return json<TodayApplicationActionsResponse>(
    await fetch(`/api/today/application-actions?${params.toString()}`, {
      cache: "no-store",
    }),
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

export async function correctApplicationMilestoneDate(
  applicationId: string,
  activityEventId: string,
  applicationVersion: number,
  idempotencyKey: string,
  payload: ApplicationMilestoneCorrectionCreate,
): Promise<ApplicationMilestoneCorrectionMutationResponse> {
  return json<ApplicationMilestoneCorrectionMutationResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(applicationId)}/activity/${encodeURIComponent(activityEventId)}/corrections`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "If-Match": `"${applicationVersion}"`,
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify(payload),
      },
    ),
  );
}

export async function getApplicationInterviewRounds(
  applicationId: string,
): Promise<ApplicationInterviewRoundsResponse> {
  return json<ApplicationInterviewRoundsResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(applicationId)}/interview-rounds`,
      { cache: "no-store", credentials: "same-origin" },
    ),
  );
}

export async function createApplicationInterviewRound(
  applicationId: string,
  applicationVersion: number,
  idempotencyKey: string,
  payload: InterviewRoundCreate,
): Promise<InterviewRoundMutationResponse> {
  return json<InterviewRoundMutationResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(applicationId)}/interview-rounds`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "If-Match": `"${applicationVersion}"`,
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify(payload),
      },
    ),
  );
}

export async function recordApplicationInterviewRoundEvent(
  applicationId: string,
  roundId: string,
  roundVersion: number,
  idempotencyKey: string,
  payload: InterviewRoundEventCreate,
): Promise<InterviewRoundMutationResponse> {
  return json<InterviewRoundMutationResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(applicationId)}/interview-rounds/${encodeURIComponent(roundId)}/events`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "If-Match": `"${roundVersion}"`,
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify(payload),
      },
    ),
  );
}

export async function getApplicationSubmission(
  applicationId: string,
): Promise<ApplicationSubmissionResponse> {
  return json<ApplicationSubmissionResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(applicationId)}/submission`,
      { cache: "no-store", credentials: "same-origin" },
    ),
  );
}

export async function transitionApplication(
  applicationId: string,
  applicationVersion: number,
  idempotencyKey: string,
  payload: ApplicationTransitionCreate,
): Promise<ApplicationTransitionResponse> {
  return json<ApplicationTransitionResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(applicationId)}/transitions`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "If-Match": `"${applicationVersion}"`,
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify(payload),
      },
    ),
  );
}

export async function getApplicationPack(
  applicationId: string,
): Promise<ApplicationPackResponse> {
  return json<ApplicationPackResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(applicationId)}/application-pack`,
      { cache: "no-store", credentials: "same-origin" },
    ),
  );
}

export async function createApplicationPack(
  applicationId: string,
  applicationVersion: number,
  idempotencyKey: string,
  payload: ApplicationPackCreate,
): Promise<ApplicationPackResponse> {
  return json<ApplicationPackResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(applicationId)}/application-packs`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "If-Match": `"${applicationVersion}"`,
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify(payload),
      },
    ),
  );
}

export async function createApplicationPackRevision(
  applicationId: string,
  packId: string,
  packVersion: number,
  idempotencyKey: string,
  payload: ApplicationPackRevisionCreate,
): Promise<ApplicationPackResponse> {
  return json<ApplicationPackResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(applicationId)}/application-packs/${encodeURIComponent(packId)}/revisions`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "If-Match": `"${packVersion}"`,
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify(payload),
      },
    ),
  );
}

export async function recordApplicationPackEvent(
  applicationId: string,
  packId: string,
  packVersion: number,
  idempotencyKey: string,
  payload: ApplicationPackEventCreate,
): Promise<ApplicationPackResponse> {
  return json<ApplicationPackResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(applicationId)}/application-packs/${encodeURIComponent(packId)}/events`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "If-Match": `"${packVersion}"`,
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify(payload),
      },
    ),
  );
}

export async function getApplicationArtifacts(
  applicationId: string,
): Promise<ApplicationArtifactsResponse> {
  return json<ApplicationArtifactsResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(applicationId)}/application-artifacts`,
      { cache: "no-store", credentials: "same-origin" },
    ),
  );
}

export async function createApplicationArtifactRevision(
  applicationId: string,
  packId: string,
  packVersion: number,
  idempotencyKey: string,
  payload: ApplicationArtifactRevisionCreate,
): Promise<ApplicationArtifactsResponse> {
  return json<ApplicationArtifactsResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(applicationId)}/application-packs/${encodeURIComponent(packId)}/artifact-revisions`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "If-Match": `"${packVersion}"`,
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify(payload),
      },
    ),
  );
}

export async function recordApplicationArtifactEvent(
  applicationId: string,
  packId: string,
  packVersion: number,
  idempotencyKey: string,
  payload: ApplicationArtifactEventCreate,
): Promise<ApplicationArtifactsResponse> {
  return json<ApplicationArtifactsResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(applicationId)}/application-packs/${encodeURIComponent(packId)}/artifact-events`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "If-Match": `"${packVersion}"`,
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify(payload),
      },
    ),
  );
}

export async function getApplicationContacts(
  applicationId: string,
): Promise<ApplicationContactBenchResponse> {
  return json<ApplicationContactBenchResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(applicationId)}/contacts`,
      { cache: "no-store" },
    ),
  );
}

export async function startApplicationContactSearch(
  applicationId: string,
  applicationVersion: number,
  idempotencyKey: string,
): Promise<ApplicationContactBenchResponse> {
  return json<ApplicationContactBenchResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(applicationId)}/contact-searches`,
      {
        method: "POST",
        headers: {
          "If-Match": `"${applicationVersion}"`,
          "Idempotency-Key": idempotencyKey,
        },
      },
    ),
  );
}

export async function getApplicationOutreach(
  applicationId: string,
): Promise<ApplicationOutreachResponse> {
  return json<ApplicationOutreachResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(applicationId)}/outreach`,
      {
        cache: "no-store",
        credentials: "same-origin",
      },
    ),
  );
}

export async function startApplicationOutreachSequence(
  applicationId: string,
  applicationVersion: number,
  idempotencyKey: string,
): Promise<ApplicationOutreachResponse> {
  return json<ApplicationOutreachResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(applicationId)}/outreach-sequences`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "If-Match": `"${applicationVersion}"`,
          "Idempotency-Key": idempotencyKey,
        },
      },
    ),
  );
}

export async function saveApplicationOutreachMessage(
  applicationId: string,
  sequenceId: string,
  sequenceVersion: number,
  idempotencyKey: string,
  payload: OutreachMessageCreate,
): Promise<ApplicationOutreachResponse> {
  return json<ApplicationOutreachResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(applicationId)}/outreach-sequences/${encodeURIComponent(sequenceId)}/messages`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "If-Match": `"${sequenceVersion}"`,
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify(payload),
      },
    ),
  );
}

export async function recordApplicationOutreachEvent(
  applicationId: string,
  sequenceId: string,
  sequenceVersion: number,
  idempotencyKey: string,
  payload: OutreachEventCreate,
): Promise<ApplicationOutreachResponse> {
  return json<ApplicationOutreachResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(applicationId)}/outreach-sequences/${encodeURIComponent(sequenceId)}/events`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "If-Match": `"${sequenceVersion}"`,
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify(payload),
      },
    ),
  );
}

export async function recordApplicationOutreachReply(
  applicationId: string,
  sequenceId: string,
  sequenceVersion: number,
  idempotencyKey: string,
  payload: OutreachReplyCreate,
): Promise<ApplicationOutreachResponse> {
  return json<ApplicationOutreachResponse>(
    await fetch(
      `/api/applications/${encodeURIComponent(applicationId)}/outreach-sequences/${encodeURIComponent(sequenceId)}/replies`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "If-Match": `"${sequenceVersion}"`,
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify(payload),
      },
    ),
  );
}
