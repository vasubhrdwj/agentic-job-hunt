export type TodayRecommendationReadinessIssueId =
  | "professional_experience"
  | "work_authorization"
  | "base_resume"
  | "active_career_target"
  | "approved_evidence";

export interface TodayRecommendationReadinessIssue {
  id: TodayRecommendationReadinessIssueId;
  label: string;
  impact: string;
}

export interface TodayRecommendationReadinessInput {
  profile: {
    years_of_experience: number | null;
    work_authorizations: readonly unknown[];
    base_resume: unknown | null;
  } | null;
  resumeVersions: readonly { is_base: boolean }[];
  careerTracks: readonly { active: boolean }[];
  evidence: readonly { approval_state: string }[];
}

/**
 * Returns only profile gaps that materially reduce the quality or certainty of
 * the categorical Today recommendation. Zero years of experience is a known
 * value, and pending evidence is intentionally not treated as approved proof.
 */
export function todayRecommendationReadinessIssues(
  input: TodayRecommendationReadinessInput,
): TodayRecommendationReadinessIssue[] {
  const issues: TodayRecommendationReadinessIssue[] = [];

  if (input.profile?.years_of_experience === null || !input.profile) {
    issues.push({
      id: "professional_experience",
      label: "Professional experience",
      impact: "Experience requirements stay uncertain.",
    });
  }

  if (!input.profile || input.profile.work_authorizations.length === 0) {
    issues.push({
      id: "work_authorization",
      label: "Work authorization",
      impact: "Location eligibility cannot be confirmed.",
    });
  }

  if (
    !input.profile?.base_resume &&
    !input.resumeVersions.some((resume) => resume.is_base)
  ) {
    issues.push({
      id: "base_resume",
      label: "Base résumé",
      impact: "Skill matching cannot use your background.",
    });
  }

  if (!input.careerTracks.some((track) => track.active)) {
    issues.push({
      id: "active_career_target",
      label: "Active career target",
      impact: "Roles lack a clear target for ranking.",
    });
  }

  if (!input.evidence.some((item) => item.approval_state === "approved")) {
    issues.push({
      id: "approved_evidence",
      label: "Approved achievement evidence",
      impact: "Fit confidence and application stories stay weaker.",
    });
  }

  return issues;
}
