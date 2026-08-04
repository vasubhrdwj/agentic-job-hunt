import type {
  ApplicationArtifactBlocker,
  ApplicationArtifactQuestionInput,
  ApplicationArtifactRevisionResponse,
  ApplicationArtifactsResponse,
} from "./application-artifact-types";
import type {
  ApplicationPackEvidenceReference,
  ApplicationPackRequirementReview,
  ApplicationPackResponse,
} from "./application-pack-types";

export interface ApplicationDossierPreparedInputs {
  grounding_parent_revision_id: string;
  requirements: ApplicationPackRequirementReview[];
  selected_evidence_refs: ApplicationPackEvidenceReference[];
  questions: ApplicationArtifactQuestionInput[];
}

export type ApplicationDossierPreviewCreate = ApplicationDossierPreparedInputs;

export interface ApplicationDossierApproveCreate extends ApplicationDossierPreparedInputs {
  preview_fingerprint: string;
  confirm_dossier_reviewed: true;
}

export interface ApplicationDossierPreviewResponse {
  data_source: "database_preview";
  application_id: string;
  pack_id: string;
  pack_version: number;
  preview_fingerprint: string;
  materials: ApplicationArtifactRevisionResponse;
  blockers: ApplicationArtifactBlocker[];
}

export interface ApplicationDossierApprovalResponse {
  data_source: "database";
  application_id: string;
  pack: ApplicationPackResponse;
  artifacts: ApplicationArtifactsResponse;
}
