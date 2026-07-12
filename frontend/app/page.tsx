import { InputForm } from "@/components/input-form";
import { WorkspaceHeader } from "@/components/workspace-header";
import { getServerOwnerSession } from "@/lib/server-session";
import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const session = await getServerOwnerSession();
  if (!session) redirect("/login");

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-12 sm:py-16">
      <WorkspaceHeader
        active="hunt"
        title="Run a focused hunt"
        description="Find fresh first-party roles, rank them against your resume, and prepare outreach for five appropriate contacts per role when the evidence supports them."
      />
      <InputForm />
    </main>
  );
}
