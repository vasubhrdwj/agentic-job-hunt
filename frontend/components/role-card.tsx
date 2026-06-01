import type { OutreachDraft, Role } from "@/lib/types";
import { DraftCard } from "./draft-card";

export function RoleCard({
  role,
  drafts,
}: {
  role: Role;
  drafts: OutreachDraft[];
}) {
  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <header className="mb-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">
              {role.title}
            </h2>
            <p className="text-sm text-zinc-500">
              {role.company} • {role.location}
            </p>
          </div>
          <a
            href={role.url}
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 text-xs text-indigo-600 hover:underline dark:text-indigo-400"
          >
            Posting ↗
          </a>
        </div>
        <p className="mt-3 text-sm text-zinc-700 dark:text-zinc-300">
          {role.summary}
        </p>
        <p className="mt-2 text-xs text-zinc-500">
          <span className="font-medium">Match:</span> {role.match_reason}
        </p>
      </header>

      <div className="grid gap-3 md:grid-cols-3">
        {drafts.length === 0 ? (
          <p className="text-sm text-zinc-500">
            No drafts were generated for this role.
          </p>
        ) : (
          drafts.map((draft) => (
            <DraftCard key={draft.draft_id} draft={draft} />
          ))
        )}
      </div>
    </section>
  );
}
