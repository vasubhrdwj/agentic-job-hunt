"use client";

import Link from "next/link";
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

export interface DecisionResult {
  ok: boolean;
  error?: string;
}

export function OpportunityActions({
  opportunity,
  pending,
  ownerLocalDate,
  ownerTimezone,
  onDecision,
}: {
  opportunity: TodayOpportunityItem;
  pending: boolean;
  ownerLocalDate: string;
  ownerTimezone: string;
  onDecision: (payload: OpportunityDecisionPayload) => Promise<DecisionResult>;
}) {
  const pursueDialogRef = useRef<HTMLDialogElement>(null);
  const dismissDialogRef = useRef<HTMLDialogElement>(null);
  const pursueTriggerRef = useRef<HTMLButtonElement | null>(null);
  const dismissTriggerRef = useRef<HTMLButtonElement | null>(null);
  const [initialDueOn, setInitialDueOn] = useState(
    () => dateOffset(ownerLocalDate, 1),
  );
  const [reason, setReason] = useState<DismissReason>("not_relevant");
  const [note, setNote] = useState("");
  const [pursueDialogError, setPursueDialogError] = useState<string | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);

  useEffect(() => {
    const pursueDialog = pursueDialogRef.current;
    const dismissDialog = dismissDialogRef.current;
    const restorePursueFocus = () => pursueTriggerRef.current?.focus();
    const restoreDismissFocus = () => dismissTriggerRef.current?.focus();
    pursueDialog?.addEventListener("close", restorePursueFocus);
    dismissDialog?.addEventListener("close", restoreDismissFocus);
    return () => {
      pursueDialog?.removeEventListener("close", restorePursueFocus);
      dismissDialog?.removeEventListener("close", restoreDismissFocus);
    };
  }, []);

  function openPursue(event: React.MouseEvent<HTMLButtonElement>) {
    pursueTriggerRef.current = event.currentTarget;
    setInitialDueOn(dateOffset(ownerLocalDate, 1));
    setPursueDialogError(null);
    pursueDialogRef.current?.showModal();
  }

  function openDismiss(event: React.MouseEvent<HTMLButtonElement>) {
    dismissTriggerRef.current = event.currentTarget;
    setDialogError(null);
    dismissDialogRef.current?.showModal();
  }

  async function submitPursue(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !initialDueOn
      || initialDueOn < dateOffset(ownerLocalDate, 0)
      || initialDueOn > dateOffset(ownerLocalDate, 365)
    ) {
      setPursueDialogError("Choose a due date from today through one year from now.");
      return;
    }
    const saved = await onDecision({
      action: "pursue",
      initial_action_due_on: initialDueOn,
    });
    if (saved.ok) {
      pursueDialogRef.current?.close();
    } else {
      setPursueDialogError(
        `We couldn't confirm the application. ${saved.error ?? "Please try again."} `
        + "Your chosen date is still here, and retrying is safe.",
      );
    }
  }

  async function submitDismiss(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedNote = note.trim();
    if (reason === "other" && !trimmedNote) {
      setDialogError("Add a short note for the other reason.");
      return;
    }
    const saved = await onDecision({
      action: "dismiss",
      dismiss_reason: reason,
      ...(trimmedNote ? { note: trimmedNote } : {}),
    });
    if (saved.ok) {
      dismissDialogRef.current?.close();
      setNote("");
      setReason("not_relevant");
    } else {
      setDialogError(
        `${saved.error ?? "We couldn't confirm that decision."} `
        + "Your reason and note are still here.",
      );
    }
  }

  if (opportunity.state === "pursued") {
    return (
      <div className="flex min-w-0 flex-wrap items-center gap-3">
        <span className="rounded-full bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200">
          Application started
        </span>
        <Link href="/applications" className={secondaryButtonClasses}>
          Open application workspace
        </Link>
      </div>
    );
  }

  const canRestore = (
    opportunity.state === "watch" || opportunity.state === "dismiss"
  ) && opportunity.latest_decision;
  const postingCanBePursued = opportunity.posting.state === "open";

  return (
    <>
      <div className="flex min-w-0 flex-wrap gap-2" role="group" aria-label="Opportunity decision">
        <button
          ref={pursueTriggerRef}
          type="button"
          disabled={pending || !postingCanBePursued}
          onClick={openPursue}
          className={primaryButtonClasses}
          title={postingCanBePursued ? undefined : "Only an open posting can be pursued"}
          aria-label={postingCanBePursued
            ? `Pursue ${opportunity.posting.title} at ${opportunity.posting.company}`
            : `${opportunity.posting.title} cannot be pursued because only open postings are eligible`}
        >
          {pending ? "Saving…" : postingCanBePursued ? "Pursue" : "Posting unavailable"}
        </button>
        {opportunity.state !== "watch" ? (
          <button
            type="button"
            disabled={pending}
            onClick={() => void onDecision({ action: "watch" })}
            className={secondaryButtonClasses}
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
        ref={pursueDialogRef}
        onCancel={(event) => {
          if (pending) event.preventDefault();
        }}
        aria-labelledby={`pursue-title-${opportunity.id}`}
        aria-describedby={`pursue-description-${opportunity.id}`}
        className="m-auto w-[calc(100%_-_2rem)] max-w-lg rounded-2xl border border-zinc-200 bg-white p-0 text-zinc-950 shadow-2xl backdrop:bg-black/40 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50"
      >
        <form onSubmit={submitPursue} className="space-y-5 p-5 sm:p-6">
          <div>
            <h2 id={`pursue-title-${opportunity.id}`} className="text-lg font-semibold">
              Start pursuing this role?
            </h2>
            <p
              id={`pursue-description-${opportunity.id}`}
              className="mt-2 text-sm leading-6 text-zinc-600 dark:text-zinc-400"
            >
              This creates one application record and one dated task: review the role
              and prepare the application. You can open it immediately afterward.
            </p>
          </div>
          <div>
            <label htmlFor={`pursue-due-${opportunity.id}`} className="text-sm font-medium">
              First task due date
            </label>
            <input
              id={`pursue-due-${opportunity.id}`}
              type="date"
              required
              min={dateOffset(ownerLocalDate, 0)}
              max={dateOffset(ownerLocalDate, 365)}
              value={initialDueOn}
              onChange={(event) => {
                setInitialDueOn(event.target.value);
                setPursueDialogError(null);
              }}
              className={`${inputClasses} mt-2`}
              autoFocus
            />
            <p className="mt-2 text-xs leading-5 text-zinc-500">
              Tomorrow is the default in {ownerTimezone}. Pick a realistic date you will actually honor.
            </p>
          </div>
          {pursueDialogError ? (
            <p role="alert" className="text-sm text-red-700 dark:text-red-300">
              {pursueDialogError}
            </p>
          ) : null}
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <button
              type="button"
              disabled={pending}
              onClick={() => pursueDialogRef.current?.close()}
              className={secondaryButtonClasses}
            >
              Keep reviewing
            </button>
            <button type="submit" disabled={pending} className={primaryButtonClasses}>
              {pending ? "Creating…" : "Create application"}
            </button>
          </div>
        </form>
      </dialog>

      <dialog
        ref={dismissDialogRef}
        onCancel={(event) => {
          if (pending) event.preventDefault();
        }}
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
              onChange={(event) => {
                setReason(event.target.value as DismissReason);
                setDialogError(null);
              }}
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
              onChange={(event) => {
                setNote(event.target.value);
                setDialogError(null);
              }}
              className={`${textareaClasses} mt-2`}
              placeholder="A short, factual reminder"
            />
          </div>
          {dialogError ? <p role="alert" className="text-sm text-red-700 dark:text-red-300">{dialogError}</p> : null}
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <button
              type="button"
              disabled={pending}
              onClick={() => dismissDialogRef.current?.close()}
              className={secondaryButtonClasses}
            >
              Keep reviewing
            </button>
            <button type="submit" disabled={pending} className={primaryButtonClasses}>
              {pending ? "Saving…" : "Dismiss role"}
            </button>
          </div>
        </form>
      </dialog>
    </>
  );
}

function dateOffset(base: string, days: number): string {
  const [year, month, day] = base.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day + days));
  return date.toISOString().slice(0, 10);
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
