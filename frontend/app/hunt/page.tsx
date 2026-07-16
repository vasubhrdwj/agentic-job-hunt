import Link from "next/link";

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
        title="Legacy hunt archive"
        description="New legacy hunts are retired. Exact links to retained runs remain readable and deletable until their retention period expires."
      />
      <section className="rounded-2xl border border-amber-200 bg-amber-50 p-6 text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
        <h2 className="text-lg font-semibold">Use the practical workspace for new work</h2>
        <p className="mt-2 max-w-3xl text-sm leading-6">
          Saved searches now handle role discovery and send deduplicated opportunities
          to Today. This archive never starts provider calls, drafts outreach, or creates
          new legacy runs.
        </p>
        <div className="mt-5 flex flex-wrap gap-3">
          <Link
            href="/searches"
            className="inline-flex min-h-11 items-center justify-center rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2"
          >
            Open saved searches
          </Link>
          <Link
            href="/today"
            className="inline-flex min-h-11 items-center justify-center rounded-lg border border-amber-300 bg-white px-4 py-2 text-sm font-medium text-amber-950 transition hover:border-amber-500 hover:bg-amber-100 focus:outline-none focus:ring-2 focus:ring-amber-600 focus:ring-offset-2 dark:border-amber-800 dark:bg-zinc-900 dark:text-amber-100 dark:hover:bg-amber-950"
          >
            Open Today
          </Link>
        </div>
      </section>
    </main>
  );
}
