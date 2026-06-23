import { InputForm } from "@/components/input-form";

export default function HomePage() {
  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-12 sm:py-16">
      <header className="mb-10">
        <h1 className="text-3xl font-semibold tracking-tight">
          Job Hunt Signal
        </h1>
        <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
          Find fresh first-party roles, rank them against your resume, and draft
          outreach only when a current employee can be verified.
        </p>
      </header>
      <InputForm />
    </main>
  );
}
