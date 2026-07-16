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
5. Start the new web and worker versions. Do not route traffic until `/ready`
   is 200: it requires a reachable database, current migration, fresh worker,
   and support for every active job kind.
6. Run the deployment smoke below, then sign in and inspect Today and one
   existing application without launching provider work.

## Deployment smoke

The smoke calls only liveness, readiness, and the legacy policy rejection. It
does not run a hunt, scan, contact search, or any paid provider.

```bash
.venv/bin/python -m scripts.deployment_smoke \
  --base-url https://jobs.example.com \
  --expect-legacy-mode read_only
```

To prove readiness survives an intentional restart:

```bash
.venv/bin/python -m scripts.deployment_smoke \
  --base-url https://jobs.example.com \
  --expect-legacy-mode read_only \
  --snapshot-out /tmp/job-hunt-before-restart.json
# Restart web and worker through the deployment platform, then wait for /ready.
.venv/bin/python -m scripts.deployment_smoke \
  --base-url https://jobs.example.com \
  --expect-legacy-mode read_only \
  --compare-snapshot /tmp/job-hunt-before-restart.json
```

## Rollback

Prefer an application-only rollback when the previous application supports the
current schema. Keep the database at the newer revision and run the smoke.

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

After any rollback, start matched web/worker versions, require `/ready`, run the
smoke, and verify owner login. If downgrade refuses, do not edit migration
history manually; restore the verified backup into an empty target or deploy a
forward fix.
