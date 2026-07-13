"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { postHunt, ApiError } from "@/lib/api";
import {
  consumeHuntIdempotencyKey,
  huntIdempotencyKey,
} from "@/lib/hunt-idempotency";
import type {
  EmploymentType,
  JobCriteria,
  Seniority,
} from "@/lib/types";
import {
  getCandidateProfile,
  getResumeVersion,
  getSavedSearchHuntInput,
  listResumeVersions,
  listSavedSearches,
} from "@/lib/workspace-api";
import type { HuntInput, SavedSearch } from "@/lib/workspace-types";
import { HuntProgress } from "./hunt-progress";

const SENIORITY_OPTIONS: Seniority[] = ["junior", "mid", "senior", "staff"];
const DEFAULT_KEYWORDS =
  "backend engineer, software engineer, backend developer";
const DEFAULT_LOCATIONS = "India, Remote-India, Bengaluru, Hyderabad";
const EMPLOYMENT_OPTIONS: {
  value: EmploymentType;
  label: string;
}[] = [
  { value: "full_time", label: "Full-time" },
  { value: "contract", label: "Contract" },
  { value: "intern", label: "Internship" },
];

function splitCsv(value: string): string[] {
  return value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

export function InputForm() {
  const router = useRouter();
  const submissionInFlight = useRef(false);
  const [resume, setResume] = useState("");
  const [keywords, setKeywords] = useState(DEFAULT_KEYWORDS);
  const [locations, setLocations] = useState(DEFAULT_LOCATIONS);
  const [seniority, setSeniority] = useState<Seniority>("junior");
  const [pack, setPack] = useState("backend_india");
  const [employmentTypes, setEmploymentTypes] = useState<EmploymentType[]>([
    "full_time",
  ]);
  const [compMin, setCompMin] = useState("");
  const [compMax, setCompMax] = useState("");
  const [maxAgeDays, setMaxAgeDays] = useState("45");
  const [country, setCountry] = useState("in");
  const [useSelfRag, setUseSelfRag] = useState(true);
  const [providerConsent, setProviderConsent] = useState(false);

  const [savedSearches, setSavedSearches] = useState<SavedSearch[]>([]);
  const [selectedSearchId, setSelectedSearchId] = useState("");
  const [prefillLabel, setPrefillLabel] = useState<string | null>(null);
  const [savedSearchLoading, setSavedSearchLoading] = useState(true);
  const [savedSearchError, setSavedSearchError] = useState<string | null>(null);

  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const applyHuntInput = useCallback(
    (input: HuntInput, label: string, searchId = "") => {
      setResume(input.resume_text);
      setKeywords(input.criteria.role_keywords.join(", "));
      setLocations(input.criteria.location.join(", "));
      setSeniority(input.criteria.seniority);
      setPack(input.pack);
      setEmploymentTypes(input.criteria.employment_types);
      setCompMin(input.criteria.comp_min_lpa?.toString() ?? "");
      setCompMax(input.criteria.comp_max_lpa?.toString() ?? "");
      setMaxAgeDays(input.criteria.max_age_days?.toString() ?? "");
      setCountry(input.criteria.country ?? "in");
      setUseSelfRag(input.use_self_rag);
      setSelectedSearchId(searchId);
      setPrefillLabel(label);
    },
    [],
  );

  useEffect(() => {
    let active = true;
    listSavedSearches()
      .then((items) => {
        if (active) setSavedSearches(items);
      })
      .catch((reason) => {
        if (active) {
          setSavedSearchError(
            reason instanceof Error
              ? reason.message
              : "Saved searches are unavailable right now.",
          );
        }
      })
      .finally(() => {
        if (active) setSavedSearchLoading(false);
      });

    const querySearchId = new URLSearchParams(window.location.search).get(
      "savedSearch",
    );
    if (querySearchId) {
      getSavedSearchHuntInput(querySearchId)
        .then((projection) => {
          if (!active) return;
          if (!projection.ready || !projection.input) {
            setSavedSearchError(
              "This saved search needs profile attention before it can run.",
            );
            return;
          }
          applyHuntInput(
            projection.input,
            "Saved search",
            querySearchId,
          );
        })
        .catch((reason) => {
          if (active) {
            setSavedSearchError(
              reason instanceof Error
                ? reason.message
                : "Unable to load this saved search.",
            );
          }
        });
    } else {
      loadBaseResume()
        .then((record) => {
          if (!active || !record) return;
          setResume(record.data.content);
          setPrefillLabel(`Base resume · ${record.data.label}`);
        })
        .catch(() => undefined);
    }
    return () => {
      active = false;
    };
  }, [applyHuntInput]);

  async function selectSavedSearch(searchId: string) {
    setSelectedSearchId(searchId);
    setSavedSearchError(null);
    if (!searchId) {
      window.history.replaceState(null, "", "/hunt");
      setKeywords(DEFAULT_KEYWORDS);
      setLocations(DEFAULT_LOCATIONS);
      setSeniority("junior");
      setPack("backend_india");
      setEmploymentTypes(["full_time"]);
      setCompMin("");
      setCompMax("");
      setMaxAgeDays("45");
      setCountry("in");
      setUseSelfRag(true);
      setPrefillLabel(null);
      setResume("");
      try {
        const base = await loadBaseResume();
        if (base) {
          setResume(base.data.content);
          setPrefillLabel(`Base resume · ${base.data.label}`);
        }
      } catch {
        // The one-off form remains usable with a manual paste.
      }
      return;
    }
    window.history.replaceState(
      null,
      "",
      `/hunt?savedSearch=${encodeURIComponent(searchId)}`,
    );
    const search = savedSearches.find((item) => item.id === searchId);
    try {
      const projection = await getSavedSearchHuntInput(searchId);
      if (!projection.ready || !projection.input) {
        setSavedSearchError(
          projection.blockers.length
            ? "This saved search needs profile attention before it can run."
            : "This saved search is not ready to run.",
        );
        return;
      }
      applyHuntInput(projection.input, search?.name ?? "Saved search", searchId);
    } catch (reason) {
      setSavedSearchError(
        reason instanceof Error ? reason.message : "Unable to load this saved search.",
      );
    }
  }

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (submissionInFlight.current) return;
    setError(null);

    const keywordList = splitCsv(keywords);
    const locationList = splitCsv(locations);

    if (!resume.trim()) {
      setError("Paste a resume before running the hunt.");
      return;
    }
    if (keywordList.length === 0) {
      setError("Add at least one role keyword.");
      return;
    }
    if (locationList.length === 0) {
      setError("Add at least one location.");
      return;
    }
    if (employmentTypes.length === 0) {
      setError("Select at least one employment type.");
      return;
    }
    if (!/^[a-z]{2}$/.test(country.trim().toLowerCase())) {
      setError("Use a two-letter lowercase country code, such as in or us.");
      return;
    }
    const minComp = compMin ? Number(compMin) : null;
    const maxComp = compMax ? Number(compMax) : null;
    if (minComp !== null && maxComp !== null && minComp > maxComp) {
      setError("Minimum compensation cannot be higher than maximum compensation.");
      return;
    }
    if (!providerConsent) {
      setError("Review and accept the resume-processing disclosure.");
      return;
    }

    const criteria: JobCriteria = {
      role_keywords: keywordList,
      seniority,
      location: locationList,
      comp_min_lpa: minComp,
      comp_max_lpa: maxComp,
      employment_types: employmentTypes,
      max_age_days: maxAgeDays ? Number(maxAgeDays) : null,
      country: country.trim().toLowerCase(),
    };

    submissionInFlight.current = true;
    setPending(true);
    try {
      const idempotencyKey = await huntIdempotencyKey(
        resume,
        criteria,
        pack,
        useSelfRag,
      );
      const result = await postHunt(
        resume,
        criteria,
        pack,
        idempotencyKey,
        useSelfRag,
      );
      await consumeHuntIdempotencyKey(resume, criteria, pack, useSelfRag);
      router.push(`/runs/${result.run_id}`);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Unknown error.";
      setError(message);
      submissionInFlight.current = false;
      setPending(false);
    }
  }

  if (pending) {
    return <HuntProgress />;
  }

  return (
    <form onSubmit={onSubmit} className="space-y-6">
      <div className="rounded-xl border border-indigo-200 bg-indigo-50/70 p-4 dark:border-indigo-900 dark:bg-indigo-950/30">
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-0 flex-1">
            <label htmlFor="saved-search-prefill" className="block text-sm font-medium">
              Start from a saved search
            </label>
            <select
              id="saved-search-prefill"
              value={selectedSearchId}
              disabled={savedSearchLoading}
              onChange={(event) => void selectSavedSearch(event.target.value)}
              className={`${inputClasses} mt-2`}
            >
              <option value="">
                {savedSearchLoading ? "Loading saved searches…" : "One-off hunt"}
              </option>
              {savedSearches.map((search) => (
                <option key={search.id} value={search.id}>
                  {search.name}
                </option>
              ))}
            </select>
          </div>
          <Link
            href="/searches"
            className="pb-2 text-sm font-medium text-indigo-700 underline underline-offset-4 dark:text-indigo-300"
          >
            Manage searches
          </Link>
        </div>
        {prefillLabel ? (
          <p className="mt-2 text-xs text-indigo-800 dark:text-indigo-200">
            Prefilled from {prefillLabel}. Review every field before running.
          </p>
        ) : null}
        {savedSearchError ? (
          <p role="alert" className="mt-2 text-xs text-red-700 dark:text-red-300">
            {savedSearchError}
          </p>
        ) : null}
      </div>
      {error && (
        <div
          role="alert"
          className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100"
        >
          {error}
        </div>
      )}

      <Field
        label="Resume"
        hint="Paste plain text. The backend uses the full resume for local scoring; drafting sends only a bounded role-relevant excerpt to the configured model provider."
        htmlFor="resume"
      >
        <textarea
          id="resume"
          value={resume}
          onChange={(e) => setResume(e.target.value)}
          rows={12}
          className={textareaClasses}
          placeholder="Built Python and Go backend services at..."
        />
      </Field>

      <Field
        label="Role keywords"
        hint="Comma-separated. Keep these broad enough to discover adjacent backend roles."
        htmlFor="keywords"
      >
        <input
          id="keywords"
          type="text"
          value={keywords}
          onChange={(e) => setKeywords(e.target.value)}
          className={inputClasses}
        />
      </Field>

      <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="Company pack" htmlFor="pack">
          <select
            id="pack"
            value={pack}
            onChange={(e) => setPack(e.target.value)}
            className={inputClasses}
          >
            <option value="backend_india">
              Backend · India + remote
            </option>
          </select>
        </Field>

        <Field label="Seniority" htmlFor="seniority">
          <select
            id="seniority"
            value={seniority}
            onChange={(e) => setSeniority(e.target.value as Seniority)}
            className={inputClasses}
          >
            {SENIORITY_OPTIONS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </Field>

        <Field
          label="Locations"
          hint="Comma-separated"
          htmlFor="locations"
        >
          <input
            id="locations"
            type="text"
            value={locations}
            onChange={(e) => setLocations(e.target.value)}
            className={inputClasses}
          />
        </Field>
        <Field label="Max posting age" hint="Days" htmlFor="max-age-days">
          <input
            id="max-age-days"
            type="number"
            min={1}
            max={365}
            value={maxAgeDays}
            onChange={(event) => setMaxAgeDays(event.target.value)}
            className={inputClasses}
          />
        </Field>
        <Field label="Country code" htmlFor="country">
          <input
            id="country"
            maxLength={2}
            value={country}
            onChange={(event) => setCountry(event.target.value.toLowerCase())}
            className={inputClasses}
          />
        </Field>
      </div>

      <label className="flex items-start gap-3 rounded-lg border border-zinc-200 bg-white p-4 text-sm dark:border-zinc-800 dark:bg-zinc-900">
        <input
          type="checkbox"
          checked={useSelfRag}
          onChange={(event) => setUseSelfRag(event.target.checked)}
          className="mt-1"
        />
        <span>
          <strong>Use high-scoring past outreach examples</strong>
          <br />
          <span className="text-zinc-500">
            Keeps the saved-search drafting preference without changing your criteria.
          </span>
        </span>
      </label>

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
          Employment types
        </legend>
        <div className="flex flex-wrap gap-3">
          {EMPLOYMENT_OPTIONS.map((option) => {
            const checked = employmentTypes.includes(option.value);
            return (
              <label
                key={option.value}
                className={`inline-flex cursor-pointer items-center gap-2 rounded-full border px-3 py-2 text-xs ${
                  checked
                    ? "border-indigo-500 bg-indigo-50 text-indigo-900 dark:bg-indigo-950 dark:text-indigo-100"
                    : "border-zinc-300 dark:border-zinc-700"
                }`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() =>
                    setEmploymentTypes((current) =>
                      checked
                        ? current.filter((value) => value !== option.value)
                        : [...current, option.value],
                    )
                  }
                  className="sr-only"
                />
                {option.label}
              </label>
            );
          })}
        </div>
      </fieldset>

      <div className="grid gap-6 sm:grid-cols-2">
        <Field
          label="Comp min (LPA)"
          hint="Optional"
          htmlFor="comp-min"
        >
          <input
            id="comp-min"
            type="number"
            min={0}
            value={compMin}
            onChange={(e) => setCompMin(e.target.value)}
            className={inputClasses}
          />
        </Field>
        <Field
          label="Comp max (LPA)"
          hint="Optional"
          htmlFor="comp-max"
        >
          <input
            id="comp-max"
            type="number"
            min={0}
            value={compMax}
            onChange={(e) => setCompMax(e.target.value)}
            className={inputClasses}
          />
        </Field>
      </div>

      <label className="flex items-start gap-3 rounded-lg border border-zinc-200 bg-white p-4 text-sm dark:border-zinc-800 dark:bg-zinc-900">
        <input
          id="provider-consent"
          type="checkbox"
          checked={providerConsent}
          onChange={(event) => setProviderConsent(event.target.checked)}
          className="mt-1 h-4 w-4 rounded border-zinc-300 text-indigo-600 focus:ring-indigo-500"
        />
        <span>
          I agree that role-relevant resume excerpts may be sent to the
          configured paid Gemini API. Google retains prompts and responses for
          55 days for abuse monitoring.{" "}
          <Link
            href="/privacy"
            className="font-medium text-indigo-600 underline underline-offset-2 dark:text-indigo-400"
          >
            Read the privacy details.
          </Link>
        </span>
      </label>

      <button
        type="submit"
        disabled={pending}
        className="inline-flex h-11 items-center justify-center rounded-md bg-indigo-600 px-6 text-sm font-medium text-white hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 disabled:opacity-60"
      >
        Run hunt
      </button>
    </form>
  );
}

async function loadBaseResume() {
  const profile = await getCandidateProfile();
  const baseId =
    profile?.data.base_resume?.id ??
    (await listResumeVersions()).find((resume) => resume.is_base)?.id;
  return baseId ? getResumeVersion(baseId) : null;
}

function Field({
  label,
  hint,
  htmlFor,
  children,
}: {
  label: string;
  hint?: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label
        htmlFor={htmlFor}
        className="block text-sm font-medium text-zinc-900 dark:text-zinc-100"
      >
        {label}
      </label>
      {children}
      {hint && (
        <p className="text-xs text-zinc-500 dark:text-zinc-400">{hint}</p>
      )}
    </div>
  );
}

const inputClasses =
  "block w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-900";

const textareaClasses = `${inputClasses} font-mono`;
