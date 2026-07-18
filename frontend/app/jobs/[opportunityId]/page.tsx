import { redirect } from "next/navigation";

import { OpportunityReview } from "@/components/opportunity-review";
import { WorkspaceHeader } from "@/components/workspace-header";
import { getServerOwnerSession } from "@/lib/server-session";

export const dynamic = "force-dynamic";

export default async function JobReviewPage({
  params,
  searchParams,
}: {
  params: Promise<{ opportunityId: string }>;
  searchParams: Promise<{ saved_search_id?: string | string[] }>;
}) {
  const session = await getServerOwnerSession();
  if (!session) redirect("/login");
  const { opportunityId } = await params;
  const { saved_search_id: savedSearchValue } = await searchParams;
  const savedSearchId = typeof savedSearchValue === "string"
    ? savedSearchValue
    : null;

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8 sm:px-6 sm:py-12">
      <WorkspaceHeader
        active="today"
        title="Review opportunity"
        description="Inspect the preserved source facts, explicit unknowns, deterministic resume evidence, and saved-search provenance before deciding."
      />
      <OpportunityReview
        opportunityId={opportunityId}
        savedSearchId={savedSearchId}
        ownerLocalDate={session.local_date}
        ownerTimezone={session.timezone}
      />
    </main>
  );
}
