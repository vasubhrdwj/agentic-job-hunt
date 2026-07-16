# Incident recovery

## Stabilize

1. Record UTC time, deployed commit, affected environment, and symptoms.
2. Stop new provider-consuming work if correctness, credentials, or ownership
   isolation is uncertain. Keep `/health` available when safe.
3. Do not delete queues, databases, backups, or encryption keys. Preserve logs
   and a verified backup.
4. Classify the incident: database/readiness, worker/queue, source provider,
   authentication, encryption/decryption, privacy deletion, or frontend-only.

## Diagnose safely

- `/health` 200 proves only that the web process is alive.
- `/ready` must show database reachability, exact migration parity, a fresh
  compatible worker, and no unsupported active job kinds.
- Authenticated `/api/health` adds owner-scoped queue counts without private
  payloads.
- Use stable request IDs and coarse error codes. Do not log resume text,
  messages, notes, cookies, owner tokens, data keys, or provider credentials.

## Recover

- **Web/worker mismatch:** deploy matched versions and wait for a fresh
  heartbeat; do not force-complete leased jobs.
- **Database unavailable:** restore connectivity first. If corruption is
  suspected, follow [backup and restore](backup-restore.md) into an empty
  target.
- **Migration mismatch:** stop traffic and use the
  [deploy/rollback runbook](deploy-rollback.md). Never edit `alembic_version`
  directly.
- **Source outage:** follow [source outage](source-outage.md); preserved jobs and
  opportunities are not cleanup candidates.
- **Credential exposure:** revoke the exposed provider/owner credential,
  invalidate affected sessions, rotate it, and verify no secret entered logs.
  During data-key rotation, retain old keys until every referenced ciphertext
  has been re-encrypted or expired.
- **Privacy/deletion failure:** stop retries if ownership is uncertain. Preserve
  payload-free receipts and audit metadata; never reconstruct deleted content.

## Close

Require `/ready`, run the provider-free deployment smoke, verify login and a
representative read-only workflow, then monitor one queue cycle. Document user
impact, data impact, recovery point, contributing cause, and a concrete
preventive test. A process restart alone is not evidence of recovery.
