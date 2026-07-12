"use client";

import { FormEvent, useState } from "react";

import { createOwnerSession } from "@/lib/session";

export default function LoginPage() {
  const [ownerToken, setOwnerToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

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
        Enter the private owner token configured on your backend. It is exchanged
        for an HttpOnly session and is never stored in this browser form.
      </p>

      <form onSubmit={submit} className="mt-8 space-y-5">
        <div>
          <label htmlFor="owner-token" className="text-sm font-medium">
            Owner token
          </label>
          <input
            id="owner-token"
            name="owner-token"
            type="password"
            autoComplete="current-password"
            minLength={32}
            required
            value={ownerToken}
            onChange={(event) => setOwnerToken(event.target.value)}
            aria-describedby={error ? "login-error" : "owner-token-help"}
            className="mt-2 w-full rounded-lg border border-zinc-300 bg-white px-3 py-2.5 text-sm outline-none focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200 dark:border-zinc-700 dark:bg-zinc-900 dark:focus:border-zinc-500 dark:focus:ring-zinc-800"
          />
          <p id="owner-token-help" className="mt-2 text-xs text-zinc-500">
            Use the high-entropy token whose SHA-256 hash is configured as
            JOB_HUNT_OWNER_TOKEN_HASH.
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

      <p className="mt-6 text-center text-xs text-zinc-500">
        This workspace is private. Job discovery and provider calls stay locked
        until the owner session is active.
      </p>
    </main>
  );
}
