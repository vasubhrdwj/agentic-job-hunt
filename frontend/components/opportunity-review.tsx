"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { decideOpportunity, getOpportunity } from "@/lib/opportunity-api";
import type {
  OpportunityDecisionPayload,
  OpportunityDetail,
} from "@/lib/opportunity-types";
import { createIdempotencyKey } from "@/lib/workspace-api";
import { DecisionUndo, OpportunityActions } from "./opportunity-actions";
import { MatchEvidence, OpportunityFactGrid } from "./opportunity-card";
import {
  errorText,
  formatDate,
  secondaryButtonClasses,
  StatusMessage,
} from "./workspace-ui";

interface UndoState {
  version: number;
  eventId: string;
  expiresAt: number;
  label: string;
}

export function OpportunityReview({
  opportunityId,
  ownerLocalDate,
  ownerTimezone,
}: {
  opportunityId: string;
  ownerLocalDate: string;
  ownerTimezone: string;
}) {
  const router = useRouter();
  const [opportunity, setOpportunity] = useState<OpportunityDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [undo, setUndo] = useState<UndoState | null>(null);
  const [undoPending, setUndoPending] = useState(false);
  const decisionKeys = useRef<Record<string, string>>({});

  const load = useCallback(async () => {
    setError(null);
    try {
      setOpportunity(await getOpportunity(opportunityId));
    } catch (reason) {
      setError(errorText(reason, "Unable to load this opportunity."));
    } finally {
      setLoading(false);
    }
  }, [opportunityId]);

  useEffect(() => {
    const timer = setTimeout(() => void load(), 0);
    return () => clearTimeout(timer);
  }, [load]);

  async function applyDecision(
    payload: OpportunityDecisionPayload,
  ): Promise<{ ok: boolean; error?: string }> {
    if (!opportunity) {
      return { ok: false, error: "The saved opportunity is unavailable." };
    }
    setPending(true);
    setError(null);
    const receiptKey = [
      opportunity.id,
      payload.action,
      payload.restore_decision_event_id ?? "new",
      payload.initial_action_due_on ?? "default",
      payload.acquisition_source ?? "no-source",
      payload.selected_saved_search_id ?? "no-search",
    ].join(":");
    decisionKeys.current[receiptKey] ??= createIdempotencyKey(`opportunity:${receiptKey}`);
    try {
      const response = await decideOpportunity(
        opportunity.id,
        opportunity.version,
        payload,
        decisionKeys.current[receiptKey],
      );
      delete decisionKeys.current[receiptKey];
      setOpportunity((current) => current ? {
        ...current,
        version: response.opportunity_version,
        state: response.state,
        latest_decision: response.event,
        decision_history: [...current.decision_history, response.event],
      } : current);
      if (payload.action === "pursue") {
        setUndo(null);
        const applicationId = response.pursuit?.application.id;
        router.push(applicationId
          ? `/applications/${encodeURIComponent(applicationId)}`
          : "/applications");
      } else if (payload.action !== "restore_to_inbox") {
        setUndo({
          version: response.opportunity_version,
          eventId: response.event.id,
          expiresAt: Date.parse(response.event.created_at) + 30_000,
          label: payload.action === "watch" ? "Role moved to Watching." : "Role dismissed.",
        });
      } else {
        setUndo(null);
      }
      return { ok: true };
    } catch (reason) {
      const actionError = errorText(reason, "Unable to save this decision.");
      await load();
      setError(actionError);
      return { ok: false, error: actionError };
    } finally {
      setPending(false);
    }
  }

  async function undoDecision() {
    if (!opportunity || !undo) return;
    setUndoPending(true);
    setError(null);
    try {
      const receiptKey = `${opportunity.id}:undo:${undo.eventId}`;
      decisionKeys.current[receiptKey] ??= createIdempotencyKey(`opportunity:${receiptKey}`);
      await decideOpportunity(
        opportunity.id,
        undo.version,
        { action: "restore_to_inbox", restore_decision_event_id: undo.eventId },
        decisionKeys.current[receiptKey],
      );
      delete decisionKeys.current[receiptKey];
      setUndo(null);
      await load();
    } catch (reason) {
      setError(errorText(reason, "Unable to restore this role."));
    } finally {
      setUndoPending(false);
    }
  }

  if (loading) return <p role="status" className="text-sm text-zinc-500">Loading persisted job review…</p>;
  if (!opportunity) {
    return (
      <StatusMessage kind="error">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span>{error ?? "This opportunity is unavailable."}</span>
          <Link href="/today" className={secondaryButtonClasses}>Back to Today</Link>
        </div>
      </StatusMessage>
    );
  }

  const posting = opportunity.posting;
  return (
    <div className="min-w-0 space-y-6">
      <Link href="/today" className="inline-flex min-h-10 items-center text-sm font-medium text-zinc-600 hover:text-zinc-950 dark:text-zinc-400 dark:hover:text-white">
        ← Back to Today
      </Link>
      {error ? <StatusMessage kind="error">{error}</StatusMessage> : null}

      <article aria-labelledby="job-review-title" className="min-w-0 rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm sm:p-7 dark:border-zinc-800 dark:bg-zinc-900/70">
        <div className="flex min-w-0 flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <p className="text-xs font-medium uppercase tracking-[0.12em] text-zinc-500">
              {posting.company} · {opportunity.lane.replaceAll("_", " ")}
            </p>
            <h2 id="job-review-title" className="mt-2 break-words text-2xl font-semibold tracking-tight">
              {posting.title}
            </h2>
            <p className="mt-2 text-sm text-zinc-500">
              First seen {formatDate(posting.first_seen_at)} · last confirmed {formatDate(posting.last_confirmed_at)}
            </p>
          </div>
          <a
            href={posting.canonical_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex min-h-11 shrink-0 items-center justify-center rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
          >
            {posting.first_party ? "Open first-party posting ↗" : "Open source posting ↗"}
          </a>
        </div>
        {!posting.first_party ? (
          <p className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
            The destination has not been verified as first-party. Review it before entering personal information.
          </p>
        ) : null}
        <p className="mt-5 break-words text-sm leading-6 text-zinc-700 dark:text-zinc-300">{posting.summary}</p>
        <OpportunityFactGrid facts={opportunity.facts} />
        <MatchEvidence opportunity={opportunity} />
        <div className="mt-6 border-t border-zinc-200 pt-5 dark:border-zinc-800">
          <OpportunityActions
            opportunity={opportunity}
            pending={pending}
            ownerLocalDate={ownerLocalDate}
            ownerTimezone={ownerTimezone}
            onDecision={applyDecision}
          />
        </div>
      </article>

      {opportunity.unknowns.length > 0 ? (
        <section className="rounded-2xl border border-amber-200 bg-amber-50 p-5 dark:border-amber-900 dark:bg-amber-950/20">
          <h2 className="font-semibold text-amber-950 dark:text-amber-100">Unknowns to verify</h2>
          <dl className="mt-3 space-y-3 text-sm">
            {opportunity.unknowns.map((unknown) => (
              <div key={unknown.field}>
                <dt className="font-medium capitalize text-amber-950 dark:text-amber-100">{unknown.field.replaceAll("_", " ")}</dt>
                <dd className="mt-1 text-amber-900 dark:text-amber-200">{unknown.message}</dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}

      <section className="rounded-2xl border border-zinc-200 bg-white p-5 sm:p-7 dark:border-zinc-800 dark:bg-zinc-900/70">
        <h2 className="text-lg font-semibold">Full job description</h2>
        {opportunity.description ? (
          <div className="mt-4 whitespace-pre-wrap break-words text-sm leading-7 text-zinc-700 dark:text-zinc-300">
            {opportunity.description}
          </div>
        ) : (
          <p className="mt-3 text-sm text-zinc-500">The source did not preserve a full description for this version.</p>
        )}
      </section>

      <div className="grid min-w-0 gap-6 lg:grid-cols-2">
        <section className="min-w-0 rounded-2xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900/70">
          <h2 className="font-semibold">Found by saved searches</h2>
          <ul className="mt-3 space-y-3 text-sm">
            {opportunity.discovered_by.map((source) => (
              <li key={source.saved_search_id} className="rounded-lg bg-zinc-50 p-3 dark:bg-zinc-950/60">
                <p className="break-words font-medium">{source.saved_search_name}</p>
                <p className="mt-1 text-xs text-zinc-500">First matched {formatDate(source.first_matched_at)}</p>
              </li>
            ))}
          </ul>
        </section>
        <section className="min-w-0 rounded-2xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900/70">
          <h2 className="font-semibold">Posting history</h2>
          <ol className="mt-3 space-y-3 text-sm">
            {opportunity.posting_versions.map((version) => (
              <li key={version.version} className="rounded-lg bg-zinc-50 p-3 dark:bg-zinc-950/60">
                <p className="font-medium capitalize">Version {version.version} · {version.change_kind}</p>
                <p className="mt-1 text-xs text-zinc-500">Observed {formatDate(version.observed_at)}</p>
                {version.changed_fields.length > 0 ? <p className="mt-1 break-words text-xs">Changed: {version.changed_fields.join(", ")}</p> : null}
              </li>
            ))}
          </ol>
        </section>
      </div>

      {opportunity.apply_urls.length > 1 ? (
        <section className="rounded-2xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900/70">
          <h2 className="font-semibold">Known source links</h2>
          <ul className="mt-3 space-y-2 text-sm">
            {opportunity.apply_urls.map((url) => (
              <li key={url} className="min-w-0">
                <a href={url} target="_blank" rel="noopener noreferrer" className="block min-h-10 break-all text-indigo-700 underline underline-offset-4 dark:text-indigo-300">
                  {url} ↗
                </a>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {undo ? (
        <DecisionUndo
          label={undo.label}
          expiresAt={undo.expiresAt}
          pending={undoPending}
          onUndo={undoDecision}
          onExpire={() => setUndo(null)}
        />
      ) : null}
    </div>
  );
}
