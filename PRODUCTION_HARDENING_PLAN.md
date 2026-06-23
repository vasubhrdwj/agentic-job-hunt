# Production hardening plan

Last updated: 2026-06-23

This document defines the work remaining after the main implementation work in
`REBUILD_PLAN_V2.md`. The feature work is substantial, but the V2 rebuild is
not complete because its live release gate still requires at least three
qualifying roles and the 2026-06-23 run returned two. The application is also
not ready for unrestricted user traffic.

Each phase below is accepted only after its code, automated checks, browser QA,
independent review, sole-author commit, and push have all completed.

## Release standard

A phase may be marked complete only when:

1. Backend tests, frontend lint, TypeScript, and the production build pass.
2. New failure modes have explicit tests.
3. The affected user flow is exercised in a real browser at desktop and mobile
   widths with no unexplained console errors.
4. The diff receives an independent review.
5. The phase is committed without co-author trailers and pushed to
   `origin/v2-rebuild`.
6. Manual verification steps are documented.

The release is user-ready only when every blocking gate in the final section is
green.

## Phase 0 — Baseline and release contract

Status: QA executed; acceptance completes when this phase commit is pushed

Delivered:

- Captured the current automated and browser QA baseline.
- Converted the remaining risks into explicit implementation phases.
- Added a repeatable acceptance standard and manual verification checklist.

Evidence:

- `367 passed, 2 skipped, 16 subtests passed`
- Frontend lint and TypeScript checks pass.
- Next.js production build passes.
- Browser QA passes for form validation, mock hunt submission, result review,
  outcome persistence, desktop layout, mobile layout, and console errors.

Known production blockers:

- `POST /api/hunt` holds one HTTP request open for the full pipeline.
- SQLite data is configured on an ephemeral deployment path.
- The API has no user authentication or abuse-rate boundary.
- Google GenAI/ADK tracing can export resume-derived prompt content to Phoenix;
  production tracing is enabled and input redaction is not configured.
- Resume retention, deletion, encryption, and redaction behavior is not yet
  enforced in code.
- Live role supply is market-dependent; the strict gate returned two qualifying
  roles on 2026-06-23, so the V2 release gate of at least three remains red.
- The final container image and deployed topology still need end-to-end
  verification.

Manual verification:

```bash
.venv/bin/pytest -q
cd frontend
npm run lint
npx tsc --noEmit
npm run build
```

Run the local product with mock providers:

```bash
USE_MOCKS=1 ENABLE_TRACING=0 \
JOB_HUNT_DB_PATH=/tmp/job-hunt-manual.db \
ALLOWED_ORIGINS=http://localhost:3000 \
.venv/bin/uvicorn job_hunt_agent.api:app --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
cd frontend
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000 \
npm run dev -- --hostname 127.0.0.1 --port 3000
```

Browser checklist:

1. Open `http://127.0.0.1:3000` at a desktop viewport.
2. Submit the empty form and confirm `Paste a resume before running the hunt.`
   appears.
3. Enter `Backend engineer with Python, Go, APIs, distributed systems,
   PostgreSQL, and cloud deployment experience.` and submit.
4. Confirm the progress screen appears, then navigation reaches
   `/runs/<run_id>`.
5. With mocks, confirm the review contains 3 roles and 9 drafts.
6. Open `Log outcomes`, mark the first draft `Replied`, enter a note, save, and
   confirm the `Saved 1 outcome.` acknowledgement.
7. Reload the outcome page and confirm the saved entry is returned by
   `GET /api/runs/<run_id>`.
8. Repeat the home-page layout check at `390×844`; confirm there is no
   horizontal overflow and every control remains reachable.
9. Inspect the browser console and confirm there are no warnings or errors.

## Phase 1 — Privacy and data-ownership foundation

Status: in progress

Goal: make resume handling safe before any queue or durable database stores it.

Planned deliverables:

- Stop Google GenAI and ADK instrumentation from exporting raw prompts,
  completions, resume excerpts, API keys, or authorization data.
- Add automated span-export tests using a unique resume marker to prove that
  sensitive input never leaves the process through tracing.
- Add explicit UI disclosure and consent before any resume-derived content is
  sent to the configured model provider.
- Minimize provider input to the smallest role-relevant excerpt rather than the
  complete resume, and document the provider's current data-use, retention, and
  deletion limits. Application deletion must not falsely claim to retract data
  a provider has already processed.
- Enforce resume/request size limits before provider work begins.
- Add an expiring, revocable run-access capability and store only its hash so
  knowing a `run_id` is insufficient to read or mutate a run.
- Send capabilities only in response bodies and authorization headers; never
  place them in URLs, query strings, logs, traces, analytics, or plaintext
  server storage.
- Define the queued-request envelope with encrypted resume content, key
  rotation metadata, creation/expiry timestamps, and explicit deletion.
- Add retention cleanup and a delete-run operation that removes request,
  result, and outcome data.
- Redact request bodies and sensitive fields from application logs and error
  responses.

Manual acceptance:

1. Submit a resume containing a unique marker and confirm the marker is absent
   from application logs and captured/exported spans.
2. Decline model-provider consent and confirm no provider request occurs.
3. Accept consent and inspect the provider request fixture to confirm only the
   bounded, role-relevant excerpt is sent.
4. Confirm provider retention/deletion limitations are visible before consent
   and in the privacy documentation.
5. Confirm a run cannot be read, changed, or deleted without its unexpired,
   unrevoked access capability.
6. Inspect URLs, headers, logs, traces, and raw server storage to confirm the
   plaintext capability appears only in its authorized response/header path.
7. Confirm oversized resumes fail before any provider call.
8. Inspect raw database bytes/rows and confirm a unique resume marker is absent
   while an encrypted queued request exists.
9. Verify encrypt/decrypt round-trip, old-key decryption during rotation, new
   writes using the active key, and production startup failing closed when the
   key is missing or wrong. There must be no plaintext fallback.
10. Delete a run and confirm its request, result, and outcomes are gone.
11. Run retention cleanup and confirm only expired data is removed.

## Phase 2 — Durable queued hunts

Status: pending

Goal: remove the long-running HTTP request from the user path without creating
duplicate or unbounded paid-provider work.

Planned deliverables:

- Persist the encrypted request envelope before processing starts.
- Make `POST /api/hunt` return a run identifier and queued status immediately.
- Add queued, running, succeeded, failed, cancelled, and dead-letter states.
- Add user cancellation for queued/running jobs and an authorized operator
  requeue path for dead-letter jobs, both with auditable transitions.
- Add idempotency keys for duplicate client submissions.
- Add worker leases, lease expiry, heartbeats, bounded retries, and stale-job
  recovery after worker death.
- Prevent two workers from holding the same active lease.
- Track stage completion so a retry does not repeat completed provider work.
- Do not claim exactly-once external side effects: ambiguous provider failures
  must stop for bounded retry or operator resolution rather than loop.
- Make the frontend poll run status, survive refreshes, and display terminal
  failures, cancellation, and retry state clearly.
- Preserve the existing successful result and outcome APIs.

Manual acceptance:

1. Submit a hunt and confirm the initial response is returned quickly.
2. Refresh while the hunt is running and confirm progress remains available.
3. Submit the same idempotency key twice and confirm only one run is queued.
4. Start two workers and confirm only one receives the active lease.
5. Kill a worker after claim, allow the lease to expire, and confirm another
   worker recovers the run.
6. Inject repeated failure and confirm retries stop at the configured limit
   with a visible terminal state.
7. Cancel a queued run and a running run; confirm workers stop safely and
   terminal cancellation is visible after refresh.
8. Move a poison job to dead-letter, confirm normal users cannot requeue it,
   then requeue it through the authorized operator path and inspect the audit
   record.
9. Confirm a successful run still supports outcome logging.

## Phase 3 — Production persistence and worker topology

Status: pending

Goal: make runs, outcomes, and queue state survive deploys and service restarts.

Planned deliverables:

- Add a production database backend and schema migration path.
- Keep SQLite as a simple local-development option.
- Separate web and worker process commands in deployment configuration.
- Add database readiness checks and safe concurrency/claim semantics.
- Add documented backup, restore, migration, and failed-migration recovery
  procedures.
- Verify restart, redeploy, duplicate-delivery, and stale-job recovery behavior.

Manual acceptance:

1. Create a run and an outcome.
2. Restart both web and worker services.
3. Confirm the run, result, and outcome still exist.
4. Restore a backup into a clean database and confirm the run is readable.
5. Apply migrations from the previous schema version and verify data.
6. Inject a failed migration and confirm startup fails safely without partial
   schema application or data loss.

## Phase 4 — Authentication and abuse controls

Status: pending

Goal: replace capability-only access with real user ownership and protect paid
provider and worker capacity.

Planned deliverables:

- Add an authenticated user/session ownership boundary for every run and
  outcome.
- Add rate limits, per-user concurrency limits, and global queue backpressure.
- Prevent one user from reading or mutating another user's runs.
- Add secure defaults and startup validation for production.

Manual acceptance:

1. Confirm unauthenticated or incorrectly owned run access is rejected.
2. Confirm excessive submissions and concurrency receive clear errors.
3. Confirm queue saturation fails fast rather than consuming unbounded memory
   or provider calls.
4. Confirm production refuses to start with unsafe authentication, CORS, data
   key, or rate-limit configuration.

## Phase 5 — Source reliability and the unmet V2 release gate

Status: pending

Goal: satisfy the original V2 live gate without weakening evidence rules.

Planned deliverables:

- Add scheduled registry/source health reporting.
- Record per-source availability, freshness, rejection reasons, and qualifying
  role counts.
- Expand or repair the curated company pack based on verified first-party
  sources.
- Keep runtime behavior honest when fewer than three roles qualify.
- Keep the release gate red until a generic-backend/India hunt returns at least
  three qualifying roles, unless the user explicitly approves a requirement
  change.
- Keep the product explicit when fewer than three roles satisfy every filter.

Manual acceptance:

1. Run registry verification and confirm dead sources are identified.
2. Run a live hunt and inspect every apply URL and evidence field.
3. Confirm the V2 release command returns at least three qualifying roles.
4. Separately confirm low-supply criteria produce an honest degraded runtime
   result, never fabricated or aggregator-backed roles.

## Phase 6 — Staging, exhaustive QA, and release handoff

Status: pending

Goal: prove the complete system in the environment actual users will reach.

Planned deliverables:

- Build and run the production container.
- Deploy web, worker, database, and frontend to staging.
- Run exhaustive desktop/mobile browser QA and failure injection.
- Verify tracing, health/readiness checks, alerts, and operational recovery.
- Synchronize README and operations documentation.
- Produce the final user-readiness verdict with remaining limitations.

Manual acceptance:

1. Complete a real hunt from the staged frontend.
2. Refresh during processing and recover after a service restart.
3. Confirm persisted results and outcomes after a redeploy.
4. Verify access controls with two separate users.
5. Confirm all first-party apply links resolve.
6. Confirm no high- or critical-severity QA findings remain.

## Final user-readiness gates

- [ ] Hunt creation returns promptly and processing is asynchronous.
- [ ] Resume content is absent from traces/logs and encrypted while queued.
- [ ] Run access requires a valid owner session or access capability.
- [ ] Queue state and user data survive restarts and deploys.
- [ ] Runs and outcomes are isolated by authenticated owner.
- [ ] Abuse limits protect provider quotas and worker capacity.
- [ ] Resume retention and deletion behavior is implemented and documented.
- [ ] The original live V2 gate returns at least three qualifying roles, or a
      requirement change is explicitly approved.
- [ ] Live source monitoring and honest degraded runtime behavior are
      operational.
- [ ] Production container and staging deployment pass exhaustive QA.
- [ ] Operational recovery and monitoring are documented.
- [ ] No critical or high-severity defects remain.
