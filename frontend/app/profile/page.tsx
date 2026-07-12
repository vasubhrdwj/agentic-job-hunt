import { redirect } from "next/navigation";

import { ProfileWorkspace } from "@/components/profile-workspace";
import { WorkspaceHeader } from "@/components/workspace-header";
import { getServerOwnerSession } from "@/lib/server-session";

export const dynamic = "force-dynamic";

export default async function ProfilePage() {
  const session = await getServerOwnerSession();
  if (!session) redirect("/login");

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10 sm:py-14">
      <WorkspaceHeader
        active="profile"
        title="Your job-search foundation"
        description="Save your resume, preferences, career targets, and approved evidence once. You stay in control: nothing on this page searches the web or calls an AI provider."
      />
      <ProfileWorkspace />
    </main>
  );
}
