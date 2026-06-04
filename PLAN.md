# Job-Hunt Agent — First Submission Draft Plan

**Hackathon:** Arize × Google (Gemini 3 + Agent Builder + Phoenix)
**Deadline:** 2026-06-11
**Today:** 2026-05-24
**Team:** Vasu (agent + backend) · Arpita (tools + data + frontend)
**Repo layout:** `job_hunt_agent/` (Python ADK app), frontend TBD week 2

---

## What we are submitting (loose end-goal for v1 / first submission draft)

An agent that **learns to write better job-hunt outreach over time** by reading its own Phoenix traces.

### The user-visible loop

1. User pastes a resume + target criteria (role, focus area, comp band, location)
2. Agent finds **3 matching open roles**
3. For each role, agent finds **3 plausible referral targets** (engineers / hiring managers at that company)
4. Agent drafts **a personalized outreach message** per target, with visible reasoning ("why this person, why this role-fit")
5. User reviews → edits → exports (no real send in v1 — too risky)
6. User logs outcome later (replied / didn't / introduced)
7. **The Arize moment:** next run, the agent calls Phoenix MCP to pull its highest-scoring past messages for similar role types and uses them as few-shot examples. Eval scores climb across rounds. We graph it. That's the demo.

### What is in scope for the first submission draft

- ✅ Single-user web flow (no auth, no multi-tenancy)
- ✅ Resume parsed from plain text (no PDF magic yet — paste-only is fine)
- ✅ Real Gemini-powered drafting with personalization
- ✅ Phoenix traces visible for every run, every tool call
- ✅ Phoenix MCP wired into the agent for self-retrieval
- ✅ LLM-as-judge eval running per message
- ✅ Seeded "past outreach" dataset (15–20 examples) so the self-improvement loop has a corpus on day one
- ✅ Demo video (≤3 min) + Devpost write-up + public GitHub repo (MIT)

### What is explicitly out of scope for v1 (polish session)

- ❌ Actually sending emails / DMs
- ❌ LinkedIn scraping at scale (use public Google `site:linkedin.com` results)
- ❌ Multi-user auth / accounts
- ❌ Outcome tracking from real replies (we log manually for v1)
- ❌ Mobile-responsive polish
- ❌ Anything stretch-goal in the original plan

### Definition of "submission-draft done"

A judge can:
1. Open the hosted URL
2. Paste a resume + criteria
3. See 3 roles, 9 targets, 9 drafted messages within ~90 seconds
4. Click through to Phoenix and see the full trace tree for that run
5. Watch a recorded demo segment showing round-1 vs round-5 eval scores

---

## Architecture (locked — do not redebate)

```
┌─────────────┐
│  Frontend   │  (week 2 — Arpita)
│  React/Next │  resume input → review → export → log outcome
└──────┬──────┘
       │ HTTP
┌──────▼──────────────────────────────────────────┐
│  Python ADK Agent (job_hunt_agent/)             │
│                                                  │
│  Agent (Gemini)                                  │
│    ├─ Tool: search_jobs(criteria) → [Role]      │
│    ├─ Tool: find_referrals(role) → [Person]     │
│    ├─ Tool: draft_message(role, person, resume) │
│    └─ Tool: phoenix_mcp.query_traces (self-RAG) │
└──────┬──────────────────────────────────────────┘
       │ OTLP
┌──────▼──────────┐    ┌──────────────────┐
│  Phoenix Cloud  │    │  Firestore       │
│  traces + evals │    │  resumes,        │
│  + datasets     │    │  outcome logs    │
└─────────────────┘    └──────────────────┘
```

**Tech locked:**
- Google ADK (already working — `job_hunt_agent/hello_adk.py`)
- Gemini 2.5 Flash for tools, Gemini 3 Pro for final drafting (swap at end of week 2)
- Phoenix Cloud (free tier) for tracing, datasets, evals
- `openinference-instrumentation-google-adk` (already wired)
- `@arizeai/phoenix-mcp` (added week 2)
- Firestore for outcome logs (added week 2)

---

# Days 3–7 — the work this session is splitting

Two tracks. Both start now. They meet at the **tool contract** below, agreed on day 3.

## The tool contract (lock this first — both tracks depend on it)

Both tracks code against these signatures. Do not change without a 5-min sync.

```python
# job_hunt_agent/schemas.py  (Arpita writes this file on day 3 AM, both import from it)

from pydantic import BaseModel
from typing import Literal

class JobCriteria(BaseModel):
    role_keywords: list[str]      # ["SCIM", "identity", "IAM"]
    seniority: Literal["junior", "mid", "senior", "staff"]
    location: list[str]            # ["Hyderabad", "Remote-India"]
    comp_min_lpa: int | None = None
    comp_max_lpa: int | None = None

class Role(BaseModel):
    company: str
    title: str
    url: str                       # job posting URL
    location: str
    summary: str                   # 2-3 sentences, extracted
    match_reason: str              # why this matches the criteria

class Person(BaseModel):
    name: str
    title: str
    company: str
    profile_url: str               # LinkedIn / GitHub / company page
    source: Literal["linkedin", "github", "company_page", "other"]
    why_relevant: str              # one-liner: why this person for this role
```

**Decision point for day 3 AM (15-min sync):** Vasu and Arpita confirm these schemas. Once locked, work in parallel.

---

## Track V — Vasu: agent spine + persona + integration

### V1. Agent persona & system prompt (Day 3)

Define the agent's identity, tone, and operating rules. This is the "constitution" — every tool call inherits from it.

**Deliverable:** `job_hunt_agent/agent.py` with a refined Agent definition replacing the hello-world one.

**Success checklist:**
- [ ] System prompt explicitly tells the agent: "you find roles, find people, draft outreach — you never invent profile URLs"
- [ ] Persona is opinionated: "concise, specific, no LinkedIn-influencer tone"
- [ ] Includes the 3-step plan the agent should follow on every run (search jobs → find people → draft)
- [ ] Includes guardrails: "if you cannot find a real profile URL via tool, say so — do not fabricate"
- [ ] `python agent.py "find me jobs in SCIM"` returns a coherent plan-of-action response (even before tools exist)
- [ ] Run appears in Phoenix with system prompt visible in the span attributes

### V2. Mock-tool integration harness (Day 3 PM)

Stub the three tools so the agent loop runs end-to-end with fake data. This unblocks **everything** — Vasu can iterate on agent behavior without waiting for real tools.

**Deliverable:** `job_hunt_agent/tools/mocks.py` returning canned `Role` / `Person` objects.

**Success checklist:**
- [ ] `search_jobs_mock` returns 3 hardcoded `Role` objects
- [ ] `find_referrals_mock` returns 3 hardcoded `Person` objects per role
- [ ] `draft_message_mock` returns a templated string
- [ ] Agent registered with all three mock tools, runs a full loop on a sample prompt, prints 3 roles × 3 people × 3 messages
- [ ] Phoenix shows a span tree: agent → search_jobs → find_referrals (×3) → draft_message (×9)

### V3. Swap mocks for real tools as they land (Day 5–6)

Once Arpita ships real implementations (same signatures), Vasu swaps imports. No agent code changes.

**Success checklist:**
- [ ] Each real tool drop-in replaces its mock with zero edits to `agent.py`
- [ ] End-to-end run on real data produces 3 roles × 3 people, all with non-empty URLs
- [ ] Phoenix trace shows real tool latencies (will be slower than mocks — expected)

### V4. Resume input + criteria parsing (Day 6)

Add the entry-point function the future frontend will call.

**Deliverable:** `job_hunt_agent/run.py` — `def run_hunt(resume_text: str, criteria: JobCriteria) -> HuntResult`

**Success checklist:**
- [ ] Takes plain-text resume + structured `JobCriteria`
- [ ] Returns a `HuntResult` (Pydantic) with `roles: list[Role]`, `outreach: list[OutreachDraft]`
- [ ] CLI wrapper: `python -m job_hunt_agent.run --resume sample.txt --keywords SCIM,IAM` prints structured JSON
- [ ] Full run takes < 90s on real tools

### V5. End-to-end smoke test by Sunday night (Day 7)

**Success checklist:**
- [ ] Vasu's sample resume → 3 SCIM/identity roles, 9 real referral candidates, 9 drafted messages
- [ ] At least 7/9 profile URLs resolve to a live page (open in browser, check)
- [ ] Phoenix dashboard shows complete trace tree for the run
- [ ] Commit hash tagged `v0.1-eod-day7` so we have a known-good baseline

---

## Track A — Arpita: schemas + tools + seed data

### A1. Define schemas (Day 3 AM, ~1 hour)

**Deliverable:** `job_hunt_agent/schemas.py` (the contract above).

**Success checklist:**
- [ ] All three models (`JobCriteria`, `Role`, `Person`) defined as Pydantic v2
- [ ] Each field has a docstring or `Field(description=...)`
- [ ] `pytest tests/test_schemas.py` passes round-trip serialization
- [ ] Pushed to repo before noon day 3 so Vasu can import

### A2. `search_jobs` tool (Days 3 PM – 5)

Web-search-backed job finder. Start with a single source (LinkedIn public job listings via Google site search) — broaden later.

**Deliverable:** `job_hunt_agent/tools/job_search.py` — `def search_jobs(criteria: JobCriteria) -> list[Role]`

**Implementation hint:**
- Use SerpAPI (free tier) with Google Search `site:linkedin.com/jobs` queries — do not try to scrape LinkedIn directly
- Query template: `site:linkedin.com/jobs "{keyword}" "{location}"`
- Gemini-extract structured `Role` from each result snippet + URL
- Cap at top 5 results, dedupe by company+title

**Success checklist:**
- [ ] Returns ≥3 valid `Role` objects for the criteria `{keywords: ["SCIM"], location: ["Remote-India"]}`
- [ ] Every returned `url` resolves to a real job posting (manual check on 5 sample runs)
- [ ] `match_reason` is specific, not generic ("matches because the listing mentions SCIM 2.0 + Okta integration" not "matches keywords")
- [ ] Function is fully type-hinted and importable
- [ ] Has a `tools/test_job_search.py` that runs against a real query and asserts shape (skip on CI if no API key)
- [ ] Failure modes handled: empty results, rate-limit, malformed response — returns `[]` not crash

### A3. `find_referrals` tool (Days 5–6)

Given a `Role`, return 3 plausible referral candidates at that company.

**Deliverable:** `job_hunt_agent/tools/referrals.py` — `def find_referrals(role: Role) -> list[Person]`

**Implementation hint:**
- Query template: `site:linkedin.com/in "{role.company}" "{keyword_from_role}"`
- Also try `site:github.com "{role.company}"` for engineering roles
- For each top result, Gemini-extracts `name`, `title`, populates `why_relevant`
- Accept that ~30% of links will be dead — that's fine for v1, log it

**Success checklist:**
- [ ] Returns exactly 3 `Person` objects per role (pads with `source="other"` if needed)
- [ ] At least 2 of 3 profile URLs open to a real profile page (manual spot-check)
- [ ] `why_relevant` references something concrete from the role or the person's title
- [ ] No fabricated profiles — if search returns nothing, returns `[]` and logs warning
- [ ] Tested standalone against 3 different real roles before integration

### A4. Sample resume + test fixtures (Day 4, parallel)

So Vasu has something to test against immediately.

**Deliverable:** `fixtures/sample_resume.txt` + `fixtures/sample_criteria.json`

**Success checklist:**
- [ ] One realistic resume (Vasu's actual one is fine, redacted if needed)
- [ ] Two `JobCriteria` JSON files — one SCIM/identity, one different domain (so we can sanity-check the agent isn't hardcoded to SCIM)
- [ ] Committed by EOD day 4

### A5. `draft_message` tool (Day 7 — light pass, deepens week 2)

Quick first version. Real personalization work happens in week 2.

**Deliverable:** `job_hunt_agent/tools/draft.py` — `def draft_message(role: Role, person: Person, resume_text: str) -> str`

**Success checklist for v1:**
- [ ] Gemini call with a clear system prompt: "write a concise referral request, 4 sentences max, reference one specific thing about the company and one about the person"
- [ ] Output is a single string, no markdown, no salutation placeholder garbage (`[Your name]`)
- [ ] Three sample outputs reviewed by Vasu — do they sound human?
- [ ] Returns deterministic structure (greeting, hook, ask, sign-off)

---

## Non-conflict guarantees

These are the rules that keep both tracks from stomping on each other:

| Concern | Rule |
|---|---|
| File ownership | Vasu owns `agent.py`, `run.py`, `tools/mocks.py`. Arpita owns `schemas.py`, `tools/job_search.py`, `tools/referrals.py`, `tools/draft.py`, `fixtures/`. |
| Schema changes | Edits to `schemas.py` after day 3 noon require a 5-min sync. No silent edits. |
| Phoenix project | Both use `project_name="job-hunt-agent"` (already set). Don't fork project names. |
| Secrets | Both work from the same `.env` (already exists). Add new keys to `.env.example` in the same commit. |
| Branches | Each task on its own branch named `track-v/<task>` or `track-a/<task>`. Merge to `main` via PR (even solo PRs — judges read commit history). |
| Daily sync | 15 min at a fixed time (suggest 9pm IST). Topics: blockers, schema deltas, anything that affects the other track. |

---

## Daily checkpoint — what "done for the day" looks like

### Day 3 (Mon May 25)
- [ ] V1 done (persona + system prompt, hello-prompt run looks coherent)
- [ ] A1 done (schemas merged, both import from it)
- [ ] V2 started (mock tools stubbed)
- [ ] A2 started (job search prototype querying real results)

### Day 4 (Tue May 26)
- [ ] V2 done (agent runs full loop on mocks, Phoenix trace visible)
- [ ] A2 in progress, A4 done (fixtures committed)

### Day 5 (Wed May 27)
- [ ] A2 done (real `search_jobs` returns valid roles)
- [ ] V3 partially done (job search swapped in, referrals still mocked)
- [ ] A3 started

### Day 6 (Thu May 28)
- [ ] A3 done (real `find_referrals`)
- [ ] V3 done (all real tools wired)
- [ ] V4 done (CLI entry-point)

### Day 7 (Fri May 29) — Sunday-night equivalent
- [ ] A5 done (basic draft_message)
- [ ] V5 done (end-to-end smoke test passes against Vasu's resume)
- [ ] Tag commit `v0.1-eod-day7`
- [ ] Both: paste the day-7 Phoenix trace screenshot into the team channel

---

## Risks for this slice (Days 3–7)

| Risk | Mitigation |
|---|---|
| Schemas drift mid-week and one track breaks the other | Lock at day 3 noon. Any change after = 5-min sync. |
| `search_jobs` returns junk (LinkedIn snippet quality is mixed) | Have a manual override file `fixtures/curated_roles.json` Arpita can hand-fill if the tool quality is too low by day 5 EOD. Demo still works. |
| Real tool latency makes runs too slow to iterate on | Keep mocks importable behind a `USE_MOCKS=1` env flag. Default to real tools but instant-switch. |
| Phoenix Cloud rate limits during heavy iteration | Use a separate Phoenix project (`job-hunt-agent-dev`) for noisy local runs; reserve the main project for demo-quality traces. |
| One of us gets sick / blocked | Both tracks are designed so the other can ship a partial submission alone using mocks. Mocks are the insurance policy — keep them working. |

---

## Reminders for the next session

When we pick this up after day 7:
- Week 2 work starts: Phoenix MCP integration, LLM-as-judge eval, seed dataset
- Frontend kickoff (Arpita pivots from tools to React app)
- Decide whether to swap drafting model to Gemini 3 Pro (cost vs. quality)

---

# Days 8–14 — Week 2: the Arize loop + the frontend

**Where we are starting from:** `v0.1-eod-week1` — `run_hunt()` produces real roles + real referrals + Gemini-personalized drafts. 53 tests passing. Live `python -m job_hunt_agent.run` end-to-end works.

**Where we need to be by EOD Day 14:** a hosted URL where pasting a resume + criteria runs the full pipeline, every run is traced in Phoenix, and the *self-improvement loop is demonstrable* — drafts measurably get better when the agent retrieves its own past traces. That's the demo's wow moment; everything in week 2 serves it.

## The week-2 contract (lock this first — both tracks depend on it)

Same pattern as week 1: agree on the data shape, then work in parallel.

```python
# job_hunt_agent/schemas.py  (Arpita adds, both import from it)

class OutcomeLog(BaseModel):
    """User-logged result for one drafted message."""
    run_id: str           # ties back to a Phoenix trace
    role_company: str
    role_title: str
    person_name: str
    message: str          # the actual draft that was sent
    outcome: Literal["replied", "no_reply", "introduced", "rejected", "pending"]
    notes: str | None = None
    logged_at: datetime
```

Plus an HTTP contract for the frontend ↔ backend boundary:

```
POST /api/hunt           { resume_text, criteria } -> HuntResult
POST /api/outcomes        { run_id, outcomes: [OutcomeLog] } -> { ok: true }
GET  /api/runs/:run_id    -> { hunt_result, outcomes }
```

**Decision point Day 8 AM (15-min sync):** confirm `OutcomeLog` schema + the 3 HTTP endpoints above. Once locked, work parallel.

---

## Track V — Vasu: Phoenix MCP + self-RAG + eval (the Arize loop)

### V6. Phoenix MCP server wiring (Day 8–9)

Connect the agent to Phoenix Cloud via `@arizeai/phoenix-mcp` so it can query its own traces at runtime.

**Deliverable:** `job_hunt_agent/mcp_client.py` — thin async wrapper exposing `query_past_drafts(role_keywords: list[str], top_k: int = 3) -> list[PastDraft]`.

**Implementation hint:**
- Use the official MCP client SDK; configure with `PHOENIX_API_KEY` + `PHOENIX_COLLECTOR_ENDPOINT` from `.env`
- Don't expose raw MCP tool calls to the agent yet — wrap in our domain-specific function so we control the prompt surface
- Fallback: if MCP fails, query Phoenix REST API directly with the same shape (`/v1/spans` filter). Keep the wrapper interface stable.

**Success checklist:**
- [ ] `mcp_client.query_past_drafts(["SCIM"])` returns a list of past drafts with their eval scores attached
- [ ] Works against the live Phoenix project (`job-hunt-agent`)
- [ ] Gracefully returns `[]` on auth failure / no traces / MCP server down
- [ ] Has a unit test that mocks the MCP transport and asserts the parsing logic
- [ ] Latency < 1.5s on typical query (cap with timeout)

### V7. Seeded "past outreach" dataset (Day 9–10)

Without a seeded corpus, the self-improvement loop has nothing to retrieve from on day one. Generate 15–20 fake-but-realistic past runs with outcomes already attached.

**Deliverable:** `fixtures/seed_outreach.jsonl` + `scripts/seed_phoenix.py` that uploads them as Phoenix traces with eval scores.

**Design rule for the seed data (this matters):** the "good" examples must share a *visibly distinguishing pattern* the agent can pick up on as few-shot examples. Examples that work:
- Good drafts always cite one specific protocol detail from the resume ("SCIM 2.0 RFC 7643")
- Good drafts ask for a *specific* next step ("15 min next week?"), bad ones are open-ended ("would love to chat sometime")
- Good drafts reference the recipient's specific team (Lifecycle, IGA), bad ones generic ("your team")

The agent learning to *imitate* the good pattern in round 5 is the demo's payoff. If the pattern isn't there, the demo flops.

**Success checklist:**
- [ ] 15–20 entries committed in `fixtures/seed_outreach.jsonl`
- [ ] Each entry has: role, person, message, eval_score (1–5), outcome (replied/no_reply/etc.)
- [ ] Distribution: ~6 high-score (4–5), ~6 mid-score (3), ~6 low-score (1–2) — not all good
- [ ] A clear pattern distinguishes high from low (documented in `fixtures/SEED_NOTES.md`)
- [ ] `python scripts/seed_phoenix.py` uploads them all without error; visible in Phoenix UI
- [ ] Manual review with Arpita — does a human reader agree with the score ranking?

### V8. Self-RAG retrieval in `draft_message` (Day 10–11)

The actual self-improvement loop: before drafting, pull top-3 highest-scoring past drafts for similar role types and use as few-shot examples.

**Deliverable:** updated `tools/draft.py` that calls `mcp_client.query_past_drafts()` and threads them into the Gemini prompt as exemplars.

**Implementation hint:**
- Add a `use_self_rag: bool = True` flag to `draft_message` so it can be turned off for round-1 baseline runs (this *is* the A/B comparison for the demo)
- In the prompt, label exemplars clearly: "Here are 3 past messages that got replies. Match their style but personalize to this specific person and role."
- Cap the exemplar prompt size — don't blow the context window
- Trace which past drafts were pulled (Phoenix span attribute) so the demo can show the retrieval

**Success checklist:**
- [ ] `draft_message(..., use_self_rag=True)` calls MCP, retrieves examples, includes them in the prompt
- [ ] `draft_message(..., use_self_rag=False)` skips retrieval entirely (baseline mode)
- [ ] Phoenix trace shows: `draft_message` span has child span `query_past_drafts` with retrieved-IDs in attributes
- [ ] Side-by-side on the same (role, person, resume) tuple: round-1 (no RAG) vs round-5 (with RAG) drafts read *visibly* different
- [ ] No regression: drafts without RAG still pass the existing tests

### V9. LLM-as-judge eval (Day 11–12)

Per the PLAN: score each drafted message 1–5 on "would respond" based on personalization, specificity, ask-clarity, tone.

**Deliverable:** `job_hunt_agent/evals.py` — `def score_draft(role, person, message) -> EvalResult` using Phoenix's eval framework or a direct Gemini judge call.

**Implementation hint:**
- Build the judge prompt carefully — test it against handwritten obviously-good and obviously-bad messages first. If the judge can't tell them apart, the demo's eval-score-climbing graph is meaningless.
- Score breakdown: 4 sub-scores (personalization, specificity, ask, tone), each 1–5; composite is the average
- Wire it into `run_hunt()` so every draft gets a score attached to the OutreachDraft → also visible in Phoenix
- Don't let eval failures break the pipeline — log and continue

**Success checklist:**
- [ ] Judge prompt scores Vasu's "obviously good" handwritten reference message ≥4
- [ ] Judge prompt scores a "lazy template" handwritten message ≤2
- [ ] `OutreachDraft.eval_score` field added to schema; populated by `run_hunt()`
- [ ] Eval spans visible in Phoenix as children of the `draft_message` span
- [ ] Eval call latency < 2s per message

### V10. The eval-score-climbing chart (Day 13)

The single visual that wins the demo. Show round-1 (no RAG) vs round-5 (with RAG) eval scores across the same 9-message panel.

**Deliverable:** `scripts/compare_rounds.py` — runs `run_hunt` twice (RAG off, then RAG on) against the same criteria, scores both, and emits a markdown table + a simple bar chart (matplotlib PNG).

**Success checklist:**
- [ ] Both rounds run against `fixtures/sample_resume.txt` + `fixtures/sample_criteria_scim.json`
- [ ] Markdown table shows per-message score, per-round average
- [ ] PNG chart shows round-2 avg > round-1 avg visibly (target: ≥+0.7 points)
- [ ] If the gap isn't ≥+0.7, **stop and tune the seed data** — this is the demo's payoff
- [ ] Output committed to `demo/round_comparison.md` + `demo/round_comparison.png`

---

## Track A — Arpita: schemas + frontend + outcome capture + demo polish

### A6. `OutcomeLog` schema + FastAPI wrapper (Day 8)

The thin HTTP layer between the frontend and `run_hunt()`. This is the contract — once it lands, V and A unblock each other.

**Deliverable:** `job_hunt_agent/api.py` — FastAPI app with 3 endpoints:

```python
POST /api/hunt           # body: {resume_text, criteria} -> HuntResult
POST /api/runs/{run_id}/outcomes
                         # body: {outcomes: list[OutcomeLog]} -> {ok, inserted, outcomes}
GET  /api/runs/{run_id}   # -> {hunt_result, outcomes}
```

Plus `OutcomeLog` model added to `schemas.py`.

**Implementation hint:**
- Run with `uvicorn job_hunt_agent.api:app`
- Persist outcomes in SQLite (`outcomes.db`) for v1 — Firestore is overkill for the demo
- Generate `run_id` server-side, return it in the HuntResult so the frontend can log outcomes back later
- CORS open for localhost during dev

**Success checklist:**
- [ ] `OutcomeLog` schema in `schemas.py` with round-trip test
- [ ] All 3 endpoints documented in `api.py` docstrings
- [ ] `curl POST localhost:8000/api/hunt -d '{...}'` returns valid `HuntResult` JSON
- [ ] Outcomes persist to SQLite, visible across server restarts
- [ ] Unit test exercises each endpoint with `TestClient`

### A7. Next.js frontend (Day 9–11)

Three screens, no auth, no fancy state management. Optimize for: judge can use it in one minute, demo recording looks clean.

**Deliverable:** `frontend/` directory — Next.js 14 app with three pages:

1. **Input page** — textarea for resume + a form for criteria (keywords, seniority, location). One "Run hunt" button.
2. **Review page** — shows 3 roles in cards, each with 3 referral targets and their drafted message. Per-message "Edit" + "Copy" buttons.
3. **Outcomes page** — after the user "sends" a message (just copy), they come back here and log outcomes (radio: replied / no_reply / introduced / rejected).

**Implementation hint:**
- Tailwind for styles, no design system — keep it functional
- Deploy to Vercel for the hosted URL (free tier, 5-min setup)
- Skip auth entirely. v1 is single-user.
- Loading state during `/api/hunt` matters — that call takes ~90s. Show progress: "searching jobs..." → "finding referrals..." → "drafting messages..."

**Success checklist:**
- [ ] All 3 pages render without errors
- [ ] Form submission hits `/api/hunt` and renders the result
- [ ] Loading state visible (judges will sit through 90s if there's a progress indicator; they'll close the tab if there isn't)
- [ ] Deployed to Vercel with a public URL
- [ ] Hosted URL works against a hosted backend (see A8)
- [ ] Lighthouse mobile score >70 (not perfect, just not embarrassing)

### A8. Outcome capture + hosted backend (Day 11–12)

The outcomes flow needs to actually persist across the demo. Also: the backend needs to be reachable from the Vercel frontend.

**Deliverable:** Fly.io backend + Vercel frontend wired to `POST /api/runs/{run_id}/outcomes`, with SQLite persisted on a Fly volume and Phoenix tracing enabled for hosted `/api/hunt` runs.

**Success checklist:**
- [ ] Backend deployed to public URL; `/health` and `/api/hunt` reachable from Vercel
- [ ] Fly volume mounted at `/data`; `JOB_HUNT_DB_PATH=/data/outcomes.db`
- [ ] Production env has `GOOGLE_API_KEY`, `SERPAPI_API_KEY`, `PHOENIX_API_KEY`, `PHOENIX_COLLECTOR_ENDPOINT`, `ALLOWED_ORIGINS`, and `ENABLE_TRACING=1`
- [ ] Outcomes page POSTs to `/api/runs/{run_id}/outcomes` successfully
- [ ] Logged outcomes show up on the `/runs/{run_id}` page and survive a redeploy
- [ ] Browser CORS allowlist is restricted to the production Vercel URL
- [ ] The `run_id` returned by hosted `/api/hunt` appears in Phoenix traces

### A9. Demo script + recorded video (Day 12–13)

The PLAN's demo script template (lines 0:00–3:00) is the skeleton. Adapt it to what actually shipped.

**Deliverable:** `demo/script.md` + `demo/job-hunt-agent-demo.mp4` (≤3 min).

**Recording rules:**
- Pre-record, don't go live. Use Loom or QuickTime.
- Show: paste resume → loading → 3 roles + 9 drafts → switch to Phoenix tab → show trace tree → switch to the comparison chart from V10 → close.
- Voiceover, not a talking head. Subtitles auto-generated.
- One take is fine if it's good. Don't over-polish.

**Success checklist:**
- [ ] Script written by Day 12 EOD (don't draft it Day 13 morning)
- [ ] Rough cut by Day 13 noon; final cut by EOD
- [ ] Under 3 minutes total
- [ ] Hits the 4 key beats: problem → product → Phoenix trace → eval-score-climbing graph
- [ ] Vasu watches it once, signs off before upload

### A10. Devpost writeup + README polish (Day 13–14)

The submission-day artifacts.

**Deliverable:** `README.md` + `demo/DEVPOST.md` (the writeup that gets pasted into the submission form).

**Success checklist:**
- [ ] README has: 30-sec pitch, install (3 lines), run (1 line), screenshot, link to demo video
- [ ] DEVPOST.md hits Devpost's required sections: Inspiration, What it does, How we built it, Challenges, What we learned, What's next
- [ ] Both reference the hosted URL + the video
- [ ] Public GitHub repo; MIT LICENSE at root
- [ ] Devpost submission filled out (don't actually submit until Day 14 EOD)

---

## Non-conflict guarantees (week 2)

Mostly the same rules as week 1; one new file-ownership column:

| Concern | Rule |
|---|---|
| File ownership | Vasu: `mcp_client.py`, `evals.py`, `scripts/seed_phoenix.py`, `scripts/compare_rounds.py`, `tools/draft.py` (RAG edits only). Arpita: `api.py`, `frontend/`, deployment configs, `demo/`, `README.md`. **Shared:** `schemas.py` (Arpita adds `OutcomeLog`, then locked). |
| Schema changes | After Day 8 noon, any `schemas.py` edit = 5-min sync. |
| Deploy secrets | Vasu owns Phoenix Cloud account + MCP keys. Arpita owns Vercel + backend host (Fly/Render). Both `.env.example` updated whenever a new key lands. |
| Phoenix projects | Production demo runs go to `job-hunt-agent`. Noisy iteration goes to `job-hunt-agent-dev`. The comparison chart MUST read from `job-hunt-agent` (clean traces). |
| Branches | One PR per task: `track-v/V6-mcp`, `track-a/A7-frontend-input`, etc. |
| Daily sync | 15 min, same time as week 1. Topics: blockers, schema deltas, what the other person needs to unblock by tomorrow. |

---

## Daily checkpoint — what "done for the day" looks like

### Day 8
- [ ] `v0.1-eod-week1` tag pushed
- [ ] `OutcomeLog` schema merged (A6 schema half)
- [ ] V6 (MCP client) started, A6 (FastAPI wrapper) started, A7 (Next.js skeleton) scaffolded
- [ ] Demo script v0 drafted (rough beats, not full lines)

### Day 9
- [ ] V6 done — `mcp_client.query_past_drafts()` returns live data
- [ ] A6 done — 3 endpoints respond locally
- [ ] V7 started (seed data design)
- [ ] A7 input page renders

### Day 10
- [ ] V7 done — 15–20 seed entries in `fixtures/seed_outreach.jsonl`; uploaded to Phoenix
- [ ] V8 started (self-RAG threading into draft prompt)
- [ ] A7 review page renders against a real `/api/hunt` call

### Day 11
- [ ] V8 done — self-RAG visible in Phoenix span tree
- [ ] V9 started (eval judge prompt being tuned)
- [ ] A8 backend deployed; frontend hitting it

### Day 12
- [ ] V9 done — eval scores attached to drafts in Phoenix
- [ ] A8 done — outcomes flow persisting end-to-end
- [ ] A9 demo script final, recording started

### Day 13
- [ ] V10 done — round-comparison chart shows ≥+0.7 eval-score gap
- [ ] A9 demo video final cut
- [ ] A10 README + DEVPOST drafts done

### Day 14
- [ ] v0.2-eod-week2 tag pushed
- [ ] Devpost submission saved (not submitted)
- [ ] Both: dry-run the demo end-to-end at the daily sync. Time it. Note breakage.

Days 15–21 are buffer + polish + actual submission (Jun 11).

---

## Risks for week 2 (and what to do)

| Risk | Mitigation |
|---|---|
| **The self-improvement loop isn't visually convincing.** Round-5 drafts look about the same as round-1. | This is the biggest risk to the whole demo. Mitigation: design seed data with a STRONG distinguishing pattern (V7). Test the round-1 vs round-5 gap by Day 11, not Day 13. If the gap is <+0.5 by Day 11, stop and re-engineer the seed pattern. |
| **Phoenix MCP has rough edges or auth quirks.** | Have the REST-API fallback ready before MCP is even attempted. V6 wrapper signature stays the same regardless of which transport runs underneath. |
| **Frontend timeline slips** (5 days for React + deploy is tight for one person). | Strict scope: 3 pages, no auth, no design system. If A7 isn't deployable by Day 11, fall back to a single-page React app served from FastAPI's static dir. |
| **Eval judge gives noisy scores** that don't correlate with human judgement. | Hand-write 6 reference messages (3 "obviously good", 3 "obviously bad") and validate the judge against them BEFORE wiring it into the pipeline. If the judge can't tell them apart, fix the judge prompt — don't proceed with eval until it works. |
| **Demo video is rushed and weak.** | Write the script Day 8, not Day 13. Record rough cuts continuously. Day 13 is for final edit only. |
| **Hosting outage on submission day** (Vercel/Fly free tier hiccups). | Have a recorded video + GitHub repo + the JSON output of one canonical `run_hunt()` saved as `demo/canonical_run.json`. Judges can verify even if the URL is briefly down. |
| **Gemini 3 Pro swap.** Original PLAN had this for end of week 2 as a "cost vs. quality" call. | Test Pro on 5 sample (role, person, resume) tuples Day 12. If subjectively better, swap for the demo. If marginal, stay on 2.5 Flash — cheaper, faster, currently shipping good output. |

---

## Reminders for week 3

- Day 15–17: pure polish — tighten copy, fix any judge-mentioned issues, smooth the loading states
- Day 18–19: dry-run the demo three times with stopwatch, fix anything that breaks
- Day 20: actual Devpost submission (do this BEFORE Day 21, not on Day 21)
- Day 21: buffer for the inevitable last-minute fire

If you have spare time in week 3, the stretch goals in the original plan come back in scope (multi-channel outreach, persona modes, real outcome-based reranking). **But ship the core loop first.**
