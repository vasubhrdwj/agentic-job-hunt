"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { getApplication } from "@/lib/application-api";
import type { ApplicationDetailResponse } from "@/lib/application-types";
import { ApplicationPack } from "./application-pack";
import { ApplicationPeople } from "./application-people";
import { ApplicationStageBadge, DueDate } from "./applications-workspace";
import {
  errorText,
  formatDate,
  secondaryButtonClasses,
  StatusMessage,
} from "./workspace-ui";

export function ApplicationDossier({
  applicationId,
  ownerLocalDate,
}: {
  applicationId: string;
  ownerLocalDate: string;
}) {
  const [detail, setDetail] = useState<ApplicationDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setDetail(await getApplication(applicationId));
    } catch (reason) {
      setError(errorText(reason, "Unable to load this application."));
    } finally {
      setLoading(false);
    }
  }, [applicationId]);

  useEffect(() => {
    const timer = setTimeout(() => void load(), 0);
    return () => clearTimeout(timer);
  }, [load]);

  if (loading) {
    return <p role="status" className="text-sm text-zinc-500">Loading application dossier…</p>;
  }
  if (!detail) {
    return (
      <StatusMessage kind="error">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span>{error ?? "This application is unavailable."}</span>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void load()}
              className={secondaryButtonClasses}
            >
              Try again
            </button>
            <Link href="/applications" className={secondaryButtonClasses}>
              Back to applications
            </Link>
          </div>
        </div>
      </StatusMessage>
    );
  }

  const application = detail.application;
  const posting = application.posting;
  return (
    <div className="min-w-0 space-y-6">
      <Link
        href="/applications"
        className="inline-flex min-h-10 items-center text-sm font-medium text-zinc-600 hover:text-zinc-950 dark:text-zinc-400 dark:hover:text-white"
      >
        ← Back to applications
      </Link>
      {error ? <StatusMessage kind="error">{error}</StatusMessage> : null}

      <article
        aria-labelledby="application-dossier-title"
        className="min-w-0 rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm sm:p-7 dark:border-zinc-800 dark:bg-zinc-900/70"
      >
        <div className="flex min-w-0 flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <ApplicationStageBadge stage={application.stage} />
              <span className="text-xs text-zinc-500">{posting.company}</span>
            </div>
            <h2
              id="application-dossier-title"
              className="mt-3 break-words text-2xl font-semibold tracking-tight"
            >
              {posting.title}
            </h2>
            <p className="mt-2 text-sm text-zinc-500">
              Started {formatDate(application.created_at)}
            </p>
          </div>
          <a
            href={posting.canonical_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex min-h-11 shrink-0 items-center justify-center rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
          >
            {posting.first_party ? "Open first-party posting ↗" : "Open source posting ↗"}
          </a>
        </div>
        {!posting.first_party ? (
          <p className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
            This destination has not been verified as first-party. Review it before entering personal information.
          </p>
        ) : null}
        {posting.state !== "open" ? (
          <p className="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900 dark:border-red-900 dark:bg-red-950/30 dark:text-red-100">
            This posting is now {posting.state}. Verify availability before spending more time on it.
          </p>
        ) : null}
      </article>

      <section
        aria-labelledby="next-action-title"
        className="rounded-2xl border border-indigo-200 bg-indigo-50 p-5 sm:p-7 dark:border-indigo-900 dark:bg-indigo-950/25"
      >
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-indigo-700 dark:text-indigo-300">
          Do next
        </p>
        <h2 id="next-action-title" className="mt-2 text-xl font-semibold text-indigo-950 dark:text-indigo-100">
          {application.current_action.title}
        </h2>
        <DueDate
          action={application.current_action}
          ownerLocalDate={ownerLocalDate}
          className="mt-3"
        />
        <p className="mt-4 max-w-2xl text-sm leading-6 text-indigo-900 dark:text-indigo-200">
          Review the current posting, identify the strongest evidence from your experience,
          and decide what must be tailored before applying.
        </p>
      </section>

      <ApplicationPack
        key={`pack:${application.id}`}
        applicationId={application.id}
        applicationVersion={application.version}
      />

      <ApplicationPeople
        key={application.id}
        applicationId={application.id}
        applicationVersion={application.version}
        postingState={posting.state}
      />

      <section className="rounded-2xl border border-zinc-200 bg-white p-5 sm:p-6 dark:border-zinc-800 dark:bg-zinc-900/70">
        <h2 className="font-semibold">Role record</h2>
        <dl className="mt-4 grid gap-4 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-xs font-medium uppercase tracking-wide text-zinc-500">Company</dt>
            <dd className="mt-1 break-words font-medium">{posting.company}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase tracking-wide text-zinc-500">Posting status</dt>
            <dd className="mt-1 capitalize">{posting.state}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase tracking-wide text-zinc-500">Captured version</dt>
            <dd className="mt-1 break-all font-mono text-xs">{application.pursued_posting_version_id}</dd>
          </div>
        </dl>
        <Link
          href={`/jobs/${encodeURIComponent(application.opportunity_id)}`}
          className={`${secondaryButtonClasses} mt-5`}
        >
          Review saved opportunity
        </Link>
      </section>

      <section className="rounded-2xl border border-zinc-200 bg-white p-5 sm:p-7 dark:border-zinc-800 dark:bg-zinc-900/70">
        <h2 className="text-lg font-semibold">Activity</h2>
        <p className="mt-1 text-sm text-zinc-500">A durable, chronological record of this application.</p>
        <ol className="mt-5 space-y-4">
          {detail.activity.map((event) => (
            <li key={event.id} className="flex min-w-0 gap-3">
              <span aria-hidden="true" className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full bg-indigo-500" />
              <div className="min-w-0">
                <p className="font-medium">Application started</p>
                <p className="mt-1 text-sm text-zinc-500">
                  Entered Pursuing · {formatDate(event.occurred_at)}
                </p>
              </div>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
