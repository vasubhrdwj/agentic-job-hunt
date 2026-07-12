import { InputForm } from "@/components/input-form";
import { SessionStatus } from "@/components/session-status";
import { getServerOwnerSession } from "@/lib/server-session";
import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const session = await getServerOwnerSession();
  if (!session) redirect("/login");

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-12 sm:py-16">
      <header className="mb-10">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <h1 className="text-3xl font-semibold tracking-tight">
            Job Hunt Signal
          </h1>
          <SessionStatus />
        </div>
        <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
          Find fresh first-party roles, rank them against your resume, and draft
          outreach only when a current employee can be verified.
        </p>
      </header>
      <InputForm />
    </main>
  );
}
