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

- `/` — resume + criteria input, provider disclosure, "Run hunt" button.
- `/privacy` — resume, provider-retention, tracing, and deletion disclosure.
- `/runs/[runId]` — private review page, edit/copy drafts, delete run.
- `/runs/[runId]/outcomes` — log per-draft outcomes (replied / no_reply / introduced / rejected).

The backend returns a one-time run capability after submission. The frontend
keeps it in `sessionStorage` and sends it only in the `Authorization` header;
private run pages intentionally cannot be opened from another browser session.

## Scripts

```bash
npm run dev     # local dev server
npm run build   # production build (Turbopack default in Next 16)
npm run lint    # ESLint (Next flat config)
```
