import Link from "next/link";

import { SessionStatus } from "@/components/session-status";

type WorkspaceSection =
  | "today"
  | "applications"
  | "review"
  | "searches"
  | "profile"
  | "account"
  | "privacy"
  | "hunt";

const NAV_ITEMS: Array<{
  href: string;
  label: string;
  section: WorkspaceSection;
}> = [
  { href: "/today", label: "Today", section: "today" },
  { href: "/applications", label: "Applications", section: "applications" },
  { href: "/review", label: "Weekly review", section: "review" },
  { href: "/searches", label: "Saved searches", section: "searches" },
  { href: "/profile", label: "Profile", section: "profile" },
  { href: "/account", label: "Account", section: "account" },
  { href: "/privacy", label: "Privacy", section: "privacy" },
];

export function WorkspaceHeader({
  active,
  title,
  description,
}: {
  active: WorkspaceSection;
  title: string;
  description: string;
}) {
  return (
    <header className="mb-10 space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-zinc-500">
            Job Hunt Signal
          </p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight">{title}</h1>
        </div>
        <SessionStatus />
      </div>
      <p className="max-w-2xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
        {description}
      </p>
      <nav aria-label="Workspace navigation" className="flex flex-wrap gap-2">
        {NAV_ITEMS.map((item) => {
          const selected = item.section === active;
          return (
            <Link
              key={item.section}
              href={item.href}
              aria-current={selected ? "page" : undefined}
              className={`rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ${
                selected
                  ? "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-950"
                  : "border-zinc-300 bg-white text-zinc-600 hover:border-zinc-500 hover:text-zinc-950 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:border-zinc-500 dark:hover:text-white"
              }`}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
