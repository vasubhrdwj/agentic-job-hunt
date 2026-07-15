"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent, RefObject } from "react";

import {
  correctApplicationMilestoneDate,
  getApplication,
} from "@/lib/application-api";
import type { ApplicationMilestoneCorrectionCreate } from "@/lib/application-correction-types";
import type {
  ApplicationActivityEvent,
  ApplicationDetailResponse,
  ApplicationMilestoneCorrectionMutationResponse,
} from "@/lib/application-types";
import {
  createIdempotencyKey,
  WorkspaceApiError,
} from "@/lib/workspace-api";
import {
  errorText,
  formatDate,
  inputClasses,
  primaryButtonClasses,
  secondaryButtonClasses,
  StatusMessage,
} from "./workspace-ui";

interface PendingCorrection {
  key: string;
  fingerprint: string;
  expectedVersion: number;
  activityEventId: string;
  knownCorrectionIds: string[];
  payload: ApplicationMilestoneCorrectionCreate;
}

interface LocalProjection {
  version: number;
  activity: ApplicationActivityEvent[];
}

export function ApplicationActivity({
  applicationId,
  applicationVersion,
  activity,
  ownerLocalDate,
  onApplicationChanged,
}: {
  applicationId: string;
  applicationVersion: number;
  activity: ApplicationActivityEvent[];
  ownerLocalDate: string;
  onApplicationChanged: () => Promise<boolean>;
}) {
  const [localProjection, setLocalProjection] =
    useState<LocalProjection | null>(null);
  const [editorEventId, setEditorEventId] = useState<string | null>(null);
  const [correctedOn, setCorrectedOn] = useState(ownerLocalDate);
  const [confirmationFingerprint, setConfirmationFingerprint] =
    useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [hasPending, setHasPending] = useState(false);
  const [synchronizationBlocked, setSynchronizationBlocked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const pendingRef = useRef<PendingCorrection | null>(null);
  const editorContainerRef = useRef<HTMLDivElement | null>(null);
  const editorTriggerRef = useRef<HTMLButtonElement | null>(null);
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const previousEditorRef = useRef<string | null>(null);

  const useLocalProjection = Boolean(
    localProjection && localProjection.version > applicationVersion,
  );
  const durableVersion = useLocalProjection
    ? localProjection!.version
    : applicationVersion;
  const durableActivity = useLocalProjection
    ? localProjection!.activity
    : activity;
  const editorEvent = editorEventId
    ? durableActivity.find((event) => event.id === editorEventId) ?? null
    : null;
  const controlsLocked = busy || hasPending || synchronizationBlocked;

  useEffect(() => {
    const previousEditor = previousEditorRef.current;
    previousEditorRef.current = editorEventId;
    if (editorEventId) {
      const timer = setTimeout(() => {
        editorContainerRef.current?.querySelector<HTMLElement>("input")?.focus();
      }, 0);
      return () => clearTimeout(timer);
    }
    if (previousEditor) {
      const timer = setTimeout(() => {
        const trigger = editorTriggerRef.current;
        if (trigger?.isConnected) trigger.focus();
        else headingRef.current?.focus();
        editorTriggerRef.current = null;
      }, 0);
      return () => clearTimeout(timer);
    }
  }, [editorEventId]);

  function acceptMutation(result: ApplicationMilestoneCorrectionMutationResponse) {
    setLocalProjection((existing) => {
      const base = existing && existing.version > applicationVersion
        ? existing.activity
        : activity;
      return {
        version: result.application.version,
        activity: base.map((event) => (
          event.id === result.activity_event.id ? result.activity_event : event
        )),
      };
    });
  }

  function acceptDetail(detail: ApplicationDetailResponse) {
    if (detail.application.id !== applicationId) return;
    setLocalProjection({
      version: detail.application.version,
      activity: detail.activity,
    });
  }

  function clearPending() {
    pendingRef.current = null;
    setHasPending(false);
  }

  function closeEditor() {
    setEditorEventId(null);
    setConfirmationFingerprint(null);
  }

  function openEditor(
    event: ApplicationActivityEvent,
    trigger: HTMLButtonElement,
  ) {
    const currentDate = resolvedDate(event);
    if (!currentDate || !isCorrectableMilestone(event)) return;
    editorTriggerRef.current = trigger;
    setEditorEventId(event.id);
    setCorrectedOn(currentDate);
    setConfirmationFingerprint(null);
    setError(null);
    setNotice(null);
  }

  async function synchronizeAfterSave() {
    const synchronized = await onApplicationChanged();
    if (!synchronized) {
      setSynchronizationBlocked(true);
      setError(
        "The correction is saved, but the rest of this dossier could not be refreshed. " +
        "Refresh the dossier before recording another correction.",
      );
    }
  }

  async function runCorrection(request: PendingCorrection) {
    if (busy) return;
    const existing = pendingRef.current;
    if (existing && existing.fingerprint !== request.fingerprint) {
      setError(
        "Retry the unchanged pending correction or check its saved state " +
        "before changing the date.",
      );
      return;
    }
    const exactRequest = existing ?? request;
    pendingRef.current = exactRequest;
    setHasPending(true);
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await correctApplicationMilestoneDate(
        applicationId,
        exactRequest.activityEventId,
        exactRequest.expectedVersion,
        exactRequest.key,
        exactRequest.payload,
      );
      acceptMutation(result);
      clearPending();
      closeEditor();
      const replayedCorrectionIsCurrent =
        result.activity_event.corrections.at(-1)?.id === result.correction.id;
      const savedCopy = result.correction_created
        ? "Correction saved. The updated date is now current; the original remains in Activity."
        : replayedCorrectionIsCurrent
          ? "The exact correction was already saved and remains current."
          : "The exact correction was already saved. A newer correction is now current in Activity.";
      setNotice(savedCopy);
      await synchronizeAfterSave();
    } catch (reason) {
      const saved = await safelyLoadApplication(applicationId);
      if (saved) acceptDetail(saved);
      const apiError = reason instanceof WorkspaceApiError ? reason : null;
      const ambiguous =
        !apiError || apiError.retryable || apiError.code === "mutation_pending";
      if (ambiguous && saved && correctionMatches(saved, exactRequest)) {
        clearPending();
        closeEditor();
        const savedCopy =
          "The exact correction was confirmed from the durable record.";
        setNotice(savedCopy);
        await synchronizeAfterSave();
      } else if (
        saved &&
        saved.application.version !== exactRequest.expectedVersion
      ) {
        clearPending();
        await onApplicationChanged();
        setError(
          "This application changed elsewhere. Review the current milestone " +
          "date before correcting it.",
        );
      } else if (!ambiguous) {
        clearPending();
        setError(errorText(reason, "The milestone correction was rejected."));
      } else {
        setError(
          errorText(reason, "The correction result is not yet confirmed.") +
          " Your exact request is retained; retry it unchanged or check saved state.",
        );
      }
    } finally {
      setBusy(false);
    }
  }

  async function checkSavedState() {
    const request = pendingRef.current;
    if (!request || busy) return;
    setBusy(true);
    setError(null);
    try {
      const saved = await safelyLoadApplication(applicationId);
      if (saved) acceptDetail(saved);
      if (saved && correctionMatches(saved, request)) {
        clearPending();
        closeEditor();
        const savedCopy =
          "The exact correction was confirmed from the durable record.";
        setNotice(savedCopy);
        await synchronizeAfterSave();
      } else if (
        saved &&
        saved.application.version !== request.expectedVersion
      ) {
        clearPending();
        await onApplicationChanged();
        setError(
          "A different application update is already saved. Review the " +
          "current milestone date before continuing.",
        );
      } else {
        setError(
          "This exact correction is not visible yet. Retry the unchanged " +
          "request with its original safe receipt.",
        );
      }
    } finally {
      setBusy(false);
    }
  }

  async function refreshDossier() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      if (await onApplicationChanged()) {
        setSynchronizationBlocked(false);
        setNotice("The dossier is current again.");
      } else {
        setError(
          "The saved correction remains available, but the dossier still " +
          "could not be refreshed.",
        );
      }
    } finally {
      setBusy(false);
    }
  }

  function submitCorrection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !editorEvent ||
      controlsLocked ||
      !isCorrectableMilestone(editorEvent)
    ) return;
    const currentDate = resolvedDate(editorEvent);
    const confirmation = correctionFingerprint(
      editorEvent.id,
      currentDate,
      correctedOn,
    );
    if (
      !currentDate ||
      !correctedOn ||
      correctedOn === currentDate ||
      confirmationFingerprint !== confirmation
    ) return;
    const payload: ApplicationMilestoneCorrectionCreate = {
      corrected_effective_on: correctedOn,
      confirm_correction: true,
    };
    void runCorrection({
      key: createIdempotencyKey(
        "application-milestone-correction:" +
        applicationId +
        ":" +
        editorEvent.id,
      ),
      fingerprint: editorEvent.id + ":" + JSON.stringify(payload),
      expectedVersion: durableVersion,
      activityEventId: editorEvent.id,
      knownCorrectionIds: editorEvent.corrections.map(
        (correction) => correction.id,
      ),
      payload,
    });
  }

  return (
    <section
      id="application-activity"
      aria-labelledby="application-activity-title"
      aria-busy={busy}
      className="scroll-mt-6 rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm sm:p-7 dark:border-zinc-800 dark:bg-zinc-900/70"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h2
            ref={headingRef}
            id="application-activity-title"
            tabIndex={-1}
            className="text-lg font-semibold"
          >
            Activity
          </h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-zinc-500">
            A permanent chronological record. If a saved milestone date is
            wrong, add a correction—the original stays visible and your stage
            and task do not change.
          </p>
        </div>
        {synchronizationBlocked ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void refreshDossier()}
            className={secondaryButtonClasses + " w-full shrink-0 sm:w-auto"}
          >
            {busy ? "Refreshing…" : "Refresh dossier"}
          </button>
        ) : null}
      </div>

      {error ? (
        <div className="mt-4">
          <StatusMessage kind="error">{error}</StatusMessage>
        </div>
      ) : null}
      {notice ? (
        <div className="mt-4">
          <StatusMessage kind="success">{notice}</StatusMessage>
        </div>
      ) : null}

      <ol className="mt-5 space-y-5">
        {durableActivity.map((event) => (
          <ActivityItem
            key={event.id}
            event={event}
            editorOpen={editorEventId === event.id}
            editorEvent={editorEventId === event.id ? editorEvent : null}
            correctedOn={correctedOn}
            ownerLocalDate={ownerLocalDate}
            controlsLocked={controlsLocked}
            busy={busy}
            confirmationFingerprint={confirmationFingerprint}
            editorContainerRef={editorContainerRef}
            onOpenEditor={openEditor}
            onCorrectedOn={setCorrectedOn}
            onConfirmationFingerprint={setConfirmationFingerprint}
            onCancelEditor={closeEditor}
            onSubmit={submitCorrection}
          />
        ))}
      </ol>

      {hasPending && !busy ? (
        <div className="mt-5 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          <button
            type="button"
            onClick={() => void checkSavedState()}
            className={secondaryButtonClasses}
          >
            Check saved state
          </button>
          <button
            type="button"
            onClick={() => {
              const pending = pendingRef.current;
              if (pending) void runCorrection(pending);
            }}
            className={secondaryButtonClasses}
          >
            Retry unchanged
          </button>
        </div>
      ) : null}
    </section>
  );
}

function ActivityItem({
  event,
  editorOpen,
  editorEvent,
  correctedOn,
  ownerLocalDate,
  controlsLocked,
  busy,
  confirmationFingerprint,
  editorContainerRef,
  onOpenEditor,
  onCorrectedOn,
  onConfirmationFingerprint,
  onCancelEditor,
  onSubmit,
}: {
  event: ApplicationActivityEvent;
  editorOpen: boolean;
  editorEvent: ApplicationActivityEvent | null;
  correctedOn: string;
  ownerLocalDate: string;
  controlsLocked: boolean;
  busy: boolean;
  confirmationFingerprint: string | null;
  editorContainerRef: RefObject<HTMLDivElement | null>;
  onOpenEditor: (
    event: ApplicationActivityEvent,
    trigger: HTMLButtonElement,
  ) => void;
  onCorrectedOn: (value: string) => void;
  onConfirmationFingerprint: (value: string | null) => void;
  onCancelEditor: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  const copy = activityCopy(event);
  const currentDate = resolvedDate(event);
  const corrections = orderedCorrections(event);
  const hasCorrections = corrections.length > 0;
  return (
    <li
      id={"activity-" + event.id}
      className="flex min-w-0 scroll-mt-6 gap-3"
    >
      <span
        aria-hidden="true"
        className={
          "mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full " +
          (hasCorrections ? "bg-amber-500" : "bg-indigo-500")
        }
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="break-words font-medium">{copy.title}</p>
          {hasCorrections ? (
            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-900 dark:bg-amber-950/50 dark:text-amber-200">
              Corrected
            </span>
          ) : null}
        </div>
        <p className="mt-1 text-sm leading-6 text-zinc-500">
          {copy.detail} · Recorded {formatDate(event.occurred_at)}
        </p>

        {isDatedMilestone(event) && currentDate ? (
          hasCorrections ? (
            <dl className="mt-3 grid gap-2 rounded-lg bg-zinc-50 p-3 text-sm sm:grid-cols-2 dark:bg-zinc-950/60">
              <div>
                <dt className="text-xs font-medium uppercase tracking-wide text-zinc-500">
                  Current saved date
                </dt>
                <dd className="mt-1 font-semibold">
                  {formatDateOnly(currentDate)}
                </dd>
              </div>
              <div>
                <dt className="text-xs font-medium uppercase tracking-wide text-zinc-500">
                  Originally recorded
                </dt>
                <dd className="mt-1 text-zinc-600 dark:text-zinc-400">
                  {formatDateOnly(event.effective_on!)} · Superseded
                </dd>
              </div>
            </dl>
          ) : (
            <p className="mt-2 text-sm font-medium text-zinc-700 dark:text-zinc-300">
              Saved date: {formatDateOnly(currentDate)}
            </p>
          )
        ) : null}

        {hasCorrections ? (
          <CorrectionHistory corrections={corrections} />
        ) : null}

        {isCorrectableMilestone(event) ? (
          <button
            type="button"
            disabled={controlsLocked}
            onClick={(clickEvent) =>
              onOpenEditor(event, clickEvent.currentTarget)
            }
            aria-expanded={editorOpen}
            aria-controls={"activity-correction-editor-" + event.id}
            className={secondaryButtonClasses + " mt-3 w-full sm:w-auto"}
          >
            Correct date
          </button>
        ) : event.event_type === "application_interviewing" &&
          event.interview_round_id ? (
          <p className="mt-3 text-xs leading-5 text-zinc-500">
            This date comes from the completed interview round and stays tied
            to that round&apos;s history.{" "}
            <a
              href="#interview-rounds"
              className="font-medium underline underline-offset-4"
            >
              Review interview rounds
            </a>
          </p>
        ) : null}

        {editorOpen && editorEvent && currentDate ? (
          <CorrectionEditor
            containerRef={editorContainerRef}
            event={editorEvent}
            currentDate={currentDate}
            correctedOn={correctedOn}
            ownerLocalDate={ownerLocalDate}
            controlsLocked={controlsLocked}
            busy={busy}
            confirmationFingerprint={confirmationFingerprint}
            onCorrectedOn={onCorrectedOn}
            onConfirmationFingerprint={onConfirmationFingerprint}
            onCancel={onCancelEditor}
            onSubmit={onSubmit}
          />
        ) : null}
      </div>
    </li>
  );
}

function CorrectionEditor({
  containerRef,
  event,
  currentDate,
  correctedOn,
  ownerLocalDate,
  controlsLocked,
  busy,
  confirmationFingerprint,
  onCorrectedOn,
  onConfirmationFingerprint,
  onCancel,
  onSubmit,
}: {
  containerRef: RefObject<HTMLDivElement | null>;
  event: ApplicationActivityEvent;
  currentDate: string;
  correctedOn: string;
  ownerLocalDate: string;
  controlsLocked: boolean;
  busy: boolean;
  confirmationFingerprint: string | null;
  onCorrectedOn: (value: string) => void;
  onConfirmationFingerprint: (value: string | null) => void;
  onCancel: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  const fingerprint = correctionFingerprint(
    event.id,
    currentDate,
    correctedOn,
  );
  const confirmed = confirmationFingerprint === fingerprint;
  const unchanged = correctedOn === currentDate;
  const hintId = "activity-correction-hint-" + event.id;
  return (
    <div
      ref={containerRef}
      id={"activity-correction-editor-" + event.id}
      className="mt-4 rounded-xl border border-amber-200 bg-amber-50/70 p-4 sm:p-5 dark:border-amber-900 dark:bg-amber-950/20"
    >
      <form onSubmit={onSubmit}>
        <fieldset disabled={controlsLocked} className="space-y-4">
          <legend className="font-semibold">
            Correct {milestoneName(event)} date
          </legend>
          <p
            id={hintId}
            className="text-sm leading-6 text-zinc-700 dark:text-zinc-300"
          >
            The current saved date is {formatDateOnly(currentDate)}. Saving
            adds a new immutable correction; it does not delete the original,
            change the application stage, or replace your current task.
          </p>
          <label className="block max-w-sm text-sm font-medium">
            Correct date
            <input
              type="date"
              value={correctedOn}
              max={ownerLocalDate}
              required
              aria-describedby={hintId}
              onChange={(changeEvent) =>
                onCorrectedOn(changeEvent.target.value)
              }
              className={inputClasses + " mt-2"}
            />
          </label>
          {unchanged ? (
            <p className="text-xs text-zinc-600 dark:text-zinc-400">
              Choose a different date to add a correction.
            </p>
          ) : null}
          <label className="flex items-start gap-3 rounded-lg border border-amber-200 bg-white p-3 text-sm leading-6 dark:border-amber-900 dark:bg-zinc-900">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(changeEvent) =>
                onConfirmationFingerprint(
                  changeEvent.target.checked ? fingerprint : null,
                )
              }
              className="mt-1 h-4 w-4 shrink-0"
            />
            <span>
              I confirm this corrected date is accurate. Keep every earlier
              value in the permanent Activity history.
            </span>
          </label>
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:flex-wrap">
            <button
              type="button"
              onClick={onCancel}
              className={secondaryButtonClasses + " w-full sm:w-auto"}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!correctedOn || unchanged || !confirmed}
              className={primaryButtonClasses + " w-full sm:w-auto"}
            >
              {busy ? "Saving correction…" : "Save corrected date"}
            </button>
          </div>
        </fieldset>
      </form>
    </div>
  );
}

function CorrectionHistory({
  corrections,
}: {
  corrections: ApplicationActivityEvent["corrections"];
}) {
  return (
    <div className="mt-3 rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
      <p className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
        Correction history · {corrections.length}
      </p>
      <ol className="mt-3 space-y-3">
        {corrections.map((correction, index) => {
          const current = index === corrections.length - 1;
          return (
            <li key={correction.id} className="text-sm leading-6">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">
                  Correction {correction.correction_number}
                </span>
                <span
                  className={
                    "rounded-full px-2 py-0.5 text-xs font-semibold " +
                    (current
                      ? "bg-emerald-100 text-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-200"
                      : "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300")
                  }
                >
                  {current ? "Current" : "Superseded"}
                </span>
              </div>
              <p className="mt-1 text-zinc-700 dark:text-zinc-300">
                Changed {formatDateOnly(correction.previous_effective_on)} to{" "}
                {formatDateOnly(correction.corrected_effective_on)}.
              </p>
              <p className="text-xs text-zinc-500">
                Recorded {formatDate(correction.recorded_at)}
              </p>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function orderedCorrections(
  event: ApplicationActivityEvent,
): ApplicationActivityEvent["corrections"] {
  return [...event.corrections].sort(
    (left, right) => left.correction_number - right.correction_number,
  );
}

function isDatedMilestone(event: ApplicationActivityEvent): boolean {
  return (
    event.event_type === "application_screening" ||
    event.event_type === "application_interviewing" ||
    event.event_type === "application_offer"
  );
}

function isCorrectableMilestone(event: ApplicationActivityEvent): boolean {
  if (!event.effective_on || !isDatedMilestone(event)) return false;
  return (
    event.event_type !== "application_interviewing" ||
    !event.interview_round_id
  );
}

function resolvedDate(event: ApplicationActivityEvent): string | null {
  return event.resolved_effective_on ?? event.effective_on;
}

function correctionFingerprint(
  activityEventId: string,
  currentDate: string | null,
  correctedDate: string,
): string {
  return (
    activityEventId + ":" + (currentDate ?? "missing") + ":" + correctedDate
  );
}

function activityCopy(event: ApplicationActivityEvent): {
  title: string;
  detail: string;
} {
  if (event.event_type === "application_ready_to_apply") {
    return {
      title: "Application materials marked ready",
      detail: "Entered Ready to apply",
    };
  }
  if (event.event_type === "application_applied") {
    return {
      title: "Manual application recorded",
      detail: "Entered Applied",
    };
  }
  if (event.event_type === "application_screening") {
    return {
      title: "Recruiter screen completed",
      detail: "Entered Screening",
    };
  }
  if (event.event_type === "application_interviewing") {
    return event.interview_round_id
      ? {
          title: "Interview round completed",
          detail: "Entered Interviewing after the completed round",
        }
      : {
          title: "Interview completed",
          detail: "Entered Interviewing",
        };
  }
  if (event.event_type === "application_offer") {
    return {
      title: "Offer received",
      detail: "Entered Offer",
    };
  }
  if (event.event_type === "application_closed") {
    return {
      title: "Application closed",
      detail: effectiveDateDetail(
        "Recorded a terminal outcome",
        event.effective_on,
      ),
    };
  }
  if (event.event_type !== "application_created") {
    return {
      title: "Application updated",
      detail: "Recorded a durable application change",
    };
  }
  return {
    title: "Application started",
    detail: "Entered Pursuing",
  };
}

function milestoneName(event: ApplicationActivityEvent): string {
  if (event.event_type === "application_screening") {
    return "recruiter screen";
  }
  if (event.event_type === "application_offer") return "offer";
  return "interview";
}

function effectiveDateDetail(prefix: string, value: string | null): string {
  return value ? prefix + " on " + formatDateOnly(value) : prefix;
}

function formatDateOnly(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(year, month - 1, day, 12);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}

function correctionMatches(
  detail: ApplicationDetailResponse,
  request: PendingCorrection,
): boolean {
  const event = detail.activity.find(
    (item) => item.id === request.activityEventId,
  );
  if (!event) return false;
  const previousCorrectionId = request.knownCorrectionIds.at(-1) ?? null;
  const expectedCorrectionNumber = request.knownCorrectionIds.length + 1;
  return event.corrections.some(
    (correction) =>
      !request.knownCorrectionIds.includes(correction.id) &&
      correction.correction_number === expectedCorrectionNumber &&
      correction.supersedes_correction_id === previousCorrectionId &&
      correction.corrected_effective_on ===
        request.payload.corrected_effective_on,
  );
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
