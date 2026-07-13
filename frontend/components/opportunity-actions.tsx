"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import type {
  DismissReason,
  OpportunityDecisionPayload,
  TodayOpportunityItem,
} from "@/lib/opportunity-types";
import {
  inputClasses,
  primaryButtonClasses,
  secondaryButtonClasses,
  textareaClasses,
} from "./workspace-ui";

const DISMISS_REASONS: Array<{ value: DismissReason; label: string }> = [
  { value: "not_relevant", label: "Role is not relevant" },
  { value: "seniority_mismatch", label: "Seniority mismatch" },
  { value: "location_or_mode", label: "Location or work mode" },
  { value: "compensation", label: "Compensation" },
  { value: "not_a_better_move", label: "Not a better career move" },
  { value: "company", label: "Company preference" },
  { value: "already_applied", label: "Already applied" },
  { value: "closed_or_invalid", label: "Closed or invalid posting" },
  { value: "duplicate", label: "Looks like a duplicate" },
  { value: "other", label: "Another reason" },
];

export function OpportunityActions({
  opportunity,
  pending,
  onDecision,
}: {
  opportunity: TodayOpportunityItem;
  pending: boolean;
  onDecision: (payload: OpportunityDecisionPayload) => Promise<void>;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const dismissTriggerRef = useRef<HTMLButtonElement | null>(null);
  const [reason, setReason] = useState<DismissReason>("not_relevant");
  const [note, setNote] = useState("");
  const [dialogError, setDialogError] = useState<string | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const restoreFocus = () => dismissTriggerRef.current?.focus();
    dialog.addEventListener("close", restoreFocus);
    return () => dialog.removeEventListener("close", restoreFocus);
  }, []);

  function openDismiss(event: React.MouseEvent<HTMLButtonElement>) {
    dismissTriggerRef.current = event.currentTarget;
    setDialogError(null);
    dialogRef.current?.showModal();
  }

  async function submitDismiss(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedNote = note.trim();
    if (reason === "other" && !trimmedNote) {
      setDialogError("Add a short note for the other reason.");
      return;
    }
    dialogRef.current?.close();
    await onDecision({
      action: "dismiss",
      dismiss_reason: reason,
      ...(trimmedNote ? { note: trimmedNote } : {}),
    });
    setNote("");
    setReason("not_relevant");
  }

  const canRestore = opportunity.state !== "inbox" && opportunity.latest_decision;

  return (
    <>
      <div className="flex min-w-0 flex-wrap gap-2" role="group" aria-label="Opportunity decision">
        {opportunity.state !== "watch" ? (
          <button
            type="button"
            disabled={pending}
            onClick={() => void onDecision({ action: "watch" })}
            className={primaryButtonClasses}
            aria-label={`Watch ${opportunity.posting.title} at ${opportunity.posting.company}`}
          >
            {pending ? "Saving…" : "Watch"}
          </button>
        ) : null}
        {canRestore ? (
          <button
            type="button"
            disabled={pending}
            onClick={() => void onDecision({
              action: "restore_to_inbox",
              restore_decision_event_id: opportunity.latest_decision?.id,
            })}
            className={secondaryButtonClasses}
            aria-label={`Restore ${opportunity.posting.title} to the review inbox`}
          >
            Restore to inbox
          </button>
        ) : null}
        {opportunity.state !== "dismiss" ? (
          <button
            ref={dismissTriggerRef}
            type="button"
            disabled={pending}
            onClick={openDismiss}
            className={secondaryButtonClasses}
            aria-label={`Dismiss ${opportunity.posting.title} at ${opportunity.posting.company}`}
          >
            Dismiss
          </button>
        ) : null}
      </div>

      <dialog
        ref={dialogRef}
        aria-labelledby={`dismiss-title-${opportunity.id}`}
        aria-describedby={`dismiss-description-${opportunity.id}`}
        className="m-auto w-[calc(100%_-_2rem)] max-w-lg rounded-2xl border border-zinc-200 bg-white p-0 text-zinc-950 shadow-2xl backdrop:bg-black/40 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50"
      >
        <form onSubmit={submitDismiss} className="space-y-5 p-5 sm:p-6">
          <div>
            <h2 id={`dismiss-title-${opportunity.id}`} className="text-lg font-semibold">
              Dismiss this role?
            </h2>
            <p
              id={`dismiss-description-${opportunity.id}`}
              className="mt-2 text-sm leading-6 text-zinc-600 dark:text-zinc-400"
            >
              It moves out of your inbox but remains in the dismissed view. Your reason
              helps you remember why; it is not sent to the employer.
            </p>
          </div>
          <div>
            <label htmlFor={`dismiss-reason-${opportunity.id}`} className="text-sm font-medium">
              Reason
            </label>
            <select
              id={`dismiss-reason-${opportunity.id}`}
              value={reason}
              onChange={(event) => setReason(event.target.value as DismissReason)}
              className={`${inputClasses} mt-2`}
              autoFocus
            >
              {DISMISS_REASONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>
          <div>
            <div className="flex items-center justify-between gap-3">
              <label htmlFor={`dismiss-note-${opportunity.id}`} className="text-sm font-medium">
                Note {reason === "other" ? "(required)" : "(optional)"}
              </label>
              <span className="text-xs text-zinc-500">{note.length}/500</span>
            </div>
            <textarea
              id={`dismiss-note-${opportunity.id}`}
              value={note}
              maxLength={500}
              onChange={(event) => setNote(event.target.value)}
              className={`${textareaClasses} mt-2`}
              placeholder="A short, factual reminder"
            />
          </div>
          {dialogError ? <p role="alert" className="text-sm text-red-700 dark:text-red-300">{dialogError}</p> : null}
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <button
              type="button"
              onClick={() => dialogRef.current?.close()}
              className={secondaryButtonClasses}
            >
              Keep reviewing
            </button>
            <button type="submit" className={primaryButtonClasses}>Dismiss role</button>
          </div>
        </form>
      </dialog>
    </>
  );
}

export function DecisionUndo({
  label,
  expiresAt,
  pending,
  onUndo,
  onExpire,
}: {
  label: string;
  expiresAt: number;
  pending: boolean;
  onUndo: () => Promise<void>;
  onExpire: () => void;
}) {
  const [seconds, setSeconds] = useState(() => Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000)));

  useEffect(() => {
    const timer = window.setInterval(() => {
      const remaining = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
      setSeconds(remaining);
      if (remaining === 0) {
        window.clearInterval(timer);
        onExpire();
      }
    }, 250);
    return () => window.clearInterval(timer);
  }, [expiresAt, onExpire]);

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed inset-x-4 bottom-4 z-50 mx-auto flex max-w-lg items-center justify-between gap-3 rounded-xl border border-zinc-700 bg-zinc-950 px-4 py-3 text-sm text-white shadow-xl"
    >
      <span className="min-w-0 break-words">{label}</span>
      <button
        type="button"
        disabled={pending || seconds === 0}
        onClick={() => void onUndo()}
        className="min-h-11 shrink-0 rounded-lg bg-white px-3 py-2 font-medium text-zinc-950 disabled:opacity-60"
      >
        {pending ? "Restoring…" : `Undo · ${seconds}s`}
      </button>
    </div>
  );
}
