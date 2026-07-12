# Phase 0 — Durable Foundation

Phase 0 is complete. Practical mode uses migrated Postgres as the sole system
of record for the owner session, encrypted hunt request, generic job state,
encrypted result, and encrypted outcome log. The old SQLite repository remains
available only when `ENABLE_PRACTICAL_MODE=0`; there is no dual write or silent
fallback between the two backends.

## Delivered through step 0F

- Pinned SQLAlchemy 2, Alembic, psycopg 3, and Postgres 16 development runtime
- Explicit database lifecycle and Alembic revisions `20260711_0001`,
  `20260711_0002`, and `20260712_0003`
- Private owners and hashed, revocable, opaque browser sessions
- Practical-mode owner authentication and origin checks before paid hunt work
- Generic background jobs, append-only events, worker heartbeats, scheduler
  slot deduplication, and owner-scoped dedupe keys
- Lease fencing, retry/dead-letter handling, cancellation acknowledgement, and
  stale-job recovery
- Strict queue payload policy: flat record IDs and bounded execution config
  only; arbitrary or nested free text is rejected
- Capability-aware `/ready`: active work without a fresh compatible worker
  returns 503
- Owner-scoped `/api/health` queue counts
- Docker Compose topology for Postgres, migration, web, worker, and frontend
- Same-origin Next.js proxy with header/cookie allowlists, byte limits, bounded
  upstream responses, and timeouts
- Authenticated frontend shell, stable payload-derived idempotency keys, and
  bounded polling retry while retaining the last good run state
- Deterministic OpenAPI snapshot, generated TypeScript types used by the fetch
  layer, runtime checks for critical responses, and CI schema-drift enforcement
- GitHub CI with a mandatory Postgres service, migration drift check, full
  backend suite, frontend checks/build, and production dependency audit
- Owner-scoped encrypted `hunt_runs` and append-only encrypted `hunt_outcomes`
- Atomic hunt/job creation with owner-scoped idempotency and ID-only queue
  payloads
- Practical worker dispatch from the generic Postgres queue, including lease
  renewal, retries, dead-letter recovery, cancellation fencing, and restart
  recovery
- Cookie-authorized run reads and mutations, so private run links work in a new
  tab without browser `sessionStorage`
- Practical retention and cleanup through Postgres; production no longer
  requires a shared SQLite path or operator bearer token

## Correctness properties now covered

- The same job key deduplicates within one owner, but never across owners.
- Owner ID `system` cannot collide with ownerless system work.
- An expired lease cannot heartbeat, update stage, fail, or complete work.
- A cancellation request wins over completion, failure, and lease recovery.
- Resume/message-shaped queue payloads are rejected and distinctive private
  test strings are absent from persisted queue JSON and events.
- Readiness unions all fresh worker capabilities and reports unsupported active
  job kinds explicitly.
- Private operational counts exclude other owners and system jobs.
- Lost HTTP responses or double clicks reuse the same hunt idempotency key.
- Idempotent replay rotates the legacy compatibility capability without
  extending the original privacy-retention deadline.
- Practical run requests, results, and outcome notes are encrypted at rest;
  queue rows contain only an opaque hunt ID.
- Another owner receives 404 for every practical run operation, even when they
  possess the returned legacy capability.
- A stale worker cannot store a result, cancellation wins over finalization,
  and an already encrypted result can be recovered without repeating paid work.
- Practical mode never creates or reads the SQLite compatibility file; legacy
  mode never advertises a Postgres worker capability.

## Verification status

The full hermetic backend suite passes with `468 passed`, `7 skipped`, and `16`
subtests. The frontend API-contract check, lint, typecheck, and production build
also pass. The suite covers the practical API lifecycle, encrypted repository,
migration round trip, worker restart, lost leases, cancellation, owner
isolation, exact outcome validation, and the untouched legacy path.

All five real-Postgres gates pass, covering `SKIP LOCKED`, concurrent queue and
hunt deduplication, cross-owner isolation, and owner-session persistence.
Postgres `alembic check` reports no drift. CI supplies `TEST_DATABASE_URL`, so
those tests cannot become optional release skips.

The rebuilt Docker stack passed the signed-in practical smoke through the
Next.js proxy, FastAPI, generic worker, and Postgres: migration/readiness,
idempotent replay, three mock roles, exactly five contacts per role, encrypted
storage, cookie-only new-tab access, outcome logging, deletion, and logout
locking. Container logs were clean and the stack was stopped with named volumes
preserved.

## Next: Phase 1

Phase 1 adds a persistent candidate profile, career tracks, approved resume
evidence, and saved searches. That is the first step toward a daily opportunity
radar; the monolithic hunt remains useful while those normalized workflows are
built on this foundation.

## Local verification

```bash
.venv/bin/python -m pytest -q

cd frontend
npm run api:check
npm run lint
npm run typecheck
npm run build
```

With Docker Desktop available:

```bash
docker compose build migrate web worker
docker compose up -d postgres
docker compose run --rm migrate
DATABASE_URL=postgresql+psycopg://job_hunt:job_hunt@localhost:5432/job_hunt \
  .venv/bin/python -m alembic check
TEST_DATABASE_URL=postgresql+psycopg://job_hunt:job_hunt@localhost:5432/job_hunt \
  .venv/bin/python -m pytest tests/test_job_queue_postgres.py -q
docker compose up web worker frontend
```

Generate the private owner credential once with:

```bash
.venv/bin/python scripts/generate_owner_token.py
```

Save the plaintext token in a password manager and put only the printed hash in
`JOB_HUNT_OWNER_TOKEN_HASH`.
