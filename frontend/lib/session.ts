import { parseOwnerSession, type OwnerSession } from "./api-contract";

export type { OwnerSession } from "./api-contract";

export type OwnerAccessState =
  | "ready"
  | "signed_in"
  | "setup_required"
  | "unavailable";

export async function getOwnerAccessState(): Promise<OwnerAccessState> {
  try {
    const response = await fetch("/api/session", {
      cache: "no-store",
      credentials: "same-origin",
    });
    if (response.ok) return "signed_in";
    if (response.status === 401) return "ready";
    if (response.status === 503) return "setup_required";
    return "unavailable";
  } catch {
    return "unavailable";
  }
}

export async function getOwnerSession(): Promise<OwnerSession | null> {
  const response = await fetch("/api/session", {
    cache: "no-store",
    credentials: "same-origin",
  });
  if (response.status === 401 || response.status === 503) return null;
  if (!response.ok) throw new Error("Unable to check the owner session.");
  return parseOwnerSession(await response.json());
}

export async function createOwnerSession(ownerToken: string): Promise<OwnerSession> {
  const response = await fetch("/api/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ owner_token: ownerToken }),
    credentials: "same-origin",
  });
  if (response.status === 401) {
    throw new Error("That private access key is not valid.");
  }
  if (response.status === 422) {
    throw new Error("That private access key is not valid.");
  }
  if (response.status === 503) {
    throw new Error("Private access has not been configured yet.");
  }
  if (response.status === 502 || response.status === 504) {
    throw new Error("The private job-search service is not online yet.");
  }
  if (!response.ok) throw new Error("Unable to sign in right now.");
  return parseOwnerSession(await response.json());
}

export async function deleteOwnerSession(): Promise<void> {
  const response = await fetch("/api/session", {
    method: "DELETE",
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error("Unable to sign out right now.");
}
