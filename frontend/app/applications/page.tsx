import { redirect } from "next/navigation";

import { ApplicationsWorkspace } from "@/components/applications-workspace";
import { WorkspaceHeader } from "@/components/workspace-header";
import { getServerOwnerSession } from "@/lib/server-session";

export const dynamic = "force-dynamic";

export default async function ApplicationsPage() {
  const session = await getServerOwnerSession();
  if (!session) redirect("/login");

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6 sm:py-12">
      <WorkspaceHeader
        active="applications"
        title="Applications"
        description="Keep every pursued role tied to a clear, dated next action. This workspace reads only your persisted application records."
      />
      <ApplicationsWorkspace ownerLocalDate={session.local_date} />
    </main>
  );
}
