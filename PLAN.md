# Job-Hunt Agent — First Submission Draft Plan

**Hackathon:** Arize × Google (Gemini 3 + Agent Builder + Phoenix)
**Deadline:** 2026-06-11
**Today:** 2026-05-24
**Team:** Vasu (agent + backend) · Arpita (tools + data + frontend)
**Repo layout:** `job-hunt-agent/` (Python ADK app), frontend TBD week 2

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
│  Python ADK Agent (job-hunt-agent/)             │
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
- Google ADK (already working — `job-hunt-agent/hello_adk.py`)
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
- Use Google Programmable Search Engine or SerpAPI (free tier) — do not try to scrape LinkedIn directly
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
