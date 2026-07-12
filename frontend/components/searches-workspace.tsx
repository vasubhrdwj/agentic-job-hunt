"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  createIdempotencyKey,
  createSavedSearch,
  deleteSavedSearch,
  getCandidateProfile,
  getSavedSearchHuntInput,
  listCareerTracks,
  listResumeVersions,
  listSavedSearches,
  updateSavedSearch,
} from "@/lib/workspace-api";
import type {
  CandidateProfile,
  CareerTrack,
  DayOfWeek,
  ResumeVersionSummary,
  SavedSearch,
  SavedSearchCreate,
  ScheduleCadence,
  Versioned,
} from "@/lib/workspace-types";
import type { EmploymentType, Seniority } from "@/lib/types";
import {
  errorText,
  formatDate,
  FormField,
  inputClasses,
  primaryButtonClasses,
  secondaryButtonClasses,
  splitList,
  StatusMessage,
  WorkspaceSection,
} from "./workspace-ui";

const SENIORITIES: Seniority[] = ["junior", "mid", "senior", "staff"];
const EMPLOYMENT_TYPES: Array<{
  value: Exclude<EmploymentType, "unknown">;
  label: string;
}> = [
  { value: "full_time", label: "Full-time" },
  { value: "contract", label: "Contract" },
  { value: "intern", label: "Internship" },
];
const WEEKDAYS: Array<{ value: DayOfWeek; label: string }> = [
  { value: "mon", label: "Mon" },
  { value: "tue", label: "Tue" },
  { value: "wed", label: "Wed" },
  { value: "thu", label: "Thu" },
  { value: "fri", label: "Fri" },
  { value: "sat", label: "Sat" },
  { value: "sun", label: "Sun" },
];

export function SearchesWorkspace() {
  const router = useRouter();
  const [profile, setProfile] = useState<Versioned<CandidateProfile> | null>(null);
  const [resumes, setResumes] = useState<ResumeVersionSummary[]>([]);
  const [tracks, setTracks] = useState<CareerTrack[]>([]);
  const [searches, setSearches] = useState<SavedSearch[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [trackId, setTrackId] = useState("");
  const [resumeId, setResumeId] = useState("");
  const [keywords, setKeywords] = useState("");
  const [locations, setLocations] = useState("");
  const [seniority, setSeniority] = useState<Seniority>("senior");
  const [employmentTypes, setEmploymentTypes] = useState<
    Exclude<EmploymentType, "unknown">[]
  >(["full_time"]);
  const [compMin, setCompMin] = useState("");
  const [compMax, setCompMax] = useState("");
  const [maxAgeDays, setMaxAgeDays] = useState("45");
  const [country, setCountry] = useState("in");
  const [pack, setPack] = useState("backend_india");
  const [useSelfRag, setUseSelfRag] = useState(true);
  const [searchActive, setSearchActive] = useState(true);
  const [cadence, setCadence] = useState<ScheduleCadence>("manual");
  const [timezone, setTimezone] = useState("UTC");
  const [localTime, setLocalTime] = useState("09:00");
  const [days, setDays] = useState<DayOfWeek[]>(["mon"]);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<{
    kind: "error" | "success";
    text: string;
  } | null>(null);
  const [runningId, setRunningId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [runError, setRunError] = useState<Record<string, string>>({});
  const createKey = useRef<string | null>(null);

  const reload = useCallback(async () => {
    setLoadError(null);
    try {
      const [nextProfile, nextResumes, nextTracks, nextSearches] = await Promise.all([
        getCandidateProfile(),
        listResumeVersions(),
        listCareerTracks(),
        listSavedSearches(),
      ]);
      setProfile(nextProfile);
      setResumes(nextResumes);
      setTracks(nextTracks);
      setSearches(nextSearches);
      setTrackId((current) => current || nextTracks.find((track) => track.active)?.id || "");
      const defaultSeniority = nextTracks.find((track) => track.active)?.seniority_levels[0];
      if (defaultSeniority) setSeniority((current) => current || defaultSeniority);
    } catch (error) {
      setLoadError(errorText(error, "Unable to load saved searches."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([
      getCandidateProfile(),
      listResumeVersions(),
      listCareerTracks(),
      listSavedSearches(),
    ])
      .then(([nextProfile, nextResumes, nextTracks, nextSearches]) => {
        if (!active) return;
        setProfile(nextProfile);
        setResumes(nextResumes);
        setTracks(nextTracks);
        setSearches(nextSearches);
        const detectedTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
        if (detectedTimezone) setTimezone(detectedTimezone);
        setTrackId(nextTracks.find((track) => track.active)?.id || "");
        const defaultSeniority = nextTracks.find(
          (track) => track.active,
        )?.seniority_levels[0];
        if (defaultSeniority) setSeniority(defaultSeniority);
      })
      .catch((error) => {
        if (active) setLoadError(errorText(error, "Unable to load saved searches."));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function saveSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const roleKeywords = splitList(keywords);
    const targetLocations = splitList(locations);
    const min = compMin ? Number(compMin) : null;
    const max = compMax ? Number(compMax) : null;
    if (!name.trim() || !trackId || roleKeywords.length === 0 || targetLocations.length === 0) {
      setMessage({
        kind: "error",
        text: "Name the search and choose a career target, role keywords, and locations.",
      });
      return;
    }
    const chosenTrack = tracks.find((track) => track.id === trackId);
    if (!chosenTrack?.active) {
      setMessage({ kind: "error", text: "Choose an active career target for this search." });
      return;
    }
    if (!chosenTrack.seniority_levels.includes(seniority)) {
      setMessage({ kind: "error", text: "Choose a seniority allowed by the selected career target." });
      return;
    }
    if (employmentTypes.length === 0) {
      setMessage({ kind: "error", text: "Choose at least one employment type." });
      return;
    }
    if (min !== null && max !== null && min > max) {
      setMessage({
        kind: "error",
        text: "Minimum compensation cannot be higher than maximum compensation.",
      });
      return;
    }
    if (cadence === "weekly" && days.length === 0) {
      setMessage({ kind: "error", text: "Choose at least one day for a weekly schedule." });
      return;
    }

    setPending(true);
    setMessage(null);
    try {
      const payload: SavedSearchCreate = {
          name: name.trim(),
          career_track_id: trackId,
          resume_version_id: resumeId || null,
          criteria: {
            role_keywords: roleKeywords,
            seniority,
            location: targetLocations,
            comp_min_lpa: min,
            comp_max_lpa: max,
            employment_types: employmentTypes,
            max_age_days: maxAgeDays ? Number(maxAgeDays) : null,
            country: country.trim().toLowerCase(),
          },
          schedule: {
            cadence,
            timezone: timezone.trim(),
            local_time: cadence === "manual" ? null : localTime,
            days_of_week: cadence === "weekly" ? days : [],
          },
          pack,
          use_self_rag: useSelfRag,
          active: searchActive,
        };
      const editing = editingId
        ? searches.find((search) => search.id === editingId)
        : null;
      if (editing) {
        await updateSavedSearch(editing, payload);
      } else {
        createKey.current ??= createIdempotencyKey("saved-search");
        await createSavedSearch(payload, createKey.current);
      }
      createKey.current = null;
      setName("");
      setEditingId(null);
      setMessage({
        kind: "success",
        text:
          editing
            ? "Saved search updated."
            : cadence === "manual"
            ? "Saved search created. Use Run now whenever you are ready."
            : "Saved search and schedule preference created. Automatic scans are not connected yet; use Run now for now.",
      });
      await reload();
    } catch (error) {
      setMessage({ kind: "error", text: errorText(error, "Unable to save the search.") });
    } finally {
      setPending(false);
    }
  }

  function editSearch(search: SavedSearch) {
    setEditingId(search.id);
    setName(search.name);
    setTrackId(search.career_track_id);
    setResumeId(search.resume_version_id ?? "");
    setKeywords(search.criteria.role_keywords.join(", "));
    setLocations(search.criteria.location.join(", "));
    setSeniority(search.criteria.seniority);
    setEmploymentTypes(search.criteria.employment_types);
    setCompMin(search.criteria.comp_min_lpa?.toString() ?? "");
    setCompMax(search.criteria.comp_max_lpa?.toString() ?? "");
    setMaxAgeDays(search.criteria.max_age_days?.toString() ?? "");
    setCountry(search.criteria.country ?? "in");
    setPack(search.pack);
    setUseSelfRag(search.use_self_rag);
    setSearchActive(search.active);
    setCadence(search.schedule.cadence);
    setTimezone(search.schedule.timezone);
    setLocalTime(search.schedule.local_time?.slice(0, 5) ?? "09:00");
    setDays(search.schedule.days_of_week);
    setMessage(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function chooseTrack(nextTrackId: string) {
    setTrackId(nextTrackId);
    const nextTrack = tracks.find((track) => track.id === nextTrackId);
    if (nextTrack && !nextTrack.seniority_levels.includes(seniority)) {
      setSeniority(nextTrack.seniority_levels[0]);
    }
  }

  function cancelEdit() {
    setEditingId(null);
    setName("");
    createKey.current = null;
    setMessage(null);
  }

  async function removeSearch(search: SavedSearch) {
    if (!window.confirm(`Delete “${search.name}”? This does not delete your resume or career target.`)) {
      return;
    }
    setDeletingId(search.id);
    setRunError((current) => ({ ...current, [search.id]: "" }));
    try {
      await deleteSavedSearch(search);
      setSearches(await listSavedSearches());
      if (editingId === search.id) cancelEdit();
    } catch (error) {
      setRunError((current) => ({
        ...current,
        [search.id]: errorText(error, "Unable to delete this search."),
      }));
    } finally {
      setDeletingId(null);
    }
  }

  async function runNow(search: SavedSearch) {
    setRunningId(search.id);
    setRunError((current) => ({ ...current, [search.id]: "" }));
    try {
      const projection = await getSavedSearchHuntInput(search.id);
      if (!projection.ready || !projection.input) {
        const blockers = projection.blockers.map(blockerLabel).join(" ");
        setRunError((current) => ({
          ...current,
          [search.id]: blockers || "This search is not ready to run.",
        }));
        return;
      }
      router.push(`/?savedSearch=${encodeURIComponent(search.id)}`);
    } catch (error) {
      setRunError((current) => ({
        ...current,
        [search.id]: errorText(error, "Unable to prepare this search."),
      }));
    } finally {
      setRunningId(null);
    }
  }

  if (loading) {
    return <p role="status" className="text-sm text-zinc-500">Loading your saved searches…</p>;
  }
  if (loadError) {
    return (
      <StatusMessage kind="error">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span>{loadError}</span>
          <button type="button" className={secondaryButtonClasses} onClick={() => void reload()}>
            Try again
          </button>
        </div>
      </StatusMessage>
    );
  }

  const activeTracks = tracks.filter((track) => track.active);
  const hasResume = Boolean(profile?.data.base_resume || resumes.length > 0);
  const missingSetup = [
    !profile ? "About you" : null,
    !hasResume ? "a base resume" : null,
    activeTracks.length === 0 ? "a career target" : null,
  ].filter((item): item is string => item !== null);
  const selectedTrack = tracks.find((track) => track.id === trackId);
  const allowedSeniorities = selectedTrack?.seniority_levels ?? SENIORITIES;
  const selectableTracks =
    selectedTrack && !selectedTrack.active
      ? [selectedTrack, ...activeTracks]
      : activeTracks;

  return (
    <div className="space-y-8">
      <WorkspaceSection
        eyebrow="Reusable search"
        title="Save exactly what you want"
        description="Saving only remembers your choices. It does not contact employers, search the web, or use an AI provider."
      >
        {missingSetup.length > 0 ? (
          <StatusMessage kind="info">
            <p>
              Before saving a search, complete {formatList(missingSetup)} on your profile.
            </p>
            <Link href="/profile" className="mt-2 inline-block font-medium underline underline-offset-4">
              Complete profile setup
            </Link>
          </StatusMessage>
        ) : (
          <form onSubmit={saveSearch} className="space-y-5">
            <div className="grid gap-5 sm:grid-cols-2">
              <FormField label="Search name" htmlFor="search-name">
                <input id="search-name" value={name} onChange={(event) => setName(event.target.value)} className={inputClasses} placeholder="Senior backend · India" />
              </FormField>
              <FormField label="Career target" htmlFor="search-track">
                <select id="search-track" value={trackId} onChange={(event) => chooseTrack(event.target.value)} className={inputClasses}>
                  {selectableTracks.map((track) => <option key={track.id} value={track.id} disabled={!track.active}>{track.name}{track.active ? "" : " · Inactive—choose another"}</option>)}
                </select>
              </FormField>
              <FormField label="Resume" htmlFor="search-resume" hint="Saving pins the resume version used today. Changing your base later will not silently change this search.">
                <select id="search-resume" value={resumeId} onChange={(event) => setResumeId(event.target.value)} className={inputClasses}>
                  <option value="">Use current base resume</option>
                  {resumes.map((resume) => <option key={resume.id} value={resume.id}>{resume.label}{resume.is_base ? " · Base" : ""}</option>)}
                </select>
              </FormField>
              <FormField label="Seniority" htmlFor="search-seniority">
                <select id="search-seniority" value={seniority} onChange={(event) => setSeniority(event.target.value as Seniority)} className={inputClasses}>
                  {allowedSeniorities.map((value) => <option key={value} value={value}>{titleCase(value)}</option>)}
                </select>
              </FormField>
            </div>

            <FormField label="Role keywords" htmlFor="search-keywords" hint="Comma-separated. Include adjacent titles you would genuinely consider.">
              <input id="search-keywords" value={keywords} onChange={(event) => setKeywords(event.target.value)} className={inputClasses} placeholder="backend engineer, platform engineer, distributed systems" />
            </FormField>
            <FormField label="Locations" htmlFor="search-locations" hint="Comma-separated">
              <input id="search-locations" value={locations} onChange={(event) => setLocations(event.target.value)} className={inputClasses} placeholder="Remote-India, Bengaluru, Hyderabad" />
            </FormField>

            <fieldset>
              <legend className="text-sm font-medium">Employment types</legend>
              <div className="mt-2 flex flex-wrap gap-2">
                {EMPLOYMENT_TYPES.map((option) => {
                  const checked = employmentTypes.includes(option.value);
                  return (
                    <label key={option.value} className={`cursor-pointer rounded-full border px-3 py-2 text-xs font-medium ${checked ? "border-indigo-500 bg-indigo-50 text-indigo-900 dark:bg-indigo-950 dark:text-indigo-100" : "border-zinc-300 dark:border-zinc-700"}`}>
                      <input type="checkbox" className="sr-only" checked={checked} onChange={() => setEmploymentTypes((items) => checked ? items.filter((item) => item !== option.value) : [...items, option.value])} />
                      {option.label}
                    </label>
                  );
                })}
              </div>
            </fieldset>

            <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
              <FormField label="Min comp (LPA)" htmlFor="search-comp-min"><input id="search-comp-min" type="number" min={0} value={compMin} onChange={(event) => setCompMin(event.target.value)} className={inputClasses} /></FormField>
              <FormField label="Max comp (LPA)" htmlFor="search-comp-max"><input id="search-comp-max" type="number" min={0} value={compMax} onChange={(event) => setCompMax(event.target.value)} className={inputClasses} /></FormField>
              <FormField label="Max posting age" htmlFor="search-max-age"><input id="search-max-age" type="number" min={1} max={365} value={maxAgeDays} onChange={(event) => setMaxAgeDays(event.target.value)} className={inputClasses} /></FormField>
              <FormField label="Country code" htmlFor="search-country"><input id="search-country" maxLength={2} value={country} onChange={(event) => setCountry(event.target.value.toLowerCase())} className={inputClasses} /></FormField>
            </div>

            <div className="grid gap-5 sm:grid-cols-2">
              <FormField label="Company pack" htmlFor="search-pack"><select id="search-pack" value={pack} onChange={(event) => setPack(event.target.value)} className={inputClasses}><option value="backend_india">Backend · India + remote</option></select></FormField>
              <FormField label="Cadence preference" htmlFor="search-cadence" hint="Only Run now works today. Other cadences are saved for the upcoming scan worker.">
                <select id="search-cadence" value={cadence} onChange={(event) => setCadence(event.target.value as ScheduleCadence)} className={inputClasses}>
                  <option value="manual">Manual · Run when I choose</option>
                  <option value="daily">Daily · Not automated yet</option>
                  <option value="weekdays">Weekdays · Not automated yet</option>
                  <option value="weekly">Weekly · Not automated yet</option>
                </select>
              </FormField>
            </div>

            {cadence !== "manual" ? (
              <div className="space-y-4 rounded-lg border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/30">
                <p className="text-sm text-amber-900 dark:text-amber-100"><strong>Preference only:</strong> automatic scans are not connected yet. You can still use Run now.</p>
                <div className="grid gap-4 sm:grid-cols-2">
                  <FormField label="Timezone" htmlFor="search-timezone"><input id="search-timezone" value={timezone} onChange={(event) => setTimezone(event.target.value)} className={inputClasses} /></FormField>
                  <FormField label="Local time" htmlFor="search-time"><input id="search-time" type="time" step={60} value={localTime} onChange={(event) => setLocalTime(event.target.value)} className={inputClasses} /></FormField>
                </div>
                {cadence === "weekly" ? (
                  <fieldset><legend className="text-sm font-medium">Days</legend><div className="mt-2 flex flex-wrap gap-2">{WEEKDAYS.map((day) => { const checked = days.includes(day.value); return <label key={day.value} className={`cursor-pointer rounded-full border px-3 py-2 text-xs ${checked ? "border-amber-600 bg-white dark:bg-zinc-900" : "border-amber-300 dark:border-amber-800"}`}><input type="checkbox" className="sr-only" checked={checked} onChange={() => setDays((items) => checked ? items.filter((item) => item !== day.value) : [...items, day.value])} />{day.label}</label>; })}</div></fieldset>
                ) : null}
              </div>
            ) : null}

            <label className="flex items-start gap-3 rounded-lg border border-zinc-200 p-4 text-sm dark:border-zinc-800">
              <input type="checkbox" checked={useSelfRag} onChange={(event) => setUseSelfRag(event.target.checked)} className="mt-1" />
              <span><strong>Use high-scoring past outreach examples</strong><br /><span className="text-zinc-500">Helps draft style when relevant examples exist; it does not reuse unapproved personal claims.</span></span>
            </label>
            <label className="flex items-start gap-3 text-sm">
              <input type="checkbox" checked={searchActive} onChange={(event) => setSearchActive(event.target.checked)} className="mt-1" />
              <span><strong>Keep this search active</strong><br /><span className="text-zinc-500">Inactive searches remain saved but cannot be prepared with Run now.</span></span>
            </label>
            {message ? <StatusMessage kind={message.kind}>{message.text}</StatusMessage> : null}
            <div className="flex flex-wrap gap-3">
              <button type="submit" disabled={pending} className={primaryButtonClasses}>{pending ? "Saving…" : editingId ? "Save search changes" : "Save search"}</button>
              {editingId ? <button type="button" onClick={cancelEdit} className={secondaryButtonClasses}>Cancel edit</button> : null}
              {message?.kind === "error" ? <button type="button" onClick={() => void reload()} className={secondaryButtonClasses}>Reload saved data</button> : null}
            </div>
          </form>
        )}
      </WorkspaceSection>

      <WorkspaceSection
        eyebrow="Ready when you are"
        title="Your saved searches"
        description="Run now prepares the exact resume and criteria for review on the existing hunt form. It never launches without your provider consent."
      >
        {searches.length === 0 ? (
          <div className="rounded-lg border border-dashed border-zinc-300 p-6 text-center dark:border-zinc-700"><p className="text-sm font-medium">No saved searches yet</p><p className="mt-1 text-sm text-zinc-500">Create one above, or keep using New hunt for one-off searches.</p></div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {searches.map((search) => {
              const track = tracks.find((item) => item.id === search.career_track_id);
              const resume = search.resume_version_id ? resumes.find((item) => item.id === search.resume_version_id) : profile?.data.base_resume;
              return (
                <article key={search.id} className="rounded-xl border border-zinc-200 p-5 dark:border-zinc-800">
                  <div className="flex items-start justify-between gap-3"><div><h3 className="font-semibold">{search.name}</h3><p className="mt-1 text-xs text-zinc-500">{track?.name ?? "Missing career target"} · {resume?.label ?? "Missing resume"}</p></div><span className={`rounded-full px-2 py-1 text-xs ${search.active ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300" : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800"}`}>{search.active ? "Active" : "Inactive"}</span></div>
                  <p className="mt-4 text-sm text-zinc-700 dark:text-zinc-300">{search.criteria.role_keywords.join(", ")}</p>
                  <dl className="mt-3 grid grid-cols-2 gap-3 text-xs"><div><dt className="text-zinc-500">Locations</dt><dd className="mt-1">{search.criteria.location.join(", ")}</dd></div><div><dt className="text-zinc-500">Cadence</dt><dd className="mt-1 capitalize">{search.schedule.cadence}{search.schedule.cadence !== "manual" ? " · preference only" : ""}</dd></div><div><dt className="text-zinc-500">Last scan</dt><dd className="mt-1">{formatDate(search.last_scan_at)}</dd></div><div><dt className="text-zinc-500">Next automatic scan</dt><dd className="mt-1">Not connected yet</dd></div></dl>
                  {runError[search.id] ? <div className="mt-3"><StatusMessage kind="error">{runError[search.id]}</StatusMessage></div> : null}
                  <div className="mt-4 flex flex-wrap gap-2">
                    <button type="button" disabled={runningId === search.id} onClick={() => void runNow(search)} className={primaryButtonClasses}>{runningId === search.id ? "Preparing…" : "Run now"}</button>
                    <button type="button" onClick={() => editSearch(search)} className={secondaryButtonClasses}>Edit</button>
                    <button type="button" disabled={deletingId === search.id} onClick={() => void removeSearch(search)} className={secondaryButtonClasses}>{deletingId === search.id ? "Deleting…" : "Delete"}</button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </WorkspaceSection>
    </div>
  );
}

function blockerLabel(value: string): string {
  const labels: Record<string, string> = {
    profile_missing: "Complete About you first.",
    base_resume_missing: "Add a base resume first.",
    selected_resume_missing: "The selected resume is unavailable; choose another version.",
    career_track_inactive: "Reactivate or replace the career target.",
    saved_search_inactive: "This saved search is inactive.",
  };
  return labels[value] ?? "This search needs attention before it can run.";
}

function titleCase(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatList(items: string[]): string {
  if (items.length <= 1) return items[0] ?? "profile setup";
  if (items.length === 2) return `${items[0]} and ${items[1]}`;
  return `${items.slice(0, -1).join(", ")}, and ${items.at(-1)}`;
}
