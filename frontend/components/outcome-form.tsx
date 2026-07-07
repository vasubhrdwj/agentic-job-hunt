"use client";

import Link from "next/link";
import { useState } from "react";
import { ApiError, postOutcomes } from "@/lib/api";
import type { HuntResult, OutcomeKind, OutcomeLog } from "@/lib/types";

const OPTIONS: { value: OutcomeKind; label: string }[] = [
  { value: "pending", label: "Pending" },
  { value: "replied", label: "Replied" },
  { value: "no_reply", label: "No reply" },
  { value: "introduced", label: "Introduced" },
  { value: "rejected", label: "Rejected" },
];

type DraftState = {
  outcome: OutcomeKind;
  notes: string;
};

export function OutcomeForm({
  runId,
  accessToken,
  huntResult,
  previousOutcomes,
}: {
  runId: string;
  accessToken: string;
  huntResult: HuntResult;
  previousOutcomes: OutcomeLog[];
}) {
  const initial: Record<string, DraftState> = Object.fromEntries(
    huntResult.outreach.map((draft) => [
      draft.draft_id,
      { outcome: "pending" as OutcomeKind, notes: "" },
    ]),
  );

  const [state, setState] = useState<Record<string, DraftState>>(initial);
  const [pending, setPending] = useState(false);
  const [savedCount, setSavedCount] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  function update(draftId: string, patch: Partial<DraftState>) {
    setState((prev) => ({ ...prev, [draftId]: { ...prev[draftId], ...patch } }));
  }

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setSavedCount(null);

    const logs: OutcomeLog[] = Object.entries(state)
      .filter(([, value]) => value.outcome !== "pending" || value.notes.trim())
      .map(([draftId, value]) => ({
        draft_id: draftId,
        outcome: value.outcome,
        notes: value.notes.trim() ? value.notes.trim() : null,
      }));

    if (logs.length === 0) {
      setError("Pick at least one outcome before saving.");
      return;
    }

    setPending(true);
    try {
      const res = await postOutcomes(runId, accessToken, logs);
      setSavedCount(res.inserted);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? `Backend returned ${err.status}.`
          : err instanceof Error
            ? err.message
            : "Unknown error.";
      setError(message);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-8">
      {previousOutcomes.length > 0 && (
        <section className="rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="text-sm font-semibold">
            Previously logged ({previousOutcomes.length})
          </h2>
          <ul className="mt-3 space-y-2 text-sm">
            {previousOutcomes.map((log, i) => (
              <li
                key={`${log.draft_id}-${log.logged_at ?? i}`}
                className="flex items-start justify-between gap-3 border-b border-zinc-100 pb-2 last:border-0 dark:border-zinc-800"
              >
                <div>
                  <span className="font-medium">{log.outcome}</span>
                  {log.notes && (
                    <span className="text-zinc-500"> — {log.notes}</span>
                  )}
                </div>
                <span className="font-mono text-[10px] text-zinc-400">
                  {log.draft_id.slice(0, 8)} •{" "}
                  {log.logged_at
                    ? new Date(log.logged_at).toLocaleString()
                    : ""}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <form onSubmit={onSubmit} className="space-y-4">
        {huntResult.outreach.map((draft) => {
          const cur = state[draft.draft_id];
          return (
            <fieldset
              key={draft.draft_id}
              className="rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900"
            >
              <legend className="px-1 text-sm font-medium">
                {draft.person.name} at {draft.role.company}
              </legend>
              <p className="-mt-1 text-xs text-zinc-500">
                {draft.role.title} • {draft.person.title}
              </p>

              <div className="mt-4 flex flex-wrap gap-2">
                {OPTIONS.map((opt) => {
                  const checked = cur.outcome === opt.value;
                  return (
                    <label
                      key={opt.value}
                      className={`inline-flex h-9 cursor-pointer items-center rounded-full border px-3 text-xs ${
                        checked
                          ? "border-indigo-500 bg-indigo-50 text-indigo-900 dark:border-indigo-400 dark:bg-indigo-950 dark:text-indigo-100"
                          : "border-zinc-300 hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
                      }`}
                    >
                      <input
                        type="radio"
                        name={`outcome-${draft.draft_id}`}
                        value={opt.value}
                        checked={checked}
                        onChange={() =>
                          update(draft.draft_id, { outcome: opt.value })
                        }
                        className="sr-only"
                      />
                      {opt.label}
                    </label>
                  );
                })}
              </div>

              <textarea
                value={cur.notes}
                onChange={(e) =>
                  update(draft.draft_id, { notes: e.target.value })
                }
                placeholder="Optional notes (forwarded, hiring manager intro, etc.)"
                rows={2}
                className="mt-3 block w-full rounded-md border border-zinc-300 bg-white p-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-950"
              />
            </fieldset>
          );
        })}

        {error && (
          <div
            role="alert"
            className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100"
          >
            {error}
          </div>
        )}

        {savedCount !== null && (
          <div className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-100">
            Saved {savedCount} outcome{savedCount === 1 ? "" : "s"}.{" "}
            <Link
              href={`/runs/${runId}`}
              className="font-medium underline underline-offset-2"
            >
              Back to review
            </Link>
            .
          </div>
        )}

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={pending}
            className="inline-flex h-11 items-center justify-center rounded-md bg-indigo-600 px-6 text-sm font-medium text-white hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 disabled:opacity-60"
          >
            {pending ? "Saving…" : "Save outcomes"}
          </button>
          <Link
            href={`/runs/${runId}`}
            className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
          >
            Cancel
          </Link>
        </div>
      </form>
    </div>
  );
}
