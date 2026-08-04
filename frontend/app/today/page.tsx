import { Suspense } from "react";
import { redirect } from "next/navigation";

import { TodayApplicationActions } from "@/components/today-application-actions";
import { TodayDailyDigest } from "@/components/today-daily-digest";
import { TodayRecommendationReadiness } from "@/components/today-recommendation-readiness";
import { TodayWorkspace } from "@/components/today-workspace";
import { WorkspaceHeader } from "@/components/workspace-header";
import { getServerOwnerSession } from "@/lib/server-session";

export const dynamic = "force-dynamic";

export default async function TodayPage() {
  const session = await getServerOwnerSession();
  if (!session) redirect("/login");

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6 sm:py-12">
      <WorkspaceHeader
        active="today"
        title="Today"
        description="Handle due application work first, then review persisted, deduplicated roles from your saved searches. This page reads saved data only; unknown facts and degraded sources stay visible."
      />
      <div className="space-y-8">
        <TodayDailyDigest />
        <TodayApplicationActions />
        <TodayRecommendationReadiness />
        <section aria-label="Opportunity review inbox">
          <Suspense fallback={<p role="status" className="text-sm text-zinc-500">Loading your persisted opportunity inbox…</p>}>
            <TodayWorkspace
              ownerLocalDate={session.local_date}
              ownerTimezone={session.timezone}
            />
          </Suspense>
        </section>
      </div>
    </main>
  );
}
