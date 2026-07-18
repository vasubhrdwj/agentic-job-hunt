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

export interface PreparedAssessmentReviewRow {
  requirementId: string;
  ordinal: number;
  requirementExcerpt: string;
  coverage: "supported" | PreparedCoverage;
  linkedEvidenceCount: number;
  linkedEvidenceStatements: string[];
}

export interface PreparedAssessmentReviewDecision {
  requirementId: string;
  ordinal: number;
  coverage: "supported" | PreparedCoverage;
  evidenceIds: readonly string[];
}

/**
 * Prepares conservative local proposals for a brand-new extracted review.
 *
 * A proposal is never stronger than Partial: bounded skill concepts, plus a
 * narrow allowlist of AWS service-to-platform relationships, are only reasons
 * to attach approved evidence for inspection. No fuzzy synonym, resume-text,
 * or statement-text matching happens here. The caller decides whether and
 * when to persist these proposals.
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

export function buildPreparedAssessmentReviewRows(
  decisions: readonly PreparedAssessmentReviewDecision[],
  requirements: readonly { id: string; text: string }[],
  evidence: readonly { id: string; statement: string }[],
): PreparedAssessmentReviewRow[] {
  const requirementById = new Map<string, string>();
  for (const requirement of requirements) {
    if (!requirementById.has(requirement.id)) {
      requirementById.set(requirement.id, requirement.text);
    }
  }
  const evidenceById = new Map<string, string>();
  for (const item of evidence) {
    if (!evidenceById.has(item.id)) evidenceById.set(item.id, item.statement);
  }

  return decisions.flatMap((decision) => {
    const text = requirementById.get(decision.requirementId);
    if (text === undefined) return [];
    const linkedEvidenceStatements = decision.evidenceIds.flatMap((id) => {
      const statement = evidenceById.get(id);
      return statement === undefined ? [] : [conciseExcerpt(statement, 120)];
    });
    return [{
      requirementId: decision.requirementId,
      ordinal: decision.ordinal,
      requirementExcerpt: conciseExcerpt(text, 180),
      coverage: decision.coverage,
      linkedEvidenceCount: decision.evidenceIds.length,
      linkedEvidenceStatements,
    }];
  });
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
  const requirementTokens = tokenizeSkillConcept(text);
  const skillTokens = tokenizeSkillConcept(skillTag);
  if (requirementTokens.length === 0 || skillTokens.length === 0) return false;

  if (containsTokenSequence(requirementTokens, skillTokens)) return true;

  return isAllowlistedUmbrellaMatch(requirementTokens, skillTokens);
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

const IGNORED_SKILL_CONNECTORS = new Set(["and"]);

const AWS_SERVICE_CONCEPTS = [
  ["alb"],
  ["api", "gateway"],
  ["cloudwatch"],
  ["dynamodb"],
  ["ec2"],
  ["ecs"],
  ["eks"],
  ["iam"],
  ["kinesis"],
  ["lambda"],
  ["msk"],
  ["rds"],
  ["s3"],
  ["sns"],
  ["sqs"],
  ["step", "functions"],
] as const;

function tokenizeSkillConcept(value: string): string[] {
  const tokens = value
    .normalize("NFKC")
    .toLocaleLowerCase("en-US")
    .match(/[.]?[a-z0-9]+(?:[.][a-z0-9]+|[+#]+[a-z0-9]*)*/g);
  return (tokens ?? []).filter((token) => !IGNORED_SKILL_CONNECTORS.has(token));
}

function containsTokenSequence(haystack: readonly string[], needle: readonly string[]): boolean {
  if (needle.length > haystack.length) return false;
  for (let start = 0; start <= haystack.length - needle.length; start += 1) {
    if (needle.every((token, offset) => haystack[start + offset] === token)) return true;
  }
  return false;
}

function isAllowlistedUmbrellaMatch(
  requirementTokens: readonly string[],
  skillTokens: readonly string[],
): boolean {
  if (!containsTokenSequence(requirementTokens, ["aws"])) return false;
  if (skillTokens[0] !== "aws") return false;

  const serviceTokens = skillTokens.slice(1);
  return AWS_SERVICE_CONCEPTS.some(
    (service) =>
      service.length === serviceTokens.length &&
      service.every((token, index) => serviceTokens[index] === token),
  );
}

function conciseExcerpt(value: string, maxLength: number): string {
  if (value.length <= maxLength) return value;
  return `${value.slice(0, maxLength - 1).trimEnd()}…`;
}
