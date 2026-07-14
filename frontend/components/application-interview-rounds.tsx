"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  createApplicationInterviewRound,
  getApplicationInterviewRounds,
  recordApplicationInterviewRoundEvent,
} from "@/lib/application-api";
import type {
  ApplicationInterviewRoundsResponse,
  InterviewHistoryState,
  InterviewCancellationParty,
  InterviewMeetingFormat,
  InterviewRoundCreate,
  InterviewRoundEventCreate,
  InterviewRoundKind,
  InterviewRoundMutationResponse,
  InterviewRoundResponse,
} from "@/lib/application-interview-types";
import type { ApplicationStage } from "@/lib/application-types";
import {
  createIdempotencyKey,
  WorkspaceApiError,
} from "@/lib/workspace-api";
import {
  errorText,
  inputClasses,
  primaryButtonClasses,
  secondaryButtonClasses,
  StatusMessage,
} from "./workspace-ui";

type Editor = "schedule" | "reschedule" | "complete" | "cancel" | null;

type PendingMutation =
  | {
      intent: "schedule";
      key: string;
      fingerprint: string;
      expectedVersion: number;
      knownRoundIds: string[];
      payload: InterviewRoundCreate;
    }
  | {
      intent: "event";
      key: string;
      fingerprint: string;
      expectedVersion: number;
      roundId: string;
      payload: InterviewRoundEventCreate;
    };

const KIND_OPTIONS: Array<{
  value: InterviewRoundKind;
  label: string;
  defaultTitle: string;
}> = [
  { value: "hiring_manager", label: "Hiring manager", defaultTitle: "Hiring manager interview" },
  { value: "technical", label: "Technical", defaultTitle: "Technical interview" },
  { value: "system_design", label: "System design", defaultTitle: "System design interview" },
  { value: "behavioral", label: "Behavioral", defaultTitle: "Behavioral interview" },
  { value: "case_study", label: "Case study", defaultTitle: "Case study interview" },
  { value: "panel", label: "Panel", defaultTitle: "Panel interview" },
  { value: "final", label: "Final", defaultTitle: "Final interview" },
  { value: "other", label: "Other", defaultTitle: "Interview round" },
];

const FORMAT_OPTIONS: Array<{ value: InterviewMeetingFormat; label: string }> = [
  { value: "video", label: "Video" },
  { value: "phone", label: "Phone" },
  { value: "onsite", label: "Onsite" },
  { value: "unspecified", label: "Not specified" },
];

const CANCELLATION_OPTIONS: Array<{
  value: InterviewCancellationParty;
  label: string;
}> = [
  { value: "employer", label: "Employer" },
  { value: "candidate", label: "Me" },
  { value: "mutual", label: "Mutual" },
  { value: "unknown", label: "Not specified" },
];

export function ApplicationInterviewRounds({
  applicationId,
  applicationVersion,
  applicationStage,
  ownerLocalDate,
  ownerTimezone,
  onApplicationChanged,
  onHistoryChanged,
}: {
  applicationId: string;
  applicationVersion: number;
  applicationStage: ApplicationStage;
  ownerLocalDate: string;
  ownerTimezone: string;
  onApplicationChanged: () => Promise<boolean>;
  onHistoryChanged: (state: InterviewHistoryState) => void;
}) {
  const [projection, setProjection] = useState<ApplicationInterviewRoundsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [hasPending, setHasPending] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editor, setEditor] = useState<Editor>(null);
  const [historyExpanded, setHistoryExpanded] = useState(false);

  const [kind, setKind] = useState<InterviewRoundKind>("hiring_manager");
  const [title, setTitle] = useState("Hiring manager interview");
  const [scheduledLocal, setScheduledLocal] = useState(
    `${addBusinessDays(ownerLocalDate, 2)}T10:00`,
  );
  const [scheduledTimezone, setScheduledTimezone] = useState(ownerTimezone);
  const [durationMinutes, setDurationMinutes] = useState("60");
  const [meetingFormat, setMeetingFormat] = useState<InterviewMeetingFormat>("video");
  const [preparationDueOn, setPreparationDueOn] = useState(ownerLocalDate);
  const [scheduleConfirmed, setScheduleConfirmed] = useState(false);

  const [completedOn, setCompletedOn] = useState(ownerLocalDate);
  const [followUpDueOn, setFollowUpDueOn] = useState(addBusinessDays(ownerLocalDate, 1));
  const [completeConfirmed, setCompleteConfirmed] = useState(false);

  const [cancelledOn, setCancelledOn] = useState(ownerLocalDate);
  const [cancelledBy, setCancelledBy] = useState<InterviewCancellationParty>("unknown");
  const [nextDecisionDueOn, setNextDecisionDueOn] = useState(ownerLocalDate);
  const [cancelConfirmed, setCancelConfirmed] = useState(false);

  const projectionRef = useRef<ApplicationInterviewRoundsResponse | null>(null);
  const pendingRef = useRef<PendingMutation | null>(null);
  const generationRef = useRef(0);
  const editorContainerRef = useRef<HTMLDivElement | null>(null);
  const editorTriggerRef = useRef<HTMLButtonElement | null>(null);
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const previousEditorRef = useRef<Editor>(null);
  const shouldLoad = canApplicationHaveRounds(applicationStage);

  const acceptProjection = useCallback((next: ApplicationInterviewRoundsResponse) => {
    if (next.application.id !== applicationId) return false;
    const previous = projectionRef.current;
    if (previous && next.application.version < previous.application.version) return false;
    projectionRef.current = next;
    setProjection(next);
    onHistoryChanged(next.rounds.length > 0 ? "recorded" : "none");
    setLoadError(null);
    return true;
  }, [applicationId, onHistoryChanged]);

  const refresh = useCallback(async (
    showLoading = false,
    synchronizeApplication = false,
  ) => {
    if (!shouldLoad) return null;
    const generation = generationRef.current;
    if (showLoading) {
      setLoading(true);
      onHistoryChanged(
        projectionRef.current?.rounds.length ? "recorded" : "checking",
      );
    }
    try {
      const next = await getApplicationInterviewRounds(applicationId);
      if (generationRef.current !== generation || !acceptProjection(next)) return null;
      if (synchronizeApplication) {
        const synchronized = await onApplicationChanged();
        if (!synchronized && generationRef.current === generation) {
          setLoadError(
            "Interview rounds are current, but the rest of this dossier could not be refreshed.",
          );
          onHistoryChanged(
            next.rounds.length > 0 ? "recorded" : "unavailable",
          );
        }
      }
      return next;
    } catch (reason) {
      if (generationRef.current === generation) {
        setLoadError(errorText(reason, "Unable to load interview rounds."));
        onHistoryChanged(
          projectionRef.current?.rounds.length ? "recorded" : "unavailable",
        );
      }
      return null;
    } finally {
      if (showLoading && generationRef.current === generation) setLoading(false);
    }
  }, [acceptProjection, applicationId, onApplicationChanged, onHistoryChanged, shouldLoad]);

  useEffect(() => {
    if (!shouldLoad) {
      onHistoryChanged("none");
      const timer = setTimeout(() => setLoading(false), 0);
      return () => clearTimeout(timer);
    }
    const timer = setTimeout(() => void refresh(true), 0);
    return () => clearTimeout(timer);
  }, [applicationVersion, onHistoryChanged, refresh, shouldLoad]);

  useEffect(() => () => {
    generationRef.current += 1;
  }, [applicationId]);

  useEffect(() => {
    const previousEditor = previousEditorRef.current;
    previousEditorRef.current = editor;
    if (editor) {
      const timer = setTimeout(() => {
        editorContainerRef.current
          ?.querySelector<HTMLElement>("input, select, textarea, button")
          ?.focus();
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
  }, [editor]);

  if (!shouldLoad) return null;
  if (loading && !projection) return <InterviewRoundsSkeleton />;
  if (!projection) {
    return (
      <section id="interview-rounds" aria-labelledby="interview-rounds-error-title">
        <StatusMessage kind="error">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span id="interview-rounds-error-title">
              {loadError ?? "Interview rounds are unavailable."}
            </span>
            <button
              type="button"
              onClick={() => void refresh(true, true)}
              className={secondaryButtonClasses}
            >
              Try again
            </button>
          </div>
        </StatusMessage>
      </section>
    );
  }

  const durableProjection = projection;
  const durableStage = durableProjection.application.stage;
  const activeRound = durableProjection.rounds.find((round) => round.status === "scheduled") ?? null;
  const history = durableProjection.rounds.filter((round) => round.status !== "scheduled").reverse();
  const visibleHistory = historyExpanded ? history : history.slice(0, 3);
  const canSchedule = canScheduleRound(durableStage) && activeRound === null;
  const controlsLocked = busy || hasPending || loading;

  function acceptMutation(result: InterviewRoundMutationResponse) {
    const current = projectionRef.current;
    if (!current || result.application.id !== applicationId) return;
    const rounds = [
      ...current.rounds.filter((round) => round.id !== result.round.id),
      result.round,
    ].sort((left, right) => left.round_number - right.round_number);
    acceptProjection({
      data_source: "database",
      application: result.application,
      rounds,
    });
  }

  function clearPending() {
    pendingRef.current = null;
    setHasPending(false);
  }

  async function executeMutation(request: PendingMutation) {
    if (request.intent === "schedule") {
      return createApplicationInterviewRound(
        applicationId,
        request.expectedVersion,
        request.key,
        request.payload,
      );
    }
    return recordApplicationInterviewRoundEvent(
      applicationId,
      request.roundId,
      request.expectedVersion,
      request.key,
      request.payload,
    );
  }

  async function runMutation(request: PendingMutation) {
    if (busy) return;
    const existing = pendingRef.current;
    if (existing && existing.fingerprint !== request.fingerprint) {
      setActionError("Retry the unchanged pending interview update or check its saved state before changing details.");
      return;
    }
    const exactRequest = existing ?? request;
    pendingRef.current = exactRequest;
    setHasPending(true);
    setBusy(true);
    setActionError(null);
    setNotice(null);
    try {
      const result = await executeMutation(exactRequest);
      acceptMutation(result);
      clearPending();
      setEditor(null);
      resetConfirmations();
      const synchronized = await onApplicationChanged();
      if (!synchronized) {
        setActionError(
          "The interview update is saved, but the rest of this dossier could not be refreshed. Use Refresh rounds to retry the dossier refresh.",
        );
      }
      setNotice(mutationSuccessCopy(exactRequest));
    } catch (reason) {
      const saved = await refresh(false);
      const apiError = reason instanceof WorkspaceApiError ? reason : null;
      const ambiguous = !apiError || apiError.retryable || apiError.code === "mutation_pending";
      if (saved && mutationMatches(saved, exactRequest)) {
        clearPending();
        setEditor(null);
        resetConfirmations();
        const synchronized = await onApplicationChanged();
        if (!synchronized) {
          setActionError(
            "The interview update is saved, but the rest of this dossier could not be refreshed. Use Refresh rounds to retry the dossier refresh.",
          );
        }
        setNotice("The exact interview update was confirmed from the durable record.");
      } else if (saved && resourceChanged(saved, exactRequest)) {
        clearPending();
        const synchronized = await onApplicationChanged();
        setActionError(
          synchronized
            ? "This interview or application changed elsewhere. Review the saved round before continuing."
            : "This interview or application changed elsewhere, and the rest of this dossier could not be refreshed. Review the saved round and use Refresh rounds before continuing.",
        );
      } else if (!ambiguous) {
        clearPending();
        setActionError(errorText(reason, "The interview update was rejected."));
      } else {
        setActionError(
          `${errorText(reason, "The interview update is not yet confirmed.")} ` +
          "Your exact request is retained; retry it unchanged or check saved state.",
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
    setActionError(null);
    try {
      const saved = await refresh(false);
      if (saved && mutationMatches(saved, request)) {
        clearPending();
        setEditor(null);
        resetConfirmations();
        const synchronized = await onApplicationChanged();
        if (!synchronized) {
          setActionError(
            "The interview update is saved, but the rest of this dossier could not be refreshed. Use Refresh rounds to retry the dossier refresh.",
          );
        }
        setNotice("The exact interview update was confirmed from the durable record.");
      } else if (saved && resourceChanged(saved, request)) {
        clearPending();
        const synchronized = await onApplicationChanged();
        setActionError(
          synchronized
            ? "A different interview update is already saved. Review it before continuing."
            : "A different interview update is already saved, and the rest of this dossier could not be refreshed. Review the saved round and use Refresh rounds before continuing.",
        );
      } else {
        setActionError("This exact update is not visible yet. Retry the unchanged request with its original safe receipt.");
      }
    } finally {
      setBusy(false);
    }
  }

  function resetConfirmations() {
    setScheduleConfirmed(false);
    setCompleteConfirmed(false);
    setCancelConfirmed(false);
  }

  function openSchedule(trigger: HTMLButtonElement) {
    editorTriggerRef.current = trigger;
    setKind("hiring_manager");
    setTitle("Hiring manager interview");
    setScheduledLocal(`${addBusinessDays(ownerLocalDate, 2)}T10:00`);
    setEditor("schedule");
    setScheduledTimezone(ownerTimezone);
    setDurationMinutes("60");
    setMeetingFormat("video");
    setPreparationDueOn(ownerLocalDate);
    setScheduleConfirmed(false);
    setActionError(null);
  }

  function openRoundEditor(
    nextEditor: Exclude<Editor, "schedule" | null>,
    round: InterviewRoundResponse,
    trigger: HTMLButtonElement,
  ) {
    editorTriggerRef.current = trigger;
    setEditor(nextEditor);
    setActionError(null);
    if (nextEditor === "reschedule") {
      setScheduledLocal(toDateTimeLocal(round.scheduled_start_at, round.scheduled_timezone));
      setScheduledTimezone(round.scheduled_timezone);
      setDurationMinutes(String(round.duration_minutes));
      setMeetingFormat(round.meeting_format);
      const savedDueOn = durableProjection.application.current_action?.due_on;
      setPreparationDueOn(
        savedDueOn && savedDueOn > ownerLocalDate ? savedDueOn : ownerLocalDate,
      );
      setScheduleConfirmed(false);
    } else if (nextEditor === "complete") {
      setCompletedOn(ownerLocalDate);
      setFollowUpDueOn(addBusinessDays(ownerLocalDate, 1));
      setCompleteConfirmed(false);
    } else {
      setCancelledOn(ownerLocalDate);
      setCancelledBy("unknown");
      setNextDecisionDueOn(ownerLocalDate);
      setCancelConfirmed(false);
    }
  }

  function scheduleRound() {
    const payload: InterviewRoundCreate = {
      kind,
      title: title.trim(),
      scheduled_local: scheduledLocal,
      scheduled_timezone: scheduledTimezone.trim(),
      duration_minutes: Number(durationMinutes),
      meeting_format: meetingFormat,
      next_action_due_on: preparationDueOn,
      confirm_schedule: true,
    };
    void runMutation({
      intent: "schedule",
      key: createIdempotencyKey(`interview-round:${applicationId}:schedule`),
      fingerprint: JSON.stringify(payload),
      expectedVersion: durableProjection.application.version,
      knownRoundIds: durableProjection.rounds.map((round) => round.id),
      payload,
    });
  }

  function recordRoundEvent(round: InterviewRoundResponse, payload: InterviewRoundEventCreate) {
    void runMutation({
      intent: "event",
      key: createIdempotencyKey(`interview-round:${round.id}:${payload.event_type}`),
      fingerprint: `${round.id}:${JSON.stringify(payload)}`,
      expectedVersion: round.version,
      roundId: round.id,
      payload,
    });
  }

  return (
    <section
      id="interview-rounds"
      aria-labelledby="interview-rounds-title"
      aria-busy={busy || loading}
      className="scroll-mt-6 rounded-2xl border border-violet-200 bg-white p-5 shadow-sm sm:p-7 dark:border-violet-900 dark:bg-zinc-900/70"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-violet-700 dark:text-violet-300">
            Interview operations
          </p>
          <h2
            ref={headingRef}
            id="interview-rounds-title"
            tabIndex={-1}
            className="mt-2 text-xl font-semibold"
          >
            Interview rounds
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
            Keep each confirmed appointment, schedule change, completion, and cancellation tied to one stable round.
          </p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          <button
            type="button"
            disabled={controlsLocked || loading}
            onClick={() => {
              setNotice(null);
              setActionError(null);
              void refresh(true, true);
            }}
            className={secondaryButtonClasses}
          >
            {loading ? "Refreshing…" : "Refresh rounds"}
          </button>
          {canSchedule ? (
            <button
              type="button"
              disabled={controlsLocked || loading}
              onClick={(event) => openSchedule(event.currentTarget)}
              aria-expanded={editor === "schedule"}
              aria-controls="interview-round-editor"
              className={primaryButtonClasses}
            >
              Schedule interview
            </button>
          ) : null}
        </div>
      </div>

      {loadError ? <div className="mt-4"><StatusMessage kind="error">{loadError} Saved rounds remain below.</StatusMessage></div> : null}
      {actionError ? <div className="mt-4"><StatusMessage kind="error">{actionError}</StatusMessage></div> : null}
      {notice ? <div className="mt-4"><StatusMessage kind="success">{notice}</StatusMessage></div> : null}

      {activeRound ? (
        <div className="mt-5">
          <h3 className="text-sm font-semibold text-zinc-700 dark:text-zinc-300">Next scheduled round</h3>
          <RoundCard
            round={activeRound}
            active
            activeEditor={editor === "schedule" ? null : editor}
            controlsLocked={controlsLocked}
            ownerTimezone={ownerTimezone}
            onEdit={(next, trigger) => openRoundEditor(next, activeRound, trigger)}
          />
        </div>
      ) : durableProjection.rounds.length === 0 ? (
        <p className="mt-5 rounded-xl border border-dashed border-zinc-300 p-4 text-sm leading-6 text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
          No interview round is recorded yet. Schedule only after the employer confirms an appointment.
        </p>
      ) : canScheduleRound(durableStage) ? (
        <p className="mt-5 rounded-xl bg-zinc-50 p-4 text-sm text-zinc-700 dark:bg-zinc-950/60 dark:text-zinc-300">
          No round is currently scheduled. Add the next confirmed appointment when you receive it.
        </p>
      ) : (
        <p className="mt-5 rounded-xl bg-zinc-50 p-4 text-sm text-zinc-700 dark:bg-zinc-950/60 dark:text-zinc-300">
          Interview history is read-only after an offer or final application outcome.
        </p>
      )}

      {editor ? (
        <div
          ref={editorContainerRef}
          id="interview-round-editor"
          className="mt-5 rounded-xl border border-violet-200 bg-violet-50/60 p-4 sm:p-5 dark:border-violet-900 dark:bg-violet-950/20"
        >
          {editor === "schedule" ? (
            <AppointmentForm
              legend="Schedule a confirmed interview"
              kind={kind}
              title={title}
              scheduledLocal={scheduledLocal}
              scheduledTimezone={scheduledTimezone}
              durationMinutes={durationMinutes}
              meetingFormat={meetingFormat}
              preparationDueOn={preparationDueOn}
              ownerLocalDate={ownerLocalDate}
              confirmed={scheduleConfirmed}
              controlsLocked={controlsLocked}
              submitLabel={busy ? "Scheduling…" : "Schedule interview"}
              onKind={(next) => {
                const previousDefault = KIND_OPTIONS.find((option) => option.value === kind)?.defaultTitle;
                setKind(next);
                if (!title.trim() || title === previousDefault) {
                  setTitle(KIND_OPTIONS.find((option) => option.value === next)?.defaultTitle ?? "Interview round");
                }
              }}
              onTitle={setTitle}
              onScheduledLocal={setScheduledLocal}
              onScheduledTimezone={setScheduledTimezone}
              onDurationMinutes={setDurationMinutes}
              onMeetingFormat={setMeetingFormat}
              onPreparationDueOn={setPreparationDueOn}
              onConfirmed={setScheduleConfirmed}
              onCancel={() => setEditor(null)}
              onSubmit={scheduleRound}
            />
          ) : editor === "reschedule" && activeRound ? (
            <RescheduleForm
              scheduledLocal={scheduledLocal}
              scheduledTimezone={scheduledTimezone}
              durationMinutes={durationMinutes}
              meetingFormat={meetingFormat}
              preparationDueOn={preparationDueOn}
              ownerLocalDate={ownerLocalDate}
              confirmed={scheduleConfirmed}
              controlsLocked={controlsLocked}
              onScheduledLocal={setScheduledLocal}
              onScheduledTimezone={setScheduledTimezone}
              onDurationMinutes={setDurationMinutes}
              onMeetingFormat={setMeetingFormat}
              onPreparationDueOn={setPreparationDueOn}
              onConfirmed={setScheduleConfirmed}
              onCancel={() => setEditor(null)}
              onSubmit={() => recordRoundEvent(activeRound, {
                event_type: "rescheduled",
                scheduled_local: scheduledLocal,
                scheduled_timezone: scheduledTimezone.trim(),
                duration_minutes: Number(durationMinutes),
                meeting_format: meetingFormat,
                next_action_due_on: preparationDueOn,
                confirm_reschedule: true,
              })}
            />
          ) : editor === "complete" && activeRound ? (
            <CompleteForm
              completedOn={completedOn}
              followUpDueOn={followUpDueOn}
              ownerLocalDate={ownerLocalDate}
              completedOnMin={ownerDateFromInstant(
                activeRound.scheduled_start_at,
                ownerTimezone,
              )}
              confirmed={completeConfirmed}
              controlsLocked={controlsLocked}
              onCompletedOn={setCompletedOn}
              onFollowUpDueOn={setFollowUpDueOn}
              onConfirmed={setCompleteConfirmed}
              onCancel={() => setEditor(null)}
              onSubmit={() => recordRoundEvent(activeRound, {
                event_type: "completed",
                completed_on: completedOn,
                next_action_due_on: followUpDueOn,
                confirm_complete: true,
              })}
            />
          ) : editor === "cancel" && activeRound ? (
            <CancelForm
              cancelledOn={cancelledOn}
              cancelledBy={cancelledBy}
              nextDecisionDueOn={nextDecisionDueOn}
              ownerLocalDate={ownerLocalDate}
              confirmed={cancelConfirmed}
              controlsLocked={controlsLocked}
              onCancelledOn={setCancelledOn}
              onCancelledBy={setCancelledBy}
              onNextDecisionDueOn={setNextDecisionDueOn}
              onConfirmed={setCancelConfirmed}
              onCancel={() => setEditor(null)}
              onSubmit={() => recordRoundEvent(activeRound, {
                event_type: "cancelled",
                cancelled_on: cancelledOn,
                cancelled_by: cancelledBy,
                next_action_due_on: nextDecisionDueOn,
                confirm_cancel: true,
              })}
            />
          ) : null}
        </div>
      ) : null}

      {hasPending && !busy ? (
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <button type="button" onClick={() => void checkSavedState()} className={secondaryButtonClasses}>
            Check saved state
          </button>
          <button
            type="button"
            onClick={() => {
              const pending = pendingRef.current;
              if (pending) void runMutation(pending);
            }}
            className={secondaryButtonClasses}
          >
            Retry unchanged
          </button>
        </div>
      ) : null}

      {history.length > 0 ? (
        <div className="mt-6 border-t border-zinc-200 pt-5 dark:border-zinc-800">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 id="interview-history-title" className="text-sm font-semibold">Completed and cancelled rounds</h3>
            <span className="text-xs text-zinc-500">{history.length} round{history.length === 1 ? "" : "s"}</span>
          </div>
          <div id="interview-round-history" className="mt-3 space-y-3">
            {visibleHistory.map((round) => (
              <RoundCard
                key={round.id}
                round={round}
                ownerTimezone={ownerTimezone}
              />
            ))}
          </div>
          {history.length > 3 ? (
            <button
              type="button"
              onClick={() => setHistoryExpanded((current) => !current)}
              aria-expanded={historyExpanded}
              aria-controls="interview-round-history"
              className="mt-3 text-sm font-medium text-violet-700 underline underline-offset-4 dark:text-violet-300"
            >
              {historyExpanded ? "Show fewer rounds" : `Show ${history.length - 3} more rounds`}
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function RoundCard({
  round,
  ownerTimezone,
  active = false,
  activeEditor = null,
  controlsLocked = false,
  onEdit,
}: {
  round: InterviewRoundResponse;
  ownerTimezone: string;
  active?: boolean;
  activeEditor?: "reschedule" | "complete" | "cancel" | null;
  controlsLocked?: boolean;
  onEdit?: (
    editor: "reschedule" | "complete" | "cancel",
    trigger: HTMLButtonElement,
  ) => void;
}) {
  const completionAvailable = useCompletionAvailability(
    round.scheduled_start_at,
    active,
  );
  const completionHintId = `interview-completion-${round.id}`;
  return (
    <article className="mt-2 rounded-xl border border-zinc-200 bg-zinc-50 p-4 dark:border-zinc-800 dark:bg-zinc-950/50">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={roundStatusClasses(round.status)}>{roundStatusLabel(round.status)}</span>
            <span className="text-xs text-zinc-500">Round {round.round_number} · {kindLabel(round.kind)}</span>
          </div>
          <h4 className="mt-2 break-words font-semibold">{round.title}</h4>
          <p className="mt-1 text-sm text-zinc-700 dark:text-zinc-300">
            {formatScheduledAt(round.scheduled_start_at, round.scheduled_timezone)}
          </p>
          <p className="mt-1 text-xs text-zinc-500">
            {round.duration_minutes} minutes · {meetingFormatLabel(round.meeting_format)} · {round.scheduled_timezone}
          </p>
          {round.completed_on ? (
            <p className="mt-2 text-xs font-medium text-emerald-700 dark:text-emerald-300">
              Completed {formatDateOnly(round.completed_on)}
            </p>
          ) : round.cancelled_on ? (
            <p className="mt-2 text-xs font-medium text-zinc-600 dark:text-zinc-400">
              Cancelled {formatDateOnly(round.cancelled_on)} by {cancellationPartyLabel(round.cancelled_by)}
            </p>
          ) : null}
        </div>
      </div>
      {active && onEdit ? (
        <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          <button
            type="button"
            disabled={controlsLocked}
            onClick={(event) => onEdit("reschedule", event.currentTarget)}
            aria-expanded={activeEditor === "reschedule"}
            aria-controls="interview-round-editor"
            className={secondaryButtonClasses}
          >
            Reschedule
          </button>
          <button
            type="button"
            disabled={controlsLocked || !completionAvailable}
            onClick={(event) => onEdit("complete", event.currentTarget)}
            aria-expanded={activeEditor === "complete"}
            aria-controls="interview-round-editor"
            aria-describedby={!completionAvailable ? completionHintId : undefined}
            className={primaryButtonClasses}
          >
            Mark completed
          </button>
          <button
            type="button"
            disabled={controlsLocked}
            onClick={(event) => onEdit("cancel", event.currentTarget)}
            aria-expanded={activeEditor === "cancel"}
            aria-controls="interview-round-editor"
            className={secondaryButtonClasses}
          >
            Cancel round
          </button>
        </div>
      ) : null}
      {active && !completionAvailable ? (
        <p id={completionHintId} className="mt-3 text-xs leading-5 text-zinc-500">
          Completion becomes available after this round starts on {formatScheduledAt(
            round.scheduled_start_at,
            round.scheduled_timezone,
          )}.
        </p>
      ) : null}
      {round.events.length > 1 ? (
        <details className="mt-4 border-t border-zinc-200 pt-3 text-xs dark:border-zinc-800">
          <summary className="cursor-pointer font-medium text-zinc-600 dark:text-zinc-400">
            View {round.events.length} saved round events
          </summary>
          <ol className="mt-3 space-y-2 text-zinc-600 dark:text-zinc-400">
            {round.events.map((event) => (
              <li key={event.id}>
                <span className="font-medium text-zinc-800 dark:text-zinc-200">{eventLabel(event.event_type)}</span>
                {" · "}{eventDetail(event)}
                <span className="mt-0.5 block text-zinc-500">
                  Recorded {formatScheduledAt(event.occurred_at, ownerTimezone)}
                </span>
              </li>
            ))}
          </ol>
        </details>
      ) : null}
    </article>
  );
}

function useCompletionAvailability(scheduledStartAt: string, active: boolean): boolean {
  const [clock, setClock] = useState(() => Date.now());
  const scheduledAt = new Date(scheduledStartAt).getTime();

  useEffect(() => {
    if (!active || Number.isNaN(scheduledAt) || scheduledAt <= clock) return;
    const delay = Math.min(scheduledAt - clock + 100, 60 * 60 * 1_000);
    const timer = setTimeout(() => setClock(Date.now()), delay);
    return () => clearTimeout(timer);
  }, [active, clock, scheduledAt]);

  return !Number.isNaN(scheduledAt) && clock >= scheduledAt;
}

function AppointmentForm({
  legend,
  kind,
  title,
  scheduledLocal,
  scheduledTimezone,
  durationMinutes,
  meetingFormat,
  preparationDueOn,
  ownerLocalDate,
  confirmed,
  controlsLocked,
  submitLabel,
  showIdentity = true,
  onKind,
  onTitle,
  onScheduledLocal,
  onScheduledTimezone,
  onDurationMinutes,
  onMeetingFormat,
  onPreparationDueOn,
  onConfirmed,
  onCancel,
  onSubmit,
}: {
  legend: string;
  kind: InterviewRoundKind;
  title: string;
  scheduledLocal: string;
  scheduledTimezone: string;
  durationMinutes: string;
  meetingFormat: InterviewMeetingFormat;
  preparationDueOn: string;
  ownerLocalDate: string;
  confirmed: boolean;
  controlsLocked: boolean;
  submitLabel: string;
  showIdentity?: boolean;
  onKind: (value: InterviewRoundKind) => void;
  onTitle: (value: string) => void;
  onScheduledLocal: (value: string) => void;
  onScheduledTimezone: (value: string) => void;
  onDurationMinutes: (value: string) => void;
  onMeetingFormat: (value: InterviewMeetingFormat) => void;
  onPreparationDueOn: (value: string) => void;
  onConfirmed: (value: boolean) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  const validDuration = Number(durationMinutes) >= 15 && Number(durationMinutes) <= 480;
  return (
    <form onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
      <fieldset disabled={controlsLocked} className="space-y-4">
        <legend className="font-semibold">{legend}</legend>
        <div className="grid gap-4 sm:grid-cols-2">
          {showIdentity ? (
            <>
              <label className="block text-sm font-medium">
                Round type
                <select value={kind} onChange={(event) => onKind(event.target.value as InterviewRoundKind)} className={`${inputClasses} mt-2`}>
                  {KIND_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
              </label>
              <label className="block text-sm font-medium">
                Round title
                <input value={title} maxLength={160} required onChange={(event) => onTitle(event.target.value)} className={`${inputClasses} mt-2`} />
              </label>
            </>
          ) : null}
          <label className="block text-sm font-medium">
            Scheduled date and time
            <input type="datetime-local" value={scheduledLocal} required onChange={(event) => onScheduledLocal(event.target.value)} className={`${inputClasses} mt-2`} />
            <span className="mt-1 block text-xs font-normal text-zinc-500">
              Enter the confirmed wall-clock time in the timezone below. It must
              resolve to a future instant; skipped or ambiguous daylight-saving
              times will be rejected safely.
            </span>
          </label>
          <label className="block text-sm font-medium">
            Timezone
            <input value={scheduledTimezone} maxLength={64} required onChange={(event) => onScheduledTimezone(event.target.value)} className={`${inputClasses} mt-2`} />
            <span className="mt-1 block text-xs font-normal text-zinc-500">Use an IANA timezone such as Asia/Kolkata.</span>
          </label>
          <label className="block text-sm font-medium">
            Duration in minutes
            <input type="number" min={15} max={480} value={durationMinutes} required onChange={(event) => onDurationMinutes(event.target.value)} className={`${inputClasses} mt-2`} />
          </label>
          <label className="block text-sm font-medium">
            Meeting format
            <select value={meetingFormat} onChange={(event) => onMeetingFormat(event.target.value as InterviewMeetingFormat)} className={`${inputClasses} mt-2`}>
              {FORMAT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
          <label className="block text-sm font-medium sm:col-span-2 sm:max-w-sm">
            Prepare by
            <input type="date" value={preparationDueOn} min={ownerLocalDate} required onChange={(event) => onPreparationDueOn(event.target.value)} className={`${inputClasses} mt-2`} />
          </label>
        </div>
        <Confirmation checked={confirmed} onChange={onConfirmed}>
          I am recording an employer-confirmed appointment in the timezone shown above.
        </Confirmation>
        <FormActions
          primaryLabel={submitLabel}
          disabled={!confirmed || (showIdentity && !title.trim()) || !scheduledLocal || !scheduledTimezone.trim() || !validDuration || !preparationDueOn}
          onCancel={onCancel}
        />
      </fieldset>
    </form>
  );
}

function RescheduleForm(props: Omit<Parameters<typeof AppointmentForm>[0], "kind" | "title" | "onKind" | "onTitle" | "legend" | "submitLabel">) {
  return (
    <AppointmentForm
      {...props}
      legend="Reschedule this round"
      kind="other"
      title="Existing interview round"
      onKind={() => undefined}
      onTitle={() => undefined}
      submitLabel="Save new schedule"
      showIdentity={false}
    />
  );
}

function CompleteForm({
  completedOn,
  followUpDueOn,
  ownerLocalDate,
  completedOnMin,
  confirmed,
  controlsLocked,
  onCompletedOn,
  onFollowUpDueOn,
  onConfirmed,
  onCancel,
  onSubmit,
}: {
  completedOn: string;
  followUpDueOn: string;
  ownerLocalDate: string;
  completedOnMin: string;
  confirmed: boolean;
  controlsLocked: boolean;
  onCompletedOn: (value: string) => void;
  onFollowUpDueOn: (value: string) => void;
  onConfirmed: (value: boolean) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  return (
    <form onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
      <fieldset disabled={controlsLocked} className="space-y-4">
        <legend className="font-semibold">Mark this round completed</legend>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block text-sm font-medium">
            Date completed
            <input type="date" value={completedOn} min={completedOnMin || undefined} max={ownerLocalDate} required onChange={(event) => onCompletedOn(event.target.value)} className={`${inputClasses} mt-2`} />
          </label>
          <label className="block text-sm font-medium">
            Follow-up due
            <input type="date" value={followUpDueOn} min={ownerLocalDate} required onChange={(event) => onFollowUpDueOn(event.target.value)} className={`${inputClasses} mt-2`} />
          </label>
        </div>
        <Confirmation checked={confirmed} onChange={onConfirmed}>
          I completed this interview round; recording it will replace the preparation task with a dated follow-up.
        </Confirmation>
        <FormActions primaryLabel="Record completed round" disabled={!confirmed || !completedOn || !followUpDueOn} onCancel={onCancel} />
      </fieldset>
    </form>
  );
}

function CancelForm({
  cancelledOn,
  cancelledBy,
  nextDecisionDueOn,
  ownerLocalDate,
  confirmed,
  controlsLocked,
  onCancelledOn,
  onCancelledBy,
  onNextDecisionDueOn,
  onConfirmed,
  onCancel,
  onSubmit,
}: {
  cancelledOn: string;
  cancelledBy: InterviewCancellationParty;
  nextDecisionDueOn: string;
  ownerLocalDate: string;
  confirmed: boolean;
  controlsLocked: boolean;
  onCancelledOn: (value: string) => void;
  onCancelledBy: (value: InterviewCancellationParty) => void;
  onNextDecisionDueOn: (value: string) => void;
  onConfirmed: (value: boolean) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  return (
    <form onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
      <fieldset disabled={controlsLocked} className="space-y-4">
        <legend className="font-semibold">Cancel this interview round</legend>
        <p className="text-sm leading-6 text-zinc-600 dark:text-zinc-400">
          Cancelling a round does not reject, withdraw, or close the application. It creates a dated task to decide the next step.
        </p>
        <div className="grid gap-4 sm:grid-cols-3">
          <label className="block text-sm font-medium">
            Date cancelled
            <input type="date" value={cancelledOn} max={ownerLocalDate} required onChange={(event) => onCancelledOn(event.target.value)} className={`${inputClasses} mt-2`} />
          </label>
          <label className="block text-sm font-medium">
            Cancelled by
            <select value={cancelledBy} onChange={(event) => onCancelledBy(event.target.value as InterviewCancellationParty)} className={`${inputClasses} mt-2`}>
              {CANCELLATION_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
          <label className="block text-sm font-medium">
            Next decision due
            <input type="date" value={nextDecisionDueOn} min={ownerLocalDate} required onChange={(event) => onNextDecisionDueOn(event.target.value)} className={`${inputClasses} mt-2`} />
          </label>
        </div>
        <Confirmation checked={confirmed} onChange={onConfirmed}>
          I want to cancel this round while keeping the application active.
        </Confirmation>
        <FormActions primaryLabel="Cancel interview round" disabled={!confirmed || !cancelledOn || !nextDecisionDueOn} onCancel={onCancel} destructive />
      </fieldset>
    </form>
  );
}

function Confirmation({ checked, onChange, children }: { checked: boolean; onChange: (value: boolean) => void; children: React.ReactNode }) {
  return (
    <label className="flex min-h-11 items-start gap-3 rounded-lg border border-zinc-200 bg-white p-3 text-sm dark:border-zinc-800 dark:bg-zinc-900">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="mt-0.5 h-5 w-5" />
      <span>{children}</span>
    </label>
  );
}

function FormActions({ primaryLabel, disabled, onCancel, destructive = false }: { primaryLabel: string; disabled: boolean; onCancel: () => void; destructive?: boolean }) {
  return (
    <div className="flex flex-col gap-2 sm:flex-row">
      <button type="submit" disabled={disabled} className={destructive ? destructiveButtonClasses : primaryButtonClasses}>
        {primaryLabel}
      </button>
      <button type="button" onClick={onCancel} className={secondaryButtonClasses}>Keep current details</button>
    </div>
  );
}

function InterviewRoundsSkeleton() {
  return (
    <section id="interview-rounds" aria-busy="true" aria-label="Loading interview rounds" className="scroll-mt-6 rounded-2xl border border-violet-200 bg-white p-5 shadow-sm sm:p-7 dark:border-violet-900 dark:bg-zinc-900/70">
      <p role="status" className="text-sm text-zinc-500">Loading saved interview rounds…</p>
      <div className="mt-4 h-24 animate-pulse rounded-xl bg-zinc-100 dark:bg-zinc-800" />
    </section>
  );
}

function mutationMatches(saved: ApplicationInterviewRoundsResponse, request: PendingMutation): boolean {
  if (request.intent === "schedule") {
    return saved.rounds.some((round) => (
      !request.knownRoundIds.includes(round.id) &&
      round.kind === request.payload.kind &&
      round.title === request.payload.title &&
      appointmentMatches(round, request.payload) &&
      actionMatches(saved, round.events[0]?.action_item_id, request.payload.next_action_due_on)
    ));
  }
  const round = saved.rounds.find((item) => item.id === request.roundId);
  if (!round || round.version <= request.expectedVersion) return false;
  const latest = round.events.at(-1);
  if (!latest || latest.event_type !== request.payload.event_type) return false;
  if (!actionMatches(saved, latest.action_item_id, request.payload.next_action_due_on)) return false;
  if (request.payload.event_type === "rescheduled") {
    return appointmentMatches(round, request.payload);
  }
  if (request.payload.event_type === "completed") {
    return round.status === "completed" && round.completed_on === request.payload.completed_on;
  }
  return round.status === "cancelled" &&
    round.cancelled_on === request.payload.cancelled_on &&
    round.cancelled_by === request.payload.cancelled_by;
}

function appointmentMatches(
  round: InterviewRoundResponse,
  payload: Pick<InterviewRoundCreate, "scheduled_local" | "scheduled_timezone" | "duration_minutes" | "meeting_format">,
): boolean {
  return round.scheduled_timezone === payload.scheduled_timezone &&
    round.duration_minutes === payload.duration_minutes &&
    round.meeting_format === payload.meeting_format &&
    toDateTimeLocal(round.scheduled_start_at, round.scheduled_timezone) === payload.scheduled_local;
}

function actionMatches(saved: ApplicationInterviewRoundsResponse, actionId: string | undefined, dueOn: string): boolean {
  return Boolean(
    actionId &&
    saved.application.current_action?.id === actionId &&
    saved.application.current_action.due_on === dueOn,
  );
}

function resourceChanged(saved: ApplicationInterviewRoundsResponse, request: PendingMutation): boolean {
  if (request.intent === "schedule") return saved.application.version !== request.expectedVersion;
  const round = saved.rounds.find((item) => item.id === request.roundId);
  return Boolean(round && round.version !== request.expectedVersion);
}

function mutationSuccessCopy(request: PendingMutation): string {
  if (request.intent === "schedule") return "Interview scheduled. Its preparation task is now in Today.";
  if (request.payload.event_type === "rescheduled") return "Interview rescheduled. Today now uses the new preparation date.";
  if (request.payload.event_type === "completed") return "Interview completed. A dated follow-up is now your current task.";
  return "Interview round cancelled. The application remains active with a dated next decision.";
}

function canApplicationHaveRounds(stage: ApplicationStage): boolean {
  return stage === "applied" || stage === "screening" || stage === "interviewing" || stage === "offer" || stage === "closed";
}

function canScheduleRound(stage: ApplicationStage): boolean {
  return stage === "applied" || stage === "screening" || stage === "interviewing";
}

function kindLabel(kind: InterviewRoundKind): string {
  return KIND_OPTIONS.find((option) => option.value === kind)?.label ?? "Other";
}

function meetingFormatLabel(format: InterviewMeetingFormat): string {
  return FORMAT_OPTIONS.find((option) => option.value === format)?.label ?? "Not specified";
}

function cancellationPartyLabel(value: InterviewCancellationParty | null): string {
  return CANCELLATION_OPTIONS.find((option) => option.value === value)?.label.toLowerCase() ?? "an unspecified party";
}

function roundStatusLabel(status: InterviewRoundResponse["status"]): string {
  return status === "scheduled" ? "Scheduled" : status === "completed" ? "Completed" : "Cancelled";
}

function roundStatusClasses(status: InterviewRoundResponse["status"]): string {
  const tone = status === "scheduled"
    ? "bg-violet-100 text-violet-900 dark:bg-violet-950 dark:text-violet-200"
    : status === "completed"
      ? "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200"
      : "bg-zinc-200 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-200";
  return `rounded-full px-2.5 py-1 text-xs font-semibold ${tone}`;
}

function eventLabel(event: InterviewRoundResponse["events"][number]["event_type"]): string {
  return ({
    scheduled: "Scheduled",
    rescheduled: "Rescheduled",
    completed: "Completed",
    cancelled: "Cancelled",
  } as const)[event];
}

function eventDetail(event: InterviewRoundResponse["events"][number]): string {
  if ((event.event_type === "completed" || event.event_type === "cancelled") && event.effective_on) {
    const party = event.event_type === "cancelled" && event.cancelled_by
      ? ` · by ${cancellationPartyLabel(event.cancelled_by)}`
      : "";
    return `Effective ${formatDateOnly(event.effective_on)}${party}`;
  }
  return formatScheduledAt(event.scheduled_start_at, event.scheduled_timezone);
}

function formatScheduledAt(value: string, timeZone: string): string {
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

function toDateTimeLocal(value: string, timeZone: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(date);
    const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? "";
    return `${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}`;
  } catch {
    return "";
  }
}

function ownerDateFromInstant(value: string, timeZone: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(date);
    const part = (type: Intl.DateTimeFormatPartTypes) =>
      parts.find((item) => item.type === type)?.value ?? "";
    return `${part("year")}-${part("month")}-${part("day")}`;
  } catch {
    return "";
  }
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

const destructiveButtonClasses =
  "inline-flex min-h-11 items-center justify-center rounded-lg bg-red-700 px-4 py-2 text-sm font-medium text-white hover:bg-red-800 disabled:cursor-not-allowed disabled:opacity-50";
