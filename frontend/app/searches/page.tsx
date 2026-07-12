import { redirect } from "next/navigation";

import { SearchesWorkspace } from "@/components/searches-workspace";
import { WorkspaceHeader } from "@/components/workspace-header";
import { getServerOwnerSession } from "@/lib/server-session";

export const dynamic = "force-dynamic";

export default async function SearchesPage() {
  const session = await getServerOwnerSession();
  if (!session) redirect("/login");

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10 sm:py-14">
      <WorkspaceHeader
        active="searches"
        title="Saved searches"
        description="Remember strong search setups, inspect their exact inputs, and run them deliberately. Scheduled preferences are stored, but automatic scanning is not live yet."
      />
      <SearchesWorkspace />
    </main>
  );
}
