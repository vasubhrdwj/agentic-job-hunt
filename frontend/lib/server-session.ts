import { cookies } from "next/headers";

import { parseOwnerSession, type OwnerSession } from "./api-contract";
import { backendBaseUrl } from "./backend-url";

const SESSION_COOKIE_NAME =
  process.env.JOB_HUNT_SESSION_COOKIE?.trim() || "job_hunt_session";

function backendSessionUrl(): URL {
  return new URL("api/session", backendBaseUrl());
}

export async function getServerOwnerSession(): Promise<OwnerSession | null> {
  const token = (await cookies()).get(SESSION_COOKIE_NAME)?.value;
  if (!token) return null;

  try {
    const response = await fetch(backendSessionUrl(), {
      headers: { cookie: `${SESSION_COOKIE_NAME}=${token}` },
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(5_000),
    });
    if (!response.ok) {
      await response.body?.cancel();
      return null;
    }
    return parseOwnerSession(await response.json());
  } catch {
    return null;
  }
}
