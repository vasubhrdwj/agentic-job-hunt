import type { TodayOpportunityItem } from "./opportunity-types";

type PursuitFitContext = Pick<TodayOpportunityItem, "discovered_by" | "match">;

/**
 * Keep the Pursue dialog on the same saved search that produced the fit shown
 * in Today. A sole provenance row remains a safe fallback for older or
 * unassessed opportunities; ambiguous provenance deliberately stays blank.
 */
export function defaultPursuitSavedSearchId(
  opportunity: PursuitFitContext,
): string {
  const assessedSearchId = opportunity.match.state === "assessed"
    ? opportunity.match.assessment_saved_search_id
    : null;
  if (
    assessedSearchId
    && opportunity.discovered_by.some(
      (search) => search.saved_search_id === assessedSearchId,
    )
  ) return assessedSearchId;

  return opportunity.discovered_by.length === 1
    ? opportunity.discovered_by[0].saved_search_id
    : "";
}

export function preferredApplicationPackResumeId({
  currentResumeId,
  attributedResumeId,
  resumes,
}: {
  currentResumeId: string;
  attributedResumeId: string | null;
  resumes: ReadonlyArray<{ id: string; is_base: boolean }>;
}): string {
  if (currentResumeId && resumes.some((resume) => resume.id === currentResumeId)) {
    return currentResumeId;
  }
  if (
    attributedResumeId &&
    resumes.some((resume) => resume.id === attributedResumeId)
  ) return attributedResumeId;
  return resumes.find((resume) => resume.is_base)?.id ?? resumes[0]?.id ?? "";
}
