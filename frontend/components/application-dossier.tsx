"use client";

import Link from "next/link";
import { Fragment, useCallback, useEffect, useState } from "react";

import { getApplication, getApplicationArtifacts } from "@/lib/application-api";
import type { ApplicationArtifactsResponse } from "@/lib/application-artifact-types";
import {
  applicationDossierLayout,
  applicationDossierNeedsArtifactBootstrap,
  interviewHistoryIsKnownEmpty,
  type ApplicationDossierSection,
} from "@/lib/application-dossier-layout";
import type { InterviewHistoryState } from "@/lib/application-interview-types";
import type {
  ApplicationDetailResponse,
  ApplicationStage,
} from "@/lib/application-types";
import { ApplicationActivity } from "./application-activity";
import { ApplicationUndoPursuit } from "./application-undo-pursuit";
import { ApplicationMaterials } from "./application-materials";
import { ApplicationInterviewRounds } from "./application-interview-rounds";
import { ApplicationInterviewPreparation } from "./application-interview-preparation";
import { ApplicationPack } from "./application-pack";
import { ApplicationPeople } from "./application-people";
import { ApplicationProgress } from "./application-progress";
import { ApplicationSubmission } from "./application-submission";
import { ApplicationStageBadge, DueDate } from "./applications-workspace";
import {
  errorText,
  formatDate,
  secondaryButtonClasses,
  StatusMessage,
} from "./workspace-ui";

export function ApplicationDossier({
  applicationId,
  ownerLocalDate,
  ownerTimezone,
}: {
  applicationId: string;
  ownerLocalDate: string;
  ownerTimezone: string;
}) {
  const [detail, setDetail] = useState<ApplicationDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [currentArtifacts, setCurrentArtifacts] = useState<ApplicationArtifactsResponse | null>(null);
  const [materialsRefreshNonce, setMaterialsRefreshNonce] = useState(0);
  const [interviewHistoryState, setInterviewHistoryState] =
    useState<InterviewHistoryState>("checking");
  const [secondaryLoaded, setSecondaryLoaded] = useState(false);

  const load = useCallback(async (): Promise<boolean> => {
    setError(null);
    try {
      setDetail(await getApplication(applicationId));
      return true;
    } catch (reason) {
      setError(errorText(reason, "Unable to load this application."));
      return false;
    } finally {
      setLoading(false);
    }
  }, [applicationId]);

  const refreshApplication = useCallback(async (): Promise<void> => {
    await load();
  }, [load]);

  const refreshMaterialsAfterReview = useCallback(() => {
    setMaterialsRefreshNonce((current) => current + 1);
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => void load(), 0);
    return () => clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    const application = detail?.application;
    if (!application || !applicationDossierNeedsArtifactBootstrap(application.stage)) {
      return;
    }
    let active = true;
    void getApplicationArtifacts(application.id)
      .then((next) => {
        if (!active || next.application_id !== application.id) return;
        setCurrentArtifacts((current) =>
          current?.application_id === application.id ? current : next,
        );
      })
      .catch(() => {
        // Outreach safely stays blank without approved grounding. Artifact errors
        // remain isolated from the application dossier and can be retried by the
        // materials workspace when the owner opens it.
      });
    return () => {
      active = false;
    };
  }, [detail]);

  if (loading) {
    return <p role="status" className="text-sm text-zinc-500">Loading application dossier…</p>;
  }
  if (!detail) {
    return (
      <StatusMessage kind="error">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span>{error ?? "This application is unavailable."}</span>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void load()}
              className={secondaryButtonClasses}
            >
              Try again
            </button>
            <Link href="/applications" className={secondaryButtonClasses}>
              Back to applications
            </Link>
          </div>
        </div>
      </StatusMessage>
    );
  }

  const application = detail.application;
  const posting = application.posting;
  const layout = applicationDossierLayout(application.stage);
  const effectiveInterviewHistoryState = interviewHistoryIsKnownEmpty(application.stage)
    ? "none"
    : interviewHistoryState;

  function renderSection(section: ApplicationDossierSection) {
    if (section === "application_pack") {
      return (
        <ApplicationPack
          key={`pack:${application.id}`}
          applicationId={application.id}
          applicationVersion={application.version}
          applicationStage={application.stage}
          onReviewed={refreshMaterialsAfterReview}
        />
      );
    }
    if (section === "application_materials") {
      return (
        <ApplicationMaterials
          key={`materials:${application.id}:${materialsRefreshNonce}`}
          applicationId={application.id}
          applicationVersion={application.version}
          applicationStage={application.stage}
          onArtifactsChanged={setCurrentArtifacts}
        />
      );
    }
    if (section === "application_submission") {
      return (
        <ApplicationSubmission
          applicationId={application.id}
          applicationVersion={application.version}
          stage={application.stage}
          postingState={posting.state}
          ownerLocalDate={ownerLocalDate}
          currentArtifacts={currentArtifacts}
          onApplicationChanged={refreshApplication}
        />
      );
    }
    if (section === "people") {
      return (
        <ApplicationPeople
          key={`people:${application.id}`}
          applicationId={application.id}
          applicationVersion={application.version}
          applicationStage={application.stage}
          postingState={posting.state}
          roleTitle={posting.title}
          companyName={posting.company}
          applicationArtifacts={currentArtifacts}
          ownerLocalDate={ownerLocalDate}
          ownerTimezone={ownerTimezone}
          interviewHistoryState={effectiveInterviewHistoryState}
        />
      );
    }
    if (section === "interview_rounds") {
      return (
        <ApplicationInterviewRounds
          applicationId={application.id}
          applicationVersion={application.version}
          applicationStage={application.stage}
          ownerLocalDate={ownerLocalDate}
          ownerTimezone={ownerTimezone}
          onApplicationChanged={load}
          onHistoryChanged={setInterviewHistoryState}
        />
      );
    }
    if (section === "interview_preparation") {
      return (
        <ApplicationInterviewPreparation
          key={`interview-preparation:${application.id}:${application.version}`}
          applicationId={application.id}
        />
      );
    }
    return (
      <ApplicationProgress
        applicationId={application.id}
        applicationVersion={application.version}
        stage={application.stage}
        outcome={application.outcome}
        scheduledInterviewRoundId={application.current_action?.interview_round_id ?? null}
        ownerLocalDate={ownerLocalDate}
        onApplicationChanged={refreshApplication}
      />
    );
  }

  return (
    <div className="min-w-0 space-y-6">
      <Link
        href="/applications"
        className="inline-flex min-h-10 items-center text-sm font-medium text-zinc-600 hover:text-zinc-950 dark:text-zinc-400 dark:hover:text-white"
      >
        ← Back to applications
      </Link>
      {error ? <StatusMessage kind="error">{error}</StatusMessage> : null}

      <article
        aria-labelledby="application-dossier-title"
        className="min-w-0 rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm sm:p-7 dark:border-zinc-800 dark:bg-zinc-900/70"
      >
        <div className="flex min-w-0 flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <ApplicationStageBadge stage={application.stage} outcome={application.outcome} />
              <span className="text-xs text-zinc-500">{posting.company}</span>
            </div>
            <h2
              id="application-dossier-title"
              className="mt-3 break-words text-2xl font-semibold tracking-tight"
            >
              {posting.title}
            </h2>
            <p className="mt-2 text-sm text-zinc-500">
              Started {formatDate(application.created_at)}
            </p>
          </div>
          <a
            href={posting.canonical_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex min-h-11 shrink-0 items-center justify-center rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
          >
            {posting.first_party ? "Open first-party posting ↗" : "Open source posting ↗"}
          </a>
        </div>
        {!posting.first_party ? (
          <p className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
            This destination has not been verified as first-party. Review it before entering personal information.
          </p>
        ) : null}
        {posting.state !== "open" ? (
          <p className="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900 dark:border-red-900 dark:bg-red-950/30 dark:text-red-100">
            This posting is now {posting.state}. Verify availability before spending more time on it.
          </p>
        ) : null}
      </article>

      {application.current_action ? (
        <section
          aria-labelledby="next-action-title"
          className="rounded-2xl border border-indigo-200 bg-indigo-50 p-5 sm:p-7 dark:border-indigo-900 dark:bg-indigo-950/25"
        >
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-indigo-700 dark:text-indigo-300">
            Do next
          </p>
          <h2 id="next-action-title" className="mt-2 text-xl font-semibold text-indigo-950 dark:text-indigo-100">
            {application.current_action.title}
          </h2>
          <DueDate
            action={application.current_action}
            ownerLocalDate={ownerLocalDate}
            className="mt-3"
          />
          <p className="mt-4 max-w-2xl text-sm leading-6 text-indigo-900 dark:text-indigo-200">
            {nextActionGuidance(application.stage)}
          </p>
        </section>
      ) : application.stage !== "closed" ? (
        <StatusMessage kind="error">
          This active application has no next action. Refresh before recording more progress.
        </StatusMessage>
      ) : null}

      {layout.primary.map((section) => (
        <Fragment key={section}>{renderSection(section)}</Fragment>
      ))}

      <details
        className="group rounded-2xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900/70"
        onToggle={(event) => {
          if (event.currentTarget.open) {
            setSecondaryLoaded(true);
          }
        }}
      >
        <summary className="cursor-pointer list-none px-5 py-5 sm:px-6 [&::-webkit-details-marker]:hidden">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h2 className="font-semibold">Other actions, history, and saved records</h2>
              <p className="mt-1 text-sm leading-6 text-zinc-500">
                Review older materials, correct the record, or inspect saved role details.
              </p>
            </div>
            <span aria-hidden="true" className="text-zinc-400 transition group-open:rotate-180">
              ↓
            </span>
          </div>
        </summary>

        <div className="space-y-6 border-t border-zinc-200 px-5 py-6 sm:px-6 dark:border-zinc-800">
          {secondaryLoaded ? (
            <>
              {layout.secondary.map((section) => (
                <Fragment key={section}>{renderSection(section)}</Fragment>
              ))}

              <ApplicationUndoPursuit
                applicationId={application.id}
                stage={application.stage}
                onApplicationChanged={refreshApplication}
              />

              <ApplicationActivity
                key={`activity:${application.id}`}
                applicationId={application.id}
                applicationVersion={application.version}
                activity={detail.activity}
                ownerLocalDate={ownerLocalDate}
                onApplicationChanged={load}
              />
            </>
          ) : null}

          <section className="rounded-2xl border border-zinc-200 bg-white p-5 sm:p-6 dark:border-zinc-800 dark:bg-zinc-900/70">
            <h2 className="font-semibold">Role record</h2>
            <dl className="mt-4 grid gap-4 text-sm sm:grid-cols-3">
              <div>
                <dt className="text-xs font-medium uppercase tracking-wide text-zinc-500">Company</dt>
                <dd className="mt-1 break-words font-medium">{posting.company}</dd>
              </div>
              <div>
                <dt className="text-xs font-medium uppercase tracking-wide text-zinc-500">Posting status</dt>
                <dd className="mt-1 capitalize">{posting.state}</dd>
              </div>
              <div>
                <dt className="text-xs font-medium uppercase tracking-wide text-zinc-500">Captured version</dt>
                <dd className="mt-1 break-all font-mono text-xs">{application.pursued_posting_version_id}</dd>
              </div>
            </dl>
            <Link
              href={`/jobs/${encodeURIComponent(application.opportunity_id)}`}
              className={`${secondaryButtonClasses} mt-5`}
            >
              Review saved opportunity
            </Link>
          </section>
        </div>
      </details>

    </div>
  );
}

function nextActionGuidance(stage: ApplicationStage): string {
  if (stage === "ready_to_apply") {
    return "Open the verified employer destination, submit the reviewed materials yourself, then record exactly what you used.";
  }
  if (stage === "applied") {
    return "Follow up on schedule. When an employer confirms an interview appointment, record it in Interview rounds below.";
  }
  if (stage === "screening") {
    return "Complete the recruiter follow-up, then schedule the exact interview round only after the employer confirms it.";
  }
  if (stage === "interviewing") {
    return "Complete the interview follow-up and keep the next dated task current while you wait for a confirmed decision.";
  }
  if (stage === "offer") {
    return "Review the offer carefully and record the final decision by the saved response deadline.";
  }
  return "Review the role, ground every claim in approved evidence, and prepare the exact materials you will submit.";
}
