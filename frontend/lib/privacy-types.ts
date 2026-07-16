import type { components } from "./api-generated";


type ApiSchemas = components["schemas"];

export type ExternalDataLimit = ApiSchemas["ExternalDataLimit"];

export type PrivacyOmission = Omit<ApiSchemas["PrivacyOmission"], "field"> & {
  field: string | null;
};

export type WorkspaceExportResponse = Omit<
  ApiSchemas["WorkspaceExportResponse"],
  "omissions"
> & {
  omissions: PrivacyOmission[];
};

export type DeletionPreviewResponse = ApiSchemas["DeletionPreviewResponse"];

export type RetentionReportResponse = Omit<
  ApiSchemas["RetentionReportResponse"],
  "policy_applies_to" | "retained_until_explicit_deletion" | "updated_at"
> & {
  policy_applies_to: "legacy_hunt_runs"[];
  retained_until_explicit_deletion: string[];
  updated_at: string | null;
};

export interface VersionedRetentionReport {
  data: RetentionReportResponse;
  etag: string;
}

export type WorkspaceDeletionReceipt = ApiSchemas["WorkspaceDeletionReceipt"];

export interface WorkspaceExportDownload {
  blob: Blob;
  filename: string;
}
