import { Suspense } from "react";
import { redirect } from "next/navigation";

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
        description="Review persisted, deduplicated roles from your saved searches. Unknown facts and degraded sources stay visible; opening this page never searches the web or calls a model."
      />
      <Suspense fallback={<p role="status" className="text-sm text-zinc-500">Loading your persisted opportunity inbox…</p>}>
        <TodayWorkspace />
      </Suspense>
    </main>
  );
}
