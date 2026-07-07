"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, deleteRun, getRun } from "@/lib/api";
import { clearRunAccess, loadRunAccess } from "@/lib/run-access";
import type { OutreachDraft, RunDetailResponse } from "@/lib/types";
import { RoleCard } from "@/components/role-card";

export function RunReview({ runId }: { runId: string }) {
  const router = useRouter();
  const [detail, setDetail] = useState<RunDetailResponse | null>();
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const token = loadRunAccess(runId);
      if (!token) {
        throw new Error(
          "This browser session does not have the access token for this private run.",
        );
      }
      const loaded = await getRun(runId, token);
      if (!cancelled) {
        setAccessToken(token);
        setDetail(loaded);
      }
    }

    void load().catch((err: unknown) => {
        if (cancelled) return;
        const message =
          err instanceof ApiError && err.status === 401
            ? "The access token for this run is invalid or expired."
            : err instanceof Error
              ? err.message
              : "Unable to load this run.";
        setError(message);
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  async function onDelete() {
    if (!accessToken || !window.confirm("Delete this run and all logged outcomes?")) {
      return;
    }
    setDeleting(true);
    setError(null);
    try {
      await deleteRun(runId, accessToken);
      clearRunAccess(runId);
      router.push("/");
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
      </main>
    );
  }

  if (detail === null) {
    return <PrivateRunError message="This run does not exist or is no longer available." />;
  }

  const { hunt_result } = detail;
  const draftsByRoleUrl = new Map<string, OutreachDraft[]>();
  for (const draft of hunt_result.outreach) {
    const list = draftsByRoleUrl.get(draft.role.url) ?? [];
    list.push(draft);
    draftsByRoleUrl.set(draft.role.url, list);
  }

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">
      <nav className="mb-6 flex flex-wrap items-center justify-between gap-3 text-sm">
        <Link
          href="/"
          className="text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
        >
          ← New hunt
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
          <Link
            href={`/runs/${runId}/outcomes`}
            className="inline-flex h-9 items-center rounded-md bg-indigo-600 px-4 text-xs font-medium text-white hover:bg-indigo-700"
          >
            Log outcomes →
          </Link>
        </div>
      </nav>

      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Hunt review</h1>
        <p className="mt-1 text-sm text-zinc-500">
          {hunt_result.roles.length} role
          {hunt_result.roles.length === 1 ? "" : "s"} •{" "}
          {hunt_result.outreach.length} draft
          {hunt_result.outreach.length === 1 ? "" : "s"}
        </p>
        <p className="mt-1 font-mono text-[11px] text-zinc-400">
          run_id: {hunt_result.run_id}
        </p>
      </header>

      <div className="space-y-6">
        {hunt_result.roles.length === 0 ? (
          <section className="rounded-lg border border-amber-200 bg-amber-50 p-6 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100">
            No roles met every evidence filter. Try a wider location, another
            seniority band, or additional employment types. The agent does not
            pad an empty result with aggregators or stale postings.
          </section>
        ) : (
          hunt_result.roles.map((role) => (
            <RoleCard
              key={role.url}
              role={role}
              drafts={draftsByRoleUrl.get(role.url) ?? []}
            />
          ))
        )}
      </div>
    </main>
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
          href="/"
          className="mt-4 inline-block font-medium underline underline-offset-2"
        >
          Start a new hunt
        </Link>
      </div>
    </main>
  );
}
