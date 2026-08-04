import type { ApplicationArtifactQuestionInput } from "./application-artifact-types";
import type { ApplicationDossierPreparedInputs } from "./application-dossier-types";
import type {
  ApplicationPackRequirementCoverage,
  ApplicationPackRequirementImportance,
  ApplicationPackRequirementReview,
  ApplicationPackResponse,
} from "./application-pack-types";

export interface DossierRequirementDraft {
  included: boolean;
  importance: ApplicationPackRequirementImportance;
  coverage: ApplicationPackRequirementCoverage;
  evidenceIds: string[];
}

export interface DossierQuestionDraft {
  id: string;
  text: string;
  characterLimit: string;
  evidenceIds: string[];
}

export function buildDossierPreparedInputs(
  projection: ApplicationPackResponse,
  drafts: Record<string, DossierRequirementDraft>,
  questions: DossierQuestionDraft[],
): ApplicationDossierPreparedInputs | null {
  const revision = projection.current_revision;
  if (!revision) return null;
  const evidenceById = new Map(
    projection.current_approved_evidence.map((item) => [item.id, item]),
  );
  const requirements: ApplicationPackRequirementReview[] = [];
  for (const requirement of revision.requirements) {
    const draft = drafts[requirement.id];
    if (!draft) return null;
    if (!draft.included) continue;
    const evidence_refs = draft.evidenceIds.flatMap((id) => {
      const evidence = evidenceById.get(id);
      return evidence ? [{ id: evidence.id, version: evidence.version }] : [];
    });
    if (
      draft.coverage === "needs_review" ||
      (["supported", "partial"].includes(draft.coverage) && evidence_refs.length === 0)
    ) return null;
    requirements.push({
      id: requirement.id,
      ordinal: requirement.ordinal,
      importance: draft.importance,
      text: requirement.text,
      source_start: requirement.source_start,
      source_end: requirement.source_end,
      coverage: draft.coverage,
      evidence_refs,
    });
  }
  if (requirements.length === 0) return null;

  const mappedEvidence = new Map<string, { id: string; version: number }>();
  for (const requirement of requirements) {
    for (const reference of requirement.evidence_refs) {
      if (!mappedEvidence.has(reference.id)) mappedEvidence.set(reference.id, reference);
    }
  }
  const selected_evidence_refs = [...mappedEvidence.values()].slice(0, 5);
  if (selected_evidence_refs.length === 0) return null;

  const questionInputs: ApplicationArtifactQuestionInput[] = [];
  for (const question of questions) {
    if (!question.text.trim()) return null;
    const character_limit = question.characterLimit.trim()
      ? Number(question.characterLimit)
      : null;
    if (
      character_limit !== null &&
      (!Number.isInteger(character_limit) || character_limit < 1 || character_limit > 10_000)
    ) return null;
    const evidence_refs = question.evidenceIds.flatMap((id) => {
      const reference = mappedEvidence.get(id);
      return reference ? [reference] : [];
    });
    if (evidence_refs.length !== question.evidenceIds.length) return null;
    questionInputs.push({
      id: question.id,
      text: question.text,
      character_limit,
      evidence_refs,
    });
  }
  return {
    grounding_parent_revision_id: revision.id,
    requirements,
    selected_evidence_refs,
    questions: questionInputs,
  };
}
