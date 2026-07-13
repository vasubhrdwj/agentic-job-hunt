import { InputForm } from "@/components/input-form";
import { WorkspaceHeader } from "@/components/workspace-header";
import { getServerOwnerSession } from "@/lib/server-session";
import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

export default async function HuntPage() {
  const session = await getServerOwnerSession();
  if (!session) redirect("/login");

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-12 sm:py-16">
      <WorkspaceHeader
        active="hunt"
        title="Run a full one-off hunt"
        description="Legacy workflow: search now, compare your resume, discover referral contacts, and draft outreach with explicit provider consent. Its results remain separate from the durable Today inbox."
      />
      <InputForm />
    </main>
  );
}
