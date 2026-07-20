# Deploy and rollback

## Deploy

1. Confirm CI passed and record the commit being deployed.
2. Create and verify a uniquely named backup using
   [the backup runbook](backup-restore.md).
3. Verify the current database before changing it:

```bash
export DATABASE_URL='postgresql+psycopg://...'
.venv/bin/python -m scripts.migration_gate check
```

4. Run `.venv/bin/python -m alembic upgrade head` as a one-shot migration
   process.
5. Start the new web version. The free private topology starts its embedded
   interactive worker from the same commit. Do not enable Scan roles until
   authenticated `/api/health` reports `role_scan.available=true`.
   Contact discovery requires `SERPAPI_API_KEY` and must report
   `contact_search.available=true` before the UI can queue work. Set
   `JOB_HUNT_WORKER_KINDS=scan_saved_search` for a provider-free process,
   `scan_saved_search,discover_contacts` for the free interactive process, or
   `legacy_hunt,scan_saved_search,discover_contacts` for a full worker.
   Omitting the variable preserves the full set; blank or unknown values fail
   startup. The CLI `--job-kinds` option overrides the environment.
6. Run the deployment smoke below, then sign in and inspect Today and one
   existing application without launching provider work.

## Deployment smoke

The baseline smoke calls only liveness, readiness, and the legacy policy
rejection. It does not run a hunt, scan, contact search, or any paid provider.

```bash
.venv/bin/python -m scripts.deployment_smoke \
  --base-url https://jobs.example.com \
  --expect-legacy-mode read_only
```

To prove web readiness survives an intentional restart:

```bash
.venv/bin/python -m scripts.deployment_smoke \
  --base-url https://jobs.example.com \
  --expect-legacy-mode read_only \
  --snapshot-out /tmp/job-hunt-before-restart.json
# Restart the web through the deployment platform, then wait for /web-ready.
.venv/bin/python -m scripts.deployment_smoke \
  --base-url https://jobs.example.com \
  --expect-legacy-mode read_only \
  --compare-snapshot /tmp/job-hunt-before-restart.json
```

For the free embedded-worker topology, additionally sign in through the Vercel
origin and run one provider-free saved-search scan. Confirm:

1. authenticated `/api/health` reports the scan capability;
2. the scan reaches a terminal state within ten minutes;
3. the worker heartbeat advertises `scan_saved_search` and advertises
   `discover_contacts` only when SerpAPI is configured;
4. trusted roles, if present, reach Today; and
5. a restart during a test scan leaves a recoverable durable queue record.

## Rollback

Prefer an application-only rollback when the previous application supports the
current schema. Keep the database at the newer revision and run the smoke.

Revision `20260720_0019` changes the login request from the retired shared key
to email/password accounts. Once any credential exists, its downgrade refuses
to discard account access, and application code from before `0019` cannot sign
users in against the new request contract. Back up before this upgrade and use
a forward fix or restore that verified pre-upgrade backup into an empty target;
do not roll only the web service back across this boundary.

A schema downgrade is a last resort. The gate permits only the immediately
previous revision, requires a verified current backup from the exact same
database, and still allows the migration itself to refuse when rows would be
lost:

```bash
export DATABASE_URL='postgresql+psycopg://...'
.venv/bin/python -m scripts.migration_gate downgrade \
  --verified-backup "$BACKUP"
# Review the printed plan and migration downgrade code.
.venv/bin/python -m scripts.migration_gate downgrade \
  --verified-backup "$BACKUP" \
  --apply
```

After any rollback, start matched web/worker code, require `/web-ready`, verify
the configured scan capability, run the smoke, and verify owner login. If
downgrade refuses, do not edit migration history manually; restore the verified
backup into an empty target or deploy a forward fix.
