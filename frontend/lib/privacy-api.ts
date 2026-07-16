import type {
  DeletionPreviewResponse,
  RetentionReportResponse,
  VersionedRetentionReport,
  WorkspaceDeletionReceipt,
  WorkspaceExportDownload,
} from "./privacy-types";
import { apiError, expectJson } from "./workspace-api";


function etag(response: Response, version: number): string {
  return response.headers.get("etag") || `"${version}"`;
}

export async function downloadWorkspaceExport(): Promise<WorkspaceExportDownload> {
  const response = await fetch("/api/privacy/export", {
    cache: "no-store",
    credentials: "same-origin",
  });
  if (!response.ok) throw await apiError(response);
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  return {
    blob: await response.blob(),
    filename: match?.[1] || "job-hunt-workspace.json",
  };
}

export async function getDeletionPreview(): Promise<DeletionPreviewResponse> {
  return expectJson<DeletionPreviewResponse>(
    await fetch("/api/privacy/deletion-preview", {
      cache: "no-store",
      credentials: "same-origin",
    }),
  );
}

export async function getRetentionReport(): Promise<VersionedRetentionReport> {
  const response = await fetch("/api/privacy/retention", {
    cache: "no-store",
    credentials: "same-origin",
  });
  const data = await expectJson<RetentionReportResponse>(response);
  return { data, etag: etag(response, data.version) };
}

export async function updateRetention(
  huntRunRetentionDays: number,
  currentEtag: string,
): Promise<VersionedRetentionReport> {
  const response = await fetch("/api/privacy/retention", {
    method: "PATCH",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "If-Match": currentEtag,
    },
    body: JSON.stringify({ hunt_run_retention_days: huntRunRetentionDays }),
  });
  const data = await expectJson<RetentionReportResponse>(response);
  return { data, etag: etag(response, data.version) };
}

export async function deleteWorkspace(
  confirmation: string,
  idempotencyKey: string,
): Promise<WorkspaceDeletionReceipt> {
  return expectJson<WorkspaceDeletionReceipt>(
    await fetch("/api/privacy/workspace", {
      method: "DELETE",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify({ confirmation }),
    }),
  );
}
