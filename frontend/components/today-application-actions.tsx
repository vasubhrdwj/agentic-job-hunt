"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { getTodayApplicationActions } from "@/lib/application-api";
import type {
  TodayApplicationActionGroup,
  TodayApplicationActionItem,
  TodayApplicationActionsResponse,
} from "@/lib/application-types";
import { ApplicationStageBadge, DueDate } from "./applications-workspace";
import {
  errorText,
  secondaryButtonClasses,
  StatusMessage,
} from "./workspace-ui";

const UPCOMING_PREVIEW_SIZE = 3;

export function TodayApplicationActions() {
  const [response, setResponse] = useState<TodayApplicationActionsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAllUpcoming, setShowAllUpcoming] = useState(false);

  const load = useCallback(async (preserve: boolean) => {
    if (preserve) setRefreshing(true);
    else setLoading(true);
    setError(null);
    try {
      setResponse(await getTodayApplicationActions());
    } catch (reason) {
      setError(errorText(reason, "Unable to load your application actions."));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => void load(false), 0);
    return () => clearTimeout(timer);
  }, [load]);

  if (loading && !response) return <ApplicationActionsSkeleton />;
  if (!response) {
    return (
      <StatusMessage kind="error">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span>{error ?? "Your application actions are unavailable."}</span>
          <button type="button" onClick={() => void load(false)} className={secondaryButtonClasses}>
            Try again
          </button>
        </div>
      </StatusMessage>
    );
  }

  const total = response.overdue.total + response.today.total + response.next_7_days.total;
  return (
    <section
      aria-labelledby="application-actions-title"
      aria-busy={refreshing}
      className="overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900/70"
    >
      <div className="p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-indigo-600 dark:text-indigo-400">
              Do first
            </p>
            <h2 id="application-actions-title" className="mt-2 text-xl font-semibold tracking-tight">
              Application actions
            </h2>
            <p className="mt-2 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
              Work the dated next step for each active application before reviewing more roles.
            </p>
          </div>
          <Link href="/applications" className={secondaryButtonClasses}>
            All applications
          </Link>
        </div>

        <p className="mt-4 flex flex-wrap gap-x-4 gap-y-1 text-sm font-medium" aria-label="Application action counts">
          <span className={response.overdue.total > 0 ? "text-red-700 dark:text-red-300" : "text-zinc-500"}>
            {response.overdue.total} overdue
          </span>
          <span className={response.today.total > 0 ? "text-amber-700 dark:text-amber-300" : "text-zinc-500"}>
            {response.today.total} due today
          </span>
          <span className="text-zinc-600 dark:text-zinc-400">
            {response.next_7_days.total} next 7 days
          </span>
        </p>

        {error ? (
          <div className="mt-4">
            <StatusMessage kind="error">{error} Your last loaded actions remain below.</StatusMessage>
          </div>
        ) : null}

        {total === 0 ? (
          <div className="mt-5 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-950 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100">
            No application action is overdue or due through {formatDateOnly(response.window_ends_on)}.
            Later actions, if any, remain in Applications.
          </div>
        ) : (
          <div className="mt-5 divide-y divide-zinc-200 border-y border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
            <ActionGroup
              heading="Overdue"
              group={response.overdue}
              ownerLocalDate={response.owner_local_date}
            />
            <ActionGroup
              heading="Due today"
              group={response.today}
              ownerLocalDate={response.owner_local_date}
            />
            <ActionGroup
              heading="Next 7 days"
              group={response.next_7_days}
              ownerLocalDate={response.owner_local_date}
              previewCount={UPCOMING_PREVIEW_SIZE}
              expanded={showAllUpcoming}
              onToggle={() => setShowAllUpcoming((current) => !current)}
              listId="next-seven-day-application-actions"
            />
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-xs text-zinc-500">
          <span>
            Saved actions as of {formatTimestamp(response.as_of, response.owner_timezone)} · {response.owner_timezone}
          </span>
          <button
            type="button"
            disabled={refreshing}
            onClick={() => void load(true)}
            className="font-medium underline underline-offset-4 disabled:opacity-50"
          >
            {refreshing ? "Refreshing…" : "Refresh actions"}
          </button>
        </div>
      </div>
    </section>
  );
}

function ActionGroup({
  heading,
  group,
  ownerLocalDate,
  previewCount,
  expanded = false,
  onToggle,
  listId,
}: {
  heading: string;
  group: TodayApplicationActionGroup;
  ownerLocalDate: string;
  previewCount?: number;
  expanded?: boolean;
  onToggle?: () => void;
  listId?: string;
}) {
  if (group.total === 0) return null;
  const canToggle = previewCount !== undefined && group.items.length > previewCount;
  const visibleItems = canToggle && !expanded
    ? group.items.slice(0, previewCount)
    : group.items;
  const hiddenReturnedCount = previewCount === undefined
    ? 0
    : Math.max(0, group.items.length - previewCount);
  const unreturnedCount = Math.max(0, group.total - group.items.length);
  return (
    <section aria-label={`${heading} application actions`} className="py-4 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{heading}</h3>
        <span className="text-xs text-zinc-500">{group.total} action{group.total === 1 ? "" : "s"}</span>
      </div>
      {visibleItems.length > 0 ? (
        <ul id={listId} className="mt-2 divide-y divide-zinc-100 dark:divide-zinc-800/80">
          {visibleItems.map((item) => (
            <ActionRow
              key={item.action.id}
              item={item}
              ownerLocalDate={ownerLocalDate}
            />
          ))}
        </ul>
      ) : null}
      {canToggle && onToggle && listId ? (
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          aria-controls={listId}
          className="mt-3 text-sm font-medium text-indigo-700 underline underline-offset-4 dark:text-indigo-300"
        >
          {expanded
            ? "Show fewer upcoming actions"
            : `Show ${hiddenReturnedCount} more upcoming action${hiddenReturnedCount === 1 ? "" : "s"}`}
        </button>
      ) : null}
      {unreturnedCount > 0 ? (
        <p className="mt-3 text-xs leading-5 text-zinc-600 dark:text-zinc-400">
          {unreturnedCount} more {heading.toLowerCase()} action{unreturnedCount === 1 ? " is" : "s are"} not shown in this Today view.{" "}
          <Link href="/applications" className="font-medium underline underline-offset-4">
            Find {unreturnedCount === 1 ? "it" : "them"} in Applications
          </Link>
          .
        </p>
      ) : null}
    </section>
  );
}

function ActionRow({
  item,
  ownerLocalDate,
}: {
  item: TodayApplicationActionItem;
  ownerLocalDate: string;
}) {
  return (
    <li className="flex min-w-0 flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <ApplicationStageBadge stage={item.application.stage} />
          <DueDate action={item.action} ownerLocalDate={ownerLocalDate} />
        </div>
        <p className="mt-2 break-words text-sm font-semibold">{item.action.title}</p>
        <p className="mt-1 break-words text-sm text-zinc-600 dark:text-zinc-400">
          {item.posting.company} · {item.posting.title}
        </p>
      </div>
      <Link
        href={`/applications/${encodeURIComponent(item.application.id)}`}
        className={`${secondaryButtonClasses} shrink-0`}
        aria-label={`Open ${item.action.title} for ${item.posting.title} at ${item.posting.company}`}
      >
        Open task
      </Link>
    </li>
  );
}

function ApplicationActionsSkeleton() {
  return (
    <section aria-busy="true" aria-label="Loading application actions" className="rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm sm:p-6 dark:border-zinc-800 dark:bg-zinc-900/70">
      <p role="status" className="text-sm text-zinc-500">Loading your dated application actions…</p>
      <div className="mt-4 h-20 animate-pulse rounded-xl bg-zinc-100 dark:bg-zinc-800" />
    </section>
  );
}

function formatDateOnly(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(year, month - 1, day, 12);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}

function formatTimestamp(value: string, timeZone: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone,
    }).format(date);
  } catch {
    return value;
  }
}
