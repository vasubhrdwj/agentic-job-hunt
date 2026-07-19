"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  createInterviewPreparationRevision,
  getApplicationInterviewPreparation,
} from "@/lib/application-api";
import { insertGroundedDraftIntoEmptyFields } from "@/lib/interview-preparation-drafts";
import type {
  ApplicationInterviewPreparationResponse,
  InterviewPreparationBlocker,
  InterviewPreparationMissingFact,
  InterviewPreparationPrompt,
  InterviewPreparationRevisionCreate,
  InterviewPreparationStarDraft,
} from "@/lib/interview-preparation-types";
import { createIdempotencyKey } from "@/lib/workspace-api";
import {
  errorText,
  formatDate,
  primaryButtonClasses,
  secondaryButtonClasses,
  StatusMessage,
  textareaClasses,
} from "./workspace-ui";

const EMPTY_DRAFT: InterviewPreparationStarDraft = {
  situation: "",
  task: "",
  action: "",
  result: "",
};

const BLOCKER_COPY: Record<InterviewPreparationBlocker, string> = {
  application_not_submitted:
    "Record the exact application and submitted materials before preparing interview answers.",
  application_closed:
    "This application is closed. Saved preparation remains readable but cannot be changed.",
  reviewed_application_pack_missing:
    "The exact reviewed requirements used for this application are unavailable.",
  approved_evidence_missing:
    "No approved achievement evidence is mapped to the submitted requirements.",
  evidence_snapshot_changed:
    "Mapped evidence changed or was retired after review. Recheck it before relying on these prompts.",
  required_requirement_evidence_missing:
    "At least one required qualification has no approved evidence. The app will not fill that gap with an invented story.",
  required_prompt_capacity_exceeded:
    "There are more required, evidence-backed requirements than the 12-prompt safety limit. None will be silently grouped or treated as prepared.",
};

const MISSING_FACT_COPY: Record<InterviewPreparationMissingFact, string> = {
  situation_context: "Situation: when and where this happened, who was involved, and what was at stake.",
  personal_responsibility: "Task: your exact responsibility, separate from the team’s goal.",
  specific_actions: "Action: the steps and decisions you personally took.",
  verified_result: "Result: a verified outcome or metric. Leave it blank if you cannot prove one.",
  motivation_connection: "Motivation: why this example genuinely connects to this company and role.",
  conflict_or_ambiguity_details: "Conflict or ambiguity: what was unclear or contested and how you handled it.",
  setback_and_learning_details: "Learning: the real setback, what changed in your thinking, and what you did differently.",
  leadership_or_collaboration_details: "Collaboration: who you worked with and how your contribution helped them succeed.",
};

export function ApplicationInterviewPreparation({
  applicationId,
}: {
  applicationId: string;
}) {
  const [projection, setProjection] =
    useState<ApplicationInterviewPreparationResponse | null>(null);
  const [drafts, setDrafts] = useState<Record<string, InterviewPreparationStarDraft>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [retryPending, setRetryPending] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const generation = useRef(0);
  const pending = useRef<{ key: string; signature: string } | null>(null);

  const applyProjection = useCallback((next: ApplicationInterviewPreparationResponse) => {
    setProjection(next);
    setDrafts(Object.fromEntries(next.prompts.map((prompt) => [prompt.id, prompt.draft])));
  }, []);

  const load = useCallback(async () => {
    const requestGeneration = ++generation.current;
    setLoadError(null);
    try {
      const next = await getApplicationInterviewPreparation(applicationId);
      if (requestGeneration !== generation.current || next.application_id !== applicationId) return;
      applyProjection(next);
    } catch (reason) {
      if (requestGeneration === generation.current) {
        setLoadError(errorText(reason, "Unable to load interview preparation."));
      }
    } finally {
      if (requestGeneration === generation.current) setLoading(false);
    }
  }, [applicationId, applyProjection]);

  useEffect(() => {
    pending.current = null;
    const timer = setTimeout(() => void load(), 0);
    return () => {
      clearTimeout(timer);
      generation.current += 1;
    };
  }, [load]);

  function updateDraft(
    promptId: string,
    field: keyof InterviewPreparationStarDraft,
    value: string,
  ) {
    setDrafts((current) => ({
      ...current,
      [promptId]: { ...(current[promptId] ?? EMPTY_DRAFT), [field]: value },
    }));
    setNotice(null);
    setSaveError(null);
    setRetryPending(false);
  }

  function insertGroundedStartingDraft(prompt: InterviewPreparationPrompt) {
    const starter = prompt.starting_draft?.draft;
    if (!starter) return;
    setDrafts((values) => {
      const current = values[prompt.id] ?? EMPTY_DRAFT;
      const next = insertGroundedDraftIntoEmptyFields(current, starter);
      return sameDraft(current, next) ? values : { ...values, [prompt.id]: next };
    });
    setNotice(
      "If Result was empty, the exact approved outcome was inserted locally. Existing text was kept. Verify it before saving; nothing was saved or approved automatically.",
    );
    setSaveError(null);
    setRetryPending(false);
  }

  async function save() {
    if (!projection?.source_fingerprint || projection.status === "blocked") return;
    const payload: InterviewPreparationRevisionCreate = {
      source_fingerprint: projection.source_fingerprint,
      parent_revision_id: projection.latest_revision?.id ?? null,
      prompt_drafts: projection.prompts.map((prompt) => ({
        prompt_id: prompt.id,
        ...(drafts[prompt.id] ?? EMPTY_DRAFT),
      })),
      confirm_owner_authored: true,
    };
    const signature = JSON.stringify(payload);
    if (!pending.current || pending.current.signature !== signature) {
      pending.current = {
        key: createIdempotencyKey(`interview-preparation:${applicationId}`),
        signature,
      };
    }
    setSaving(true);
    setSaveError(null);
    setNotice(null);
    try {
      const next = await createInterviewPreparationRevision(
        applicationId,
        projection.write_version,
        pending.current.key,
        payload,
      );
      if (next.application_id !== applicationId) return;
      pending.current = null;
      setRetryPending(false);
      applyProjection(next);
      setNotice(
        next.status === "ready"
          ? "Preparation saved. Every displayed STAR section has owner-authored text."
          : "Preparation saved as an owner-authored draft.",
      );
    } catch (reason) {
      setRetryPending(true);
      setSaveError(
        `${errorText(reason, "Unable to save interview preparation.")} ` +
        "Your text remains here; retrying unchanged uses the same safe receipt.",
      );
    } finally {
      setSaving(false);
    }
  }

  if (loading && !projection) {
    return (
      <section id="interview-preparation" aria-busy="true" className="rounded-2xl border border-zinc-200 bg-white p-5 sm:p-7 dark:border-zinc-800 dark:bg-zinc-900/70">
        <p role="status" className="text-sm text-zinc-500">Loading evidence-backed interview preparation…</p>
      </section>
    );
  }
  if (!projection) {
    return (
      <section id="interview-preparation" className="rounded-2xl border border-zinc-200 bg-white p-5 sm:p-7 dark:border-zinc-800 dark:bg-zinc-900/70">
        <StatusMessage kind="error">
          <span>{loadError ?? "Interview preparation is unavailable."}</span>{" "}
          <button type="button" onClick={() => void load()} className="font-medium underline underline-offset-4">Try again</button>
        </StatusMessage>
      </section>
    );
  }

  const anyOwnerText = projection.prompts.some((prompt) => {
    const draft = drafts[prompt.id] ?? EMPTY_DRAFT;
    return Object.values(draft).some((value) => value.trim());
  });
  const dirty = projection.prompts.some((prompt) =>
    !sameDraft(drafts[prompt.id] ?? EMPTY_DRAFT, prompt.draft));
  const canSave = projection.status !== "blocked" && anyOwnerText && dirty && !saving;

  return (
    <section
      id="interview-preparation"
      aria-labelledby="interview-preparation-title"
      aria-busy={saving}
      className="min-w-0 scroll-mt-24 rounded-2xl border border-violet-200 bg-white p-5 shadow-sm sm:p-7 dark:border-violet-900 dark:bg-zinc-900/70"
    >
      <div className="flex min-w-0 flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-violet-700 dark:text-violet-300">
              Interview preparation
            </p>
            <PreparationStatus status={projection.status} />
          </div>
          <h2 id="interview-preparation-title" className="mt-2 text-xl font-semibold tracking-tight">
            Build truthful stories for {projection.target.label}
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
            The questions come only from the exact role, submitted application, and evidence you approved. Grounded starters never guess Situation, Task, or Action, and they are never saved automatically.
          </p>
        </div>
        <div className="max-w-xs sm:text-right">
          <button
            type="button"
            disabled={saving}
            aria-describedby={dirty ? "interview-refresh-warning" : undefined}
            onClick={() => {
              if (
                dirty &&
                !window.confirm(
                  "Refresh interview context and discard your unsaved STAR edits?",
                )
              ) {
                return;
              }
              void load();
            }}
            className={secondaryButtonClasses}
          >
            {dirty ? "Discard edits & refresh" : "Refresh context"}
          </button>
          {dirty ? (
            <p
              id="interview-refresh-warning"
              className="mt-2 text-xs leading-5 text-amber-700 dark:text-amber-300"
            >
              Refreshing replaces unsaved STAR text. You will be asked to confirm first.
            </p>
          ) : null}
        </div>
      </div>

      <div className="mt-6 space-y-5">
        {loadError ? <StatusMessage kind="error">{loadError} The last loaded draft remains below.</StatusMessage> : null}
        {saveError ? <StatusMessage kind="error">{saveError}</StatusMessage> : null}
        {notice ? <StatusMessage kind="success">{notice}</StatusMessage> : null}

        <dl className="grid gap-3 rounded-xl border border-zinc-200 bg-zinc-50 p-4 text-sm sm:grid-cols-3 dark:border-zinc-800 dark:bg-zinc-950/40">
          <ContextItem label="Role" value={`${projection.role.title} · ${projection.role.company}`} />
          <ContextItem label="Target" value={projection.target.label} />
          <ContextItem
            label="Pinned source"
            value={projection.target.interview_round_version
              ? `Posting + round v${projection.target.interview_round_version}`
              : "Posting + submission"}
          />
          {projection.target.scheduled_start_at ? (
            <ContextItem label="Scheduled" value={formatDate(projection.target.scheduled_start_at)} />
          ) : null}
        </dl>

        <StatusMessage kind="info">{projection.disclaimer}</StatusMessage>

        {projection.blockers.length ? (
          <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-950 dark:border-red-900 dark:bg-red-950/25 dark:text-red-100">
            <h3 className="font-semibold">Resolve these before saving</h3>
            <ul className="mt-3 list-disc space-y-2 pl-5">
              {projection.blockers.map((blocker) => <li key={blocker}>{BLOCKER_COPY[blocker]}</li>)}
            </ul>
            {projection.blockers.includes("required_prompt_capacity_exceeded") ? (
              <p className="mt-3 font-medium">
                Count: {projection.required_evidence_backed_count} required evidence-backed requirements · {projection.prompt_capacity} prompt capacity.
              </p>
            ) : null}
            {projection.next_steps.length ? (
              <div className="mt-4">
                <h4 className="font-semibold">What to do next</h4>
                <ul className="mt-2 list-disc space-y-1 pl-5">
                  {projection.next_steps.map((step) => <li key={step}>{step}</li>)}
                </ul>
              </div>
            ) : null}
            <div className="mt-4 flex flex-wrap gap-3">
              <Link href="#manual-application" className={secondaryButtonClasses}>Check submission</Link>
              <Link href="#application-pack" className={secondaryButtonClasses}>Check requirements</Link>
              <Link href="/profile" className={secondaryButtonClasses}>Manage evidence</Link>
            </div>
          </div>
        ) : null}

        {projection.evidence_gaps.length ? (
          <details className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm dark:border-amber-900 dark:bg-amber-950/25">
            <summary className="cursor-pointer font-semibold">
              {projection.evidence_gaps.length} visible evidence gap{projection.evidence_gaps.length === 1 ? "" : "s"}
            </summary>
            <ul className="mt-3 space-y-2">
              {projection.evidence_gaps.map((gap) => (
                <li key={gap.requirement_id} className="leading-6">
                  <strong className="capitalize">{gap.importance}:</strong> {gap.requirement_text}
                </li>
              ))}
            </ul>
            {projection.status !== "blocked" ? (
              <p className="mt-3 leading-6 text-amber-950 dark:text-amber-100">
                These gaps stay visible, but they do not stop you from preparing the evidence-backed stories below.
              </p>
            ) : null}
          </details>
        ) : null}

        {projection.previous_context_stale ? (
          <details className="rounded-xl border border-zinc-300 bg-zinc-50 p-4 dark:border-zinc-700 dark:bg-zinc-950/40">
            <summary className="cursor-pointer font-semibold">Previous target draft · read-only</summary>
            <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
              The posting, evidence, or interview round changed. Your prior text is preserved below but is never copied into the new target automatically.
            </p>
            <div className="mt-4 space-y-3">
              {projection.previous_prompts.map((prompt) => <ReadOnlyPrompt key={prompt.id} prompt={prompt} />)}
            </div>
          </details>
        ) : null}

        {projection.prompts.length ? (
          <div className="space-y-4">
            {projection.prompts.map((prompt, index) => (
              <PromptEditor
                key={prompt.id}
                prompt={prompt}
                index={index}
                draft={drafts[prompt.id] ?? EMPTY_DRAFT}
                disabled={saving || projection.status === "blocked"}
                onChange={(field, value) => updateDraft(prompt.id, field, value)}
                onUseStartingDraft={() => insertGroundedStartingDraft(prompt)}
              />
            ))}
          </div>
        ) : (
          <p className="rounded-xl border border-dashed border-zinc-300 p-4 text-sm text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
            No story is suggested until approved evidence is mapped. Nothing is generated to fill the gap.
          </p>
        )}

        <div className="flex flex-wrap items-center gap-3 border-t border-zinc-200 pt-5 dark:border-zinc-800">
          <button type="button" disabled={!canSave} onClick={() => void save()} className={primaryButtonClasses}>
            {saving ? "Saving…" : retryPending ? "Retry exact save" : "Save owner-authored preparation"}
          </button>
          {projection.latest_revision ? (
            <span className="text-xs text-zinc-500">Saved revision {projection.latest_revision.revision_number} · {formatDate(projection.latest_revision.created_at)}</span>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function PromptEditor({
  prompt,
  index,
  draft,
  disabled,
  onChange,
  onUseStartingDraft,
}: {
  prompt: InterviewPreparationPrompt;
  index: number;
  draft: InterviewPreparationStarDraft;
  disabled: boolean;
  onChange: (field: keyof InterviewPreparationStarDraft, value: string) => void;
  onUseStartingDraft: () => void;
}) {
  const baseId = `interview-prompt-${prompt.id}`;
  const startingDraft = prompt.starting_draft;
  const hasResultStarter = Boolean(startingDraft?.draft.result.trim());
  return (
    <details open={index === 0} className="rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
      <summary className="cursor-pointer font-semibold">
        <span className="ml-2 text-xs font-medium uppercase tracking-wide text-violet-700 dark:text-violet-300">
          {categoryLabel(prompt.category)}
        </span>
        <span className="mt-2 block text-sm leading-6">{prompt.question}</span>
      </summary>
      <div className="mt-4 space-y-4">
        {startingDraft ? (
          <div className="rounded-lg border border-violet-200 bg-violet-50 p-3 text-sm dark:border-violet-900 dark:bg-violet-950/25">
            <p className="text-xs font-semibold uppercase tracking-wide text-violet-700 dark:text-violet-300">
              Grounded starting outline · not saved
            </p>
            <p className="mt-3 text-xs font-semibold uppercase tracking-wide text-zinc-500">
              What this story must demonstrate
            </p>
            <p className="mt-1 leading-6">{prompt.requirement_text}</p>
            <p className="mt-3 text-xs font-semibold uppercase tracking-wide text-zinc-500">
              Facts still missing from a truthful answer
            </p>
            <ul className="mt-2 list-disc space-y-1 pl-5 leading-6">
              {startingDraft.missing_facts.map((fact) => (
                <li key={fact}>{MISSING_FACT_COPY[fact]}</li>
              ))}
            </ul>
            {hasResultStarter ? (
              <div className="mt-3 rounded-md border border-violet-200 bg-white p-3 dark:border-violet-900 dark:bg-zinc-950/40">
                <p className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
                  Exact approved outcome available for Result
                </p>
                <p className="mt-1 leading-6">{startingDraft.draft.result}</p>
                <button
                  type="button"
                  disabled={disabled || Boolean(draft.result.trim())}
                  onClick={onUseStartingDraft}
                  className={`${secondaryButtonClasses} mt-3`}
                >
                  {draft.result.trim() ? "Keep existing Result" : "Use exact result as a starter"}
                </button>
                <p className="mt-2 text-xs leading-5 text-zinc-600 dark:text-zinc-400">
                  This fills only an empty Result field. It never replaces your text, saves, or approves the answer.
                </p>
              </div>
            ) : (
              <p className="mt-3 text-xs leading-5 text-zinc-600 dark:text-zinc-400">
                No approved statement explicitly names an outcome, so Result stays blank instead of guessing.
              </p>
            )}
          </div>
        ) : null}
        <div className="rounded-lg bg-violet-50 p-3 text-sm dark:bg-violet-950/25">
          <p className="text-xs font-semibold uppercase tracking-wide text-violet-700 dark:text-violet-300">Approved evidence · reference, not an answer</p>
          {prompt.evidence.map((evidence) => (
            <div key={`${evidence.id}:${evidence.version}`} className="mt-2">
              <p className="leading-6">{evidence.statement}</p>
              <p className="mt-1 text-xs text-zinc-500">Evidence v{evidence.version}{evidence.source_resume_version_id ? " · linked to a saved resume" : ""}</p>
              {evidence.source_excerpt ? <p className="mt-1 border-l-2 border-violet-300 pl-2 text-xs text-zinc-600 dark:text-zinc-400">{evidence.source_excerpt}</p> : null}
            </div>
          ))}
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          {(["situation", "task", "action", "result"] as const).map((field) => (
            <label key={field} htmlFor={`${baseId}-${field}`} className="block text-sm font-medium capitalize">
              {field}
              <textarea
                id={`${baseId}-${field}`}
                value={draft[field]}
                disabled={disabled}
                maxLength={3000}
                onChange={(event) => onChange(field, event.target.value)}
                className={`${textareaClasses} mt-2`}
                placeholder={`Write the ${field} in your own words. Leave blank if the evidence does not support it.`}
              />
            </label>
          ))}
        </div>
      </div>
    </details>
  );
}

function ReadOnlyPrompt({ prompt }: { prompt: InterviewPreparationPrompt }) {
  return (
    <article className="rounded-lg border border-zinc-200 bg-white p-3 text-sm dark:border-zinc-800 dark:bg-zinc-900">
      <h4 className="font-medium">{categoryLabel(prompt.category)}</h4>
      <p className="mt-1 text-zinc-600 dark:text-zinc-400">{prompt.question}</p>
      <dl className="mt-3 grid gap-2 sm:grid-cols-2">
        {(["situation", "task", "action", "result"] as const).map((field) => (
          <div key={field}>
            <dt className="text-xs font-semibold uppercase tracking-wide text-zinc-500">{field}</dt>
            <dd className="mt-1 whitespace-pre-wrap">{prompt.draft[field] || "Not written"}</dd>
          </div>
        ))}
      </dl>
    </article>
  );
}

function PreparationStatus({ status }: { status: ApplicationInterviewPreparationResponse["status"] }) {
  const classes = status === "ready"
    ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
    : status === "blocked"
      ? "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200"
      : "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200";
  return <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${classes}`}>{status.replaceAll("_", " ")}</span>;
}

function ContextItem({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-xs font-medium uppercase tracking-wide text-zinc-500">{label}</dt><dd className="mt-1 break-words font-medium">{value}</dd></div>;
}

function categoryLabel(category: InterviewPreparationPrompt["category"]): string {
  return category.replaceAll("_", " ");
}

function sameDraft(left: InterviewPreparationStarDraft, right: InterviewPreparationStarDraft): boolean {
  return left.situation === right.situation && left.task === right.task && left.action === right.action && left.result === right.result;
}
