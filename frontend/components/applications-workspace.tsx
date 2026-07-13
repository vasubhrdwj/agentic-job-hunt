"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { listApplications } from "@/lib/application-api";
import type {
  ActionItem,
  ApplicationListResponse,
  ApplicationStage,
} from "@/lib/application-types";
import {
  errorText,
  secondaryButtonClasses,
  StatusMessage,
} from "./workspace-ui";

export function ApplicationsWorkspace({ ownerLocalDate }: { ownerLocalDate: string }) {
  const [response, setResponse] = useState<ApplicationListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestInFlight = useRef(false);

  const load = useCallback(async (preserve: boolean) => {
    if (requestInFlight.current) return;
    requestInFlight.current = true;
    if (preserve) setRefreshing(true);
    else setLoading(true);
    setError(null);
    try {
      setResponse(await listApplications());
    } catch (reason) {
      setError(errorText(reason, "Unable to load your applications."));
    } finally {
      requestInFlight.current = false;
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  async function loadMore() {
    if (!response?.next_cursor || requestInFlight.current) return;
    requestInFlight.current = true;
    setLoadingMore(true);
    setError(null);
    try {
      const next = await listApplications(50, response.next_cursor);
      setResponse((current) => {
        if (!current) return next;
        const seen = new Set(current.items.map((item) => item.id));
        return {
          ...next,
          items: [
            ...current.items,
            ...next.items.filter((item) => !seen.has(item.id)),
          ],
        };
      });
    } catch (reason) {
      setError(errorText(reason, "Unable to load older applications."));
    } finally {
      requestInFlight.current = false;
      setLoadingMore(false);
    }
  }

  useEffect(() => {
    const timer = setTimeout(() => void load(false), 0);
    return () => clearTimeout(timer);
  }, [load]);

  if (loading && !response) return <ApplicationsSkeleton />;
  if (!response) {
    return (
      <StatusMessage kind="error">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span>{error ?? "Your application workspace is unavailable."}</span>
          <button
            type="button"
            onClick={() => void load(false)}
            className={secondaryButtonClasses}
          >
            Try again
          </button>
        </div>
      </StatusMessage>
    );
  }

  return (
    <div className="min-w-0 space-y-6" aria-busy={refreshing}>
      <section
        aria-label="Application summary"
        className="grid gap-3 sm:grid-cols-2"
      >
        <div className="rounded-xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900/70">
          <span className="text-3xl font-semibold">{response.total}</span>
          <span className="mt-1 block text-sm text-zinc-500">
            pursued role{response.total === 1 ? "" : "s"}
          </span>
        </div>
        <div className="rounded-xl border border-indigo-200 bg-indigo-50 p-5 dark:border-indigo-900 dark:bg-indigo-950/25">
          <p className="font-semibold text-indigo-950 dark:text-indigo-100">
            One next action per role
          </p>
          <p className="mt-1 text-sm leading-6 text-indigo-900 dark:text-indigo-200">
            Every application below has a dated task, so a promising role cannot quietly disappear.
          </p>
        </div>
      </section>

      {error ? (
        <StatusMessage kind="error">
          {error} Your last loaded applications remain below.
        </StatusMessage>
      ) : null}

      {response.items.length === 0 ? (
        <section className="rounded-2xl border border-dashed border-zinc-300 bg-white p-8 text-center dark:border-zinc-700 dark:bg-zinc-900/50">
          <h2 className="text-lg font-semibold">No applications yet</h2>
          <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
            Review roles in Today and choose Pursue when one deserves focused preparation.
            The role and its first dated action will appear here together.
          </p>
          <Link href="/today" className={`${secondaryButtonClasses} mt-5`}>
            Review opportunities
          </Link>
        </section>
      ) : (
        <section aria-label="Applications" className="space-y-4">
          {response.items.map((application) => (
            <article
              key={application.id}
              aria-labelledby={`application-title-${application.id}`}
              className="min-w-0 rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm sm:p-6 dark:border-zinc-800 dark:bg-zinc-900/70"
            >
              <div className="flex min-w-0 flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <ApplicationStageBadge stage={application.stage} />
                    <span className="text-xs text-zinc-500">
                      {application.posting.company}
                    </span>
                  </div>
                  <h2
                    id={`application-title-${application.id}`}
                    className="mt-3 break-words text-xl font-semibold tracking-tight"
                  >
                    {application.posting.title}
                  </h2>
                  {application.posting.state !== "open" ? (
                    <p className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900 dark:border-red-900 dark:bg-red-950/30 dark:text-red-100">
                      Posting is {application.posting.state}. Verify availability before spending more time on it.
                    </p>
                  ) : null}
                  <div className="mt-4 rounded-xl bg-zinc-50 p-4 dark:bg-zinc-950/60">
                    <p className="text-xs font-semibold uppercase tracking-[0.12em] text-zinc-500">
                      Next action
                    </p>
                    <p className="mt-1 break-words text-sm font-medium">
                      {application.current_action.title}
                    </p>
                    <DueDate
                      action={application.current_action}
                      ownerLocalDate={ownerLocalDate}
                      className="mt-2"
                    />
                  </div>
                </div>
                <Link
                  href={`/applications/${encodeURIComponent(application.id)}`}
                  className={secondaryButtonClasses}
                  aria-label={`Open application for ${application.posting.title} at ${application.posting.company}`}
                >
                  Open application
                </Link>
              </div>
            </article>
          ))}
        </section>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-zinc-200 pt-4 text-xs text-zinc-500 dark:border-zinc-800">
        <span>Showing {response.items.length} of {response.total} persisted applications</span>
        <div className="flex flex-wrap items-center gap-4">
          {response.next_cursor ? (
            <button
              type="button"
              disabled={loadingMore || refreshing}
              onClick={() => void loadMore()}
              className="font-medium underline underline-offset-4 disabled:opacity-50"
            >
              {loadingMore ? "Loading…" : "Load older applications"}
            </button>
          ) : null}
          <button
            type="button"
            disabled={refreshing || loadingMore}
            onClick={() => void load(true)}
            className="font-medium underline underline-offset-4 disabled:opacity-50"
          >
            {refreshing ? "Refreshing…" : "Refresh applications"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function ApplicationStageBadge({ stage }: { stage: ApplicationStage }) {
  return (
    <span className="rounded-full bg-indigo-50 px-2.5 py-1 text-xs font-semibold text-indigo-800 dark:bg-indigo-950 dark:text-indigo-200">
      {stage === "pursuing" ? "Pursuing" : stage}
    </span>
  );
}

export function DueDate({
  action,
  ownerLocalDate,
  className = "",
}: {
  action: ActionItem;
  ownerLocalDate: string;
  className?: string;
}) {
  const state = dueState(action.due_on, ownerLocalDate);
  const tone = state === "overdue"
    ? "text-red-700 dark:text-red-300"
    : state === "today"
      ? "text-amber-700 dark:text-amber-300"
      : "text-zinc-600 dark:text-zinc-400";
  const prefix = state === "overdue" ? "Overdue" : state === "today" ? "Due today" : "Due";
  return (
    <p className={`text-xs font-medium ${tone} ${className}`.trim()}>
      {prefix}{state === "today" ? "" : ` ${formatDateOnly(action.due_on)}`}
    </p>
  );
}

function ApplicationsSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading applications" className="space-y-5">
      <p role="status" className="text-sm text-zinc-500">Loading your applications…</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {[0, 1].map((item) => (
          <div key={item} className="h-28 animate-pulse rounded-xl bg-zinc-200 dark:bg-zinc-800" />
        ))}
      </div>
      {[0, 1, 2].map((item) => (
        <div key={item} className="h-48 animate-pulse rounded-2xl bg-zinc-200 dark:bg-zinc-800" />
      ))}
    </div>
  );
}

function dueState(
  value: string,
  ownerLocalDate: string,
): "overdue" | "today" | "future" {
  if (value < ownerLocalDate) return "overdue";
  if (value === ownerLocalDate) return "today";
  return "future";
}

function formatDateOnly(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(year, month - 1, day, 12);
  return Number.isNaN(date.getTime())
    ? "on an unknown date"
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}
