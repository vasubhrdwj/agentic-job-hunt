"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  getApplicationContacts,
  startApplicationContactSearch,
} from "@/lib/application-api";
import type {
  ApplicationContactBenchResponse,
  ApplicationPostingState,
  ContactBenchItem,
  ContactSearchSnapshot,
  RelevanceEvidenceResponse,
} from "@/lib/application-types";
import { createIdempotencyKey, WorkspaceApiError } from "@/lib/workspace-api";
import { ApplicationOutreach } from "./application-outreach";
import {
  errorText,
  formatDate,
  primaryButtonClasses,
  secondaryButtonClasses,
  StatusMessage,
} from "./workspace-ui";

const ACTIVE_SEARCH_STATES = new Set(["queued", "running"]);
const MAX_POLL_FAILURES = 5;

const CATEGORY_LABELS: Record<ContactBenchItem["category"], string> = {
  warm_path: "Warm path",
  team_peer: "Team peer",
  adjacent_peer: "Adjacent peer",
  team_leader: "Team leader",
  recruiter: "Recruiter",
  other: "Relevant contact",
};

interface PendingStart {
  applicationId: string;
  key: string;
  baselinePlanNumber: number;
}

export function ApplicationPeople({
  applicationId,
  applicationVersion,
  postingState,
}: {
  applicationId: string;
  applicationVersion: number;
  postingState: ApplicationPostingState;
}) {
  const [bench, setBench] = useState<ApplicationContactBenchResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [liveWarning, setLiveWarning] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [pollPaused, setPollPaused] = useState(false);
  const [pollNonce, setPollNonce] = useState(0);
  const pendingStart = useRef<PendingStart | null>(null);
  const requestGeneration = useRef(0);
  const benchRef = useRef<ApplicationContactBenchResponse | null>(null);

  useEffect(() => {
    requestGeneration.current += 1;
    pendingStart.current = null;
    benchRef.current = null;
  }, [applicationId]);

  const acceptResponse = useCallback((
    next: ApplicationContactBenchResponse,
    expectedApplicationId: string,
  ) => {
    if (next.application_id !== expectedApplicationId) return false;
    const previous = benchRef.current;
    if (previous) {
      const previousPlan = Math.max(
        previous.current_search?.plan_number ?? 0,
        previous.last_completed_result?.plan_number ?? 0,
      );
      const nextPlan = Math.max(
        next.current_search?.plan_number ?? 0,
        next.last_completed_result?.plan_number ?? 0,
      );
      if (nextPlan < previousPlan) return false;
      if (
        previous.current_search &&
        next.current_search &&
        previous.current_search.id === next.current_search.id
      ) {
        const order: Record<ContactSearchSnapshot["status"], number> = {
          queued: 0,
          running: 1,
          completed: 2,
          failed: 2,
          cancelled: 2,
        };
        if (order[next.current_search.status] < order[previous.current_search.status]) {
          return false;
        }
        if (
          order[previous.current_search.status] === 2 &&
          next.current_search.status !== previous.current_search.status
        ) return false;
      }
      if (previous.last_completed_result && !next.last_completed_result) return false;
      if (
        previous.last_completed_result &&
        next.last_completed_result &&
        next.last_completed_result.plan_number < previous.last_completed_result.plan_number
      ) return false;
    }
    const pending = pendingStart.current;
    if (
      pending &&
      pending.applicationId === next.application_id &&
      (next.current_search?.plan_number ?? 0) > pending.baselinePlanNumber
    ) {
      // A successful GET is durable proof that a POST with a lost response did
      // create a newer plan. Future retries must use a fresh receipt key.
      pendingStart.current = null;
    }
    benchRef.current = next;
    setBench(next);
    return true;
  }, []);

  const refresh = useCallback(async (showLoading: boolean) => {
    const requestedApplicationId = applicationId;
    const generation = requestGeneration.current;
    if (showLoading) setLoading(true);
    try {
      const next = await getApplicationContacts(requestedApplicationId);
      if (
        requestGeneration.current !== generation ||
        !acceptResponse(next, requestedApplicationId)
      ) return;
      setLoadError(null);
      setLiveWarning(null);
    } catch (reason) {
      if (requestGeneration.current !== generation) return;
      setLoadError(contactRequestError(reason, "Unable to load the contact bench."));
    } finally {
      if (showLoading && requestGeneration.current === generation) {
        setLoading(false);
      }
    }
  }, [acceptResponse, applicationId]);

  useEffect(() => {
    let active = true;
    const timer = setTimeout(() => {
      if (active) void refresh(true);
    }, 0);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [refresh]);

  const currentSearchId = bench?.current_search?.id ?? null;
  const currentSearchStatus = bench?.current_search?.status ?? null;
  useEffect(() => {
    if (!currentSearchStatus || !ACTIVE_SEARCH_STATES.has(currentSearchStatus)) {
      return;
    }

    let active = true;
    let failures = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const generation = requestGeneration.current;

    async function poll() {
      try {
        const next = await getApplicationContacts(applicationId);
        if (!active || requestGeneration.current !== generation) return;
        failures = 0;
        if (!acceptResponse(next, applicationId)) return;
        setLoadError(null);
        setLiveWarning(null);
        setPollPaused(false);
        if (
          next.current_search &&
          ACTIVE_SEARCH_STATES.has(next.current_search.status)
        ) {
          timer = setTimeout(() => void poll(), 2_000);
        }
      } catch (reason) {
        if (!active || requestGeneration.current !== generation) return;
        failures += 1;
        const detail = contactRequestError(
          reason,
          "Unable to refresh contact-search progress.",
        );
        if (failures >= MAX_POLL_FAILURES) {
          setPollPaused(true);
          setLiveWarning(
            `${detail} Live updates are paused, but the last saved bench remains below.`,
          );
          return;
        }
        setLiveWarning(
          `${detail} Retrying live updates without clearing saved contacts.`,
        );
        const delay = Math.min(16_000, 2_000 * (2 ** failures));
        timer = setTimeout(() => void poll(), delay);
      }
    }

    timer = setTimeout(() => void poll(), 2_000);
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [acceptResponse, applicationId, currentSearchId, currentSearchStatus, pollNonce]);

  async function startSearch() {
    const baselinePlanNumber = bench?.current_search?.plan_number ?? 0;
    if (pendingStart.current?.applicationId !== applicationId) {
      pendingStart.current = {
        applicationId,
        key: createIdempotencyKey(`application-contacts:${applicationId}`),
        baselinePlanNumber,
      };
    }
    const receipt = pendingStart.current;
    const requestedApplicationId = applicationId;
    const generation = requestGeneration.current;
    setStarting(true);
    setStartError(null);
    setLiveWarning(null);
    try {
      const next = await startApplicationContactSearch(
        requestedApplicationId,
        applicationVersion,
        receipt.key,
      );
      if (
        requestGeneration.current !== generation ||
        !acceptResponse(next, requestedApplicationId)
      ) return;
      if (pendingStart.current === receipt) pendingStart.current = null;
      setLoadError(null);
      setPollPaused(false);
    } catch (reason) {
      if (requestGeneration.current !== generation) return;
      const ambiguousFailure =
        !(reason instanceof WorkspaceApiError) || reason.retryable;
      if (ambiguousFailure) {
        try {
          const reconciled = await getApplicationContacts(requestedApplicationId);
          if (requestGeneration.current !== generation) return;
          const created =
            (reconciled.current_search?.plan_number ?? 0) > baselinePlanNumber;
          if (created && acceptResponse(reconciled, requestedApplicationId)) {
            if (pendingStart.current === receipt) pendingStart.current = null;
            setLoadError(null);
            setStartError(null);
            setPollPaused(false);
            return;
          }
        } catch {
          // The stable receipt key is intentionally retained. Repeating the
          // explicit action is safe even when both response checks were lost.
        }
      }
      const detail = contactRequestError(reason, "Unable to start the contact search.");
      setStartError(
        ambiguousFailure
          ? `${detail} Trying the button again is safe and will not duplicate the search.`
          : detail,
      );
    } finally {
      if (requestGeneration.current === generation) setStarting(false);
    }
  }

  function retryLiveUpdates() {
    setPollPaused(false);
    setLiveWarning(null);
    setPollNonce((value) => value + 1);
  }

  const current = bench?.current_search ?? null;
  const result = bench?.last_completed_result ?? null;
  const activeSearch = Boolean(
    current && ACTIVE_SEARCH_STATES.has(current.status),
  );
  const canStart = postingState === "open" && !activeSearch && !starting;

  return (
    <>
    <section
      aria-labelledby="application-people-title"
      aria-busy={loading || starting}
      className="rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm sm:p-7 dark:border-zinc-800 dark:bg-zinc-900/70"
    >
      <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-indigo-600 dark:text-indigo-400">
            People
          </p>
          <h2 id="application-people-title" className="mt-2 text-xl font-semibold tracking-tight">
            Build a verified contact bench
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
            Find up to five appropriate peers, leaders, and recruiters with public
            evidence that they currently work at this company. Fewer than five is
            shown honestly when the evidence is not strong enough.
          </p>
        </div>
        {bench ? (
          <div className="shrink-0 rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-3 text-center dark:border-indigo-900 dark:bg-indigo-950/30">
            <p className="text-2xl font-semibold text-indigo-950 dark:text-indigo-100">
              {bench.verified_count}/{bench.target_count}
            </p>
            <p className="text-xs font-medium text-indigo-700 dark:text-indigo-300">
              evidence-verified
            </p>
          </div>
        ) : null}
      </div>

      <div className="mt-6 space-y-4">
        {loading && !bench ? (
          <p role="status" className="text-sm text-zinc-500">
            Loading contact bench…
          </p>
        ) : null}

        {loadError && !bench ? (
          <StatusMessage kind="error">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span>{loadError}</span>
              <button
                type="button"
                onClick={() => void refresh(true)}
                className={secondaryButtonClasses}
              >
                Try again
              </button>
            </div>
          </StatusMessage>
        ) : null}

        {bench ? <SearchStatus bench={bench} /> : null}

        {startError ? <StatusMessage kind="error">{startError}</StatusMessage> : null}
        {loadError && bench ? (
          <StatusMessage kind="error">
            {loadError} The last saved bench is still shown.
          </StatusMessage>
        ) : null}
        {liveWarning ? (
          <StatusMessage kind="info">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span>{liveWarning}</span>
              {pollPaused ? (
                <button
                  type="button"
                  onClick={retryLiveUpdates}
                  className={secondaryButtonClasses}
                >
                  Retry live updates
                </button>
              ) : null}
            </div>
          </StatusMessage>
        ) : null}

        {postingState !== "open" ? (
          <StatusMessage kind="info">
            A new people search is unavailable because this posting is {postingState}.
            Any previously verified contacts remain visible for review.
          </StatusMessage>
        ) : null}

        {!loading && bench ? (
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => void startSearch()}
              disabled={!canStart}
              className={primaryButtonClasses}
            >
              {starting
                ? "Starting search…"
                : activeSearch
                  ? "Search in progress"
                  : searchButtonLabel(bench.status)}
            </button>
            {result ? (
              <span className="text-xs text-zinc-500">
                Last completed {formatDate(result.completed_at)}
              </span>
            ) : null}
          </div>
        ) : null}

        {result ? <ContactResult result={result} /> : null}

        <p className="border-t border-zinc-200 pt-4 text-xs leading-5 text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
          This search only finds profiles and preserves evidence. Any message work
          is an explicit, separately tracked manual action below.
        </p>
      </div>
    </section>
    <ApplicationOutreach
      key={applicationId}
      applicationId={applicationId}
      applicationVersion={applicationVersion}
      postingState={postingState}
      benchReady={Boolean(result && result.verified_count > 0)}
      contactSearchRunning={activeSearch}
    />
    </>
  );
}

function SearchStatus({ bench }: { bench: ApplicationContactBenchResponse }) {
  const search = bench.current_search;
  if (bench.status === "not_started") {
    return (
      <StatusMessage kind="info">
        No people search has been started for this application. Starting one makes
        public provider requests and saves only evidence-backed results.
      </StatusMessage>
    );
  }
  if (!search) return null;

  if (search.status === "queued") {
    return (
      <StatusMessage kind="info">
        {search.retryable
          ? "A provider attempt was incomplete. The durable worker will retry it automatically."
          : "Contact search queued. The durable worker will start it shortly."}
        {bench.last_completed_result
          ? " Your last completed bench remains visible while this refresh runs."
          : ""}
      </StatusMessage>
    );
  }
  if (search.status === "running") {
    return (
      <StatusMessage kind="info">
        {runningLabel(search)}
        {bench.last_completed_result
          ? " Your last completed bench remains visible until the new result is complete."
          : ""}
      </StatusMessage>
    );
  }
  if (search.status === "failed") {
    return (
      <StatusMessage kind="error">
        {friendlySearchError(search.error_code)}
        {bench.last_completed_result
          ? " The last completed bench is still safe to review below."
          : " You can try again when the issue is resolved."}
      </StatusMessage>
    );
  }
  if (search.status === "cancelled") {
    return (
      <StatusMessage kind="info">
        The latest contact search was cancelled before publication.
        {bench.last_completed_result
          ? " The last completed bench is still shown below."
          : " No contacts were published."}
      </StatusMessage>
    );
  }
  if (search.coverage_status === "met") {
    return (
      <StatusMessage kind="success">
        Five distinct contacts met the current-employer evidence requirement.
      </StatusMessage>
    );
  }
  return (
      <StatusMessage kind="info">
        The search completed with {search.selected_count} of {search.target_count}
        {" "}verified contacts. The result is intentionally not padded.
      </StatusMessage>
  );
}

function ContactResult({
  result,
}: {
  result: NonNullable<ApplicationContactBenchResponse["last_completed_result"]>;
}) {
  const availableCount = result.contacts.filter(
    (contact) =>
      contact.lifecycle === "active" &&
      contact.bench_state !== "paused" &&
      contact.bench_state !== "stopped",
  ).length;
  const restrictedCount = result.contacts.length - availableCount;
  return (
    <div className="space-y-5">
      <div>
        <h3 className="font-semibold">
          {result.verified_count}/{result.target_count} evidence-verified people
        </h3>
        <p className="mt-1 text-sm text-zinc-500">
          Ranked for relevance and category diversity; each person has preserved
          current-employer evidence.
        </p>
      </div>

      {restrictedCount > 0 ? (
        <StatusMessage kind="info">
          {availableCount} {availableCount === 1 ? "profile is" : "profiles are"}
          {" "}active and available for review; {restrictedCount}
          {" "}{restrictedCount === 1 ? "is" : "are"} restricted and must not be
          used for outreach.
        </StatusMessage>
      ) : null}

      {result.shortfall_reasons.length > 0 ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/25">
          <h4 className="text-sm font-semibold text-amber-950 dark:text-amber-100">
            Why this bench has fewer than five
          </h4>
          <ul className="mt-3 space-y-2 text-sm text-amber-900 dark:text-amber-200">
            {result.shortfall_reasons.map((reason) => (
              <li key={reason.code} className="flex gap-2">
                <span className="inline-flex h-6 min-w-6 shrink-0 items-center justify-center rounded-full bg-amber-200 px-1.5 text-xs font-semibold dark:bg-amber-900">
                  {reason.count}
                </span>
                <span className="leading-6">{reason.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {result.contacts.length > 0 ? (
        <ol className="grid gap-4 lg:grid-cols-2">
          {result.contacts.map((contact) => (
            <ContactCard key={contact.id} contact={contact} />
          ))}
        </ol>
      ) : (
        <p className="rounded-xl border border-zinc-200 p-4 text-sm text-zinc-600 dark:border-zinc-800 dark:text-zinc-400">
          No public profile met the current-employer evidence floor in this search.
        </p>
      )}
    </div>
  );
}

function ContactCard({ contact }: { contact: ContactBenchItem }) {
  const restricted =
    contact.lifecycle !== "active" ||
    contact.bench_state === "paused" ||
    contact.bench_state === "stopped";
  return (
    <li className="min-w-0 rounded-xl border border-zinc-200 p-4 sm:p-5 dark:border-zinc-800">
      <div className="flex min-w-0 items-start gap-3">
        <span
          aria-label={`Rank ${contact.bench_rank}`}
          className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-sm font-semibold text-indigo-800 dark:bg-indigo-950 dark:text-indigo-200"
        >
          {contact.bench_rank}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <h4 className="break-words font-semibold">{contact.public_name}</h4>
            <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-xs font-medium text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
              {CATEGORY_LABELS[contact.category]}
            </span>
          </div>
          <p className="mt-1 break-words text-sm text-zinc-600 dark:text-zinc-400">
            {contact.current_title} at {contact.current_company}
          </p>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-zinc-500">
        <span className="font-medium text-emerald-700 dark:text-emerald-300">
          {Math.round(contact.confidence * 100)}% evidence confidence
        </span>
        <span>Verified {formatDate(contact.verified_at)}</span>
      </div>

      <p className="mt-4 break-words text-sm leading-6 text-zinc-700 [overflow-wrap:anywhere] dark:text-zinc-300">
        {contact.why_relevant}
      </p>

      <div className="mt-4 flex flex-wrap gap-2">
        <a
          href={contact.profile_url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Open public profile for ${contact.public_name} (opens in new tab)`}
          className={secondaryButtonClasses}
        >
          Open public profile ↗
        </a>
        <a
          href={contact.employer_evidence.url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Review employer evidence for ${contact.public_name} (opens in new tab)`}
          className={secondaryButtonClasses}
        >
          Review employer evidence ↗
        </a>
      </div>

      <div className="mt-4 rounded-lg bg-zinc-50 p-3 text-sm dark:bg-zinc-950/60">
        <p className="break-words leading-6 text-zinc-700 [overflow-wrap:anywhere] dark:text-zinc-300">
          “{contact.employer_evidence.excerpt}”
        </p>
        <p className="mt-2 text-xs text-zinc-500">
          {sourceLabel(contact.employer_evidence.source)} · checked {formatDate(contact.employer_evidence.observed_at)}
        </p>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <RelevanceFact
          label="Relationship"
          contactName={contact.public_name}
          evidence={contact.relationship}
        />
        <RelevanceFact
          label="Team proximity"
          contactName={contact.public_name}
          evidence={contact.team_proximity}
        />
      </div>

      {restricted ? (
        <p className="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-xs leading-5 text-red-900 dark:border-red-900 dark:bg-red-950/30 dark:text-red-100">
          This contact is {contact.lifecycle === "do_not_contact" ? "marked do not contact" : contact.lifecycle.replaceAll("_", " ")}
          {contact.bench_state === "paused" || contact.bench_state === "stopped"
            ? ` and the bench entry is ${contact.bench_state}`
            : ""}
          . Do not use it for outreach.
        </p>
      ) : null}
    </li>
  );
}

function RelevanceFact({
  label,
  contactName,
  evidence,
}: {
  label: string;
  contactName: string;
  evidence: RelevanceEvidenceResponse;
}) {
  if (evidence.status === "unknown" && !evidence.summary) return null;
  return (
    <div className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-800">
      <p className="font-semibold text-zinc-700 dark:text-zinc-300">
        {label} · {evidence.status}
      </p>
      {evidence.summary ? (
        <p className="mt-1 break-words leading-5 text-zinc-500 [overflow-wrap:anywhere]">
          {evidence.summary}
        </p>
      ) : null}
      {evidence.url ? (
        <a
          href={evidence.url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Review ${label.toLowerCase()} evidence for ${contactName} (opens in new tab)`}
          className="mt-2 inline-flex min-h-8 items-center font-medium text-indigo-700 hover:underline dark:text-indigo-300"
        >
          Review evidence ↗
        </a>
      ) : null}
    </div>
  );
}

function runningLabel(search: ContactSearchSnapshot): string {
  if (search.job_stage === "discovering_contacts") {
    return "Searching public profiles and checking current-employer evidence…";
  }
  if (search.job_stage === "finalizing_contacts") {
    return "Finalizing the verified contact bench…";
  }
  return "Building the verified contact bench…";
}

function searchButtonLabel(status: ApplicationContactBenchResponse["status"]): string {
  if (status === "not_started") return "Find 5 verified people";
  if (status === "completed") return "Refresh contact bench";
  return "Try contact search again";
}

function friendlySearchError(code: string | null): string {
  const messages: Record<string, string> = {
    provider_configuration_failure:
      "Public-profile search is not configured for this worker yet.",
    provider_unavailable:
      "The public-profile provider was unavailable after safe retries.",
    invalid_contact_search_reference:
      "The saved role changed in a way that makes this search unsafe to publish.",
    publication_conflict:
      "Another contact result changed while this search was being finalized.",
    contact_search_processing_failed:
      "The search could not be finalized after safe retries.",
    search_job_unavailable:
      "The durable contact-search job is unavailable. Start a new search to try again.",
    lease_expired:
      "The worker lost its lease before it could safely publish the result.",
  };
  return code && messages[code]
    ? messages[code]
    : "The latest contact search failed without publishing a partial result.";
}

function contactRequestError(reason: unknown, fallback: string): string {
  if (reason instanceof WorkspaceApiError && reason.isConflict) {
    return "This application changed in another tab. Reload the page, review the newer version, and try again.";
  }
  return errorText(reason, fallback);
}

function sourceLabel(value: string): string {
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}
