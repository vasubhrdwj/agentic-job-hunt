"use client";

import { useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";

import { getApplication, transitionApplication } from "@/lib/application-api";
import type {
  ApplicationProgressTransitionCreate,
} from "@/lib/application-progress-types";
import type {
  ApplicationDetailResponse,
  ApplicationOutcome,
  ApplicationOutcomeKind,
  ApplicationStage,
} from "@/lib/application-types";
import { createIdempotencyKey, WorkspaceApiError } from "@/lib/workspace-api";
import {
  errorText,
  inputClasses,
  primaryButtonClasses,
  secondaryButtonClasses,
  StatusMessage,
} from "./workspace-ui";

type ProgressChoice =
  | "screening"
  | "interviewing"
  | "offer"
  | ApplicationOutcomeKind;

interface ChoiceOption {
  value: ProgressChoice;
  label: string;
}

interface PendingTransition {
  key: string;
  fingerprint: string;
  payload: ApplicationProgressTransitionCreate;
  expectedVersion: number;
}

const OUTCOME_OPTIONS: Record<ApplicationOutcomeKind, ChoiceOption> = {
  rejected: { value: "rejected", label: "Rejected by employer" },
  withdrawn: { value: "withdrawn", label: "I withdrew" },
  offer_accepted: { value: "offer_accepted", label: "Offer accepted" },
  offer_declined: { value: "offer_declined", label: "Offer declined" },
  no_response: { value: "no_response", label: "No response — close as stale" },
  posting_closed: { value: "posting_closed", label: "Posting closed" },
};

export function ApplicationProgress({
  applicationId,
  applicationVersion,
  stage,
  outcome,
  scheduledInterviewRoundId,
  ownerLocalDate,
  onApplicationChanged,
}: {
  applicationId: string;
  applicationVersion: number;
  stage: ApplicationStage;
  outcome: ApplicationOutcome | null;
  scheduledInterviewRoundId: string | null;
  ownerLocalDate: string;
  onApplicationChanged: () => Promise<void>;
}) {
  const options = useMemo(() => choicesForStage(stage), [stage]);
  const [choice, setChoice] = useState<ProgressChoice | "">("");
  const validChoice = choice && options.some((option) => option.value === choice)
    ? choice
    : "";
  const [effectiveOn, setEffectiveOn] = useState(ownerLocalDate);
  const [nextActionDueOn, setNextActionDueOn] = useState(
    addBusinessDays(ownerLocalDate, 2),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [hasPending, setHasPending] = useState(false);
  const pending = useRef<PendingTransition | null>(null);

  if (!isProgressStage(stage)) return null;

  if (stage === "closed") {
    return (
      <section
        aria-labelledby="hiring-progress-title"
        className="min-w-0 rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm sm:p-7 dark:border-zinc-800 dark:bg-zinc-900/70"
      >
        <ProgressHeading closed />
        {outcome ? (
          <div className="mt-5 rounded-xl bg-zinc-50 p-4 dark:bg-zinc-950/60">
            <p className="font-semibold">{outcomeLabel(outcome.outcome)}</p>
            <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
              Closed on {formatDateOnly(outcome.outcome_on)}. Recorded manually; no further role-specific action is open.
            </p>
          </div>
        ) : (
          <StatusMessage kind="info">
            This application is closed. Its durable outcome is being refreshed.
          </StatusMessage>
        )}
      </section>
    );
  }

  if (scheduledInterviewRoundId) {
    return (
      <section
        aria-labelledby="hiring-progress-title"
        className="min-w-0 rounded-2xl border border-sky-200 bg-white p-5 shadow-sm sm:p-7 dark:border-sky-900 dark:bg-zinc-900/70"
      >
        <ProgressHeading />
        <div className="mt-4">
          <StatusMessage kind="info">
            Complete or cancel the scheduled interview round before recording another hiring milestone or closing this application. This keeps the round outcome and hiring progress in the right order.
          </StatusMessage>
        </div>
        <a href="#interview-rounds" className={`${secondaryButtonClasses} mt-4`}>
          Manage scheduled round
        </a>
      </section>
    );
  }

  const selectedOutcome = validChoice && isOutcomeChoice(validChoice) ? validChoice : null;
  const controlsLocked = busy || hasPending;
  const minNextActionDate = effectiveOn > ownerLocalDate
    ? effectiveOn
    : ownerLocalDate;

  async function submitProgress(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!validChoice || !effectiveOn) return;
    const payload = transitionPayload(
      validChoice,
      effectiveOn,
      nextActionDueOn,
    );
    if (!payload) return;
    await runTransition(payload);
  }

  async function runTransition(payload: ApplicationProgressTransitionCreate) {
    if (busy) return;
    const fingerprint = JSON.stringify(payload);
    const existing = pending.current;
    if (existing && existing.fingerprint !== fingerprint) {
      setError("Retry the unchanged pending update or check its saved state before changing inputs.");
      return;
    }
    const request = existing ?? {
      key: createIdempotencyKey(`application-progress:${applicationId}:${payload.to_stage}`),
      fingerprint,
      payload,
      expectedVersion: applicationVersion,
    };
    pending.current = request;
    setHasPending(true);
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await transitionApplication(
        applicationId,
        request.expectedVersion,
        request.key,
        request.payload,
      );
      pending.current = null;
      setHasPending(false);
      await onApplicationChanged();
      setChoice("");
      setNotice(successCopy(payload));
    } catch (reason) {
      const saved = await safelyLoadApplication(applicationId);
      const apiError = reason instanceof WorkspaceApiError ? reason : null;
      const ambiguous = !apiError || apiError.retryable || apiError.code === "mutation_pending";
      if (saved && transitionMatches(saved, payload)) {
        pending.current = null;
        setHasPending(false);
        await onApplicationChanged();
        setChoice("");
        setNotice("The exact progress update was confirmed from the durable record.");
      } else if (saved && saved.application.version !== request.expectedVersion) {
        pending.current = null;
        setHasPending(false);
        await onApplicationChanged();
        setError("This application changed in a different way. Review its latest progress before recording another update.");
      } else if (!ambiguous) {
        pending.current = null;
        setHasPending(false);
        setError(errorText(reason, "The progress update was rejected."));
      } else {
        setError(
          `${errorText(reason, "The update result is not yet confirmed.")} ` +
          "Your exact request is retained; retry it unchanged or check saved state.",
        );
      }
    } finally {
      setBusy(false);
    }
  }

  async function checkSavedState() {
    const request = pending.current;
    if (!request || busy) return;
    setBusy(true);
    setError(null);
    try {
      const saved = await safelyLoadApplication(applicationId);
      if (saved && transitionMatches(saved, request.payload)) {
        pending.current = null;
        setHasPending(false);
        await onApplicationChanged();
        setChoice("");
        setNotice("The exact progress update was confirmed from the durable record.");
      } else if (saved && saved.application.version !== request.expectedVersion) {
        pending.current = null;
        setHasPending(false);
        await onApplicationChanged();
        setError("A different progress update is already saved. Review the current application before continuing.");
      } else {
        setError("This exact update is not visible yet. Retry the unchanged request with its original safe receipt.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function retryPending() {
    const request = pending.current;
    if (!request || busy) return;
    await runTransition(request.payload);
  }

  return (
    <section
      aria-labelledby="hiring-progress-title"
      className="min-w-0 rounded-2xl border border-sky-200 bg-white p-5 shadow-sm sm:p-7 dark:border-sky-900 dark:bg-zinc-900/70"
    >
      <ProgressHeading />
      <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
        Record confirmed hiring milestones and final outcomes only. Schedule and complete appointments in Interview rounds so every round keeps its own durable history.
      </p>

      {error ? <div className="mt-4"><StatusMessage kind="error">{error}</StatusMessage></div> : null}
      {notice ? <div className="mt-4"><StatusMessage kind="success">{notice}</StatusMessage></div> : null}

      <form onSubmit={submitProgress} className="mt-5 space-y-4">
        <label className="block text-sm font-medium">
          What changed?
          <select
            value={validChoice}
            disabled={controlsLocked}
            required
            onChange={(event) => {
              const next = event.target.value as ProgressChoice | "";
              setChoice(next);
              setError(null);
              if (next === "offer") {
                setNextActionDueOn(addBusinessDays(ownerLocalDate, 3));
              } else if (next && !isOutcomeChoice(next)) {
                setNextActionDueOn(addBusinessDays(ownerLocalDate, 2));
              }
            }}
            className={`${inputClasses} mt-2`}
          >
            <option value="">Choose a confirmed milestone</option>
            {options.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>

        {validChoice ? (
          <div className={`grid gap-4 ${selectedOutcome ? "" : "sm:grid-cols-2"}`}>
            <label className="block text-sm font-medium">
              {effectiveDateLabel(validChoice)}
              <input
                type="date"
                value={effectiveOn}
                max={ownerLocalDate}
                disabled={controlsLocked}
                required
                onChange={(event) => setEffectiveOn(event.target.value)}
                className={`${inputClasses} mt-2`}
              />
            </label>
            {!selectedOutcome ? (
              <label className="block text-sm font-medium">
                {validChoice === "offer" ? "Offer response due" : "Next action due"}
                <input
                  type="date"
                  value={nextActionDueOn}
                  min={minNextActionDate}
                  disabled={controlsLocked}
                  required
                  onChange={(event) => setNextActionDueOn(event.target.value)}
                  className={`${inputClasses} mt-2`}
                />
              </label>
            ) : null}
          </div>
        ) : null}

        {selectedOutcome ? (
          <p className="rounded-lg border border-zinc-200 bg-zinc-50 p-3 text-sm text-zinc-700 dark:border-zinc-800 dark:bg-zinc-950/60 dark:text-zinc-300">
            Closing completes the current task and ends role-specific next actions. The outcome remains in the durable Activity record.
          </p>
        ) : null}

        <button
          type="submit"
          disabled={controlsLocked || !validChoice || !effectiveOn || (!selectedOutcome && !nextActionDueOn)}
          className={`${selectedOutcome ? closeButtonClasses : primaryButtonClasses} w-full sm:w-auto`}
        >
          {busy ? "Recording progress…" : submitLabel(validChoice)}
        </button>
      </form>

      {hasPending && !busy ? (
        <div className="mt-4 flex flex-wrap gap-2">
          <button type="button" onClick={() => void checkSavedState()} className={secondaryButtonClasses}>
            Check saved state
          </button>
          <button type="button" onClick={() => void retryPending()} className={secondaryButtonClasses}>
            Retry unchanged
          </button>
        </div>
      ) : null}
    </section>
  );
}

function ProgressHeading({ closed = false }: { closed?: boolean }) {
  return (
    <>
      <p className="text-xs font-semibold uppercase tracking-[0.14em] text-sky-700 dark:text-sky-300">
        Hiring progress
      </p>
      <h2 id="hiring-progress-title" className="mt-2 text-xl font-semibold">
        {closed ? "Application closed" : "Keep the next step current"}
      </h2>
    </>
  );
}

function choicesForStage(stage: ApplicationStage): ChoiceOption[] {
  if (stage === "pursuing" || stage === "ready_to_apply") {
    return [OUTCOME_OPTIONS.withdrawn, OUTCOME_OPTIONS.posting_closed];
  }
  const terminal = stage === "offer"
    ? [
        OUTCOME_OPTIONS.offer_accepted,
        OUTCOME_OPTIONS.offer_declined,
        OUTCOME_OPTIONS.rejected,
        OUTCOME_OPTIONS.withdrawn,
        OUTCOME_OPTIONS.no_response,
        OUTCOME_OPTIONS.posting_closed,
      ]
    : [OUTCOME_OPTIONS.rejected, OUTCOME_OPTIONS.withdrawn, OUTCOME_OPTIONS.no_response, OUTCOME_OPTIONS.posting_closed];
  if (stage === "applied") {
    return [
      { value: "screening", label: "Recruiter screen completed" },
      { value: "offer", label: "Offer received" },
      ...terminal,
    ];
  }
  if (stage === "screening") {
    return [
      { value: "offer", label: "Offer received" },
      ...terminal,
    ];
  }
  if (stage === "interviewing") {
    return [{ value: "offer", label: "Offer received" }, ...terminal];
  }
  if (stage === "offer") return terminal;
  return [];
}

function transitionPayload(
  choice: ProgressChoice,
  effectiveOn: string,
  nextActionDueOn: string,
): ApplicationProgressTransitionCreate | null {
  if (choice === "screening" || choice === "interviewing") {
    return {
      to_stage: choice,
      reached_on: effectiveOn,
      next_action_due_on: nextActionDueOn,
      confirm_progress: true,
    };
  }
  if (choice === "offer") {
    return {
      to_stage: "offer",
      received_on: effectiveOn,
      next_action_due_on: nextActionDueOn,
      confirm_offer: true,
    };
  }
  if (isOutcomeChoice(choice)) {
    return {
      to_stage: "closed",
      outcome: choice,
      outcome_on: effectiveOn,
      confirm_close: true,
    };
  }
  return null;
}

function transitionMatches(
  detail: ApplicationDetailResponse,
  payload: ApplicationProgressTransitionCreate,
): boolean {
  if (payload.to_stage === "closed") {
    return Boolean(
      detail.application.stage === "closed" &&
      detail.application.outcome?.outcome === payload.outcome &&
      detail.application.outcome.outcome_on === payload.outcome_on,
    );
  }
  const eventType = {
    screening: "application_screening",
    interviewing: "application_interviewing",
    offer: "application_offer",
  } as const;
  const effectiveOn = payload.to_stage === "offer"
    ? payload.received_on
    : payload.reached_on;
  return detail.activity.some((event) => (
    event.event_type === eventType[payload.to_stage] &&
    event.effective_on === effectiveOn
  ));
}

async function safelyLoadApplication(
  applicationId: string,
): Promise<ApplicationDetailResponse | null> {
  try {
    return await getApplication(applicationId);
  } catch {
    return null;
  }
}

function isProgressStage(stage: ApplicationStage): boolean {
  return stage === "pursuing" || stage === "ready_to_apply" ||
    stage === "applied" || stage === "screening" ||
    stage === "interviewing" || stage === "offer" || stage === "closed";
}

function isOutcomeChoice(value: ProgressChoice): value is ApplicationOutcomeKind {
  return value in OUTCOME_OPTIONS;
}

function effectiveDateLabel(choice: ProgressChoice): string {
  if (choice === "screening") return "Recruiter screen date";
  if (choice === "interviewing") return "Interview date";
  if (choice === "offer") return "Offer received date";
  return "Outcome date";
}

function submitLabel(choice: ProgressChoice | ""): string {
  if (choice === "screening") return "Record recruiter screen";
  if (choice === "interviewing") return "Record interview";
  if (choice === "offer") return "Record offer";
  if (choice && isOutcomeChoice(choice)) return `Close as ${outcomeLabel(choice).toLowerCase()}`;
  return "Record progress";
}

function successCopy(payload: ApplicationProgressTransitionCreate): string {
  if (payload.to_stage === "screening") return "Recruiter screen recorded. Your next dated task is ready.";
  if (payload.to_stage === "interviewing") return "Interview progress recorded. Your next dated task is ready.";
  if (payload.to_stage === "offer") return "Offer recorded. Your response deadline is now the current task.";
  return `${outcomeLabel(payload.outcome)} recorded. This application is now closed.`;
}

export function outcomeLabel(outcome: ApplicationOutcomeKind): string {
  return ({
    rejected: "Rejected",
    withdrawn: "Withdrawn",
    offer_accepted: "Offer accepted",
    offer_declined: "Offer declined",
    no_response: "No response",
    posting_closed: "Posting closed",
  } as const)[outcome];
}

function addBusinessDays(value: string, count: number): string {
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day, 12));
  let remaining = count;
  while (remaining > 0) {
    date.setUTCDate(date.getUTCDate() + 1);
    const weekday = date.getUTCDay();
    if (weekday !== 0 && weekday !== 6) remaining -= 1;
  }
  return date.toISOString().slice(0, 10);
}

function formatDateOnly(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(year, month - 1, day, 12);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}

const closeButtonClasses =
  "inline-flex min-h-11 items-center justify-center rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-950 dark:hover:bg-white";
