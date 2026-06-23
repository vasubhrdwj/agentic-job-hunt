"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { postHunt, ApiError } from "@/lib/api";
import type {
  EmploymentType,
  JobCriteria,
  Seniority,
} from "@/lib/types";
import { HuntProgress } from "./hunt-progress";

const SENIORITY_OPTIONS: Seniority[] = ["junior", "mid", "senior", "staff"];
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
  const [resume, setResume] = useState("");
  const [keywords, setKeywords] = useState(
    "backend engineer, software engineer, backend developer",
  );
  const [locations, setLocations] = useState(
    "India, Remote-India, Bengaluru, Hyderabad",
  );
  const [seniority, setSeniority] = useState<Seniority>("junior");
  const [pack, setPack] = useState("backend_india");
  const [employmentTypes, setEmploymentTypes] = useState<EmploymentType[]>([
    "full_time",
  ]);
  const [compMin, setCompMin] = useState("");
  const [compMax, setCompMax] = useState("");

  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
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

    const criteria: JobCriteria = {
      role_keywords: keywordList,
      seniority,
      location: locationList,
      comp_min_lpa: compMin ? Number(compMin) : null,
      comp_max_lpa: compMax ? Number(compMax) : null,
      employment_types: employmentTypes,
      max_age_days: 45,
      country: "in",
    };

    setPending(true);
    try {
      const result = await postHunt(resume, criteria, pack);
      router.push(`/runs/${result.run_id}`);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Unknown error.";
      setError(message);
      setPending(false);
    }
  }

  if (pending) {
    return <HuntProgress />;
  }

  return (
    <form onSubmit={onSubmit} className="space-y-6">
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
        hint="Paste plain text. It is sent to the backend and, when drafting, the configured model provider."
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

      <div className="grid gap-6 sm:grid-cols-2">
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
      </div>

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

      <button
        type="submit"
        className="inline-flex h-11 items-center justify-center rounded-md bg-indigo-600 px-6 text-sm font-medium text-white hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 disabled:opacity-60"
      >
        Run hunt
      </button>
    </form>
  );
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
