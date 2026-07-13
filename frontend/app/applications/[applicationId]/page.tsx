import { redirect } from "next/navigation";

import { ApplicationDossier } from "@/components/application-dossier";
import { WorkspaceHeader } from "@/components/workspace-header";
import { getServerOwnerSession } from "@/lib/server-session";

export const dynamic = "force-dynamic";

export default async function ApplicationDetailPage({
  params,
}: {
  params: Promise<{ applicationId: string }>;
}) {
  const session = await getServerOwnerSession();
  if (!session) redirect("/login");
  const { applicationId } = await params;

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8 sm:px-6 sm:py-12">
      <WorkspaceHeader
        active="applications"
        title="Application dossier"
        description="A practical record of the role you chose to pursue, the posting version you acted on, your dated next step, and immutable activity."
      />
      <ApplicationDossier
        key={applicationId}
        applicationId={applicationId}
        ownerLocalDate={session.local_date}
      />
    </main>
  );
}
