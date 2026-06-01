import Link from "next/link";
import { notFound } from "next/navigation";
import { getRun } from "@/lib/api";
import { OutcomeForm } from "@/components/outcome-form";

type OutcomesPageProps = {
  params: Promise<{ runId: string }>;
};

export default async function OutcomesPage(props: OutcomesPageProps) {
  const { runId } = await props.params;
  const detail = await getRun(runId);
  if (!detail) notFound();

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-10">
      <nav className="mb-6 flex items-center justify-between text-sm">
        <Link
          href={`/runs/${runId}`}
          className="text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
        >
          ← Back to review
        </Link>
        <Link
          href="/"
          className="text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
        >
          New hunt
        </Link>
      </nav>

      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">
          Log outcomes
        </h1>
        <p className="mt-1 text-sm text-zinc-500">
          Record what happened with each draft. Submit appends to the log;
          nothing overwrites.
        </p>
        <p className="mt-1 font-mono text-[11px] text-zinc-400">
          run_id: {detail.hunt_result.run_id}
        </p>
      </header>

      <OutcomeForm
        runId={runId}
        huntResult={detail.hunt_result}
        previousOutcomes={detail.outcomes}
      />
    </main>
  );
}
