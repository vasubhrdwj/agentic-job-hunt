# Source outage

Job sources are external and can fail independently. The expected degraded
behavior is explicit source warnings, preserved previously saved
opportunities, and no fabricated replacement listings.

## Detect

- `/ready` diagnoses database/worker compatibility, not third-party source
  availability.
- Inspect the saved-search scan detail for failed source counts and stable
  error codes such as `source_fetch_failed` or `source_unavailable`.
- Confirm whether one company/source or every source is failing. Never copy raw
  provider errors, credentials, or response bodies into a shared ticket.

## Respond

1. Pause repeated manual retries; they can amplify rate limits.
2. Confirm the worker is fresh and the queue is progressing through
   authenticated `/api/health`.
3. Check provider status, credential validity, quota, DNS, and TLS from the
   worker environment.
4. Run one bounded saved search after recovery. Do not run a legacy hunt as a
   source-health test.
5. Verify prior opportunities remain visible and the new scan records which
   sources recovered.

If only one source is down, continue reviewing preserved opportunities and
other successful sources. If all sources are down, communicate that discovery
is paused; application tracking, follow-ups, weekly review, and interview
preparation remain useful because they are database-backed.

The automated regression covering this contract is
`test_source_failure_preserves_previously_saved_opportunities` in
`tests/test_opportunity_scan_worker.py`.
