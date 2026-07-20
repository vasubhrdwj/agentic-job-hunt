import {
  parseOwnerSession,
  parseSessionStatus,
  type OwnerSession,
} from "./api-contract";

export type { OwnerSession } from "./api-contract";

export type OwnerAccessState =
  | "ready"
  | "signed_in"
  | "setup_required"
  | "unavailable";

export type OwnerAccess = {
  state: OwnerAccessState;
  signupEnabled: boolean;
};

type AccountCredentials = {
  email: string;
  password: string;
};

type AccountSignup = AccountCredentials & {
  displayName: string;
};

export async function getOwnerAccessState(): Promise<OwnerAccess> {
  try {
    const statusResponse = await fetch("/api/session/status", {
      cache: "no-store",
      credentials: "same-origin",
    });
    if (!statusResponse.ok) return unavailableAccess();
    const statusBody = parseSessionStatus(await statusResponse.json());
    if (statusBody.state === "setup_required") {
      return {
        state: "setup_required",
        signupEnabled: statusBody.signup_enabled,
      };
    }

    const sessionResponse = await fetch("/api/session", {
      cache: "no-store",
      credentials: "same-origin",
    });
    if (sessionResponse.ok) {
      return { state: "signed_in", signupEnabled: statusBody.signup_enabled };
    }
    if (sessionResponse.status === 401) {
      return { state: "ready", signupEnabled: statusBody.signup_enabled };
    }
    if (sessionResponse.status === 503) {
      return {
        state: "setup_required",
        signupEnabled: statusBody.signup_enabled,
      };
    }
    return unavailableAccess();
  } catch {
    return unavailableAccess();
  }
}

export async function getOwnerSession(): Promise<OwnerSession | null> {
  const response = await fetch("/api/session", {
    cache: "no-store",
    credentials: "same-origin",
  });
  if (response.status === 401 || response.status === 503) return null;
  if (!response.ok) throw new Error("Unable to check your session.");
  return parseOwnerSession(await response.json());
}

export async function signInAccount({
  email,
  password,
}: AccountCredentials): Promise<OwnerSession> {
  const response = await fetch("/api/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: email.trim(), password }),
    credentials: "same-origin",
  });
  if (response.status === 401) {
    throw new Error("Email or password is incorrect.");
  }
  if (response.status === 422) {
    throw new Error("Enter a valid email address and password.");
  }
  if (response.status === 429) {
    throw new Error("Sign-in is busy or temporarily limited. Wait briefly and try again.");
  }
  if (response.status === 503) {
    throw new Error("Account sign-in has not been configured yet.");
  }
  if (response.status === 502 || response.status === 504) {
    throw new Error("The job-search service is not online yet.");
  }
  if (!response.ok) throw new Error("Unable to sign in right now.");
  return parseOwnerSession(await response.json());
}

export async function createAccount({
  displayName,
  email,
  password,
}: AccountSignup): Promise<OwnerSession> {
  const response = await fetch("/api/accounts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      display_name: displayName.trim(),
      email: email.trim(),
      password,
      timezone: browserTimezone(),
    }),
    credentials: "same-origin",
  });
  if (response.status === 403) {
    throw new Error("New account creation is currently closed.");
  }
  if (response.status === 409) {
    throw new Error("An account already uses that email. Sign in instead.");
  }
  if (response.status === 422) {
    throw new Error(
      "Check your name and email, and use a password with at least 12 characters.",
    );
  }
  if (response.status === 429) {
    throw new Error("Account creation is busy or temporarily limited. Wait and try again.");
  }
  if (response.status === 503) {
    throw new Error("Account creation has not been configured yet.");
  }
  if (response.status === 502 || response.status === 504) {
    throw new Error("The job-search service is not online yet.");
  }
  if (!response.ok) throw new Error("Unable to create your account right now.");
  return parseOwnerSession(await response.json());
}

export async function claimWorkspaceAccount({
  email,
  password,
}: AccountCredentials): Promise<OwnerSession> {
  const response = await fetch("/api/accounts/claim", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: email.trim(), password }),
    credentials: "same-origin",
  });
  if (response.status === 401) {
    throw new Error("Your session expired. Sign in again and retry.");
  }
  if (response.status === 409) {
    throw new Error(
      "This workspace is already secured or that email belongs to another account.",
    );
  }
  if (response.status === 422) {
    throw new Error(
      "Enter a valid email and use a password with at least 12 characters.",
    );
  }
  if (response.status === 429) {
    throw new Error("Account security is busy. Wait briefly and try again.");
  }
  if (response.status === 502 || response.status === 503 || response.status === 504) {
    throw new Error("The job-search service is temporarily unavailable.");
  }
  if (!response.ok) throw new Error("Unable to secure this workspace right now.");
  return parseOwnerSession(await response.json());
}

export async function deleteOwnerSession(): Promise<void> {
  const response = await fetch("/api/session", {
    method: "DELETE",
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error("Unable to sign out right now.");
}

function unavailableAccess(): OwnerAccess {
  return { state: "unavailable", signupEnabled: false };
}

function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone?.trim() || "UTC";
  } catch {
    return "UTC";
  }
}
