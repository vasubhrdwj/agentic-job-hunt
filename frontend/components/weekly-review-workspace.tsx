"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import type { FormEvent } from "react";

import {
  getWeeklyReview,
  reviewApplicationAction,
} from "@/lib/application-api";
import type {
  ApplicationActionReviewCreate,
  ContactRescueMetric,
  FunnelSegmentMetric,
  FunnelStage,
  FunnelStageMetric,
  OutreachObservedMetric,
  StaleApplicationReviewItem,
  WeeklyReviewDecision,
  WeeklyReviewResponse,
} from "@/lib/weekly-review-types";
import { createIdempotencyKey, WorkspaceApiError } from "@/lib/workspace-api";
import {
  errorText,
  inputClasses,
  primaryButtonClasses,
  secondaryButtonClasses,
  StatusMessage,
} from "./workspace-ui";

interface PendingActionReview {
  key: string;
  fingerprint: string;
  payload: ApplicationActionReviewCreate;
  applicationVersion: number;
  actionId: string;
  actionVersion: number;
}

export function WeeklyReviewWorkspace({
  ownerLocalDate,
}: {
  ownerLocalDate: string;
}) {
  const [response, setResponse] = useState<WeeklyReviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const requestInFlight = useRef(false);
  const staleTitleRef = useRef<HTMLHeadingElement>(null);

  const load = useCallback(async (preserve: boolean) => {
    if (requestInFlight.current) return null;
    requestInFlight.current = true;
    if (preserve) setRefreshing(true);
    else setLoading(true);
    setError(null);
    try {
      const next = await getWeeklyReview();
      setResponse(next);
      return next;
    } catch (reason) {
      setError(errorText(reason, "Unable to load your weekly review."));
      return null;
    } finally {
      requestInFlight.current = false;
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => void load(false), 0);
    return () => clearTimeout(timer);
  }, [load]);

  function actionReviewed(message: string, reconciled?: WeeklyReviewResponse) {
    if (reconciled) setResponse(reconciled);
    else void load(true);
    setNotice(message);
    setTimeout(() => staleTitleRef.current?.focus(), 0);
  }

  if (loading && !response) {
    return <ReviewSkeleton label="Building your weekly review from saved application and outreach history…" />;
  }
  if (!response) {
    return (
      <LoadFailure
        message={error ?? "Your weekly review is unavailable."}
        retry={() => void load(false)}
      />
    );
  }

  return (
    <div className="min-w-0 space-y-8">
      {error ? (
        <StatusMessage kind="error">
          {error} Your last complete as-of review remains below.
        </StatusMessage>
      ) : null}
      <section
        aria-labelledby="weekly-stale-title"
        aria-busy={refreshing}
        className="rounded-2xl border border-amber-200 bg-amber-50/70 p-5 shadow-sm sm:p-7 dark:border-amber-900 dark:bg-amber-950/20"
      >
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-amber-700 dark:text-amber-300">
              Decide first
            </p>
            <h2
              id="weekly-stale-title"
              ref={staleTitleRef}
              tabIndex={-1}
              className="mt-2 text-xl font-semibold tracking-tight outline-none"
            >
              Applications waiting on you
            </h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-700 dark:text-zinc-300">
              {response.stale_application_total} overdue {response.stale_application_total === 1 ? "application needs" : "applications need"} a decision. Choose a real next date; this review never treats silence as rejection.
            </p>
          </div>
          <button
            type="button"
            disabled={refreshing}
            onClick={() => void load(true)}
            className={secondaryButtonClasses}
          >
            {refreshing ? "Refreshing…" : "Refresh weekly review"}
          </button>
        </div>

        {notice ? (
          <div className="mt-5">
            <StatusMessage kind="success">{notice}</StatusMessage>
          </div>
        ) : null}
        <div className="mt-6">
          {response.stale_applications.length === 0 ? (
            <div className="rounded-xl border border-emerald-200 bg-white p-5 dark:border-emerald-900 dark:bg-zinc-900/70">
              <p className="font-semibold text-emerald-900 dark:text-emerald-100">
                No overdue application decisions
              </p>
              <p className="mt-1 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
                Every active application has a current date. Due and upcoming work remains in Today.
              </p>
              <Link href="/today" className={`${secondaryButtonClasses} mt-4`}>
                Open Today
              </Link>
            </div>
          ) : (
            <div className="space-y-4">
              {response.stale_applications.map((item) => (
                <StaleApplicationCard
                  key={`${item.application.id}:${item.current_action.id}`}
                  item={item}
                  ownerLocalDate={response.owner_local_date || ownerLocalDate}
                  onReviewed={actionReviewed}
                />
              ))}
              {response.stale_application_total > response.stale_applications.length ? (
                <StatusMessage kind="info">
                  Showing the first {response.stale_applications.length} of {response.stale_application_total} overdue applications in stable order. Continue in{" "}
                  <Link href="/applications" className="font-medium underline underline-offset-4">Applications</Link>{" "}
                  or use <Link href="/today" className="font-medium underline underline-offset-4">Today</Link> for the full action queue.
                </StatusMessage>
              ) : null}
            </div>
          )}
        </div>
      </section>

      <section
        aria-labelledby="weekly-funnel-title"
        aria-busy={refreshing}
        className="rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm sm:p-7 dark:border-zinc-800 dark:bg-zinc-900/70"
      >
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-indigo-600 dark:text-indigo-400">
              Learn honestly
            </p>
            <h2 id="weekly-funnel-title" className="mt-2 text-xl font-semibold tracking-tight">
              Job-search funnel
            </h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
              Rates use only old-enough, explicitly recorded applications. Open work, missing facts, and small samples stay visible instead of quietly becoming failures.
            </p>
          </div>
        </div>
        <div className="mt-6">
          <FunnelWorkspace response={response} />
        </div>
      </section>
    </div>
  );
}

function StaleApplicationCard({
  item,
  ownerLocalDate,
  onReviewed,
}: {
  item: StaleApplicationReviewItem;
  ownerLocalDate: string;
  onReviewed: (message: string, reconciled?: WeeklyReviewResponse) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [decision, setDecision] = useState<WeeklyReviewDecision>("continue");
  const [newDueOn, setNewDueOn] = useState(addDays(ownerLocalDate, 2));
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [hasPending, setHasPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<PendingActionReview | null>(null);
  const triggerId = `weekly-review-trigger-${item.application.id}-${item.current_action.id}`;
  const interviewOwned = item.current_action.interview_round_id !== null;

  function closeEditor() {
    if (busy || hasPending) return;
    setEditing(false);
    setError(null);
    setTimeout(() => document.getElementById(triggerId)?.focus(), 0);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!confirmed || !newDueOn) return;
    await runReview({
      decision,
      new_due_on: newDueOn,
      confirm_current_action: true,
    });
  }

  async function runReview(payload: ApplicationActionReviewCreate) {
    if (busy) return;
    const fingerprint = JSON.stringify(payload);
    const existing = pending.current;
    if (existing && existing.fingerprint !== fingerprint) {
      setError("Retry the unchanged pending decision or check its saved state before changing inputs.");
      return;
    }
    const request = existing ?? {
      key: createIdempotencyKey(`weekly-review:${item.application.id}:${item.current_action.id}`),
      fingerprint,
      payload,
      applicationVersion: item.application.version,
      actionId: item.current_action.id,
      actionVersion: item.current_action.version,
    };
    pending.current = request;
    setHasPending(true);
    setBusy(true);
    setError(null);
    try {
      await reviewApplicationAction(
        item.application.id,
        request.actionId,
        request.applicationVersion,
        request.key,
        request.payload,
      );
      finishReview(decision === "waiting"
        ? "Waiting date saved. This application remains active, not rejected."
        : "Next action date saved. The application is back in your active plan.");
    } catch (reason) {
      const latest = await safelyLoadWeeklyReview();
      const current = latest?.stale_applications.find(
        (row) => row.application.id === item.application.id,
      );
      const apiError = reason instanceof WorkspaceApiError ? reason : null;
      const ambiguous = !apiError || apiError.retryable || apiError.code === "mutation_pending";
      if (latest && !current) {
        finishReview("The exact next-action review was confirmed from the durable record.", latest);
      } else if (current && (
        current.application.version !== request.applicationVersion ||
        current.current_action.version !== request.actionVersion
      )) {
        pending.current = null;
        setHasPending(false);
        setError("This application changed in a different way. Refresh the weekly review before choosing another date.");
      } else if (!ambiguous) {
        pending.current = null;
        setHasPending(false);
        setError(errorText(reason, "The application review was rejected."));
      } else {
        setError(
          `${errorText(reason, "The review result is not yet confirmed.")} ` +
          "Your exact decision and safe receipt are retained; check saved state or retry unchanged.",
        );
      }
    } finally {
      setBusy(false);
    }
  }

  function finishReview(message: string, latest?: WeeklyReviewResponse) {
    pending.current = null;
    setHasPending(false);
    setEditing(false);
    onReviewed(message, latest);
  }

  async function checkSavedState() {
    const request = pending.current;
    if (!request || busy) return;
    setBusy(true);
    setError(null);
    try {
      const latest = await safelyLoadWeeklyReview();
      const current = latest?.stale_applications.find(
        (row) => row.application.id === item.application.id,
      );
      if (latest && !current) {
        finishReview("The exact next-action review was confirmed from the durable record.", latest);
      } else if (current && (
        current.application.version !== request.applicationVersion ||
        current.current_action.version !== request.actionVersion
      )) {
        pending.current = null;
        setHasPending(false);
        setError("A different application update is already saved. Refresh before continuing.");
      } else {
        setError("This exact decision is not visible yet. Retry it unchanged with the original safe receipt.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <article
      aria-labelledby={`weekly-application-${item.application.id}`}
      className="rounded-xl border border-amber-200 bg-white p-5 dark:border-amber-900 dark:bg-zinc-900/80"
    >
      <div className="flex min-w-0 flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="rounded-full bg-amber-100 px-2.5 py-1 font-semibold text-amber-900 dark:bg-amber-950 dark:text-amber-100">
              {item.days_overdue} {item.days_overdue === 1 ? "day" : "days"} overdue
            </span>
            <span className="text-zinc-500">{item.posting.company}</span>
          </div>
          <h3
            id={`weekly-application-${item.application.id}`}
            className="mt-3 break-words text-lg font-semibold"
          >
            {item.posting.title}
          </h3>
          <p className="mt-2 text-sm font-medium">{item.current_action.title}</p>
          <p className="mt-1 text-xs text-zinc-500">
            Was due {formatDateOnly(item.current_action.due_on)} · {stageLabel(item.application.stage)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link
            href={`/applications/${encodeURIComponent(item.application.id)}`}
            className={secondaryButtonClasses}
          >
            Open dossier
          </Link>
          {interviewOwned ? (
            <Link
              href={`/applications/${encodeURIComponent(item.application.id)}#interview-rounds`}
              className={primaryButtonClasses}
            >
              Manage interview
            </Link>
          ) : !editing ? (
            <button
              id={triggerId}
              type="button"
              onClick={() => {
                setEditing(true);
                setError(null);
              }}
              className={primaryButtonClasses}
            >
              Choose next step
            </button>
          ) : null}
        </div>
      </div>

      {interviewOwned ? (
        <div className="mt-4">
          <StatusMessage kind="info">
            This action belongs to a scheduled interview round. Reschedule, complete, or cancel that exact round in Interview rounds; the weekly review cannot replace its preparation date.
          </StatusMessage>
        </div>
      ) : null}

      {editing && !interviewOwned ? (
        <form onSubmit={submit} className="mt-5 border-t border-zinc-200 pt-5 dark:border-zinc-800">
          <fieldset disabled={busy || hasPending}>
            <legend className="text-sm font-semibold">What is true now?</legend>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <DecisionOption
                groupName={`review-decision-${item.application.id}-${item.current_action.id}`}
                value="continue"
                checked={decision === "continue"}
                title="I will continue"
                description="Set the next concrete action date."
                onChange={setDecision}
              />
              <DecisionOption
                groupName={`review-decision-${item.application.id}-${item.current_action.id}`}
                value="waiting"
                checked={decision === "waiting"}
                title="I am waiting"
                description="Set when you will check again; this is not a rejection."
                onChange={setDecision}
              />
            </div>

            <label className="mt-4 block max-w-sm text-sm font-medium">
              {decision === "waiting" ? "Check again on" : "Next action due"}
              <input
                type="date"
                value={newDueOn}
                min={ownerLocalDate}
                required
                onChange={(event) => setNewDueOn(event.target.value)}
                className={`${inputClasses} mt-2`}
              />
              <span className="mt-1 block text-xs font-normal text-zinc-500">
                Dates use your workspace timezone.
              </span>
            </label>

            <label className="mt-4 flex max-w-2xl items-start gap-3 rounded-lg border border-zinc-200 bg-zinc-50 p-3 text-sm dark:border-zinc-800 dark:bg-zinc-950/60">
              <input
                type="checkbox"
                checked={confirmed}
                required
                onChange={(event) => setConfirmed(event.target.checked)}
                className="mt-0.5 h-4 w-4 rounded border-zinc-300"
              />
              <span>
                I am reviewing the current action shown above and want to replace its overdue date.
              </span>
            </label>
          </fieldset>

          {error ? <div className="mt-4"><StatusMessage kind="error">{error}</StatusMessage></div> : null}

          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="submit"
              disabled={busy || hasPending || !confirmed || !newDueOn}
              className={primaryButtonClasses}
            >
              {busy ? "Saving decision…" : decision === "waiting" ? "Save waiting date" : "Save next action"}
            </button>
            {!hasPending ? (
              <button type="button" onClick={closeEditor} className={secondaryButtonClasses}>
                Cancel
              </button>
            ) : null}
          </div>

          {hasPending && !busy ? (
            <div className="mt-3 flex flex-wrap gap-2">
              <button type="button" onClick={() => void checkSavedState()} className={secondaryButtonClasses}>
                Check saved state
              </button>
              <button
                type="button"
                onClick={() => pending.current && void runReview(pending.current.payload)}
                className={secondaryButtonClasses}
              >
                Retry unchanged
              </button>
            </div>
          ) : null}
        </form>
      ) : null}
    </article>
  );
}

function DecisionOption({
  groupName,
  value,
  checked,
  title,
  description,
  onChange,
}: {
  groupName: string;
  value: WeeklyReviewDecision;
  checked: boolean;
  title: string;
  description: string;
  onChange: (value: WeeklyReviewDecision) => void;
}) {
  return (
    <label className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm ${
      checked
        ? "border-indigo-400 bg-indigo-50 dark:border-indigo-700 dark:bg-indigo-950/30"
        : "border-zinc-200 dark:border-zinc-800"
    }`}>
      <input
        type="radio"
        name={groupName}
        value={value}
        checked={checked}
        onChange={() => onChange(value)}
        className="mt-0.5 h-4 w-4"
      />
      <span>
        <span className="block font-medium">{title}</span>
        <span className="mt-1 block text-xs leading-5 text-zinc-500">{description}</span>
      </span>
    </label>
  );
}

function FunnelWorkspace({ response }: { response: WeeklyReviewResponse }) {
  return (
    <div className="space-y-8">
      <div className="rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm text-blue-950 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-100">
        <p className="font-semibold">How to read this review</p>
        <ul className="mt-2 grid gap-2 leading-6 sm:grid-cols-2">
          <li><strong>Mature:</strong> submitted at least {response.policy.application_maturity_days} days ago.</li>
          <li><strong>Immature:</strong> too recent to judge fairly.</li>
          <li><strong>Censored open:</strong> still active with no recorded conversion yet.</li>
          <li><strong>Missing:</strong> the required snapshot or outcome was not recorded.</li>
          <li><strong>Converted later:</strong> happened after the fixed {response.policy.application_maturity_days}-day evaluation cutoff and is excluded from its rate.</li>
        </ul>
        <p className="mt-3 text-xs text-blue-800 dark:text-blue-200">
          {response.policy.observation_window_days}-day window {formatDateOnly(response.window.starts_on)}–{formatDateOnly(response.window.ends_on)} · {response.owner_timezone} · calculated {formatDateTime(response.as_of, response.owner_timezone)} · policy {response.policy.version}
        </p>
      </div>

      <section aria-labelledby="overall-funnel-title">
        <h3 id="overall-funnel-title" className="text-lg font-semibold">Submitted application funnel</h3>
        <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
          Every rate below shows its converted numerator and evaluable sample size.
        </p>
        <div className="mt-4 grid gap-4 md:grid-cols-3">
          {orderedStages(response.funnel.overall).map((metric) => (
            <FunnelMetricCard key={metric.stage} metric={metric} />
          ))}
        </div>
        {response.funnel.attribution_missing > 0 || response.funnel.assessment_missing > 0 ? (
          <div className="mt-4">
            <StatusMessage kind="info">
              Segmentation is incomplete: {response.funnel.attribution_missing} submitted application{response.funnel.attribution_missing === 1 ? " is" : "s are"} missing a saved source or career-track snapshot, and {response.funnel.assessment_missing} {response.funnel.assessment_missing === 1 ? "is" : "are"} missing an assessment snapshot. They remain in the overall funnel.
            </StatusMessage>
          </div>
        ) : null}
      </section>

      <section aria-labelledby="segments-title" className="space-y-5">
        <div>
          <h3 id="segments-title" className="text-lg font-semibold">Where results are coming from</h3>
          <p className="mt-1 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
            Only immutable snapshots captured with the application are segmented. Unknown values remain in the missing count.
          </p>
        </div>
        <SegmentTable title="Acquisition source" rows={response.funnel.by_acquisition_source} />
        <SegmentTable title="Career track" rows={response.funnel.by_career_track} />
        <SegmentTable title="Assessment band" rows={response.funnel.by_assessment_band} />
      </section>

      <section aria-labelledby="outreach-observations-title" className="space-y-5">
        <div>
          <h3 id="outreach-observations-title" className="text-lg font-semibold">Outreach observations</h3>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
            {response.outreach.noncausal_label} These are manually recorded associations, not proof that a contact caused a reply, referral, or interview.
          </p>
          <p className="mt-2 text-xs leading-5 text-zinc-500">
            Outreach becomes mature after {response.policy.outreach_maturity_days} days. {response.outreach.unattributed_legacy_successes} older successful {response.outreach.unattributed_legacy_successes === 1 ? "outcome is" : "outcomes are"} excluded because no exact sent attempt was recorded.
          </p>
        </div>
        <OutreachTable title="By contact category" rows={response.outreach.by_contact_category} />
        <OutreachTable title="By bench position" rows={response.outreach.by_sequence_position} />
        <RescueTable rows={response.outreach.contacts_two_through_five} />
      </section>
    </div>
  );
}

function FunnelMetricCard({ metric }: { metric: FunnelStageMetric }) {
  return (
    <article className="rounded-xl border border-zinc-200 bg-zinc-50 p-5 dark:border-zinc-800 dark:bg-zinc-950/60">
      <p className="text-xs font-semibold uppercase tracking-[0.12em] text-zinc-500">
        {stageMetricLabel(metric.stage)}
      </p>
      <p className="mt-3 text-3xl font-semibold">{formatRate(metric.rate)}</p>
      <p className="mt-1 text-sm font-medium">
        {metric.converted} converted / {metric.evaluable} evaluable
      </p>
      {metric.rate === null ? (
        <p className="mt-2 text-xs leading-5 text-amber-700 dark:text-amber-300">
          No evaluable mature sample yet; no rate is claimed.
        </p>
      ) : metric.evaluable < 5 ? (
        <p className="mt-2 text-xs leading-5 text-amber-700 dark:text-amber-300">
          Very small sample. Treat this as directional, not a reliable trend.
        </p>
      ) : null}
      <dl className="mt-4 grid grid-cols-2 gap-x-3 gap-y-2 border-t border-zinc-200 pt-4 text-xs dark:border-zinc-800">
        <Count label="Cohort" value={metric.cohort_total} />
        <Count label="Mature" value={metric.mature} />
        <Count label="Immature" value={metric.immature} />
        <Count label="Open/censored" value={metric.censored_open} />
        <Count label="Converted later" value={metric.late_converted} />
        <Count label="Missing" value={metric.missing} />
      </dl>
    </article>
  );
}

function Count({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-zinc-500">{label}</dt>
      <dd className="mt-0.5 font-semibold text-zinc-900 dark:text-zinc-100">{value}</dd>
    </div>
  );
}

function SegmentTable({ title, rows }: { title: string; rows: FunnelSegmentMetric[] }) {
  return (
    <div className="overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-800">
      <div className="border-b border-zinc-200 bg-zinc-50 px-4 py-3 dark:border-zinc-800 dark:bg-zinc-950/60">
        <h4 className="font-semibold">{title}</h4>
      </div>
      {rows.length === 0 ? (
        <p className="px-4 py-5 text-sm text-zinc-500">No captured {title.toLowerCase()} snapshots in this window.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[700px] border-collapse text-left text-sm">
            <caption className="sr-only">Mature funnel conversion by {title.toLowerCase()}</caption>
            <thead className="text-xs uppercase tracking-wide text-zinc-500">
              <tr>
                <th scope="col" className="px-4 py-3">{title}</th>
                <th scope="col" className="px-4 py-3">Captured</th>
                <th scope="col" className="px-4 py-3">Screen</th>
                <th scope="col" className="px-4 py-3">Interview</th>
                <th scope="col" className="px-4 py-3">Offer</th>
                <th scope="col" className="px-4 py-3">Missing</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
              {rows.map((row) => (
                <tr key={row.key}>
                  <th scope="row" className="px-4 py-3 font-medium">{row.label}</th>
                  <td className="px-4 py-3 tabular-nums">{row.cohort_total}</td>
                  {(["screen", "interview", "offer"] as const).map((stage) => (
                    <td key={stage} className="px-4 py-3">
                      <CompactMetric metric={row.stages.find((metric) => metric.stage === stage)} />
                    </td>
                  ))}
                  <td className="px-4 py-3 tabular-nums">{row.missing}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function CompactMetric({ metric }: { metric?: FunnelStageMetric }) {
  if (!metric) return <span className="text-zinc-500">Not captured</span>;
  return (
    <span className="whitespace-nowrap">
      <span className="font-semibold">{formatRate(metric.rate)}</span>{" "}
      <span className="text-xs text-zinc-500">({metric.converted}/{metric.evaluable})</span>
      {metric.late_converted > 0 ? (
        <span className="block text-xs text-zinc-500">+{metric.late_converted} after cutoff</span>
      ) : null}
    </span>
  );
}

function OutreachTable({ title, rows }: { title: string; rows: OutreachObservedMetric[] }) {
  return (
    <div className="overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-800">
      <div className="border-b border-zinc-200 bg-zinc-50 px-4 py-3 dark:border-zinc-800 dark:bg-zinc-950/60">
        <h4 className="font-semibold">{title}</h4>
      </div>
      {rows.length === 0 ? (
        <p className="px-4 py-5 text-sm text-zinc-500">No mature manually recorded outreach in this window.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] border-collapse text-left text-sm">
            <caption className="sr-only">Observed outreach results {title.toLowerCase()}</caption>
            <thead className="text-xs uppercase tracking-wide text-zinc-500">
              <tr>
                <th scope="col" className="px-4 py-3">Group</th>
                <th scope="col" className="px-4 py-3">Observed result</th>
                <th scope="col" className="px-4 py-3">Reached</th>
                <th scope="col" className="px-4 py-3">Mature/evaluable</th>
                <th scope="col" className="px-4 py-3">Censored/immature</th>
                <th scope="col" className="px-4 py-3">Ambiguous excluded</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
              {rows.map((row) => (
                <tr key={row.key}>
                  <th scope="row" className="px-4 py-3 font-medium">{row.label}</th>
                  <td className="px-4 py-3">
                    <span className="font-semibold">{formatRate(row.observed_rate)}</span>{" "}
                    <span className="text-xs text-zinc-500">({row.successes}/{row.evaluable})</span>
                  </td>
                  <td className="px-4 py-3 tabular-nums">{row.reached}</td>
                  <td className="px-4 py-3 tabular-nums">{row.mature}/{row.evaluable}</td>
                  <td className="px-4 py-3 tabular-nums">{row.censored_open}/{row.immature}</td>
                  <td className="px-4 py-3 tabular-nums">{row.ambiguity_excluded}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function RescueTable({ rows }: { rows: ContactRescueMetric[] }) {
  const byPosition = new Map(rows.map((row) => [row.position, row]));
  return (
    <div className="overflow-hidden rounded-xl border border-indigo-200 dark:border-indigo-900">
      <div className="border-b border-indigo-200 bg-indigo-50 px-4 py-4 dark:border-indigo-900 dark:bg-indigo-950/30">
        <h4 className="font-semibold">Did contacts two through five rescue the search?</h4>
        <p className="mt-1 max-w-3xl text-xs leading-5 text-indigo-900 dark:text-indigo-200">
          Observed rescue rate means success among applications still unsuccessful after every earlier contacted person. It is not causal uplift and does not compare equivalent randomized groups.
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] border-collapse text-left text-sm">
          <caption className="sr-only">Observed non-causal rescue rates for contacts two through five</caption>
          <thead className="text-xs uppercase tracking-wide text-zinc-500">
            <tr>
              <th scope="col" className="px-4 py-3">Contact</th>
              <th scope="col" className="px-4 py-3">Observed rescue</th>
              <th scope="col" className="px-4 py-3">Reached</th>
              <th scope="col" className="px-4 py-3">Mature/evaluable</th>
              <th scope="col" className="px-4 py-3">Censored/immature</th>
              <th scope="col" className="px-4 py-3">Ambiguous excluded</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
            {([2, 3, 4, 5] as const).map((position) => {
              const row = byPosition.get(position);
              return (
                <tr key={position}>
                  <th scope="row" className="px-4 py-3 font-medium">Person {position}</th>
                  {row ? (
                    <>
                      <td className="px-4 py-3">
                        <span className="font-semibold">{formatRate(row.observed_rate)}</span>{" "}
                        <span className="text-xs text-zinc-500">({row.successes}/{row.evaluable})</span>
                      </td>
                      <td className="px-4 py-3 tabular-nums">{row.reached}</td>
                      <td className="px-4 py-3 tabular-nums">{row.mature}/{row.evaluable}</td>
                      <td className="px-4 py-3 tabular-nums">{row.censored_open}/{row.immature}</td>
                      <td className="px-4 py-3 tabular-nums">{row.ambiguity_excluded}</td>
                    </>
                  ) : (
                    <td colSpan={5} className="px-4 py-3 text-zinc-500">
                      No eligible mature observations yet.
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function LoadFailure({ message, retry }: { message: string; retry: () => void }) {
  return (
    <StatusMessage kind="error">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span>{message}</span>
        <button type="button" onClick={retry} className={secondaryButtonClasses}>Try again</button>
      </div>
    </StatusMessage>
  );
}

function ReviewSkeleton({ label }: { label: string }) {
  return (
    <div role="status" className="space-y-3" aria-label={label}>
      <p className="text-sm text-zinc-500">{label}</p>
      <div className="h-24 animate-pulse rounded-xl bg-zinc-200 dark:bg-zinc-800" />
      <div className="h-24 animate-pulse rounded-xl bg-zinc-200 dark:bg-zinc-800" />
    </div>
  );
}

async function safelyLoadWeeklyReview(): Promise<WeeklyReviewResponse | null> {
  try {
    return await getWeeklyReview();
  } catch {
    return null;
  }
}

function orderedStages(metrics: FunnelStageMetric[]): FunnelStageMetric[] {
  const order: Record<FunnelStage, number> = { screen: 0, interview: 1, offer: 2 };
  return [...metrics].sort((left, right) => order[left.stage] - order[right.stage]);
}

function formatRate(value: number | null): string {
  if (value === null) return "Not enough data";
  return new Intl.NumberFormat(undefined, {
    style: "percent",
    maximumFractionDigits: 1,
  }).format(value);
}

function stageMetricLabel(stage: FunnelStage): string {
  return ({ screen: "Recruiter screen", interview: "Interview", offer: "Offer" } as const)[stage];
}

function stageLabel(stage: string): string {
  return stage.replaceAll("_", " ").replace(/^./, (value) => value.toUpperCase());
}

function formatDateOnly(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(year, month - 1, day, 12);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}

function formatDateTime(value: string, timeZone: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone,
    }).format(date);
  } catch {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(date);
  }
}

function addDays(value: string, count: number): string {
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day, 12));
  date.setUTCDate(date.getUTCDate() + count);
  return date.toISOString().slice(0, 10);
}
