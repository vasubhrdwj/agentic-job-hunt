import { redirect } from "next/navigation";

import { ProfileWorkspace } from "@/components/profile-workspace";
import { WorkspaceHeader } from "@/components/workspace-header";
import { getServerOwnerSession } from "@/lib/server-session";

export const dynamic = "force-dynamic";

export default async function ProfilePage({
  searchParams,
}: {
  searchParams: Promise<{ welcome?: string | string[] }>;
}) {
  const session = await getServerOwnerSession();
  if (!session) redirect("/login");
  const { welcome } = await searchParams;
  const showWelcome = welcome === "1";

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10 sm:py-14">
      <WorkspaceHeader
        active="profile"
        title="Your job-search foundation"
        description="Save your resume, preferences, career targets, and approved evidence once. You stay in control: nothing on this page searches the web or calls an AI provider."
      />
      {showWelcome ? (
        <div
          role="status"
          className="mb-6 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm leading-6 text-emerald-950 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-100"
        >
          <p className="font-medium">Your account is ready.</p>
          <p className="mt-1">
            Add your basic preferences and resume here first. The app will use
            this foundation to assess roles and prepare grounded application help.
          </p>
        </div>
      ) : null}
      <ProfileWorkspace />
    </main>
  );
}
