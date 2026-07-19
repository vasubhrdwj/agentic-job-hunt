"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getApplicationArtifacts,
  getApplicationSubmission,
  transitionApplication,
} from "@/lib/application-api";
import type { ApplicationArtifactsResponse } from "@/lib/application-artifact-types";
import type {
  ApplicationSubmissionResponse,
  ApplicationTransitionCreate,
} from "@/lib/application-submission-types";
import type { ApplicationStage, ApplicationPostingState } from "@/lib/application-types";
import { persistedVerifiedDestination } from "@/lib/application-submission-handoff";
import { createIdempotencyKey, WorkspaceApiError } from "@/lib/workspace-api";
import { ApprovedResumeDownload } from "./approved-resume-download";
import {
  errorText,
  inputClasses,
  primaryButtonClasses,
  secondaryButtonClasses,
  StatusMessage,
} from "./workspace-ui";

interface PendingTransition {
  key: string;
  fingerprint: string;
  payload: ApplicationTransitionCreate;
  expectedVersion: number;
  navigateTo: string | null;
}

export function ApplicationSubmission({
  applicationId,
  applicationVersion,
  stage,
  postingState,
  ownerLocalDate,
  currentArtifacts,
  onApplicationChanged,
}: {
  applicationId: string;
  applicationVersion: number;
  stage: ApplicationStage;
  postingState: ApplicationPostingState;
  ownerLocalDate: string;
  currentArtifacts: ApplicationArtifactsResponse | null;
  onApplicationChanged: () => Promise<void>;
}) {
  const [artifacts, setArtifacts] = useState<ApplicationArtifactsResponse | null>(null);
  const effectiveArtifacts = newestArtifacts(applicationId, currentArtifacts, artifacts);
  const [projection, setProjection] = useState<ApplicationSubmissionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [readyDueOn, setReadyDueOn] = useState(ownerLocalDate);
  const [appliedOn, setAppliedOn] = useState(ownerLocalDate);
  const [followUpDueOn, setFollowUpDueOn] = useState(
    addBusinessDays(ownerLocalDate, 5),
  );
  const [destination, setDestination] = useState("");
  const [hasPending, setHasPending] = useState(false);
  const pending = useRef<PendingTransition | null>(null);

  const load = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);
    try {
      const [artifactResult, projectionResult] = await Promise.allSettled([
        getApplicationArtifacts(applicationId),
        getApplicationSubmission(applicationId),
      ]);
      const messages: string[] = [];
      if (artifactResult.status === "fulfilled") {
        setArtifacts(artifactResult.value);
      } else {
        messages.push(errorText(
          artifactResult.reason,
          "Unable to load the approved application materials.",
        ));
      }
      if (projectionResult.status === "fulfilled") {
        const nextProjection = projectionResult.value;
        setProjection(nextProjection);
        setDestination((current) => (
          current && nextProjection.available_destinations.includes(current)
            ? current
            : nextProjection.submission?.destination_url
              ?? nextProjection.available_destinations[0]
              ?? ""
        ));
      } else {
        messages.push(errorText(
          projectionResult.reason,
          "Unable to load manual application tracking.",
        ));
      }
      setError(messages.length > 0 ? messages.join(" ") : null);
      return projectionResult.status === "fulfilled" ? projectionResult.value : null;
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [applicationId]);

  useEffect(() => {
    const timer = setTimeout(() => void load(true), 0);
    return () => clearTimeout(timer);
  }, [applicationVersion, load]);

  const exactMaterials = useMemo(() => {
    if (effectiveArtifacts?.status !== "approved") return null;
    if (effectiveArtifacts.blockers.some((blocker) => blocker !== "posting_closed")) return null;
    const pack = effectiveArtifacts.pack;
    const revision = effectiveArtifacts.approved_revision;
    const event = effectiveArtifacts.approval_event;
    const resume = effectiveArtifacts.tailored_resume_version;
    if (!pack || !revision || !event || !resume) return null;
    return {
      application_pack_id: pack.id,
      application_pack_revision_id: revision.grounding_revision_id,
      application_pack_review_event_id: revision.grounding_review_event_id,
      application_artifact_revision_id: revision.id,
      application_artifact_approval_event_id: event.id,
      tailored_resume_version_id: resume.id,
    };
  }, [effectiveArtifacts]);

  async function runTransition(
    payload: ApplicationTransitionCreate,
    navigateTo: string | null = null,
  ) {
    if (busy) return;
    const fingerprint = JSON.stringify({ payload, navigateTo });
    const existing = pending.current;
    if (existing && existing.fingerprint !== fingerprint) {
      setError("Retry the unchanged pending action or refresh its saved state before changing inputs.");
      return;
    }
    const request = existing ?? {
      key: createIdempotencyKey(`application-transition:${applicationId}:${payload.to_stage}`),
      fingerprint,
      payload,
      expectedVersion: applicationVersion,
      navigateTo,
    };
    pending.current = request;
    setHasPending(true);
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const attempt = await transitionApplication(
          applicationId,
          request.expectedVersion,
          request.key,
          request.payload,
        )
        .then(() => ({ ok: true as const }))
        .catch((reason: unknown) => ({ ok: false as const, reason }));

      if (!attempt.ok) {
        const reason = attempt.reason;
        const saved = await load(false);
        const apiError = reason instanceof WorkspaceApiError ? reason : null;
        const ambiguous = !apiError || apiError.retryable || apiError.code === "mutation_pending";
        if (saved && transitionMatches(saved, payload)) {
          pending.current = null;
          setHasPending(false);
          await refreshParent();
          setNotice("The saved transition was confirmed after checking the durable record.");
        } else if (saved?.submission) {
          pending.current = null;
          setHasPending(false);
          await refreshParent();
          setError(
            "A different durable application record already exists. Review the exact saved receipt below; it was not replaced.",
          );
        } else if (!ambiguous) {
          pending.current = null;
          setHasPending(false);
          await refreshParent();
          setError(errorText(reason, "The application transition was rejected."));
        } else {
          setError(
            `${errorText(reason, "The transition result is not yet confirmed.")} ` +
            "Your exact request is retained; retry it unchanged or check saved state.",
          );
        }
        return;
      }

      pending.current = null;
      setHasPending(false);
      const parentRefreshed = await refreshParent();
      await load(false);
      setNotice(
        payload.to_stage === "ready_to_apply"
          ? "This exact pack is ready. Continuing to the verified employer site…"
          : parentRefreshed
            ? "Your manual application was recorded with the exact materials used."
            : "Your manual application was recorded. Refresh the page if the summary has not updated.",
      );
      if (request.navigateTo) {
        window.location.assign(request.navigateTo);
      }
    } finally {
      setBusy(false);
    }
  }

  async function refreshParent(): Promise<boolean> {
    try {
      await onApplicationChanged();
      return true;
    } catch {
      return false;
    }
  }

  async function checkSavedState() {
    const request = pending.current;
    if (!request || busy) return;
    setBusy(true);
    setError(null);
    try {
      const saved = await load(false);
      if (saved && transitionMatches(saved, request.payload)) {
        pending.current = null;
        setHasPending(false);
        await refreshParent();
        setNotice("The exact transition was confirmed from the durable record.");
      } else if (saved?.submission) {
        pending.current = null;
        setHasPending(false);
        await refreshParent();
        setError(
          "A different durable application record already exists. Review the exact saved receipt below.",
        );
      } else if (
        request.payload.to_stage === "ready_to_apply" &&
        saved?.stage === "ready_to_apply"
      ) {
        setError(
          "The ready state is visible, but this read view cannot prove it came from the exact pending request. Retry unchanged with the original safe receipt before continuing.",
        );
      } else {
        setError(
          "This exact transition is not visible yet. Retry the unchanged request with its original safe receipt.",
        );
      }
    } finally {
      setBusy(false);
    }
  }

  async function retryPending() {
    const request = pending.current;
    if (!request || busy) return;
    await runTransition(request.payload, request.navigateTo);
  }

  async function continueToEmployer() {
    if (!exactMaterials) {
      setError("The approved application materials are no longer available. Refresh before continuing.");
      return;
    }
    const verifiedDestination = persistedVerifiedDestination(projection, destination);
    if (!verifiedDestination) {
      setError(
        "The selected employer destination is no longer in the persisted verified list. Refresh before continuing.",
      );
      return;
    }
    await runTransition({
      ...exactMaterials,
      to_stage: "ready_to_apply",
      next_action_due_on: readyDueOn,
      confirm_ready: true,
    }, verifiedDestination);
  }

  function continueToPersistedEmployer() {
    const verifiedDestination = persistedVerifiedDestination(projection, destination);
    if (!verifiedDestination) {
      setError(
        "The selected employer destination is no longer in the persisted verified list. Refresh before continuing.",
      );
      return;
    }
    window.location.assign(verifiedDestination);
  }

  if (loading) {
    return <p role="status" className="text-sm text-zinc-500">Loading application checklist…</p>;
  }

  const durableStage = projection?.stage ?? stage;
  const receipt = projection?.submission ?? null;
  const destinationHost = safeHost(destination);
  const controlsLocked = busy || hasPending;
  const receiptArtifact = receipt &&
    effectiveArtifacts?.approved_revision?.id === receipt.application_artifact_revision_id
    ? effectiveArtifacts.approved_revision
    : null;
  const receiptResume = receipt &&
    effectiveArtifacts?.tailored_resume_version?.id === receipt.tailored_resume_version_id
    ? effectiveArtifacts.tailored_resume_version
    : null;

  return (
    <section
      id="manual-application"
      aria-labelledby="manual-application-title"
      className="min-w-0 rounded-2xl border border-emerald-200 bg-white p-5 shadow-sm sm:p-7 dark:border-emerald-900 dark:bg-zinc-900/70"
    >
      <p className="text-xs font-semibold uppercase tracking-[0.14em] text-emerald-700 dark:text-emerald-300">
        Manual application
      </p>
      <h2 id="manual-application-title" className="mt-2 text-xl font-semibold">
        Continue to the employer, then record the result
      </h2>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
        The app records this exact pack as ready, then takes you to the verified employer site.
        It never fills or submits the employer form for you.
      </p>

      {error ? <div className="mt-4"><StatusMessage kind="error">{error}</StatusMessage></div> : null}
      {notice ? <div className="mt-4"><StatusMessage kind="success">{notice}</StatusMessage></div> : null}

      {receipt ? (
        <div className="mt-6 rounded-xl border border-emerald-200 bg-emerald-50 p-4 dark:border-emerald-900 dark:bg-emerald-950/25">
          <h3 className="font-semibold text-emerald-950 dark:text-emerald-100">Application recorded</h3>
          <p className="mt-1 text-sm text-emerald-900 dark:text-emerald-200">
            Recorded only — this app did not submit the application for you.
          </p>
          <dl className="mt-4 grid gap-4 text-sm sm:grid-cols-2">
            <ReceiptItem label="Applied on" value={formatDateOnly(receipt.applied_on)} />
            <ReceiptItem label="Destination" value={safeHost(receipt.destination_url)} />
            <ReceiptItem
              label="Artifact revision"
              value={receiptArtifact
                ? `#${receiptArtifact.revision_number}`
                : receipt.application_artifact_revision_id}
            />
            <ReceiptItem
              label="Tailored résumé"
              value={receiptResume?.label ?? receipt.tailored_resume_version_id}
            />
          </dl>
          <a
            href={receipt.destination_url}
            target="_blank"
            rel="noopener noreferrer"
            className={`${secondaryButtonClasses} mt-5 max-w-full break-all`}
          >
            Open recorded destination ↗
          </a>
        </div>
      ) : !exactMaterials ? (
        <StatusMessage kind="info">
          <span className="font-medium">Approve one exact application-material revision first.</span>{" "}
          The tailored résumé, answers, note, and their source links must be reviewed before readiness can be recorded.
        </StatusMessage>
      ) : !projection?.first_party_verified ? (
        <StatusMessage kind="info">
          The saved destination has not been verified as first-party. Do not enter personal information until a verified employer careers or ATS link is available.
        </StatusMessage>
      ) : postingState !== "open" && durableStage === "pursuing" ? (
        <StatusMessage kind="info">
          This posting is no longer open, so a new ready-to-apply transition is blocked. Your reviewed materials remain readable.
        </StatusMessage>
      ) : durableStage === "pursuing" ? (
        <div className="mt-6 space-y-5">
          <DestinationPicker
            destinations={projection.available_destinations}
            value={destination}
            disabled={controlsLocked}
            onChange={setDestination}
          />
          <ExactMaterialsSummary artifacts={effectiveArtifacts} destinationHost={destinationHost} />
          <ApprovedResumeDownload
            applicationId={applicationId}
            available={
              effectiveArtifacts?.status === "approved" &&
              effectiveArtifacts.current_revision?.id === effectiveArtifacts.approved_revision?.id
            }
            label="Download approved résumé (.docx)"
          />
          <p className="text-sm leading-6 text-zinc-600 dark:text-zinc-400">
            Continuing records the approved materials as ready, then leaves this app. You will still review and submit the employer form yourself.
          </p>
          <details className="rounded-lg border border-zinc-200 px-4 py-3 text-sm dark:border-zinc-800">
            <summary className="cursor-pointer font-medium">
              Submit by {formatDateOnly(readyDueOn)} (change)
            </summary>
            <label className="mt-3 block font-medium">
              Submit by
              <input
                type="date"
                disabled={controlsLocked}
                value={readyDueOn}
                min={ownerLocalDate}
                onChange={(event) => setReadyDueOn(event.target.value)}
                className={`${inputClasses} mt-2 sm:max-w-xs`}
              />
            </label>
          </details>
          <button
            type="button"
            disabled={controlsLocked || !destination || !readyDueOn}
            onClick={() => void continueToEmployer()}
            className={`${primaryButtonClasses} w-full sm:w-auto`}
          >
            {busy ? "Preparing handoff…" : `Continue to ${destinationHost || "employer"} →`}
          </button>
        </div>
      ) : durableStage === "ready_to_apply" ? (
        <div className="mt-6 space-y-5">
          <DestinationPicker
            destinations={projection.available_destinations}
            value={destination}
            disabled={controlsLocked}
            onChange={setDestination}
          />
          <ExactMaterialsSummary artifacts={effectiveArtifacts} destinationHost={destinationHost} />
          <ApprovedResumeDownload
            applicationId={applicationId}
            available={
              effectiveArtifacts?.status === "approved" &&
              effectiveArtifacts.current_revision?.id === effectiveArtifacts.approved_revision?.id
            }
            label="Download approved résumé (.docx)"
          />
          <button
            type="button"
            disabled={controlsLocked || !destination}
            onClick={continueToPersistedEmployer}
            className={`${secondaryButtonClasses} w-full sm:w-auto`}
          >
            Continue to employer →
          </button>
          <div className="rounded-xl bg-zinc-50 p-4 text-sm dark:bg-zinc-950/60">
            <p className="font-medium">After you submit on the employer site</p>
            <p className="mt-1 leading-6 text-zinc-600 dark:text-zinc-400">
              Record it here as applied on {formatDateOnly(appliedOn)}, with follow-up due {formatDateOnly(followUpDueOn)}.
              Nothing is recorded as applied until you click the confirmation button below.
            </p>
          </div>
          <details className="rounded-lg border border-zinc-200 px-4 py-3 text-sm dark:border-zinc-800">
            <summary className="cursor-pointer font-medium">Change application or follow-up dates</summary>
            <div className="mt-3 grid gap-4 sm:grid-cols-2">
              <label className="block font-medium">
                Date you applied
                <input
                  type="date"
                  disabled={controlsLocked}
                  value={appliedOn}
                  max={ownerLocalDate}
                  onChange={(event) => setAppliedOn(event.target.value)}
                  className={`${inputClasses} mt-2`}
                />
              </label>
              <label className="block font-medium">
                Follow-up due
                <input
                  type="date"
                  disabled={controlsLocked}
                  value={followUpDueOn}
                  min={appliedOn > ownerLocalDate ? appliedOn : ownerLocalDate}
                  onChange={(event) => setFollowUpDueOn(event.target.value)}
                  className={`${inputClasses} mt-2`}
                />
              </label>
            </div>
          </details>
          <button
            type="button"
            disabled={controlsLocked || !destination || !appliedOn || !followUpDueOn}
            onClick={() => void runTransition({
              ...exactMaterials,
              to_stage: "applied",
              destination_url: destination,
              applied_on: appliedOn,
              next_action_due_on: followUpDueOn,
              confirm_manual_submission: true,
            })}
            className={`${primaryButtonClasses} w-full sm:w-auto`}
          >
            {busy ? "Recording application…" : "I submitted — record application"}
          </button>
        </div>
      ) : null}

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

function DestinationPicker({
  destinations,
  value,
  disabled,
  onChange,
}: {
  destinations: string[];
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  if (destinations.length <= 1) return null;
  return (
    <label className="block text-sm font-medium">
      Verified employer destination
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className={`${inputClasses} mt-2`}
      >
        {destinations.map((item) => (
          <option key={item} value={item}>{safeHost(item)}</option>
        ))}
      </select>
      <span className="mt-1 block break-all text-xs font-normal text-zinc-500">
        {value}
      </span>
    </label>
  );
}

function ExactMaterialsSummary({
  artifacts,
  destinationHost,
}: {
  artifacts: ApplicationArtifactsResponse | null;
  destinationHost: string;
}) {
  return (
    <dl className="grid gap-4 rounded-xl bg-zinc-50 p-4 text-sm sm:grid-cols-3 dark:bg-zinc-950/60">
      <ReceiptItem label="Artifact" value={`Revision #${artifacts?.approved_revision?.revision_number ?? "—"}`} />
      <ReceiptItem label="Tailored résumé" value={artifacts?.tailored_resume_version?.label ?? "Approved exact version"} />
      <ReceiptItem label="Destination" value={destinationHost || "Verified employer link"} />
    </dl>
  );
}

function ReceiptItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium uppercase tracking-wide text-zinc-500">{label}</dt>
      <dd className="mt-1 break-words font-medium">{value}</dd>
    </div>
  );
}

function safeHost(value: string): string {
  try {
    return new URL(value).hostname;
  } catch {
    return value || "—";
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

function newestArtifacts(
  applicationId: string,
  handedOff: ApplicationArtifactsResponse | null,
  independentlyLoaded: ApplicationArtifactsResponse | null,
): ApplicationArtifactsResponse | null {
  if (handedOff?.application_id !== applicationId) handedOff = null;
  if (independentlyLoaded?.application_id !== applicationId) independentlyLoaded = null;
  if (!handedOff) return independentlyLoaded;
  if (!independentlyLoaded) return handedOff;
  const handedOffVersion = handedOff.pack?.version ?? 0;
  const loadedVersion = independentlyLoaded.pack?.version ?? 0;
  return handedOffVersion >= loadedVersion ? handedOff : independentlyLoaded;
}

function transitionMatches(
  projection: ApplicationSubmissionResponse,
  payload: ApplicationTransitionCreate,
): boolean {
  if (payload.to_stage === "ready_to_apply") {
    // The read projection does not persist the ready request's exact material
    // references or due date. Only a successful same-receipt replay can prove
    // that an ambiguous ready mutation was this request.
    return false;
  }
  if (payload.to_stage !== "applied") return false;
  const submission = projection.submission;
  return Boolean(
    submission &&
    submission.application_pack_id === payload.application_pack_id &&
    submission.application_pack_revision_id === payload.application_pack_revision_id &&
    submission.application_pack_review_event_id === payload.application_pack_review_event_id &&
    submission.application_artifact_revision_id === payload.application_artifact_revision_id &&
    submission.application_artifact_approval_event_id === payload.application_artifact_approval_event_id &&
    submission.tailored_resume_version_id === payload.tailored_resume_version_id &&
    submission.destination_url === payload.destination_url &&
    submission.applied_on === payload.applied_on
  );
}
