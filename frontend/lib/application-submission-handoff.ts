import type {
  ApplicationSubmissionResponse,
  AppliedTransitionCreate,
  ReadyToApplyTransitionCreate,
} from "./application-submission-types";

type PersistedDestinationProjection = Pick<
  ApplicationSubmissionResponse,
  "available_destinations" | "first_party_verified"
>;

export function persistedVerifiedDestination(
  projection: PersistedDestinationProjection | null,
  candidate: string,
): string | null {
  if (!projection?.first_party_verified) return null;
  if (!projection.available_destinations.includes(candidate)) return null;
  try {
    return new URL(candidate).protocol === "https:" ? candidate : null;
  } catch {
    return null;
  }
}

export type SubmissionHandoffTransition =
  | ReadyToApplyTransitionCreate
  | AppliedTransitionCreate;

export interface PendingSubmissionHandoff {
  key: string;
  fingerprint: string;
  payload: SubmissionHandoffTransition;
  expectedVersion: number;
  navigateTo: string | null;
}

const PENDING_HANDOFF_VERSION = 1;
const PENDING_HANDOFF_STORAGE_PREFIX = "job-hunt:pending-submission-handoff:v1:";
const EXACT_MATERIAL_FIELDS = [
  "application_pack_id",
  "application_pack_revision_id",
  "application_pack_review_event_id",
  "application_artifact_revision_id",
  "application_artifact_approval_event_id",
  "tailored_resume_version_id",
] as const;
type ExactMaterialFields = Pick<
  ReadyToApplyTransitionCreate,
  (typeof EXACT_MATERIAL_FIELDS)[number]
>;

export function pendingSubmissionHandoffStorageKey(applicationId: string): string {
  return `${PENDING_HANDOFF_STORAGE_PREFIX}${applicationId}`;
}

export function clearPendingSubmissionHandoffs(
  storage?: Pick<Storage, "length" | "key" | "removeItem">,
): void {
  const target = storage ?? (typeof window === "undefined" ? null : window.sessionStorage);
  if (!target) return;
  try {
    for (let index = target.length - 1; index >= 0; index -= 1) {
      const key = target.key(index);
      if (key?.startsWith(PENDING_HANDOFF_STORAGE_PREFIX)) target.removeItem(key);
    }
  } catch {
    // Cleanup is best-effort for browsers that block session storage access.
  }
}

export function serializePendingSubmissionHandoff(
  applicationId: string,
  pending: PendingSubmissionHandoff,
): string {
  return JSON.stringify({
    version: PENDING_HANDOFF_VERSION,
    applicationId,
    pending,
  });
}

/**
 * Restore only an exact, internally consistent same-application retry.
 * Browser storage is untrusted input, so malformed payloads, changed
 * fingerprints, and unsafe destinations fail closed.
 */
export function parsePendingSubmissionHandoff(
  raw: string | null,
  applicationId: string,
): PendingSubmissionHandoff | null {
  if (!raw || !applicationId.trim()) return null;
  let decoded: unknown;
  try {
    decoded = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(decoded)) return null;
  if (
    decoded.version !== PENDING_HANDOFF_VERSION ||
    decoded.applicationId !== applicationId ||
    !isRecord(decoded.pending)
  ) return null;

  const stored = decoded.pending;
  if (
    !nonEmptyString(stored.key) ||
    stored.key.length > 512 ||
    !nonEmptyString(stored.fingerprint) ||
    !Number.isInteger(stored.expectedVersion) ||
    Number(stored.expectedVersion) < 1 ||
    !isRecord(stored.payload) ||
    !(stored.navigateTo === null || isSafeHttpsUrl(stored.navigateTo))
  ) return null;

  const common = exactMaterialFields(stored.payload);
  if (!common) return null;
  let payload: SubmissionHandoffTransition;
  if (
    stored.payload.to_stage === "ready_to_apply" &&
    stored.payload.confirm_ready === true &&
    isDateOnly(stored.payload.next_action_due_on) &&
    isSafeHttpsUrl(stored.navigateTo)
  ) {
    payload = {
      ...common,
      to_stage: "ready_to_apply",
      next_action_due_on: stored.payload.next_action_due_on,
      confirm_ready: true,
    };
  } else if (
    stored.payload.to_stage === "applied" &&
    stored.payload.confirm_manual_submission === true &&
    isSafeHttpsUrl(stored.payload.destination_url) &&
    isDateOnly(stored.payload.applied_on) &&
    isDateOnly(stored.payload.next_action_due_on) &&
    stored.navigateTo === null
  ) {
    payload = {
      ...common,
      to_stage: "applied",
      destination_url: stored.payload.destination_url,
      applied_on: stored.payload.applied_on,
      next_action_due_on: stored.payload.next_action_due_on,
      confirm_manual_submission: true,
    };
  } else {
    return null;
  }

  const navigateTo = stored.navigateTo as string | null;
  const fingerprint = JSON.stringify({ payload, navigateTo });
  if (stored.fingerprint !== fingerprint) return null;
  return {
    key: stored.key,
    fingerprint,
    payload,
    expectedVersion: Number(stored.expectedVersion),
    navigateTo,
  };
}

export interface AmbiguousTransitionReadback {
  exactRequestConfirmed: false;
  targetStageVisible: boolean;
}

/**
 * A read projection can show that a stage exists, but cannot identify the
 * mutation receipt that produced it. It also omits the ready due date and the
 * applied follow-up due date. Therefore it must never confirm an ambiguous
 * handoff request; only replaying the unchanged receipt can do that.
 */
export function inspectAmbiguousTransitionReadback(
  projection: Pick<ApplicationSubmissionResponse, "stage" | "submission"> | null,
  payload: SubmissionHandoffTransition,
): AmbiguousTransitionReadback {
  const targetStageVisible = payload.to_stage === "ready_to_apply"
    ? projection?.stage === "ready_to_apply"
    : projection?.stage === "applied" && projection.submission !== null;
  return {
    exactRequestConfirmed: false,
    targetStageVisible,
  };
}

export function transitionNavigationDestination(
  resolution: "same_receipt_confirmed" | "ambiguous" | "rejected",
  requestedDestination: string | null,
): string | null {
  return resolution === "same_receipt_confirmed" ? requestedDestination : null;
}

function exactMaterialFields(
  payload: Record<string, unknown>,
): ExactMaterialFields | null {
  if (EXACT_MATERIAL_FIELDS.some((field) => !nonEmptyString(payload[field]))) {
    return null;
  }
  return Object.fromEntries(
    EXACT_MATERIAL_FIELDS.map((field) => [field, payload[field]]),
  ) as ExactMaterialFields;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isDateOnly(value: unknown): value is string {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
}

function isSafeHttpsUrl(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}
