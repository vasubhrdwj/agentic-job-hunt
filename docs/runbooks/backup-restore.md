# Backup and restore

## Recovery targets

Choose an RPO/RTO appropriate for a personal job search. A reasonable starting
point is one daily backup stored encrypted, seven daily copies, four weekly
copies, and a quarterly restore drill. Store the archive and its `.manifest.json`
sidecar together in encrypted storage outside the application host.

The tool supports SQLite and PostgreSQL, creates a consistent archive, records
the migration revision and a password-free source identity hash, and verifies
integrity. It never overwrites a backup and never restores into a non-empty
target.

## Create and verify

Set credentials through the environment so passwords do not appear in process
arguments:

```bash
export DATABASE_URL='postgresql+psycopg://...'
BACKUP="backups/job-hunt-$(date -u +%Y%m%dT%H%M%SZ).dump"
.venv/bin/python -m scripts.database_backup create "$BACKUP"
.venv/bin/python -m scripts.database_backup verify "$BACKUP" --expect-current
```

Use a `.sqlite` suffix for a SQLite source; backend selection comes from
`DATABASE_URL`. A successful backup has two files. Missing either file is a
failed backup. Do not rename one without the other.

## Restore drill or recovery

1. Provision a new, empty database. Never point restore at the live database.
2. Stop any web/worker process configured for the new target.
3. Set `DATABASE_URL` to the empty target and restore explicitly:

```bash
export DATABASE_URL='postgresql+psycopg://...empty-target...'
.venv/bin/python -m scripts.database_backup restore "$BACKUP" --confirm-empty-target
.venv/bin/python -m scripts.migration_gate check
```

4. Start one web process and one compatible worker against the restored target.
5. Run the [deployment smoke](deploy-rollback.md#deployment-smoke).
6. Sign in and verify representative non-sensitive counts and recent records.
7. For a drill, destroy the isolated restored target after recording the
   result. For recovery, switch traffic only after verification.

`pg_dump` and `pg_restore` are included in the runtime image. PostgreSQL
passwords are passed through `PGPASSWORD`, never command-line arguments.

## Failure rules

- A checksum, archive, migration, or identity mismatch is a hard failure.
- Keep the damaged database read-only for investigation; restore into a new
  target and cut over.
- Keep every encryption key referenced by retained rows. Rotating the active
  key does not decrypt an old archive if the old key was discarded.
- Never use a backup from another database to authorize a schema downgrade.
