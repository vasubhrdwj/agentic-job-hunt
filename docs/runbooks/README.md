# Operational runbooks

These runbooks are the supported operator path for the practical job-search
workspace. They favor preserving user data, explicit dry runs, and observable
readiness over in-place repair.

- [Backup and restore](backup-restore.md)
- [Deploy and rollback](deploy-rollback.md)
- [Source outage](source-outage.md)
- [Incident recovery](incident-recovery.md)
- [Legacy hunt deprecation and import](legacy-hunt-deprecation.md)
- [Manual browser matrix](manual-browser-matrix.md)
- [Scheduled cadence and daily digest](scheduled-cadence.md)

Before any production action, record the deployed commit, database identity,
UTC start time, and operator. Keep `DATABASE_URL` and encryption keys in the
environment or secret manager; never paste them into tickets, shell history,
or command arguments.
