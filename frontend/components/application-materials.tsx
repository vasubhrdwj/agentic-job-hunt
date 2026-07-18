"use client";

import { useCallback, useEffect, useEffectEvent, useMemo, useRef, useState } from "react";

import {
  createApplicationArtifactRevision,
  getApplicationArtifacts,
  recordApplicationArtifactEvent,
} from "@/lib/application-api";
import type {
  ApplicationArtifactBlocker,
  ApplicationArtifactEventCreate,
  ApplicationArtifactQuestionInput,
  ApplicationArtifactRevisionCreate,
  ApplicationArtifactRevisionResponse,
  ApplicationArtifactsResponse,
} from "@/lib/application-artifact-types";
import type { ApplicationStage } from "@/lib/application-types";
import { buildInitialMaterialsGenerationPlan } from "@/lib/application-materials-auto-generation";
import {
  buildGroundedFitStory,
  type GroundedFitStory,
} from "@/lib/grounded-fit-story";
import {
  createIdempotencyKey,
  WorkspaceApiError,
} from "@/lib/workspace-api";
import {
  errorText,
  FormField,
  formatDate,
  inputClasses,
  primaryButtonClasses,
  secondaryButtonClasses,
  StatusMessage,
  textareaClasses,
} from "./workspace-ui";

interface QuestionDraft {
  id: string;
  text: string;
  characterLimit: string;
  evidenceIds: string[];
}

type MutationIntent = "generate" | "approve" | "reject";

interface PendingMutation {
  intent: MutationIntent;
  key: string;
  fingerprint: string;
  expectedVersion: number;
  applied: (response: ApplicationArtifactsResponse) => boolean;
  successMessage: string;
  onConfirmed?: (response: ApplicationArtifactsResponse) => void;
}

interface MutationOptions {
  intent: MutationIntent;
  idempotencyKey?: string;
  fingerprint: string;
  expectedVersion: number;
  execute: (pending: PendingMutation) => Promise<ApplicationArtifactsResponse>;
  applied: (response: ApplicationArtifactsResponse) => boolean;
  successMessage: string;
  ambiguousMessage: string;
  onConfirmed?: (response: ApplicationArtifactsResponse) => void;
}

const BLOCKER_COPY: Record<ApplicationArtifactBlocker, string> = {
  application_pack_missing:
    "Start the fit and evidence review above before creating application materials.",
  grounding_review_required:
    "Mark one exact fit-and-evidence revision reviewed before creating materials.",
  posting_closed:
    "This posting is closed. Saved materials remain readable, but no new version can be created or approved.",
  grounded_evidence_missing:
    "Evidence pinned to the reviewed fit is unavailable. Review the fit again before continuing.",
  grounding_evidence_changed:
    "Approved evidence changed after this draft was created. Generate a new version from the current reviewed sources.",
  questions_need_owner_input:
    "One or more questions need your input before this exact version can be approved.",
  tailored_resume_unchanged:
    "The grounded generator found no safe résumé improvement. The unchanged résumé remains visible and cannot be disguised as tailored.",
  current_revision_rejected:
    "This exact draft was rejected. Change the inputs and create a new immutable version.",
};

const MAX_QUESTIONS = 20;

export function ApplicationMaterials({
  applicationId,
  applicationVersion,
  applicationStage,
  onArtifactsChanged,
}: {
  applicationId: string;
  applicationVersion: number;
  applicationStage: ApplicationStage;
  onArtifactsChanged?: (projection: ApplicationArtifactsResponse) => void;
}) {
  const [projection, setProjection] = useState<ApplicationArtifactsResponse | null>(null);
  const [questions, setQuestions] = useState<QuestionDraft[]>([]);
  const [selectedEvidenceIds, setSelectedEvidenceIds] = useState<string[]>([]);
  const [inputsDirty, setInputsDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<MutationIntent | null>(null);
  const [unresolvedIntent, setUnresolvedIntent] = useState<MutationIntent | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const projectionRef = useRef<ApplicationArtifactsResponse | null>(null);
  const dirtyRef = useRef(false);
  const pendingRef = useRef<PendingMutation | null>(null);
  const autoAttemptedKeyRef = useRef<string | null>(null);
  const requestGeneration = useRef(0);

  const setDirty = useCallback((value: boolean) => {
    dirtyRef.current = value;
    setInputsDirty(value);
  }, []);

  const hydrateInputs = useCallback((
    next: ApplicationArtifactsResponse,
    force = false,
  ) => {
    if (!force && dirtyRef.current) return;
    const revision = next.current_revision;
    const hydratedQuestions = revision
      ? revision.questions.map((question) => ({
          id: question.id,
          text: question.text,
          characterLimit: question.character_limit === null
            ? ""
            : String(question.character_limit),
          evidenceIds: question.evidence_refs.map((item) => item.id),
        }))
      : [];
    const evidenceIds = revision
      ? revision.selected_evidence.map((item) => item.id)
      : (next.source_catalog?.evidence ?? []).slice(0, 5).map((item) => item.id);
    setQuestions(hydratedQuestions);
    setSelectedEvidenceIds(evidenceIds);
    setDirty(false);
  }, [setDirty]);

  const acceptResponse = useCallback((
    next: ApplicationArtifactsResponse,
    generation: number,
    forceInputs = false,
  ) => {
    if (
      requestGeneration.current !== generation ||
      next.application_id !== applicationId
    ) return false;
    const previous = projectionRef.current;
    if (previous?.pack && next.pack && previous.pack.id !== next.pack.id) return false;
    if (
      previous?.pack &&
      next.pack &&
      next.pack.version < previous.pack.version
    ) return false;

    projectionRef.current = next;
    setProjection(next);
    onArtifactsChanged?.(next);
    hydrateInputs(next, forceInputs);

    const pending = pendingRef.current;
    if (pending && pending.applied(next)) {
      pendingRef.current = null;
      setUnresolvedIntent(null);
      pending.onConfirmed?.(next);
      setNotice(`${pending.successMessage} Confirmed from saved state.`);
    } else if (
      pending &&
      next.pack &&
      next.pack.version > pending.expectedVersion
    ) {
      pendingRef.current = null;
      setUnresolvedIntent(null);
      setActionError(
        "The saved pack advanced without this exact result. Review the newer version before starting another action.",
      );
    }
    return true;
  }, [applicationId, hydrateInputs, onArtifactsChanged]);

  const refresh = useCallback(async (showLoading = false) => {
    const generation = ++requestGeneration.current;
    if (showLoading) setLoading(true);
    setLoadError(null);
    try {
      const next = await getApplicationArtifacts(applicationId);
      acceptResponse(next, generation);
    } catch (reason) {
      if (requestGeneration.current === generation) {
        setLoadError(errorText(reason, "Unable to load application materials."));
      }
    } finally {
      if (requestGeneration.current === generation) setLoading(false);
    }
  }, [acceptResponse, applicationId]);

  useEffect(() => {
    const timer = setTimeout(() => void refresh(true), 0);
    return () => {
      requestGeneration.current += 1;
      clearTimeout(timer);
    };
  }, [refresh]);

  useEffect(() => {
    function refreshOnFocus() {
      if (!busy) void refresh(false);
    }
    window.addEventListener("focus", refreshOnFocus);
    return () => window.removeEventListener("focus", refreshOnFocus);
  }, [busy, refresh]);

  function pendingFor(options: MutationOptions): PendingMutation {
    const existing = pendingRef.current;
    if (existing) {
      if (
        existing.intent !== options.intent ||
        existing.fingerprint !== options.fingerprint ||
        existing.expectedVersion !== options.expectedVersion
      ) {
        throw new Error(
          "A different materials action still has an unconfirmed result. Retry that unchanged action before starting another one.",
        );
      }
      return existing;
    }
    const created: PendingMutation = {
      intent: options.intent,
      fingerprint: options.fingerprint,
      expectedVersion: options.expectedVersion,
      key: options.idempotencyKey ??
        createIdempotencyKey(`application-artifacts:${applicationId}:${options.intent}`),
      applied: options.applied,
      successMessage: options.successMessage,
      onConfirmed: options.onConfirmed,
    };
    pendingRef.current = created;
    return created;
  }

  async function runMutation(options: MutationOptions): Promise<boolean> {
    if (busy) return false;
    let pending: PendingMutation;
    try {
      pending = pendingFor(options);
    } catch (reason) {
      setActionError(errorText(reason, "Review the latest saved materials."));
      return false;
    }

    const generation = requestGeneration.current;
    setBusy(options.intent);
    setActionError(null);
    setNotice(null);
    try {
      const next = await options.execute(pending);
      if (requestGeneration.current !== generation) return false;
      const accepted = acceptResponse(next, generation);
      const current = projectionRef.current;
      const confirmed = options.applied(next) || Boolean(current && options.applied(current));
      if (accepted && confirmed) {
        const saved = options.applied(next) ? next : current!;
        pendingRef.current = null;
        setUnresolvedIntent(null);
        options.onConfirmed?.(saved);
        setNotice(options.successMessage);
        return true;
      }
      if (!accepted) {
        setUnresolvedIntent(options.intent);
        setActionError(
          "A newer saved view arrived first. Check saved state or retry this exact unchanged action.",
        );
        return false;
      }
      pendingRef.current = null;
      setUnresolvedIntent(null);
      setActionError(
        "The response did not contain the requested saved change. Review the latest state before trying again.",
      );
      return false;
    } catch (reason) {
      if (requestGeneration.current !== generation) return false;
      const apiError = reason instanceof WorkspaceApiError ? reason : null;
      const ambiguous = !apiError || apiError.retryable || apiError.code === "mutation_pending";
      const conflict = apiError && !ambiguous && [409, 412, 428].includes(apiError.status);
      if (conflict) {
        pendingRef.current = null;
        setUnresolvedIntent(null);
        await refresh(false);
        setActionError(
          "These materials changed in another tab. The newer saved version is shown and your question choices remain here.",
        );
        return false;
      }
      if (ambiguous) {
        try {
          const checked = await getApplicationArtifacts(applicationId);
          const accepted = acceptResponse(checked, generation);
          const current = projectionRef.current;
          if (
            (accepted && options.applied(checked)) ||
            Boolean(current && options.applied(current))
          ) {
            const saved = current && options.applied(current) ? current : checked;
            pendingRef.current = null;
            setUnresolvedIntent(null);
            options.onConfirmed?.(saved);
            setNotice(`${options.successMessage} Confirmed from saved state.`);
            return true;
          }
          if (
            checked.pack &&
            checked.pack.version > pending.expectedVersion
          ) {
            pendingRef.current = null;
            setUnresolvedIntent(null);
            setActionError(
              "The pack changed while this action was checked and the requested result is not present. Review the latest version before continuing.",
            );
            return false;
          }
        } catch {
          // Preserve the exact payload and receipt for a safe unchanged retry.
        }
        setUnresolvedIntent(options.intent);
        setActionError(
          `${options.ambiguousMessage} Check saved state or retry unchanged; the same receipt cannot create a duplicate.`,
        );
        return false;
      }

      pendingRef.current = null;
      setUnresolvedIntent(null);
      setActionError(errorText(reason, "Unable to save application materials."));
      return false;
    } finally {
      setBusy(null);
    }
  }

  const sourceCatalog = projection?.source_catalog ?? null;
  const selectedEvidence = useMemo(() => {
    const selected = new Set(selectedEvidenceIds);
    return (sourceCatalog?.evidence ?? []).filter((item) => selected.has(item.id));
  }, [selectedEvidenceIds, sourceCatalog]);

  function buildQuestions(): ApplicationArtifactQuestionInput[] | null {
    const result: ApplicationArtifactQuestionInput[] = [];
    const evidenceById = new Map(
      (sourceCatalog?.evidence ?? []).map((item) => [item.id, item]),
    );
    for (const question of questions) {
      if (!question.text.trim()) continue;
      const parsedLimit = question.characterLimit.trim()
        ? Number(question.characterLimit)
        : null;
      if (
        parsedLimit !== null &&
        (!Number.isInteger(parsedLimit) || parsedLimit < 1 || parsedLimit > 10_000)
      ) return null;
      result.push({
        id: question.id,
        text: question.text,
        character_limit: parsedLimit,
        evidence_refs: question.evidenceIds.flatMap((id) => {
          const evidence = evidenceById.get(id);
          return evidence ? [{ id: evidence.id, version: evidence.version }] : [];
        }),
      });
    }
    return result;
  }

  const questionPayloadValid = buildQuestions() !== null;
  const initialGenerationPlan = useMemo(() => buildInitialMaterialsGenerationPlan({
    applicationId,
    applicationStage,
    projection,
    selectedEvidenceIds,
    questionCount: questions.length,
    questionsValid: questionPayloadValid,
    inputsDirty,
  }), [
    applicationId,
    applicationStage,
    inputsDirty,
    projection,
    questionPayloadValid,
    questions.length,
    selectedEvidenceIds,
  ]);

  const runInitialMutation = useEffectEvent(runMutation);

  useEffect(() => {
    if (
      !initialGenerationPlan ||
      busy ||
      unresolvedIntent ||
      autoAttemptedKeyRef.current === initialGenerationPlan.idempotencyKey
    ) return;
    autoAttemptedKeyRef.current = initialGenerationPlan.idempotencyKey;
    const { payload } = initialGenerationPlan;
    void runInitialMutation({
      intent: "generate",
      idempotencyKey: initialGenerationPlan.idempotencyKey,
      fingerprint: JSON.stringify(payload),
      expectedVersion: initialGenerationPlan.expectedPackVersion,
      execute: (pending) => createApplicationArtifactRevision(
        applicationId,
        initialGenerationPlan.packId,
        pending.expectedVersion,
        pending.key,
        payload,
      ),
      applied: (next) => revisionMatchesPayload(next.current_revision, payload),
      successMessage: "Grounded application materials were prepared automatically.",
      ambiguousMessage: "The automatic materials version may already be saved.",
      onConfirmed: (next) => hydrateInputs(next, true),
    });
  }, [
    applicationId,
    busy,
    hydrateInputs,
    initialGenerationPlan,
    unresolvedIntent,
  ]);

  async function generateRevision() {
    if (!projection?.pack || !sourceCatalog?.reviewed_grounding_revision_id) return;
    const exactQuestions = buildQuestions();
    if (!exactQuestions || selectedEvidence.length === 0) return;
    const payload: ApplicationArtifactRevisionCreate = {
      operation: "generate",
      grounding_revision_id: sourceCatalog.reviewed_grounding_revision_id,
      parent_artifact_revision_id: projection.current_revision?.id ?? null,
      generation_mode: "deterministic",
      selected_evidence_refs: selectedEvidence.map((item) => ({
        id: item.id,
        version: item.version,
      })),
      questions: exactQuestions,
    };
    await runMutation({
      intent: "generate",
      fingerprint: JSON.stringify(payload),
      expectedVersion: projection.pack.version,
      execute: (pending) => createApplicationArtifactRevision(
        applicationId,
        projection.pack!.id,
        pending.expectedVersion,
        pending.key,
        payload,
      ),
      applied: (next) => revisionMatchesPayload(next.current_revision, payload),
      successMessage: payload.parent_artifact_revision_id
        ? "A new immutable materials version was created."
        : "Grounded application materials were created.",
      ambiguousMessage: "The generated version may already be saved.",
      onConfirmed: (next) => hydrateInputs(next, true),
    });
  }

  async function recordEvent(eventType: "approved" | "rejected") {
    const pack = projection?.pack;
    const revision = projection?.current_revision;
    if (!pack || !revision) return;
    const payload: ApplicationArtifactEventCreate = eventType === "approved"
      ? {
          event_type: "approved",
          artifact_revision_id: revision.id,
          confirm_artifacts_reviewed: true,
        }
      : {
          event_type: "rejected",
          artifact_revision_id: revision.id,
        };
    await runMutation({
      intent: eventType === "approved" ? "approve" : "reject",
      fingerprint: JSON.stringify(payload),
      expectedVersion: pack.version,
      execute: (pending) => recordApplicationArtifactEvent(
        applicationId,
        pack.id,
        pending.expectedVersion,
        pending.key,
        payload,
      ),
      applied: (next) => eventType === "approved"
        ? Boolean(
            next.status === "approved" &&
            next.approved_revision?.id === revision.id &&
            next.approval_event?.artifact_revision_id === revision.id,
          )
        : Boolean(
            next.current_event?.event_type === "rejected" &&
            next.current_event.artifact_revision_id === revision.id,
          ),
      successMessage: eventType === "approved"
        ? `Materials revision ${revision.revision_number} was approved exactly.`
        : `Materials revision ${revision.revision_number} was rejected.`,
      ambiguousMessage: eventType === "approved"
        ? "The approval may already be recorded."
        : "The rejection may already be recorded.",
    });
  }

  function addQuestion() {
    if (questions.length >= MAX_QUESTIONS) return;
    setQuestions((current) => [...current, emptyQuestion()]);
    setDirty(true);
  }

  function updateQuestion(id: string, patch: Partial<QuestionDraft>) {
    setQuestions((current) => current.map((question) => (
      question.id === id ? { ...question, ...patch } : question
    )));
    setDirty(true);
  }

  function removeQuestion(id: string) {
    setQuestions((current) => current.filter((question) => question.id !== id));
    setDirty(true);
  }

  function toggleSelectedEvidence(id: string) {
    setSelectedEvidenceIds((current) => {
      if (current.includes(id)) return current.filter((item) => item !== id);
      return current.length >= 5 ? current : [...current, id];
    });
    setDirty(true);
  }

  function toggleQuestionEvidence(question: QuestionDraft, evidenceId: string) {
    const evidenceIds = question.evidenceIds.includes(evidenceId)
      ? question.evidenceIds.filter((item) => item !== evidenceId)
      : question.evidenceIds.length >= 3
        ? question.evidenceIds
        : [...question.evidenceIds, evidenceId];
    updateQuestion(question.id, { evidenceIds });
  }

  async function copyText(key: string, text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(key);
      window.setTimeout(() => setCopied((current) => current === key ? null : current), 1800);
    } catch {
      setActionError(
        "Clipboard access was blocked. Select the visible exact text and copy it manually.",
      );
    }
  }

  if (loading && !projection) {
    return (
      <section className="rounded-2xl border border-zinc-200 bg-white p-5 sm:p-7 dark:border-zinc-800 dark:bg-zinc-900/70">
        <p role="status" className="text-sm text-zinc-500">Loading application materials…</p>
        <div className="mt-5 h-40 animate-pulse rounded-xl bg-zinc-100 dark:bg-zinc-800" />
      </section>
    );
  }

  if (!projection) {
    return (
      <StatusMessage kind="error">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <span>{loadError ?? "Application materials are unavailable."}</span>
          <button type="button" onClick={() => void refresh(true)} className={secondaryButtonClasses}>
            Try again
          </button>
        </div>
      </StatusMessage>
    );
  }

  const revision = projection.current_revision;
  const postingClosed = projection.blockers.includes("posting_closed");
  const stageLocked = applicationStage !== "pursuing";
  const controlsLocked = Boolean(busy || unresolvedIntent || stageLocked);
  const canGenerate = Boolean(
    projection.pack &&
    sourceCatalog?.reviewed_grounding_revision_id &&
    selectedEvidence.length > 0 &&
    questionPayloadValid &&
    !stageLocked &&
    !postingClosed,
  );
  const hasNeedsInput = Boolean(
    revision?.answers.some((answer) => answer.status === "needs_owner_input"),
  );
  const approvalBlocked = stageLocked || postingClosed ||
    hasNeedsInput ||
    projection.blockers.some((blocker) => [
      "grounding_evidence_changed",
      "grounded_evidence_missing",
      "questions_need_owner_input",
      "tailored_resume_unchanged",
      "current_revision_rejected",
    ].includes(blocker));
  const currentRejected = projection.current_event?.event_type === "rejected" &&
    projection.current_event.artifact_revision_id === revision?.id;

  return (
    <section
      aria-labelledby="application-materials-title"
      aria-busy={Boolean(busy)}
      data-application-version={applicationVersion}
      className="min-w-0 rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm sm:p-7 dark:border-zinc-800 dark:bg-zinc-900/70"
    >
      <div className="flex min-w-0 flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-indigo-600 dark:text-indigo-400">
              Application materials
            </p>
            <MaterialsStatusBadge status={projection.status} />
          </div>
          <h2 id="application-materials-title" className="mt-2 text-xl font-semibold tracking-tight">
            Truthful résumé and answer drafts
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
            Created locally from the exact fit review and achievements you approved. Gaps stay gaps. Nothing is filled, sent, or submitted.
          </p>
        </div>
        <button
          type="button"
          disabled={Boolean(busy)}
          onClick={() => void refresh(false)}
          className={secondaryButtonClasses}
        >
          Refresh saved state
        </button>
      </div>

      <div className="mt-6 space-y-6">
        {loadError ? (
          <StatusMessage kind="error">
            {loadError} Your last loaded materials and unsaved question choices remain below.
          </StatusMessage>
        ) : null}
        {actionError ? <StatusMessage kind="error">{actionError}</StatusMessage> : null}
        {notice ? <StatusMessage kind="success">{notice}</StatusMessage> : null}
        {unresolvedIntent ? (
          <StatusMessage kind="info">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <span>
                The {unresolvedIntent} result is unconfirmed. Inputs are locked so the exact payload and safe receipt cannot change.
              </span>
              <button
                type="button"
                disabled={Boolean(busy)}
                onClick={() => void refresh(false)}
                className={secondaryButtonClasses}
              >
                Check saved state
              </button>
            </div>
          </StatusMessage>
        ) : null}
        {stageLocked ? (
          <StatusMessage kind="info">
            These materials are read-only because the application has left preparation. The approved résumé and answers remain frozen for an exact submission record.
          </StatusMessage>
        ) : null}

        <BlockerList blockers={projection.blockers} />

        {!sourceCatalog?.reviewed_grounding_revision_id ? (
          <div className="rounded-xl border border-dashed border-zinc-300 p-5 dark:border-zinc-700">
            <h3 className="font-semibold">Review fit and evidence first</h3>
            <p className="mt-2 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
              Phase 5B starts only from one exact reviewed grounding revision. Complete that review above, then refresh this section.
            </p>
            <a href="#application-pack-title" className={`${secondaryButtonClasses} mt-4`}>
              Go to fit review
            </a>
          </div>
        ) : (
          <>
            <details
              open={!revision}
              className="min-w-0 rounded-xl border border-zinc-200 p-4 sm:p-5 dark:border-zinc-800"
            >
              <summary className="cursor-pointer font-semibold">
                {revision ? "Create another immutable version" : "Choose sources and add exact questions"}
              </summary>
              <div className="mt-5 space-y-6">
                <div className="grid gap-3 sm:grid-cols-3">
                  <SummaryFact
                    label="Reviewed fit"
                    value={`Revision ${sourceCatalog.reviewed_grounding_revision_number ?? "—"}`}
                  />
                  <SummaryFact
                    label="Available achievements"
                    value={String(sourceCatalog.evidence.length)}
                  />
                  <SummaryFact
                    label="Generation"
                    value="Local · deterministic"
                  />
                </div>

                <details className="min-w-0 rounded-lg bg-zinc-50 p-4 dark:bg-zinc-950/60">
                  <summary className="cursor-pointer text-sm font-semibold">
                    Change selected evidence · {selectedEvidence.length} selected
                  </summary>
                  <p className="mt-2 text-xs leading-5 text-zinc-500">
                    Up to five approved achievements are selected automatically. Open this only when you want to use different evidence.
                  </p>
                  <fieldset disabled={controlsLocked} className="mt-3 min-w-0">
                    <legend className="sr-only">Achievements to use in the résumé and note</legend>
                    <div className="space-y-2">
                      {sourceCatalog.evidence.map((evidence) => (
                        <label
                          key={`${evidence.id}:${evidence.version}`}
                          className="flex min-w-0 items-start gap-3 rounded-lg bg-white p-3 text-sm dark:bg-zinc-900/70"
                        >
                          <input
                            type="checkbox"
                            className="mt-1"
                            checked={selectedEvidenceIds.includes(evidence.id)}
                            disabled={
                              controlsLocked ||
                              (!selectedEvidenceIds.includes(evidence.id) && selectedEvidenceIds.length >= 5)
                            }
                            onChange={() => toggleSelectedEvidence(evidence.id)}
                          />
                          <span className="min-w-0">
                            <span className="block break-words leading-6">{evidence.statement}</span>
                            <span className="mt-1 block text-xs text-zinc-500">
                              Approved {formatDate(evidence.approved_at)} · version {evidence.version}
                            </span>
                          </span>
                        </label>
                      ))}
                    </div>
                  </fieldset>
                </details>

                <div>
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                    <div>
                      <h3 className="text-sm font-semibold">Exact application questions</h3>
                      <p className="mt-1 text-xs leading-5 text-zinc-500">
                        Optional. Paste the question exactly, add its limit, and choose achievements that genuinely help answer it.
                      </p>
                    </div>
                    <button
                      type="button"
                      disabled={controlsLocked || questions.length >= MAX_QUESTIONS}
                      onClick={addQuestion}
                      className={secondaryButtonClasses}
                    >
                      Add question
                    </button>
                  </div>
                  {questions.length === 0 ? (
                    <p className="mt-3 rounded-lg bg-zinc-50 p-4 text-sm text-zinc-500 dark:bg-zinc-950/60">
                      No application questions added. You can still create the tailored résumé and company note.
                    </p>
                  ) : (
                    <ol className="mt-4 space-y-4">
                      {questions.map((question, index) => (
                        <QuestionEditor
                          key={question.id}
                          index={index}
                          question={question}
                          evidence={sourceCatalog.evidence}
                          disabled={controlsLocked}
                          onChange={(change) => updateQuestion(question.id, change)}
                          onEvidence={(evidenceId) => toggleQuestionEvidence(question, evidenceId)}
                          onRemove={() => removeQuestion(question.id)}
                        />
                      ))}
                    </ol>
                  )}
                </div>

                {!questionPayloadValid ? (
                  <StatusMessage kind="error">
                    Character limits must be whole numbers from 1 to 10,000.
                  </StatusMessage>
                ) : null}
                {selectedEvidence.length === 0 ? (
                  <StatusMessage kind="error">
                    Select at least one approved achievement before creating materials.
                  </StatusMessage>
                ) : null}
                {inputsDirty ? (
                  <StatusMessage kind="info">
                    These source or question choices are not saved until you create the next immutable version.
                  </StatusMessage>
                ) : null}

                <button
                  type="button"
                  disabled={
                    !canGenerate ||
                    Boolean(busy) ||
                    Boolean(unresolvedIntent && unresolvedIntent !== "generate")
                  }
                  onClick={() => void generateRevision()}
                  className={`${primaryButtonClasses} w-full sm:w-auto`}
                >
                  {busy === "generate"
                    ? "Creating grounded materials…"
                    : unresolvedIntent === "generate"
                      ? "Retry unchanged generation"
                      : revision
                        ? "Create new immutable version"
                        : "Create grounded materials"}
                </button>
              </div>
            </details>

            {revision ? (
              <ArtifactReview
                revision={revision}
                unsupportedRequirements={
                  sourceCatalog.reviewed_grounding_revision_id === revision.grounding_revision_id
                    ? sourceCatalog.unsupported_requirements
                    : []
                }
                approvedRevisionId={projection.approved_revision?.id ?? null}
                currentRejected={currentRejected}
                tailoredResumeLabel={projection.tailored_resume_version?.label ?? null}
                copied={copied}
                copyText={copyText}
              />
            ) : (
              <StatusMessage kind="info">
                No materials version exists yet. Choose the evidence and optional questions above to create one locally.
              </StatusMessage>
            )}

            {revision && projection.approved_revision?.id !== revision.id ? (
              <fieldset
                aria-disabled={controlsLocked || approvalBlocked || currentRejected}
                className={`rounded-xl border border-zinc-200 p-4 sm:p-5 dark:border-zinc-800 ${
                  approvalBlocked || currentRejected ? "opacity-65" : ""
                }`}
              >
                <legend className="px-1 font-semibold">Approve this exact package</legend>
                <p className="mt-1 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
                  Review the visible résumé diff, note, answers, sources, and unclaimed gaps above. Approving confirms that review in one step and creates an immutable tailored résumé. It does not fill or submit an application.
                </p>
                {hasNeedsInput ? (
                  <p className="mt-3 text-sm font-medium text-amber-700 dark:text-amber-300">
                    At least one answer still needs your input. Change the question evidence and create a new version before approval.
                  </p>
                ) : null}
                <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:flex-wrap">
                  <button
                    type="button"
                    disabled={
                      approvalBlocked ||
                      currentRejected ||
                      Boolean(busy) ||
                      Boolean(unresolvedIntent && unresolvedIntent !== "approve")
                    }
                    onClick={() => void recordEvent("approved")}
                    className={`${primaryButtonClasses} w-full sm:w-auto`}
                  >
                    {busy === "approve"
                      ? "Approving exact version…"
                      : unresolvedIntent === "approve"
                        ? "Retry unchanged approval"
                        : "Approve this package"}
                  </button>
                  <button
                    type="button"
                    disabled={
                      stageLocked ||
                      postingClosed ||
                      currentRejected ||
                      Boolean(busy) ||
                      Boolean(unresolvedIntent && unresolvedIntent !== "reject")
                    }
                    onClick={() => void recordEvent("rejected")}
                    className={`${secondaryButtonClasses} w-full sm:w-auto`}
                  >
                    {busy === "reject"
                      ? "Rejecting exact version…"
                      : unresolvedIntent === "reject"
                        ? "Retry unchanged rejection"
                        : "Reject this version"}
                  </button>
                </div>
              </fieldset>
            ) : null}

            {projection.status === "approved" && projection.approved_revision ? (
              <StatusMessage kind="success">
                Revision {projection.approved_revision.revision_number} is approved. The exact tailored résumé
                {projection.tailored_resume_version
                  ? ` is saved as “${projection.tailored_resume_version.label}” version ${projection.tailored_resume_version.version}.`
                  : " is being reconciled with the saved résumé record."}
              </StatusMessage>
            ) : null}
          </>
        )}
      </div>
    </section>
  );
}

function QuestionEditor({
  index,
  question,
  evidence,
  disabled,
  onChange,
  onEvidence,
  onRemove,
}: {
  index: number;
  question: QuestionDraft;
  evidence: NonNullable<ApplicationArtifactsResponse["source_catalog"]>["evidence"];
  disabled: boolean;
  onChange: (change: Partial<QuestionDraft>) => void;
  onEvidence: (evidenceId: string) => void;
  onRemove: () => void;
}) {
  return (
    <li className="min-w-0 rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h4 className="text-sm font-semibold">Question {index + 1}</h4>
        <button
          type="button"
          disabled={disabled}
          onClick={onRemove}
          className="min-h-10 text-left text-sm font-medium text-red-700 underline underline-offset-4 sm:text-right dark:text-red-300"
        >
          Remove
        </button>
      </div>
      <div className="mt-4 grid min-w-0 gap-4 sm:grid-cols-[minmax(0,1fr)_10rem]">
        <FormField label="Exact question" htmlFor={`artifact-question-${question.id}`}>
          <textarea
            id={`artifact-question-${question.id}`}
            rows={4}
            disabled={disabled}
            value={question.text}
            onChange={(event) => onChange({ text: event.target.value })}
            className={textareaClasses}
            placeholder="Paste the employer's exact question…"
          />
        </FormField>
        <FormField
          label="Character limit"
          htmlFor={`artifact-question-limit-${question.id}`}
          hint="Optional"
        >
          <input
            id={`artifact-question-limit-${question.id}`}
            type="number"
            min={1}
            max={10_000}
            disabled={disabled}
            value={question.characterLimit}
            onChange={(event) => onChange({ characterLimit: event.target.value })}
            className={inputClasses}
            placeholder="e.g. 500"
          />
        </FormField>
      </div>
      <details className="mt-4 rounded-lg bg-zinc-50 p-3 dark:bg-zinc-950/60">
        <summary className="cursor-pointer text-sm font-medium">
          Answer sources · {question.evidenceIds.length} selected
        </summary>
        <p className="mt-2 text-xs leading-5 text-zinc-500">
          Empty is allowed; safe matching may suggest evidence, otherwise the answer is marked as needing your input.
        </p>
        <div className="mt-3 space-y-2">
          {evidence.map((item) => (
            <label key={item.id} className="flex min-w-0 items-start gap-3 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                disabled={
                  disabled ||
                  (!question.evidenceIds.includes(item.id) && question.evidenceIds.length >= 3)
                }
                checked={question.evidenceIds.includes(item.id)}
                onChange={() => onEvidence(item.id)}
              />
              <span className="break-words leading-6">{item.statement}</span>
            </label>
          ))}
        </div>
      </details>
    </li>
  );
}

function ArtifactReview({
  revision,
  unsupportedRequirements,
  approvedRevisionId,
  currentRejected,
  tailoredResumeLabel,
  copied,
  copyText,
}: {
  revision: ApplicationArtifactRevisionResponse;
  unsupportedRequirements: NonNullable<ApplicationArtifactsResponse["source_catalog"]>["unsupported_requirements"];
  approvedRevisionId: string | null;
  currentRejected: boolean;
  tailoredResumeLabel: string | null;
  copied: string | null;
  copyText: (key: string, text: string) => Promise<void>;
}) {
  const questions = new Map(revision.questions.map((question) => [question.id, question]));
  const allAnswers = revision.answers
    .map((answer) => {
      const question = questions.get(answer.question_id);
      return `${question?.text ?? "Question"}\n\n${answer.text || "Needs owner input"}`;
    })
    .join("\n\n---\n\n");
  const hasIncompleteAnswers = revision.answers.some(
    (answer) => answer.status === "needs_owner_input",
  );
  const groundedFitStory = buildGroundedFitStory({
    companyNote: revision.company_note,
    selectedEvidence: revision.selected_evidence,
    unsupportedRequirements,
  });

  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <SummaryFact label="Current version" value={`Revision ${revision.revision_number}`} />
        <SummaryFact label="Generator" value={revision.generator_version} />
        <SummaryFact label="Evidence used" value={String(revision.selected_evidence.length)} />
        <SummaryFact
          label="Review state"
          value={approvedRevisionId === revision.id ? "Approved exact version" : currentRejected ? "Rejected" : "Draft"}
        />
      </div>

      {approvedRevisionId && approvedRevisionId !== revision.id ? (
        <StatusMessage kind="info">
          A prior version remains approved. This newer draft has not replaced it.
        </StatusMessage>
      ) : null}
      {tailoredResumeLabel && approvedRevisionId === revision.id ? (
        <StatusMessage kind="success">
          Exact tailored résumé saved as “{tailoredResumeLabel}”.
        </StatusMessage>
      ) : null}

      {groundedFitStory ? (
        <GroundedFitStoryReview
          story={groundedFitStory}
          exactSourceNote={revision.company_note.text}
          sourceClaims={revision.company_note.claims}
          copied={copied}
          copyText={copyText}
        />
      ) : (
        <LegacyCompanyNote
          revision={revision}
          unsupportedRequirements={unsupportedRequirements}
          copied={copied}
          copyText={copyText}
        />
      )}

      <section aria-labelledby={`resume-diff-${revision.id}`} className="rounded-xl border border-zinc-200 p-4 sm:p-5 dark:border-zinc-800">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h3 id={`resume-diff-${revision.id}`} className="font-semibold">Exact résumé diff</h3>
            <p className="mt-1 text-sm text-zinc-500">
              Unified base-versus-tailored changes. “+” is added and “−” is removed.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void copyText("resume", revision.tailored_resume.text)}
            className={secondaryButtonClasses}
          >
            {copied === "resume" ? "Copied exact résumé" : "Copy tailored résumé"}
          </button>
        </div>
        <DiffViewer revision={revision} />
        <ClaimsDetails title="Résumé claim sources" claims={revision.tailored_resume.claims} />
      </section>

      {revision.questions.length > 0 ? (
        <section aria-labelledby={`answers-${revision.id}`} className="rounded-xl border border-zinc-200 p-4 sm:p-5 dark:border-zinc-800">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h3 id={`answers-${revision.id}`} className="font-semibold">Exact questions and answers</h3>
              <p className="mt-1 text-sm text-zinc-500">Copy one answer at a time or copy the complete set.</p>
            </div>
            <button
              type="button"
              disabled={!allAnswers || hasIncompleteAnswers}
              onClick={() => void copyText("all-answers", allAnswers)}
              className={secondaryButtonClasses}
            >
              {copied === "all-answers" ? "Copied all answers" : "Copy all answers"}
            </button>
          </div>
          <ol className="mt-4 space-y-4">
            {revision.answers.map((answer, index) => {
              const question = questions.get(answer.question_id);
              const answerKey = `answer:${answer.id}`;
              return (
                <li key={answer.id} className="min-w-0 rounded-lg bg-zinc-50 p-4 dark:bg-zinc-950/60">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0">
                      <p className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Question {index + 1}</p>
                      <p className="mt-2 whitespace-pre-wrap break-words text-sm font-medium leading-6">
                        {question?.text ?? "Saved application question"}
                      </p>
                      {question?.character_limit ? (
                        <p className="mt-1 text-xs text-zinc-500">Limit: {question.character_limit} characters</p>
                      ) : null}
                    </div>
                    <button
                      type="button"
                      disabled={answer.status === "needs_owner_input"}
                      onClick={() => void copyText(answerKey, answer.text)}
                      className={secondaryButtonClasses}
                    >
                      {copied === answerKey ? "Copied answer" : "Copy answer"}
                    </button>
                  </div>
                  {answer.status === "needs_owner_input" ? (
                    <StatusMessage kind="error">
                      No safe grounded answer was created. Choose relevant evidence above and generate a new version.
                    </StatusMessage>
                  ) : (
                    <p className="mt-4 whitespace-pre-wrap break-words text-sm leading-6">{answer.text}</p>
                  )}
                  <p className="mt-3 text-xs text-zinc-500">
                    {answer.text.length}{question?.character_limit ? ` / ${question.character_limit}` : ""} characters
                  </p>
                  <ClaimsDetails title="Answer claim sources" claims={answer.claims} />
                </li>
              );
            })}
          </ol>
        </section>
      ) : null}

      <details className="rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
        <summary className="cursor-pointer text-sm font-medium">View exact tailored résumé text</summary>
        <pre className="mt-4 max-h-[36rem] overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-zinc-50 p-4 font-mono text-xs leading-6 dark:bg-zinc-950/60">
          {revision.tailored_resume.text}
        </pre>
      </details>
    </div>
  );
}

function GroundedFitStoryReview({
  story,
  exactSourceNote,
  sourceClaims,
  copied,
  copyText,
}: {
  story: GroundedFitStory;
  exactSourceNote: string;
  sourceClaims: ApplicationArtifactRevisionResponse["company_note"]["claims"];
  copied: string | null;
  copyText: (key: string, text: string) => Promise<void>;
}) {
  return (
    <section
      aria-labelledby="grounded-fit-story-title"
      className="rounded-xl border border-indigo-200 bg-indigo-50/50 p-4 sm:p-5 dark:border-indigo-900 dark:bg-indigo-950/20"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-indigo-700 dark:text-indigo-300">
            Automatically prepared
          </p>
          <h3 id="grounded-fit-story-title" className="mt-1 font-semibold">
            Why I fit · grounded draft
          </h3>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
            Copy-ready starting text for an application note or outreach message. It uses only the pinned role and approved achievements from this exact materials version.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void copyText("fit-story", story.message)}
          className={secondaryButtonClasses}
        >
          {copied === "fit-story" ? "Copied grounded draft" : "Copy why-I-fit draft"}
        </button>
      </div>

      <pre className="mt-4 whitespace-pre-wrap break-words rounded-lg bg-white p-4 font-sans text-sm leading-6 dark:bg-zinc-950/60">
        {story.message}
      </pre>

      {story.unclaimedGaps.length > 0 ? (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-amber-950 dark:border-amber-900 dark:bg-amber-950/25 dark:text-amber-100">
          <h4 className="text-sm font-semibold">
            Kept out of the draft · {story.unclaimedGaps.length} unclaimed {story.unclaimedGaps.length === 1 ? "gap" : "gaps"}
          </h4>
          <p className="mt-1 text-xs leading-5 text-amber-900 dark:text-amber-200">
            These reviewed requirements have no approved support, so the app does not imply that you meet them.
          </p>
          <ul className="mt-3 space-y-2 text-sm">
            {story.unclaimedGaps.map((gap) => (
              <li key={gap.id} className="break-words leading-6">
                <span className="font-medium capitalize">{gap.importance}:</span> {gap.text}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="mt-4 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100">
          The reviewed fit contains no requirements marked unsupported. Partial support is still described only through its approved evidence.
        </p>
      )}

      <details className="mt-4 rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900/60">
        <summary className="cursor-pointer text-sm font-medium">
          View exact saved source note and provenance
        </summary>
        <p className="mt-2 text-xs leading-5 text-zinc-500">
          The draft above is derived on screen from these immutable exact sources; it adds no unsourced experience claim.
        </p>
        <pre className="mt-3 whitespace-pre-wrap break-words rounded-lg bg-zinc-50 p-4 font-sans text-sm leading-6 dark:bg-zinc-950/60">
          {exactSourceNote}
        </pre>
        <ClaimsDetails title="Source-note claim references" claims={sourceClaims} />
      </details>
    </section>
  );
}

function LegacyCompanyNote({
  revision,
  unsupportedRequirements,
  copied,
  copyText,
}: {
  revision: ApplicationArtifactRevisionResponse;
  unsupportedRequirements: NonNullable<ApplicationArtifactsResponse["source_catalog"]>["unsupported_requirements"];
  copied: string | null;
  copyText: (key: string, text: string) => Promise<void>;
}) {
  return (
    <>
      {unsupportedRequirements.length > 0 ? (
        <details open className="rounded-xl border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/25">
          <summary className="cursor-pointer font-semibold text-amber-950 dark:text-amber-100">
            Deliberately not claimed · {unsupportedRequirements.length}
          </summary>
          <p className="mt-2 text-sm leading-6 text-amber-900 dark:text-amber-200">
            These reviewed gaps were not turned into résumé or answer claims.
          </p>
          <ul className="mt-3 space-y-2 text-sm text-amber-950 dark:text-amber-100">
            {unsupportedRequirements.map((requirement) => (
              <li key={requirement.id} className="break-words">
                <span className="font-medium capitalize">{requirement.importance}:</span> {requirement.text}
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <section aria-labelledby={`company-note-${revision.id}`} className="rounded-xl border border-zinc-200 p-4 sm:p-5 dark:border-zinc-800">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h3 id={`company-note-${revision.id}`} className="font-semibold">Company-specific note</h3>
            <p className="mt-1 text-sm text-zinc-500">Grounded only in reviewed evidence and the pinned role.</p>
          </div>
          <button
            type="button"
            onClick={() => void copyText("note", revision.company_note.text)}
            className={secondaryButtonClasses}
          >
            {copied === "note" ? "Copied note" : "Copy note"}
          </button>
        </div>
        <pre className="mt-4 whitespace-pre-wrap break-words rounded-lg bg-zinc-50 p-4 font-sans text-sm leading-6 dark:bg-zinc-950/60">
          {revision.company_note.text}
        </pre>
        <ClaimsDetails title="Note claim sources" claims={revision.company_note.claims} />
      </section>
    </>
  );
}

function DiffViewer({ revision }: { revision: ApplicationArtifactRevisionResponse }) {
  return (
    <div
      aria-label="Unified résumé diff"
      className="mt-4 max-h-[36rem] min-w-0 overflow-y-auto rounded-lg border border-zinc-200 bg-zinc-950 font-mono text-xs text-zinc-100 dark:border-zinc-700"
    >
      {revision.diff.lines.map((line, index) => {
        const classes = line.operation === "insert"
          ? "bg-emerald-950/70 text-emerald-100"
          : line.operation === "delete"
            ? "bg-red-950/70 text-red-100"
            : "text-zinc-300";
        const prefix = line.operation === "insert" ? "+" : line.operation === "delete" ? "−" : " ";
        return (
          <div
            key={`${line.operation}:${line.base_line_number}:${line.tailored_line_number}:${index}`}
            className={`grid min-w-0 grid-cols-[2.75rem_2.75rem_1rem_minmax(0,1fr)] border-b border-white/5 px-2 py-1 ${classes}`}
          >
            <span className="select-none text-right text-zinc-500">{line.base_line_number ?? ""}</span>
            <span className="select-none text-right text-zinc-500">{line.tailored_line_number ?? ""}</span>
            <span className="select-none text-right">{prefix}</span>
            <span className="min-w-0 whitespace-pre-wrap break-words pl-2">{line.text || " "}</span>
          </div>
        );
      })}
    </div>
  );
}

function ClaimsDetails({
  title,
  claims,
}: {
  title: string;
  claims: ApplicationArtifactRevisionResponse["tailored_resume"]["claims"];
}) {
  return (
    <details className="mt-4 rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
      <summary className="cursor-pointer text-sm font-medium">{title} · {claims.length}</summary>
      {claims.length === 0 ? (
        <p className="mt-2 text-sm text-zinc-500">No new factual claims were introduced in this artifact.</p>
      ) : (
        <ol className="mt-3 space-y-3">
          {claims.map((claim) => (
            <li key={claim.id} className="min-w-0 rounded-lg bg-zinc-50 p-3 text-sm dark:bg-zinc-950/60">
              <p className="break-words font-medium leading-6">{claim.text}</p>
              <ul className="mt-2 space-y-1 text-xs leading-5 text-zinc-500">
                {claim.sources.map((source, index) => {
                  const summary = claimSourceSummary(source);
                  return (
                    <li key={`${claim.id}:${source.kind}:${index}`} className="break-words">
                      <span className="font-medium text-zinc-700 dark:text-zinc-300">{summary.label}</span>
                      {summary.detail ? ` — ${summary.detail}` : ""}
                    </li>
                  );
                })}
              </ul>
            </li>
          ))}
        </ol>
      )}
    </details>
  );
}

function BlockerList({ blockers }: { blockers: ApplicationArtifactBlocker[] }) {
  if (blockers.length === 0) return null;
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/25 dark:text-amber-100">
      <h3 className="font-semibold">Review before continuing</h3>
      <ul className="mt-2 space-y-2 leading-6">
        {blockers.map((blocker) => (
          <li key={blocker}>• {BLOCKER_COPY[blocker] ?? humanize(blocker)}</li>
        ))}
      </ul>
    </div>
  );
}

function MaterialsStatusBadge({ status }: { status: ApplicationArtifactsResponse["status"] }) {
  const label = {
    not_started: "Not started",
    draft: "Draft",
    approved: "Approved",
  }[status];
  const tone = status === "approved"
    ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
    : "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200";
  return <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${tone}`}>{label}</span>;
}

function SummaryFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-lg bg-zinc-50 p-3 dark:bg-zinc-950/60">
      <p className="text-xs font-medium uppercase tracking-wide text-zinc-500">{label}</p>
      <p className="mt-1 break-words text-sm font-medium">{value}</p>
    </div>
  );
}

function emptyQuestion(): QuestionDraft {
  return {
    id: crypto.randomUUID().replaceAll("-", ""),
    text: "",
    characterLimit: "",
    evidenceIds: [],
  };
}

function revisionMatchesPayload(
  revision: ApplicationArtifactRevisionResponse | null,
  payload: ApplicationArtifactRevisionCreate,
): boolean {
  if (
    !revision ||
    revision.grounding_revision_id !== payload.grounding_revision_id ||
    revision.parent_artifact_revision_id !== payload.parent_artifact_revision_id ||
    revision.questions.length !== payload.questions.length
  ) return false;
  const selected = revision.selected_evidence.map((item) => `${item.id}:${item.version}`).sort();
  const expectedSelected = (payload.selected_evidence_refs ?? [])
    .map((item) => `${item.id}:${item.version}`)
    .sort();
  if (JSON.stringify(selected) !== JSON.stringify(expectedSelected)) return false;
  return revision.questions.every((question, index) => {
    const expected = payload.questions[index];
    if (
      !expected ||
      question.id !== expected.id ||
      question.text !== expected.text ||
      question.character_limit !== expected.character_limit
    ) return false;
    const refs = question.evidence_refs.map((item) => `${item.id}:${item.version}`).sort();
    const expectedRefs = expected.evidence_refs.map((item) => `${item.id}:${item.version}`).sort();
    return JSON.stringify(refs) === JSON.stringify(expectedRefs);
  });
}

function humanize(value: string): string {
  return value.replaceAll("_", " ").replace(/^./, (character) => character.toUpperCase());
}

function claimSourceSummary(
  source: ApplicationArtifactRevisionResponse["tailored_resume"]["claims"][number]["sources"][number],
): { label: string; detail: string } {
  if (source.kind === "evidence_snapshot") {
    return {
      label: `Approved evidence · version ${source.evidence_version}`,
      detail: source.quote,
    };
  }
  if (source.kind === "job_description_span") {
    return {
      label: "Pinned job description",
      detail: source.quote,
    };
  }
  return {
    label: source.field === "company_name" ? "Pinned company" : "Pinned role title",
    detail: source.value,
  };
}
