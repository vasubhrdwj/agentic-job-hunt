import Link from "next/link";

import type {
  CompensationValue,
  EvidenceFact,
  OpportunityFacts,
  TodayOpportunityItem,
  TransparentMatchSummary,
} from "@/lib/opportunity-types";
import { opportunityFitPresentation } from "@/lib/opportunity-fit";
import { OpportunityActions } from "./opportunity-actions";

export function OpportunityCard({
  opportunity,
  pending,
  ownerLocalDate,
  ownerTimezone,
  onDecision,
}: {
  opportunity: TodayOpportunityItem;
  pending: boolean;
  ownerLocalDate: string;
  ownerTimezone: string;
  onDecision: Parameters<typeof OpportunityActions>[0]["onDecision"];
}) {
  const posting = opportunity.posting;
  return (
    <article
      aria-labelledby={`opportunity-title-${opportunity.id}`}
      className="min-w-0 overflow-hidden rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm sm:p-6 dark:border-zinc-800 dark:bg-zinc-900/70"
    >
      <div className="flex min-w-0 flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {opportunity.match.state === "assessed" && opportunity.match.fit_band ? (
              <span className={`rounded-full px-2 py-1 font-semibold ${opportunityFitPresentation(opportunity.match.fit_band).badgeClasses}`}>
                {opportunityFitPresentation(opportunity.match.fit_band).label}
              </span>
            ) : null}
            <StateBadge>{titleCase(opportunity.lane)}</StateBadge>
            <StateBadge>{titleCase(posting.change_kind)}</StateBadge>
            <span className="text-zinc-500">First seen {relativeDate(posting.first_seen_at)}</span>
          </div>
          <h2
            id={`opportunity-title-${opportunity.id}`}
            className="mt-3 break-words text-xl font-semibold tracking-tight"
          >
            {posting.title}
          </h2>
          <p className="mt-1 break-words text-sm text-zinc-600 dark:text-zinc-400">
            {posting.company}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Link
            href={`/jobs/${encodeURIComponent(opportunity.id)}`}
            className="inline-flex min-h-11 items-center justify-center rounded-lg border border-zinc-300 px-3 py-2 text-sm font-medium hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
          >
            Review
          </Link>
          <a
            href={posting.canonical_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex min-h-11 items-center justify-center rounded-lg border border-indigo-300 px-3 py-2 text-sm font-medium text-indigo-700 hover:bg-indigo-50 dark:border-indigo-800 dark:text-indigo-300 dark:hover:bg-indigo-950"
            aria-label={`Open ${posting.first_party ? "first-party" : "source"} posting for ${posting.title} at ${posting.company} in a new tab`}
          >
            {posting.first_party ? "First-party posting ↗" : "Source posting ↗"}
          </a>
        </div>
      </div>

      {!posting.first_party ? (
        <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          This link has not been verified as first-party. Review the destination before sharing information.
        </p>
      ) : null}

      <p className="mt-4 break-words text-sm leading-6 text-zinc-700 dark:text-zinc-300">
        {posting.summary}
      </p>

      <OpportunityFactGrid facts={opportunity.facts} />
      <MatchEvidence opportunity={opportunity} />

      {opportunity.unknowns.length > 0 ? (
        <section className="mt-5 rounded-xl border border-amber-200 bg-amber-50/70 p-4 dark:border-amber-900 dark:bg-amber-950/20">
          <h3 className="text-sm font-semibold text-amber-950 dark:text-amber-100">
            Check before deciding
          </h3>
          <ul className="mt-2 space-y-1 text-xs leading-5 text-amber-900 dark:text-amber-200">
            {opportunity.unknowns.map((unknown) => (
              <li key={unknown.field} className="break-words">
                <span className="font-medium">{titleCase(unknown.field)}:</span>{" "}
                {unknown.message}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className="mt-5 border-t border-zinc-200 pt-4 dark:border-zinc-800">
        <h3 className="text-xs font-semibold uppercase tracking-[0.12em] text-zinc-500">
          Found by {opportunity.discovered_by.length} saved search{opportunity.discovered_by.length === 1 ? "" : "es"}
        </h3>
        <ul className="mt-2 flex flex-wrap gap-2">
          {opportunity.discovered_by.map((source) => (
            <li key={source.saved_search_id} className="max-w-full rounded-full bg-zinc-100 px-3 py-1 text-xs dark:bg-zinc-800">
              <span className="break-words">{source.saved_search_name}</span>
            </li>
          ))}
        </ul>
      </section>

      <div className="mt-5 border-t border-zinc-200 pt-5 dark:border-zinc-800">
        <OpportunityActions
          opportunity={opportunity}
          pending={pending}
          ownerLocalDate={ownerLocalDate}
          ownerTimezone={ownerTimezone}
          onDecision={onDecision}
        />
      </div>
    </article>
  );
}

export function OpportunityFactGrid({ facts }: { facts: OpportunityFacts }) {
  const entries: Array<{ label: string; fact: EvidenceFact<unknown>; value: string }> = [
    { label: "Location", fact: facts.location, value: facts.location.value ?? "Unknown" },
    {
      label: "Employment",
      fact: facts.employment_type,
      value: facts.employment_type.value ? titleCase(facts.employment_type.value) : "Unknown",
    },
    {
      label: "Posted",
      fact: facts.posted_date,
      value: facts.posted_date.value ? formatDateOnly(facts.posted_date.value) : "Unknown",
    },
    {
      label: "Compensation",
      fact: facts.compensation,
      value: facts.compensation.value ? formatCompensation(facts.compensation.value) : "Unknown",
    },
  ];
  return (
    <dl className="mt-5 grid min-w-0 grid-cols-2 gap-3 lg:grid-cols-4">
      {entries.map(({ label, fact, value }) => (
        <div key={label} className="min-w-0 rounded-lg bg-zinc-50 p-3 dark:bg-zinc-950/60">
          <dt className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">{label}</dt>
          <dd className="mt-1 break-words text-sm font-medium">{value}</dd>
          <dd className={`mt-1 text-[11px] ${factStateColor(fact.state)}`}>
            {titleCase(fact.state)}{fact.source_label ? ` · ${fact.source_label}` : ""}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function MatchEvidence({ opportunity }: { opportunity: TodayOpportunityItem }) {
  const match = opportunity.match;
  if (match.state === "not_assessed" || !match.fit_band || !match.confidence) {
    return (
      <section className="mt-5 rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
        <h3 className="text-sm font-semibold">Automatic fit assessment</h3>
        <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
          {notAssessedLabel(match.not_assessed_reason)}
        </p>
      </section>
    );
  }
  const presentation = opportunityFitPresentation(match.fit_band);
  return (
    <section className={`mt-5 rounded-xl border p-4 ${presentation.panelClasses}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-zinc-500">
            Automatic fit assessment
          </p>
          <h3 className="mt-1 text-lg font-semibold">{presentation.label}</h3>
        </div>
        <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${presentation.badgeClasses}`}>
          {titleCase(match.confidence)} confidence
        </span>
      </div>
      <p className="mt-2 text-sm leading-6 text-zinc-700 dark:text-zinc-300">
        {presentation.guidance}
      </p>
      {match.strengths.length > 0 ? (
        <div className="mt-4">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-zinc-600 dark:text-zinc-300">
            Why it fits
          </h4>
          <ul className="mt-2 space-y-1 text-sm leading-6 text-zinc-700 dark:text-zinc-300">
            {match.strengths.map((strength) => <li key={strength}>✓ {strength}</li>)}
          </ul>
        </div>
      ) : null}
      {match.gaps.length > 0 ? (
        <div className="mt-4">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-zinc-600 dark:text-zinc-300">
            What to verify
          </h4>
          <ul className="mt-2 space-y-1 text-sm leading-6 text-zinc-700 dark:text-zinc-300">
            {match.gaps.map((gap) => <li key={gap}>• {gap}</li>)}
          </ul>
        </div>
      ) : null}
      {match.matched_terms.length > 0 ? (
        <div className="mt-4 flex flex-wrap gap-2" aria-label="Supported job-description skills">
          {match.matched_terms.map((term) => (
            <span key={term} className="rounded-full bg-emerald-50 px-2.5 py-1 text-xs text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200">
              {term}
            </span>
          ))}
        </div>
      ) : (
        <p className="mt-4 text-sm text-zinc-600 dark:text-zinc-400">
          No meaningful technical overlap was found in the saved résumé and approved evidence.
        </p>
      )}
      {match.representative_requirement ? (
        <p className="mt-3 break-words text-xs leading-5 text-zinc-600 dark:text-zinc-400">
          <span className="font-medium text-zinc-800 dark:text-zinc-200">JD evidence:</span>{" "}
          {match.representative_requirement}
        </p>
      ) : null}
      <p className="mt-2 text-[11px] text-zinc-500">
        Local rule-based method: {match.algorithm_version}. Approved evidence links: {match.approved_evidence_ids.length}. No paid model or invented percentage.
      </p>
    </section>
  );
}

function StateBadge({ children }: { children: React.ReactNode }) {
  return <span className="rounded-full bg-zinc-100 px-2 py-1 font-medium dark:bg-zinc-800">{children}</span>;
}

function factStateColor(state: EvidenceFact<unknown>["state"]): string {
  if (state === "verified") return "text-emerald-700 dark:text-emerald-300";
  if (state === "inferred") return "text-blue-700 dark:text-blue-300";
  return "text-amber-700 dark:text-amber-300";
}

function formatCompensation(value: CompensationValue): string {
  const formatter = new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: value.currency,
    maximumFractionDigits: 0,
  });
  const minimum = value.minimum === null ? null : formatter.format(value.minimum);
  const maximum = value.maximum === null ? null : formatter.format(value.maximum);
  const range = minimum && maximum ? `${minimum}–${maximum}` : minimum ?? maximum ?? "Unknown";
  return `${range} / ${value.period}`;
}

function formatDateOnly(value: string): string {
  const parsed = new Date(`${value}T00:00:00`);
  return Number.isNaN(parsed.getTime())
    ? "Unknown"
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(parsed);
}

function relativeDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "at an unknown time";
  const days = Math.max(0, Math.floor((Date.now() - parsed.getTime()) / 86_400_000));
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  return `${days} days ago`;
}

function notAssessedLabel(reason: TransparentMatchSummary["not_assessed_reason"]): string {
  const labels = {
    assessment_pending: "The saved local comparison is still pending.",
    resume_unavailable: "No usable resume version was available for comparison.",
    description_unavailable: "The source did not provide enough job-description text.",
    assessment_unavailable: "The saved private inputs could not be read safely, so no recommendation was produced.",
    not_requested: "This role has not been compared with your saved profile yet.",
  };
  return reason ? labels[reason] : "This role has not been assessed.";
}

function titleCase(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
