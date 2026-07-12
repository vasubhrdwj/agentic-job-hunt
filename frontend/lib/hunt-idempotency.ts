import type { JobCriteria } from "./types";

type HuntSubmission = {
  resume_text: string;
  criteria: JobCriteria;
  pack: string;
  provider_consent: true;
};

const STORAGE_PREFIX = "job-hunt:idempotency:v1:";
const inMemoryKeys = new Map<string, string>();

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value === null || typeof value !== "object") return value;

  const sorted: Record<string, unknown> = {};
  for (const key of Object.keys(value).sort()) {
    const child = (value as Record<string, unknown>)[key];
    if (child !== undefined) sorted[key] = canonicalize(child);
  }
  return sorted;
}

async function submissionFingerprint(
  resumeText: string,
  criteria: JobCriteria,
  pack: string,
): Promise<string> {
  const submission: HuntSubmission = {
    resume_text: resumeText,
    criteria,
    pack,
    provider_consent: true,
  };
  const canonical = JSON.stringify(canonicalize(submission));
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(canonical),
  );
  const fingerprint = Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
  return fingerprint;
}

export async function huntIdempotencyKey(
  resumeText: string,
  criteria: JobCriteria,
  pack: string,
): Promise<string> {
  const fingerprint = await submissionFingerprint(resumeText, criteria, pack);
  const storageKey = `${STORAGE_PREFIX}${fingerprint}`;
  let existing = inMemoryKeys.get(storageKey) ?? null;
  try {
    existing = window.sessionStorage.getItem(storageKey) ?? existing;
  } catch {
    // Restricted browser storage still gets stable retries within this page.
  }
  if (existing) return existing;

  const key = `hunt-v1-${crypto.randomUUID()}`;
  inMemoryKeys.set(storageKey, key);
  try {
    window.sessionStorage.setItem(storageKey, key);
  } catch {
    // The in-memory fallback preserves the current logical submission.
  }
  return key;
}

export async function consumeHuntIdempotencyKey(
  resumeText: string,
  criteria: JobCriteria,
  pack: string,
): Promise<void> {
  const fingerprint = await submissionFingerprint(resumeText, criteria, pack);
  const storageKey = `${STORAGE_PREFIX}${fingerprint}`;
  inMemoryKeys.delete(storageKey);
  try {
    window.sessionStorage.removeItem(storageKey);
  } catch {
    // Completion must not fail because browser storage is unavailable.
  }
}

export function clearPendingHuntIdempotency(): void {
  inMemoryKeys.clear();
  try {
    for (let index = window.sessionStorage.length - 1; index >= 0; index -= 1) {
      const key = window.sessionStorage.key(index);
      if (key?.startsWith(STORAGE_PREFIX)) window.sessionStorage.removeItem(key);
    }
  } catch {
    // In-memory state is already cleared when browser storage is restricted.
  }
}
