"use client";

import { useRef, useState } from "react";
import type { OutreachDraft } from "@/lib/types";

export function DraftCard({ draft }: { draft: OutreachDraft }) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [message, setMessage] = useState(draft.message);
  const [copied, setCopied] = useState(false);

  function onEdit() {
    const node = textareaRef.current;
    if (!node) return;
    node.focus();
    node.select();
  }

  async function onCopy() {
    try {
      await navigator.clipboard.writeText(message);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard may be blocked in insecure contexts. Fall back silently.
    }
  }

  return (
    <div className="rounded-md border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium">{draft.person.name}</p>
          <p className="text-xs text-zinc-500">{draft.person.title}</p>
          <a
            href={draft.person.profile_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-indigo-600 hover:underline dark:text-indigo-400"
          >
            {draft.person.source} profile ↗
          </a>
          {draft.person.verified_current_employer &&
          draft.person.confidence >= 0.5 ? (
            <p className="mt-1 text-[10px] font-medium text-emerald-700 dark:text-emerald-300">
              Verified current employer ·{" "}
              {Math.round(draft.person.confidence * 100)}% confidence
            </p>
          ) : (
            <p className="mt-1 text-[10px] font-medium text-amber-700 dark:text-amber-300">
              Unverified legacy contact
            </p>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          {typeof draft.eval_score === "number" && (
            <span
              title="LLM-judge composite (personalization, specificity, ask, tone)"
              className={`rounded-full px-2 py-0.5 text-[10px] font-semibold tracking-wide ${
                draft.eval_score >= 4
                  ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
                  : draft.eval_score >= 3
                    ? "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
                    : "bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300"
              }`}
            >
              {draft.eval_score.toFixed(1)} / 5
            </span>
          )}
          <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-[10px] uppercase tracking-wide text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
            {draft.person.source}
          </span>
        </div>
      </div>

      <p className="mb-3 text-xs text-zinc-600 dark:text-zinc-400">
        <span className="font-medium">Why relevant:</span>{" "}
        {draft.person.why_relevant}
      </p>

      <label htmlFor={`legacy-draft-${draft.draft_id}`} className="sr-only">
        Editable outreach draft for {draft.person.name}
      </label>
      <textarea
        id={`legacy-draft-${draft.draft_id}`}
        ref={textareaRef}
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        rows={8}
        className="block w-full rounded-md border border-zinc-300 bg-white p-3 font-mono text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-950"
      />

      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          onClick={onEdit}
          className="inline-flex h-9 items-center justify-center rounded-md border border-zinc-300 px-3 text-xs font-medium hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
        >
          Edit
        </button>
        <button
          type="button"
          onClick={onCopy}
          className="inline-flex h-9 items-center justify-center rounded-md bg-zinc-900 px-3 text-xs font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          {copied ? "Copied" : "Copy"}
        </button>
        <span className="ml-auto font-mono text-[10px] text-zinc-400">
          {draft.draft_id.slice(0, 8)}
        </span>
      </div>
    </div>
  );
}
