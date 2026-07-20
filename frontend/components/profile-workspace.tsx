"use client";

import {
  type DragEvent,
  FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  createCareerTrack,
  createEvidence,
  createIdempotencyKey,
  createResumeVersion,
  getCandidateProfile,
  listCareerTracks,
  listEvidence,
  listResumeVersions,
  makeBaseResume,
  reviewEvidence,
  saveCandidateProfile,
  updateCareerTrack,
  uploadResumeVersion,
} from "@/lib/workspace-api";
import {
  formatResumeFileSize,
  profileGapLabels,
  RESUME_FILE_ACCEPT,
  resumeImportPresentation,
  resumeLabelFromFilename,
  validateResumeFile,
} from "@/lib/resume-upload";
import type {
  AchievementEvidence,
  AuthorizationStatus,
  CandidateProfile,
  CandidateProfileWrite,
  CareerPriorities,
  CareerTrack,
  ResumeImportReport,
  ResumeVersionSummary,
  Versioned,
  WorkAuthorization,
  WorkMode,
} from "@/lib/workspace-types";
import {
  hasMeaningfulCandidateProfile,
  parseYearsOfExperienceInput,
} from "@/lib/workspace-types";
import { careerTrackMatchesPayload } from "@/lib/career-track-update";
import { DEFAULT_CAREER_TARGET_SENIORITIES } from "@/lib/search-defaults";
import type { EmploymentType, Seniority } from "@/lib/types";
import {
  errorText,
  FormField,
  inputClasses,
  primaryButtonClasses,
  secondaryButtonClasses,
  splitList,
  StatusMessage,
  textareaClasses,
  WorkspaceSection,
} from "./workspace-ui";

const SENIORITIES: Seniority[] = ["junior", "mid", "senior", "staff"];
const WORK_MODES: Array<{ value: WorkMode; label: string }> = [
  { value: "remote", label: "Remote" },
  { value: "hybrid", label: "Hybrid" },
  { value: "onsite", label: "On-site" },
];
const EMPLOYMENT_TYPES: Array<{
  value: Exclude<EmploymentType, "unknown">;
  label: string;
}> = [
  { value: "full_time", label: "Full-time" },
  { value: "contract", label: "Contract" },
  { value: "intern", label: "Internship" },
];
const AUTHORIZATION_OPTIONS: Array<{ value: AuthorizationStatus; label: string }> = [
  { value: "citizen", label: "Citizen" },
  { value: "permanent_resident", label: "Permanent resident" },
  { value: "work_permit", label: "Work permit" },
  { value: "needs_sponsorship", label: "Needs sponsorship" },
  { value: "not_authorized", label: "Not currently authorized" },
  { value: "other", label: "Other" },
];
const DEFAULT_PRIORITIES: CareerPriorities = {
  compensation: 3,
  scope: 3,
  learning: 3,
  company_quality: 3,
  flexibility: 3,
};

export function ProfileWorkspace() {
  const [profile, setProfile] = useState<Versioned<CandidateProfile> | null>(null);
  const [resumes, setResumes] = useState<ResumeVersionSummary[]>([]);
  const [tracks, setTracks] = useState<CareerTrack[]>([]);
  const [evidence, setEvidence] = useState<AchievementEvidence[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [currentTitle, setCurrentTitle] = useState("");
  const [currentLocation, setCurrentLocation] = useState("");
  const [yearsOfExperience, setYearsOfExperience] = useState("");
  const [careerThesis, setCareerThesis] = useState("");
  const [noticeDays, setNoticeDays] = useState("");
  const [workModes, setWorkModes] = useState<WorkMode[]>([]);
  const [employmentTypes, setEmploymentTypes] = useState<
    Exclude<EmploymentType, "unknown">[]
  >(["full_time"]);
  const [authorizations, setAuthorizations] = useState<WorkAuthorization[]>([]);
  const [profilePending, setProfilePending] = useState(false);
  const [profileMessage, setProfileMessage] = useState<{
    kind: "error" | "success";
    text: string;
  } | null>(null);

  const [resumeFile, setResumeFile] = useState<File | null>(null);
  const [uploadLabel, setUploadLabel] = useState("");
  const [uploadSetAsBase, setUploadSetAsBase] = useState(true);
  const [uploadPending, setUploadPending] = useState(false);
  const [uploadDragging, setUploadDragging] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadWarning, setUploadWarning] = useState<string | null>(null);
  const [uploadReport, setUploadReport] = useState<ResumeImportReport | null>(null);
  const uploadInput = useRef<HTMLInputElement | null>(null);
  const uploadKey = useRef<string | null>(null);

  const [resumeLabel, setResumeLabel] = useState("Base resume");
  const [resumeContent, setResumeContent] = useState("");
  const [setAsBase, setSetAsBase] = useState(true);
  const [resumePending, setResumePending] = useState(false);
  const [basePendingId, setBasePendingId] = useState<string | null>(null);
  const [resumeMessage, setResumeMessage] = useState<{
    kind: "error" | "success";
    text: string;
  } | null>(null);
  const resumeKey = useRef<string | null>(null);
  const baseKeys = useRef<Record<string, string>>({});

  const [trackName, setTrackName] = useState("");
  const [roleFamilies, setRoleFamilies] = useState("");
  const [seniorities, setSeniorities] = useState<Seniority[]>([
    ...DEFAULT_CAREER_TARGET_SENIORITIES,
  ]);
  const [targetLocations, setTargetLocations] = useState("");
  const [priorities, setPriorities] = useState(DEFAULT_PRIORITIES);
  const [trackActive, setTrackActive] = useState(true);
  const [editingTrackId, setEditingTrackId] = useState<string | null>(null);
  const [trackPending, setTrackPending] = useState(false);
  const [trackMessage, setTrackMessage] = useState<{
    kind: "error" | "success";
    text: string;
  } | null>(null);
  const trackKey = useRef<string | null>(null);

  const [evidenceStatement, setEvidenceStatement] = useState("");
  const [evidenceSkills, setEvidenceSkills] = useState("");
  const [evidenceResumeId, setEvidenceResumeId] = useState("");
  const [evidencePending, setEvidencePending] = useState(false);
  const [evidenceReviewId, setEvidenceReviewId] = useState<string | null>(null);
  const [evidenceMessage, setEvidenceMessage] = useState<{
    kind: "error" | "success";
    text: string;
  } | null>(null);
  const evidenceKey = useRef<string | null>(null);

  const hydrateProfile = useCallback((value: CandidateProfile | null) => {
    setCurrentTitle(value?.current_title ?? "");
    setCurrentLocation(value?.current_location ?? "");
    setYearsOfExperience(value?.years_of_experience?.toString() ?? "");
    setCareerThesis(value?.career_thesis ?? "");
    setNoticeDays(value?.notice_period_days?.toString() ?? "");
    setWorkModes(value?.work_modes ?? []);
    setEmploymentTypes(value?.employment_types ?? ["full_time"]);
    setAuthorizations(value?.work_authorizations ?? []);
  }, []);

  const reload = useCallback(async () => {
    setLoadError(null);
    try {
      const [nextProfile, nextResumes, nextTracks, nextEvidence] = await Promise.all([
        getCandidateProfile(),
        listResumeVersions(),
        listCareerTracks(),
        listEvidence(),
      ]);
      setProfile(nextProfile);
      setResumes(nextResumes);
      setTracks(nextTracks);
      setEvidence(nextEvidence);
      hydrateProfile(nextProfile?.data ?? null);
    } catch (error) {
      setLoadError(errorText(error, "Unable to load your profile."));
    } finally {
      setLoading(false);
    }
  }, [hydrateProfile]);

  useEffect(() => {
    let active = true;
    Promise.all([
      getCandidateProfile(),
      listResumeVersions(),
      listCareerTracks(),
      listEvidence(),
    ])
      .then(([nextProfile, nextResumes, nextTracks, nextEvidence]) => {
        if (!active) return;
        setProfile(nextProfile);
        setResumes(nextResumes);
        setTracks(nextTracks);
        setEvidence(nextEvidence);
        hydrateProfile(nextProfile?.data ?? null);
      })
      .catch((error) => {
        if (active) setLoadError(errorText(error, "Unable to load your profile."));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [hydrateProfile]);

  async function refreshResumeData() {
    const [nextProfile, nextResumes, nextEvidence] = await Promise.all([
      getCandidateProfile(),
      listResumeVersions(),
      listEvidence(),
    ]);
    setProfile(nextProfile);
    setResumes(nextResumes);
    setEvidence(nextEvidence);
    hydrateProfile(nextProfile?.data ?? null);
  }

  function selectResumeFile(file: File | null) {
    if (!file) return;
    const validationError = validateResumeFile(file);
    if (validationError) {
      setResumeFile(null);
      setUploadReport(null);
      setUploadWarning(null);
      setUploadError(validationError);
      if (uploadInput.current) uploadInput.current.value = "";
      return;
    }
    setResumeFile(file);
    setUploadLabel(resumeLabelFromFilename(file.name));
    uploadKey.current = null;
    setUploadReport(null);
    setUploadWarning(null);
    setUploadError(null);
  }

  function clearResumeFile() {
    setResumeFile(null);
    setUploadLabel("");
    uploadKey.current = null;
    setUploadError(null);
    if (uploadInput.current) uploadInput.current.value = "";
  }

  function dropResume(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setUploadDragging(false);
    if (uploadPending) return;
    if (event.dataTransfer.files.length !== 1) {
      setUploadError("Drop one resume file at a time.");
      return;
    }
    selectResumeFile(event.dataTransfer.files.item(0));
  }

  async function uploadResume(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!resumeFile) {
      setUploadError("Choose a PDF, DOCX, or TXT resume first.");
      return;
    }
    const validationError = validateResumeFile(resumeFile);
    if (validationError) {
      setUploadError(validationError);
      return;
    }

    setUploadPending(true);
    setUploadError(null);
    setUploadWarning(null);
    setUploadReport(null);
    uploadKey.current ??= createIdempotencyKey("resume-upload");
    try {
      const report = await uploadResumeVersion({
        file: resumeFile,
        label: uploadLabel.trim() || undefined,
        setAsBase: uploadSetAsBase || resumes.length === 0,
        idempotencyKey: uploadKey.current,
      });
      uploadKey.current = null;
      setUploadReport(report);
      setResumeFile(null);
      setUploadLabel("");
      if (uploadInput.current) uploadInput.current.value = "";
      try {
        await refreshResumeData();
      } catch (error) {
        const detail = errorText(error, "Reload the page to see the latest profile.");
        setUploadWarning(
          `Your resume was imported, but the latest profile could not be refreshed. ${detail}`,
        );
      }
    } catch (error) {
      setUploadError(errorText(error, "Unable to import this resume."));
    } finally {
      setUploadPending(false);
    }
  }

  async function saveAboutYou(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (employmentTypes.length === 0) {
      setProfileMessage({ kind: "error", text: "Choose at least one employment type." });
      return;
    }
    const authorizationCountries = authorizations.map((item) => item.country_code.trim());
    if (authorizationCountries.some((code) => !/^[A-Za-z]{2}$/.test(code))) {
      setProfileMessage({ kind: "error", text: "Use a two-letter code for every authorization country." });
      return;
    }
    if (new Set(authorizationCountries.map((code) => code.toUpperCase())).size !== authorizationCountries.length) {
      setProfileMessage({ kind: "error", text: "Add each work-authorization country only once." });
      return;
    }
    let experienceYears: number | null;
    try {
      experienceYears = parseYearsOfExperienceInput(yearsOfExperience);
    } catch {
      setProfileMessage({
        kind: "error",
        text: "Enter total professional experience between 0 and 60 years.",
      });
      return;
    }
    const payload: CandidateProfileWrite = {
      current_title: currentTitle.trim() || null,
      current_location: currentLocation.trim() || null,
      years_of_experience: experienceYears,
      career_thesis: careerThesis.trim() || null,
      notice_period_days: noticeDays ? Number(noticeDays) : null,
      work_authorizations: authorizations,
      work_modes: workModes,
      employment_types: employmentTypes,
      onboarding_step: profile?.data.onboarding_step ?? "resume",
    };
    if (!hasMeaningfulCandidateProfile(payload)) {
      setProfileMessage({
        kind: "error",
        text: "Add at least one useful detail about you, such as your title, experience, location, work mode, authorization, notice period, or career direction.",
      });
      return;
    }
    setProfilePending(true);
    setProfileMessage(null);
    try {
      const saved = await saveCandidateProfile(
        payload,
        profile?.data.version ?? 0,
      );
      setProfile(saved);
      hydrateProfile(saved.data);
      setProfileMessage({ kind: "success", text: "About you is saved." });
    } catch (error) {
      setProfileMessage({ kind: "error", text: errorText(error, "Unable to save your profile.") });
    } finally {
      setProfilePending(false);
    }
  }

  async function addResume(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!resumeContent.trim()) {
      setResumeMessage({ kind: "error", text: "Paste your resume before saving." });
      return;
    }
    setResumePending(true);
    setResumeMessage(null);
    resumeKey.current ??= createIdempotencyKey("resume");
    try {
      await createResumeVersion(
        {
          label: resumeLabel.trim() || "Resume",
          content: resumeContent,
          source: "pasted",
          parent_resume_version_id: null,
          set_as_base: setAsBase || resumes.length === 0,
        },
        resumeKey.current,
      );
      resumeKey.current = null;
      setResumeContent("");
      setResumeLabel("Base resume");
      setResumeMessage({
        kind: "success",
        text: "Resume saved securely as a new immutable version.",
      });
      await refreshResumeData();
    } catch (error) {
      setResumeMessage({ kind: "error", text: errorText(error, "Unable to save the resume.") });
    } finally {
      setResumePending(false);
    }
  }

  async function chooseBase(resume: ResumeVersionSummary) {
    setBasePendingId(resume.id);
    setResumeMessage(null);
    baseKeys.current[resume.id] ??= createIdempotencyKey(`base:${resume.id}`);
    try {
      await makeBaseResume(resume, baseKeys.current[resume.id]);
      delete baseKeys.current[resume.id];
      setResumeMessage({ kind: "success", text: `${resume.label} is now your base resume.` });
      await refreshResumeData();
    } catch (error) {
      setResumeMessage({ kind: "error", text: errorText(error, "Unable to change the base resume.") });
    } finally {
      setBasePendingId(null);
    }
  }

  async function addTrack(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const roles = splitList(roleFamilies);
    const locations = splitList(targetLocations);
    if (!trackName.trim() || roles.length === 0 || locations.length === 0 || seniorities.length === 0) {
      setTrackMessage({
        kind: "error",
        text: "Name the target and add at least one role family, seniority, and location.",
      });
      return;
    }
    if (!Object.values(priorities).some((value) => value > 0)) {
      setTrackMessage({ kind: "error", text: "Set at least one career priority above zero." });
      return;
    }
    const payload = {
      name: trackName.trim(),
      role_families: roles,
      seniority_levels: seniorities,
      target_locations: locations,
      priorities,
      active: trackActive,
    };
    const editing = editingTrackId
      ? tracks.find((track) => track.id === editingTrackId) ?? null
      : null;
    setTrackPending(true);
    setTrackMessage(null);
    try {
      if (editing) {
        await updateCareerTrack(editing, payload);
      } else {
        trackKey.current ??= createIdempotencyKey("career-track");
        await createCareerTrack(payload, trackKey.current);
      }
      trackKey.current = null;
      setTrackName("");
      setRoleFamilies("");
      setSeniorities([...DEFAULT_CAREER_TARGET_SENIORITIES]);
      setTargetLocations("");
      setTrackActive(true);
      setEditingTrackId(null);
      setTrackMessage({
        kind: "success",
        text: editing ? "Career target updated." : "Career target saved.",
      });
      setTracks(await listCareerTracks());
    } catch (error) {
      if (editing) {
        try {
          const refreshedTracks = await listCareerTracks();
          setTracks(refreshedTracks);
          const refreshed = refreshedTracks.find((track) => track.id === editing.id);
          if (refreshed && careerTrackMatchesPayload(refreshed, payload)) {
            setTrackName("");
            setRoleFamilies("");
            setSeniorities([...DEFAULT_CAREER_TARGET_SENIORITIES]);
            setTargetLocations("");
            setTrackActive(true);
            setEditingTrackId(null);
            setTrackMessage({
              kind: "success",
              text: "Career target updated. Confirmed from the saved record.",
            });
            return;
          }
        } catch {
          // Preserve the original mutation error when confirmation is unavailable.
        }
      }
      setTrackMessage({ kind: "error", text: errorText(error, "Unable to save the career target.") });
    } finally {
      setTrackPending(false);
    }
  }

  function editTrack(track: CareerTrack) {
    setEditingTrackId(track.id);
    setTrackName(track.name);
    setRoleFamilies(track.role_families.join(", "));
    setSeniorities(track.seniority_levels);
    setTargetLocations(track.target_locations.join(", "));
    setPriorities({ ...track.priorities });
    setTrackActive(track.active);
    setTrackMessage(null);
  }

  function cancelTrackEdit() {
    setEditingTrackId(null);
    setTrackName("");
    setRoleFamilies("");
    setSeniorities([...DEFAULT_CAREER_TARGET_SENIORITIES]);
    setTargetLocations("");
    setTrackActive(true);
    setPriorities(DEFAULT_PRIORITIES);
    trackKey.current = null;
    setTrackMessage(null);
  }

  async function addEvidence(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!evidenceStatement.trim()) {
      setEvidenceMessage({ kind: "error", text: "Write the achievement you want to remember." });
      return;
    }
    setEvidencePending(true);
    setEvidenceMessage(null);
    evidenceKey.current ??= createIdempotencyKey("evidence");
    try {
      await createEvidence(
        {
          statement: evidenceStatement.trim(),
          source_resume_version_id: evidenceResumeId || null,
          source_excerpt: null,
          skills: splitList(evidenceSkills),
          origin: "owner_entered",
        },
        evidenceKey.current,
      );
      evidenceKey.current = null;
      setEvidenceStatement("");
      setEvidenceSkills("");
      setEvidenceMessage({
        kind: "success",
        text: "Achievement added as pending. Approve it before future reuse.",
      });
      setEvidence(await listEvidence());
    } catch (error) {
      setEvidenceMessage({ kind: "error", text: errorText(error, "Unable to add the achievement.") });
    } finally {
      setEvidencePending(false);
    }
  }

  async function setEvidenceState(
    item: AchievementEvidence,
    state: "approved" | "rejected" | "retired",
  ) {
    setEvidenceReviewId(item.id);
    setEvidenceMessage(null);
    try {
      await reviewEvidence(item, state);
      setEvidenceMessage({ kind: "success", text: "Achievement review saved." });
      setEvidence(await listEvidence());
    } catch (error) {
      setEvidenceMessage({ kind: "error", text: errorText(error, "Unable to review the achievement.") });
    } finally {
      setEvidenceReviewId(null);
    }
  }

  if (loading) {
    return <p role="status" className="text-sm text-zinc-500">Loading your private profile…</p>;
  }

  if (loadError) {
    return (
      <StatusMessage kind="error">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span>{loadError}</span>
          <button type="button" onClick={() => void reload()} className={secondaryButtonClasses}>
            Try again
          </button>
        </div>
      </StatusMessage>
    );
  }

  const baseResume =
    profile?.data.base_resume ?? resumes.find((resume) => resume.is_base) ?? null;
  const profileHighlights = [
    profile?.data.current_title?.trim() ?? "",
    profile?.data.current_location?.trim() ?? "",
    profile?.data.years_of_experience !== null && profile?.data.years_of_experience !== undefined
      ? `${profile.data.years_of_experience} years of experience`
      : "",
  ].filter(Boolean);
  const profileGaps = profileGapLabels({
    currentTitle: profile?.data.current_title ?? "",
    currentLocation: profile?.data.current_location ?? "",
    yearsOfExperience: profile?.data.years_of_experience?.toString() ?? "",
    careerThesis: profile?.data.career_thesis ?? "",
    noticeDays: profile?.data.notice_period_days?.toString() ?? "",
    workModeCount: profile?.data.work_modes.length ?? 0,
    authorizationCount: profile?.data.work_authorizations.length ?? 0,
  });

  return (
    <div className="space-y-8">
      <WorkspaceSection
        eyebrow="Step 1"
        title="Import your resume"
        description="Upload your existing resume once. The app extracts its text into an encrypted immutable version, fills facts it can verify, and discards the original file after reading it."
      >
        <div className="space-y-6">
          {baseResume ? (
            <StatusMessage kind="info">
              Current base: <strong>{baseResume.label}</strong> · {baseResume.character_count.toLocaleString()} characters
            </StatusMessage>
          ) : (
            <StatusMessage kind="info">Add a base resume so saved searches and ordinary new hunts can prefill it.</StatusMessage>
          )}

          <form onSubmit={uploadResume} className="space-y-4">
            <div
              onDragEnter={(event) => {
                event.preventDefault();
                if (!uploadPending) setUploadDragging(true);
              }}
              onDragOver={(event) => {
                event.preventDefault();
                event.dataTransfer.dropEffect = "copy";
              }}
              onDragLeave={() => setUploadDragging(false)}
              onDrop={dropResume}
              className={`rounded-xl border-2 border-dashed px-5 py-8 text-center transition ${
                uploadDragging
                  ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-950/30"
                  : "border-zinc-300 bg-zinc-50/70 dark:border-zinc-700 dark:bg-zinc-950/40"
              }`}
            >
              <input
                ref={uploadInput}
                id="resume-file"
                type="file"
                accept={RESUME_FILE_ACCEPT}
                className="sr-only"
                disabled={uploadPending}
                aria-describedby="resume-file-help"
                onChange={(event) => selectResumeFile(event.target.files?.item(0) ?? null)}
              />
              <p className="text-sm font-semibold">Drop your resume here</p>
              <p id="resume-file-help" className="mt-1 text-xs leading-5 text-zinc-500">
                PDF, DOCX, or TXT · 3 MB maximum
              </p>
              <button
                type="button"
                disabled={uploadPending}
                onClick={() => uploadInput.current?.click()}
                className={`${secondaryButtonClasses} mt-4`}
              >
                Choose a file
              </button>
            </div>

            {resumeFile ? (
              <div className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{resumeFile.name}</p>
                    <p className="mt-1 text-xs text-zinc-500">
                      {formatResumeFileSize(resumeFile.size)} · ready to import
                    </p>
                  </div>
                  <button
                    type="button"
                    disabled={uploadPending}
                    onClick={clearResumeFile}
                    className={secondaryButtonClasses}
                  >
                    Remove
                  </button>
                </div>
              </div>
            ) : null}

            {resumeFile ? (
              <details className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
                <summary className="cursor-pointer text-sm font-medium">Import options</summary>
                <div className="mt-4 grid gap-4 sm:grid-cols-2">
                  <FormField
                    label={<>Version label <span className="font-normal text-zinc-500">(optional)</span></>}
                    htmlFor="upload-resume-label"
                  >
                    <input
                      id="upload-resume-label"
                      value={uploadLabel}
                      onChange={(event) => {
                        setUploadLabel(event.target.value);
                        uploadKey.current = null;
                      }}
                      className={inputClasses}
                      placeholder="Backend resume · July 2026"
                    />
                  </FormField>
                  <label className="flex items-center gap-2 self-end pb-3 text-sm">
                    <input
                      type="checkbox"
                      checked={uploadSetAsBase || resumes.length === 0}
                      disabled={resumes.length === 0 || uploadPending}
                      onChange={(event) => {
                        setUploadSetAsBase(event.target.checked);
                        uploadKey.current = null;
                      }}
                    />
                    Use this as my base resume
                  </label>
                </div>
              </details>
            ) : null}

            {uploadPending ? (
              <div role="status" aria-live="polite" className="space-y-2">
                <p className="text-sm font-medium">Uploading and reading your resume…</p>
                <progress
                  aria-label="Resume import in progress"
                  className="h-2 w-full overflow-hidden rounded-full accent-indigo-600"
                />
                <p className="text-xs text-zinc-500">This usually takes a few seconds.</p>
              </div>
            ) : null}

            {uploadError ? <StatusMessage kind="error">{uploadError}</StatusMessage> : null}
            {uploadReport ? <ResumeImportSummary report={uploadReport} /> : null}
            {uploadWarning ? <StatusMessage kind="info">{uploadWarning}</StatusMessage> : null}

            <button
              type="submit"
              disabled={!resumeFile || uploadPending}
              className={primaryButtonClasses}
            >
              {uploadPending ? "Importing…" : "Import resume"}
            </button>
          </form>

          <details className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
            <summary className="cursor-pointer text-sm font-medium">
              Can’t upload? Paste plain text instead
            </summary>
            <p className="mt-2 text-xs leading-5 text-zinc-500">
              This fallback saves the text as a resume version, but it does not import profile details automatically.
            </p>
            <form onSubmit={addResume} className="mt-4 space-y-4">
              <FormField label="Version label" htmlFor="resume-label">
                <input id="resume-label" value={resumeLabel} onChange={(event) => setResumeLabel(event.target.value)} className={inputClasses} placeholder="Backend resume · July 2026" />
              </FormField>
              <FormField label="Resume text" htmlFor="resume-content" hint="Paste plain text. It is encrypted before durable storage.">
                <textarea id="resume-content" rows={14} value={resumeContent} onChange={(event) => setResumeContent(event.target.value)} className={`${textareaClasses} font-mono`} placeholder="Your name\nExperience…" />
              </FormField>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={setAsBase || resumes.length === 0} disabled={resumes.length === 0 || resumePending} onChange={(event) => setSetAsBase(event.target.checked)} />
                Use this as my base resume
              </label>
              <button type="submit" disabled={resumePending} className={primaryButtonClasses}>
                {resumePending ? "Encrypting and saving…" : "Save pasted resume"}
              </button>
            </form>
          </details>

          {resumeMessage ? <StatusMessage kind={resumeMessage.kind}>{resumeMessage.text}</StatusMessage> : null}

          <div>
            <h3 className="text-sm font-semibold">Saved versions</h3>
            {resumes.length === 0 ? (
              <p className="mt-2 text-sm text-zinc-500">No resume versions yet.</p>
            ) : (
              <ul className="mt-3 divide-y divide-zinc-200 rounded-lg border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
                {resumes.map((resume) => (
                  <li key={resume.id} className="flex flex-wrap items-center justify-between gap-3 p-4">
                    <div>
                      <p className="text-sm font-medium">{resume.label}{resume.is_base ? " · Base" : ""}</p>
                      <p className="mt-1 text-xs text-zinc-500">{resume.character_count.toLocaleString()} characters · version {resume.version}</p>
                    </div>
                    {!resume.is_base ? (
                      <button type="button" disabled={basePendingId === resume.id} onClick={() => void chooseBase(resume)} className={secondaryButtonClasses}>
                        {basePendingId === resume.id ? "Updating…" : "Make base"}
                      </button>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </WorkspaceSection>

      <WorkspaceSection
        eyebrow="Step 2"
        title="Review your profile"
        description="The resume importer fills factual details it can verify. Open this only to correct them or add preferences a resume usually cannot answer."
      >
        <div className="rounded-lg border border-zinc-200 bg-zinc-50/70 p-4 dark:border-zinc-800 dark:bg-zinc-950/40">
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-zinc-500">
            Profile summary
          </p>
          <p className="mt-2 text-sm leading-6">
            {profileHighlights.length > 0
              ? profileHighlights.join(" · ")
              : "No factual profile details have been imported yet."}
          </p>
          {profileGaps.length > 0 ? (
            <div className="mt-3 border-t border-zinc-200 pt-3 dark:border-zinc-800">
              <p className="text-xs text-zinc-500">Useful details still missing</p>
              <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Missing profile details">
                {profileGaps.map((detail) => (
                  <li key={detail} className="rounded-full bg-zinc-200/70 px-2 py-0.5 text-xs text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
                    {detail}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="mt-3 border-t border-zinc-200 pt-3 text-xs text-emerald-700 dark:border-zinc-800 dark:text-emerald-400">
              Core profile details are complete.
            </p>
          )}
        </div>

        <details className="mt-4 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
          <summary className="cursor-pointer text-sm font-medium">
            Review or edit profile details
          </summary>
          <form onSubmit={saveAboutYou} className="mt-5 space-y-5">
            <div className="grid gap-5 sm:grid-cols-3">
              <FormField label="Current title" htmlFor="current-title">
                <input id="current-title" value={currentTitle} onChange={(event) => setCurrentTitle(event.target.value)} className={inputClasses} placeholder="Senior Backend Engineer" />
              </FormField>
              <FormField label="Home location" htmlFor="current-location">
                <input id="current-location" value={currentLocation} onChange={(event) => setCurrentLocation(event.target.value)} className={inputClasses} placeholder="Bengaluru, India" />
              </FormField>
              <FormField label="Professional experience (years)" htmlFor="years-of-experience" hint="Optional. Used to check stated experience requirements.">
                <input id="years-of-experience" type="number" min={0} max={60} step="0.1" inputMode="decimal" value={yearsOfExperience} onChange={(event) => setYearsOfExperience(event.target.value)} className={inputClasses} placeholder="1" />
              </FormField>
            </div>

            <FormField label="Career direction" htmlFor="career-thesis" hint="A short, honest description of what would make your next role better.">
              <textarea id="career-thesis" value={careerThesis} onChange={(event) => setCareerThesis(event.target.value)} className={textareaClasses} placeholder="I want broader platform ownership and strong engineering mentorship…" />
            </FormField>

            <div className="grid gap-5 sm:grid-cols-2">
              <ChoiceGroup legend="Preferred work modes" options={WORK_MODES} selected={workModes} onToggle={(value) => toggleValue(workModes, value, setWorkModes)} />
              <ChoiceGroup legend="Employment types" options={EMPLOYMENT_TYPES} selected={employmentTypes} onToggle={(value) => toggleValue(employmentTypes, value, setEmploymentTypes)} />
            </div>

            <FormField label="Notice period (days)" htmlFor="notice-days" hint="Optional. Use 0 if you can start immediately.">
              <input id="notice-days" type="number" min={0} max={365} value={noticeDays} onChange={(event) => setNoticeDays(event.target.value)} className={inputClasses} />
            </FormField>

            <fieldset className="space-y-3">
              <legend className="text-sm font-medium">Work authorization</legend>
              <div className="flex justify-end">
                <button type="button" className={secondaryButtonClasses} onClick={() => setAuthorizations((items) => [...items, { country_code: "IN", status: "citizen" }])}>
                  Add country
                </button>
              </div>
              {authorizations.length === 0 ? (
                <p className="text-sm text-zinc-500">No work authorization added yet.</p>
              ) : (
                authorizations.map((authorization, index) => (
                  <div key={`${authorization.country_code}-${index}`} className="grid gap-3 rounded-lg border border-zinc-200 p-3 sm:grid-cols-[8rem_1fr_auto] dark:border-zinc-800">
                    <label className="sr-only" htmlFor={`authorization-country-${index}`}>Country code</label>
                    <input id={`authorization-country-${index}`} maxLength={2} value={authorization.country_code} onChange={(event) => setAuthorizations((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, country_code: event.target.value.toUpperCase() } : item))} className={inputClasses} aria-label="Two-letter country code" />
                    <label className="sr-only" htmlFor={`authorization-status-${index}`}>Authorization status</label>
                    <select id={`authorization-status-${index}`} value={authorization.status} onChange={(event) => setAuthorizations((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, status: event.target.value as AuthorizationStatus } : item))} className={inputClasses}>
                      {AUTHORIZATION_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                    <button type="button" className={secondaryButtonClasses} onClick={() => setAuthorizations((items) => items.filter((_, itemIndex) => itemIndex !== index))}>Remove</button>
                  </div>
                ))
              )}
            </fieldset>

            {profileMessage ? <StatusMessage kind={profileMessage.kind}>{profileMessage.text}</StatusMessage> : null}
            <div className="flex flex-wrap gap-3">
              <button type="submit" disabled={profilePending} className={primaryButtonClasses}>
                {profilePending ? "Saving…" : profile ? "Save changes" : "Save about me"}
              </button>
              {profileMessage?.kind === "error" ? <button type="button" onClick={() => void reload()} className={secondaryButtonClasses}>Reload newer profile</button> : null}
            </div>
          </form>
        </details>
      </WorkspaceSection>

      <WorkspaceSection
        eyebrow="Step 3"
        title="Career targets"
        description="Save the role families, levels, and locations you want once; new searches reuse them as defaults."
      >
        <form onSubmit={addTrack} className="space-y-5">
          <div className="grid gap-5 sm:grid-cols-2">
            <FormField label="Target name" htmlFor="track-name">
              <input id="track-name" value={trackName} onChange={(event) => setTrackName(event.target.value)} className={inputClasses} placeholder="Platform · Remote" />
            </FormField>
            <FormField label="Role families" htmlFor="role-families" hint="Comma-separated">
              <input id="role-families" value={roleFamilies} onChange={(event) => setRoleFamilies(event.target.value)} className={inputClasses} placeholder="Platform engineering, Backend systems" />
            </FormField>
          </div>
          <FormField label="Target locations" htmlFor="target-locations" hint="Comma-separated">
            <input id="target-locations" value={targetLocations} onChange={(event) => setTargetLocations(event.target.value)} className={inputClasses} placeholder="Remote-India, Bengaluru" />
          </FormField>
          <ChoiceGroup legend="Seniority levels" options={SENIORITIES.map((value) => ({ value, label: titleCase(value) }))} selected={seniorities} onToggle={(value) => toggleValue(seniorities, value, setSeniorities)} />

          <label className="flex items-start gap-3 text-sm">
            <input type="checkbox" checked={trackActive} onChange={(event) => setTrackActive(event.target.checked)} className="mt-1" />
            <span><strong>Active career target</strong><br /><span className="text-zinc-500">Inactive targets remain saved but cannot power a role scan.</span></span>
          </label>

          <details className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
            <summary className="cursor-pointer text-sm font-medium">Advanced preference weights</summary>
            <p className="mt-2 text-xs leading-5 text-zinc-500">
              These values are saved for future ranking work but do not affect today’s fit order. You can leave the defaults unchanged.
            </p>
            <div className="mt-3 grid gap-4 sm:grid-cols-2">
              {(Object.keys(priorities) as Array<keyof CareerPriorities>).map((key) => (
                <label key={key} className="rounded-lg border border-zinc-200 p-3 text-sm dark:border-zinc-800">
                  <span className="flex justify-between gap-3"><span>{titleCase(key)}</span><strong>{priorities[key]}</strong></span>
                  <input type="range" min={0} max={5} value={priorities[key]} onChange={(event) => setPriorities((current) => ({ ...current, [key]: Number(event.target.value) }))} className="mt-2 w-full accent-indigo-600" />
                </label>
              ))}
            </div>
          </details>
          {trackMessage ? <StatusMessage kind={trackMessage.kind}>{trackMessage.text}</StatusMessage> : null}
          <div className="flex flex-wrap gap-3">
            <button type="submit" disabled={trackPending} className={primaryButtonClasses}>{trackPending ? "Saving…" : editingTrackId ? "Save target changes" : "Save career target"}</button>
            {editingTrackId ? <button type="button" onClick={cancelTrackEdit} className={secondaryButtonClasses}>Cancel edit</button> : null}
            {trackMessage?.kind === "error" ? <button type="button" onClick={() => void listCareerTracks().then(setTracks).catch((error) => setTrackMessage({ kind: "error", text: errorText(error, "Unable to reload career targets.") }))} className={secondaryButtonClasses}>Reload targets</button> : null}
          </div>
        </form>

        <div className="mt-7">
          <h3 className="text-sm font-semibold">Your targets</h3>
          {tracks.length === 0 ? <p className="mt-2 text-sm text-zinc-500">No career targets yet.</p> : (
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              {tracks.map((track) => (
                <article key={track.id} className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
                  <div className="flex items-center justify-between gap-3"><h4 className="font-medium">{track.name}</h4><span className={`text-xs ${track.active ? "text-emerald-600" : "text-zinc-500"}`}>{track.active ? "Active" : "Inactive"}</span></div>
                  <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">{track.role_families.join(", ")}</p>
                  <p className="mt-1 text-xs text-zinc-500">{track.seniority_levels.map(titleCase).join(" · ")} · {track.target_locations.join(", ")}</p>
                  <button type="button" onClick={() => editTrack(track)} className={`${secondaryButtonClasses} mt-3`}>Edit target</button>
                </article>
              ))}
            </div>
          )}
        </div>
      </WorkspaceSection>

      <WorkspaceSection
        eyebrow="Step 4"
        title="Resume-backed achievements"
        description="Exact claims extracted from an uploaded resume are ready automatically for fit assessment and grounded drafts. Add or manage claims manually only when you need to."
      >
        <div className="space-y-3">
          {evidence.length === 0 ? (
            <p className="rounded-lg border border-zinc-200 bg-zinc-50/70 p-4 text-sm text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950/40">
              No achievements imported yet. Upload a resume above and the app will keep its concrete claims linked to that source.
            </p>
          ) : evidence.map((item) => (
            <article key={item.id} className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm leading-6">{item.statement}</p>
                  <p className="mt-2 text-xs text-zinc-500">
                    {item.origin === "resume_suggestion" ? "From resume" : "Added manually"}
                    {item.skills.length ? ` · ${item.skills.join(" · ")}` : ""}
                  </p>
                </div>
                <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-xs font-medium dark:bg-zinc-800">
                  {item.approval_state === "approved" ? "Ready" : titleCase(item.approval_state)}
                </span>
              </div>
            </article>
          ))}
        </div>

        <details className="mt-4 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
          <summary className="cursor-pointer text-sm font-medium">
            Add or manage achievements manually
          </summary>
          <p className="mt-2 text-xs leading-5 text-zinc-500">
            Manually added claims stay pending until you approve them. This keeps unverified text out of fit assessments and messages.
          </p>
          <form onSubmit={addEvidence} className="mt-5 space-y-4">
            <FormField label="Achievement" htmlFor="evidence-statement">
              <textarea id="evidence-statement" value={evidenceStatement} onChange={(event) => setEvidenceStatement(event.target.value)} className={textareaClasses} placeholder="Reduced deployment rollback time from 40 minutes to 8 minutes by…" />
            </FormField>
            <div className="grid gap-4 sm:grid-cols-2">
              <FormField label="Skills" htmlFor="evidence-skills" hint="Comma-separated">
                <input id="evidence-skills" value={evidenceSkills} onChange={(event) => setEvidenceSkills(event.target.value)} className={inputClasses} placeholder="Python, Kubernetes, incident response" />
              </FormField>
              <FormField label="Related resume" htmlFor="evidence-resume" hint="Optional provenance link">
                <select id="evidence-resume" value={evidenceResumeId} onChange={(event) => setEvidenceResumeId(event.target.value)} className={inputClasses}>
                  <option value="">No resume link</option>
                  {resumes.map((resume) => <option key={resume.id} value={resume.id}>{resume.label}</option>)}
                </select>
              </FormField>
            </div>
            {evidenceMessage ? <StatusMessage kind={evidenceMessage.kind}>{evidenceMessage.text}</StatusMessage> : null}
            <div className="flex flex-wrap gap-3">
              <button type="submit" disabled={evidencePending} className={primaryButtonClasses}>{evidencePending ? "Saving…" : "Add for review"}</button>
              {evidenceMessage?.kind === "error" ? <button type="button" onClick={() => void listEvidence().then(setEvidence).catch((error) => setEvidenceMessage({ kind: "error", text: errorText(error, "Unable to reload achievements.") }))} className={secondaryButtonClasses}>Reload achievements</button> : null}
            </div>
          </form>

          {evidence.some((item) => item.approval_state === "pending" || item.approval_state === "approved") ? (
            <div className="mt-6 border-t border-zinc-200 pt-5 dark:border-zinc-800">
              <h3 className="text-sm font-semibold">Review controls</h3>
              <ul className="mt-3 space-y-3">
                {evidence
                  .filter((item) => item.approval_state === "pending" || item.approval_state === "approved")
                  .map((item) => (
                    <li key={item.id} className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
                      <p className="line-clamp-2 text-sm leading-6">{item.statement}</p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {item.approval_state === "pending" ? (
                          <>
                            <button type="button" disabled={evidenceReviewId === item.id} onClick={() => void setEvidenceState(item, "approved")} className={primaryButtonClasses}>Approve</button>
                            <button type="button" disabled={evidenceReviewId === item.id} onClick={() => void setEvidenceState(item, "rejected")} className={secondaryButtonClasses}>Reject</button>
                          </>
                        ) : (
                          <button type="button" disabled={evidenceReviewId === item.id} onClick={() => void setEvidenceState(item, "retired")} className={secondaryButtonClasses}>Retire</button>
                        )}
                      </div>
                    </li>
                  ))}
              </ul>
            </div>
          ) : null}
        </details>
      </WorkspaceSection>
    </div>
  );
}

function ResumeImportSummary({ report }: { report: ResumeImportReport }) {
  const summary = resumeImportPresentation(report);
  const resume = report.resume_version;

  return (
    <StatusMessage kind="success">
      <p className="font-semibold">Resume imported</p>
      <p className="mt-1 leading-6">
        <strong>{resume.label}</strong> · {resume.character_count.toLocaleString()} characters
        {resume.is_base ? " · set as your base resume" : ""}
      </p>
      <div className="mt-3 space-y-1 text-xs leading-5">
        {summary.parsedSections.length > 0 ? (
          <p><strong>Sections read:</strong> {summary.parsedSections.join(", ")}</p>
        ) : null}
        {summary.importedProfileDetails.length > 0 ? (
          <p><strong>Profile filled:</strong> {summary.importedProfileDetails.join(", ")}</p>
        ) : null}
        <p>
          <strong>Achievements imported:</strong> {summary.achievementCount}
        </p>
      </div>
      {summary.missingDetails.length > 0 ? (
        <div className="mt-3 border-t border-emerald-200 pt-3 dark:border-emerald-900">
          <p className="text-xs font-medium">Worth adding later</p>
          <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Missing profile details">
            {summary.missingDetails.map((detail) => (
              <li
                key={detail}
                className="rounded-full border border-emerald-300/80 bg-white/60 px-2 py-0.5 text-xs dark:border-emerald-800 dark:bg-emerald-950/60"
              >
                {detail}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {report.warnings.length > 0 ? (
        <ul className="mt-3 list-disc space-y-1 pl-4 text-xs">
          {report.warnings.map((warning) => <li key={warning}>{warning}</li>)}
        </ul>
      ) : null}
    </StatusMessage>
  );
}

function ChoiceGroup<T extends string>({
  legend,
  options,
  selected,
  onToggle,
}: {
  legend: string;
  options: Array<{ value: T; label: string }>;
  selected: T[];
  onToggle: (value: T) => void;
}) {
  return (
    <fieldset>
      <legend className="text-sm font-medium">{legend}</legend>
      <div className="mt-2 flex flex-wrap gap-2">
        {options.map((option) => {
          const checked = selected.includes(option.value);
          return (
            <label key={option.value} className={`cursor-pointer rounded-full border px-3 py-2 text-xs font-medium focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-indigo-600 dark:focus-within:outline-indigo-400 ${checked ? "border-indigo-500 bg-indigo-50 text-indigo-900 dark:bg-indigo-950 dark:text-indigo-100" : "border-zinc-300 dark:border-zinc-700"}`}>
              <input type="checkbox" className="sr-only" checked={checked} onChange={() => onToggle(option.value)} />
              {option.label}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

function toggleValue<T>(items: T[], value: T, update: (items: T[]) => void) {
  update(items.includes(value) ? items.filter((item) => item !== value) : [...items, value]);
}

function titleCase(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
