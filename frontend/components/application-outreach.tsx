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
  recordApplicationOutreachReply,
  saveApplicationOutreachMessage,
  startApplicationOutreachSequence,
} from "@/lib/application-api";
import type { ApplicationArtifactsResponse } from "@/lib/application-artifact-types";
import type { InterviewHistoryState } from "@/lib/application-interview-types";
import type {
  ApplicationPostingState,
  ApplicationStage,
} from "@/lib/application-types";
import type {
  ApplicationOutreachResponse,
  OutreachChannel,
  OutreachEventCreate,
  OutreachMessageCreate,
  OutreachMessageKind,
  OutreachMessageVersion,
  OutreachNonReplyOutcome,
  OutreachOutcome,
  OutreachReplyCreate,
  OutreachReplyKind,
  OutreachRecipient,
  OutreachSentAttempt,
  OutreachTimelineEvent,
} from "@/lib/outreach-types";
import {
  approvedOutreachGrounding,
  hydrateOutreachDraft,
  outreachDraftIsDirty,
  prepareGroundedOutreachDrafts,
} from "@/lib/grounded-outreach-drafts";
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
const MAX_REPLY_NOTE_CHARS = 1_000;
const REPLY_KINDS: OutreachReplyKind[] = [
  "reply_received",
  "useful_reply",
  "introduced",
  "referred",
  "declined",
  "do_not_contact",
];

type DraftKey = `${string}:${OutreachMessageKind}`;
type ControlMode = "pause" | "resume" | "stop";

interface PendingIntent {
  receiptKey: string;
  fingerprint: string;
  expectedVersion: number;
  applied?: (response: ApplicationOutreachResponse) => boolean;
  successMessage?: string;
  onConfirmed?: () => void;
}

interface MutationOptions {
  intent: string;
  fingerprint: string;
  expectedVersion: number;
  execute: (pending: PendingIntent) => Promise<ApplicationOutreachResponse>;
  applied: (response: ApplicationOutreachResponse) => boolean;
  successMessage: string;
  ambiguousMessage: string;
  onConfirmed?: () => void;
}

function notifyConfirmed(pending: PendingIntent) {
  const callback = pending.onConfirmed;
  pending.onConfirmed = undefined;
  callback?.();
}

type RecipientDeadline = OutreachRecipient;

interface ReplyDraft {
  replyKind: OutreachReplyKind | "";
  receivedOn: string;
  note: string;
  confirmationFingerprint: string | null;
}

interface ReplyUi {
  ownerLocalDate: string;
  ownerTimezone: string;
  editorAttemptId: string | null;
  draft: ReplyDraft;
  busy: string | null;
  unresolvedIntent: string | null;
  onOpen: (attempt: OutreachSentAttempt) => void;
  onReplyKindChange: (value: OutreachReplyKind | "") => void;
  onReceivedOnChange: (value: string) => void;
  onNoteChange: (value: string) => void;
  onConfirmationChange: (attempt: OutreachSentAttempt, checked: boolean) => void;
  onCancel: () => void;
  onRecord: (recipient: OutreachRecipient, attempt: OutreachSentAttempt) => void;
}

export function ApplicationOutreach({
  applicationId,
  applicationVersion,
  applicationStage,
  postingState,
  roleTitle,
  companyName,
  applicationArtifacts,
  ownerLocalDate,
  ownerTimezone,
  benchReady,
  contactSearchRunning,
  interviewHistoryState,
}: {
  applicationId: string;
  applicationVersion: number;
  applicationStage: ApplicationStage;
  postingState: ApplicationPostingState;
  roleTitle: string;
  companyName: string;
  applicationArtifacts: ApplicationArtifactsResponse | null;
  ownerLocalDate: string;
  ownerTimezone: string;
  benchReady: boolean;
  contactSearchRunning: boolean;
  interviewHistoryState: InterviewHistoryState;
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
  const [outcomeChoices, setOutcomeChoices] = useState<Record<string, OutreachNonReplyOutcome | "">>({});
  const [controlMode, setControlMode] = useState<ControlMode | null>(null);
  const [controlReason, setControlReason] = useState("");
  const [locallyCopied, setLocallyCopied] = useState<Record<string, boolean>>({});
  const [manualCopyFallback, setManualCopyFallback] = useState<Record<string, boolean>>({});
  const [replyEditorAttemptId, setReplyEditorAttemptId] = useState<string | null>(null);
  const [replyDraft, setReplyDraft] = useState<ReplyDraft>({
    replyKind: "",
    receivedOn: ownerLocalDate,
    note: "",
    confirmationFingerprint: null,
  });

  const requestGeneration = useRef(0);
  const outreachRef = useRef<ApplicationOutreachResponse | null>(null);
  const draftValues = useRef<Record<DraftKey, string>>({});
  const dirtyDrafts = useRef(new Set<DraftKey>());
  const pendingIntents = useRef(new Map<string, PendingIntent>());
  const replyTriggerAttemptId = useRef<string | null>(null);
  const approvedGrounding = useMemo(() => approvedOutreachGrounding({
    artifacts: applicationArtifacts,
    applicationId,
    roleTitle,
    companyName,
  }), [applicationArtifacts, applicationId, companyName, roleTitle]);

  useEffect(() => () => {
    requestGeneration.current += 1;
  }, []);

  useEffect(() => {
    if (!replyEditorAttemptId) return;
    const timer = setTimeout(() => {
      document.getElementById(replyKindInputId(replyEditorAttemptId))?.focus();
    }, 0);
    return () => clearTimeout(timer);
  }, [replyEditorAttemptId]);

  const hydrateDrafts = useCallback((next: ApplicationOutreachResponse) => {
    setDrafts((current) => {
      const hydrated = { ...current };
      for (const recipient of next.recipients) {
        const prepared = prepareGroundedOutreachDrafts(approvedGrounding, {
          applicationContactId: recipient.application_contact_id,
          publicName: recipient.public_name,
          category: recipient.category,
        });
        for (const kind of ["initial", "follow_up"] as const) {
          const key = draftKey(recipient.application_contact_id, kind);
          const saved = messageFor(recipient, kind);
          hydrated[key] = hydrateOutreachDraft({
            currentValue: hydrated[key] ?? "",
            dirty: dirtyDrafts.current.has(key),
            savedBody: saved?.body ?? null,
            preparedBody: kind === "initial" ? prepared?.initial ?? "" : prepared?.followUp ?? "",
          });
        }
      }
      draftValues.current = hydrated;
      return hydrated;
    });
  }, [approvedGrounding]);

  useEffect(() => {
    const current = outreachRef.current;
    if (current) hydrateDrafts(current);
  }, [hydrateDrafts]);

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
        notifyConfirmed(pending);
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
    onConfirmed,
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
      pending.onConfirmed ??= onConfirmed;
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
        notifyConfirmed(pending);
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
          notifyConfirmed(pending);
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
      successMessage: "Manual outreach started. Up to five eligible leads are ready for your review.",
      ambiguousMessage: "We could not confirm whether manual outreach started.",
    });
  }

  async function saveMessage(
    recipient: OutreachRecipient,
    kind: OutreachMessageKind,
    copyAfterSave = false,
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
      if (copyAfterSave) {
        const currentOutreach = outreachRef.current;
        if (!currentOutreach) return;
        const latest = findMessage(
          currentOutreach,
          recipient.application_contact_id,
          kind,
        );
        if (latest) await copyMessage(recipient, latest);
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
    const payload: OutreachEventCreate = {
      event_type: "marked_sent",
      message_version_id: message.id,
      channel,
      confirm_exact_version: true,
    };
    await runMutation({
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

  function openReplyEditor(attempt: OutreachSentAttempt) {
    if (busy || unresolvedIntent) return;
    replyTriggerAttemptId.current = attempt.marked_sent_event_id;
    setReplyDraft({
      replyKind: "",
      receivedOn: ownerLocalDate,
      note: "",
      confirmationFingerprint: null,
    });
    setReplyEditorAttemptId(attempt.marked_sent_event_id);
    setActionError(null);
    setNotice(null);
  }

  function closeReplyEditor() {
    setReplyEditorAttemptId(null);
    setReplyDraft({
      replyKind: "",
      receivedOn: ownerLocalDate,
      note: "",
      confirmationFingerprint: null,
    });
    const triggerAttemptId = replyTriggerAttemptId.current;
    replyTriggerAttemptId.current = null;
    setTimeout(() => {
      if (triggerAttemptId) {
        document.getElementById(replyTriggerId(triggerAttemptId))?.focus();
      }
    }, 0);
  }

  async function recordReply(
    recipient: OutreachRecipient,
    selectedAttempt: OutreachSentAttempt,
  ) {
    const current = outreachRef.current;
    const sequence = current?.sequence;
    const attempt = current
      ? findSentAttempt(current, selectedAttempt.marked_sent_event_id)
      : null;
    if (!sequence || !attempt) {
      setActionError(
        "That exact sent message is no longer present in the saved view. Refresh before recording the reply.",
      );
      return;
    }
    if (replyDraft.note.length > MAX_REPLY_NOTE_CHARS) {
      setActionError(
        `Keep the private note within ${MAX_REPLY_NOTE_CHARS.toLocaleString()} characters.`,
      );
      return;
    }
    const payload = replyPayload(attempt, replyDraft);
    if (!payload) {
      setActionError("Choose what kind of reply arrived and when you received it.");
      return;
    }
    if (payload.received_on > ownerLocalDate) {
      setActionError("The received date cannot be later than today in your timezone.");
      return;
    }
    if (attempt.sent_local_on && payload.received_on < attempt.sent_local_on) {
      setActionError("The received date cannot be earlier than the exact message send date.");
      return;
    }
    const fingerprint = replyFingerprint(payload);
    if (replyDraft.confirmationFingerprint !== fingerprint) {
      setActionError(
        "Confirm that this reply belongs to this exact sent message before saving it.",
      );
      return;
    }

    const knownReplyIds = new Set(attempt.replies.map((reply) => reply.id));
    const intent = replyIntent(attempt.marked_sent_event_id);
    await runMutation({
      intent,
      fingerprint,
      expectedVersion: sequence.version,
      execute: (pending) => recordApplicationOutreachReply(
        applicationId,
        sequence.id,
        pending.expectedVersion,
        pending.receiptKey,
        payload,
      ),
      applied: (next) => {
        const savedAttempt = findSentAttempt(next, attempt.marked_sent_event_id);
        return Boolean(savedAttempt?.replies.some(
          (reply) => !knownReplyIds.has(reply.id) && replyMatches(reply, payload),
        ));
      },
      successMessage: `${replyKindLabel(payload.reply_kind)} recorded for ${recipient.public_name} against the exact ${kindLabel(attempt.kind).toLowerCase()} version ${attempt.version_number}.`,
      ambiguousMessage: "We could not confirm that this exact reply was recorded.",
      onConfirmed: closeReplyEditor,
    });
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
      !recipientIsResolved(recipient) &&
      recipient.bench_state === "ready" &&
      recipient.lifecycle === "active",
  ).length;
  const currentPausedCount = currentRecipients.filter(
    (recipient) =>
      !recipientIsResolved(recipient) &&
      recipient.bench_state === "paused" &&
      recipient.lifecycle === "active",
  ).length;
  const currentResolvedCount = currentRecipients.filter(
    recipientIsResolved,
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
  const contactabilityIssue = applicationContactBlockCopy(
    interviewHistoryState,
    applicationStage,
  );
  const applicationContactable = contactabilityIssue === null;
  const actionPostingState: ApplicationPostingState = applicationContactable
    ? postingState
    : "closed";
  const startBlockedReason = contactabilityIssue ?? (
    postingState !== "open"
      ? `This posting is ${postingState}.`
      : contactSearchRunning
        ? "Wait for the contact refresh to finish."
        : !benchReady
          ? "Build a source-backed contact bench above first."
          : null
  );
  const replyUi: ReplyUi = {
    ownerLocalDate,
    ownerTimezone,
    editorAttemptId: replyEditorAttemptId,
    draft: replyDraft,
    busy,
    unresolvedIntent,
    onOpen: openReplyEditor,
    onReplyKindChange: (value) => setReplyDraft((current) => ({
      ...current,
      replyKind: value,
    })),
    onReceivedOnChange: (value) => setReplyDraft((current) => ({
      ...current,
      receivedOn: value,
    })),
    onNoteChange: (value) => setReplyDraft((current) => ({
      ...current,
      note: value,
    })),
    onConfirmationChange: (attempt, checked) => setReplyDraft((current) => {
      const payload = replyPayload(attempt, current);
      return {
        ...current,
        confirmationFingerprint: checked && payload
          ? replyFingerprint(payload)
          : null,
      };
    }),
    onCancel: closeReplyEditor,
    onRecord: (recipient, attempt) => void recordReply(recipient, attempt),
  };

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
            Grounded starting drafts are prepared from the exact approved application
            package when available. Review or edit, save an exact version, copy it,
            send it on the person&apos;s profile or by email, then record what happened.
          </p>
        </div>
        <span className="inline-flex min-h-8 shrink-0 items-center self-start rounded-full border border-emerald-200 bg-emerald-50 px-3 text-xs font-semibold text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200">
          Manual send
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
          <h3 className="font-semibold">Start one useful, bounded first wave</h3>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
            Up to five eligible, distinct leads can be prepared together, with a
            separate draft and manual send record for each person. Peers, a likely
            team leader, and a recruiter are preferred when the source results allow it.
            Nothing is sent automatically.
          </p>
          <p className="mt-2 max-w-2xl text-xs leading-5 text-zinc-500">
            Safety limits still apply at send time: one initial message per person,
            a 30-day person cooldown, and at most three cold employee contacts at the
            same company in seven days. Recruiters and known warm paths are exempt
            from the company cold-contact count.
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
            applicationContactable={applicationContactable}
            contactabilityIssue={contactabilityIssue}
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
                    postingState={actionPostingState}
                    draftInitial={drafts[draftKey(recipient.application_contact_id, "initial")] ?? ""}
                    draftFollowUp={drafts[draftKey(recipient.application_contact_id, "follow_up")] ?? ""}
                    initialDirty={Boolean(dirtyFlags[draftKey(recipient.application_contact_id, "initial")])}
                    followUpDirty={Boolean(dirtyFlags[draftKey(recipient.application_contact_id, "follow_up")])}
                    draftsPrepared={prepareGroundedOutreachDrafts(approvedGrounding, {
                      applicationContactId: recipient.application_contact_id,
                      publicName: recipient.public_name,
                      category: recipient.category,
                    }) !== null}
                    channelByMessage={channels}
                    locallyCopied={locallyCopied}
                    manualCopyFallback={manualCopyFallback}
                    outcomeChoice={outcomeChoices[recipient.application_contact_id] ?? ""}
                    busy={busy}
                    unresolvedIntent={unresolvedIntent}
                    replyUi={replyUi}
                    onDraftChange={(kind, value) => {
                      const key = draftKey(recipient.application_contact_id, kind);
                      const saved = messageFor(recipient, kind)?.body ?? null;
                      const prepared = prepareGroundedOutreachDrafts(approvedGrounding, {
                        applicationContactId: recipient.application_contact_id,
                        publicName: recipient.public_name,
                        category: recipient.category,
                      });
                      const preparedBody = kind === "initial"
                        ? prepared?.initial ?? ""
                        : prepared?.followUp ?? "";
                      const dirty = outreachDraftIsDirty({
                        value,
                        savedBody: saved,
                        preparedBody,
                      });
                      if (!dirty) dirtyDrafts.current.delete(key);
                      else dirtyDrafts.current.add(key);
                      draftValues.current = { ...draftValues.current, [key]: value };
                      setDirtyFlags((current) => ({ ...current, [key]: dirty }));
                      setDrafts((current) => ({ ...current, [key]: value }));
                    }}
                    onSave={(kind, copyAfterSave) => void saveMessage(recipient, kind, copyAfterSave)}
                    onCopy={(message) => void copyMessage(recipient, message)}
                    onManualCopy={(message) => void copyMessage(recipient, message, true)}
                    onChannelChange={(messageId, channel) => setChannels((current) => ({
                      ...current,
                      [messageId]: channel,
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
              description="These people are resolved. Their exact sent messages remain here, and a late reply can still be attached to the message that received it."
              recipients={previousRecipients}
              reserve={false}
              replyUi={replyUi}
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
            postingState={actionPostingState}
            applicationContactable={applicationContactable}
            contactabilityIssue={contactabilityIssue}
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

          <OutreachTimeline
            events={outreach!.timeline}
            recipients={outreach!.recipients}
            ownerTimezone={ownerTimezone}
          />
        </div>
      ) : null}
    </section>
  );
}

function SequenceSummary({
  outreach,
  postingState,
  applicationContactable,
  contactabilityIssue,
  onRefresh,
  busy,
}: {
  outreach: ApplicationOutreachResponse;
  postingState: ApplicationPostingState;
  applicationContactable: boolean;
  contactabilityIssue: string | null;
  onRefresh: () => void;
  busy: boolean;
}) {
  const sequence = outreach.sequence;
  if (!sequence) return null;
  const resolved = outreach.recipients.filter(
    recipientIsResolved,
  ).length;
  const ready = outreach.recipients.filter(
    (recipient) =>
      !recipientIsResolved(recipient) &&
      recipient.bench_state === "ready" &&
      recipient.lifecycle === "active",
  ).length;
  const paused = outreach.recipients.filter(
    (recipient) =>
      !recipientIsResolved(recipient) &&
      recipient.bench_state === "paused" &&
      recipient.lifecycle === "active",
  ).length;
  const reserve = outreach.recipients.filter(
    (recipient) =>
      !recipientIsResolved(recipient) &&
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
      {!applicationContactable ? (
        <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          {contactabilityIssue} Saved messages and outcomes remain readable.
        </p>
      ) : postingState !== "open" ? (
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
  replyUi,
}: {
  title: string;
  description: string;
  recipients: OutreachRecipient[];
  reserve: boolean;
  unavailable?: boolean;
  replyUi?: ReplyUi;
}) {
  return (
    <details className="rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
      <summary className="min-h-11 cursor-pointer list-none font-medium focus:outline-none focus:ring-2 focus:ring-indigo-500">
        {title}
      </summary>
      <p className="mt-2 text-sm leading-6 text-zinc-500">{description}</p>
      <ul className="mt-3 divide-y divide-zinc-200 dark:divide-zinc-800">
        {recipients.map((recipient) => (
            <li key={recipient.application_contact_id} className="min-w-0 py-3">
              <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <p className="break-words font-medium">{recipient.public_name}</p>
                  <p className="break-words text-sm text-zinc-500">Source result: {recipient.current_title}</p>
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
              {!reserve && hasSentHistory(recipient) ? (
                <SentAttemptList
                  recipient={recipient}
                  replyUi={unavailable ? undefined : replyUi}
                  compact
                />
              ) : null}
            </li>
          ))}
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
  draftsPrepared,
  channelByMessage,
  locallyCopied,
  manualCopyFallback,
  outcomeChoice,
  busy,
  unresolvedIntent,
  replyUi,
  onDraftChange,
  onSave,
  onCopy,
  onManualCopy,
  onChannelChange,
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
  draftsPrepared: boolean;
  channelByMessage: Record<string, OutreachChannel>;
  locallyCopied: Record<string, boolean>;
  manualCopyFallback: Record<string, boolean>;
  outcomeChoice: OutreachNonReplyOutcome | "";
  busy: string | null;
  unresolvedIntent: string | null;
  replyUi: ReplyUi;
  onDraftChange: (kind: OutreachMessageKind, value: string) => void;
  onSave: (kind: OutreachMessageKind, copyAfterSave: boolean) => void;
  onCopy: (message: OutreachMessageVersion) => void;
  onManualCopy: (message: OutreachMessageVersion) => void;
  onChannelChange: (messageId: string, channel: OutreachChannel) => void;
  onMarkSent: (message: OutreachMessageVersion) => void;
  onOutcomeChange: (outcome: OutreachNonReplyOutcome | "") => void;
  onRecordOutcome: () => void;
}) {
  const resolved = recipientIsResolved(recipient);
  const latestReply = latestRecordedReply(recipient);
  const active =
    !resolved &&
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
            Source result: {recipient.current_title} · {recipient.current_company}
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

      {latestReply ? (
        <p className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-950 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100">
          Reply recorded: <strong>{replyKindLabel(latestReply.reply_kind)}</strong>
          {` · received ${formatLocalDate(latestReply.received_on)}`}
        </p>
      ) : null}

      {hasSentHistory(recipient) ? (
        <SentAttemptList recipient={recipient} replyUi={replyUi} />
      ) : null}

      {!resolved && !initial?.sent_at ? (
        <MessageEditor
          recipientId={recipient.application_contact_id}
          recipientName={recipient.public_name}
          kind="initial"
          value={draftInitial}
          dirty={initialDirty}
          prepared={draftsPrepared && !initial && Boolean(draftInitial) && !initialDirty}
          saved={initial}
          enabled={active}
          busy={busy}
          unresolvedIntent={unresolvedIntent}
          locallyCopied={initial ? Boolean(locallyCopied[initial.id]) : false}
          manualCopyFallback={initial ? Boolean(manualCopyFallback[initial.id]) : false}
          channel={initial ? channelByMessage[initial.id] ?? "linkedin" : "linkedin"}
          copyAvailable={active}
          onChange={(value) => onDraftChange("initial", value)}
          onSave={(copyAfterSave) => onSave("initial", copyAfterSave)}
          onCopy={() => initial && onCopy(initial)}
          onManualCopy={() => initial && onManualCopy(initial)}
          onChannelChange={(channel) => initial && onChannelChange(initial.id, channel)}
          onMarkSent={() => initial && onMarkSent(initial)}
        />
      ) : null}

      {initial?.sent_at && !resolved && !followUp?.sent_at ? (
        <div className="mt-5 border-t border-zinc-200 pt-5 dark:border-zinc-800">
          <MessageEditor
            recipientId={recipient.application_contact_id}
            recipientName={recipient.public_name}
            kind="follow_up"
            value={draftFollowUp}
            dirty={followUpDirty}
            prepared={draftsPrepared && !followUp && Boolean(draftFollowUp) && !followUpDirty}
            saved={followUp}
            enabled={active && !followUp?.sent_at}
            busy={busy}
            unresolvedIntent={unresolvedIntent}
            locallyCopied={followUp ? Boolean(locallyCopied[followUp.id]) : false}
            manualCopyFallback={followUp ? Boolean(manualCopyFallback[followUp.id]) : false}
            channel={followUp ? channelByMessage[followUp.id] ?? "linkedin" : "linkedin"}
            copyAvailable={active && deadlineReached(recipient.follow_up_due_at)}
            unavailableCopyReason={
              !deadlineReached(recipient.follow_up_due_at)
                ? `Copy and send become available ${formatDate(recipient.follow_up_due_at)}.`
                : undefined
            }
            onChange={(value) => onDraftChange("follow_up", value)}
            onSave={(copyAfterSave) => onSave("follow_up", copyAfterSave)}
            onCopy={() => followUp && onCopy(followUp)}
            onManualCopy={() => followUp && onManualCopy(followUp)}
            onChannelChange={(channel) => followUp && onChannelChange(followUp.id, channel)}
            onMarkSent={() => followUp && onMarkSent(followUp)}
          />
        </div>
      ) : null}

      {outcomeOptions.length > 0 ? (
        <div className="mt-5 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
          <label htmlFor={`outcome-${recipient.application_contact_id}`} className="text-sm font-medium">
            Close without a reply
          </label>
          <p className="mt-1 text-xs leading-5 text-zinc-500">
            Use this only when nobody replied. Replies belong to the exact sent message above.
          </p>
          <select
            id={`outcome-${recipient.application_contact_id}`}
            value={selectedOutcome}
            disabled={Boolean(busy) || Boolean(unresolvedIntent)}
            onChange={(event) => onOutcomeChange(event.target.value as OutreachNonReplyOutcome | "")}
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
            Close without a reply
          </button>
        </div>
      ) : null}

      {!active && sequenceStatus === "paused" && !resolved ? (
        <p className="mt-4 text-xs leading-5 text-zinc-500">
          Resume this plan before editing, copying, sending, or closing without a reply.
          Any reply to an already-sent message can still be recorded above.
        </p>
      ) : null}
      {sequenceStatus === "active" && !active && !resolved ? (
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
  prepared,
  saved,
  enabled,
  busy,
  unresolvedIntent,
  locallyCopied,
  manualCopyFallback,
  channel,
  copyAvailable,
  unavailableCopyReason,
  onChange,
  onSave,
  onCopy,
  onManualCopy,
  onChannelChange,
  onMarkSent,
}: {
  recipientId: string;
  recipientName: string;
  kind: OutreachMessageKind;
  value: string;
  dirty: boolean;
  prepared: boolean;
  saved: OutreachMessageVersion | null;
  enabled: boolean;
  busy: string | null;
  unresolvedIntent: string | null;
  locallyCopied: boolean;
  manualCopyFallback: boolean;
  channel: OutreachChannel;
  copyAvailable: boolean;
  unavailableCopyReason?: string;
  onChange: (value: string) => void;
  onSave: (copyAfterSave: boolean) => void;
  onCopy: () => void;
  onManualCopy: () => void;
  onChannelChange: (channel: OutreachChannel) => void;
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
    !blocked(sentIntent),
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
        {prepared
          ? "Prepared from this role’s exact approved materials and the person’s public search result. Review and edit it; nothing sends automatically."
          : "Keep it specific and truthful. The text is saved exactly as written, and nothing sends automatically."}
      </p>
      <textarea
        id={inputId}
        aria-describedby={`${inputId}-help`}
        value={value}
        maxLength={MAX_MESSAGE_CHARS}
        disabled={!enabled || Boolean(unresolvedIntent) || Boolean(busy)}
        onChange={(event) => onChange(event.target.value)}
        placeholder={kind === "initial"
          ? "No grounded starting draft is available; write a short, specific message…"
          : "No grounded follow-up is available; write one brief, respectful note…"}
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
          onClick={() => onSave(copyAvailable)}
          className={primaryButtonClasses}
        >
          {copyAvailable
            ? saved
              ? "Save new version & copy"
              : "Save & copy exact message"
            : saved
              ? "Save as new version"
              : "Save exact message"}
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
              <div className="mt-3 max-w-xs">
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
              </div>
              <button
                type="button"
                disabled={!canMarkSent}
                onClick={onMarkSent}
                className={`${primaryButtonClasses} mt-3 w-full sm:w-auto`}
              >
                I sent version {saved.version_number} — record via {channelLabel(channel)}
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function SentAttemptList({
  recipient,
  replyUi,
  compact = false,
}: {
  recipient: OutreachRecipient;
  replyUi?: ReplyUi;
  compact?: boolean;
}) {
  const attempts = [...(recipient.sent_attempts ?? [])].sort((left, right) => (
    left.sent_at.localeCompare(right.sent_at) ||
    left.marked_sent_event_id.localeCompare(right.marked_sent_event_id)
  ));
  const projectedMessageIds = new Set(
    attempts.map((attempt) => attempt.message_version_id),
  );
  const legacyMessages = [recipient.initial_message, recipient.follow_up_message]
    .filter((message): message is OutreachMessageVersion => Boolean(
      message?.sent_at && !projectedMessageIds.has(message.id),
    ));
  if (attempts.length === 0 && legacyMessages.length === 0) return null;

  return (
    <section
      aria-label={`Exact sent messages and replies for ${recipient.public_name}`}
      className={compact ? "mt-3 space-y-3" : "mt-4 space-y-3"}
    >
      {attempts.map((attempt) => (
        <SentAttemptCard
          key={attempt.marked_sent_event_id}
          recipient={recipient}
          attempt={attempt}
          replyUi={replyUi}
        />
      ))}
      {legacyMessages.map((message) => (
        <SentMessageSummary key={message.id} message={message} />
      ))}
    </section>
  );
}

function SentAttemptCard({
  recipient,
  attempt,
  replyUi,
}: {
  recipient: OutreachRecipient;
  attempt: OutreachSentAttempt;
  replyUi?: ReplyUi;
}) {
  const intent = replyIntent(attempt.marked_sent_event_id);
  const editorOpen = replyUi?.editorAttemptId === attempt.marked_sent_event_id;
  const pendingThis = replyUi?.unresolvedIntent === intent;
  const payload = replyUi ? replyPayload(attempt, replyUi.draft) : null;
  const payloadFingerprint = payload ? replyFingerprint(payload) : null;
  const confirmationMatches = Boolean(
    payloadFingerprint &&
    replyUi?.draft.confirmationFingerprint === payloadFingerprint,
  );
  const dateOutsideBounds = Boolean(
    payload && (
      payload.received_on > (replyUi?.ownerLocalDate ?? payload.received_on) ||
      (attempt.sent_local_on && payload.received_on < attempt.sent_local_on)
    ),
  );
  const noteTooLong = Boolean(
    replyUi && replyUi.draft.note.length > MAX_REPLY_NOTE_CHARS,
  );
  const fieldsLocked = Boolean(replyUi?.busy || replyUi?.unresolvedIntent);
  const submitBlocked = Boolean(
    !payload ||
    dateOutsideBounds ||
    noteTooLong ||
    !confirmationMatches ||
    replyUi?.busy ||
    (replyUi?.unresolvedIntent && !pendingThis),
  );
  const formId = replyFormId(attempt.marked_sent_event_id);

  return (
    <article className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 dark:border-emerald-900 dark:bg-emerald-950/30">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-emerald-950 dark:text-emerald-100">
            {kindLabel(attempt.kind)} version {attempt.version_number} recorded as sent
          </p>
          <p className="mt-1 text-xs text-emerald-800 dark:text-emerald-300">
            {channelLabel(attempt.channel)} · Sent {formatLocalDate(attempt.sent_local_on)} in {replyUi?.ownerTimezone ?? "your workspace timezone"}
          </p>
        </div>
        <span className="shrink-0 rounded-full bg-white/80 px-2.5 py-1 text-[11px] font-semibold text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-200">
          Exact attempt
        </span>
      </div>

      <details className="mt-3">
        <summary className="min-h-9 cursor-pointer text-xs font-medium text-emerald-900 focus:outline-none focus:ring-2 focus:ring-indigo-500 dark:text-emerald-200">
          Review exact sent text
        </summary>
        <pre className="mt-2 whitespace-pre-wrap break-words font-sans text-sm leading-6 [overflow-wrap:anywhere]">
          {attempt.body}
        </pre>
      </details>

      <ReplyHistory
        attempt={attempt}
        ownerTimezone={replyUi?.ownerTimezone}
      />

      {replyUi && !editorOpen ? (
        <button
          id={replyTriggerId(attempt.marked_sent_event_id)}
          type="button"
          aria-expanded={false}
          aria-controls={formId}
          disabled={Boolean(replyUi.busy) || Boolean(replyUi.unresolvedIntent)}
          onClick={() => replyUi.onOpen(attempt)}
          className={`${secondaryButtonClasses} mt-4 w-full bg-white/80 sm:w-auto dark:bg-zinc-950/70`}
        >
          Record a reply to this message
        </button>
      ) : null}

      {replyUi && editorOpen ? (
        <form
          id={formId}
          className="mt-4 border-t border-emerald-200 pt-4 dark:border-emerald-900"
          onSubmit={(event) => {
            event.preventDefault();
            replyUi.onRecord(recipient, attempt);
          }}
        >
          <fieldset disabled={fieldsLocked}>
            <legend className="text-sm font-semibold text-emerald-950 dark:text-emerald-100">
              Record a reply to this exact sent message
            </legend>
            <p className="mt-1 text-xs leading-5 text-emerald-800 dark:text-emerald-300">
              Save the fact against {kindLabel(attempt.kind).toLowerCase()} version {attempt.version_number}.
              This does not send anything or reopen message actions.
            </p>

            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <label htmlFor={replyKindInputId(attempt.marked_sent_event_id)} className="text-sm">
                <span className="block font-medium">What kind of reply arrived?</span>
                <select
                  id={replyKindInputId(attempt.marked_sent_event_id)}
                  value={replyUi.draft.replyKind}
                  onChange={(event) => replyUi.onReplyKindChange(
                    event.target.value as OutreachReplyKind | "",
                  )}
                  className={`${inputClasses} mt-1 bg-white dark:bg-zinc-950`}
                >
                  <option value="">Choose reply kind</option>
                  {REPLY_KINDS.map((kind) => (
                    <option key={kind} value={kind}>{replyKindLabel(kind)}</option>
                  ))}
                </select>
              </label>

              <label htmlFor={replyDateInputId(attempt.marked_sent_event_id)} className="text-sm">
                <span className="block font-medium">Date received</span>
                <input
                  id={replyDateInputId(attempt.marked_sent_event_id)}
                  type="date"
                  value={replyUi.draft.receivedOn}
                  min={attempt.sent_local_on || undefined}
                  max={replyUi.ownerLocalDate}
                  onChange={(event) => replyUi.onReceivedOnChange(event.target.value)}
                  className={`${inputClasses} mt-1 bg-white dark:bg-zinc-950`}
                />
                <span className="mt-1 block text-xs leading-5 text-emerald-800 dark:text-emerald-300">
                  {attempt.sent_local_on
                    ? `Your workspace date; not before ${formatLocalDate(attempt.sent_local_on)}.`
                    : "Dates use your workspace timezone."}
                </span>
              </label>
            </div>

            {replyUi.draft.replyKind ? (
              <p
                role="note"
                className={`mt-4 rounded-lg border p-3 text-xs leading-5 ${
                  replyUi.draft.replyKind === "do_not_contact"
                    ? "border-red-200 bg-red-50 text-red-900 dark:border-red-900 dark:bg-red-950/30 dark:text-red-100"
                    : "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100"
                }`}
              >
                <strong>What saving this changes:</strong>{" "}
                {replyKindEffect(replyUi.draft.replyKind)}
              </p>
            ) : null}

            <label htmlFor={replyNoteInputId(attempt.marked_sent_event_id)} className="mt-4 block text-sm">
              <span className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-medium">Private note (optional)</span>
                <span className={replyUi.draft.note.length > MAX_REPLY_NOTE_CHARS ? "text-xs text-red-700" : "text-xs text-emerald-800 dark:text-emerald-300"}>
                  {replyUi.draft.note.length.toLocaleString()}/{MAX_REPLY_NOTE_CHARS.toLocaleString()}
                </span>
              </span>
              <textarea
                id={replyNoteInputId(attempt.marked_sent_event_id)}
                value={replyUi.draft.note}
                maxLength={MAX_REPLY_NOTE_CHARS}
                rows={3}
                onChange={(event) => replyUi.onNoteChange(event.target.value)}
                placeholder="Optional context for your private job-search record"
                className={`${textareaClasses} mt-1 bg-white dark:bg-zinc-950`}
              />
              <span className="mt-1 block text-xs leading-5 text-emerald-800 dark:text-emerald-300">
                Keep this brief and private; do not paste sensitive correspondence.
              </span>
            </label>

            <label className="mt-4 flex min-h-11 items-start gap-3 rounded-lg border border-emerald-200 bg-white/80 p-3 text-sm dark:border-emerald-900 dark:bg-zinc-950/70">
              <input
                type="checkbox"
                checked={confirmationMatches}
                disabled={!payload || dateOutsideBounds || noteTooLong}
                onChange={(event) => replyUi.onConfirmationChange(
                  attempt,
                  event.target.checked,
                )}
                className="mt-0.5 h-5 w-5 shrink-0 accent-indigo-600"
              />
              <span>
                I confirm this reply belongs to this exact sent message version,
                not another message or attempt.
              </span>
            </label>
          </fieldset>

          {pendingThis ? (
            <p className="mt-3 text-xs font-medium text-amber-800 dark:text-amber-300">
              The last save result is unconfirmed. The exact fields are locked;
              retrying below uses the same receipt and cannot duplicate the reply.
            </p>
          ) : null}
          {dateOutsideBounds ? (
            <p className="mt-3 text-xs font-medium text-red-800 dark:text-red-300">
              {attempt.sent_local_on
                ? `Choose a date from ${formatLocalDate(attempt.sent_local_on)} through ${formatLocalDate(replyUi.ownerLocalDate)}.`
                : `Choose a date no later than ${formatLocalDate(replyUi.ownerLocalDate)}.`}
            </p>
          ) : null}
          <div className="mt-4 flex flex-col-reverse gap-2 sm:flex-row">
            <button
              type="button"
              disabled={fieldsLocked}
              onClick={replyUi.onCancel}
              className={secondaryButtonClasses}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitBlocked}
              className={primaryButtonClasses}
            >
              {replyUi.busy === intent
                ? "Saving reply…"
                : pendingThis
                  ? "Retry exact reply safely"
                  : "Save reply"}
            </button>
          </div>
        </form>
      ) : null}
    </article>
  );
}

function ReplyHistory({
  attempt,
  ownerTimezone,
}: {
  attempt: OutreachSentAttempt;
  ownerTimezone?: string;
}) {
  const replies = [...(attempt.replies ?? [])].sort((left, right) => (
    left.received_on.localeCompare(right.received_on) ||
    left.recorded_at.localeCompare(right.recorded_at) ||
    left.id.localeCompare(right.id)
  ));
  if (replies.length === 0) return null;
  return (
    <div className="mt-4 border-t border-emerald-200 pt-4 dark:border-emerald-900">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-semibold text-emerald-950 dark:text-emerald-100">
          Saved replies · {replies.length}
        </p>
        <span className="text-[11px] font-medium text-emerald-800 dark:text-emerald-300">
          Immutable history
        </span>
      </div>
      <ol className="mt-3 space-y-3">
        {replies.map((reply) => (
          <li
            key={reply.id}
            className="rounded-lg border border-emerald-200 bg-white/80 p-3 dark:border-emerald-900 dark:bg-zinc-950/70"
          >
            <p className="text-sm font-semibold">{replyKindLabel(reply.reply_kind)}</p>
            <p className="mt-1 text-xs text-zinc-500">
              Received {formatLocalDate(reply.received_on)}
              {ownerTimezone
                ? ` · Recorded ${formatOwnerTimestamp(reply.recorded_at, ownerTimezone)}`
                : ""}
            </p>
            {reply.note ? (
              <div className="mt-2 border-t border-zinc-200 pt-2 dark:border-zinc-800">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
                  Private note
                </p>
                <p className="mt-1 whitespace-pre-wrap break-words text-sm leading-6 [overflow-wrap:anywhere]">
                  {reply.note}
                </p>
              </div>
            ) : null}
          </li>
        ))}
      </ol>
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
      <p className="mt-3 text-xs leading-5 text-emerald-800 dark:text-emerald-300">
        Refresh to load the exact send-attempt record before attaching a reply.
      </p>
    </div>
  );
}

function SequenceControls({
  status,
  reason,
  postingState,
  applicationContactable,
  contactabilityIssue,
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
  applicationContactable: boolean;
  contactabilityIssue: string | null;
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
          No further messages can be sent. Saved activity remains available, and
          replies to an already-sent message can still be recorded on that exact attempt.
        </p>
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
      <h3 className="font-semibold">Plan controls</h3>
      <p className="mt-1 text-sm text-zinc-500">
        {contactabilityIssue
          ? `${contactabilityIssue} Only an explicit stop remains available.`
          : status === "paused"
            ? `Paused${reason ? `: ${sequenceReasonLabel(reason)}` : "."}`
            : postingState === "open"
              ? "Pause when you need time; stop only when this plan should end permanently."
              : `This posting is ${postingState}; only an explicit stop remains available.`}
      </p>
      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
        {applicationContactable && postingState === "open" && status === "active" ? (
          <button type="button" disabled={busy || Boolean(unresolvedIntent)} onClick={() => onChoose("pause")} className={secondaryButtonClasses}>
            Pause outreach
          </button>
        ) : applicationContactable && postingState === "open" ? (
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

function OutreachTimeline({
  events,
  recipients,
  ownerTimezone,
}: {
  events: OutreachTimelineEvent[];
  recipients: OutreachRecipient[];
  ownerTimezone: string;
}) {
  if (events.length === 0) return null;
  return (
    <details className="rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
      <summary className="min-h-11 cursor-pointer list-none font-medium focus:outline-none focus:ring-2 focus:ring-indigo-500">
        Saved outreach activity · {events.length}
      </summary>
      <ol className="mt-3 space-y-3 border-l border-zinc-200 pl-4 dark:border-zinc-800">
        {events.slice().reverse().map((event) => {
          const detail = timelineDetail(event, recipients);
          return (
            <li key={event.id} className="text-sm">
              <p className="font-medium">{timelineLabel(event, recipients)}</p>
              <p className="mt-0.5 text-xs text-zinc-500">
                {detail
                  ? `${detail} · Logged ${formatOwnerTimestamp(event.occurred_at, ownerTimezone)}`
                  : formatOwnerTimestamp(event.occurred_at, ownerTimezone)}
              </p>
            </li>
          );
        })}
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

function findSentAttempt(
  response: ApplicationOutreachResponse,
  markedSentEventId: string,
) {
  for (const recipient of response.recipients) {
    const attempt = (recipient.sent_attempts ?? []).find(
      (candidate) => candidate.marked_sent_event_id === markedSentEventId,
    );
    if (attempt) return attempt;
  }
  return null;
}

function hasSentHistory(recipient: OutreachRecipient) {
  return Boolean(
    (recipient.sent_attempts ?? []).length > 0 ||
    recipient.initial_message?.sent_at ||
    recipient.follow_up_message?.sent_at,
  );
}

function latestRecordedReply(recipient: OutreachRecipient) {
  const replies = (recipient.sent_attempts ?? []).flatMap(
    (attempt) => attempt.replies ?? [],
  );
  return replies.reduce<OutreachSentAttempt["replies"][number] | null>(
    (latest, reply) => {
      if (!latest) return reply;
      return reply.recorded_at > latest.recorded_at ||
        (reply.recorded_at === latest.recorded_at && reply.id > latest.id)
        ? reply
        : latest;
    },
    null,
  );
}

function recipientIsResolved(recipient: OutreachRecipient) {
  return recipient.outcome !== null || latestRecordedReply(recipient) !== null;
}

function replyIntent(markedSentEventId: string) {
  return `reply:${markedSentEventId}`;
}

function replyPayload(
  attempt: OutreachSentAttempt,
  draft: ReplyDraft,
): OutreachReplyCreate | null {
  if (!draft.replyKind || !/^\d{4}-\d{2}-\d{2}$/.test(draft.receivedOn)) {
    return null;
  }
  const note = draft.note.trim();
  return {
    marked_sent_event_id: attempt.marked_sent_event_id,
    reply_kind: draft.replyKind,
    received_on: draft.receivedOn,
    note: note || null,
    confirm_exact_sent_attempt: true,
  };
}

function replyFingerprint(payload: OutreachReplyCreate) {
  return JSON.stringify(payload);
}

function replyMatches(
  reply: OutreachSentAttempt["replies"][number],
  payload: OutreachReplyCreate,
) {
  return (
    reply.marked_sent_event_id === payload.marked_sent_event_id &&
    reply.reply_kind === payload.reply_kind &&
    reply.received_on === payload.received_on &&
    (reply.note ?? null) === (payload.note ?? null)
  );
}

function replyFormId(markedSentEventId: string) {
  return `outreach-reply-form-${markedSentEventId}`;
}

function replyTriggerId(markedSentEventId: string) {
  return `outreach-reply-trigger-${markedSentEventId}`;
}

function replyKindInputId(markedSentEventId: string) {
  return `outreach-reply-kind-${markedSentEventId}`;
}

function replyDateInputId(markedSentEventId: string) {
  return `outreach-reply-date-${markedSentEventId}`;
}

function replyNoteInputId(markedSentEventId: string) {
  return `outreach-reply-note-${markedSentEventId}`;
}

function allowedOutcomes(
  recipient: RecipientDeadline,
  status: "active" | "paused" | "stopped" | "completed",
  postingState: ApplicationPostingState,
): OutreachNonReplyOutcome[] {
  if (postingState !== "open") return [];
  if (status === "stopped" || status === "completed") return [];
  if (status === "paused") {
    return [];
  }
  if (recipient.bench_state !== "ready" || recipient.lifecycle !== "active") return [];
  if (recipientIsResolved(recipient)) return [];
  if (!recipient.initial_message?.sent_at) return ["unreachable"];
  return ["no_reply", "unreachable"];
}

function deadlineReached(value: string | null | undefined) {
  if (!value) return false;
  const date = new Date(value);
  return !Number.isNaN(date.getTime()) && date.getTime() <= Date.now();
}

function applicationContactBlockCopy(
  interviewHistoryState: InterviewHistoryState,
  applicationStage: ApplicationStage,
): string | null {
  if (interviewHistoryState === "checking") {
    return "Interview progress is still being checked, so new outreach is temporarily paused.";
  }
  if (interviewHistoryState === "unavailable") {
    return "Interview progress could not be verified, so new outreach is temporarily paused. Retry Interview rounds above.";
  }
  if (interviewHistoryState === "recorded") {
    return "An interview round is already recorded, so new outreach is closed for this role.";
  }
  if (!["pursuing", "ready_to_apply", "applied"].includes(applicationStage)) {
    return "Hiring progress is already recorded, so new outreach is closed for this role.";
  }
  return null;
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

function replyKindLabel(kind: OutreachReplyKind) {
  return ({
    reply_received: "They replied",
    useful_reply: "Useful reply / conversation started",
    introduced: "They introduced me",
    referred: "They referred me",
    declined: "They declined",
    do_not_contact: "They asked not to be contacted",
  } as const)[kind];
}

function replyKindEffect(kind: OutreachReplyKind) {
  if (kind === "do_not_contact") {
    return "This permanently marks this person as Do not contact. If this outreach plan is still live, it also stops every remaining message action.";
  }
  if (kind === "introduced" || kind === "referred") {
    return "This closes outreach for this person. If the plan is still live, it stops the remaining outreach because the referral goal was reached.";
  }
  return "This closes outreach for this person. If the plan is still live, remaining outreach pauses for your review; if nobody remains, the plan completes.";
}

function formatOwnerTimestamp(value: string, timeZone: string) {
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

function formatLocalDate(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return value;
  const date = new Date(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
  );
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
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

function timelineLabel(
  event: OutreachTimelineEvent,
  recipients: OutreachRecipient[],
) {
  switch (event.event_type) {
    case "sequence_started": return `Started manual outreach at wave ${event.wave}`;
    case "message_saved": return `Saved ${kindLabel(event.kind).toLowerCase()} version`;
    case "copied": return "Copied an exact saved message";
    case "marked_sent": return `Recorded a manual send via ${channelLabel(event.channel)}`;
    case "outcome_recorded": return `Recorded outcome: ${outcomeLabel(event.outcome)}`;
    case "reply_recorded": {
      const recipient = recipients.find(
        (candidate) => candidate.application_contact_id === event.application_contact_id,
      );
      return `${replyKindLabel(event.reply_kind)} from ${recipient?.public_name ?? "a contact"} · ${kindLabel(event.message_kind)} version ${event.message_version_number}`;
    }
    case "paused": return `Paused outreach: ${event.reason}`;
    case "resumed": return `Resumed outreach: ${event.reason}`;
    case "stopped": return `Stopped outreach: ${event.reason}`;
    case "wave_advanced": return `Unlocked wave ${event.wave}`;
  }
}

function timelineDetail(
  event: OutreachTimelineEvent,
  recipients: OutreachRecipient[],
) {
  if (event.event_type !== "reply_recorded") return null;
  const attempt = recipients
    .flatMap((recipient) => recipient.sent_attempts ?? [])
    .find((candidate) => (
      candidate.marked_sent_event_id === event.marked_sent_event_id
    ));
  return `Received ${formatLocalDate(event.received_on)}${attempt ? ` on the exact ${channelLabel(attempt.channel)} send` : ""}`;
}

function sentence(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return "Unable to update manual outreach.";
  return `${trimmed.charAt(0).toUpperCase()}${trimmed.slice(1)}`;
}
