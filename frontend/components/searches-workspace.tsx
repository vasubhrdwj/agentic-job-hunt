"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { createOpportunityScan } from "@/lib/opportunity-api";
import {
  createIdempotencyKey,
  createSavedSearch,
  deleteSavedSearch,
  getCandidateProfile,
  getOwnerHealth,
  listCareerTracks,
  listResumeVersions,
  listSavedSearches,
  updateSavedSearch,
  WorkspaceApiError,
} from "@/lib/workspace-api";
import type { RoleScanCapability } from "@/lib/workspace-api";
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
import { hasMeaningfulCandidateProfile } from "@/lib/workspace-types";
import {
  careerTrackSearchPrefill,
  preferredSavedSearchSeniority,
} from "@/lib/search-defaults";
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
  const [roleScanCapability, setRoleScanCapability] =
    useState<RoleScanCapability | null>(null);
  const [roleScanChecking, setRoleScanChecking] = useState(true);

  const [name, setName] = useState("");
  const [trackId, setTrackId] = useState("");
  const [resumeId, setResumeId] = useState("");
  const [keywords, setKeywords] = useState("");
  const [locations, setLocations] = useState("");
  const [seniority, setSeniority] = useState<Seniority>("junior");
  const [employmentTypes, setEmploymentTypes] = useState<
    Exclude<EmploymentType, "unknown">[]
  >(["full_time"]);
  const [compMin, setCompMin] = useState("");
  const [compMax, setCompMax] = useState("");
  const [maxAgeDays, setMaxAgeDays] = useState("45");
  const [country, setCountry] = useState("in");
  const [pack, setPack] = useState("backend_india");
  const [useSelfRag, setUseSelfRag] = useState(false);
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
  const scanKeys = useRef<Record<string, string>>({});
  const keywordsTouched = useRef(false);
  const locationsTouched = useRef(false);

  const refreshRoleScanCapability = useCallback(async () => {
    setRoleScanChecking(true);
    try {
      const health = await getOwnerHealth();
      setRoleScanCapability(health.capabilities.role_scan);
    } catch {
      setRoleScanCapability({
        available: false,
        reason: "health_unavailable",
        fresh_worker_count: 0,
        compatible_worker_count: 0,
      });
    } finally {
      setRoleScanChecking(false);
    }
  }, []);

  const reload = useCallback(async () => {
    setLoadError(null);
    void refreshRoleScanCapability();
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
      const selectedTrack =
        nextTracks.find((track) => track.id === trackId) ??
        nextTracks.find((track) => track.active);
      setTrackId(selectedTrack?.id ?? "");
      if (selectedTrack) {
        setSeniority((current) =>
          selectedTrack.seniority_levels.includes(current)
            ? current
            : preferredSavedSearchSeniority(
                selectedTrack.seniority_levels,
                nextProfile?.data.years_of_experience,
              ),
        );
        if (!editingId && !keywordsTouched.current) {
          setKeywords(careerTrackSearchPrefill(selectedTrack.role_families));
        }
        if (!editingId && !locationsTouched.current) {
          setLocations(careerTrackSearchPrefill(selectedTrack.target_locations));
        }
      }
    } catch (error) {
      setLoadError(errorText(error, "Unable to load saved searches."));
    } finally {
      setLoading(false);
    }
  }, [editingId, refreshRoleScanCapability, trackId]);

  useEffect(() => {
    let active = true;
    void refreshRoleScanCapability();
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
        const activeTrack = nextTracks.find((track) => track.active);
        setTrackId(activeTrack?.id || "");
        if (activeTrack) {
          setSeniority(
            preferredSavedSearchSeniority(
              activeTrack.seniority_levels,
              nextProfile?.data.years_of_experience,
            ),
          );
          if (!keywordsTouched.current) {
            setKeywords(careerTrackSearchPrefill(activeTrack.role_families));
          }
          if (!locationsTouched.current) {
            setLocations(careerTrackSearchPrefill(activeTrack.target_locations));
          }
        }
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
  }, [refreshRoleScanCapability]);

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
      setCompMin("");
      setCompMax("");
      setUseSelfRag(false);
      setEditingId(null);
      setMessage({
        kind: "success",
        text:
          editing
            ? "Saved search updated."
            : cadence === "manual"
            ? "Saved search created. Use Scan roles whenever you are ready."
            : "Saved search and automatic cadence created. The background service will scan when it is awake; Scan roles remains available anytime.",
      });
      await reload();
    } catch (error) {
      setMessage({ kind: "error", text: errorText(error, "Unable to save the search.") });
    } finally {
      setPending(false);
    }
  }

  function editSearch(search: SavedSearch) {
    keywordsTouched.current = true;
    locationsTouched.current = true;
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
      setSeniority(
        preferredSavedSearchSeniority(
          nextTrack.seniority_levels,
          profile?.data.years_of_experience,
        ),
      );
    }
    if (nextTrack && !editingId && !keywordsTouched.current) {
      setKeywords(careerTrackSearchPrefill(nextTrack.role_families));
    }
    if (nextTrack && !editingId && !locationsTouched.current) {
      setLocations(careerTrackSearchPrefill(nextTrack.target_locations));
    }
  }

  function cancelEdit() {
    setEditingId(null);
    setName("");
    setCompMin("");
    setCompMax("");
    setUseSelfRag(false);
    keywordsTouched.current = false;
    locationsTouched.current = false;
    const selectedTrack = tracks.find((track) => track.id === trackId);
    setKeywords(careerTrackSearchPrefill(selectedTrack?.role_families ?? []));
    setLocations(careerTrackSearchPrefill(selectedTrack?.target_locations ?? []));
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

  async function scanRoles(search: SavedSearch) {
    if (roleScanChecking || roleScanCapability?.available !== true) {
      setRunError((current) => ({
        ...current,
        [search.id]:
          "Role scans are paused until the background scan service is ready. Check again and retry.",
      }));
      return;
    }
    setRunningId(search.id);
    setRunError((current) => ({ ...current, [search.id]: "" }));
    try {
      scanKeys.current[search.id] ??= createIdempotencyKey(`scan:${search.id}`);
      const scan = await createOpportunityScan(
        search.id,
        search.version,
        scanKeys.current[search.id],
      );
      delete scanKeys.current[search.id];
      router.push(`/today?scan=${encodeURIComponent(scan.id)}`);
    } catch (error) {
      if (
        error instanceof WorkspaceApiError &&
        error.code === "scan_worker_unavailable"
      ) {
        await refreshRoleScanCapability();
      }
      setRunError((current) => ({
        ...current,
        [search.id]: errorText(error, "Unable to start this role scan."),
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
    !profile || !hasMeaningfulCandidateProfile(profile.data) ? "About you" : null,
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
                <input id="search-name" value={name} onChange={(event) => setName(event.target.value)} className={inputClasses} placeholder="Backend roles · India" />
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
              <input id="search-keywords" value={keywords} onChange={(event) => { keywordsTouched.current = true; setKeywords(event.target.value); }} className={inputClasses} placeholder="backend engineer, platform engineer, distributed systems" />
            </FormField>
            <FormField label="Locations" htmlFor="search-locations" hint="Comma-separated">
              <input id="search-locations" value={locations} onChange={(event) => { locationsTouched.current = true; setLocations(event.target.value); }} className={inputClasses} placeholder="Remote-India, Bengaluru, Hyderabad" />
            </FormField>

            <fieldset>
              <legend className="text-sm font-medium">Employment types</legend>
              <div className="mt-2 flex flex-wrap gap-2">
                {EMPLOYMENT_TYPES.map((option) => {
                  const checked = employmentTypes.includes(option.value);
                  return (
                    <label key={option.value} className={`cursor-pointer rounded-full border px-3 py-2 text-xs font-medium focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-indigo-600 dark:focus-within:outline-indigo-400 ${checked ? "border-indigo-500 bg-indigo-50 text-indigo-900 dark:bg-indigo-950 dark:text-indigo-100" : "border-zinc-300 dark:border-zinc-700"}`}>
                      <input type="checkbox" className="sr-only" checked={checked} onChange={() => setEmploymentTypes((items) => checked ? items.filter((item) => item !== option.value) : [...items, option.value])} />
                      {option.label}
                    </label>
                  );
                })}
              </div>
            </fieldset>

            <div className="grid gap-5 sm:grid-cols-2">
              <FormField label="Max posting age" htmlFor="search-max-age"><input id="search-max-age" type="number" min={1} max={365} value={maxAgeDays} onChange={(event) => setMaxAgeDays(event.target.value)} className={inputClasses} /></FormField>
              <FormField label="Country code" htmlFor="search-country"><input id="search-country" maxLength={2} value={country} onChange={(event) => setCountry(event.target.value.toLowerCase())} className={inputClasses} /></FormField>
            </div>

            <div className="grid gap-5 sm:grid-cols-2">
              <FormField label="Company pack" htmlFor="search-pack"><select id="search-pack" value={pack} onChange={(event) => setPack(event.target.value)} className={inputClasses}><option value="backend_india">Backend · India + remote</option></select></FormField>
              <FormField label="Scan cadence" htmlFor="search-cadence" hint="Scheduled scans run at this local time whenever the background service is awake.">
                <select id="search-cadence" value={cadence} onChange={(event) => setCadence(event.target.value as ScheduleCadence)} className={inputClasses}>
                  <option value="manual">Manual · Run when I choose</option>
                  <option value="daily">Daily</option>
                  <option value="weekdays">Weekdays</option>
                  <option value="weekly">Weekly</option>
                </select>
              </FormField>
            </div>

            {cadence !== "manual" ? (
              <div className="space-y-4 rounded-lg border border-indigo-200 bg-indigo-50 p-4 dark:border-indigo-900 dark:bg-indigo-950/30">
                <p className="text-sm text-indigo-900 dark:text-indigo-100"><strong>Automatic while awake:</strong> the free hosted service can sleep, so a scan may begin after you next open the app. Use Scan roles whenever timing matters.</p>
                <div className="grid gap-4 sm:grid-cols-2">
                  <FormField label="Timezone" htmlFor="search-timezone"><input id="search-timezone" value={timezone} onChange={(event) => setTimezone(event.target.value)} className={inputClasses} /></FormField>
                  <FormField label="Local time" htmlFor="search-time"><input id="search-time" type="time" step={60} value={localTime} onChange={(event) => setLocalTime(event.target.value)} className={inputClasses} /></FormField>
                </div>
                {cadence === "weekly" ? (
                  <fieldset><legend className="text-sm font-medium">Days</legend><div className="mt-2 flex flex-wrap gap-2">{WEEKDAYS.map((day) => { const checked = days.includes(day.value); return <label key={day.value} className={`cursor-pointer rounded-full border px-3 py-2 text-xs focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-indigo-700 dark:focus-within:outline-indigo-300 ${checked ? "border-indigo-600 bg-white dark:bg-zinc-900" : "border-indigo-300 dark:border-indigo-800"}`}><input type="checkbox" className="sr-only" checked={checked} onChange={() => setDays((items) => checked ? items.filter((item) => item !== day.value) : [...items, day.value])} />{day.label}</label>; })}</div></fieldset>
                ) : null}
              </div>
            ) : null}

            <label className="flex items-start gap-3 text-sm">
              <input type="checkbox" checked={searchActive} onChange={(event) => setSearchActive(event.target.checked)} className="mt-1" />
              <span><strong>Keep this search active</strong><br /><span className="text-zinc-500">Inactive searches remain saved but cannot start a role scan.</span></span>
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
        description="Scan roles searches configured job sources and saves deduplicated results to Today without contacts, drafting, or model calls. The retired legacy hunt remains read-only."
      >
        <div className="space-y-4">
          {roleScanChecking ? (
            <StatusMessage kind="info">
              Checking whether the role-scan service is ready…
            </StatusMessage>
          ) : roleScanCapability?.available ? null : (
            <StatusMessage kind="info">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="font-medium">Role scans are temporarily paused.</p>
                  <p className="mt-1">
                    The scan service may still be waking up. Your saved searches
                    are safe; check again in a moment.
                  </p>
                </div>
                <button
                  type="button"
                  className={secondaryButtonClasses}
                  onClick={() => void refreshRoleScanCapability()}
                >
                  Check again
                </button>
              </div>
            </StatusMessage>
          )}
          {searches.length === 0 ? (
            <div className="rounded-lg border border-dashed border-zinc-300 p-6 text-center dark:border-zinc-700"><p className="text-sm font-medium">No saved searches yet</p><p className="mt-1 text-sm text-zinc-500">Create one above, then use Scan roles to find opportunities.</p></div>
          ) : (
            <div className="grid gap-4 lg:grid-cols-2">
            {searches.map((search) => {
              const track = tracks.find((item) => item.id === search.career_track_id);
              const resume = search.resume_version_id ? resumes.find((item) => item.id === search.resume_version_id) : profile?.data.base_resume;
              return (
                <article key={search.id} className="rounded-xl border border-zinc-200 p-5 dark:border-zinc-800">
                  <div className="flex items-start justify-between gap-3"><div><h3 className="font-semibold">{search.name}</h3><p className="mt-1 text-xs text-zinc-500">{track?.name ?? "Missing career target"} · {resume?.label ?? "Missing resume"}</p></div><span className={`rounded-full px-2 py-1 text-xs ${search.active ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300" : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800"}`}>{search.active ? "Active" : "Inactive"}</span></div>
                  <p className="mt-4 text-sm text-zinc-700 dark:text-zinc-300">{search.criteria.role_keywords.join(", ")}</p>
                  <dl className="mt-3 grid grid-cols-2 gap-3 text-xs"><div><dt className="text-zinc-500">Locations</dt><dd className="mt-1">{search.criteria.location.join(", ")}</dd></div><div><dt className="text-zinc-500">Cadence</dt><dd className="mt-1 capitalize">{search.schedule.cadence}</dd></div><div><dt className="text-zinc-500">Last scan</dt><dd className="mt-1">{formatDate(search.last_scan_at)}</dd></div><div><dt className="text-zinc-500">Next automatic scan</dt><dd className="mt-1">{search.schedule.cadence === "manual" ? "Manual" : formatDate(search.next_scan_at)}</dd></div></dl>
                  {!search.active ? (
                    <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
                      This search is paused. Review its criteria and schedule, then edit it and turn “Keep this search active” back on when it is ready.
                    </p>
                  ) : null}
                  {runError[search.id] ? <div className="mt-3"><StatusMessage kind="error">{runError[search.id]}</StatusMessage></div> : null}
                  <div className="mt-4 flex flex-wrap gap-2">
                    <button
                      type="button"
                      disabled={
                        !search.active ||
                        runningId === search.id ||
                        roleScanChecking ||
                        roleScanCapability?.available !== true
                      }
                      onClick={() => void scanRoles(search)}
                      className={primaryButtonClasses}
                    >
                      {runningId === search.id
                        ? "Starting scan…"
                        : roleScanChecking
                          ? "Checking scan service…"
                          : roleScanCapability?.available
                            ? "Scan roles"
                            : "Scan paused"}
                    </button>
                    <button type="button" onClick={() => editSearch(search)} className={secondaryButtonClasses}>Edit</button>
                    {search.last_scan_at ? null : (
                      <button type="button" disabled={deletingId === search.id} onClick={() => void removeSearch(search)} className={secondaryButtonClasses}>{deletingId === search.id ? "Deleting…" : "Delete"}</button>
                    )}
                  </div>
                  {search.last_scan_at ? (
                    <p className="mt-3 text-xs leading-5 text-zinc-500">
                      This search is kept with its scan history. To stop using it, choose Edit and turn off <span className="font-medium text-zinc-700 dark:text-zinc-300">Keep this search active</span>.
                    </p>
                  ) : null}
                </article>
              );
            })}
            </div>
          )}
        </div>
      </WorkspaceSection>
    </div>
  );
}

function titleCase(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatList(items: string[]): string {
  if (items.length <= 1) return items[0] ?? "profile setup";
  if (items.length === 2) return `${items[0]} and ${items[1]}`;
  return `${items.slice(0, -1).join(", ")}, and ${items.at(-1)}`;
}
