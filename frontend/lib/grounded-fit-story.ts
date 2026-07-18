import type {
  ApplicationArtifactClaim,
  ApplicationArtifactEvidenceSnapshot,
  ApplicationArtifactText,
  ApplicationArtifactUnsupportedRequirement,
} from "./application-artifact-types";

const MAX_STORY_EVIDENCE = 3;

export interface GroundedFitStoryInput {
  companyNote: Pick<ApplicationArtifactText, "text" | "claims">;
  selectedEvidence: Pick<
    ApplicationArtifactEvidenceSnapshot,
    "id" | "version" | "statement"
  >[];
  unsupportedRequirements: Pick<
    ApplicationArtifactUnsupportedRequirement,
    "id" | "ordinal" | "importance" | "text" | "coverage"
  >[];
}

export interface GroundedFitStoryGap {
  id: string;
  importance: "required" | "preferred";
  text: string;
}

export interface GroundedFitStory {
  message: string;
  companyName: string;
  roleTitle: string;
  highlightedRequirement: string | null;
  evidence: { id: string; version: number; statement: string }[];
  unclaimedGaps: GroundedFitStoryGap[];
}

/**
 * Builds copy-ready prose only from exact sources already validated and pinned
 * in an immutable application-material revision. Missing provenance fails closed.
 */
export function buildGroundedFitStory(
  input: GroundedFitStoryInput,
): GroundedFitStory | null {
  const exactClaims = input.companyNote.claims.filter((claim) => (
    claimMatchesDocument(input.companyNote.text, claim)
  ));
  const roleTitle = inlineText(postingField(exactClaims, "title") ?? "");
  const companyName = inlineText(postingField(exactClaims, "company_name") ?? "");
  if (!roleTitle || !companyName) return null;

  const evidenceSources = new Map<string, string>();
  for (const claim of exactClaims) {
    for (const source of claim.sources) {
      if (
        source.kind === "evidence_snapshot" &&
        source.quote === claim.text
      ) {
        evidenceSources.set(
          `${source.evidence_id}:${source.evidence_version}`,
          source.quote,
        );
      }
    }
  }

  const evidence: GroundedFitStory["evidence"] = [];
  const seenEvidence = new Set<string>();
  for (const item of input.selectedEvidence) {
    const key = `${item.id}:${item.version}`;
    if (
      seenEvidence.has(key) ||
      evidenceSources.get(key) !== item.statement
    ) continue;
    const statement = inlineText(item.statement);
    if (!statement) continue;
    evidence.push({
      id: item.id,
      version: item.version,
      statement,
    });
    seenEvidence.add(key);
    if (evidence.length === MAX_STORY_EVIDENCE) break;
  }
  if (evidence.length === 0) return null;

  const highlightedRequirement = firstJobRequirement(exactClaims);
  const lines = [
    `I’m interested in the ${roleTitle} role at ${companyName}.`,
  ];
  if (highlightedRequirement) {
    lines.push(`The posting calls out: ${highlightedRequirement}`);
  }
  lines.push(
    "",
    "Relevant experience I can substantiate:",
    ...evidence.map((item) => `• ${item.statement}`),
    "",
    "I’d welcome a conversation about the role.",
  );

  return {
    message: lines.join("\n"),
    companyName,
    roleTitle,
    highlightedRequirement,
    evidence,
    unclaimedGaps: input.unsupportedRequirements
      .filter((item) => item.coverage === "unsupported")
      .sort((left, right) => (
        importanceRank(left.importance) - importanceRank(right.importance) ||
        left.ordinal - right.ordinal ||
        left.id.localeCompare(right.id)
      ))
      .map((item) => ({
        id: item.id,
        importance: item.importance,
        text: inlineText(item.text),
      }))
      .filter((item) => Boolean(item.text)),
  };
}

function postingField(
  claims: ApplicationArtifactClaim[],
  field: "company_name" | "title",
): string | null {
  for (const claim of claims) {
    for (const source of claim.sources) {
      if (
        source.kind === "posting_field" &&
        source.field === field &&
        source.value === claim.text
      ) return claim.text;
    }
  }
  return null;
}

function firstJobRequirement(claims: ApplicationArtifactClaim[]): string | null {
  for (const claim of claims) {
    if (claim.sources.some((source) => (
      source.kind === "job_description_span" && source.quote === claim.text
    ))) {
      return inlineText(claim.text) || null;
    }
  }
  return null;
}

function claimMatchesDocument(
  documentText: string,
  claim: ApplicationArtifactClaim,
): boolean {
  return (
    claim.start >= 0 &&
    claim.end > claim.start &&
    claim.end <= documentText.length &&
    documentText.slice(claim.start, claim.end) === claim.text
  );
}

function inlineText(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function importanceRank(value: "required" | "preferred"): number {
  return value === "required" ? 0 : 1;
}
