import Link from "next/link";
import { notFound } from "next/navigation";
import { getRun } from "@/lib/api";
import type { OutreachDraft } from "@/lib/types";
import { RoleCard } from "@/components/role-card";

type ReviewPageProps = {
  params: Promise<{ runId: string }>;
};

export default async function ReviewPage(props: ReviewPageProps) {
  const { runId } = await props.params;
  const detail = await getRun(runId);
  if (!detail) notFound();

  const { hunt_result } = detail;
  const draftsByRoleUrl = new Map<string, OutreachDraft[]>();
  for (const draft of hunt_result.outreach) {
    const list = draftsByRoleUrl.get(draft.role.url) ?? [];
    list.push(draft);
    draftsByRoleUrl.set(draft.role.url, list);
  }

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">
      <nav className="mb-6 flex items-center justify-between text-sm">
        <Link
          href="/"
          className="text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
        >
          ← New hunt
        </Link>
        <Link
          href={`/runs/${runId}/outcomes`}
          className="inline-flex h-9 items-center rounded-md bg-indigo-600 px-4 text-xs font-medium text-white hover:bg-indigo-700"
        >
          Log outcomes →
        </Link>
      </nav>

      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">
          Hunt review
        </h1>
        <p className="mt-1 text-sm text-zinc-500">
          {hunt_result.roles.length} role
          {hunt_result.roles.length === 1 ? "" : "s"} •{" "}
          {hunt_result.outreach.length} draft
          {hunt_result.outreach.length === 1 ? "" : "s"}
        </p>
        <p className="mt-1 font-mono text-[11px] text-zinc-400">
          run_id: {hunt_result.run_id}
        </p>
      </header>

      <div className="space-y-6">
        {hunt_result.roles.map((role) => (
          <RoleCard
            key={role.url}
            role={role}
            drafts={draftsByRoleUrl.get(role.url) ?? []}
          />
        ))}
      </div>
    </main>
  );
}
