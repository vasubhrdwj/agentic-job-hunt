export type PreparedCoverage = "partial" | "unsupported";

export interface RequirementPreparationInput {
  id: string;
  ordinal: number;
  text: string;
  coverage: "needs_review" | "supported" | "partial" | "unsupported";
}

export interface ApprovedEvidencePreparationInput {
  id: string;
  approvalState: "pending" | "approved" | "rejected" | "retired";
  skills: readonly string[];
}

export interface RequirementPreparationRequest {
  packStatus: "not_started" | "draft" | "reviewed";
  revisionSource: "extracted" | "edited" | null;
  hasReviewEvent: boolean;
  requirements: readonly RequirementPreparationInput[];
  evidence: readonly ApprovedEvidencePreparationInput[];
}

export interface PreparedRequirementProposal {
  requirementId: string;
  ordinal: number;
  coverage: PreparedCoverage;
  evidenceIds: string[];
  matchedSkillTags: string[];
}

export interface PreparedRequirementPlan {
  proposals: PreparedRequirementProposal[];
  partialCount: number;
  unsupportedCount: number;
  matchedEvidenceCount: number;
}

/**
 * Prepares conservative local proposals for a brand-new extracted review.
 *
 * A proposal is never stronger than Partial: an exact skill-tag phrase is only
 * a reason to attach approved evidence for the owner to inspect. No synonym,
 * resume-text, or statement-text matching happens here. The caller decides
 * whether and when to persist these proposals.
 */
export function prepareRequirementProposals(
  request: RequirementPreparationRequest,
): PreparedRequirementPlan {
  if (
    request.packStatus !== "draft" ||
    request.revisionSource !== "extracted" ||
    request.hasReviewEvent
  ) {
    return emptyPlan();
  }

  const approvedEvidence = dedupeApprovedEvidence(request.evidence);
  const requirements = dedupeRequirements(request.requirements);
  const proposals: PreparedRequirementProposal[] = [];
  const matchedEvidenceIds = new Set<string>();

  for (const requirement of requirements) {
    if (requirement.coverage !== "needs_review") continue;

    const evidenceIds: string[] = [];
    const matchedSkillTags = new Map<string, string>();
    for (const evidence of approvedEvidence) {
      const matchingTags = evidence.skills.filter((skill) =>
        containsExactSkillTag(requirement.text, skill),
      );
      if (matchingTags.length === 0) continue;

      evidenceIds.push(evidence.id);
      matchedEvidenceIds.add(evidence.id);
      for (const tag of matchingTags) {
        const key = normalizeSkillTag(tag);
        if (!matchedSkillTags.has(key)) matchedSkillTags.set(key, tag.trim());
      }
    }

    proposals.push({
      requirementId: requirement.id,
      ordinal: requirement.ordinal,
      coverage: evidenceIds.length > 0 ? "partial" : "unsupported",
      evidenceIds,
      matchedSkillTags: [...matchedSkillTags.entries()]
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([, tag]) => tag),
    });
  }

  const partialCount = proposals.filter((proposal) => proposal.coverage === "partial").length;
  return {
    proposals,
    partialCount,
    unsupportedCount: proposals.length - partialCount,
    matchedEvidenceCount: matchedEvidenceIds.size,
  };
}

function emptyPlan(): PreparedRequirementPlan {
  return {
    proposals: [],
    partialCount: 0,
    unsupportedCount: 0,
    matchedEvidenceCount: 0,
  };
}

export function containsExactSkillTag(text: string, skillTag: string): boolean {
  const haystack = normalizePhrase(text);
  const needle = normalizeSkillTag(skillTag);
  if (!haystack || !needle) return false;

  let offset = 0;
  while (offset <= haystack.length - needle.length) {
    const index = haystack.indexOf(needle, offset);
    if (index < 0) return false;
    const previous = index > 0 ? haystack[index - 1] : undefined;
    const nextIndex = index + needle.length;
    const next = nextIndex < haystack.length ? haystack[nextIndex] : undefined;
    if (!isWordCharacter(previous) && !isWordCharacter(next)) return true;
    offset = index + 1;
  }
  return false;
}

function dedupeRequirements(
  requirements: readonly RequirementPreparationInput[],
): RequirementPreparationInput[] {
  const byId = new Map<string, RequirementPreparationInput>();
  for (const requirement of requirements) {
    if (!byId.has(requirement.id)) byId.set(requirement.id, requirement);
  }
  return [...byId.values()].sort(
    (left, right) => left.ordinal - right.ordinal || left.id.localeCompare(right.id),
  );
}

function dedupeApprovedEvidence(
  evidence: readonly ApprovedEvidencePreparationInput[],
): Array<{ id: string; skills: string[] }> {
  const byId = new Map<string, Set<string>>();
  for (const item of evidence) {
    if (item.approvalState !== "approved" || !item.id) continue;
    const tags = byId.get(item.id) ?? new Set<string>();
    for (const skill of item.skills) {
      const normalized = normalizeSkillTag(skill);
      if (normalized) tags.add(normalized);
    }
    byId.set(item.id, tags);
  }
  return [...byId.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([id, skills]) => ({ id, skills: [...skills].sort() }));
}

function normalizePhrase(value: string): string {
  return value.normalize("NFKC").trim().toLocaleLowerCase("en-US").replace(/\s+/g, " ");
}

function normalizeSkillTag(value: string): string {
  return normalizePhrase(value);
}

function isWordCharacter(value: string | undefined): boolean {
  return value !== undefined && /[a-z0-9]/i.test(value);
}
