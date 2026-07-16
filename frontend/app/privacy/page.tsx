import { redirect } from "next/navigation";

import { PrivacyWorkspace } from "@/components/privacy-workspace";
import { WorkspaceHeader } from "@/components/workspace-header";
import { getServerOwnerSession } from "@/lib/server-session";


export const dynamic = "force-dynamic";

export default async function PrivacyPage() {
  const session = await getServerOwnerSession();
  if (!session) redirect("/login");

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6 sm:py-12">
      <WorkspaceHeader
        active="privacy"
        title="Privacy & data"
        description="Export what you own, control bounded legacy-run retention, understand provider-side limits, or permanently delete the local workspace."
      />
      <PrivacyWorkspace />
    </main>
  );
}
