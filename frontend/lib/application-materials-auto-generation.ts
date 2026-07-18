import type {
  ApplicationArtifactRevisionCreate,
  ApplicationArtifactsResponse,
} from "./application-artifact-types";
import type { ApplicationStage } from "./application-types";

export const APPLICATION_MATERIALS_GENERATOR_VERSION =
  "application-artifacts-deterministic-v1" as const;

export interface InitialMaterialsGenerationPlan {
  idempotencyKey: string;
  packId: string;
  expectedPackVersion: number;
  payload: ApplicationArtifactRevisionCreate;
}

export function buildInitialMaterialsGenerationPlan({
  applicationId,
  applicationStage,
  projection,
  selectedEvidenceIds,
  questionCount,
  questionsValid,
  inputsDirty,
}: {
  applicationId: string;
  applicationStage: ApplicationStage;
  projection: ApplicationArtifactsResponse | null;
  selectedEvidenceIds: string[];
  questionCount: number;
  questionsValid: boolean;
  inputsDirty: boolean;
}): InitialMaterialsGenerationPlan | null {
  const pack = projection?.pack;
  const sources = projection?.source_catalog;
  if (
    applicationStage !== "pursuing" ||
    !projection ||
    projection.application_id !== applicationId ||
    !pack ||
    !sources?.reviewed_grounding_revision_id ||
    projection.current_revision !== null ||
    projection.blockers.length > 0 ||
    questionCount !== 0 ||
    !questionsValid ||
    inputsDirty
  ) return null;

  const evidence = sources.evidence.slice(0, 5);
  if (evidence.length === 0) return null;
  const expectedEvidenceIds = evidence.map((item) => item.id);
  if (
    selectedEvidenceIds.length !== expectedEvidenceIds.length ||
    selectedEvidenceIds.some((id, index) => id !== expectedEvidenceIds[index])
  ) return null;

  return {
    idempotencyKey: [
      "application-artifacts:auto",
      applicationId,
      sources.reviewed_grounding_revision_id,
      APPLICATION_MATERIALS_GENERATOR_VERSION,
    ].join(":"),
    packId: pack.id,
    expectedPackVersion: pack.version,
    payload: {
      operation: "generate",
      grounding_revision_id: sources.reviewed_grounding_revision_id,
      parent_artifact_revision_id: null,
      generation_mode: "deterministic",
      selected_evidence_refs: evidence.map((item) => ({
        id: item.id,
        version: item.version,
      })),
      questions: [],
    },
  };
}
