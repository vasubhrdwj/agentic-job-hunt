import { redirect } from "next/navigation";

import { WeeklyReviewWorkspace } from "@/components/weekly-review-workspace";
import { WorkspaceHeader } from "@/components/workspace-header";
import { getServerOwnerSession } from "@/lib/server-session";

export const dynamic = "force-dynamic";

export default async function WeeklyReviewPage() {
  const session = await getServerOwnerSession();
  if (!session) redirect("/login");

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6 sm:py-12">
      <WorkspaceHeader
        active="review"
        title="Weekly review"
        description="Clear applications that need a decision, then learn from mature outcomes without treating open or missing data as failure."
      />
      <WeeklyReviewWorkspace ownerLocalDate={session.local_date} />
    </main>
  );
}
