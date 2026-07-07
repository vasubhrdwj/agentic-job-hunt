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

Add `--trace` to emit OpenTelemetry spans to your Phoenix project, or `--use-mocks` for the no-network smoke loop. The CLI also prints the generated `run_id` so you can correlate the output with a Phoenix trace or the persisted SQLite row.

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

## Run the API

FastAPI wraps `run_hunt` for the Next.js frontend.

```bash
uvicorn job_hunt_agent.api:app --reload
```

Endpoints:

```
POST   /api/hunt                  { resume_text, criteria, pack, provider_consent: true }
                                  → HuntResult + { status, access_token }
POST   /api/runs/{run_id}/outcomes  Authorization: Bearer <access_token>
GET    /api/runs/{run_id}           Authorization: Bearer <access_token>
DELETE /api/runs/{run_id}           Authorization: Bearer <access_token>
GET    /health                    → { ok: true }
```

The access token is returned once, stored by the frontend in browser
`sessionStorage`, and sent only in the `Authorization` header. It is never
placed in a run URL. A run opened in another browser session is intentionally
inaccessible.

Configure persistence and CORS via env:

- `JOB_HUNT_DB_PATH` — SQLite file for runs + outcomes (default `outcomes.db`).
- `JOB_HUNT_DATA_KEYS` — comma-separated `key-id:Fernet-key` values. The first
  key encrypts new resume-bearing requests; retain older keys during rotation.
- `ALLOWED_ORIGINS` — comma-separated CORS allowlist (default dev origins for localhost 3000/5173).
- `ENABLE_TRACING` — set to `1` so API-triggered hunts emit Phoenix spans.
- `ENABLE_TRACE_DRAFT_CONTENT` — keep `0` for user traffic. Prompts, model
  outputs, and draft text are private by default.
- `GEMINI_PAID_SERVICE_ACK` — production must set `1` manually after
  confirming the Google API key uses paid Gemini quota.
- `RETENTION_CLEANUP_INTERVAL_SECONDS` — API-triggered expired-data cleanup
  interval. Set `3600` in production so health checks enforce retention.

`criteria` accepts `employment_types` and `max_age_days`; `pack` defaults to
`backend_india`.

Resume-bearing request payloads are encrypted before SQLite writes and erased
when the synchronous hunt finishes. Results and outcomes expire after 30 days
via startup cleanup, hourly API-triggered cleanup, or
`python -m job_hunt_agent.cleanup`. Users can delete a run immediately from
the review page. See the frontend `/privacy` page for the
provider disclosure and retention limits.

Privacy mode intentionally prevents new user draft text from entering the
shared Phoenix corpus. Self-RAG continues to use the curated seed corpus; an
owner-scoped learning store is required before production can learn from
individual users' drafts safely.

## Deploy

A8 deploys the FastAPI backend to Render (free tier) and the Next.js frontend
to Vercel. Render's free tier has no persistent disk, so SQLite lives on the
ephemeral filesystem at `/tmp/outcomes.db`: outcomes persist across requests,
but a redeploy, restart, or idle spin-down wipes them. Two operational rules
follow from that:

> The included free-tier blueprint is for demos and controlled personal use,
> not a public production service. A production deployment still needs durable
> storage, authentication/rate limits, and a queued worker for long hunts.

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
   `JOB_HUNT_DATA_KEYS`, `GEMINI_PAID_SERVICE_ACK`, plus `ALLOWED_ORIGINS`. Generate
   `JOB_HUNT_DATA_KEYS` as described above. For `ALLOWED_ORIGINS` use the
   production Vercel URL once it exists (production startup rejects `*` and
   localhost — use a placeholder like `https://pending.invalid` until the
   Vercel URL is known, then update).
4. Deploys are automatic on every push to the connected branch.
5. Add a free UptimeRobot (or cron-job.org) monitor on
   `https://<service>.onrender.com/health` at a 5-minute interval.

The blueprint sets `ENVIRONMENT=production`, `ENABLE_TRACING=1`,
`ENABLE_TRACE_DRAFT_CONTENT=0`, `RETENTION_CLEANUP_INTERVAL_SECONDS=3600`,
`JOB_HUNT_DB_PATH=/tmp/outcomes.db`, `PHOENIX_QUERY_TRANSPORT=rest`, and a
720-hour Phoenix lookback for seeded demo traces. Startup fails loudly in
production if required secrets are missing, localhost CORS is configured,
mocks are enabled, draft content tracing is enabled, or the DB path is not
absolute.

One-time Vercel setup:

```bash
cd frontend
cp .env.example .env.local
vercel link
vercel env add NEXT_PUBLIC_API_BASE_URL production
vercel --prod
```

Set `NEXT_PUBLIC_API_BASE_URL` to `https://<service>.onrender.com` (shown at
the top of the Render service page). After Vercel prints the production URL,
update Render's `ALLOWED_ORIGINS` env var to that exact URL — Render restarts
the service automatically when env vars change.

Production smoke test:

```bash
API=https://<service>.onrender.com
curl $API/health
curl -X POST $API/api/hunt \
  -H "Content-Type: application/json" \
  -d @fixtures/sample_hunt_request.json

RUN_ID=<run_id from the hunt response>
ACCESS_TOKEN=<access_token from the hunt response>
DRAFT_ID=<draft_id from the hunt response>
curl -X POST $API/api/runs/$RUN_ID/outcomes \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"outcomes\":[{\"draft_id\":\"$DRAFT_ID\",\"outcome\":\"replied\",\"notes\":\"deploy smoke\"}]}"
curl -H "Authorization: Bearer $ACCESS_TOKEN" $API/api/runs/$RUN_ID
```

The `/api/hunt` call blocks for the full pipeline (~90s). This branch does not
yet include a durable background queue, so verify the deployed request path
before inviting users. A production version should enqueue the hunt and have
the frontend poll `GET /api/runs/{run_id}`. The final `curl` should include the
logged outcome. Also verify the same `run_id` is visible in Phoenix before
recording the demo. Do not expect outcomes to survive a redeploy — that is the
documented free-tier trade-off.

## Abridged response shape

```json
{
  "run_id": "<trace-correlated id>",
  "status": "succeeded",
  "access_token": "<returned once; send only as a bearer token>",
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
}
```

An empty `outreach` array means no current employee met the verification bar;
the UI explains how to continue the search manually.

## Layout

```
job_hunt_agent/        ADK agent, schemas, pipeline runner, tracing
  api.py               FastAPI surface (POST /api/hunt, outcomes, GET run)
  evals.py             LLM-as-judge draft scoring (V9)
  mcp_client.py        Phoenix past-draft retrieval (self-RAG)
  persistence.py       SQLite layer for runs + outcomes
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
