import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_RESUME_FILE_BYTES,
  formatResumeFileSize,
  profileGapLabels,
  resumeImportPresentation,
  resumeLabelFromFilename,
  validateResumeFile,
} from "../lib/resume-upload";
import type { ResumeImportReport } from "../lib/workspace-types";

test("resume upload accepts supported files up to 3 MB", () => {
  assert.equal(validateResumeFile({ name: "resume.PDF", size: 50_000 }), null);
  assert.equal(validateResumeFile({ name: "resume.docx", size: MAX_RESUME_FILE_BYTES }), null);
  assert.equal(validateResumeFile({ name: "resume.txt", size: 1 }), null);
});

test("resume upload rejects unsupported, empty, and oversized files", () => {
  assert.match(
    validateResumeFile({ name: "resume.pages", size: 10_000 }) ?? "",
    /PDF, DOCX, or TXT/,
  );
  assert.match(
    validateResumeFile({ name: "resume.pdf", size: 0 }) ?? "",
    /empty/,
  );
  assert.match(
    validateResumeFile({ name: "resume.pdf", size: MAX_RESUME_FILE_BYTES + 1 }) ?? "",
    /larger than 3 MB/,
  );
});

test("resume upload derives a readable optional label and file size", () => {
  assert.equal(resumeLabelFromFilename("Vasu_Backend-Resume.pdf"), "Vasu Backend Resume");
  assert.equal(resumeLabelFromFilename(".txt"), "Resume");
  assert.equal(formatResumeFileSize(100), "100 B");
  assert.equal(formatResumeFileSize(1_025), "2 KB");
  assert.equal(formatResumeFileSize(1.5 * 1024 * 1024), "1.5 MB");
});

test("import report becomes a concise, deduplicated UI summary", () => {
  const report = {
    resume_version: {
      id: "resume-1",
      label: "Backend resume",
      source: "uploaded",
      parent_resume_version_id: null,
      is_base: true,
      character_count: 4_200,
      version: 1,
      created_at: "2026-07-20T00:00:00Z",
      updated_at: "2026-07-20T00:00:00Z",
    },
    imported_profile_fields: ["current_title", "current_title", "years_of_experience"],
    achievement_suggestions_created: 4.9,
    missing_profile_fields: ["work_authorizations", "notice_period_days"],
    warnings: [],
    parsed_sections: ["experience", "skills", "experience"],
  } satisfies ResumeImportReport;

  assert.deepEqual(resumeImportPresentation(report), {
    importedProfileDetails: ["Current title", "Years of experience"],
    missingDetails: ["Work authorization", "Notice period"],
    parsedSections: ["Experience", "Skills"],
    achievementCount: 4,
  });
});

test("profile gaps are derived from durable profile state after import feedback disappears", () => {
  assert.deepEqual(profileGapLabels({
    currentTitle: "Backend Engineer",
    currentLocation: "",
    yearsOfExperience: "1.5",
    careerThesis: " ",
    noticeDays: "0",
    workModeCount: 0,
    authorizationCount: 1,
  }), ["Home location", "Career direction", "Work-mode preference"]);
});
