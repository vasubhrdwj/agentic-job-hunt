"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import {
  getApplication,
  undoApplicationPursuit,
} from "@/lib/application-api";
import {
  canUndoApplicationPursuit,
  shouldRetainUndoPursuitRequest,
  undoPursuitErrorText,
} from "@/lib/application-undo-pursuit";
import type { ApplicationStage } from "@/lib/application-types";
import { createIdempotencyKey } from "@/lib/workspace-api";
import {
  secondaryButtonClasses,
  StatusMessage,
} from "./workspace-ui";

interface PendingUndo {
  expectedVersion: number;
  idempotencyKey: string;
}

export function ApplicationUndoPursuit({
  applicationId,
  stage,
  onApplicationChanged,
}: {
  applicationId: string;
  stage: ApplicationStage;
  onApplicationChanged: () => Promise<void>;
}) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasPending, setHasPending] = useState(false);
  const pending = useRef<PendingUndo | null>(null);

  if (!canUndoApplicationPursuit(stage)) return null;

  async function runUndo() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      let request = pending.current;
      if (!request) {
        const fresh = await getApplication(applicationId);
        if (!canUndoApplicationPursuit(fresh.application.stage)) {
          await onApplicationChanged();
          setError(
            "This role now has recorded application progress, so it cannot be returned to the inbox.",
          );
          return;
        }
        request = {
          expectedVersion: fresh.application.version,
          idempotencyKey: createIdempotencyKey(
            `undo-pursuit:${applicationId}:v${fresh.application.version}`,
          ),
        };
        pending.current = request;
        setHasPending(true);
      }

      await undoApplicationPursuit(
        applicationId,
        request.expectedVersion,
        request.idempotencyKey,
      );
      pending.current = null;
      setHasPending(false);
      router.replace("/today");
    } catch (reason) {
      if (!shouldRetainUndoPursuitRequest(reason)) {
        pending.current = null;
        setHasPending(false);
      }
      setError(undoPursuitErrorText(reason));
      if (
        reason &&
        typeof reason === "object" &&
        (reason as { code?: unknown }).code === "version_conflict"
      ) {
        await onApplicationChanged();
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-labelledby="undo-pursuit-title"
      className="rounded-2xl border border-zinc-200 bg-white p-5 sm:p-6 dark:border-zinc-800 dark:bg-zinc-900/70"
    >
      <h2 id="undo-pursuit-title" className="font-semibold">
        Pursued by mistake?
      </h2>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
        Use this only to correct an accidental Pursue click before you submit or contact anyone.
      </p>

      {error ? <div className="mt-4"><StatusMessage kind="error">{error}</StatusMessage></div> : null}

      {!confirming ? (
        <button
          type="button"
          onClick={() => {
            setConfirming(true);
            setError(null);
          }}
          className={`${secondaryButtonClasses} mt-4`}
        >
          Undo accidental pursuit
        </button>
      ) : (
        <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/25">
          <p className="text-sm leading-6 text-amber-950 dark:text-amber-100">
            Continuing permanently removes this application&apos;s prep/test data and dated tasks,
            then returns the saved role to the Today inbox. The posting, search, profile, and résumé stay intact.
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <button
              type="button"
              disabled={busy}
              onClick={() => void runUndo()}
              className="inline-flex min-h-11 items-center justify-center rounded-lg bg-red-700 px-4 py-2 text-sm font-medium text-white transition hover:bg-red-800 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2 disabled:cursor-wait disabled:opacity-55"
            >
              {busy ? "Undoing pursuit…" : hasPending ? "Retry undo" : "Yes, undo pursuit"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirming(false)}
              className={secondaryButtonClasses}
            >
              Keep application
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
