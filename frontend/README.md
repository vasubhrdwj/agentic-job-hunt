# Job Hunt Signal — frontend

Next.js 16 (App Router) + Tailwind + TypeScript. Drives the FastAPI backend in [`job_hunt_agent/api.py`](../job_hunt_agent/api.py).

## Run locally

```bash
# repo root: create the private credential and configure its printed hash
.venv/bin/python scripts/generate_owner_token.py

cd frontend
cp .env.example .env.local   # server-only API_BASE_URL points at FastAPI
npm ci
npm run dev
```

Backend and worker (from the repo root):

```bash
# once, then after every new migration
docker compose build migrate web worker
docker compose up -d postgres
docker compose run --rm migrate

# in both backend terminals, export the configured root .env first
set -a
source .env
set +a

# terminal 1
ENABLE_PRACTICAL_MODE=1 USE_MOCKS=1 .venv/bin/uvicorn job_hunt_agent.api:app --reload

# terminal 2, same DATABASE_URL
ENABLE_PRACTICAL_MODE=1 USE_MOCKS=1 .venv/bin/python -m job_hunt_agent.worker
```

On Windows PowerShell, use `$env:USE_MOCKS = "1"` before each command.

Open <http://localhost:3000>.

The root page redirects to owner login until the backend validates the opaque
HttpOnly session. Browser requests use the same-origin `/api/*` Route Handler.
It forwards a small header allowlist and only the `job_hunt_session` cookie to
the server-only `API_BASE_URL`; the FastAPI origin is not exposed in browser
JavaScript. The proxy caps request/response sizes and aborts slow upstreams.

## Pages

- `/login` — exchange the private owner token for an HttpOnly session.
- `/` — authenticated resume + criteria input and "Run hunt" button.
- `/privacy` — resume, provider-retention, tracing, and deletion disclosure.
- `/runs/[runId]` — private status/review page; polls queued/running runs, supports cancellation, and shows terminal failures.
- `/runs/[runId]/outcomes` — log per-draft outcomes after success (replied / no_reply / introduced / rejected).

Practical run pages use the owner session cookie, so a copied/bookmarked link
works in a new tab without a tab-local bearer token. The backend temporarily
retains a capability field only for legacy-mode response compatibility.
Submissions send an `Idempotency-Key` so duplicate clicks or lost HTTP responses
do not enqueue multiple paid-provider runs.

## Scripts

```bash
npm run dev        # local dev server
npm run build      # production build (Turbopack default in Next 16)
npm run lint       # ESLint (Next flat config)
npm run typecheck  # TypeScript without emitting files
npm run api:check  # FastAPI snapshot + generated TypeScript drift gate
```
