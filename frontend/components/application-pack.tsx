"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  createApplicationPack,
  createApplicationPackRevision,
  getApplicationPack,
  recordApplicationPackEvent,
} from "@/lib/application-api";
import type {
  ApplicationPackBlocker,
  ApplicationPackCreate,
  ApplicationPackRequirementCoverage,
  ApplicationPackRequirementImportance,
  ApplicationPackRequirementResponse,
  ApplicationPackRequirementReview,
  ApplicationPackResponse,
  ApplicationPackRevisionCreate,
} from "@/lib/application-pack-types";
import {
  createIdempotencyKey,
  listResumeVersions,
  WorkspaceApiError,
} from "@/lib/workspace-api";
import type {
  AchievementEvidence,
  ResumeVersionSummary,
} from "@/lib/workspace-types";
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

interface RequirementDraft {
  included: boolean;
  importance: ApplicationPackRequirementImportance;
  coverage: ApplicationPackRequirementCoverage;
  evidenceIds: string[];
}

interface PendingMutation {
  intent: "start" | "save" | "review";
  key: string;
  fingerprint: string;
  expectedVersion: number;
  applied: (response: ApplicationPackResponse) => boolean;
  successMessage: string;
  onConfirmed?: (response: ApplicationPackResponse) => void;
}

interface MutationOptions {
  intent: PendingMutation["intent"];
  fingerprint: string;
  expectedVersion: number;
  execute: (pending: PendingMutation) => Promise<ApplicationPackResponse>;
  applied: (response: ApplicationPackResponse) => boolean;
  successMessage: string;
  ambiguousMessage: string;
  onConfirmed?: (response: ApplicationPackResponse) => void;
}

const COVERAGE_OPTIONS: Array<{
  value: ApplicationPackRequirementCoverage;
  label: string;
  description: string;
}> = [
  {
    value: "supported",
    label: "Supported",
    description: "Approved evidence fully supports this requirement.",
  },
  {
    value: "partial",
    label: "Partial",
    description: "Approved evidence supports only part of this requirement.",
  },
  {
    value: "unsupported",
    label: "Unsupported",
    description: "This is an honest gap; do not claim it in application materials.",
  },
  {
    value: "needs_review",
    label: "Needs review",
    description: "Keep this open until you have made an explicit decision.",
  },
];

const BLOCKER_COPY: Record<ApplicationPackBlocker, string> = {
  base_resume_missing:
    "No base resume is configured. Choose any saved immutable resume below, or add one in Profile.",
  approved_evidence_missing:
    "No current approved achievements are available. You can still extract and review requirements, but Supported or Partial coverage needs approved evidence.",
  owner_job_description_required:
    "The captured posting has no usable job description. Paste the exact description below to continue.",
  no_requirements_extracted:
    "No exact requirement statements could be extracted from this job description.",
  requirements_need_review:
    "Every requirement needs an explicit Supported, Partial, or Unsupported decision.",
  mapped_evidence_changed:
    "Evidence used by this revision changed or is no longer approved. Review the mappings again.",
  posting_closed:
    "This posting is closed. Saved pack history remains readable, but no new review changes are allowed.",
};

export function ApplicationPack({
  applicationId,
  applicationVersion,
}: {
  applicationId: string;
  applicationVersion: number;
}) {
  const [projection, setProjection] = useState<ApplicationPackResponse | null>(null);
  const [resumes, setResumes] = useState<ResumeVersionSummary[]>([]);
  const [selectedResumeId, setSelectedResumeId] = useState("");
  const [ownerJobDescription, setOwnerJobDescription] = useState("");
  const [drafts, setDrafts] = useState<Record<string, RequirementDraft>>({});
  const [dirty, setDirty] = useState(false);
  const [editingReviewed, setEditingReviewed] = useState(false);
  const [reviewConfirmed, setReviewConfirmed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<PendingMutation["intent"] | null>(null);
  const [unresolvedIntent, setUnresolvedIntent] = useState<PendingMutation["intent"] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [copiedRequirementId, setCopiedRequirementId] = useState<string | null>(null);

  const projectionRef = useRef<ApplicationPackResponse | null>(null);
  const draftsRef = useRef<Record<string, RequirementDraft>>({});
  const dirtyRef = useRef(false);
  const pendingMutation = useRef<PendingMutation | null>(null);
  const unresolvedIntentRef = useRef<PendingMutation["intent"] | null>(null);
  const requestGeneration = useRef(0);
  const descriptionDirtyRef = useRef(false);

  const setDirtyState = useCallback((value: boolean) => {
    dirtyRef.current = value;
    setDirty(value);
  }, []);

  const setUnresolvedState = useCallback((value: PendingMutation["intent"] | null) => {
    unresolvedIntentRef.current = value;
    setUnresolvedIntent(value);
  }, []);

  const hydrateDrafts = useCallback((
    next: ApplicationPackResponse,
    force = false,
  ) => {
    if (!next.current_revision || (!force && dirtyRef.current)) return;
    const hydrated = Object.fromEntries(
      next.current_revision.requirements.map((requirement) => [
        requirement.id,
        {
          included: true,
          importance: requirement.importance,
          coverage: requirement.coverage,
          evidenceIds: requirement.evidence.map((item) => item.id),
        } satisfies RequirementDraft,
      ]),
    );
    draftsRef.current = hydrated;
    setDrafts(hydrated);
    setDirtyState(false);
    setReviewConfirmed(false);
  }, [setDirtyState]);

  const acceptResponse = useCallback((
    next: ApplicationPackResponse,
    expectedApplicationId: string,
    generation: number,
    forceDrafts = false,
  ) => {
    if (
      requestGeneration.current !== generation ||
      next.application_id !== expectedApplicationId
    ) return false;

    const previous = projectionRef.current;
    if (previous?.pack) {
      if (!next.pack || next.pack.id !== previous.pack.id) return false;
      if (next.pack.version < previous.pack.version) return false;
      if (
        previous.current_revision &&
        next.current_revision &&
        next.current_revision.revision_number < previous.current_revision.revision_number
      ) return false;
    }

    projectionRef.current = next;
    setProjection(next);
    setLoadError(null);
    hydrateDrafts(next, forceDrafts);
    const pending = pendingMutation.current;
    if (
      pending &&
      unresolvedIntentRef.current === pending.intent &&
      pending.applied(next)
    ) {
      pendingMutation.current = null;
      setUnresolvedState(null);
      pending.onConfirmed?.(next);
      setActionError(null);
      setNotice(`${pending.successMessage} Confirmed from saved state.`);
    }
    return true;
  }, [hydrateDrafts, setUnresolvedState]);

  const refresh = useCallback(async (showLoading = false) => {
    const requestedApplicationId = applicationId;
    const generation = requestGeneration.current;
    if (showLoading) setLoading(true);
    try {
      const [packResult, resumeResult] = await Promise.allSettled([
        getApplicationPack(requestedApplicationId),
        listResumeVersions(),
      ]);
      if (packResult.status === "rejected") throw packResult.reason;
      const next = packResult.value;
      if (!acceptResponse(next, requestedApplicationId, generation)) return null;
      if (resumeResult.status === "fulfilled") {
        const nextResumes = resumeResult.value;
        setResumes(nextResumes);
        setSelectedResumeId((current) => {
          if (current && nextResumes.some((resume) => resume.id === current)) return current;
          return nextResumes.find((resume) => resume.is_base)?.id ?? nextResumes[0]?.id ?? "";
        });
      } else {
        setLoadError(errorText(
          resumeResult.reason,
          "The pack loaded, but saved resume labels are temporarily unavailable.",
        ));
      }
      if (!descriptionDirtyRef.current) setOwnerJobDescription("");
      return next;
    } catch (reason) {
      if (requestGeneration.current !== generation) return null;
      setLoadError(errorText(reason, "Unable to load the grounded application pack."));
      return null;
    } finally {
      if (showLoading && requestGeneration.current === generation) setLoading(false);
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
    const existing = pendingMutation.current;
    if (existing) {
      if (
        existing.intent !== options.intent ||
        existing.fingerprint !== options.fingerprint ||
        existing.expectedVersion !== options.expectedVersion
      ) {
        throw new Error(
          "A different action still has an unconfirmed result. Retry that unchanged action before editing or starting another one.",
        );
      }
      return existing;
    }
    const created: PendingMutation = {
      intent: options.intent,
      fingerprint: options.fingerprint,
      expectedVersion: options.expectedVersion,
      key: createIdempotencyKey(`application-pack:${applicationId}:${options.intent}`),
      applied: options.applied,
      successMessage: options.successMessage,
      onConfirmed: options.onConfirmed,
    };
    pendingMutation.current = created;
    return created;
  }

  async function runMutation(options: MutationOptions): Promise<boolean> {
    if (busy) return false;
    let pending: PendingMutation;
    try {
      pending = pendingFor(options);
    } catch (reason) {
      setActionError(errorText(reason, "Review the latest saved application pack."));
      return false;
    }

    const requestedApplicationId = applicationId;
    const generation = requestGeneration.current;
    setBusy(options.intent);
    setActionError(null);
    setNotice(null);
    try {
      const next = await options.execute(pending);
      if (requestGeneration.current !== generation) return false;
      const accepted = acceptResponse(next, requestedApplicationId, generation);
      const confirmed =
        next.application_id === requestedApplicationId && options.applied(next);
      const current = projectionRef.current;
      const confirmedByCurrent = Boolean(
        current?.application_id === requestedApplicationId && options.applied(current),
      );
      if ((accepted && confirmed) || confirmedByCurrent) {
        const saved = confirmedByCurrent ? current! : next;
        pendingMutation.current = null;
        setUnresolvedState(null);
        options.onConfirmed?.(saved);
        setNotice(options.successMessage);
        return true;
      }
      if (!accepted) {
        setUnresolvedState(options.intent);
        setActionError(
          "A newer saved view arrived first, so this result is still unconfirmed. Retry the same unchanged action safely.",
        );
        return false;
      }
      pendingMutation.current = null;
      setUnresolvedState(null);
      setActionError(
        "The server accepted the request but the returned saved record does not contain the requested change. Review the latest state before trying again.",
      );
      return false;
    } catch (reason) {
      if (requestGeneration.current !== generation) return false;
      const apiError = reason instanceof WorkspaceApiError ? reason : null;
      const ambiguous = !apiError || apiError.retryable || apiError.code === "mutation_pending";
      const conflict = apiError && !ambiguous && [409, 412, 428].includes(apiError.status);
      if (conflict) {
        pendingMutation.current = null;
        setUnresolvedState(null);
        await refresh(false);
        setActionError(
          "This application pack changed in another tab. The latest saved state is shown and your unsaved review remains here. Review it before saving again.",
        );
        return false;
      }

      if (ambiguous) {
        try {
          const checked = await getApplicationPack(requestedApplicationId);
          const accepted = acceptResponse(checked, requestedApplicationId, generation);
          const current = projectionRef.current;
          const confirmed = accepted && options.applied(checked);
          const confirmedByCurrent = Boolean(
            current?.application_id === requestedApplicationId && options.applied(current),
          );
          if (confirmed || confirmedByCurrent) {
            const saved = confirmedByCurrent ? current! : checked;
            pendingMutation.current = null;
            setUnresolvedState(null);
            options.onConfirmed?.(saved);
            setNotice(`${options.successMessage} Confirmed from the saved record.`);
            return true;
          }
          if (
            options.intent !== "start" &&
            checked.pack &&
            checked.pack.version > pending.expectedVersion
          ) {
            pendingMutation.current = null;
            setUnresolvedState(null);
            setActionError(
              "The pack changed while this action was being checked, and the requested result is not present. Review the latest state before trying again.",
            );
            return false;
          }
        } catch {
          // Keep the exact payload and receipt so an unchanged retry remains safe.
        }
        setUnresolvedState(options.intent);
        setActionError(
          `${options.ambiguousMessage} Retry the same unchanged action; it cannot create a duplicate.`,
        );
        return false;
      }

      pendingMutation.current = null;
      setUnresolvedState(null);
      await refresh(false);
      setActionError(packErrorText(reason));
      return false;
    } finally {
      if (requestGeneration.current === generation) setBusy(null);
    }
  }

  async function startPack() {
    if (!projection || projection.status !== "not_started" || !selectedResumeId) return;
    const needsOwnerDescription = projection.blockers.includes(
      "owner_job_description_required",
    );
    const payload: ApplicationPackCreate = {
      base_resume_version_id: selectedResumeId,
      owner_job_description: needsOwnerDescription ? ownerJobDescription : null,
    };
    await runMutation({
      intent: "start",
      fingerprint: JSON.stringify(payload),
      expectedVersion: applicationVersion,
      execute: (pending) => createApplicationPack(
        applicationId,
        pending.expectedVersion,
        pending.key,
        payload,
      ),
      applied: (next) => Boolean(
        next.pack?.base_resume_version_id === payload.base_resume_version_id &&
        next.current_revision &&
        (
          payload.owner_job_description === null ||
          next.current_revision.job_description === payload.owner_job_description
        )
      ),
      successMessage: "Grounding review started from the exact saved inputs.",
      ambiguousMessage: "The start result could not be confirmed.",
      onConfirmed: (next) => {
        descriptionDirtyRef.current = false;
        hydrateDrafts(next, true);
      },
    });
  }

  function changeCoverage(
    requirementId: string,
    coverage: ApplicationPackRequirementCoverage,
  ) {
    const current = draftsRef.current[requirementId];
    if (!current) return;
    const next = {
      ...draftsRef.current,
      [requirementId]: {
        ...current,
        coverage,
        evidenceIds: coverage === "unsupported" ? [] : current.evidenceIds,
      },
    };
    draftsRef.current = next;
    setDrafts(next);
    setDirtyState(true);
    setReviewConfirmed(false);
  }

  function toggleRequirementIncluded(requirementId: string) {
    const current = draftsRef.current[requirementId];
    if (!current) return;
    const next = {
      ...draftsRef.current,
      [requirementId]: { ...current, included: !current.included },
    };
    draftsRef.current = next;
    setDrafts(next);
    setDirtyState(true);
    setReviewConfirmed(false);
  }

  function changeImportance(
    requirementId: string,
    importance: ApplicationPackRequirementImportance,
  ) {
    const current = draftsRef.current[requirementId];
    if (!current) return;
    const next = {
      ...draftsRef.current,
      [requirementId]: { ...current, importance },
    };
    draftsRef.current = next;
    setDrafts(next);
    setDirtyState(true);
    setReviewConfirmed(false);
  }

  function toggleEvidence(requirementId: string, evidenceId: string) {
    const current = draftsRef.current[requirementId];
    if (!current || current.coverage === "unsupported") return;
    const selected = current.evidenceIds.includes(evidenceId);
    const evidenceIds = selected
      ? current.evidenceIds.filter((id) => id !== evidenceId)
      : [...current.evidenceIds, evidenceId];
    const coverage =
      evidenceIds.length === 0 && ["supported", "partial"].includes(current.coverage)
        ? "needs_review" as const
        : current.coverage;
    const next = {
      ...draftsRef.current,
      [requirementId]: { ...current, coverage, evidenceIds },
    };
    draftsRef.current = next;
    setDrafts(next);
    setDirtyState(true);
    setReviewConfirmed(false);
  }

  async function saveRevision() {
    const pack = projection?.pack;
    const payload = buildRevisionPayload(projection, draftsRef.current);
    if (!pack || !payload) {
      setActionError(
        "Supported and Partial requirements need at least one currently approved achievement.",
      );
      return;
    }
    await runMutation({
      intent: "save",
      fingerprint: JSON.stringify(payload),
      expectedVersion: pack.version,
      execute: (pending) => createApplicationPackRevision(
        applicationId,
        pack.id,
        pending.expectedVersion,
        pending.key,
        payload,
      ),
      applied: (next) => revisionMatchesPayload(next, payload),
      successMessage: "Saved as a new immutable review revision.",
      ambiguousMessage: "The new review revision could not be confirmed.",
      onConfirmed: (next) => {
        setEditingReviewed(false);
        hydrateDrafts(next, true);
      },
    });
  }

  async function markReviewed() {
    const pack = projection?.pack;
    const revision = projection?.current_revision;
    if (!pack || !revision || dirty || !reviewConfirmed) return;
    const payload = {
      event_type: "reviewed" as const,
      revision_id: revision.id,
      confirm_requirements_reviewed: true as const,
    };
    await runMutation({
      intent: "review",
      fingerprint: JSON.stringify(payload),
      expectedVersion: pack.version,
      execute: (pending) => recordApplicationPackEvent(
        applicationId,
        pack.id,
        pending.expectedVersion,
        pending.key,
        payload,
      ),
      applied: (next) => Boolean(
        next.status === "reviewed" &&
        next.reviewed_revision?.id === revision.id &&
        next.review_event?.revision_id === revision.id
      ),
      successMessage: `Revision ${revision.revision_number} is marked reviewed.`,
      ambiguousMessage: "The exact-revision review confirmation could not be verified.",
      onConfirmed: () => {
        setReviewConfirmed(false);
        setEditingReviewed(false);
      },
    });
  }

  async function copyRequirement(requirement: ApplicationPackRequirementResponse) {
    setActionError(null);
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard is unavailable.");
      await navigator.clipboard.writeText(requirement.text);
      setCopiedRequirementId(requirement.id);
      window.setTimeout(() => setCopiedRequirementId((id) => id === requirement.id ? null : id), 2_000);
    } catch {
      setActionError(
        "Clipboard access was blocked. Select the exact source excerpt in the card and copy it manually.",
      );
    }
  }

  if (loading && !projection) {
    return (
      <section className="rounded-2xl border border-zinc-200 bg-white p-5 sm:p-7 dark:border-zinc-800 dark:bg-zinc-900/70">
        <p role="status" className="text-sm text-zinc-500">Loading grounded application review…</p>
      </section>
    );
  }

  if (!projection) {
    return (
      <StatusMessage kind="error">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span>{loadError ?? "The grounded application review is unavailable."}</span>
          <button type="button" onClick={() => void refresh(true)} className={secondaryButtonClasses}>
            Try again
          </button>
        </div>
      </StatusMessage>
    );
  }

  const controlsLocked = Boolean(busy || unresolvedIntent);
  const startActionLocked = Boolean(
    busy || (unresolvedIntent && unresolvedIntent !== "start"),
  );
  const saveActionLocked = Boolean(
    busy || (unresolvedIntent && unresolvedIntent !== "save"),
  );
  const reviewActionLocked = Boolean(
    busy || (unresolvedIntent && unresolvedIntent !== "review"),
  );
  const ownerDescriptionRequired = projection.blockers.includes(
    "owner_job_description_required",
  );
  const postingClosed = projection.blockers.includes("posting_closed");
  const selectedResume = resumes.find((resume) => resume.id === selectedResumeId) ?? null;
  const revision = projection.current_revision;
  const readOnlyReviewed = projection.status === "reviewed" && !editingReviewed;
  const payloadReady = buildRevisionPayload(projection, drafts) !== null;
  const everyRequirementReviewed = Boolean(
    revision &&
    revision.requirements.length > 0 &&
    revision.requirements.every(
      (requirement) => {
        const draft = drafts[requirement.id];
        return Boolean(
          draft && (!draft.included || draft.coverage !== "needs_review"),
        );
      },
    ) &&
    payloadReady
  );
  const canConfirmReview = Boolean(
    projection.status === "draft" &&
    !dirty &&
    everyRequirementReviewed &&
    !projection.blockers.includes("requirements_need_review") &&
    !projection.blockers.includes("mapped_evidence_changed") &&
    !postingClosed
  );

  return (
    <section
      aria-labelledby="application-pack-title"
      aria-busy={Boolean(busy)}
      className="min-w-0 rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm sm:p-7 dark:border-zinc-800 dark:bg-zinc-900/70"
    >
      <div className="flex min-w-0 flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-indigo-600 dark:text-indigo-400">
              Grounded application pack
            </p>
            <PackStatusBadge status={projection.status} />
          </div>
          <h2 id="application-pack-title" className="mt-2 text-xl font-semibold tracking-tight">
            Fit and evidence review
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
            Provider-free and database-only. Review exact job-description excerpts against achievements you already approved. Gaps stay visible; nothing is invented or submitted.
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

      <div className="mt-6 space-y-5">
        {loadError ? (
          <StatusMessage kind="error">
            {loadError} Your last loaded review and unsaved choices remain below.
          </StatusMessage>
        ) : null}
        {actionError ? <StatusMessage kind="error">{actionError}</StatusMessage> : null}
        {notice ? <StatusMessage kind="success">{notice}</StatusMessage> : null}
        {unresolvedIntent ? (
          <StatusMessage kind="info">
            The {unresolvedIntent} result is still unconfirmed. Controls are locked so retrying preserves the exact payload and receipt.
          </StatusMessage>
        ) : null}

        {projection.status === "not_started" ? (
          <div className="space-y-5">
            <BlockerList blockers={projection.blockers} />

            <div className="grid min-w-0 gap-5 sm:grid-cols-2">
              <FormField
                label="Resume to ground this application"
                htmlFor={`application-pack-resume-${applicationId}`}
                hint="The selected immutable version is pinned to this pack. It does not become your base resume."
              >
                <select
                  id={`application-pack-resume-${applicationId}`}
                  value={selectedResumeId}
                  disabled={controlsLocked || resumes.length === 0}
                  onChange={(event) => setSelectedResumeId(event.target.value)}
                  className={inputClasses}
                >
                  {resumes.length === 0 ? <option value="">No saved resumes</option> : null}
                  {resumes.map((resume) => (
                    <option key={resume.id} value={resume.id}>
                      {resume.label}{resume.is_base ? " · Base" : ""} · version {resume.version}
                    </option>
                  ))}
                </select>
              </FormField>
              <div className="rounded-xl border border-zinc-200 p-4 text-sm dark:border-zinc-800">
                <p className="font-medium">Approved achievement evidence</p>
                <p className="mt-1 text-zinc-600 dark:text-zinc-400">
                  {projection.current_approved_evidence.length} current approved achievement{projection.current_approved_evidence.length === 1 ? "" : "s"}
                </p>
                <Link href="/profile" className={`${secondaryButtonClasses} mt-3`}>
                  Review profile evidence
                </Link>
              </div>
            </div>

            {ownerDescriptionRequired ? (
              <FormField
                label="Exact job description"
                htmlFor={`owner-job-description-${applicationId}`}
                hint="Accepted only because the pinned posting has no persisted description. Whitespace is preserved exactly."
              >
                <textarea
                  id={`owner-job-description-${applicationId}`}
                  rows={14}
                  value={ownerJobDescription}
                  disabled={controlsLocked}
                  onChange={(event) => {
                    setOwnerJobDescription(event.target.value);
                    descriptionDirtyRef.current = true;
                  }}
                  className={`${textareaClasses} font-mono`}
                  placeholder="Paste the complete job description from the posting…"
                />
              </FormField>
            ) : (
              <StatusMessage kind="info">
                The server will use the description preserved with the exact posting version you pursued.
              </StatusMessage>
            )}

            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                disabled={
                  startActionLocked ||
                  !selectedResume ||
                  postingClosed ||
                  (ownerDescriptionRequired && !ownerJobDescription.trim())
                }
                onClick={() => void startPack()}
                className={primaryButtonClasses}
              >
                {busy === "start" ? "Starting exact review…" : unresolvedIntent === "start" ? "Retry unchanged start" : "Start grounded review"}
              </button>
              {!selectedResume ? (
                <Link href="/profile" className={secondaryButtonClasses}>Add a saved resume</Link>
              ) : null}
            </div>
          </div>
        ) : revision && projection.pack ? (
          <div className="space-y-6">
            <div className="grid min-w-0 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <SummaryFact label="Current revision" value={`Revision ${revision.revision_number}`} />
              <SummaryFact label="Job-description source" value={descriptionSourceLabel(revision.job_description_source)} />
              <SummaryFact label="Extraction method" value={revision.extraction_version} />
              <SummaryFact
                label="Pinned resume"
                value={resumes.find((resume) => resume.id === projection.pack?.base_resume_version_id)?.label ?? "Saved immutable version"}
              />
            </div>

            <BlockerList blockers={projection.blockers} />

            {projection.reviewed_revision && projection.review_event ? (
              <StatusMessage kind={projection.status === "reviewed" ? "success" : "info"}>
                Revision {projection.reviewed_revision.revision_number} was explicitly reviewed {formatDate(projection.review_event.occurred_at)}. {projection.status === "draft" ? "A newer draft is now current; the prior reviewed revision remains recorded." : "That exact revision remains current."}
              </StatusMessage>
            ) : null}

            {revision.requirements.length === 0 ? (
              <StatusMessage kind="error">
                No exact requirements were extracted. Review the saved job-description source before continuing.
              </StatusMessage>
            ) : (
              <div className="space-y-4">
                <div className="flex flex-wrap items-end justify-between gap-3">
                  <div>
                    <h3 className="font-semibold">Requirements and honest coverage</h3>
                    <p className="mt-1 text-sm text-zinc-500">
                      Required and preferred statements are exact source spans. A genuine gap is a valid review outcome.
                    </p>
                  </div>
                  {projection.status === "reviewed" && !editingReviewed ? (
                    <button
                      type="button"
                      disabled={controlsLocked || postingClosed}
                      onClick={() => setEditingReviewed(true)}
                      className={secondaryButtonClasses}
                    >
                      Review requirements again
                    </button>
                  ) : null}
                </div>

                <ol className="space-y-4">
                  {revision.requirements.map((requirement) => (
                    <RequirementCard
                      key={requirement.id}
                      requirement={requirement}
                      draft={drafts[requirement.id]}
                      currentEvidence={projection.current_approved_evidence}
                      readOnly={readOnlyReviewed}
                      reviewDisabled={controlsLocked || postingClosed}
                      copyDisabled={Boolean(busy)}
                      copied={copiedRequirementId === requirement.id}
                      onCopy={() => void copyRequirement(requirement)}
                      onIncluded={() => toggleRequirementIncluded(requirement.id)}
                      onImportance={(importance) => changeImportance(requirement.id, importance)}
                      onCoverage={(coverage) => changeCoverage(requirement.id, coverage)}
                      onEvidence={(evidenceId) => toggleEvidence(requirement.id, evidenceId)}
                    />
                  ))}
                </ol>
              </div>
            )}

            <details className="min-w-0 rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
              <summary className="cursor-pointer text-sm font-medium">Review the exact job-description source</summary>
              <p className="mt-2 text-xs text-zinc-500">
                {descriptionSourceLabel(revision.job_description_source)} · exact whitespace preserved
              </p>
              <div className="mt-4 max-h-96 overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-zinc-50 p-4 font-mono text-xs leading-6 dark:bg-zinc-950/60">
                {revision.job_description}
              </div>
            </details>

            {!readOnlyReviewed ? (
              <div className="space-y-4 border-t border-zinc-200 pt-5 dark:border-zinc-800">
                {dirty ? (
                  <StatusMessage kind="info">
                    You have unsaved coverage or evidence changes. Refreshes will not overwrite them.
                  </StatusMessage>
                ) : null}
                {!payloadReady ? (
                  <StatusMessage kind="error">
                    Supported and Partial requirements need at least one currently approved achievement before this revision can be saved.
                  </StatusMessage>
                ) : null}
                <div className="flex flex-wrap gap-3">
                  <button
                    type="button"
                      disabled={saveActionLocked || postingClosed || !dirty || !payloadReady}
                    onClick={() => void saveRevision()}
                    className={primaryButtonClasses}
                  >
                    {busy === "save" ? "Saving immutable revision…" : unresolvedIntent === "save" ? "Retry unchanged save" : "Save as new immutable review version"}
                  </button>
                  {editingReviewed && !dirty ? (
                    <button
                      type="button"
                      disabled={controlsLocked || postingClosed}
                      onClick={() => setEditingReviewed(false)}
                      className={secondaryButtonClasses}
                    >
                      Cancel review changes
                    </button>
                  ) : null}
                </div>

                <fieldset
                  aria-disabled={!canConfirmReview || controlsLocked || postingClosed}
                  className={`rounded-xl border border-zinc-200 p-4 dark:border-zinc-800 ${!canConfirmReview || controlsLocked || postingClosed ? "opacity-60" : ""}`}
                >
                  <legend className="px-1 text-sm font-semibold">Confirm the exact saved revision</legend>
                  <label className="flex items-start gap-3 text-sm leading-6">
                    <input
                      type="checkbox"
                      className="mt-1"
                      disabled={!canConfirmReview || controlsLocked || postingClosed}
                      checked={reviewConfirmed}
                      onChange={(event) => setReviewConfirmed(event.target.checked)}
                    />
                    <span>
                      I reviewed every requirement in revision {revision.revision_number}, including all Partial and Unsupported gaps, against currently approved evidence.
                    </span>
                  </label>
                  {!canConfirmReview ? (
                    <p className="mt-2 text-xs text-zinc-500">
                      Save local changes and resolve every Needs review or changed-evidence blocker first.
                    </p>
                  ) : null}
                  <button
                    type="button"
                    disabled={!canConfirmReview || !reviewConfirmed || reviewActionLocked || postingClosed}
                    onClick={() => void markReviewed()}
                    className={`${primaryButtonClasses} mt-4`}
                  >
                    {busy === "review" ? "Confirming exact revision…" : unresolvedIntent === "review" ? "Retry unchanged confirmation" : `Mark revision ${revision.revision_number} reviewed`}
                  </button>
                </fieldset>
              </div>
            ) : (
              <StatusMessage kind="success">
                The requirement review is complete and immutable. Phase 5B will build truthful resume and answer drafts from this reviewed evidence; no application materials have been generated yet.
              </StatusMessage>
            )}
          </div>
        ) : (
          <StatusMessage kind="error">The saved pack projection is incomplete. Refresh before continuing.</StatusMessage>
        )}
      </div>
    </section>
  );
}

function RequirementCard({
  requirement,
  draft,
  currentEvidence,
  readOnly,
  reviewDisabled,
  copyDisabled,
  copied,
  onCopy,
  onIncluded,
  onImportance,
  onCoverage,
  onEvidence,
}: {
  requirement: ApplicationPackRequirementResponse;
  draft: RequirementDraft | undefined;
  currentEvidence: AchievementEvidence[];
  readOnly: boolean;
  reviewDisabled: boolean;
  copyDisabled: boolean;
  copied: boolean;
  onCopy: () => void;
  onIncluded: () => void;
  onImportance: (importance: ApplicationPackRequirementImportance) => void;
  onCoverage: (coverage: ApplicationPackRequirementCoverage) => void;
  onEvidence: (evidenceId: string) => void;
}) {
  const [showAllEvidence, setShowAllEvidence] = useState(false);
  const titleId = `application-pack-requirement-${requirement.id}`;
  const selectedIds = new Set(draft?.evidenceIds ?? requirement.evidence.map((item) => item.id));
  const currentEvidenceIds = new Set(currentEvidence.map((item) => item.id));
  const unavailableEvidence = requirement.evidence.filter(
    (item) => selectedIds.has(item.id) && !currentEvidenceIds.has(item.id),
  );
  const currentCoverage = draft?.coverage ?? requirement.coverage;
  const currentImportance = draft?.importance ?? requirement.importance;
  const included = draft?.included ?? true;
  const previewEvidenceIds = new Set([
    ...selectedIds,
    ...currentEvidence.slice(0, 5).map((item) => item.id),
  ]);
  const visibleEvidence = showAllEvidence
    ? currentEvidence
    : currentEvidence.filter((item) => previewEvidenceIds.has(item.id));

  return (
    <li>
      <article
        aria-labelledby={titleId}
        className="min-w-0 rounded-xl border border-zinc-200 p-4 sm:p-5 dark:border-zinc-800"
      >
        <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className={currentImportance === "required"
                ? "rounded-full bg-red-50 px-2.5 py-1 font-semibold text-red-800 dark:bg-red-950/50 dark:text-red-200"
                : "rounded-full bg-blue-50 px-2.5 py-1 font-semibold text-blue-800 dark:bg-blue-950/50 dark:text-blue-200"}
              >
                {currentImportance === "required" ? "Required" : "Preferred"}
              </span>
              {included ? (
                <CoverageBadge coverage={currentCoverage} />
              ) : (
                <span className="rounded-full bg-zinc-100 px-2.5 py-1 font-semibold text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
                  Excluded from next revision
                </span>
              )}
              <span className="text-zinc-500">Source characters {requirement.source_start + 1}–{requirement.source_end}</span>
            </div>
            <h4 id={titleId} className="sr-only">Requirement {requirement.ordinal}</h4>
            <blockquote className="mt-3 whitespace-pre-wrap break-words border-l-2 border-indigo-300 pl-3 text-sm leading-6 dark:border-indigo-800">
              {requirement.text}
            </blockquote>
          </div>
          <button type="button" disabled={copyDisabled} onClick={onCopy} className={secondaryButtonClasses}>
            {copied ? "Copied exact excerpt" : "Copy exact excerpt"}
          </button>
        </div>

        {readOnly ? (
          <div className="mt-5">
            <p className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Reviewed evidence</p>
            {requirement.evidence.length > 0 ? (
              <ul className="mt-2 space-y-2">
                {requirement.evidence.map((evidence) => (
                  <EvidenceSnapshot key={`${evidence.id}:${evidence.version}`} evidence={evidence} />
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-sm text-zinc-500">Recorded as an unsupported gap with no evidence attached.</p>
            )}
          </div>
        ) : (
          <div className="mt-5 space-y-5">
            <div className="grid min-w-0 gap-3 rounded-lg bg-zinc-50 p-3 text-sm sm:grid-cols-2 dark:bg-zinc-950/60">
              <label className="flex items-start gap-3 leading-6">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={included}
                  disabled={reviewDisabled}
                  onChange={onIncluded}
                />
                <span>
                  <strong className="block">Include in my fit review</strong>
                  <span className="block text-xs text-zinc-500">
                    Exclude marketing, benefits, or responsibilities that are not actual fit requirements.
                  </span>
                </span>
              </label>
              <label className="block">
                <span className="font-semibold">Requirement type</span>
                <select
                  value={currentImportance}
                  disabled={reviewDisabled || !included}
                  onChange={(event) => onImportance(
                    event.target.value as ApplicationPackRequirementImportance,
                  )}
                  className={`${inputClasses} mt-2`}
                >
                  <option value="required">Required</option>
                  <option value="preferred">Preferred</option>
                </select>
              </label>
            </div>

            <fieldset disabled={reviewDisabled || !included}>
              <legend className="text-sm font-semibold">Coverage decision</legend>
              <div className="mt-3 grid min-w-0 gap-2 sm:grid-cols-2">
                {COVERAGE_OPTIONS.map((option) => (
                  <label key={option.value} className="flex min-w-0 items-start gap-3 rounded-lg border border-zinc-200 p-3 text-sm dark:border-zinc-800">
                    <input
                      type="radio"
                      name={`coverage-${requirement.id}`}
                      value={option.value}
                      className="mt-1"
                      checked={currentCoverage === option.value}
                      onChange={() => onCoverage(option.value)}
                    />
                    <span className="min-w-0">
                      <strong className="block">{option.label}</strong>
                      <span className="mt-1 block text-xs leading-5 text-zinc-500">{option.description}</span>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>

            <fieldset disabled={reviewDisabled || !included || currentCoverage === "unsupported"}>
              <legend className="text-sm font-semibold">Current approved evidence</legend>
              <p className="mt-1 text-xs leading-5 text-zinc-500">
                Select only achievements that genuinely support this exact statement. Unsupported gaps cannot carry evidence.
              </p>
              {unavailableEvidence.length > 0 ? (
                <StatusMessage kind="error">
                  {unavailableEvidence.length} saved evidence mapping{unavailableEvidence.length === 1 ? " is" : "s are"} no longer currently approved. Choose a current achievement or record an honest gap.
                </StatusMessage>
              ) : null}
              {currentEvidence.length > 0 ? (
                <div className="mt-3">
                  <div className="max-h-96 space-y-2 overflow-y-auto pr-1">
                    {visibleEvidence.map((evidence) => (
                      <label key={evidence.id} className="flex min-w-0 items-start gap-3 rounded-lg bg-zinc-50 p-3 text-sm dark:bg-zinc-950/60">
                        <input
                          type="checkbox"
                          className="mt-1"
                          checked={selectedIds.has(evidence.id)}
                          onChange={() => onEvidence(evidence.id)}
                        />
                        <span className="min-w-0">
                          <span className="block break-words leading-6">{evidence.statement}</span>
                          {evidence.source_excerpt ? (
                            <span className="mt-1 block whitespace-pre-wrap break-words text-xs leading-5 text-zinc-500">
                              Source excerpt: {evidence.source_excerpt}
                            </span>
                          ) : (
                            <span className="mt-1 block text-xs text-zinc-500">Owner-approved statement; no source excerpt saved.</span>
                          )}
                        </span>
                      </label>
                    ))}
                  </div>
                  {currentEvidence.length > visibleEvidence.length || showAllEvidence ? (
                    <button
                      type="button"
                      onClick={() => setShowAllEvidence((value) => !value)}
                      className={`${secondaryButtonClasses} mt-3`}
                    >
                      {showAllEvidence
                        ? "Show shorter evidence list"
                        : `Show all ${currentEvidence.length} approved achievements`}
                    </button>
                  ) : null}
                </div>
              ) : (
                <p className="mt-3 text-sm text-zinc-500">No current approved achievements are available.</p>
              )}
            </fieldset>
          </div>
        )}
      </article>
    </li>
  );
}

function EvidenceSnapshot({
  evidence,
}: {
  evidence: ApplicationPackRequirementResponse["evidence"][number];
}) {
  return (
    <li className="rounded-lg bg-zinc-50 p-3 text-sm dark:bg-zinc-950/60">
      <p className="break-words leading-6">{evidence.statement}</p>
      {evidence.source_excerpt ? (
        <p className="mt-1 whitespace-pre-wrap break-words text-xs leading-5 text-zinc-500">
          Source excerpt: {evidence.source_excerpt}
        </p>
      ) : null}
      <p className="mt-1 text-xs text-zinc-500">
        Approved {formatDate(evidence.approved_at)} · evidence version {evidence.version}
      </p>
    </li>
  );
}

function BlockerList({ blockers }: { blockers: ApplicationPackBlocker[] }) {
  if (blockers.length === 0) return null;
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-amber-950 dark:border-amber-900 dark:bg-amber-950/25 dark:text-amber-100">
      <h3 className="text-sm font-semibold">Review before continuing</h3>
      <ul className="mt-2 space-y-1 text-sm leading-6">
        {blockers.map((blocker) => <li key={blocker}>• {BLOCKER_COPY[blocker]}</li>)}
      </ul>
    </div>
  );
}

function PackStatusBadge({ status }: { status: ApplicationPackResponse["status"] }) {
  const copy = {
    not_started: "Not started",
    draft: "Draft review",
    reviewed: "Reviewed",
  }[status];
  const tone = status === "reviewed"
    ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
    : status === "draft"
      ? "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-200"
      : "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300";
  return <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${tone}`}>{copy}</span>;
}

function CoverageBadge({ coverage }: { coverage: ApplicationPackRequirementCoverage }) {
  const copy = {
    needs_review: "Needs review",
    supported: "Supported",
    partial: "Partial",
    unsupported: "Unsupported gap",
  }[coverage];
  const tone = {
    needs_review: "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
    supported: "bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
    partial: "bg-blue-50 text-blue-800 dark:bg-blue-950 dark:text-blue-200",
    unsupported: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  }[coverage];
  return <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${tone}`}>{copy}</span>;
}

function SummaryFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-xl bg-zinc-50 p-4 dark:bg-zinc-950/60">
      <p className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">{label}</p>
      <p className="mt-1 break-words text-sm font-medium">{value}</p>
    </div>
  );
}

function descriptionSourceLabel(
  source: NonNullable<ApplicationPackResponse["current_revision"]>["job_description_source"],
): string {
  return {
    persisted_description: "Captured posting description",
    owner_supplied: "Exact description pasted by you",
  }[source];
}

function buildRevisionPayload(
  projection: ApplicationPackResponse | null,
  drafts: Record<string, RequirementDraft>,
): ApplicationPackRevisionCreate | null {
  const revision = projection?.current_revision;
  if (!revision || !projection) return null;
  const evidenceById = new Map(
    projection.current_approved_evidence.map((item) => [item.id, item]),
  );
  const requirements: ApplicationPackRequirementReview[] = [];
  for (const requirement of revision.requirements) {
    const draft = drafts[requirement.id];
    if (!draft) return null;
    if (!draft.included) continue;
    const evidence_refs = draft.evidenceIds.flatMap((id) => {
      const evidence = evidenceById.get(id);
      return evidence ? [{ id: evidence.id, version: evidence.version }] : [];
    });
    if (
      ["supported", "partial"].includes(draft.coverage) &&
      evidence_refs.length === 0
    ) return null;
    requirements.push({
      id: requirement.id,
      ordinal: requirement.ordinal,
      importance: draft.importance,
      text: requirement.text,
      source_start: requirement.source_start,
      source_end: requirement.source_end,
      coverage: draft.coverage,
      evidence_refs,
    });
  }
  return requirements.length > 0
    ? { parent_revision_id: revision.id, requirements }
    : null;
}

function revisionMatchesPayload(
  response: ApplicationPackResponse,
  payload: ApplicationPackRevisionCreate,
): boolean {
  const revision = response.current_revision;
  if (
    !revision ||
    revision.parent_revision_id !== payload.parent_revision_id ||
    revision.source !== "edited" ||
    revision.requirements.length !== payload.requirements.length
  ) return false;
  return revision.requirements.every((requirement, index) => {
    const expected = payload.requirements[index];
    if (
      !expected ||
      requirement.id !== expected.id ||
      requirement.ordinal !== expected.ordinal ||
      requirement.importance !== expected.importance ||
      requirement.text !== expected.text ||
      requirement.source_start !== expected.source_start ||
      requirement.source_end !== expected.source_end ||
      requirement.coverage !== expected.coverage
    ) return false;
    const actualRefs = requirement.evidence.map((item) => `${item.id}:${item.version}`).sort();
    const expectedRefs = expected.evidence_refs.map((item) => `${item.id}:${item.version}`).sort();
    return JSON.stringify(actualRefs) === JSON.stringify(expectedRefs);
  });
}

function packErrorText(reason: unknown): string {
  return errorText(reason, "Unable to save the application pack.");
}
