"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, getRun } from "@/lib/api";
import type { RunDetailResponse } from "@/lib/types";
import { OutcomeForm } from "@/components/outcome-form";

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
            Outcomes can only be logged after a hunt succeeds. Current status:{" "}
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
          href="/"
          className="text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
        >
          New hunt
        </Link>
      </nav>

      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Log outcomes</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Record what happened with each draft. Submit appends to the log;
          nothing overwrites.
        </p>
        <p className="mt-1 font-mono text-[11px] text-zinc-400">
          run_id: {detail.hunt_result.run_id}
        </p>
      </header>

      <OutcomeForm
        runId={runId}
        huntResult={detail.hunt_result}
        previousOutcomes={detail.outcomes}
      />
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
          href="/"
          className="mt-4 inline-block font-medium underline underline-offset-2"
        >
          Start a new hunt
        </Link>
      </div>
    </main>
  );
}
