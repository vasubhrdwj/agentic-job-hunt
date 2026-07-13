import Link from "next/link";

export default function PrivacyPage() {
  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-12 sm:py-16">
      <Link
        href="/hunt"
        className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
      >
        ← Back to hunt
      </Link>

      <article className="mt-8 space-y-8">
        <header>
          <h1 className="text-3xl font-semibold tracking-tight">
            Resume processing and privacy
          </h1>
          <p className="mt-2 text-sm text-zinc-500">
            Last reviewed against Google&apos;s official terms on June 23,
            2026.
          </p>
        </header>

        <Section title="What leaves this application">
          <p>
            Job Hunt Signal uses your full resume locally for role-fit scoring.
            When outreach is drafted, it sends only a bounded, role-relevant
            excerpt to the configured paid Gemini API. If no relevant excerpt
            is found, the drafting prompt says so instead of falling back to
            the start of the resume.
          </p>
        </Section>

        <Section title="Google processing">
          <p>
            This deployment must use paid Gemini API quota. Google states that
            paid-service prompts and responses are not used to improve its
            products. Google also states that prompts, context, and outputs are
            retained for 55 days for abuse monitoring and may be reviewed by
            authorized personnel when flagged.
          </p>
          <p>
            Do not use this application if that provider retention is
            unacceptable. Deleting a run here cannot retract data already
            processed under Google&apos;s retention policy.
          </p>
          <p>
            Source:{" "}
            <a
              href="https://ai.google.dev/gemini-api/terms"
              target="_blank"
              rel="noopener noreferrer"
              className="text-indigo-600 underline underline-offset-2 dark:text-indigo-400"
            >
              Gemini API Additional Terms
            </a>{" "}
            and{" "}
            <a
              href="https://ai.google.dev/gemini-api/docs/usage-policies"
              target="_blank"
              rel="noopener noreferrer"
              className="text-indigo-600 underline underline-offset-2 dark:text-indigo-400"
            >
              Gemini API abuse monitoring
            </a>
            .
          </p>
        </Section>

        <Section title="Storage and access">
          <p>
            Resume-bearing requests are encrypted before storage and removed
            after synchronous processing finishes. Results and outcome logs are
            private to an expiring browser-session capability and are retained
            for up to 30 days unless you delete the run sooner.
          </p>
          <p>
            The capability is kept in this tab&apos;s browser session storage
            and sent only in an Authorization header. Opening a private run in
            another browser session will not grant access.
          </p>
        </Section>

        <Section title="Tracing">
          <p>
            Model prompts, model responses, authorization data, and resume text
            are redacted from exported observability spans. Operational
            metadata such as run identifiers, role counts, source types, and
            evaluation scores may still be traced.
          </p>
        </Section>

        <Section title="Deletion">
          <p>
            Use the Delete run action on a review page to remove the stored
            result, encrypted request metadata, and outcome history. Expired
            runs are also removed by retention cleanup.
          </p>
        </Section>
      </article>
    </main>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      <h2 className="text-xl font-semibold">{title}</h2>
      <div className="space-y-3 text-sm leading-6 text-zinc-700 dark:text-zinc-300">
        {children}
      </div>
    </section>
  );
}
