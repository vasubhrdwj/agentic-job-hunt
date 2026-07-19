"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { todayRecommendationReadinessIssues } from "@/lib/today-recommendation-readiness";
import type { TodayRecommendationReadinessIssue } from "@/lib/today-recommendation-readiness";
import {
  getCandidateProfile,
  listCareerTracks,
  listEvidence,
  listResumeVersions,
} from "@/lib/workspace-api";
import { secondaryButtonClasses } from "./workspace-ui";

export function TodayRecommendationReadiness() {
  const [issues, setIssues] = useState<TodayRecommendationReadinessIssue[] | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([
      getCandidateProfile(),
      listResumeVersions(),
      listCareerTracks(),
      listEvidence(),
    ])
      .then(([profile, resumeVersions, careerTracks, evidence]) => {
        if (!active) return;
        setIssues(todayRecommendationReadinessIssues({
          profile: profile?.data ?? null,
          resumeVersions,
          careerTracks,
          evidence,
        }));
      })
      .catch(() => {
        // Readiness guidance must never block the persisted Today inbox.
      });
    return () => {
      active = false;
    };
  }, []);

  if (!issues?.length) return null;

  return (
    <aside
      aria-labelledby="today-recommendation-readiness-title"
      className="rounded-xl border border-violet-200 bg-violet-50/70 p-4 dark:border-violet-900 dark:bg-violet-950/20"
    >
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h2
            id="today-recommendation-readiness-title"
            className="font-semibold text-violet-950 dark:text-violet-100"
          >
            Make recommendations more certain
          </h2>
          <p className="mt-1 text-sm leading-6 text-violet-900 dark:text-violet-200">
            Add these details once and the app can reuse them for eligibility,
            fit, and application help. You can keep reviewing roles now.
          </p>
          <ul className="mt-3 flex flex-wrap gap-2" aria-label="Missing recommendation inputs">
            {issues.map((issue) => (
              <li
                key={issue.id}
                className="rounded-lg border border-violet-200 bg-white px-3 py-2 text-xs text-violet-950 dark:border-violet-800 dark:bg-violet-950/40 dark:text-violet-100"
              >
                <span className="font-semibold">{issue.label}</span>
                <span className="ml-1 text-violet-700 dark:text-violet-300">
                  {issue.impact}
                </span>
              </li>
            ))}
          </ul>
        </div>
        <Link
          href="/profile"
          aria-label="Add missing recommendation inputs in Profile"
          className={`${secondaryButtonClasses} shrink-0 text-center`}
        >
          Improve my profile
        </Link>
      </div>
    </aside>
  );
}
