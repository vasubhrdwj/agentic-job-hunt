import { redirect } from "next/navigation";

import { AccountWorkspace } from "@/components/account-workspace";
import { WorkspaceHeader } from "@/components/workspace-header";
import { getServerOwnerSession } from "@/lib/server-session";

export const dynamic = "force-dynamic";

export default async function AccountPage({
  searchParams,
}: {
  searchParams: Promise<{ secured?: string | string[] }>;
}) {
  const session = await getServerOwnerSession();
  if (!session) redirect("/login");
  const { secured } = await searchParams;

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10 sm:py-14">
      <WorkspaceHeader
        active="account"
        title="Account"
        description="See how this workspace is secured without exposing credentials or mixing your job-search data with another account."
      />
      <AccountWorkspace initialSession={session} secured={secured === "1"} />
    </main>
  );
}
