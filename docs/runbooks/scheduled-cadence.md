# Scheduled cadence and daily digest

The free production topology uses Vercel as an external clock and the sleeping
Render web service as the durable scheduler/worker host. No paid scheduler or
email service is required.

## Configure

1. Generate one random value of at least 32 characters, for example
   `openssl rand -hex 32`.
2. Add that value as `CRON_SECRET` in the Vercel project.
3. Add the same value as `CRON_SECRET` in the Render web service.
4. Redeploy both services. Vercel reads [`frontend/vercel.json`](../../frontend/vercel.json)
   and registers the daily `/api/internal/cadence` invocation.

This secret authenticates service-to-service cadence traffic. It is not shown
to users and is unrelated to email/password login.

## What a wake does

1. Vercel sends its automatic bearer header to the server-only Next.js route.
2. Next.js checks the header in constant time and forwards one POST to
   `/internal/cadence/tick` on the configured `API_BASE_URL`.
3. Render cold-starts. FastAPI starts its embedded worker, then the tick locks
   due saved searches and enqueues bounded durable scan jobs.
4. PostgreSQL slot dedupe makes a retry, overlapping request, or embedded tick
   converge on one scan and one queue job.
5. The embedded worker drains work while the service is awake. Lost leases are
   recovered on the next wake.

Vercel Hobby currently permits a once-daily schedule with up to ±59 minutes of
timing precision. The configured 04:00 UTC wake prioritizes the India morning
use case. Vercel does not retry a failed cron invocation. Any normal visit to
the app also wakes Render, immediately runs the same embedded scheduler tick,
and catches up overdue daily, weekday, or weekly searches; the next daily cron
is the second fallback. The product does not claim minute-precise execution on
the free topology.

## Verify

- In Vercel, confirm the cron appears and inspect its function logs.
- A successful response reports only counts plus `embedded_worker_alive=true`;
  it never includes search criteria, database details, or the secret.
- In Render, confirm a fresh worker heartbeat and successful
  `scan_saved_search` jobs through authenticated `/api/health`.
- In Today, confirm the digest's local date/timezone and open one highlighted
  role to verify its fit reasons.

For a continuously awake worker or Render Cron-compatible host, the equivalent
one-shot enqueue command is:

```bash
python -m scripts.run_cadence
```

If a wake fails, do not manually edit `next_scan_at`. Restore the service or
secret and retry the same wake. Slot identity and queue dedupe make replay the
safe recovery path.
