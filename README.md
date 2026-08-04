# Job Hunt Signal

Turn one résumé into a ranked, evidence-backed job pipeline and a five-person
outreach plan.

[Live app](https://agentic-job-hunt.vercel.app) ·
[OpenAI Build Week submission copy](demo/OPENAI_BUILD_WEEK.md) ·
[MIT License](LICENSE)

Job Hunt Signal is a multi-user job-search workspace for people who want better
roles without rebuilding the same spreadsheet, fit analysis, résumé edits, and
outreach plan for every application. Upload a PDF, DOCX, or TXT résumé once;
the app extracts the useful facts, searches curated first-party job boards,
ranks the complete result set by explainable fit, and prepares grounded next
steps for each role.

The product does the repetitive research and drafting. The job seeker remains
the person who applies and sends messages.

## What it does

- Parses an uploaded résumé into a reusable profile, skills, experience, and
  grounded achievement evidence. The original binary file is not retained.
- Scans first-party ATS and company career sources, normalizes postings,
  removes stale or ineligible roles, deduplicates them, and avoids repeatedly
  flooding the inbox with one company.
- Sorts **Recommended** roles by actionable state, eligibility, fit band, and
  confidence before pagination. Every assessment exposes supporting evidence,
  uncertainty, and missing inputs instead of a mysterious score.
- Creates an application dossier with a grounded why-fit story, requirement
  coverage, tailored résumé changes, application answers, and interview story
  starters. Unsupported claims stay blocked rather than being invented.
- Finds **up to five** appropriate referral leads from public source evidence,
  diversifies the bench, and prepares a separate grounded message for each.
  Honest shortfalls such as `3/5 source-backed` remain visible.
- Tracks manual sends, follow-ups, applications, interviews, corrections,
  outcomes, overdue actions, and weekly funnel learning.
- Gives every account a separate owner-scoped workspace, encrypted private
  fields, immutable history, data export, and permanent deletion controls.

It does **not** auto-submit employer forms, auto-send messages, scrape private
profiles, or fabricate a person, résumé claim, or job requirement.

## Five-minute judge walkthrough — no paid keys

The hosted app has open email/password signup. For a fully deterministic local
walkthrough, use the mock mode below; it exercises the real database, API,
worker, and UI without calling Google, SerpAPI, or Phoenix.

1. Start the stack with the Docker instructions below.
2. Seed a synthetic account and workspace:

   ```bash
   python3 scripts/seed_demo_workspace.py
   ```

3. Sign in at <http://localhost:3000> with the generated credentials.
4. Open **Profile** to inspect the parsed synthetic résumé, extracted skills,
   and ready-to-use evidence.
5. Open **Saved searches**, select the seeded backend search, and choose **Scan roles**.
6. Open **Today** to see fit-ranked roles; pursue one to create its dossier.
7. In **People**, start contact research. Mock mode produces a deterministic
   source-backed bench of up to five people and a distinct draft for each.
8. Continue through fit, materials, application, and follow-up checkpoints.

The seed utility is dependency-free and calls only public application APIs.
See [the demo seed notes](fixtures/DEMO_WORKSPACE.md). Résumé identities,
people, and outcome claims in the demo data are synthetic; mock results are not
live hiring leads.

Provider-free role scanning and fit assessment also work in the hosted app. A
fresh hosted account needs the optional SerpAPI integration for live public
profile discovery; use `USE_MOCKS=1` locally for the complete deterministic
five-person People workflow without a paid key.

## Clean-clone setup with Docker (recommended)

Supported: Docker Desktop/Compose on macOS, Linux, or Windows. The commands
below use a macOS/Linux shell. Prerequisites are Git, Docker, and optional
Python 3 for the seed command.

```bash
git clone https://github.com/vasubhrdwj/agentic-job-hunt.git
cd agentic-job-hunt
cp .env.example .env

POSTGRES_PASSWORD="$(openssl rand -hex 24)"
DATA_KEY="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')"
PRIVACY_SECRET="$(openssl rand -hex 32)"

sed -i.bak "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${POSTGRES_PASSWORD}/" .env
sed -i.bak "s|^JOB_HUNT_DATA_KEYS=.*|JOB_HUNT_DATA_KEYS=v1:${DATA_KEY}|" .env
sed -i.bak "s/^JOB_HUNT_PRIVACY_RECEIPT_SECRET=.*/JOB_HUNT_PRIVACY_RECEIPT_SECRET=${PRIVACY_SECRET}/" .env
sed -i.bak "s/^USE_MOCKS=.*/USE_MOCKS=1/" .env
rm -f .env.bak

docker compose up --build -d
docker compose ps
```

Open <http://localhost:3000>. Compose starts Postgres 16, applies every Alembic
migration, then starts FastAPI, the durable worker, and the Node 20/Next.js
frontend. New account passwords must contain at least 12 characters.

Stop the stack with `docker compose down`. Add `-v` only if you intentionally
want to delete the local database volume.

### Environment variables

For the local Docker demo, only a URL-safe `POSTGRES_PASSWORD` is strictly
required. The setup above also creates stable local encryption and privacy
secrets so restarts remain reproducible.

| Variable | When needed | Purpose |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | Local Docker, required | Password used by the Compose Postgres service. |
| `DATABASE_URL` | Host-run or hosted backend, required | SQLAlchemy Postgres URL shared by web and worker. |
| `JOB_HUNT_DATA_KEYS` | Hosted, required; local recommended | `key-id:Fernet-key` entries used to encrypt private fields. |
| `JOB_HUNT_PRIVACY_RECEIPT_SECRET` | Hosted, required; local recommended | Stable 32+ character secret for deletion receipts and auth throttling. |
| `ALLOWED_ORIGINS` | Required | Exact frontend origins allowed to make browser mutations. |
| `JOB_HUNT_SIGNUP_MODE` | Required for new users | Set to `open` for judge/local signup. |
| `USE_MOCKS` | Free deterministic demo | Set to `1`; no provider account or API key is needed. |
| `SERPAPI_API_KEY` | Optional | Enables live public-profile contact discovery. Role scanning does not need it. |
| `GOOGLE_API_KEY` | Optional legacy pipeline only | Enables the older Google ADK/Gemini hunt and evaluation path. |
| `PHOENIX_API_KEY`, `PHOENIX_COLLECTOR_ENDPOINT` | Optional | Enables legacy tracing and self-RAG experiments. |

The practical résumé parser, first-party role scanner, fit assessment,
application grounding, and default message preparation do not require a paid
model API. Never commit a populated `.env` file.

## Hosted deployment status

The production topology uses Vercel for the Next.js frontend, managed Postgres
for durable owner-scoped data, and one free Render web service for FastAPI. On
the free topology, `ENABLE_EMBEDDED_SCAN_WORKER=1` runs the durable role and
contact queue inside the web process while it is awake; interrupted leases are
recovered after a restart.

Render checks `<service>.onrender.com/web-ready`, which verifies database
reachability and the current migration revision before routing web traffic.
The authenticated `/api/health` endpoint separately reports fresh worker
heartbeats, supported job kinds, owner queue counts, and whether role or contact
work can currently be accepted. Production operators should check both before
and after a deployment because web readiness alone does not prove that the
embedded worker is alive.

## Manual development setup

Prerequisites: Python 3.13, Node.js 20, npm, and Postgres 16. The shortest
manual path still uses the Compose database:

```bash
git clone https://github.com/vasubhrdwj/agentic-job-hunt.git
cd agentic-job-hunt
cp .env.example .env
# Set POSTGRES_PASSWORD, JOB_HUNT_DATA_KEYS, JOB_HUNT_PRIVACY_RECEIPT_SECRET,
# and USE_MOCKS=1 as shown in the Docker setup above.

docker compose up -d postgres

python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

cd frontend
npm ci
cp .env.example .env.local
cd ..

set -a
source .env
set +a
python -m alembic upgrade head
```

Then run these in three terminals from the repository root, with the virtual
environment and `.env` loaded in the two backend terminals:

```bash
uvicorn job_hunt_agent.api:app --reload --port 8000
```

```bash
python -m job_hunt_agent.worker
```

```bash
cd frontend
npm run dev
```

## Sample data

- [`fixtures/sample_resume.txt`](fixtures/sample_resume.txt) — synthetic
  backend/identity engineer résumé used by the workspace seeder.
- [`fixtures/sample_criteria_backend.json`](fixtures/sample_criteria_backend.json)
  — backend search criteria.
- [`scripts/seed_demo_workspace.py`](scripts/seed_demo_workspace.py) — creates
  a unique account, imports the résumé, and creates a career track and saved
  search through the same APIs as the UI.
- [`fixtures/seed_outreach.jsonl`](fixtures/seed_outreach.jsonl) — explicitly
  synthetic legacy outreach/evaluation corpus with its rubric documented in
  [`fixtures/SEED_NOTES.md`](fixtures/SEED_NOTES.md).

## How Codex and GPT-5.6 contributed

Codex was our development collaborator, not a hidden production dependency.
Vasu and Arpita used it as a tight product/engineering loop: describe a real job-search
friction, inspect the running product, turn the feedback into a small change,
review the diff, and commit it independently. This made it practical to evolve
one workflow across database migrations, repositories, API contracts, UI, and
regression coverage without losing the user problem between layers.

Concrete examples:

- Codex scaffolded and evolved the durable owner-scoped workflow across
  `job_hunt_agent/models/`, Alembic migrations, repositories, routers, and the
  generated OpenAPI/TypeScript contract.
- It accelerated the explainable ranking path in
  `job_hunt_agent/opportunity_assessment.py` and the persisted ordering in
  `job_hunt_agent/opportunity_repository.py`, then connected it to Today.
- It paired on the five-person referral workflow in
  `job_hunt_agent/contact_search_worker.py`,
  `job_hunt_agent/contact_search_repository.py`, and
  `frontend/lib/grounded-outreach-drafts.ts`, including explicit evidence
  shortfalls rather than invented contacts.
- It helped build grounded application support in
  `job_hunt_agent/application_pack_repository.py`,
  `job_hunt_agent/application_artifact_repository.py`,
  `frontend/lib/grounded-fit-story.ts`, and
  `frontend/lib/application-materials-auto-generation.ts`.
- It turned deployment failures across Vercel, Render, Neon Postgres,
  migrations, CORS, and session boundaries into narrow fixes and regression
  cases rather than one-off production workarounds.
- It removed onboarding friction with normal email/password accounts and the
  bounded PDF/DOCX/TXT pipeline in `job_hunt_agent/resume_ingestion.py`, atomic
  encrypted imports, and `frontend/components/profile-workspace.tsx`.
- During the GPT-5.6 submission pass, a GPT-5.6 Codex agent inspected the
  public API contracts and implemented the dependency-free judge seeder in
  `scripts/seed_demo_workspace.py` plus its sample-data guide.

Vasu and Arpita made the consequential product calls: focus on early-career backend and
software-engineering searches; rank the whole inbox by fit; look for five
appropriate people instead of one; replace a shared passkey with normal
multi-user signup; make résumé upload the default onboarding path; minimize
approval work; prefer free first-party sources; never invent claims or people;
and keep the user as the final applicant and sender. Codex proposed and
implemented alternatives under those constraints; it did not choose the
product direction autonomously.

GPT-5.6 was used through Codex for development and submission tooling. It is
**not** the inference model serving the deployed application and does not
process a judge's résumé. The optional legacy agent/drafting path uses Google
ADK with Gemini 3.5 Flash and a fixed Gemini 2.5 Flash judge. The practical
product's matching, grounding, and default drafting paths are intentionally
deterministic and provider-free. This boundary is explicit so reviewers can
distinguish AI-assisted engineering from runtime model usage.

## What changed during OpenAI Build Week

This repository existed before the event as a narrower agent demo. The last
pre-submission-period baseline is commit
[`31043a9`](https://github.com/vasubhrdwj/agentic-job-hunt/commit/31043a9).
The Build Week extension starts at
[`d1b64b1`](https://github.com/vasubhrdwj/agentic-job-hunt/commit/d1b64b1);
the dated history and full diff are visible in the
[Build Week comparison](https://github.com/vasubhrdwj/agentic-job-hunt/compare/31043a9...main).

During that window, the project gained the durable opportunity radar,
fit-ranked Today inbox, application dossiers, five-contact bench and outreach
waves, application materials, interview preparation, outcome learning,
multi-user accounts, privacy controls, free-tier scan worker, and secure
upload-first résumé onboarding. The commit history deliberately keeps these as
small, reviewable product slices.

## Architecture

```text
Next.js 16 / React 19
        │ same-origin bounded proxy + HttpOnly session
        ▼
FastAPI ─── PostgreSQL 16 (owner-scoped, encrypted private fields)
        │
        ├── durable scan/contact job queue ── worker
        ├── first-party ATS/company adapters
        ├── deterministic fit + evidence grounding
        └── optional SerpAPI / legacy Gemini + Phoenix integrations
```

Important implementation areas:

```text
job_hunt_agent/        FastAPI, models, repositories, workers, source adapters
frontend/              Next.js workspace and generated API client contract
migrations/            Alembic history for the multi-user Postgres product
fixtures/              synthetic résumé, criteria, and evaluation data
scripts/               migrations, operations, OpenAPI, and demo seeding
docs/                  delivered phase contracts and production runbooks
tests/                  hermetic backend and frontend regression coverage
```

## Verification commands

These are provided for judges and contributors; the hosted demo is available
without rebuilding.

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q

cd frontend
npm ci
npm test
npm run typecheck
npm run lint -- --max-warnings=0
npm run api:check
npm run build -- --webpack
```

Before activating or changing a production company source, run the strict live
registry gate from the repository root:

```bash
.venv/bin/python scripts/verify_registry.py --pack backend_india --live --strict-live
```

Production and recovery procedures are in
[`docs/runbooks/README.md`](docs/runbooks/README.md). The privacy contract is
documented in
[`docs/PHASE_6_PRIVACY_CONTROLS.md`](docs/PHASE_6_PRIVACY_CONTROLS.md).

## License

MIT — see [LICENSE](LICENSE). Copyright 2026 Vasu Bhardwaj and Arpita Gupta.
