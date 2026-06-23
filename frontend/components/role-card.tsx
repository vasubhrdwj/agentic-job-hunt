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
            Apply ↗
          </a>
        </div>
        <p className="mt-3 text-sm text-zinc-700 dark:text-zinc-300">
          {role.summary}
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Badge>{sourceLabel(role.source)}</Badge>
          <Badge>{role.employment_type.replaceAll("_", " ")}</Badge>
          {role.posted_at && <Badge>posted {role.posted_at.split("T")[0]}</Badge>}
          {!role.posted_at && role.source_updated_at && (
            <Badge>updated {role.source_updated_at.split("T")[0]}</Badge>
          )}
          {typeof role.fit_score === "number" && (
            <Badge>{Math.round(role.fit_score * 100)}% resume fit</Badge>
          )}
          <Badge>{Math.round(role.confidence * 100)}% source confidence</Badge>
        </div>
        <p className="mt-2 text-xs text-zinc-500">
          <span className="font-medium">Match:</span> {role.match_reason}
        </p>
      </header>

      <div className="grid gap-3 md:grid-cols-3">
        {drafts.length === 0 ? (
          <p className="text-sm text-zinc-500">
            No verified current-employee contacts were found. Try the company
            team page or a LinkedIn employee search instead of trusting a weak
            match.
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

function sourceLabel(source: Role["source"]) {
  if (source === "bespoke") return "first-party";
  return source.replaceAll("_", " ");
}

function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-full bg-zinc-100 px-2 py-1 text-[10px] font-medium capitalize text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
      {children}
    </span>
  );
}
