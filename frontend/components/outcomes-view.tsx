"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, getRun } from "@/lib/api";
import type { RunDetailResponse } from "@/lib/types";

export function OutcomesView({ runId }: { runId: string }) {
  const [detail, setDetail] = useState<RunDetailResponse | null>();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const loaded = await getRun(runId);
      if (!cancelled) {
        setDetail(loaded);
      }
    }

    void load().catch((err: unknown) => {
      if (cancelled) return;
      setError(
        err instanceof ApiError && err.status === 401
          ? "Your owner session is missing or expired. Sign in again to view this run."
          : err instanceof Error
            ? err.message
            : "Unable to load this run.",
      );
    });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (error) {
    return <OutcomeError message={error} />;
  }

  if (detail === undefined) {
    return (
      <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-12">
        <p role="status" className="text-sm text-zinc-500">
          Loading private run…
        </p>
      </main>
    );
  }

  if (detail === null) {
    return <OutcomeError message="This run does not exist or is no longer available." />;
  }

  if (detail.status !== "succeeded" || !detail.hunt_result) {
    return (
      <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-12">
        <div
          role="alert"
          className="rounded-lg border border-amber-200 bg-amber-50 p-6 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100"
        >
          <p>
            This legacy run has no readable archived outcome record. Current status:{" "}
            <span className="font-mono">{detail.status}</span>.
          </p>
          <Link
            href={`/runs/${runId}`}
            className="mt-4 inline-block font-medium underline underline-offset-2"
          >
            Back to run status
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-10">
      <nav className="mb-6 flex items-center justify-between text-sm">
        <Link
          href={`/runs/${runId}`}
          className="text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
        >
          ← Back to review
        </Link>
        <Link
          href="/hunt"
          className="text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
        >
          Legacy archive
        </Link>
      </nav>

      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Archived outcomes</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Outcome logging is retired with the legacy workflow. Previously saved
          entries remain readable below.
        </p>
        <p className="mt-1 font-mono text-[11px] text-zinc-400">
          run_id: {detail.hunt_result.run_id}
        </p>
      </header>

      <div
        role="status"
        className="mb-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100"
      >
        This page is read-only. Use Applications and Weekly review to track
        current job-search outcomes.
      </div>

      {detail.outcomes.length > 0 ? (
        <section className="rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="text-sm font-semibold">
            Previously logged ({detail.outcomes.length})
          </h2>
          <ul className="mt-3 space-y-2 text-sm">
            {detail.outcomes.map((log, index) => (
              <li
                key={`${log.draft_id}-${log.logged_at ?? index}`}
                className="flex flex-wrap items-start justify-between gap-3 border-b border-zinc-100 pb-2 last:border-0 dark:border-zinc-800"
              >
                <div>
                  <span className="font-medium capitalize">
                    {log.outcome.replaceAll("_", " ")}
                  </span>
                  {log.notes ? <span className="text-zinc-500"> — {log.notes}</span> : null}
                </div>
                <span className="font-mono text-[10px] text-zinc-400">
                  {log.draft_id.slice(0, 8)}
                  {log.logged_at
                    ? ` · ${new Date(log.logged_at).toLocaleString()}`
                    : ""}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : (
        <p className="rounded-lg border border-dashed border-zinc-300 p-5 text-sm text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
          No outcomes were logged before this workflow became read-only.
        </p>
      )}
    </main>
  );
}

function OutcomeError({ message }: { message: string }) {
  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-12">
      <div
        role="alert"
        className="rounded-lg border border-amber-200 bg-amber-50 p-6 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100"
      >
        <p>{message}</p>
        <Link
          href="/today"
          className="mt-4 inline-block font-medium underline underline-offset-2"
        >
          Open the practical workspace
        </Link>
      </div>
    </main>
  );
}
