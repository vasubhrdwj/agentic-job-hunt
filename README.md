# Job Hunt Signal

An agent that runs a focused, evidence-based job hunt — and **learns to write better outreach from real outcomes in its own traces**. Paste a resume and criteria: it searches verified first-party company boards, filters for freshness and employment type, ranks roles against the resume, and drafts outreach only for contacts whose current employer can be verified. If the evidence is weak, it returns fewer results instead of inventing them.

> Submission for the Arize × Google hackathon (Gemini 3 + Agent Builder + Phoenix).
> **Live app:** https://agentic-job-hunt.vercel.app · **Demo video:** _[add link after upload]_ · **Writeup:** [demo/DEVPOST.md](demo/DEVPOST.md)

![Round comparison: judge scores climb when self-RAG is on](demo/round_comparison.png)

*Same resume, same criteria, judge held constant — round 2 retrieves the agent's best past drafts from Phoenix as exemplars. Drafter: Gemini 3.5 Flash (production config), gap +0.39. The same loop lifts the weaker Gemini 2.5 Flash drafter by [+0.81](demo/round_comparison_gemini25.md) — the agent's own memory helps weaker writers most. Regenerate with `python scripts/compare_rounds.py`.*

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in GOOGLE_API_KEY, SERPAPI_API_KEY, PHOENIX_*
```

## Practical product rebuild

The repository is being upgraded from a one-run hackathon demo into a private
job-search workspace. The implementable product plan is in
[`PRACTICAL_JOB_SEARCH_PLAN.md`](PRACTICAL_JOB_SEARCH_PLAN.md); the delivered
foundation and first reusable job-search workflow are documented in
[`docs/PHASE_0_FOUNDATION.md`](docs/PHASE_0_FOUNDATION.md) and
[`docs/PHASE_1_PROFILE_SEARCH.md`](docs/PHASE_1_PROFILE_SEARCH.md). The manual,
durable opportunity radar is documented in
[`docs/PHASE_2_OPPORTUNITY_RADAR.md`](docs/PHASE_2_OPPORTUNITY_RADAR.md). The
first practical application checkpoint is documented in
[`docs/PHASE_3_APPLICATION_PIPELINE.md`](docs/PHASE_3_APPLICATION_PIPELINE.md).
The verified-contact foundation and its remaining provider/UI checkpoints are
documented in
[`docs/PHASE_4_CONTACT_BENCH.md`](docs/PHASE_4_CONTACT_BENCH.md).

The delivered foundation includes migrated Postgres models, private owner
sessions, owner-scoped generic jobs, lease/cancellation safety, capability-aware
readiness, a bounded same-origin proxy, generated API contracts, and mandatory
Postgres CI gates. Practical hunt requests, results, outcomes, and generic
worker dispatch now use encrypted, owner-scoped Postgres state. SQLite remains
only behind the explicit `ENABLE_PRACTICAL_MODE=0` development compatibility
path.

Phase 1 adds a persistent candidate profile, immutable encrypted resume
versions, career targets, approval-gated achievement evidence, and saved
searches. Phase 2 adds a search-only **Scan roles** action, stable native job
identity, immutable posting versions, source-health warnings, deduplication
across saved searches, and a database-only **Today** inbox. Today exposes
unknown facts and supports durable Watch, Dismiss, and restore decisions; it
never searches live sources, reads a resume, discovers contacts, drafts text,
or calls a model merely because the page was opened.

Phase 3A adds an atomic **Pursue** decision. It creates exactly one application,
one open dated next action, and one immutable creation activity, then exposes a
database-only Applications list and dossier. The only current application stage
is `pursuing`; stage transitions, action updates, and application packs remain
explicit follow-up work.

Phase 4A adds the durable verified-contact bench foundation. Each application
can retain a 12-person evidence-backed discovery pool and a deterministic,
diverse bench of up to five. It preserves the exact public evidence used,
deduplicates normalized profile identities, and reports honest shortfalls such
as `3/5 verified`. Reads are database-only and no outreach is sent. The live
provider-backed worker and dossier controls arrive in the next checkpoints, so
the practical UI does not yet claim that it can start a contact search.

The separate **Legacy hunt** remains available when you explicitly want the
current end-to-end flow with resume matching, at least five appropriate
referral leads per returned role, and draft generation. Cadence preferences
are stored and timezone-correct, but automatic scan dispatch is deliberately
not connected yet.

For the private local workspace:

```bash
.venv/bin/python scripts/generate_owner_token.py  # save token; copy printed env values
docker compose build migrate web worker
docker compose up -d postgres
docker compose run --rm migrate
docker compose up web worker frontend
```

Open <http://localhost:3000>, enter the one-time owner token, and keep
`ENABLE_PRACTICAL_MODE=1` whenever real provider calls are possible.

## Run

```bash
python -m job_hunt_agent.run \
  --resume fixtures/sample_resume.txt \
  --keywords "backend engineer,software engineer,backend developer" \
  --location "India,Remote-India,Bengaluru,Hyderabad" \
  --seniority junior \
  --employment-types full_time \
  --max-age-days 45 \
  --pack backend_india \
  --trace
```

Output is structured JSON: `{ run_id, roles: [...], outreach: [{ role, person, message }, ...] }`. Roles include source, employment type, posting date, source confidence, resume-fit score, and a match reason quoting real job-description evidence.

Add `--trace` to emit OpenTelemetry spans to your Phoenix project, or `--use-mocks` for the no-network smoke loop. The CLI also prints the generated `run_id` so you can correlate the output with a Phoenix trace.

## V2 evidence rules

- `backend_india` contains 20 curated companies with live-verified ATS or first-party career sources.
- Curated hunts do not fall back to paid aggregators. A failed company board degrades to an empty result and a warning.
- Requested employment types are strict. A posting with no explicit employment evidence remains `unknown` and is omitted from a full-time-only hunt.
- Referral candidates require current-employer evidence and confidence of at least 0.5. No verified candidate means no draft, plus honest guidance in the UI.
- Past drafts are retrieved by logged outcome first (`introduced`/`replied` before neutral or `no_reply`), with judge score used only as a tiebreaker.

## The self-improvement loop

Three pieces close the loop; each has a verification command:

1. **Seeded corpus** — 18 synthetic past drafts with a documented quality
   rubric ([fixtures/SEED_NOTES.md](fixtures/SEED_NOTES.md)). Upload:
   `python scripts/seed_phoenix.py --project job-hunt-agent --allow-duplicates --verify`
2. **LLM-as-judge eval** — every draft gets four 1–5 sub-scores
   (personalization, specificity, ask, tone); the composite is written onto
   the draft's Phoenix span. Calibrate the judge against handwritten
   good/bad references before trusting it:
   `python scripts/validate_judge.py`
3. **Self-RAG drafting** — `draft_message` queries Phoenix for the
   top-scoring past drafts on the run's keywords (score ≥ 4) and threads
   them into the prompt as exemplars. A/B it:
   `PHOENIX_QUERY_LOOKBACK_HOURS=720 python scripts/compare_rounds.py --trace`
   writes `demo/round_comparison.md` + `.png` and fails if the self-RAG
   round doesn't beat baseline by +0.3 (gate calibrated for the Gemini 3.5
   Flash drafter; both model's charts live in `demo/`).

Dev extras (matplotlib, pytest): `pip install -r requirements-dev.txt`.

## Verify the company registry

The REG Definition of Done is deliberately live and strict: every active company
must return at least one posting with a matching company identity, where the ATS
provides one, and an apply URL on a configured careers domain.

```bash
.venv/bin/python scripts/verify_registry.py --pack backend_india --live --strict-live
```

This command performs network requests and fails on dead or unverified sources.
`pytest tests/test_registry.py` remains hermetic and never performs live checks.

## Run the API and worker

FastAPI now enqueues encrypted hunt requests for a separate worker process. The
HTTP submit path returns immediately; the worker claims the job, runs
`run_hunt`, and atomically saves the final result.

```bash
uvicorn job_hunt_agent.api:app --reload
python -m job_hunt_agent.worker          # second terminal, long-running loop
python -m job_hunt_agent.worker --once   # process one queued run and exit
```

Endpoints:

```
POST   /api/session                 { owner_token } → HttpOnly owner session
POST   /api/hunt                    { resume_text, criteria, pack, provider_consent: true }
                                    → 202 { run_id, status: queued, access_token }
GET    /api/runs/{run_id}           owner session cookie
                                    → queue state, plus result/outcomes after success
POST   /api/runs/{run_id}/cancel    owner session + allowed Origin
POST   /api/runs/{run_id}/outcomes  owner session + allowed Origin (succeeded only)
DELETE /api/runs/{run_id}           owner session + allowed Origin
POST   /api/runs/{run_id}/requeue   owner session + allowed Origin (dead-letter only)
GET/PUT /api/me/profile             owner profile + current base-resume metadata
GET/POST /api/me/resume-versions    immutable encrypted resume versions
GET/POST /api/me/evidence           approval-gated achievement evidence
PATCH  /api/me/evidence/{id}        edit/review evidence with If-Match
GET/POST /api/career-tracks         reusable career targets
GET/PATCH/DELETE /api/career-tracks/{id}
GET/POST /api/saved-searches        pinned criteria, resume, target, and cadence
GET/PATCH/DELETE /api/saved-searches/{id}
GET    /api/saved-searches/{id}/hunt-input
                                    → provider-free exact hunt prefill
POST   /api/saved-searches/{id}/scans
                                    → 202 durable search-only scan (If-Match + idempotency)
GET    /api/scans/{id}              → persisted progress, counts, and safe source warnings
GET    /api/today                   → database-only deduplicated opportunity inbox
GET    /api/opportunities/{id}      → posting facts, versions, provenance, decision history
POST   /api/opportunities/{id}/decision
                                    → pursue, watch, dismiss, or restore (If-Match + idempotency)
GET    /api/applications            → database-only pursuing applications + next actions
GET    /api/applications/{id}       → application dossier + immutable activity
GET    /api/applications/{id}/activity
                                    → database-only immutable activity stream
GET    /health                      → { ok: true }
GET    /ready                       → DB migration + compatible worker readiness
```

The practical frontend does not store or require the returned access token.
Owner-scoped run links work in a new tab or after a reload through the opaque
HttpOnly session cookie. The response field remains temporarily for the
development-only legacy API compatibility path.

Configure persistence and CORS via env:

- `DATABASE_URL` — Postgres shared by the practical web and worker processes.
- `JOB_HUNT_OWNER_TOKEN_HASH` — SHA-256 of the private owner-login token.
- `JOB_HUNT_DATA_KEYS` — comma-separated `key-id:Fernet-key` values. The first
  key encrypts new requests, results, and outcome notes; retain older keys
  during rotation.
- `ALLOWED_ORIGINS` — comma-separated CORS allowlist (default dev origins for localhost 3000/5173).
- `ENABLE_TRACING` — set to `1` so API-triggered hunts emit Phoenix spans.
- `ENABLE_TRACE_DRAFT_CONTENT` — keep `0` for user traffic. Prompts, model
  outputs, and draft text are private by default.
- `GEMINI_PAID_SERVICE_ACK` — production must set `1` manually after
  confirming the Google API key uses paid Gemini quota.
- `RETENTION_CLEANUP_INTERVAL_SECONDS` — API-triggered expired-data cleanup
  interval. Set `3600` in production. `/health` remains a lightweight liveness
  check during a database outage.
- `JOB_HUNT_DB_PATH` — legacy-only SQLite path when practical mode is explicitly
  disabled; practical production does not require or read it.

`criteria` accepts `employment_types` and `max_age_days`; `pack` defaults to
`backend_india`.

Resume-bearing requests are encrypted before Postgres writes, stay available
only while queued/running/retryable, and are erased when the worker succeeds,
the user cancels, the request expires, or the run is deleted. Results and
outcome notes are encrypted too, and the entire run expires after 30 days via
API-triggered cleanup or `python -m job_hunt_agent.cleanup`. Users can cancel
active runs or delete a run immediately from the review page. See the frontend
`/privacy` page for provider disclosure and retention limits.

Privacy mode intentionally prevents new user draft text from entering the
shared Phoenix corpus. Self-RAG continues to use the curated seed corpus; an
owner-scoped learning store is required before production can learn from
individual users' drafts safely.

## Hosted deployment status

`render.yaml` is now fail-closed: its health check uses `/ready`, while the
web-only free blueprint still lacks a migration predeploy step and a background
worker service. The application code now has a shared Postgres run repository,
but the blueprint must not report a healthy practical deployment until that
worker topology is added. [Render background workers do not have a free
plan](https://render.com/docs/blueprint-spec), so adding one is an explicit
hosting-cost decision rather than a silent default.
The notes below document the earlier demo deployment only; they are not current
production instructions.

A8 deploys the FastAPI backend to Render (free tier) and the Next.js frontend
to Vercel. Render's free tier has no persistent disk, so SQLite lives on the
ephemeral filesystem at `/tmp/outcomes.db`: outcomes persist across requests,
but a redeploy, restart, or idle spin-down wipes them. Two operational rules
follow from that:

> The included free-tier blueprint is for demos and controlled personal use,
> not a practical production service. This branch has a durable Postgres queue
> and worker for local use, but a hosted deployment still needs a deployed
> worker and migration step sharing the same managed Postgres.

- A keep-alive ping on `/health` every 5 minutes prevents the 15-minute idle
  spin-down (which would both wipe state and add a ~1-minute cold start for
  the next visitor).
- Freeze deploys once the demo is recorded — every push redeploys and resets
  the DB. Phoenix traces are the durable record either way.

One-time Render setup:

1. Push the branch containing `render.yaml` to GitHub.
2. Render dashboard → **New → Blueprint** → pick this repo. Render reads
   `render.yaml` and creates the `job-hunt-agent` web service (Docker runtime,
   free plan, Singapore region, health check on `/health`).
3. Fill the required env vars when prompted: `GOOGLE_API_KEY`,
   `SERPAPI_API_KEY`, `PHOENIX_API_KEY`, `PHOENIX_COLLECTOR_ENDPOINT`,
   `DATABASE_URL`, `JOB_HUNT_OWNER_TOKEN_HASH`, `JOB_HUNT_DATA_KEYS`,
   `GEMINI_PAID_SERVICE_ACK`, plus `ALLOWED_ORIGINS`. Generate
   `JOB_HUNT_DATA_KEYS` as described above. For `ALLOWED_ORIGINS` use the
   production Vercel URL once it exists (production startup rejects `*` and
   localhost — use a placeholder like `https://pending.invalid` until the
   Vercel URL is known, then update).
4. Deploys are automatic on every push to the connected branch.
5. Add a free UptimeRobot (or cron-job.org) monitor on
   `https://<service>.onrender.com/health` at a 5-minute interval.

The blueprint sets `ENVIRONMENT=production`, `ENABLE_PRACTICAL_MODE=1`,
`ENABLE_TRACING=1`,
`ENABLE_TRACE_DRAFT_CONTENT=0`, `RETENTION_CLEANUP_INTERVAL_SECONDS=3600`,
`PHOENIX_QUERY_TRANSPORT=rest`, and a 720-hour Phoenix lookback for seeded demo
traces. Startup fails loudly in production if required secrets are missing,
localhost CORS is configured, mocks are enabled, draft content tracing is
enabled, or the database is not Postgres.

One-time Vercel setup:

```bash
cd frontend
cp .env.example .env.local
vercel link
vercel env add API_BASE_URL production
vercel --prod
```

Set the server-only `API_BASE_URL` to `https://<service>.onrender.com` (shown at
the top of the Render service page). After Vercel prints the production URL,
update Render's `ALLOWED_ORIGINS` env var to that exact URL — Render restarts
the service automatically when env vars change.

Queued smoke test:

```bash
API=http://localhost:8000  # web and worker must share DATABASE_URL
ORIGIN=http://localhost:3000
OWNER_TOKEN=<the one-time token saved in your password manager>
curl $API/health
curl -c /tmp/job-hunt-owner.cookies -X POST $API/api/session \
  -H "Origin: $ORIGIN" \
  -H "Content-Type: application/json" \
  -d "{\"owner_token\":\"$OWNER_TOKEN\"}"
curl -b /tmp/job-hunt-owner.cookies -X POST $API/api/hunt \
  -H "Origin: $ORIGIN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: smoke-$(date +%s)" \
  -d @fixtures/sample_hunt_request.json

RUN_ID=<run_id from the hunt response>

python -m job_hunt_agent.worker --once

curl -b /tmp/job-hunt-owner.cookies $API/api/runs/$RUN_ID
DRAFT_ID=<draft_id from the successful GET response>
curl -X POST $API/api/runs/$RUN_ID/outcomes \
  -b /tmp/job-hunt-owner.cookies \
  -H "Origin: $ORIGIN" \
  -H "Content-Type: application/json" \
  -d "{\"outcomes\":[{\"draft_id\":\"$DRAFT_ID\",\"outcome\":\"replied\",\"notes\":\"deploy smoke\"}]}"
```

`/api/hunt` should return queued state promptly. The final `GET` should move
from `queued`/`running` to `succeeded` after a worker processes the run, and the
outcome POST should work only after success. The current free-tier Render
blueprint runs only the web service, so an end-to-end hosted smoke requires a
separate background worker and migration step.

## Abridged response shape

`POST /api/hunt` returns queue state, not the completed hunt:

```json
{
  "run_id": "<trace-correlated id>",
  "status": "queued",
  "stage": "queued",
  "attempt_count": 0,
  "max_attempts": 3,
  "access_token": "<returned once; send only as a bearer token>",
  "reused": false
}
```

After the worker succeeds, `GET /api/runs/{run_id}` returns:

```json
{
  "run_id": "<trace-correlated id>",
  "status": "succeeded",
  "hunt_result": {
    "run_id": "<trace-correlated id>",
    "roles": [
      {
        "company": "<verified company>",
        "title": "<title from posting>",
        "url": "<first-party apply URL>",
        "location": "<location from posting>",
        "summary": "<posting-derived summary>",
        "match_reason": "<resume overlap plus quoted JD evidence>",
        "source": "greenhouse",
        "apply_urls": ["<first-party apply URL>"],
        "posted_at": "<source-provided date>",
        "source_updated_at": "<source-provided last update>",
        "employment_type": "full_time",
        "fit_score": 0.42,
        "confidence": 1.0
      }
    ],
    "outreach": []
  },
  "outcomes": []
}
```

An empty `outreach` array means no current employee met the verification bar;
the UI explains how to continue the search manually.

## Layout

```
job_hunt_agent/        ADK agent, schemas, pipeline runner, tracing
  api.py               FastAPI surface (enqueue, status, cancel, outcomes)
  database.py          SQLAlchemy engine/session lifecycle and migration head
  hunt_repository.py   Encrypted owner-scoped Postgres hunt aggregate
  profile_repository.py Profile, resume-version, and career-target persistence
  evidence_repository.py Encrypted approval-gated achievement evidence
  saved_search_repository.py Saved criteria and timezone projections
  sqlalchemy_owner_workspace.py Transaction-owning profile/search API adapter
  job_queue.py         Generic Postgres jobs, leases, events, and heartbeats
  models/              Owner, profile, search, job, hunt, and outcome tables
  evals.py             LLM-as-judge draft scoring (V9)
  mcp_client.py        Phoenix past-draft retrieval (self-RAG)
  persistence.py       Explicit practical-mode-off SQLite compatibility path
  worker.py            Postgres practical worker + isolated legacy dispatcher
  tools/               search_jobs, find_referrals, draft_message (+ mocks)
frontend/              Next.js app (input → review → outcomes)
fixtures/              sample resume, criteria, seed corpus, judge references
scripts/               seed_phoenix.py, validate_judge.py, compare_rounds.py
demo/                  round comparison chart, demo script, Devpost writeup,
                       canonical_run.json (offline backup of one real run)
tests/                 pytest suite (offline; live tests skip without keys)
PLAN.md                Full plan and task history
```

## License

MIT — see [LICENSE](LICENSE).
