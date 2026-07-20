"use client";

import {
  type FormEvent,
  type ReactNode,
  useEffect,
  useState,
  useSyncExternalStore,
} from "react";

import {
  createAccount,
  getOwnerAccessState,
  signInAccount,
  type OwnerAccessState,
} from "@/lib/session";

type LoginMode = "sign_in" | "create_account";

export default function LoginPage() {
  const [mode, setMode] = useState<LoginMode>("sign_in");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [confirmEmail, setConfirmEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [accessState, setAccessState] = useState<OwnerAccessState | "checking">(
    "checking",
  );
  const [signupEnabled, setSignupEnabled] = useState(false);
  const [connectionAttempt, setConnectionAttempt] = useState(0);
  const workspaceDeleted = useSyncExternalStore(
    subscribeToLocation,
    workspaceDeletedSnapshot,
    () => false,
  );

  useEffect(() => {
    let cancelled = false;
    getOwnerAccessState().then((access) => {
      if (cancelled) return;
      if (access.state === "signed_in") {
        window.location.replace("/");
        return;
      }
      setSignupEnabled(access.signupEnabled);
      setAccessState(access.state);
      if (!access.signupEnabled) setMode("sign_in");
    });
    return () => {
      cancelled = true;
    };
  }, [connectionAttempt]);

  function selectMode(nextMode: LoginMode) {
    setMode(nextMode);
    setError(null);
    setPassword("");
    setConfirmPassword("");
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (mode === "create_account" && normalizedEmail(email) !== normalizedEmail(confirmEmail)) {
      setError("Email addresses do not match.");
      return;
    }
    if (mode === "create_account" && password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    setSubmitting(true);
    try {
      if (mode === "create_account") {
        await createAccount({ displayName, email, password });
        window.location.replace("/profile?welcome=1");
      } else {
        await signInAccount({ email, password });
        window.location.replace("/");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to continue.");
      setPassword("");
      setConfirmPassword("");
      setSubmitting(false);
    }
  }

  const creatingAccount = mode === "create_account";

  return (
    <main className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-6 py-16">
      <p className="text-sm font-medium text-indigo-600 dark:text-indigo-400">
        Job Hunt Signal
      </p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight">
        {creatingAccount ? "Create your job-search account" : "Sign in to your workspace"}
      </h1>
      <p className="mt-3 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
        Each account has its own profile, resumes, searches, applications, and
        outreach history.
      </p>

      {workspaceDeleted ? (
        <div
          role="status"
          className="mt-6 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-100"
        >
          Workspace permanently deleted. Its profile, resumes, searches,
          applications, outreach, and retained legacy runs are no longer available.
        </div>
      ) : null}

      {accessState === "checking" ? (
        <div
          role="status"
          className="mt-8 rounded-lg border border-zinc-200 bg-zinc-50 px-4 py-4 text-sm text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300"
        >
          Checking the account service…
        </div>
      ) : null}

      {accessState === "unavailable" ? (
        <div
          role="alert"
          className="mt-8 rounded-lg border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100"
        >
          <p className="font-medium">Sign-in is temporarily unavailable</p>
          <p className="mt-2 leading-6">
            The website is online, but the job-search service is not responding.
            Your account details have not been submitted.
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
          <p className="font-medium">Account setup is not ready</p>
          <p className="mt-2 leading-6">
            The service is reachable, but its database or account configuration is
            incomplete. Sign-in and account creation cannot work yet.
          </p>
        </div>
      ) : null}

      {accessState === "ready" ? (
        <section className="mt-8 rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm dark:border-zinc-800 dark:bg-zinc-900/70">
          {signupEnabled ? (
            <div
              className="grid grid-cols-2 rounded-xl bg-zinc-100 p-1 dark:bg-zinc-950"
              aria-label="Account access"
            >
              <ModeButton
                selected={!creatingAccount}
                onClick={() => selectMode("sign_in")}
              >
                Sign in
              </ModeButton>
              <ModeButton
                selected={creatingAccount}
                onClick={() => selectMode("create_account")}
              >
                Create account
              </ModeButton>
            </div>
          ) : null}

          <form onSubmit={submit} className={signupEnabled ? "mt-6 space-y-5" : "space-y-5"}>
            {creatingAccount ? (
              <div>
                <label htmlFor="display-name" className="text-sm font-medium">
                  Display name
                </label>
                <input
                  id="display-name"
                  name="display-name"
                  type="text"
                  autoComplete="name"
                  required
                  minLength={1}
                  maxLength={120}
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                  className="mt-2 w-full rounded-lg border border-zinc-300 bg-white px-3 py-2.5 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100 dark:border-zinc-700 dark:bg-zinc-900 dark:focus:ring-indigo-950"
                />
              </div>
            ) : null}

            <div>
              <label htmlFor="email" className="text-sm font-medium">
                Email
              </label>
              <input
                id="email"
                name="email"
                type="email"
                inputMode="email"
                autoComplete="email"
                required
                maxLength={254}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="mt-2 w-full rounded-lg border border-zinc-300 bg-white px-3 py-2.5 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100 dark:border-zinc-700 dark:bg-zinc-900 dark:focus:ring-indigo-950"
              />
            </div>

            {creatingAccount ? (
              <div>
                <label htmlFor="confirm-email" className="text-sm font-medium">
                  Confirm email
                </label>
                <input
                  id="confirm-email"
                  name="confirm-email"
                  type="email"
                  inputMode="email"
                  autoComplete="email"
                  required
                  maxLength={254}
                  value={confirmEmail}
                  onChange={(event) => setConfirmEmail(event.target.value)}
                  className="mt-2 w-full rounded-lg border border-zinc-300 bg-white px-3 py-2.5 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100 dark:border-zinc-700 dark:bg-zinc-900 dark:focus:ring-indigo-950"
                />
              </div>
            ) : null}

            <div>
              <label htmlFor="password" className="text-sm font-medium">
                Password
              </label>
              <input
                id="password"
                name="password"
                type="password"
                autoComplete={creatingAccount ? "new-password" : "current-password"}
                required
                minLength={12}
                maxLength={128}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                aria-describedby={error ? "login-error password-help" : "password-help"}
                className="mt-2 w-full rounded-lg border border-zinc-300 bg-white px-3 py-2.5 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100 dark:border-zinc-700 dark:bg-zinc-900 dark:focus:ring-indigo-950"
              />
              <p id="password-help" className="mt-2 text-xs leading-5 text-zinc-500">
                {creatingAccount
                  ? "Use at least 12 characters and a password unique to this app."
                  : "Password recovery is not available in this beta yet."}
              </p>
            </div>

            {creatingAccount ? (
              <div>
                <label htmlFor="confirm-password" className="text-sm font-medium">
                  Confirm password
                </label>
                <input
                  id="confirm-password"
                  name="confirm-password"
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={12}
                  maxLength={128}
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  className="mt-2 w-full rounded-lg border border-zinc-300 bg-white px-3 py-2.5 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100 dark:border-zinc-700 dark:bg-zinc-900 dark:focus:ring-indigo-950"
                />
              </div>
            ) : null}

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
              className="w-full rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:cursor-wait disabled:opacity-60"
            >
              {submitting
                ? creatingAccount
                  ? "Creating account…"
                  : "Signing in…"
                : creatingAccount
                  ? "Create account"
                  : "Sign in"}
            </button>
          </form>

          {!signupEnabled ? (
            <p className="mt-5 border-t border-zinc-200 pt-4 text-xs leading-5 text-zinc-500 dark:border-zinc-800">
              New account creation is closed on this deployment. Existing accounts
              can still sign in.
            </p>
          ) : null}
        </section>
      ) : null}

      <p className="mt-6 text-center text-xs leading-5 text-zinc-500">
        Local beta: email verification and password recovery are not available yet.
        Account passwords are used only to access this job-search app.
      </p>
    </main>
  );
}

function ModeButton({
  selected,
  onClick,
  children,
}: {
  selected: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={`rounded-lg px-3 py-2 text-sm font-medium transition ${
        selected
          ? "bg-white text-zinc-950 shadow-sm dark:bg-zinc-800 dark:text-white"
          : "text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
      }`}
    >
      {children}
    </button>
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

function normalizedEmail(value: string): string {
  return value.normalize("NFKC").trim().toLocaleLowerCase("en-US");
}
