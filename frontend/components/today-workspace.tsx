"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  decideOpportunity,
  getOpportunityScan,
  getToday,
} from "@/lib/opportunity-api";
import type {
  OpportunityDecisionPayload,
  OpportunityDecisionResponse,
  OpportunityLane,
  ScanStatusResponse,
  TodayOpportunityItem,
  TodayResponse,
  TodayView,
} from "@/lib/opportunity-types";
import { createIdempotencyKey, listSavedSearches } from "@/lib/workspace-api";
import type { SavedSearch } from "@/lib/workspace-types";
import { DecisionUndo } from "./opportunity-actions";
import { OpportunityCard } from "./opportunity-card";
import {
  errorText,
  formatDate,
  inputClasses,
  secondaryButtonClasses,
  StatusMessage,
} from "./workspace-ui";

const TERMINAL_SCAN_STATES = new Set(["succeeded", "partial", "failed", "cancelled"]);

interface UndoState {
  opportunityId: string;
  opportunityVersion: number;
  eventId: string;
  label: string;
  expiresAt: number;
}

export function TodayWorkspace({
  ownerLocalDate,
  ownerTimezone,
}: {
  ownerLocalDate: string;
  ownerTimezone: string;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const view = validView(searchParams.get("view"));
  const savedSearchId = searchParams.get("search") || undefined;
  const lane = validLane(searchParams.get("lane"));
  const scanId = searchParams.get("scan") || undefined;

  const [today, setToday] = useState<TodayResponse | null>(null);
  const [savedSearches, setSavedSearches] = useState<SavedSearch[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filterNonce, setFilterNonce] = useState(0);
  const [scan, setScan] = useState<ScanStatusResponse | null>(null);
  const [scanError, setScanError] = useState<string | null>(null);
  const [pendingOpportunityId, setPendingOpportunityId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [undo, setUndo] = useState<UndoState | null>(null);
  const [undoPending, setUndoPending] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const decisionKeys = useRef<Record<string, string>>({});

  const query = useMemo(
    () => ({ view, savedSearchId, lane, limit: 20 }),
    [view, savedSearchId, lane],
  );

  const loadToday = useCallback(async (preserve: boolean) => {
    if (preserve) setRefreshing(true);
    else setLoading(true);
    setLoadError(null);
    try {
      setToday(await getToday(query));
    } catch (reason) {
      setLoadError(errorText(reason, "Unable to load your Today inbox."));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [query]);

  useEffect(() => {
    const timer = setTimeout(() => void loadToday(true), 0);
    return () => clearTimeout(timer);
  }, [loadToday, filterNonce]);

  useEffect(() => {
    let active = true;
    listSavedSearches()
      .then((items) => {
        if (active) setSavedSearches(items);
      })
      .catch(() => {
        // The Today projection remains usable; do not pretend the owner has no searches.
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!scanId) {
      const timer = setTimeout(() => {
        setScan(null);
        setScanError(null);
      }, 0);
      return () => clearTimeout(timer);
    }
    const activeScanId = scanId;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const next = await getOpportunityScan(activeScanId);
        if (!active) return;
        setScan(next);
        setScanError(null);
        if (TERMINAL_SCAN_STATES.has(next.status)) {
          setFilterNonce((value) => value + 1);
        } else {
          timer = setTimeout(() => void poll(), 2_000);
        }
      } catch (reason) {
        if (!active) return;
        setScanError(errorText(reason, "Unable to refresh scan progress."));
        timer = setTimeout(() => void poll(), 5_000);
      }
    }
    void poll();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [scanId]);

  function updateFilter(name: "view" | "search" | "lane", value: string) {
    const params = new URLSearchParams(searchParams.toString());
    if (value) params.set(name, value);
    else params.delete(name);
    params.delete("cursor");
    router.replace(params.size ? `/today?${params.toString()}` : "/today", { scroll: false });
  }

  async function applyDecision(
    opportunity: TodayOpportunityItem,
    payload: OpportunityDecisionPayload,
  ): Promise<{ ok: boolean; error?: string }> {
    setPendingOpportunityId(opportunity.id);
    setActionError(null);
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
      applyDecisionLocally(response);
      if (payload.action === "pursue") {
        setUndo(null);
        setFilterNonce((value) => value + 1);
        const applicationId = response.pursuit?.application.id;
        router.push(applicationId
          ? `/applications/${encodeURIComponent(applicationId)}`
          : "/applications");
      } else if (payload.action !== "restore_to_inbox") {
        setUndo({
          opportunityId: opportunity.id,
          opportunityVersion: response.opportunity_version,
          eventId: response.event.id,
          label: payload.action === "watch" ? "Role moved to Watching." : "Role dismissed.",
          expiresAt: Date.parse(response.event.created_at) + 30_000,
        });
      } else {
        setUndo(null);
      }
      if (payload.action !== "pursue") setFilterNonce((value) => value + 1);
      return { ok: true };
    } catch (reason) {
      const message = errorText(reason, "Unable to save this decision.");
      setActionError(message);
      setFilterNonce((value) => value + 1);
      return { ok: false, error: message };
    } finally {
      setPendingOpportunityId(null);
    }
  }

  function applyDecisionLocally(response: OpportunityDecisionResponse) {
    setToday((current) => {
      if (!current) return current;
      const items = current.items
        .map((item) => item.id === response.opportunity_id
          ? {
              ...item,
              version: response.opportunity_version,
              state: response.state,
              latest_decision: response.event,
            }
          : item)
        .filter((item) => belongsInView(item.state, view));
      return { ...current, items };
    });
  }

  async function undoDecision() {
    if (!undo) return;
    setUndoPending(true);
    setActionError(null);
    try {
      const receiptKey = `${undo.opportunityId}:undo:${undo.eventId}`;
      decisionKeys.current[receiptKey] ??= createIdempotencyKey(`opportunity:${receiptKey}`);
      await decideOpportunity(
        undo.opportunityId,
        undo.opportunityVersion,
        {
          action: "restore_to_inbox",
          restore_decision_event_id: undo.eventId,
        },
        decisionKeys.current[receiptKey],
      );
      delete decisionKeys.current[receiptKey];
      setUndo(null);
      setFilterNonce((value) => value + 1);
    } catch (reason) {
      setActionError(errorText(reason, "Unable to restore this role."));
    } finally {
      setUndoPending(false);
    }
  }

  async function loadMore() {
    if (!today?.next_cursor) return;
    setLoadingMore(true);
    setLoadError(null);
    try {
      const next = await getToday({ ...query, cursor: today.next_cursor });
      setToday((current) => current ? {
        ...next,
        items: [...current.items, ...next.items],
      } : next);
    } catch (reason) {
      setLoadError(errorText(reason, "Unable to load more roles."));
    } finally {
      setLoadingMore(false);
    }
  }

  if (loading && !today) return <TodaySkeleton />;
  if (loadError && !today) {
    return (
      <StatusMessage kind="error">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span>{loadError}</span>
          <button type="button" onClick={() => void loadToday(false)} className={secondaryButtonClasses}>
            Try again
          </button>
        </div>
      </StatusMessage>
    );
  }
  if (!today) return null;

  const warnings = uniqueWarnings([...(today.scan_health.warnings ?? []), ...(scan?.warnings ?? [])]);
  return (
    <div className="min-w-0 space-y-6" aria-busy={refreshing}>
      <TodaySummaryStrip today={today} view={view} onView={(next) => updateFilter("view", next)} />

      {scanId ? <ScanProgress scan={scan} error={scanError} /> : null}
      {today.scan_health.state === "degraded" || warnings.length > 0 ? (
        <aside aria-labelledby="source-health-title" className="rounded-xl border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/25">
          <h2 id="source-health-title" className="font-semibold text-amber-950 dark:text-amber-100">
            Some sources could not be fully refreshed
          </h2>
          <p className="mt-1 text-sm text-amber-900 dark:text-amber-200">
            Last-confirmed roles remain visible. A failed or incomplete source does not close or hide them.
          </p>
          <ul className="mt-3 space-y-2 text-xs text-amber-900 dark:text-amber-200">
            {warnings.slice(0, 8).map((warning) => (
              <li key={`${warning.scope}:${warning.code}:${warning.company_slug ?? "scan"}`} className="break-words">
                <span className="font-medium">{warning.company_slug ? `${warning.company_slug}: ` : ""}</span>
                {warning.message}
              </li>
            ))}
            {warnings.length > 8 ? (
              <li className="font-medium">
                {warnings.length - 8} more source warning{warnings.length - 8 === 1 ? "" : "s"} are retained in the scan record.
              </li>
            ) : null}
          </ul>
        </aside>
      ) : null}

      <section aria-label="Inbox filters" className="grid min-w-0 gap-3 rounded-xl border border-zinc-200 bg-white p-4 sm:grid-cols-3 dark:border-zinc-800 dark:bg-zinc-900/70">
        <Filter label="View" id="today-view" value={view} onChange={(value) => updateFilter("view", value)}>
          <option value="inbox">Needs review</option>
          <option value="watching">Watching</option>
          <option value="dismissed">Dismissed</option>
          <option value="all">All roles</option>
        </Filter>
        <Filter label="Saved search" id="today-search" value={savedSearchId ?? ""} onChange={(value) => updateFilter("search", value)}>
          <option value="">All saved searches</option>
          {(savedSearches ?? []).map((search) => <option key={search.id} value={search.id}>{search.name}</option>)}
        </Filter>
        <Filter label="Lane" id="today-lane" value={lane ?? ""} onChange={(value) => updateFilter("lane", value)}>
          <option value="">All lanes</option>
          <option value="unassigned">Unassigned</option>
        </Filter>
      </section>

      {loadError ? <StatusMessage kind="error">{loadError} Last-confirmed roles remain below.</StatusMessage> : null}
      {actionError ? <StatusMessage kind="error">{actionError}</StatusMessage> : null}

      {today.items.length === 0 ? (
        <TodayEmptyState
          view={view}
          filtered={Boolean(savedSearchId || lane)}
          savedSearchCount={savedSearches?.length ?? null}
          totalOpportunityCount={today.summary.needs_decision + today.summary.watching + today.summary.dismissed}
          scanHealth={today.scan_health.state}
          onClear={() => router.replace("/today")}
        />
      ) : (
        <section aria-label="Opportunity review inbox" className="space-y-5">
          {today.items.map((opportunity) => (
            <OpportunityCard
              key={opportunity.id}
              opportunity={opportunity}
              pending={pendingOpportunityId === opportunity.id}
              ownerLocalDate={ownerLocalDate}
              ownerTimezone={ownerTimezone}
              onDecision={(payload) => applyDecision(opportunity, payload)}
            />
          ))}
        </section>
      )}

      {today.next_cursor ? (
        <div className="text-center">
          <button type="button" disabled={loadingMore} onClick={() => void loadMore()} className={secondaryButtonClasses}>
            {loadingMore ? "Loading…" : "Load more roles"}
          </button>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-zinc-200 pt-4 text-xs text-zinc-500 dark:border-zinc-800">
        <span>Persisted data as of {formatDate(today.as_of)}</span>
        <button type="button" disabled={refreshing} onClick={() => void loadToday(true)} className="font-medium underline underline-offset-4 disabled:opacity-50">
          {refreshing ? "Refreshing…" : "Refresh inbox"}
        </button>
      </div>

      {undo ? (
        <DecisionUndo
          key={undo.eventId}
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

function TodaySummaryStrip({ today, view, onView }: { today: TodayResponse; view: TodayView; onView: (view: TodayView) => void }) {
  const cards: Array<{ label: string; value: number; target: TodayView }> = [
    { label: "Needs review", value: today.summary.needs_decision, target: "inbox" },
    { label: "Watching", value: today.summary.watching, target: "watching" },
    { label: "Dismissed", value: today.summary.dismissed, target: "dismissed" },
  ];
  return (
    <section aria-label="Today summary" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {cards.map((card) => (
        <button
          key={card.target}
          type="button"
          onClick={() => onView(card.target)}
          aria-pressed={view === card.target}
          className="min-h-24 rounded-xl border border-zinc-200 bg-white p-4 text-left hover:border-zinc-400 dark:border-zinc-800 dark:bg-zinc-900/70"
        >
          <span className="text-2xl font-semibold">{card.value}</span>
          <span className="mt-1 block text-xs text-zinc-500">{card.label}</span>
        </button>
      ))}
      <div className="min-h-24 rounded-xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900/70">
        <span className="text-sm font-semibold capitalize">{today.scan_health.state.replaceAll("_", " ")}</span>
        <span className="mt-2 block text-xs leading-5 text-zinc-500">
          Last success: {formatDate(today.scan_health.last_success_at)}
        </span>
      </div>
    </section>
  );
}

function ScanProgress({ scan, error }: { scan: ScanStatusResponse | null; error: string | null }) {
  if (error && !scan) return <StatusMessage kind="error">{error} Existing inbox data is unchanged.</StatusMessage>;
  if (!scan) return <StatusMessage kind="info">Loading persisted scan progress… Existing roles remain available below.</StatusMessage>;
  const active = !TERMINAL_SCAN_STATES.has(scan.status);
  const kind = scan.status === "failed" ? "error" : scan.status === "succeeded" ? "success" : "info";
  return (
    <StatusMessage kind={kind}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span>
          <strong>{active ? "Scanning roles" : `Scan ${scan.status}`}</strong>{" "}
          · {scan.stage.replaceAll("_", " ")} · {scan.counts.sources_completed}/{scan.counts.sources_total} sources
        </span>
        <span>{scan.counts.new_opportunities} new · {scan.counts.changed_postings} changed</span>
      </div>
      {error ? <p className="mt-2">{error}</p> : null}
    </StatusMessage>
  );
}

function Filter({ label, id, value, onChange, children }: { label: string; id: string; value: string; onChange: (value: string) => void; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="text-xs font-medium text-zinc-600 dark:text-zinc-400">{label}</label>
      <select id={id} value={value} onChange={(event) => onChange(event.target.value)} className={`${inputClasses} mt-1 min-w-0`}>
        {children}
      </select>
    </div>
  );
}

function TodayEmptyState({ view, filtered, savedSearchCount, totalOpportunityCount, scanHealth, onClear }: { view: TodayView; filtered: boolean; savedSearchCount: number | null; totalOpportunityCount: number; scanHealth: TodayResponse["scan_health"]["state"]; onClear: () => void }) {
  let title = "Your review inbox is clear";
  let body = "Watched and dismissed roles remain available in their views.";
  if (savedSearchCount === 0) {
    title = "Create a saved search first";
    body = "A saved search defines which roles the radar should scan and remember.";
  } else if (scanHealth === "never_run") {
    title = "Run your first role scan";
    body = "Scanning a saved search creates persisted opportunities here without discovering contacts or drafting outreach.";
  } else if (view === "all" && totalOpportunityCount === 0 && scanHealth === "healthy") {
    title = "No roles remain in this review workspace";
    body = "Your latest scan is healthy. Pursued roles live in Applications, and new matches will appear here after future scans.";
  } else if (filtered) {
    title = "No roles match these filters";
    body = "Clear the filters to return to the full persisted inbox.";
  } else if (view === "watching") {
    title = "No watched roles";
    body = "Watch a role when it deserves another look but you are not ready to act.";
  } else if (view === "dismissed") {
    title = "No dismissed roles";
    body = "Dismissed roles remain recoverable here instead of disappearing permanently.";
  } else if (view === "all") {
    title = "No persisted opportunities yet";
    body = "A healthy scan may find no matching roles; source degradation is shown separately above.";
  }
  return (
    <section className="rounded-2xl border border-dashed border-zinc-300 bg-white p-8 text-center dark:border-zinc-700 dark:bg-zinc-900/50">
      <h2 className="text-lg font-semibold">{title}</h2>
      <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">{body}</p>
      <div className="mt-5 flex flex-wrap justify-center gap-3">
        {savedSearchCount === 0 || scanHealth === "never_run" ? (
          <Link href="/searches" className={secondaryButtonClasses}>{savedSearchCount === 0 ? "Create saved search" : "Choose a search to scan"}</Link>
        ) : null}
        {filtered ? <button type="button" onClick={onClear} className={secondaryButtonClasses}>Clear filters</button> : null}
      </div>
    </section>
  );
}

function TodaySkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading Today inbox" className="space-y-5">
      <p role="status" className="text-sm text-zinc-500">Loading your persisted opportunity inbox…</p>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[0, 1, 2, 3].map((item) => <div key={item} className="h-24 animate-pulse rounded-xl bg-zinc-200 dark:bg-zinc-800" />)}
      </div>
      {[0, 1, 2].map((item) => <div key={item} className="h-72 animate-pulse rounded-2xl bg-zinc-200 dark:bg-zinc-800" />)}
    </div>
  );
}

function validView(value: string | null): TodayView {
  return value === "watching" || value === "dismissed" || value === "all" ? value : "inbox";
}

function validLane(value: string | null): OpportunityLane | undefined {
  return value === "unassigned" ? value : undefined;
}

function belongsInView(state: TodayOpportunityItem["state"], view: TodayView): boolean {
  return view === "all" || (view === "inbox" && state === "inbox") || (view === "watching" && state === "watch") || (view === "dismissed" && state === "dismiss");
}

function uniqueWarnings(warnings: ScanStatusResponse["warnings"]): ScanStatusResponse["warnings"] {
  const seen = new Set<string>();
  return warnings.filter((warning) => {
    const key = `${warning.scope}:${warning.code}:${warning.company_slug ?? ""}:${warning.source ?? ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
