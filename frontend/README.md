# Job Hunt Signal — frontend

Next.js 16 (App Router) + Tailwind + TypeScript. Drives the FastAPI backend in [`job_hunt_agent/api.py`](../job_hunt_agent/api.py).

## Run locally

```bash
cd frontend
cp .env.example .env.local   # point NEXT_PUBLIC_API_BASE_URL at the backend
npm install
npm run dev
```

Backend (in another terminal, from the repo root):

```bash
$env:USE_MOCKS = "1"     # optional, skips SerpAPI/Gemini
.venv\Scripts\uvicorn job_hunt_agent.api:app --reload
```

Open <http://localhost:3000>.

## Pages

- `/` — resume + criteria input, "Run hunt" button.
- `/runs/[runId]` — review 3 roles x 3 referral drafts, edit + copy.
- `/runs/[runId]/outcomes` — log per-draft outcomes (replied / no_reply / introduced / rejected).

## Scripts

```bash
npm run dev     # local dev server
npm run build   # production build (Turbopack default in Next 16)
npm run lint    # ESLint (Next flat config)
```
