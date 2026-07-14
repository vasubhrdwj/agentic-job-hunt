"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  getApplicationOutreach,
  recordApplicationOutreachEvent,
  saveApplicationOutreachMessage,
  startApplicationOutreachSequence,
} from "@/lib/application-api";
import type { ApplicationPostingState } from "@/lib/application-types";
import type {
  ApplicationOutreachResponse,
  OutreachChannel,
  OutreachEventCreate,
  OutreachMessageCreate,
  OutreachMessageKind,
  OutreachMessageVersion,
  OutreachOutcome,
  OutreachRecipient,
  OutreachTimelineEvent,
} from "@/lib/outreach-types";
import { createIdempotencyKey, WorkspaceApiError } from "@/lib/workspace-api";
import {
  errorText,
  formatDate,
  inputClasses,
  primaryButtonClasses,
  secondaryButtonClasses,
  StatusMessage,
  textareaClasses,
} from "./workspace-ui";

const MAX_MESSAGE_CHARS = 4_000;
const MAX_REASON_CHARS = 100;

type DraftKey = `${string}:${OutreachMessageKind}`;
type ControlMode = "pause" | "resume" | "stop";

interface PendingIntent {
  receiptKey: string;
  fingerprint: string;
  expectedVersion: number;
  applied?: (response: ApplicationOutreachResponse) => boolean;
  successMessage?: string;
}

interface MutationOptions {
  intent: string;
  fingerprint: string;
  expectedVersion: number;
  execute: (pending: PendingIntent) => Promise<ApplicationOutreachResponse>;
  applied: (response: ApplicationOutreachResponse) => boolean;
  successMessage: string;
  ambiguousMessage: string;
}

type RecipientDeadline = OutreachRecipient;

export function ApplicationOutreach({
  applicationId,
  applicationVersion,
  postingState,
  benchReady,
  contactSearchRunning,
}: {
  applicationId: string;
  applicationVersion: number;
  postingState: ApplicationPostingState;
  benchReady: boolean;
  contactSearchRunning: boolean;
}) {
  const [outreach, setOutreach] = useState<ApplicationOutreachResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [unresolvedIntent, setUnresolvedIntent] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<DraftKey, string>>({});
  const [dirtyFlags, setDirtyFlags] = useState<Partial<Record<DraftKey, boolean>>>({});
  const [channels, setChannels] = useState<Record<string, OutreachChannel>>({});
  const [sendConfirmations, setSendConfirmations] = useState<Record<string, boolean>>({});
  const [outcomeChoices, setOutcomeChoices] = useState<Record<string, OutreachOutcome | "">>({});
  const [controlMode, setControlMode] = useState<ControlMode | null>(null);
  const [controlReason, setControlReason] = useState("");
  const [locallyCopied, setLocallyCopied] = useState<Record<string, boolean>>({});
  const [manualCopyFallback, setManualCopyFallback] = useState<Record<string, boolean>>({});

  const requestGeneration = useRef(0);
  const outreachRef = useRef<ApplicationOutreachResponse | null>(null);
  const draftValues = useRef<Record<DraftKey, string>>({});
  const dirtyDrafts = useRef(new Set<DraftKey>());
  const pendingIntents = useRef(new Map<string, PendingIntent>());

  useEffect(() => () => {
    requestGeneration.current += 1;
  }, []);

  const hydrateDrafts = useCallback((next: ApplicationOutreachResponse) => {
    setDrafts((current) => {
      const hydrated = { ...current };
      for (const recipient of next.recipients) {
        for (const kind of ["initial", "follow_up"] as const) {
          const key = draftKey(recipient.application_contact_id, kind);
          if (dirtyDrafts.current.has(key)) continue;
          const saved = messageFor(recipient, kind);
          hydrated[key] = saved?.body ?? hydrated[key] ?? "";
        }
      }
      draftValues.current = hydrated;
      return hydrated;
    });
  }, []);

  const acceptResponse = useCallback((
    next: ApplicationOutreachResponse,
    expectedApplicationId: string,
    generation: number,
  ) => {
    if (
      requestGeneration.current !== generation ||
      next.application_id !== expectedApplicationId
    ) return false;

    const current = outreachRef.current;
    if (current?.sequence) {
      if (!next.sequence) return false;
      if (next.sequence.id !== current.sequence.id) return false;
      if (next.sequence.version < current.sequence.version) return false;
    }

    outreachRef.current = next;
    setOutreach(next);
    setLoadError(null);
    hydrateDrafts(next);
    for (const [intent, pending] of pendingIntents.current) {
      if (pending.applied?.(next)) {
        pendingIntents.current.delete(intent);
        setUnresolvedIntent((currentIntent) =>
          currentIntent === intent ? null : currentIntent,
        );
        setActionError(null);
        if (pending.successMessage) {
          setNotice(`${pending.successMessage} Confirmed from the saved record.`);
        }
      } else if (
        next.sequence &&
        next.sequence.version > pending.expectedVersion
      ) {
        pendingIntents.current.delete(intent);
        setUnresolvedIntent((currentIntent) =>
          currentIntent === intent ? null : currentIntent,
        );
        setNotice(null);
        setActionError(
          "The plan changed and the older unconfirmed result is not present. Review the latest saved state before trying again.",
        );
      }
    }
    return true;
  }, [hydrateDrafts]);

  const refresh = useCallback(async (showLoading = false) => {
    const requestedApplicationId = applicationId;
    const generation = requestGeneration.current;
    if (showLoading) setLoading(true);
    try {
      const next = await getApplicationOutreach(requestedApplicationId);
      if (!acceptResponse(next, requestedApplicationId, generation)) return null;
      setLoadError(null);
      return next;
    } catch (reason) {
      if (requestGeneration.current !== generation) return null;
      setLoadError(errorText(reason, "Unable to load manual outreach."));
      return null;
    } finally {
      if (showLoading && requestGeneration.current === generation) setLoading(false);
    }
  }, [acceptResponse, applicationId]);

  useEffect(() => {
    const timer = setTimeout(() => void refresh(true), 0);
    return () => clearTimeout(timer);
  }, [refresh]);

  useEffect(() => {
    function refreshOnFocus() {
      if (!busy) void refresh(false);
    }
    window.addEventListener("focus", refreshOnFocus);
    return () => window.removeEventListener("focus", refreshOnFocus);
  }, [busy, refresh]);

  const getPendingIntent = useCallback((
    intent: string,
    fingerprint: string,
    expectedVersion: number,
  ) => {
    const existing = pendingIntents.current.get(intent);
    if (existing) {
      if (existing.fingerprint !== fingerprint) {
        throw new Error(
          "This action still has an unresolved attempt with different details. Retry the unchanged action before editing it.",
        );
      }
      return existing;
    }
    const created = {
      receiptKey: createIdempotencyKey(`outreach:${applicationId}:${intent}`),
      fingerprint,
      expectedVersion,
    };
    pendingIntents.current.set(intent, created);
    return created;
  }, [applicationId]);

  const runMutation = useCallback(async ({
    intent,
    fingerprint,
    expectedVersion,
    execute,
    applied,
    successMessage,
    ambiguousMessage,
  }: MutationOptions) => {
    if (busy) return false;
    const otherPending = [...pendingIntents.current.keys()].find((key) => key !== intent);
    if (otherPending) {
      setActionError(
        "One action still has an unconfirmed result. Retry that unchanged action before starting another one.",
      );
      return false;
    }
    const requestedApplicationId = applicationId;
    const generation = requestGeneration.current;
    let pending: PendingIntent;
    try {
      pending = getPendingIntent(intent, fingerprint, expectedVersion);
      pending.applied ??= applied;
      pending.successMessage ??= successMessage;
    } catch (reason) {
      setActionError(errorText(reason, "Review the latest saved outreach state."));
      return false;
    }

    setBusy(intent);
    setActionError(null);
    setNotice(null);
    try {
      const next = await execute(pending);
      if (requestGeneration.current !== generation) return false;
      const accepted = acceptResponse(next, requestedApplicationId, generation);
      const returnedApplied =
        next.application_id === requestedApplicationId && applied(next);
      const current = outreachRef.current;
      const currentApplied = Boolean(
        current?.application_id === requestedApplicationId && applied(current),
      );
      if (returnedApplied || currentApplied) {
        pendingIntents.current.delete(intent);
        setUnresolvedIntent((value) => value === intent ? null : value);
        setNotice(successMessage);
        return true;
      }
      if (!accepted) {
        setUnresolvedIntent(intent);
        setActionError(
          "A newer saved view arrived before this response, so the action is still unconfirmed. Retry the same unchanged action safely.",
        );
        return false;
      }
      pendingIntents.current.delete(intent);
      setUnresolvedIntent((currentValue) => currentValue === intent ? null : currentValue);
      if (!applied(next)) {
        setActionError(definitiveNonApplyMessage(next));
        return false;
      }
      return false;
    } catch (reason) {
      if (requestGeneration.current !== generation) return false;
      const apiError = reason instanceof WorkspaceApiError ? reason : null;

      if (apiError?.code === "version_conflict") {
        pendingIntents.current.delete(intent);
        setUnresolvedIntent((current) => current === intent ? null : current);
        await refresh(false);
        setActionError(
          "This outreach plan changed in another tab. The latest saved state is now shown; your unsaved message text is still here. Review it before trying again.",
        );
        return false;
      }
      if (apiError?.code === "idempotency_conflict") {
        pendingIntents.current.delete(intent);
        setUnresolvedIntent((current) => current === intent ? null : current);
        await refresh(false);
        setActionError(
          "That saved action receipt belongs to a different change. The latest state is shown; review it before trying again.",
        );
        return false;
      }

      const ambiguous =
        !apiError || apiError.retryable || apiError.code === "mutation_pending";
      if (ambiguous) {
        let reconciled: ApplicationOutreachResponse | null = null;
        try {
          const checked = await getApplicationOutreach(requestedApplicationId);
          if (acceptResponse(checked, requestedApplicationId, generation)) {
            reconciled = checked;
          }
        } catch {
          // The exact pending payload and receipt remain available for a safe retry.
        }
        if (reconciled && applied(reconciled)) {
          pendingIntents.current.delete(intent);
          setUnresolvedIntent((current) => current === intent ? null : current);
          setNotice(`${successMessage} Confirmed after checking the saved record.`);
          return true;
        }
        if (
          reconciled?.sequence &&
          reconciled.sequence.version > pending.expectedVersion
        ) {
          pendingIntents.current.delete(intent);
          setUnresolvedIntent((current) => current === intent ? null : current);
          setActionError(
            "The plan changed while this action was being checked, and the requested result is not present. Review the latest state before trying again.",
          );
          return false;
        }
        setActionError(
          `${ambiguousMessage} Trying the same action again is safe and will not duplicate it.`,
        );
        setUnresolvedIntent(intent);
        return false;
      }

      pendingIntents.current.delete(intent);
      setUnresolvedIntent((current) => current === intent ? null : current);
      await refresh(false);
      setActionError(outreachErrorText(reason));
      return false;
    } finally {
      if (requestGeneration.current === generation) setBusy(null);
    }
  }, [acceptResponse, applicationId, busy, getPendingIntent, refresh]);

  async function startSequence() {
    const fingerprint = `${applicationId}:${applicationVersion}`;
    await runMutation({
      intent: "start",
      fingerprint,
      expectedVersion: applicationVersion,
      execute: (pending) => startApplicationOutreachSequence(
        applicationId,
        pending.expectedVersion,
        pending.receiptKey,
      ),
      applied: (next) => next.sequence !== null,
      successMessage: "Manual outreach started. Wave 1 is ready for your review.",
      ambiguousMessage: "We could not confirm whether manual outreach started.",
    });
  }

  async function saveMessage(
    recipient: OutreachRecipient,
    kind: OutreachMessageKind,
  ) {
    const sequence = outreachRef.current?.sequence;
    if (!sequence) return;
    const key = draftKey(recipient.application_contact_id, kind);
    const body = drafts[key] ?? "";
    if (!body.trim()) {
      setActionError("Write a message before saving it.");
      return;
    }
    if (body.length > MAX_MESSAGE_CHARS) {
      setActionError(`Keep the message within ${MAX_MESSAGE_CHARS.toLocaleString()} characters.`);
      return;
    }
    const before = messageFor(recipient, kind);
    const payload: OutreachMessageCreate = {
      application_contact_id: recipient.application_contact_id,
      kind,
      body,
    };
    const intent = `save:${recipient.application_contact_id}:${kind}`;
    const saved = await runMutation({
      intent,
      fingerprint: JSON.stringify(payload),
      expectedVersion: sequence.version,
      execute: (pending) => saveApplicationOutreachMessage(
        applicationId,
        sequence.id,
        pending.expectedVersion,
        pending.receiptKey,
        payload,
      ),
      applied: (next) => {
        const latest = findMessage(next, recipient.application_contact_id, kind);
        return Boolean(
          latest && latest.body === body && (!before || latest.id !== before.id),
        );
      },
      successMessage: before
        ? `Saved exact ${kindLabel(kind).toLowerCase()} as a new version.`
        : `Saved exact ${kindLabel(kind).toLowerCase()}.`,
      ambiguousMessage: "We could not confirm that this exact message was saved.",
    });
    if (saved) {
      if (draftValues.current[key] === body) {
        dirtyDrafts.current.delete(key);
        setDirtyFlags((current) => ({ ...current, [key]: false }));
      }
    }
  }

  async function copyMessage(
    recipient: OutreachRecipient,
    message: OutreachMessageVersion,
    confirmedManualCopy = false,
  ) {
    const sequence = outreachRef.current?.sequence;
    if (!sequence) return;
    if (!confirmedManualCopy) {
      try {
        if (!navigator.clipboard?.writeText) {
          throw new Error("Clipboard access is unavailable in this browser.");
        }
        await navigator.clipboard.writeText(message.body);
        setLocallyCopied((current) => ({ ...current, [message.id]: true }));
      } catch (reason) {
        const detail = errorText(reason, "The Clipboard could not be updated.");
        setManualCopyFallback((current) => ({ ...current, [message.id]: true }));
        setActionError(
          `${detail} No copy was recorded. Select the exact saved text, copy it manually, then use the manual confirmation below.`,
        );
        return;
      }
    }

    const intent = `copy:${message.id}`;
    const copied = await runMutation({
      intent,
      fingerprint: message.id,
      expectedVersion: sequence.version,
      execute: (pending) => recordApplicationOutreachEvent(
        applicationId,
        sequence.id,
        pending.expectedVersion,
        pending.receiptKey,
        { event_type: "copied", message_version_id: message.id },
      ),
      applied: (next) => Boolean(findMessageById(next, message.id)?.copied_at),
      successMessage: confirmedManualCopy
        ? `Confirmed a manual copy of saved version ${message.version_number}. Nothing was sent.`
        : `Copied saved version ${message.version_number}. Nothing was sent.`,
      ambiguousMessage: "The exact text was copied locally, but we could not confirm its saved copy record.",
    });
    if (copied) {
      setLocallyCopied((current) => ({ ...current, [message.id]: false }));
      setManualCopyFallback((current) => ({ ...current, [message.id]: false }));
    }
  }

  async function markSent(
    recipient: OutreachRecipient,
    message: OutreachMessageVersion,
  ) {
    const sequence = outreachRef.current?.sequence;
    if (!sequence) return;
    const channel = channels[message.id] ?? "linkedin";
    if (!sendConfirmations[message.id]) {
      setActionError("Confirm that you sent this exact saved version before recording it.");
      return;
    }
    const payload: OutreachEventCreate = {
      event_type: "marked_sent",
      message_version_id: message.id,
      channel,
      confirm_exact_version: true,
    };
    const sent = await runMutation({
      intent: `sent:${message.id}`,
      fingerprint: JSON.stringify(payload),
      expectedVersion: sequence.version,
      execute: (pending) => recordApplicationOutreachEvent(
        applicationId,
        sequence.id,
        pending.expectedVersion,
        pending.receiptKey,
        payload,
      ),
      applied: (next) => {
        const saved = findMessageById(next, message.id);
        return Boolean(saved?.sent_at && saved.sent_channel === channel);
      },
      successMessage: `${kindLabel(message.kind)} recorded as manually sent via ${channelLabel(channel)}.`,
      ambiguousMessage: "We could not confirm the manual send record.",
    });
    if (sent) {
      setSendConfirmations((current) => ({ ...current, [message.id]: false }));
    }
  }

  async function recordOutcome(recipient: OutreachRecipient) {
    const sequence = outreachRef.current?.sequence;
    if (!sequence) return;
    const outcome = outcomeChoices[recipient.application_contact_id] ?? "";
    if (!outcome) {
      setActionError("Choose what happened before recording an outcome.");
      return;
    }
    const payload: OutreachEventCreate = {
      event_type: "outcome",
      application_contact_id: recipient.application_contact_id,
      outcome,
    };
    const changed = await runMutation({
      intent: `outcome:${recipient.application_contact_id}`,
      fingerprint: JSON.stringify(payload),
      expectedVersion: sequence.version,
      execute: (pending) => recordApplicationOutreachEvent(
        applicationId,
        sequence.id,
        pending.expectedVersion,
        pending.receiptKey,
        payload,
      ),
      applied: (next) => findRecipient(next, recipient.application_contact_id)?.outcome === outcome,
      successMessage: `${outcomeLabel(outcome)} recorded for ${recipient.public_name}.`,
      ambiguousMessage: "We could not confirm the outcome record.",
    });
    if (changed) {
      setOutcomeChoices((current) => ({
        ...current,
        [recipient.application_contact_id]: "",
      }));
    }
  }

  async function changeSequenceState(mode: ControlMode) {
    const sequence = outreachRef.current?.sequence;
    const reason = controlReason.trim();
    if (!sequence) return;
    if (!reason) {
      setActionError(`Add a short reason to ${mode} outreach.`);
      return;
    }
    const eventType = mode;
    const payload: OutreachEventCreate = { event_type: eventType, reason };
    const target = mode === "pause" ? "paused" : mode === "resume" ? "active" : "stopped";
    const changed = await runMutation({
      intent: `sequence:${mode}`,
      fingerprint: JSON.stringify(payload),
      expectedVersion: sequence.version,
      execute: (pending) => recordApplicationOutreachEvent(
        applicationId,
        sequence.id,
        pending.expectedVersion,
        pending.receiptKey,
        payload,
      ),
      applied: (next) => next.sequence?.status === target,
      successMessage: mode === "pause"
        ? "Manual outreach paused. No message actions are available until you resume."
        : mode === "resume"
          ? "Manual outreach resumed at the same wave."
          : "Manual outreach stopped permanently for this application.",
      ambiguousMessage: `We could not confirm that outreach was ${mode === "stop" ? "stopped" : `${mode}d`}.`,
    });
    if (changed) {
      setControlMode(null);
      setControlReason("");
    }
  }

  const sequence = outreach?.sequence ?? null;
  const activeWave = sequence?.active_wave ?? null;
  const currentRecipients = useMemo(
    () => activeWave === null
      ? outreach?.recipients ?? []
      : outreach?.recipients.filter((recipient) => recipient.wave === activeWave) ?? [],
    [activeWave, outreach?.recipients],
  );
  const previousRecipients = useMemo(
    () => activeWave === null
      ? []
      : outreach?.recipients.filter((recipient) => recipient.wave < activeWave) ?? [],
    [activeWave, outreach?.recipients],
  );
  const unavailableRecipients = useMemo(
    () => activeWave === null
      ? []
      : outreach?.recipients.filter(
          (recipient) =>
            recipient.wave > activeWave &&
            (recipient.bench_state !== "reserve" || recipient.lifecycle !== "active"),
        ) ?? [],
    [activeWave, outreach?.recipients],
  );
  const reserveRecipients = useMemo(
    () => activeWave === null
      ? []
      : outreach?.recipients.filter(
          (recipient) =>
            recipient.wave > activeWave &&
            recipient.bench_state === "reserve" &&
            recipient.lifecycle === "active",
        ) ?? [],
    [activeWave, outreach?.recipients],
  );
  const currentReadyCount = currentRecipients.filter(
    (recipient) =>
      recipient.outcome === null &&
      recipient.bench_state === "ready" &&
      recipient.lifecycle === "active",
  ).length;
  const currentPausedCount = currentRecipients.filter(
    (recipient) =>
      recipient.outcome === null &&
      recipient.bench_state === "paused" &&
      recipient.lifecycle === "active",
  ).length;
  const currentResolvedCount = currentRecipients.filter(
    (recipient) => recipient.outcome !== null,
  ).length;
  const currentUnavailableCount = Math.max(
    0,
    currentRecipients.length - currentReadyCount - currentPausedCount - currentResolvedCount,
  );
  const currentWaveSummary = [
    `${currentReadyCount} ready`,
    currentPausedCount ? `${currentPausedCount} paused` : null,
    currentResolvedCount ? `${currentResolvedCount} resolved` : null,
    currentUnavailableCount ? `${currentUnavailableCount} unavailable` : null,
  ].filter(Boolean).join(" · ");
  const startBlockedReason = postingState !== "open"
    ? `This posting is ${postingState}.`
    : contactSearchRunning
      ? "Wait for the contact refresh to finish."
      : !benchReady
        ? "Build a verified contact bench above first."
        : null;

  return (
    <section
      aria-labelledby="application-outreach-title"
      aria-busy={loading || Boolean(busy)}
      className="rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm sm:p-7 dark:border-zinc-800 dark:bg-zinc-900/70"
    >
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-indigo-600 dark:text-indigo-400">
            Manual outreach
          </p>
          <h2 id="application-outreach-title" className="mt-2 text-xl font-semibold tracking-tight">
            Contact people without losing track
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
            Write and review every message yourself. Save an exact version, copy it,
            send it on the person&apos;s profile or by email, then record what happened.
          </p>
        </div>
        <span className="inline-flex min-h-8 shrink-0 items-center self-start rounded-full border border-emerald-200 bg-emerald-50 px-3 text-xs font-semibold text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200">
          Manual only
        </span>
      </div>

      <p className="mt-4 rounded-lg border border-indigo-200 bg-indigo-50 p-3 text-sm font-medium text-indigo-950 dark:border-indigo-900 dark:bg-indigo-950/30 dark:text-indigo-100">
        Nothing sends automatically. Copying a message does not mark it sent.
      </p>

      <div aria-live="polite" className="mt-4 space-y-3">
        {notice ? <StatusMessage kind="success">{notice}</StatusMessage> : null}
        {actionError ? <StatusMessage kind="error">{actionError}</StatusMessage> : null}
        {loadError && outreach ? (
          <StatusMessage kind="error">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span>{loadError} The last saved outreach plan is still shown.</span>
              <button type="button" onClick={() => void refresh(false)} className={secondaryButtonClasses}>
                Try refresh again
              </button>
            </div>
          </StatusMessage>
        ) : null}
      </div>

      {loading && !outreach ? (
        <p role="status" className="mt-6 text-sm text-zinc-500">Loading manual outreach…</p>
      ) : null}

      {!loading && !outreach ? (
        <div className="mt-6">
          <StatusMessage kind="error">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span>{loadError ?? "Manual outreach is unavailable."}</span>
              <button type="button" onClick={() => void refresh(true)} className={secondaryButtonClasses}>
                Try again
              </button>
            </div>
          </StatusMessage>
        </div>
      ) : null}

      {outreach?.status === "not_started" ? (
        <div className="mt-6 rounded-xl border border-zinc-200 p-4 sm:p-5 dark:border-zinc-800">
          <h3 className="font-semibold">Start with a small, safe first wave</h3>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
            The strongest role-relevant person and, when useful, one recruiter will
            be ready first. Everyone else stays in reserve until the current wave is resolved.
          </p>
          {startBlockedReason ? (
            <p className="mt-3 text-sm font-medium text-amber-800 dark:text-amber-300">
              {startBlockedReason}
            </p>
          ) : null}
          <button
            type="button"
            disabled={
              Boolean(startBlockedReason) ||
              Boolean(busy) ||
              Boolean(unresolvedIntent && unresolvedIntent !== "start")
            }
            onClick={() => void startSequence()}
            className={`${primaryButtonClasses} mt-4 w-full sm:w-auto`}
          >
            {busy === "start"
              ? "Starting manual outreach…"
              : unresolvedIntent === "start"
                ? "Retry starting safely"
                : "Start manual outreach"}
          </button>
        </div>
      ) : null}

      {sequence ? (
        <div className="mt-6 space-y-5">
          <SequenceSummary
            outreach={outreach!}
            postingState={postingState}
            onRefresh={() => void refresh(false)}
            busy={Boolean(busy)}
          />

          {currentRecipients.length > 0 ? (
            <section aria-labelledby={activeWave === null ? "outreach-plan-history" : `outreach-wave-${activeWave}`}>
              <div className="flex flex-wrap items-end justify-between gap-2">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600 dark:text-indigo-400">
                    {activeWave === null ? "Plan history" : "Current wave"}
                  </p>
                  <h3
                    id={activeWave === null ? "outreach-plan-history" : `outreach-wave-${activeWave}`}
                    tabIndex={-1}
                    className="mt-1 text-lg font-semibold"
                  >
                    {activeWave === null
                      ? `${currentRecipients.length} ${currentRecipients.length === 1 ? "person" : "people"} in this plan`
                      : `Wave ${activeWave} · ${currentWaveSummary}`}
                  </h3>
                </div>
                {activeWave !== null ? (
                  <p className="text-xs text-zinc-500">
                    {reserveRecipients.length} held in reserve
                  </p>
                ) : null}
              </div>
              <ul className="mt-4 space-y-4">
                {currentRecipients.map((recipient) => (
                  <RecipientCard
                    key={recipient.application_contact_id}
                    recipient={recipient}
                    sequenceStatus={sequence.status}
                    postingState={postingState}
                    draftInitial={drafts[draftKey(recipient.application_contact_id, "initial")] ?? ""}
                    draftFollowUp={drafts[draftKey(recipient.application_contact_id, "follow_up")] ?? ""}
                    initialDirty={Boolean(dirtyFlags[draftKey(recipient.application_contact_id, "initial")])}
                    followUpDirty={Boolean(dirtyFlags[draftKey(recipient.application_contact_id, "follow_up")])}
                    channelByMessage={channels}
                    sendConfirmationByMessage={sendConfirmations}
                    locallyCopied={locallyCopied}
                    manualCopyFallback={manualCopyFallback}
                    outcomeChoice={outcomeChoices[recipient.application_contact_id] ?? ""}
                    busy={busy}
                    unresolvedIntent={unresolvedIntent}
                    onDraftChange={(kind, value) => {
                      const key = draftKey(recipient.application_contact_id, kind);
                      const saved = messageFor(recipient, kind)?.body ?? "";
                      if (value === saved) dirtyDrafts.current.delete(key);
                      else dirtyDrafts.current.add(key);
                      draftValues.current = { ...draftValues.current, [key]: value };
                      setDirtyFlags((current) => ({ ...current, [key]: value !== saved }));
                      setDrafts((current) => ({ ...current, [key]: value }));
                    }}
                    onSave={(kind) => void saveMessage(recipient, kind)}
                    onCopy={(message) => void copyMessage(recipient, message)}
                    onManualCopy={(message) => void copyMessage(recipient, message, true)}
                    onChannelChange={(messageId, channel) => setChannels((current) => ({
                      ...current,
                      [messageId]: channel,
                    }))}
                    onSendConfirmation={(messageId, checked) => setSendConfirmations((current) => ({
                      ...current,
                      [messageId]: checked,
                    }))}
                    onMarkSent={(message) => void markSent(recipient, message)}
                    onOutcomeChange={(outcome) => setOutcomeChoices((current) => ({
                      ...current,
                      [recipient.application_contact_id]: outcome,
                    }))}
                    onRecordOutcome={() => void recordOutcome(recipient)}
                  />
                ))}
              </ul>
            </section>
          ) : null}

          {previousRecipients.length > 0 ? (
            <RecipientSummaryList
              title={`${previousRecipients.length} ${previousRecipients.length === 1 ? "person" : "people"} from earlier waves`}
              description="These people are resolved. Their exact sent messages remain in the saved activity and application history."
              recipients={previousRecipients}
              reserve={false}
            />
          ) : null}

          {unavailableRecipients.length > 0 ? (
            <RecipientSummaryList
              title={`${unavailableRecipients.length} ${unavailableRecipients.length === 1 ? "person" : "people"} unavailable`}
              description="These pinned people became restricted or ineligible before their wave opened. No outreach action is available for them."
              recipients={unavailableRecipients}
              reserve={false}
              unavailable
            />
          ) : null}

          {reserveRecipients.length > 0 ? (
            <RecipientSummaryList
              title={`${reserveRecipients.length} ${reserveRecipients.length === 1 ? "person" : "people"} reserved for later`}
              description="These people are pinned to this plan but cannot be messaged until their wave is unlocked."
              recipients={reserveRecipients}
              reserve
            />
          ) : null}

          <SequenceControls
            status={sequence.status}
            reason={sequence.reason}
            postingState={postingState}
            mode={controlMode}
            draftReason={controlReason}
            busy={Boolean(busy)}
            unresolvedIntent={unresolvedIntent}
            onChoose={(mode) => {
              setControlMode(mode);
              setControlReason("");
              setActionError(null);
            }}
            onReasonChange={setControlReason}
            onCancel={() => {
              setControlMode(null);
              setControlReason("");
            }}
            onConfirm={(mode) => void changeSequenceState(mode)}
          />

          <OutreachTimeline events={outreach!.timeline} />
        </div>
      ) : null}
    </section>
  );
}

function SequenceSummary({
  outreach,
  postingState,
  onRefresh,
  busy,
}: {
  outreach: ApplicationOutreachResponse;
  postingState: ApplicationPostingState;
  onRefresh: () => void;
  busy: boolean;
}) {
  const sequence = outreach.sequence;
  if (!sequence) return null;
  const resolved = outreach.recipients.filter(
    (recipient) => recipient.outcome !== null,
  ).length;
  const ready = outreach.recipients.filter(
    (recipient) =>
      recipient.outcome === null &&
      recipient.bench_state === "ready" &&
      recipient.lifecycle === "active",
  ).length;
  const paused = outreach.recipients.filter(
    (recipient) =>
      recipient.outcome === null &&
      recipient.bench_state === "paused" &&
      recipient.lifecycle === "active",
  ).length;
  const reserve = outreach.recipients.filter(
    (recipient) =>
      recipient.outcome === null &&
      recipient.bench_state === "reserve" &&
      recipient.lifecycle === "active",
  ).length;
  const unavailable = Math.max(
    0,
    outreach.recipients.length - ready - paused - reserve - resolved,
  );
  return (
    <div className="rounded-xl border border-zinc-200 bg-zinc-50 p-4 dark:border-zinc-800 dark:bg-zinc-950/50">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${sequenceStatusClasses(sequence.status)}`}>
              {sequenceStatusLabel(sequence.status)}
            </span>
            {sequence.active_wave ? <span className="text-xs text-zinc-500">Wave {sequence.active_wave}</span> : null}
          </div>
          <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
            {ready} ready{paused ? ` · ${paused} paused` : ""} · {reserve} reserved
            {resolved ? ` · ${resolved} resolved` : ""}
            {unavailable ? ` · ${unavailable} unavailable` : ""} · {outreach.recipients.length} total pinned
          </p>
          {sequence.reason ? (
            <p className="mt-1 text-sm text-zinc-500">Reason: {sequenceReasonLabel(sequence.reason)}</p>
          ) : null}
        </div>
        <button type="button" disabled={busy} onClick={onRefresh} className={secondaryButtonClasses}>
          Refresh saved state
        </button>
      </div>
      {postingState !== "open" ? (
        <p className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900 dark:border-red-900 dark:bg-red-950/30 dark:text-red-100">
          This posting is {postingState}. New message actions are disabled; refresh or stop this plan after reviewing it.
        </p>
      ) : null}
    </div>
  );
}

function RecipientSummaryList({
  title,
  description,
  recipients,
  reserve,
  unavailable = false,
}: {
  title: string;
  description: string;
  recipients: OutreachRecipient[];
  reserve: boolean;
  unavailable?: boolean;
}) {
  return (
    <details className="rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
      <summary className="min-h-11 cursor-pointer list-none font-medium focus:outline-none focus:ring-2 focus:ring-indigo-500">
        {title}
      </summary>
      <p className="mt-2 text-sm leading-6 text-zinc-500">{description}</p>
      <ul className="mt-3 divide-y divide-zinc-200 dark:divide-zinc-800">
        {recipients.map((recipient) => {
          const sentMessages = [recipient.initial_message, recipient.follow_up_message]
            .filter((message): message is OutreachMessageVersion => Boolean(message?.sent_at));
          return (
            <li key={recipient.application_contact_id} className="min-w-0 py-3">
              <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <p className="break-words font-medium">{recipient.public_name}</p>
                  <p className="break-words text-sm text-zinc-500">{recipient.current_title}</p>
                </div>
                <span className="shrink-0 text-xs font-medium text-zinc-500">
                  {recipient.outcome
                    ? outcomeLabel(recipient.outcome)
                    : unavailable
                      ? "Unavailable"
                      : reserve
                        ? `Reserved · Wave ${recipient.wave}`
                        : `Wave ${recipient.wave} resolved`}
                </span>
              </div>
              {!reserve && sentMessages.length > 0 ? (
                <div className="mt-2 space-y-2">
                  {sentMessages.map((message) => (
                    <details key={message.id} className="rounded-lg bg-zinc-50 p-3 dark:bg-zinc-950/60">
                      <summary className="min-h-8 cursor-pointer text-xs font-medium">
                        Review exact {kindLabel(message.kind).toLowerCase()} version {message.version_number}
                      </summary>
                      <pre className="mt-2 whitespace-pre-wrap break-words font-sans text-sm leading-6 [overflow-wrap:anywhere]">
                        {message.body}
                      </pre>
                    </details>
                  ))}
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
    </details>
  );
}

function RecipientCard({
  recipient,
  sequenceStatus,
  postingState,
  draftInitial,
  draftFollowUp,
  initialDirty,
  followUpDirty,
  channelByMessage,
  sendConfirmationByMessage,
  locallyCopied,
  manualCopyFallback,
  outcomeChoice,
  busy,
  unresolvedIntent,
  onDraftChange,
  onSave,
  onCopy,
  onManualCopy,
  onChannelChange,
  onSendConfirmation,
  onMarkSent,
  onOutcomeChange,
  onRecordOutcome,
}: {
  recipient: RecipientDeadline;
  sequenceStatus: "active" | "paused" | "stopped" | "completed";
  postingState: ApplicationPostingState;
  draftInitial: string;
  draftFollowUp: string;
  initialDirty: boolean;
  followUpDirty: boolean;
  channelByMessage: Record<string, OutreachChannel>;
  sendConfirmationByMessage: Record<string, boolean>;
  locallyCopied: Record<string, boolean>;
  manualCopyFallback: Record<string, boolean>;
  outcomeChoice: OutreachOutcome | "";
  busy: string | null;
  unresolvedIntent: string | null;
  onDraftChange: (kind: OutreachMessageKind, value: string) => void;
  onSave: (kind: OutreachMessageKind) => void;
  onCopy: (message: OutreachMessageVersion) => void;
  onManualCopy: (message: OutreachMessageVersion) => void;
  onChannelChange: (messageId: string, channel: OutreachChannel) => void;
  onSendConfirmation: (messageId: string, checked: boolean) => void;
  onMarkSent: (message: OutreachMessageVersion) => void;
  onOutcomeChange: (outcome: OutreachOutcome | "") => void;
  onRecordOutcome: () => void;
}) {
  const active =
    sequenceStatus === "active" &&
    postingState === "open" &&
    recipient.bench_state === "ready" &&
    recipient.lifecycle === "active";
  const initial = recipient.initial_message;
  const followUp = recipient.follow_up_message;
  const selectedOutcome = outcomeChoice;
  const outcomeIntent = `outcome:${recipient.application_contact_id}`;
  const outcomeBlocked = Boolean(
    busy || (unresolvedIntent && unresolvedIntent !== outcomeIntent),
  );
  const outcomeOptions = allowedOutcomes(recipient, sequenceStatus, postingState);
  const selectedOutcomeDisabled = selectedOutcome === "no_reply" && !deadlineReached(recipient.no_reply_eligible_at);

  return (
    <li className="rounded-xl border border-zinc-200 p-4 sm:p-5 dark:border-zinc-800">
      <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="break-words font-semibold">{recipient.public_name}</h4>
            <span className="rounded-full bg-indigo-50 px-2 py-1 text-[11px] font-semibold text-indigo-700 dark:bg-indigo-950/50 dark:text-indigo-300">
              {categoryLabel(recipient.category)}
            </span>
          </div>
          <p className="mt-1 break-words text-sm text-zinc-600 dark:text-zinc-400">
            {recipient.current_title} · {recipient.current_company}
          </p>
          <p className="mt-1 text-xs text-zinc-500">Bench #{recipient.bench_rank} · Wave {recipient.wave}</p>
        </div>
        <a
          href={recipient.profile_url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Open ${recipient.public_name}'s public profile in a new tab`}
          className={secondaryButtonClasses}
        >
          Open profile ↗
        </a>
      </div>

      {recipient.outcome ? (
        <p className="mt-4 rounded-lg border border-zinc-200 bg-zinc-50 p-3 text-sm dark:border-zinc-800 dark:bg-zinc-950/50">
          Recorded outcome: <strong>{outcomeLabel(recipient.outcome)}</strong>
          {recipient.outcome_at ? ` · ${formatDate(recipient.outcome_at)}` : ""}
        </p>
      ) : null}

      {!recipient.outcome && !initial?.sent_at ? (
        <MessageEditor
          recipientId={recipient.application_contact_id}
          recipientName={recipient.public_name}
          kind="initial"
          value={draftInitial}
          dirty={initialDirty}
          saved={initial}
          enabled={active}
          busy={busy}
          unresolvedIntent={unresolvedIntent}
          locallyCopied={initial ? Boolean(locallyCopied[initial.id]) : false}
          manualCopyFallback={initial ? Boolean(manualCopyFallback[initial.id]) : false}
          channel={initial ? channelByMessage[initial.id] ?? "linkedin" : "linkedin"}
          sendConfirmed={initial ? Boolean(sendConfirmationByMessage[initial.id]) : false}
          copyAvailable={active}
          onChange={(value) => onDraftChange("initial", value)}
          onSave={() => onSave("initial")}
          onCopy={() => initial && onCopy(initial)}
          onManualCopy={() => initial && onManualCopy(initial)}
          onChannelChange={(channel) => initial && onChannelChange(initial.id, channel)}
          onSendConfirmation={(checked) => initial && onSendConfirmation(initial.id, checked)}
          onMarkSent={() => initial && onMarkSent(initial)}
        />
      ) : null}

      {initial?.sent_at ? (
        <SentMessageSummary message={initial} />
      ) : null}

      {initial?.sent_at && !recipient.outcome ? (
        <div className="mt-5 border-t border-zinc-200 pt-5 dark:border-zinc-800">
          <MessageEditor
            recipientId={recipient.application_contact_id}
            recipientName={recipient.public_name}
            kind="follow_up"
            value={draftFollowUp}
            dirty={followUpDirty}
            saved={followUp}
            enabled={active && !followUp?.sent_at}
            busy={busy}
            unresolvedIntent={unresolvedIntent}
            locallyCopied={followUp ? Boolean(locallyCopied[followUp.id]) : false}
            manualCopyFallback={followUp ? Boolean(manualCopyFallback[followUp.id]) : false}
            channel={followUp ? channelByMessage[followUp.id] ?? "linkedin" : "linkedin"}
            sendConfirmed={followUp ? Boolean(sendConfirmationByMessage[followUp.id]) : false}
            copyAvailable={active && deadlineReached(recipient.follow_up_due_at)}
            unavailableCopyReason={
              !deadlineReached(recipient.follow_up_due_at)
                ? `Copy and send become available ${formatDate(recipient.follow_up_due_at)}.`
                : undefined
            }
            onChange={(value) => onDraftChange("follow_up", value)}
            onSave={() => onSave("follow_up")}
            onCopy={() => followUp && onCopy(followUp)}
            onManualCopy={() => followUp && onManualCopy(followUp)}
            onChannelChange={(channel) => followUp && onChannelChange(followUp.id, channel)}
            onSendConfirmation={(checked) => followUp && onSendConfirmation(followUp.id, checked)}
            onMarkSent={() => followUp && onMarkSent(followUp)}
          />
          {followUp?.sent_at ? (
            <p className="mt-3 text-xs font-medium text-zinc-500">
              One follow-up sent. No further follow-ups will be offered.
            </p>
          ) : null}
        </div>
      ) : null}

      {outcomeOptions.length > 0 ? (
        <div className="mt-5 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
          <label htmlFor={`outcome-${recipient.application_contact_id}`} className="text-sm font-medium">
            What happened?
          </label>
          <select
            id={`outcome-${recipient.application_contact_id}`}
            value={selectedOutcome}
            disabled={Boolean(busy) || Boolean(unresolvedIntent)}
            onChange={(event) => onOutcomeChange(event.target.value as OutreachOutcome | "")}
            className={`${inputClasses} mt-2`}
          >
            <option value="">Choose an outcome</option>
            {outcomeOptions.map((outcome) => (
              <option
                key={outcome}
                value={outcome}
                disabled={outcome === "no_reply" && !deadlineReached(recipient.no_reply_eligible_at)}
              >
                {outcomeLabel(outcome)}
              </option>
            ))}
          </select>
          {selectedOutcome ? (
            <p className="mt-2 text-xs leading-5 text-zinc-500">
              {outcomeEffect(selectedOutcome)}
            </p>
          ) : null}
          {outcomeOptions.includes("no_reply") && recipient.no_reply_eligible_at && !deadlineReached(recipient.no_reply_eligible_at) ? (
            <p className="mt-2 text-xs leading-5 text-zinc-500">
              “No reply” becomes available {formatDate(recipient.no_reply_eligible_at)}.
            </p>
          ) : null}
          <button
            type="button"
            disabled={!selectedOutcome || selectedOutcomeDisabled || outcomeBlocked}
            onClick={onRecordOutcome}
            className={`${secondaryButtonClasses} mt-3 w-full sm:w-auto`}
          >
            Record outcome
          </button>
        </div>
      ) : null}

      {!active && sequenceStatus === "paused" && !recipient.outcome ? (
        <p className="mt-4 text-xs leading-5 text-zinc-500">
          Resume this plan before editing, copying, sending, or recording a new outcome.
        </p>
      ) : null}
      {sequenceStatus === "active" && !active && !recipient.outcome ? (
        <p className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          This person is no longer eligible for message actions in the current plan.
        </p>
      ) : null}
    </li>
  );
}

function MessageEditor({
  recipientId,
  recipientName,
  kind,
  value,
  dirty,
  saved,
  enabled,
  busy,
  unresolvedIntent,
  locallyCopied,
  manualCopyFallback,
  channel,
  sendConfirmed,
  copyAvailable,
  unavailableCopyReason,
  onChange,
  onSave,
  onCopy,
  onManualCopy,
  onChannelChange,
  onSendConfirmation,
  onMarkSent,
}: {
  recipientId: string;
  recipientName: string;
  kind: OutreachMessageKind;
  value: string;
  dirty: boolean;
  saved: OutreachMessageVersion | null;
  enabled: boolean;
  busy: string | null;
  unresolvedIntent: string | null;
  locallyCopied: boolean;
  manualCopyFallback: boolean;
  channel: OutreachChannel;
  sendConfirmed: boolean;
  copyAvailable: boolean;
  unavailableCopyReason?: string;
  onChange: (value: string) => void;
  onSave: () => void;
  onCopy: () => void;
  onManualCopy: () => void;
  onChannelChange: (channel: OutreachChannel) => void;
  onSendConfirmation: (checked: boolean) => void;
  onMarkSent: () => void;
}) {
  const inputId = `${kind}-${recipientId}`;
  const saveIntent = `save:${recipientId}:${kind}`;
  const copyIntent = saved ? `copy:${saved.id}` : null;
  const sentIntent = saved ? `sent:${saved.id}` : null;
  const blocked = (intent: string | null) => Boolean(
    busy || (unresolvedIntent && unresolvedIntent !== intent),
  );
  const savedMatches = Boolean(saved && saved.body === value && !dirty);
  const canCopy = Boolean(
    enabled && saved && !saved.sent_at && savedMatches && copyAvailable && !blocked(copyIntent),
  );
  const canMarkSent = Boolean(
    enabled && saved?.copied_at && !saved.sent_at && savedMatches && copyAvailable &&
    sendConfirmed && !blocked(sentIntent),
  );

  if (saved?.sent_at) return <SentMessageSummary message={saved} />;

  return (
    <div className="mt-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <label htmlFor={inputId} className="text-sm font-semibold">
          {kindLabel(kind)} to {recipientName}
        </label>
        <span className={`text-xs ${value.length > MAX_MESSAGE_CHARS ? "text-red-700" : "text-zinc-500"}`}>
          {value.length.toLocaleString()}/{MAX_MESSAGE_CHARS.toLocaleString()}
        </span>
      </div>
      <p id={`${inputId}-help`} className="mt-1 text-xs leading-5 text-zinc-500">
        Keep it personal: why this role or team, one truthful proof from your experience,
        and one small question. The text is saved exactly as written.
      </p>
      <textarea
        id={inputId}
        aria-describedby={`${inputId}-help`}
        value={value}
        maxLength={MAX_MESSAGE_CHARS}
        disabled={!enabled || Boolean(unresolvedIntent) || Boolean(busy)}
        onChange={(event) => onChange(event.target.value)}
        placeholder={kind === "initial"
          ? "Write your own short, specific message…"
          : "Write one brief, respectful follow-up…"}
        className={`${textareaClasses} mt-2`}
      />
      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
        <button
          type="button"
          disabled={
            !enabled ||
            !value.trim() ||
            value.length > MAX_MESSAGE_CHARS ||
            blocked(saveIntent)
          }
          onClick={onSave}
          className={primaryButtonClasses}
        >
          {saved ? "Save as new version" : "Save exact message"}
        </button>
        {saved ? (
          <span className="text-xs text-zinc-500">
            Saved version {saved.version_number} · {formatDate(saved.created_at)}
          </span>
        ) : null}
        {dirty ? <span className="text-xs font-medium text-amber-700 dark:text-amber-300">Unsaved changes</span> : null}
      </div>

      {!saved && unavailableCopyReason ? (
        <p className="mt-3 text-xs text-zinc-500">{unavailableCopyReason}</p>
      ) : null}

      {saved ? (
        <div className="mt-4 rounded-lg border border-zinc-200 bg-zinc-50 p-4 dark:border-zinc-800 dark:bg-zinc-950/50">
          <p className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
            Exact saved version {saved.version_number}
          </p>
          <pre className="mt-2 whitespace-pre-wrap break-words font-sans text-sm leading-6 [overflow-wrap:anywhere]">
            {saved.body}
          </pre>
          {!savedMatches ? (
            <p className="mt-3 text-xs font-medium text-amber-800 dark:text-amber-300">
              Save your current changes before copying or recording a send.
            </p>
          ) : null}
          {unavailableCopyReason ? (
            <p className="mt-3 text-xs text-zinc-500">{unavailableCopyReason}</p>
          ) : null}
          <button
            type="button"
            disabled={!canCopy}
            onClick={onCopy}
            className={`${secondaryButtonClasses} mt-3 w-full sm:w-auto`}
          >
            {locallyCopied && !saved.copied_at
              ? "Retry saving copy record"
              : saved.copied_at
                ? "Copy exact saved version again"
                : "Copy exact saved version"}
          </button>
          {locallyCopied && !saved.copied_at ? (
            <p className="mt-2 text-xs text-amber-800 dark:text-amber-300">
              Copied locally; the saved copy record is still unconfirmed.
            </p>
          ) : null}
          {manualCopyFallback && !saved.copied_at ? (
            <button
              type="button"
              disabled={!canCopy}
              onClick={onManualCopy}
              className={`${secondaryButtonClasses} mt-2 w-full sm:w-auto`}
            >
              I copied this exact version manually
            </button>
          ) : null}

          {saved.copied_at ? (
            <div className="mt-4 border-t border-zinc-200 pt-4 dark:border-zinc-800">
              <p className="text-sm font-semibold">After you send it yourself</p>
              <div className="mt-3 grid gap-3 sm:grid-cols-[minmax(0,12rem)_1fr] sm:items-start">
                <label className="text-sm">
                  <span className="block font-medium">Channel</span>
                  <select
                    value={channel}
                    disabled={!enabled || Boolean(busy) || Boolean(unresolvedIntent)}
                    onChange={(event) => onChannelChange(event.target.value as OutreachChannel)}
                    className={`${inputClasses} mt-1`}
                  >
                    <option value="linkedin">LinkedIn</option>
                    <option value="email">Email</option>
                    <option value="other">Other</option>
                  </select>
                </label>
                <label className="flex min-h-11 items-start gap-3 rounded-lg border border-zinc-200 p-3 text-sm dark:border-zinc-800">
                  <input
                    type="checkbox"
                    checked={sendConfirmed}
                    disabled={
                      !enabled ||
                      !savedMatches ||
                      !copyAvailable ||
                      Boolean(busy) ||
                      Boolean(unresolvedIntent)
                    }
                    onChange={(event) => onSendConfirmation(event.target.checked)}
                    className="mt-0.5 h-5 w-5 shrink-0 accent-indigo-600"
                  />
                  <span>I sent this exact saved version {saved.version_number} myself.</span>
                </label>
              </div>
              <button
                type="button"
                disabled={!canMarkSent}
                onClick={onMarkSent}
                className={`${primaryButtonClasses} mt-3 w-full sm:w-auto`}
              >
                Record exact version as sent
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function SentMessageSummary({ message }: { message: OutreachMessageVersion }) {
  return (
    <div className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50 p-4 dark:border-emerald-900 dark:bg-emerald-950/30">
      <p className="text-sm font-semibold text-emerald-950 dark:text-emerald-100">
        {kindLabel(message.kind)} version {message.version_number} recorded as sent
      </p>
      <p className="mt-1 text-xs text-emerald-800 dark:text-emerald-300">
        {channelLabel(message.sent_channel ?? "other")} · {formatDate(message.sent_at)}
      </p>
      <details className="mt-3">
        <summary className="min-h-9 cursor-pointer text-xs font-medium text-emerald-900 dark:text-emerald-200">
          Review exact sent text
        </summary>
        <pre className="mt-2 whitespace-pre-wrap break-words font-sans text-sm leading-6 [overflow-wrap:anywhere]">
          {message.body}
        </pre>
      </details>
    </div>
  );
}

function SequenceControls({
  status,
  reason,
  postingState,
  mode,
  draftReason,
  busy,
  unresolvedIntent,
  onChoose,
  onReasonChange,
  onCancel,
  onConfirm,
}: {
  status: "active" | "paused" | "stopped" | "completed";
  reason: string | null;
  postingState: ApplicationPostingState;
  mode: ControlMode | null;
  draftReason: string;
  busy: boolean;
  unresolvedIntent: string | null;
  onChoose: (mode: ControlMode) => void;
  onReasonChange: (reason: string) => void;
  onCancel: () => void;
  onConfirm: (mode: ControlMode) => void;
}) {
  const controlIntent = mode ? `sequence:${mode}` : null;
  const controlBlocked = Boolean(
    busy || (unresolvedIntent && unresolvedIntent !== controlIntent),
  );
  if (status === "stopped" || status === "completed") {
    return (
      <div className="rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
        <p className="font-medium">This outreach plan is {status}.</p>
        <p className="mt-1 text-sm text-zinc-500">
          Its saved messages and activity remain available for review. No further message actions can be added.
        </p>
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
      <h3 className="font-semibold">Plan controls</h3>
      {status === "paused" ? (
        <p className="mt-1 text-sm text-zinc-500">
          Paused{reason ? `: ${sequenceReasonLabel(reason)}` : "."}
        </p>
      ) : (
        <p className="mt-1 text-sm text-zinc-500">
          {postingState === "open"
            ? "Pause when you need time; stop only when this plan should end permanently."
            : `This posting is ${postingState}; only an explicit stop remains available.`}
        </p>
      )}
      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
        {postingState === "open" && status === "active" ? (
          <button type="button" disabled={busy || Boolean(unresolvedIntent)} onClick={() => onChoose("pause")} className={secondaryButtonClasses}>
            Pause outreach
          </button>
        ) : postingState === "open" ? (
          <button type="button" disabled={busy || Boolean(unresolvedIntent)} onClick={() => onChoose("resume")} className={primaryButtonClasses}>
            Resume outreach
          </button>
        ) : null}
        <button type="button" disabled={busy || Boolean(unresolvedIntent)} onClick={() => onChoose("stop")} className={secondaryButtonClasses}>
          Stop outreach
        </button>
      </div>
      {mode ? (
        <div className="mt-4 rounded-lg bg-zinc-50 p-4 dark:bg-zinc-950/60">
          <label htmlFor="outreach-control-reason" className="text-sm font-medium">
            Reason to {mode} outreach
          </label>
          <input
            id="outreach-control-reason"
            value={draftReason}
            maxLength={MAX_REASON_CHARS}
            disabled={busy || Boolean(unresolvedIntent)}
            onChange={(event) => onReasonChange(event.target.value)}
            placeholder={mode === "stop" ? "Example: role closed or no longer pursuing" : "Add a short note"}
            className={`${inputClasses} mt-2`}
          />
          {mode === "stop" ? (
            <p className="mt-2 text-xs font-medium text-red-800 dark:text-red-300">
              Stopping is permanent for this application. Saved history will remain.
            </p>
          ) : null}
          <div className="mt-3 flex flex-col-reverse gap-2 sm:flex-row">
            <button type="button" disabled={busy || Boolean(unresolvedIntent)} onClick={onCancel} className={secondaryButtonClasses}>Cancel</button>
            <button
              type="button"
              disabled={!draftReason.trim() || controlBlocked}
              onClick={() => onConfirm(mode)}
              className={mode === "stop" ? secondaryButtonClasses : primaryButtonClasses}
            >
              Confirm {mode}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function OutreachTimeline({ events }: { events: OutreachTimelineEvent[] }) {
  if (events.length === 0) return null;
  return (
    <details className="rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
      <summary className="min-h-11 cursor-pointer list-none font-medium focus:outline-none focus:ring-2 focus:ring-indigo-500">
        Saved outreach activity · {events.length}
      </summary>
      <ol className="mt-3 space-y-3 border-l border-zinc-200 pl-4 dark:border-zinc-800">
        {events.slice().reverse().map((event) => (
          <li key={event.id} className="text-sm">
            <p className="font-medium">{timelineLabel(event)}</p>
            <p className="mt-0.5 text-xs text-zinc-500">{formatDate(event.occurred_at)}</p>
          </li>
        ))}
      </ol>
    </details>
  );
}

function draftKey(contactId: string, kind: OutreachMessageKind): DraftKey {
  return `${contactId}:${kind}`;
}

function messageFor(recipient: OutreachRecipient, kind: OutreachMessageKind) {
  return kind === "initial" ? recipient.initial_message : recipient.follow_up_message;
}

function findRecipient(response: ApplicationOutreachResponse, contactId: string) {
  return response.recipients.find((recipient) => recipient.application_contact_id === contactId);
}

function findMessage(
  response: ApplicationOutreachResponse,
  contactId: string,
  kind: OutreachMessageKind,
) {
  const recipient = findRecipient(response, contactId);
  return recipient ? messageFor(recipient, kind) : null;
}

function findMessageById(response: ApplicationOutreachResponse, messageId: string) {
  for (const recipient of response.recipients) {
    if (recipient.initial_message?.id === messageId) return recipient.initial_message;
    if (recipient.follow_up_message?.id === messageId) return recipient.follow_up_message;
  }
  return null;
}

function allowedOutcomes(
  recipient: RecipientDeadline,
  status: "active" | "paused" | "stopped" | "completed",
  postingState: ApplicationPostingState,
): OutreachOutcome[] {
  if (postingState !== "open") return [];
  if (status === "stopped" || status === "completed") return [];
  if (recipient.outcome === "useful_reply") {
    return ["introduced", "referred", "do_not_contact"];
  }
  if (status === "paused") {
    return [];
  }
  if (recipient.bench_state !== "ready" || recipient.lifecycle !== "active") return [];
  if (recipient.outcome) return [];
  if (!recipient.initial_message?.sent_at) return ["unreachable"];
  return [
    "useful_reply",
    "introduced",
    "referred",
    "declined",
    "unreachable",
    "no_reply",
    "do_not_contact",
  ];
}

function deadlineReached(value: string | null | undefined) {
  if (!value) return false;
  const date = new Date(value);
  return !Number.isNaN(date.getTime()) && date.getTime() <= Date.now();
}

function definitiveNonApplyMessage(response: ApplicationOutreachResponse) {
  if (response.sequence?.status === "stopped") {
    return "The requested action was not recorded because this application or posting is no longer active. The outreach plan was stopped safely instead.";
  }
  if (response.sequence?.status === "completed") {
    return "The requested action was not recorded because this outreach plan is already complete.";
  }
  return "The server returned saved state, but it did not contain the requested action. Review the plan before trying again.";
}

function outreachErrorText(reason: unknown) {
  if (reason instanceof WorkspaceApiError) {
    const messages: Record<string, string> = {
      mutation_pending: "This same action is still being finalized. Retry safely in a moment.",
      owner_session_required: "Your private session expired. Sign in again before changing outreach.",
      origin_forbidden: "This action was blocked because the page origin was not trusted.",
      workspace_unavailable: "The saved workspace is temporarily unavailable. Your local message text is still here.",
    };
    if (messages[reason.code]) return messages[reason.code];
    if (reason.fieldErrors.length > 0) {
      const field = reason.fieldErrors[0];
      return `${field.field ? `${field.field}: ` : ""}${field.message}`;
    }
    return sentence(reason.message);
  }
  return errorText(reason, "Unable to update manual outreach.");
}

function sequenceReasonLabel(reason: string) {
  const labels: Record<string, string> = {
    useful_reply: "A useful reply arrived",
    introduced: "An introduction was received",
    referred: "A referral was received",
    do_not_contact: "Do not contact requested",
    manual_pause: "Paused manually",
    manual_stop: "Stopped manually",
    application_terminal: "The application is no longer active",
    posting_closed: "The posting closed",
  };
  return labels[reason] ?? reason.replaceAll("_", " ");
}

function sequenceStatusLabel(status: "active" | "paused" | "stopped" | "completed") {
  return ({ active: "Active", paused: "Paused", stopped: "Stopped", completed: "Completed" } as const)[status];
}

function sequenceStatusClasses(status: "active" | "paused" | "stopped" | "completed") {
  return ({
    active: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
    paused: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
    stopped: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
    completed: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-200",
  } as const)[status];
}

function kindLabel(kind: OutreachMessageKind) {
  return kind === "initial" ? "Initial message" : "Follow-up";
}

function channelLabel(channel: OutreachChannel) {
  return ({ linkedin: "LinkedIn", email: "Email", other: "Other" } as const)[channel];
}

function outcomeLabel(outcome: OutreachOutcome) {
  return ({
    no_reply: "No reply",
    declined: "Declined",
    unreachable: "Could not reach",
    useful_reply: "Useful reply",
    introduced: "Introduced me",
    referred: "Referred me",
    do_not_contact: "Do not contact",
  } as const)[outcome];
}

function outcomeEffect(outcome: OutreachOutcome) {
  return ({
    useful_reply: "This pauses everyone else so you can focus on the conversation.",
    introduced: "This stops all remaining outreach and preserves the introduction in history.",
    referred: "This stops all remaining outreach because the referral goal was reached.",
    do_not_contact: "This permanently stops the plan and marks this person do not contact.",
    declined: "This closes this person; the next reserve unlocks when the current wave is resolved.",
    unreachable: "This closes this person without claiming that a message was sent.",
    no_reply: "This closes this person after the server-checked waiting window.",
  } as const)[outcome];
}

function categoryLabel(value: OutreachRecipient["category"]) {
  return ({
    warm_path: "Warm path",
    team_peer: "Team peer",
    adjacent_peer: "Adjacent peer",
    team_leader: "Team leader",
    recruiter: "Recruiter",
    other: "Relevant contact",
  } as const)[value];
}

function timelineLabel(event: OutreachTimelineEvent) {
  switch (event.event_type) {
    case "sequence_started": return `Started manual outreach at wave ${event.wave}`;
    case "message_saved": return `Saved ${kindLabel(event.kind).toLowerCase()} version`;
    case "copied": return "Copied an exact saved message";
    case "marked_sent": return `Recorded a manual send via ${channelLabel(event.channel)}`;
    case "outcome_recorded": return `Recorded outcome: ${outcomeLabel(event.outcome)}`;
    case "paused": return `Paused outreach: ${event.reason}`;
    case "resumed": return `Resumed outreach: ${event.reason}`;
    case "stopped": return `Stopped outreach: ${event.reason}`;
    case "wave_advanced": return `Unlocked wave ${event.wave}`;
  }
}

function sentence(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return "Unable to update manual outreach.";
  return `${trimmed.charAt(0).toUpperCase()}${trimmed.slice(1)}`;
}
