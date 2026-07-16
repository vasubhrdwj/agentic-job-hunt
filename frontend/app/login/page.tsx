"use client";

import {
  FormEvent,
  useEffect,
  useState,
  useSyncExternalStore,
} from "react";

import {
  createOwnerSession,
  getOwnerAccessState,
  type OwnerAccessState,
} from "@/lib/session";

export default function LoginPage() {
  const [ownerToken, setOwnerToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [accessState, setAccessState] = useState<OwnerAccessState | "checking">(
    "checking",
  );
  const [connectionAttempt, setConnectionAttempt] = useState(0);
  const workspaceDeleted = useSyncExternalStore(
    subscribeToLocation,
    workspaceDeletedSnapshot,
    () => false,
  );

  useEffect(() => {
    let cancelled = false;
    getOwnerAccessState().then((state) => {
      if (cancelled) return;
      if (state === "signed_in") {
        window.location.replace("/");
        return;
      }
      setAccessState(state);
    });
    return () => {
      cancelled = true;
    };
  }, [connectionAttempt]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await createOwnerSession(ownerToken);
      window.location.replace("/");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to sign in.");
      setOwnerToken("");
      setSubmitting(false);
    }
  }

  return (
    <main className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-6 py-16">
      <p className="text-sm font-medium text-zinc-500">Job Hunt Signal</p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight">
        Open your private workspace
      </h1>
      <p className="mt-3 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
        Your resumes, applications, contacts, and provider access are private.
        This page first checks that your personal job-search service is online.
      </p>

      {workspaceDeleted ? (
        <div
          role="status"
          className="mt-6 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-100"
        >
          Workspace permanently deleted. Its private profile, searches, applications,
          outreach, and retained legacy runs are no longer available.
        </div>
      ) : null}

      {accessState === "checking" ? (
        <div
          role="status"
          className="mt-8 rounded-lg border border-zinc-200 bg-zinc-50 px-4 py-4 text-sm text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300"
        >
          Checking your private service…
        </div>
      ) : null}

      {accessState === "unavailable" ? (
        <div
          role="alert"
          className="mt-8 rounded-lg border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100"
        >
          <p className="font-medium">Sign-in is not available yet</p>
          <p className="mt-2 leading-6">
            The website is online, but its private job-search service is not
            connected. No access key can work until that service is deployed.
          </p>
          <button
            type="button"
            onClick={() => {
              setAccessState("checking");
              setConnectionAttempt((value) => value + 1);
            }}
            className="mt-4 rounded-lg border border-amber-300 bg-white px-3 py-2 text-sm font-medium text-amber-950 hover:bg-amber-100 focus:outline-none focus:ring-2 focus:ring-amber-600 dark:border-amber-800 dark:bg-zinc-900 dark:text-amber-100 dark:hover:bg-amber-950"
          >
            Check again
          </button>
        </div>
      ) : null}

      {accessState === "setup_required" ? (
        <div
          role="alert"
          className="mt-8 rounded-lg border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100"
        >
          <p className="font-medium">Private access still needs setup</p>
          <p className="mt-2 leading-6">
            The service is reachable, but its database or private access
            credential has not been configured. Signing in cannot work yet.
          </p>
        </div>
      ) : null}

      {accessState === "ready" ? (
        <form onSubmit={submit} className="mt-8 space-y-5">
          <div>
            <label htmlFor="owner-token" className="text-sm font-medium">
              Private access key
            </label>
            <input
              id="owner-token"
              name="owner-token"
              type="password"
              autoComplete="current-password"
              required
              value={ownerToken}
              onChange={(event) => setOwnerToken(event.target.value)}
              aria-describedby={error ? "login-error" : "owner-token-help"}
              className="mt-2 w-full rounded-lg border border-zinc-300 bg-white px-3 py-2.5 text-sm outline-none focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200 dark:border-zinc-700 dark:bg-zinc-900 dark:focus:border-zinc-500 dark:focus:ring-zinc-800"
            />
            <p id="owner-token-help" className="mt-2 text-xs leading-5 text-zinc-500">
              This is the private key created once during setup—not your GitHub
              or Vercel password. If you were not given one, setup is incomplete.
            </p>
          </div>

          {error ? (
            <p
              id="login-error"
              role="alert"
              className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200"
            >
              {error}
            </p>
          ) : null}

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-lg bg-zinc-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-zinc-700 disabled:cursor-wait disabled:opacity-60 dark:bg-zinc-100 dark:text-zinc-950 dark:hover:bg-white"
          >
            {submitting ? "Opening workspace…" : "Open workspace"}
          </button>
        </form>
      ) : null}

      <p className="mt-6 text-center text-xs text-zinc-500">
        Private access prevents a public visitor from reading your resume,
        applications, contacts, or using your paid provider accounts.
      </p>
    </main>
  );
}

function subscribeToLocation(): () => void {
  return () => undefined;
}

function workspaceDeletedSnapshot(): boolean {
  return (
    new URLSearchParams(window.location.search).get("workspace_deleted") === "1"
  );
}
