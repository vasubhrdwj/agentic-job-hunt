# Job Hunt Signal

An agent that runs a focused, evidence-based job hunt: find 3 matching roles, surface 3 plausible referral targets per role, and draft a concise outreach message for each. Every tool call is traced to Phoenix. No fabricated profile URLs, no LinkedIn-influencer copy.

> Submission draft for the Arize × Google hackathon (Gemini 3 + Agent Builder + Phoenix). Week 1 ships a deterministic pipeline + ADK agent; week 2 wires Phoenix MCP self-RAG, LLM-as-judge eval, and a frontend.

## Install

```bash
python -m venv .venv && .venv\Scripts\activate
pip install google-adk google-genai openinference-instrumentation-google-adk opentelemetry-sdk python-dotenv pydantic
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

Add `--trace` to emit OpenTelemetry spans to your Phoenix project, or `--use-mocks` for the no-network smoke loop.

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
  tools/               search_jobs, find_referrals, draft_message (+ mocks)
fixtures/              sample resume + JobCriteria fixtures
scripts/               preview_drafts.py (eyeball check for Gemini draft tool)
tests/                 pytest suite — 54 tests, all green on real-mode mocks
PLAN.md                Full 2-week plan
```

## License

MIT.
