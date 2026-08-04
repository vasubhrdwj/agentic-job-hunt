"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import type { DailyDigestResponse } from "@/lib/daily-digest-types";

export function TodayDailyDigest() {
  const [digest, setDigest] = useState<DailyDigestResponse | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/today/digest", {
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("digest request failed");
        setDigest(await response.json() as DailyDigestResponse);
      })
      .catch((cause: unknown) => {
        if (!(cause instanceof DOMException && cause.name === "AbortError")) setError(true);
      });
    return () => controller.abort();
  }, []);

  if (error) {
    return (
      <section className="rounded-2xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-950" aria-label="Daily job digest">
        <p className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Daily digest unavailable</p>
        <p className="mt-1 text-sm text-zinc-500">Your saved roles remain available below. Refresh to retry this summary.</p>
      </section>
    );
  }
  if (!digest) {
    return (
      <section className="rounded-2xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-950" aria-label="Daily job digest">
        <p role="status" className="text-sm text-zinc-500">Preparing today&apos;s digest…</p>
      </section>
    );
  }

  return (
    <section className="rounded-2xl border border-violet-200 bg-violet-50/70 p-5 dark:border-violet-900 dark:bg-violet-950/30" aria-labelledby="daily-digest-title">
      <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-violet-700 dark:text-violet-300">Daily signal</p>
          <h2 id="daily-digest-title" className="mt-1 text-xl font-semibold text-zinc-950 dark:text-white">{digest.headline}</h2>
          <p className="mt-1 max-w-3xl text-sm text-zinc-600 dark:text-zinc-300">
            “New” means the role first entered your workspace today in {digest.timezone}. Worth-your-time roles are still open, not dismissed, eligible, and rated strong or promising against your saved profile evidence.
          </p>
        </div>
        <Link href="/searches" className="text-sm font-medium text-violet-700 underline underline-offset-4 dark:text-violet-300">Manage cadence</Link>
      </div>

      {digest.highlights.length > 0 ? (
        <div className="mt-5 grid gap-3 md:grid-cols-3">
          {digest.highlights.map((highlight) => (
            <Link key={highlight.opportunity_id} href={`/jobs/${encodeURIComponent(highlight.opportunity_id)}`} className="rounded-xl border border-violet-200 bg-white p-4 transition hover:border-violet-400 dark:border-violet-900 dark:bg-zinc-950 dark:hover:border-violet-700">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-semibold uppercase tracking-wide text-violet-700 dark:text-violet-300">{highlight.fit_band}</span>
                <span className="text-xs text-zinc-500">{highlight.confidence} confidence</span>
              </div>
              <p className="mt-2 font-semibold text-zinc-950 dark:text-white">{highlight.title}</p>
              <p className="text-sm text-zinc-500">{highlight.company}</p>
              <p className="mt-3 text-sm text-zinc-700 dark:text-zinc-300">{highlight.reasons[0]}</p>
            </Link>
          ))}
        </div>
      ) : (
        <p className="mt-4 text-sm text-zinc-600 dark:text-zinc-300">
          {digest.new_opportunities > 0
            ? "Today’s new roles did not clear every fit and eligibility gate. They remain visible below for inspection."
            : "No new roles have entered your workspace yet today."}
        </p>
      )}

      <div className="mt-4 flex flex-wrap gap-x-5 gap-y-1 text-xs text-zinc-500">
        <span>{digest.scans.succeeded} automatic scans completed</span>
        {digest.scans.running > 0 ? <span>{digest.scans.running} running</span> : null}
        {digest.scans.partial + digest.scans.failed > 0 ? <span>{digest.scans.partial + digest.scans.failed} need attention</span> : null}
        <span>{digest.active_scheduled_searches} scheduled searches active</span>
        {digest.next_scan_at ? <span>Next due {formatMoment(digest.next_scan_at, digest.timezone)}</span> : null}
      </div>
    </section>
  );
}

function formatMoment(value: string, timezone: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: timezone,
  }).format(new Date(value));
}
