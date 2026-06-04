# Job Hunt Signal

An agent that runs a focused, evidence-based job hunt: find 3 matching roles, surface 3 plausible referral targets per role, and draft a concise outreach message for each. Every tool call is traced to Phoenix. No fabricated profile URLs, no LinkedIn-influencer copy.

> Submission draft for the Arize × Google hackathon (Gemini 3 + Agent Builder + Phoenix). Week 1 ships a deterministic pipeline + ADK agent; week 2 wires Phoenix MCP self-RAG, LLM-as-judge eval, and a frontend.

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
  --keywords SCIM,identity,IAM,OIDC \
  --location Remote-India,Bengaluru,Hyderabad \
  --seniority senior
```

Output is structured JSON: `{ roles: [...], outreach: [{ role, person, message }, ...] }`.

Add `--trace` to emit OpenTelemetry spans to your Phoenix project, or `--use-mocks` for the no-network smoke loop. The CLI also prints the generated `run_id` so you can correlate the output with a Phoenix trace or the persisted SQLite row.

## Run the API

A6 wraps `run_hunt` in a small FastAPI service so the future frontend can drive it over HTTP.

```bash
uvicorn job_hunt_agent.api:app --reload
```

Endpoints:

```
POST /api/hunt                       { resume_text, criteria }       → HuntResult (with run_id)
POST /api/runs/{run_id}/outcomes      { outcomes: [OutcomeLog] }      → { ok, inserted, outcomes }
GET  /api/runs/{run_id}                                                → { hunt_result, outcomes }
GET  /health                                                           → { ok: true }
```

Configure persistence and CORS via env:

- `JOB_HUNT_DB_PATH` — SQLite file for runs + outcomes (default `outcomes.db`).
- `ALLOWED_ORIGINS` — comma-separated CORS allowlist (default dev origins for localhost 3000/5173).
- `ENABLE_TRACING` — set to `1` so API-triggered hunts emit Phoenix spans.

## Deploy

A8 deploys the FastAPI backend to Fly.io and the Next.js frontend to Vercel.
The backend stores SQLite state on a Fly volume mounted at `/data`.

One-time Fly setup:

```bash
fly launch --no-deploy --config fly.toml
fly volumes create data --size 1 --region bom --config fly.toml
fly secrets set \
  GOOGLE_API_KEY=... \
  SERPAPI_API_KEY=... \
  PHOENIX_API_KEY=... \
  PHOENIX_COLLECTOR_ENDPOINT=https://app.phoenix.arize.com/s/... \
  ALLOWED_ORIGINS=https://<your-vercel-app>.vercel.app \
  --config fly.toml
./scripts/deploy.sh
```

The Fly config sets `ENVIRONMENT=production`, `ENABLE_TRACING=1`,
`JOB_HUNT_DB_PATH=/data/outcomes.db`, `PHOENIX_QUERY_TRANSPORT=rest`, and a
720-hour Phoenix lookback for seeded demo traces. Startup fails loudly in
production if required secrets are missing, localhost CORS is configured, mocks
are enabled, or the DB path is not absolute.

One-time Vercel setup:

```bash
cd frontend
cp .env.example .env.local
vercel link
vercel env add NEXT_PUBLIC_API_BASE_URL production
vercel --prod
```

Set `NEXT_PUBLIC_API_BASE_URL` to `https://job-hunt-agent.fly.dev` (or the Fly
URL printed by `fly status`). After Vercel prints the production URL, update
Fly's `ALLOWED_ORIGINS` secret to that exact URL and redeploy.

Production smoke test:

```bash
curl https://job-hunt-agent.fly.dev/health
curl -X POST https://job-hunt-agent.fly.dev/api/hunt \
  -H "Content-Type: application/json" \
  -d @fixtures/sample_hunt_request.json

RUN_ID=<run_id from the hunt response>
DRAFT_ID=<draft_id from the hunt response>
curl -X POST https://job-hunt-agent.fly.dev/api/runs/$RUN_ID/outcomes \
  -H "Content-Type: application/json" \
  -d "{\"outcomes\":[{\"draft_id\":\"$DRAFT_ID\",\"outcome\":\"replied\",\"notes\":\"deploy smoke\"}]}"
fly deploy --config fly.toml
curl https://job-hunt-agent.fly.dev/api/runs/$RUN_ID
```

The final response should still include the logged outcome after redeploy; that
proves the Fly volume is mounted correctly. Also verify the same `run_id` is
visible in Phoenix before recording the demo.

## Example output

```json
{
  "roles": [
    {
      "company": "Okta",
      "title": "Senior Software Engineer, Lifecycle Management",
      "url": "https://www.linkedin.com/jobs/view/...",
      "location": "Remote-India",
      "summary": "Build provisioning and lifecycle workflows for enterprise identity customers.",
      "match_reason": "Snippet matches SCIM, identity in context: ..."
    }
  ],
  "outreach": [
    {
      "role": { "...": "..." },
      "person": {
        "name": "Anika Rao",
        "title": "Staff Engineer, Lifecycle Management",
        "company": "Okta",
        "profile_url": "https://www.linkedin.com/in/...",
        "source": "linkedin",
        "why_relevant": "Owns the lifecycle management team that ships SCIM provisioning features."
      },
      "message": "Hi Anika — I noticed Okta is hiring on the Lifecycle Management team..."
    }
  ]
}
```

## Layout

```
job_hunt_agent/        ADK agent, schemas, pipeline runner, tracing
  api.py               FastAPI surface (POST /api/hunt, outcomes, GET run)
  persistence.py       SQLite layer for runs + outcomes
  tools/               search_jobs, find_referrals, draft_message (+ mocks)
fixtures/              sample resume, JobCriteria fixtures, seed outreach corpus
scripts/               preview_drafts.py, seed_phoenix.py
tests/                 pytest suite — 96 passed, 3 live tests skipped
PLAN.md                Full 2-week plan
```

## License

MIT.
