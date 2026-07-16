# Legacy hunt deprecation and import

The practical owner workspace is the supported product path. The legacy
`/api/hunt` and `/api/runs/*` API has an explicit three-state policy:

| Mode | Create/outcome/cancel/requeue | Exact run GET/HEAD | Exact run DELETE |
| --- | --- | --- | --- |
| `enabled` | Allowed by legacy authorization | Allowed | Allowed |
| `read_only` | `410 legacy_read_only` | Allowed | Allowed as a privacy escape |
| `disabled` | `410 legacy_disabled` | `410` | `410` |

Read-only exceptions apply only to `/api/runs/{run_id}`. Future nested routes
do not inherit DELETE access. Every compatibility response, including errors,
includes `Deprecation`, `Sunset`, a deprecation `Link`, mode, and request ID.
Production requires an HTTPS deprecation URL and refuses invalid/past sunset
metadata while the API is enabled or read-only.

## Import completed SQLite history

The importer opens the source SQLite file read-only, never reruns providers,
and never deletes source data. It imports only valid completed results into an
existing owner. Active/failed states, incompatible schemas, conflicts, invalid
payloads, and records outside the owner's current retention window are
reported explicitly.

Configure the target URL and real encryption key through the environment:

```bash
export DATABASE_URL='postgresql+psycopg://...'
export JOB_HUNT_DATA_KEYS='v1:...'
.venv/bin/python -m scripts.import_legacy_hunts \
  --source /secure/path/outcomes.db \
  --owner-id owner
```

The default is a dry run. Save its JSON report, resolve every `failed` entry,
confirm `expired` and `unsupported` records are intentionally left in the
source, create a target backup, then apply:

```bash
.venv/bin/python -m scripts.import_legacy_hunts \
  --source /secure/path/outcomes.db \
  --owner-id owner \
  --apply
```

Re-running is idempotent when the decrypted target result and outcomes match.
The deterministic development key is never silently selected; its explicit
escape flag is forbidden in production.

## Sunset

1. Deploy `read_only` and import/verify wanted history.
2. Keep exact GET and DELETE available through the announced window.
3. Change to `disabled` after the sunset and run the deployment smoke with
   `--expect-legacy-mode disabled`.
4. Preserve the legacy source according to the user's backup policy. Disabling
   an API is not permission to delete its database.
