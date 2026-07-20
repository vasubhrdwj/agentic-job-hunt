import assert from "node:assert/strict";
import test from "node:test";

import {
  defaultPursuitSavedSearchId,
  preferredApplicationPackResumeId,
} from "../lib/application-fit-context";
import type { TodayOpportunityItem } from "../lib/opportunity-types";

function context({
  assessmentSearchId = "search-fit",
  assessed = true,
  discoveredSearchIds = ["search-other", "search-fit"],
}: {
  assessmentSearchId?: string | null;
  assessed?: boolean;
  discoveredSearchIds?: string[];
} = {}): Pick<TodayOpportunityItem, "discovered_by" | "match"> {
  return {
    discovered_by: discoveredSearchIds.map((id) => ({
      saved_search_id: id,
      saved_search_name: id,
      first_matched_at: "2026-07-20T00:00:00Z",
      last_matched_at: "2026-07-20T00:00:00Z",
    })),
    match: assessed
      ? {
          state: "assessed",
          algorithm_version: "fit-v1",
          resume_version_id: "resume-fit",
          assessment_saved_search_id: assessmentSearchId,
          assessment_input_fingerprint: "a".repeat(64),
          fit_band: "strong",
          confidence: "high",
          eligibility: "eligible",
          matched_terms: [],
          representative_requirement: null,
          approved_evidence_ids: [],
          strengths: [],
          gaps: [],
          not_assessed_reason: null,
        }
      : {
          state: "not_assessed",
          algorithm_version: null,
          resume_version_id: null,
          assessment_saved_search_id: null,
          assessment_input_fingerprint: null,
          fit_band: null,
          confidence: null,
          eligibility: null,
          matched_terms: [],
          representative_requirement: null,
          approved_evidence_ids: [],
          strengths: [],
          gaps: [],
          not_assessed_reason: "not_requested",
        },
  };
}

test("Pursue defaults to the saved search that produced the displayed fit", () => {
  assert.equal(defaultPursuitSavedSearchId(context()), "search-fit");
});

test("a sole provenance row is a safe fallback without an assessment", () => {
  assert.equal(defaultPursuitSavedSearchId(context({
    assessed: false,
    discoveredSearchIds: ["search-only"],
  })), "search-only");
});

test("ambiguous or stale assessment provenance fails closed", () => {
  assert.equal(defaultPursuitSavedSearchId(context({
    assessmentSearchId: "search-stale",
  })), "");
  assert.equal(defaultPursuitSavedSearchId(context({
    assessed: false,
  })), "");
});

test("application preparation keeps an owner choice, then prefers exact attribution", () => {
  const resumes = [
    { id: "resume-base", is_base: true },
    { id: "resume-fit", is_base: false },
  ];

  assert.equal(preferredApplicationPackResumeId({
    currentResumeId: "resume-base",
    attributedResumeId: "resume-fit",
    resumes,
  }), "resume-base");
  assert.equal(preferredApplicationPackResumeId({
    currentResumeId: "",
    attributedResumeId: "resume-fit",
    resumes,
  }), "resume-fit");
  assert.equal(preferredApplicationPackResumeId({
    currentResumeId: "resume-deleted",
    attributedResumeId: "resume-stale",
    resumes,
  }), "resume-base");
});
