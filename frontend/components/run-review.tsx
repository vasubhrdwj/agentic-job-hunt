"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { ApiError, deleteRun, getRun } from "@/lib/api";
import type { OutreachDraft, RunDetailResponse } from "@/lib/types";
import { HuntProgress } from "@/components/hunt-progress";
import { RoleCard } from "@/components/role-card";

const ACTIVE_STATUSES = new Set(["queued", "running"]);
const POLL_MS = 2_000;
const MAX_POLL_RETRIES = 5;
const MAX_POLL_BACKOFF_MS = 16_000;

function isTransientPollingError(error: unknown): boolean {
  if (error instanceof ApiError) {
    return (
      error.retryable ||
      error.status === 408 ||
      error.status === 425
    );
  }
  return error instanceof TypeError;
}

function pollingBackoff(failureCount: number): number {
  return Math.min(POLL_MS * 2 ** (failureCount - 1), MAX_POLL_BACKOFF_MS);
}

export function RunReview({ runId }: { runId: string }) {
  const router = useRouter();
  const [detail, setDetail] = useState<RunDetailResponse | null>();
  const [error, setError] = useState<string | null>(null);
  const [pollingWarning, setPollingWarning] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let consecutiveFailures = 0;
    let hasLastGoodDetail = false;

    function schedule(delay: number) {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => void load(), delay);
    }

    async function load() {
      try {
        const loaded = await getRun(runId);
        if (cancelled) return;
        consecutiveFailures = 0;
        hasLastGoodDetail = loaded !== null;
        setError(null);
        setPollingWarning(null);
        setDetail(loaded);
        if (loaded && ACTIVE_STATUSES.has(loaded.status)) schedule(POLL_MS);
      } catch (reason) {
        handleLoadError(reason);
      }
    }

    function handleLoadError(err: unknown) {
      if (cancelled) return;
      if (isTransientPollingError(err)) {
        consecutiveFailures += 1;
        if (consecutiveFailures <= MAX_POLL_RETRIES) {
          const delay = pollingBackoff(consecutiveFailures);
          setPollingWarning(
            `Live update interrupted. Retrying in ${Math.ceil(delay / 1_000)} seconds…`,
          );
          schedule(delay);
          return;
        }
        if (hasLastGoodDetail) {
          setPollingWarning(
            "Live updates are paused after repeated connection failures. The last confirmed run state is shown; reload to try again.",
          );
          return;
        }
      }
      const message =
        err instanceof ApiError && err.status === 401
          ? "Your owner session is missing or expired. Sign in again to view this run."
          : err instanceof Error
            ? err.message
            : "Unable to load this run.";
      setError(message);
    }

    void load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [runId]);

  async function onDelete() {
    if (!window.confirm("Delete this run and all logged outcomes?")) {
      return;
    }
    setDeleting(true);
    setError(null);
    try {
      await deleteRun(runId);
      router.push("/hunt");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to delete this run.");
      setDeleting(false);
    }
  }

  if (error) {
    return <PrivateRunError message={error} />;
  }

  if (detail === undefined) {
    return (
      <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-12">
        <p role="status" className="text-sm text-zinc-500">
          Loading private run…
        </p>
        {pollingWarning && (
          <p className="mt-3 text-sm text-amber-700 dark:text-amber-300">
            {pollingWarning}
          </p>
        )}
      </main>
    );
  }

  if (detail === null) {
    return <PrivateRunError message="This run does not exist or is no longer available." />;
  }

  if (ACTIVE_STATUSES.has(detail.status)) {
    return (
      <RunShell
        deleting={deleting}
        pollingWarning={pollingWarning}
        onDelete={onDelete}
      >
        <HuntProgress />
        <p className="mt-4 text-sm text-zinc-500">
          Status: <span className="font-medium">{detail.status}</span> · stage:{" "}
          <span className="font-mono text-xs">{detail.stage}</span> · attempt{" "}
          {detail.attempt_count}/{detail.max_attempts}
        </p>
      </RunShell>
    );
  }

  if (detail.status !== "succeeded") {
    return (
      <RunShell
        deleting={deleting}
        pollingWarning={pollingWarning}
        onDelete={onDelete}
      >
        <TerminalRunState detail={detail} />
      </RunShell>
    );
  }

  const huntResult = detail.hunt_result;
  if (!huntResult) {
    return <PrivateRunError message="This run succeeded but its result is unavailable." />;
  }

  const draftsByRoleUrl = new Map<string, OutreachDraft[]>();
  for (const draft of huntResult.outreach) {
    const list = draftsByRoleUrl.get(draft.role.url) ?? [];
    list.push(draft);
    draftsByRoleUrl.set(draft.role.url, list);
  }

  return (
    <RunShell
      deleting={deleting}
      pollingWarning={pollingWarning}
      onDelete={onDelete}
    >
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Hunt review</h1>
        <p className="mt-1 text-sm text-zinc-500">
          {huntResult.roles.length} role
          {huntResult.roles.length === 1 ? "" : "s"} • {huntResult.outreach.length}{" "}
          draft{huntResult.outreach.length === 1 ? "" : "s"}
        </p>
        <p className="mt-1 font-mono text-[11px] text-zinc-400">
          run_id: {huntResult.run_id}
        </p>
        {detail.outcomes.length > 0 ? (
          <Link
            href={`/runs/${runId}/outcomes`}
            className="mt-4 inline-flex min-h-10 items-center justify-center rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm font-medium text-zinc-800 transition hover:border-zinc-500 hover:bg-zinc-50 focus:outline-none focus:ring-2 focus:ring-zinc-300 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:border-zinc-500 dark:hover:bg-zinc-800"
          >
            View archived outcomes ({detail.outcomes.length}) →
          </Link>
        ) : null}
      </header>

      <div className="space-y-6">
        {huntResult.roles.length === 0 ? (
          <section className="rounded-lg border border-amber-200 bg-amber-50 p-6 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100">
            No roles met every evidence filter. Try a wider location, another
            seniority band, or additional employment types. The agent does not
            pad an empty result with aggregators or stale postings.
          </section>
        ) : (
          huntResult.roles.map((role) => (
            <RoleCard
              key={role.url}
              role={role}
              drafts={draftsByRoleUrl.get(role.url) ?? []}
            />
          ))
        )}
      </div>
    </RunShell>
  );
}

function RunShell({
  deleting,
  pollingWarning,
  onDelete,
  children,
}: {
  deleting: boolean;
  pollingWarning: string | null;
  onDelete: () => void;
  children: ReactNode;
}) {
  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">
      <nav className="mb-6 flex flex-wrap items-center justify-between gap-3 text-sm">
        <Link
          href="/hunt"
          className="text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
        >
          ← Legacy archive
        </Link>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onDelete}
            disabled={deleting}
            className="inline-flex h-9 items-center rounded-md border border-red-300 px-4 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-60 dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950"
          >
            {deleting ? "Deleting…" : "Delete run"}
          </button>
        </div>
      </nav>
      <div
        role="status"
        className="mb-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100"
      >
        Archived legacy run · read-only. You can review, copy, or permanently
        delete it, but new provider work and outcome logging are retired.
      </div>
      {pollingWarning && (
        <div
          role="status"
          className="mb-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100"
        >
          {pollingWarning}
        </div>
      )}
      {children}
    </main>
  );
}

function TerminalRunState({ detail }: { detail: RunDetailResponse }) {
  const title =
    detail.status === "cancelled"
      ? "Run cancelled"
      : detail.status === "dead_letter"
        ? "Run needs operator attention"
        : "Run failed";
  const body =
    detail.status === "dead_letter"
      ? "The worker retried this hunt up to the configured limit and stopped to avoid unbounded provider work. The retained failure remains available for inspection but cannot be requeued from the retired workflow."
      : detail.status === "cancelled"
        ? "This run was cancelled before a result was committed. Use Saved searches for new role discovery."
        : "The worker could not complete this hunt. No outreach outcomes can be logged for a failed run.";

  return (
    <section
      role="alert"
      className="rounded-lg border border-amber-200 bg-amber-50 p-6 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100"
    >
      <h1 className="text-lg font-semibold">{title}</h1>
      <p className="mt-2">{body}</p>
      <dl className="mt-4 grid gap-2 text-xs sm:grid-cols-2">
        <div>
          <dt className="font-medium">Status</dt>
          <dd className="font-mono">{detail.status}</dd>
        </div>
        <div>
          <dt className="font-medium">Stage</dt>
          <dd className="font-mono">{detail.stage}</dd>
        </div>
        <div>
          <dt className="font-medium">Attempts</dt>
          <dd className="font-mono">
            {detail.attempt_count}/{detail.max_attempts}
          </dd>
        </div>
        {detail.last_error && (
          <div>
            <dt className="font-medium">Last error</dt>
            <dd className="font-mono">{detail.last_error}</dd>
          </div>
        )}
      </dl>
    </section>
  );
}

function PrivateRunError({ message }: { message: string }) {
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
