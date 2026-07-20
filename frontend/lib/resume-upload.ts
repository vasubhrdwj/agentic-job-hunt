import type { ResumeImportReport } from "./workspace-types";

export const MAX_RESUME_FILE_BYTES = 3 * 1024 * 1024;
export const MAX_RESUME_LABEL_CHARACTERS = 120;
export const RESUME_FILE_ACCEPT = ".pdf,.docx,.txt";

const SUPPORTED_RESUME_EXTENSIONS = new Set(["pdf", "docx", "txt"]);
const PROFILE_FIELD_LABELS: Record<string, string> = {
  career_thesis: "Career direction",
  current_location: "Home location",
  current_title: "Current title",
  employment_types: "Employment preferences",
  notice_period_days: "Notice period",
  work_authorizations: "Work authorization",
  work_modes: "Work-mode preferences",
  years_of_experience: "Years of experience",
};

export interface ResumeFileLike {
  name: string;
  size: number;
}

export interface ResumeImportPresentation {
  importedProfileDetails: string[];
  missingDetails: string[];
  parsedSections: string[];
  achievementCount: number;
}

export interface ProfileGapInput {
  currentTitle: string;
  currentLocation: string;
  yearsOfExperience: string;
  careerThesis: string;
  noticeDays: string;
  workModeCount: number;
  authorizationCount: number;
}

export function validateResumeFile(file: ResumeFileLike): string | null {
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  if (!SUPPORTED_RESUME_EXTENSIONS.has(extension)) {
    return "Choose a PDF, DOCX, or TXT resume.";
  }
  if (file.size <= 0) return "This file is empty. Choose a resume with content.";
  if (file.size > MAX_RESUME_FILE_BYTES) {
    return "This file is larger than 3 MB. Choose a smaller resume.";
  }
  return null;
}

export function resumeLabelFromFilename(filename: string): string {
  const withoutExtension = filename.replace(/\.(pdf|docx|txt)$/i, "");
  const label = withoutExtension
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return (label || "Resume").slice(0, MAX_RESUME_LABEL_CHARACTERS).trimEnd();
}

export function formatResumeFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function resumeImportPresentation(
  report: ResumeImportReport,
): ResumeImportPresentation {
  return {
    importedProfileDetails: uniqueLabels(report.imported_profile_fields),
    missingDetails: uniqueLabels(report.missing_profile_fields),
    parsedSections: uniqueLabels(report.parsed_sections ?? []),
    achievementCount: Math.max(
      0,
      Math.trunc(report.achievement_suggestions_created),
    ),
  };
}

export function profileGapLabels(profile: ProfileGapInput): string[] {
  return [
    [profile.currentTitle.trim(), "Current title"],
    [profile.currentLocation.trim(), "Home location"],
    [profile.yearsOfExperience.trim(), "Years of experience"],
    [profile.careerThesis.trim(), "Career direction"],
    [profile.noticeDays.trim(), "Notice period"],
    [profile.workModeCount > 0, "Work-mode preference"],
    [profile.authorizationCount > 0, "Work authorization"],
  ].flatMap(([present, label]) => present ? [] : [String(label)]);
}

function uniqueLabels(values: string[]): string[] {
  return Array.from(
    new Set(
      values
        .map((value) => value.trim())
        .filter(Boolean)
        .map((value) => PROFILE_FIELD_LABELS[value] ?? humanize(value)),
    ),
  );
}

function humanize(value: string): string {
  const words = value.replaceAll("_", " ").replace(/\s+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "";
}
