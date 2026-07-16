"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  deleteWorkspace,
  downloadWorkspaceExport,
  getDeletionPreview,
  getRetentionReport,
  updateRetention,
} from "@/lib/privacy-api";
import type {
  DeletionPreviewResponse,
  RetentionReportResponse,
  VersionedRetentionReport,
} from "@/lib/privacy-types";
import { clearPendingHuntIdempotency } from "@/lib/hunt-idempotency";
import { clearAllRunAccess } from "@/lib/run-access";
import { createIdempotencyKey } from "@/lib/workspace-api";
import {
  errorText,
  FormField,
  inputClasses,
  primaryButtonClasses,
  secondaryButtonClasses,
  StatusMessage,
  WorkspaceSection,
} from "./workspace-ui";


type Notice = { kind: "error" | "success" | "info"; text: string };

export function PrivacyWorkspace() {
  const router = useRouter();
  const [preview, setPreview] = useState<DeletionPreviewResponse | null>(null);
  const [previewLoading, setPreviewLoading] = useState(true);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [retention, setRetention] = useState<VersionedRetentionReport | null>(null);
  const [retentionDays, setRetentionDays] = useState(30);
  const [retentionLoading, setRetentionLoading] = useState(true);
  const [retentionPending, setRetentionPending] = useState(false);
  const [retentionNotice, setRetentionNotice] = useState<Notice | null>(null);

  const [exportPending, setExportPending] = useState(false);
  const [exportNotice, setExportNotice] = useState<Notice | null>(null);

  const [confirmation, setConfirmation] = useState("");
  const [deletePending, setDeletePending] = useState(false);
  const [deleteNotice, setDeleteNotice] = useState<Notice | null>(null);
  const deletionKey = useRef<string | null>(null);
  const deleteStatusRef = useRef<HTMLDivElement>(null);

  const loadPreview = useCallback(async () => {
    setPreviewLoading(true);
    setPreviewError(null);
    try {
      setPreview(await getDeletionPreview());
    } catch (error) {
      setPreviewError(errorText(error, "Unable to load the deletion preview."));
    } finally {
      setPreviewLoading(false);
    }
  }, []);

  const loadRetention = useCallback(async () => {
    setRetentionLoading(true);
    setRetentionNotice(null);
    try {
      const next = await getRetentionReport();
      setRetention(next);
      setRetentionDays(next.data.hunt_run_retention_days);
    } catch (error) {
      setRetentionNotice({
        kind: "error",
        text: errorText(error, "Unable to load retention settings."),
      });
    } finally {
      setRetentionLoading(false);
    }
  }, []);

  useEffect(() => {
    const previewTimer = setTimeout(() => void loadPreview(), 0);
    const retentionTimer = setTimeout(() => void loadRetention(), 0);
    return () => {
      clearTimeout(previewTimer);
      clearTimeout(retentionTimer);
    };
  }, [loadPreview, loadRetention]);

  useEffect(() => {
    if (deleteNotice?.kind === "error") deleteStatusRef.current?.focus();
  }, [deleteNotice]);

  async function exportData() {
    setExportPending(true);
    setExportNotice(null);
    try {
      const download = await downloadWorkspaceExport();
      const url = URL.createObjectURL(download.blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = download.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
      setExportNotice({
        kind: "success",
        text: "Your portable JSON export is ready. It contains decrypted owner data, not stored ciphertext or session secrets.",
      });
    } catch (error) {
      setExportNotice({
        kind: "error",
        text: errorText(error, "Unable to export your workspace."),
      });
    } finally {
      setExportPending(false);
    }
  }

  async function saveRetention(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!retention) return;
    if (!Number.isInteger(retentionDays) || retentionDays < 1 || retentionDays > 30) {
      setRetentionNotice({
        kind: "error",
        text: "Choose a whole number from 1 to 30 days.",
      });
      return;
    }
    setRetentionPending(true);
    setRetentionNotice(null);
    try {
      const next = await updateRetention(retentionDays, retention.etag);
      setRetention(next);
      setRetentionDays(next.data.hunt_run_retention_days);
      setRetentionNotice({
        kind: "success",
        text: retentionResultText(next.data),
      });
      void loadPreview();
    } catch (error) {
      setRetentionNotice({
        kind: "error",
        text: errorText(error, "Unable to update retention."),
      });
    } finally {
      setRetentionPending(false);
    }
  }

  async function confirmDeletion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!preview || confirmation !== preview.confirmation_phrase) {
      setDeleteNotice({
        kind: "error",
        text: "Type the confirmation phrase exactly as shown.",
      });
      return;
    }
    setDeletePending(true);
    setDeleteNotice(null);
    deletionKey.current ??= createIdempotencyKey("privacy-workspace-delete");
    try {
      await deleteWorkspace(confirmation, deletionKey.current);
      clearAllRunAccess();
      clearPendingHuntIdempotency();
      deletionKey.current = null;
      router.replace("/login?workspace_deleted=1");
      router.refresh();
    } catch (error) {
      setDeleteNotice({
        kind: "error",
        text: errorText(error, "Unable to delete the workspace."),
      });
      setDeletePending(false);
    }
  }

  const reducingRetention = retention
    ? retentionDays < retention.data.hunt_run_retention_days
    : false;

  return (
    <div className="space-y-8">
      <WorkspaceSection
        eyebrow="Portable by design"
        title="Export your workspace"
        description="Download one deterministic JSON file containing your owner-scoped job-search history. Private encrypted fields are decrypted for you; ciphertext, session records, mutation fingerprints, credentials, and internal job errors are omitted and counted in the export manifest."
      >
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            className={primaryButtonClasses}
            disabled={exportPending}
            onClick={() => void exportData()}
          >
            {exportPending ? "Preparing export…" : "Download workspace JSON"}
          </button>
          <p className="max-w-xl text-xs leading-5 text-zinc-500 dark:text-zinc-400">
            The download is capped at 32 MiB and is never cached by the app or proxy.
          </p>
        </div>
        {exportNotice ? (
          <div className="mt-4">
            <StatusMessage kind={exportNotice.kind}>{exportNotice.text}</StatusMessage>
          </div>
        ) : null}
      </WorkspaceSection>

      <WorkspaceSection
        eyebrow="Automatic cleanup"
        title="Legacy-run retention"
        description="Choose how long encrypted legacy hunt runs remain available. This setting governs new runs and scheduled cleanup. Your profile, searches, opportunities, applications, interviews, contacts, and outreach stay until you explicitly delete the workspace."
      >
        {retentionLoading && !retention ? (
          <p role="status" className="text-sm text-zinc-500">Loading retention policy…</p>
        ) : retention ? (
          <form className="max-w-xl space-y-4" onSubmit={saveRetention}>
            <FormField
              label="Keep legacy hunt runs for"
              htmlFor="hunt-retention-days"
              hint={`${retention.data.retained_hunt_runs} legacy ${retention.data.retained_hunt_runs === 1 ? "run is" : "runs are"} currently retained.`}
            >
              <div className="flex items-center gap-3">
                <input
                  id="hunt-retention-days"
                  type="number"
                  min={1}
                  max={30}
                  step={1}
                  value={retentionDays}
                  disabled={retentionPending}
                  onChange={(event) => setRetentionDays(Number(event.target.value))}
                  className={`${inputClasses} max-w-28`}
                />
                <span className="text-sm text-zinc-600 dark:text-zinc-400">days</span>
              </div>
            </FormField>
            {reducingRetention ? (
              <StatusMessage kind="info">
                Saving a shorter period immediately and permanently deletes this workspace&apos;s legacy runs older than {retentionDays} {retentionDays === 1 ? "day" : "days"}. It never changes another owner&apos;s data.
              </StatusMessage>
            ) : null}
            <button
              type="submit"
              className={secondaryButtonClasses}
              disabled={retentionPending || retentionDays === retention.data.hunt_run_retention_days}
            >
              {retentionPending ? "Applying retention…" : "Save retention policy"}
            </button>
          </form>
        ) : (
          <button type="button" className={secondaryButtonClasses} onClick={() => void loadRetention()}>
            Retry retention settings
          </button>
        )}
        {retentionNotice ? (
          <div className="mt-4">
            <StatusMessage kind={retentionNotice.kind}>{retentionNotice.text}</StatusMessage>
          </div>
        ) : null}
      </WorkspaceSection>

      <WorkspaceSection
        eyebrow="External processing"
        title="What local deletion cannot retract"
        description="Deleting this workspace removes local owner data and revokes every local browser session. It cannot erase data that a provider has already processed under its own policies."
      >
        {previewLoading && !preview ? (
          <p role="status" className="text-sm text-zinc-500">Loading provider disclosures…</p>
        ) : preview ? (
          <div className="grid gap-4 lg:grid-cols-3">
            {preview.external_data_limits.map((limit) => (
              <article key={limit.category} className="rounded-xl border border-zinc-200 p-4 dark:border-zinc-700">
                <p className="text-xs font-semibold uppercase tracking-[0.12em] text-zinc-500">
                  {humanize(limit.category)}
                </p>
                <p className="mt-2 text-sm leading-6 text-zinc-700 dark:text-zinc-300">
                  {limit.summary}
                </p>
                <a
                  href={limit.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-3 inline-block text-sm font-medium text-indigo-600 underline underline-offset-2 dark:text-indigo-400"
                >
                  Official policy
                </a>
                <p className="mt-2 text-xs text-zinc-500">Verified {formatDateOnly(limit.verified_on)}</p>
              </article>
            ))}
          </div>
        ) : (
          <StatusMessage kind="error">
            {previewError ?? "Provider disclosures are unavailable."}{" "}
            <button type="button" className="font-semibold underline" onClick={() => void loadPreview()}>
              Retry
            </button>
          </StatusMessage>
        )}
      </WorkspaceSection>

      <section
        aria-labelledby="delete-workspace-title"
        className="rounded-2xl border border-red-300 bg-red-50/70 p-5 shadow-sm sm:p-7 dark:border-red-900 dark:bg-red-950/20"
      >
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-red-700 dark:text-red-300">
          Irreversible
        </p>
        <h2 id="delete-workspace-title" className="mt-2 text-xl font-semibold tracking-tight">
          Delete the entire workspace
        </h2>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-700 dark:text-zinc-300">
          This transaction deletes every row owned by this workspace—including all sessions—and leaves only a payload-free, keyed deletion receipt so a safe retry cannot delete newly recreated data.
        </p>

        {previewLoading && !preview ? (
          <p role="status" className="mt-6 text-sm text-zinc-500">Building deletion preview…</p>
        ) : preview ? (
          <div className="mt-6 space-y-5">
            <div className="grid gap-3 sm:grid-cols-3">
              <Metric label="Rows to delete" value={preview.total_rows} />
              <Metric label="Sessions to revoke" value={preview.active_sessions} />
              <Metric label="Data groups" value={Object.keys(preview.row_counts).length} />
            </div>
            <details className="rounded-xl border border-red-200 bg-white p-4 dark:border-red-900 dark:bg-zinc-950/40">
              <summary className="cursor-pointer text-sm font-medium">Review row counts</summary>
              <dl className="mt-4 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
                {Object.entries(preview.row_counts).map(([table, count]) => (
                  <div key={table} className="flex justify-between gap-4 border-b border-zinc-100 py-1 dark:border-zinc-800">
                    <dt className="text-zinc-600 dark:text-zinc-400">{humanize(table)}</dt>
                    <dd className="font-medium tabular-nums">{count}</dd>
                  </div>
                ))}
              </dl>
            </details>
            <form className="max-w-2xl space-y-4" onSubmit={confirmDeletion}>
              <FormField
                label={<>Type <code className="rounded bg-red-100 px-1 py-0.5 text-xs dark:bg-red-950">{preview.confirmation_phrase}</code> to confirm</>}
                htmlFor="delete-workspace-confirmation"
                hint="Copying the phrase is allowed. The owner identity must match the current authenticated workspace."
              >
                <input
                  id="delete-workspace-confirmation"
                  type="text"
                  autoComplete="off"
                  spellCheck={false}
                  value={confirmation}
                  disabled={deletePending}
                  onChange={(event) => setConfirmation(event.target.value)}
                  className={inputClasses}
                  aria-describedby="delete-workspace-warning"
                />
              </FormField>
              <p id="delete-workspace-warning" className="text-sm font-medium text-red-800 dark:text-red-200">
                Export first if you may need this history. There is no undo or restore from the product UI.
              </p>
              <button
                type="submit"
                disabled={deletePending || confirmation !== preview.confirmation_phrase}
                className="inline-flex min-h-11 items-center justify-center rounded-lg bg-red-700 px-4 py-2 text-sm font-semibold text-white transition hover:bg-red-800 focus:outline-none focus:ring-2 focus:ring-red-600 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {deletePending ? "Deleting workspace…" : "Permanently delete workspace"}
              </button>
            </form>
          </div>
        ) : (
          <div className="mt-6">
            <StatusMessage kind="error">
              {previewError ?? "Deletion preview is unavailable."} Deletion stays disabled until the preview loads.{" "}
              <button type="button" className="font-semibold underline" onClick={() => void loadPreview()}>
                Retry
              </button>
            </StatusMessage>
          </div>
        )}
        {deleteNotice ? (
          <div ref={deleteStatusRef} tabIndex={-1} className="mt-4 outline-none">
            <StatusMessage kind={deleteNotice.kind}>{deleteNotice.text}</StatusMessage>
          </div>
        ) : null}
      </section>
    </div>
  );
}


function retentionResultText(report: RetentionReportResponse): string {
  if (report.purged_hunt_runs === 0) {
    return `Retention is now ${report.hunt_run_retention_days} days. No legacy runs were old enough to delete.`;
  }
  return `Retention is now ${report.hunt_run_retention_days} days. ${report.purged_hunt_runs} old legacy ${report.purged_hunt_runs === 1 ? "run was" : "runs were"} deleted immediately.`;
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-red-200 bg-white p-4 dark:border-red-900 dark:bg-zinc-950/40">
      <p className="text-xs uppercase tracking-[0.12em] text-zinc-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
    </div>
  );
}

function humanize(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDateOnly(value: string): string {
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: "UTC" }).format(date);
}
