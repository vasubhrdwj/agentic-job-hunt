import type { ApplicationStage } from "./application-types";

interface ApiFailure {
  status: number;
  code: string;
  message: string;
  retryable: boolean;
}

export interface UndoPursuitRequest {
  url: string;
  init: RequestInit;
}

export function canUndoApplicationPursuit(stage: ApplicationStage): boolean {
  return stage === "pursuing" || stage === "ready_to_apply";
}

export function undoPursuitRequest(
  applicationId: string,
  applicationVersion: number,
  idempotencyKey: string,
): UndoPursuitRequest {
  return {
    url: `/api/applications/${encodeURIComponent(applicationId)}/undo-pursuit`,
    init: {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "If-Match": `"${applicationVersion}"`,
        "Idempotency-Key": idempotencyKey,
      },
    },
  };
}

export function shouldRetainUndoPursuitRequest(reason: unknown): boolean {
  const failure = apiFailure(reason);
  return failure === null || failure.retryable || failure.code === "mutation_pending";
}

export function undoPursuitErrorText(reason: unknown): string {
  const failure = apiFailure(reason);
  if (!failure) {
    return "The undo result is not confirmed yet. Retry the unchanged request.";
  }
  if (failure.code === "mutation_pending") {
    return "The undo is still being confirmed. Retry the unchanged request in a moment.";
  }
  if (failure.code === "version_conflict") {
    return "This application changed after the page loaded. Review the refreshed record, then try again.";
  }
  if (failure.status === 409 && failure.code === "resource_conflict") {
    return failure.message;
  }
  if (failure.code === "idempotency_conflict") {
    return "This undo request conflicts with an earlier request. Review the current application before trying again.";
  }
  return failure.message || "The accidental pursuit could not be undone.";
}

function apiFailure(value: unknown): ApiFailure | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<ApiFailure>;
  if (
    typeof candidate.status !== "number" ||
    typeof candidate.code !== "string" ||
    typeof candidate.message !== "string" ||
    typeof candidate.retryable !== "boolean"
  ) {
    return null;
  }
  return candidate as ApiFailure;
}
