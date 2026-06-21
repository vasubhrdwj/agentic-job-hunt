# Job Hunt Signal — V2 Rebuild: Agentic Execution Spec

> **Purpose.** This is a self-contained build specification for an orchestrator-led,
> multi-agent development loop. Drop it into a fresh agent session with no prior
> context. The orchestrator owns the task DAG, dispatches work to specialized
> sub-agents in parallel where dependencies allow, and enforces quality gates.
> **Nothing is "done" until its Definition of Done (DoD) verification command
> passes AND an independent critic agent signs off.** That gate is the whole point:
> it removes the human-cleanup-every-iteration problem.

---

## 0. How to operate as the orchestrator

You are the **orchestrator**. You do not write feature code yourself. You:

1. Maintain a **task ledger** (Section 6) — each task has: `id`, `owner role`,
   `deps`, `status ∈ {blocked, ready, in_progress, in_review, done}`, `evidence`.
2. Each tick, dispatch **every `ready` task** (deps satisfied) to a worker sub-agent,
   **in parallel** when they touch disjoint files.
3. Each worker runs the **build → self-verify → report** loop and returns a
   structured report (Section 5.3) with command output as evidence.
4. Route every `in_review` task to the **Critic agent** (Section 5.2). Critic returns
   `PASS` or `FAIL + reasons`. On `FAIL`, re-dispatch to the worker with the critic's
   findings appended. On `PASS`, mark `done` and unblock dependents.
5. At each **phase gate**, run the phase integration check before opening the next phase.
6. Never advance a phase with a red test suite or an unverified claim in any report.

**The agentic loop, per task:**
```
orchestrator.dispatch(task) ->
  worker: read contracts + task spec
        -> implement on a task branch
        -> run DoD verification command(s)
        -> if fail, iterate until pass (or escalate blocker)
        -> return Report{diff_summary, dod_evidence, residual_risks, followups}
  critic: independently re-run DoD + adversarial checks
        -> PASS | FAIL(reasons)
  orchestrator: PASS -> done + unblock deps ; FAIL -> re-dispatch with reasons
```

---

## 1. Mission and non-negotiables (the goal these agents serve)

**Mission.** Convert the existing hackathon demo into a job-hunt tool that produces
results a real user would *act on*: accurate, fresh, first-party where possible,
and honest about confidence.

**North-star quality bar (every output is measured against this):**
- Every **role** is a real, currently-open posting at a named real employer, with a
  **working first-party apply URL** and a posted-date. No reposts mislabeled as the
  employer, no expired postings, no staffing-agency relistings presented as the company.
- Every **referral** is a real person whose **current employment at the target company
  is verifiable** — otherwise it is **not shown**. No padding, no "Profile result
  mentioning X" placeholder titles, no confident copy about people we can't verify.
- Every **claim** the agent makes is grounded in tool output. Fabrication of any field
  (URL, company, title, person, requirement) is a hard failure.
- The system **states confidence and source** for every item and degrades **honestly**
  (returns fewer/none + explains) rather than manufacturing filler.

**Primary user persona (the default config, not a hardcode).** Early-career (~1 year)
backend engineer; India + remote; **generic backend** (explicitly *not* identity/IAM —
the user is pivoting away from SSO/SCIM work). The tool must generalize to other
users/domains through **config/packs**, never hardcoded keywords.

**Definition of "not half-assed" (enforced, see Section 7):** DoD gates are mandatory
and machine-checkable; the test suite is green at every task boundary; tests are
hermetic; fixtures are captured from *real* responses; the critic is adversarial.

---

## 2. Current state — ground truth (verify before trusting)

Repo: `job-hunt/` (Python backend `job_hunt_agent/`, Next.js `frontend/`, MIT, deployed
to Render + Vercel, **submitted** to a hackathon — do not disturb the live demo).

Pipeline today (`job_hunt_agent/run.py::run_hunt`):
```
JobCriteria --search_jobs--> [Role]
Role        --find_referrals--> [Person]
Role,Person --draft_message--> message   (self-RAG from Phoenix past drafts)
            --score_draft--> 1..5 LLM-judge composite
-> HuntResult ; outcomes logged to SQLite ; spans to Arize Phoenix
```
Key files: `job_hunt_agent/{agent,run,api,schemas,evals,mcp_client,persistence,tracing}.py`,
`job_hunt_agent/tools/{job_search,referrals,draft,mocks,registry}.py`,
`tests/test_*.py`, `fixtures/`, `scripts/{seed_phoenix,validate_judge,compare_rounds}.py`.

**Already shipped this iteration — branch `track-j/J1-google-jobs` (J1, DONE & live-verified):**
- `job_hunt_agent/tools/job_search.py::search_jobs` rewritten from a Google **dork**
  (`site:linkedin.com/jobs/view "kw"`) to SerpAPI's structured **`google_jobs`** engine.
  Maps `jobs_results` → existing `Role` contract. Returns `[]` honestly on no match.
- Added `fixtures/sample_criteria_backend.json`.
- **Open item:** 6 old dork-path tests in `tests/test_job_search.py` are RED (they feed
  `organic_results` to a path that no longer exists). Migrating them is task **JS-TEST**.
- The dork-only helpers in `job_search.py` are now dead except the four still imported by
  `referrals.py` (`_fetch_serpapi_search`, `_get_serpapi_api_key`, `_iter_serpapi_results`,
  `_load_dotenv_if_available`). Dead-code removal is task **JS-CLEAN**.

**Evidence-based findings that shape the architecture (reproduced live, not assumed):**
- `google_jobs` fixes data *structure/freshness* completely, but **discovery-by-keyword**
  for "remote/junior backend, India" skews heavily to **staffing agencies, contract/gig
  reposts, and aggregator relistings** (observed: 4 of 5 results were Insight Global /
  Mercor $85-hr contract / "join one of our clients" / a repost blog mislabeled "MongoDB").
- **Bespoke boards don't all syndicate.** Live probe: querying `backend engineer google`
  returned **zero** Google postings (Google doesn't feed Google-for-Jobs). `amazon` roles
  *do* surface but route via Talent500/LinkedIn/Indeed, not `amazon.jobs`, with messy
  attribution (`company = "ADCI MAA 15 SEZ"`).
- **Conclusion:** discovery alone cannot deliver the quality bar. A **curated company
  registry + tiered first-party source resolver** is required. `google_jobs` becomes the
  breadth/fallback + discovery feed, not the primary source.

---

## 3. Target architecture

```
                       ┌─────────────────────────────────────────────┐
 JobCriteria + Pack ──▶ │ CompanyRegistry (config packs, user-editable) │
                       └───────────────┬─────────────────────────────┘
                                       │ for each Company
                            ┌──────────▼───────────┐
                            │   SourceResolver      │  picks best adapter per company
                            └──────────┬───────────┘
        ┌──────────────┬──────────────┼──────────────┬───────────────┐
        ▼              ▼              ▼              ▼               ▼
   Greenhouse      Lever/Ashby     Workday      Bespoke (Amazon,   google_jobs
   adapter         adapters        adapter      …) adapters        fallback adapter
   (first-party, free JSON)                     (per-company)      (breadth/last resort)
        └──────────────┴──────────────┴──────────────┴───────────────┘
                                       │  [Role] (first-party apply_url, posted_at, employment_type)
                            ┌──────────▼───────────┐
                            │  ResumeFitScorer      │  rank by resume↔JD fit, evidence-based
                            └──────────┬───────────┘
                            ┌──────────▼───────────┐
                            │  Referrals (honest)   │  verified-or-omitted, no padding
                            └──────────┬───────────┘
                            ┌──────────▼───────────┐
                            │  Draft + Outcome-RAG  │  retrieval ranked by logged REPLY outcomes
                            │  + LLM judge (gate)    │
                            └──────────┬───────────┘
                                       ▼
                              HuntResult + Phoenix trace
```

Design rules:
- **Adapters cover companies by *platform*, not one-by-one.** ~6–8 platform adapters reach
  thousands of companies. Per-company cost is only "record which platform + token."
- **Source resolver is tiered** per company: explicit `source` field → platform adapter →
  bespoke adapter → `google_jobs` by company name → (optional) headless scrape. The chosen
  source and its confidence are attached to every `Role`.
- **Honest degradation everywhere.** Empty beats fabricated.
- **Config over code.** Packs/keywords/locations/employment-type live in config, not literals.

---

## 4. Data contracts (Phase 0 — the interfaces no agent may break)

Author these first; every other agent imports them. Pydantic v2, in
`job_hunt_agent/schemas.py` (extend) + `job_hunt_agent/sources/base.py` (new).

```python
# --- registry ---
class CompanySource(str, Enum):
    greenhouse = "greenhouse"; lever = "lever"; ashby = "ashby"
    workday = "workday"; smartrecruiters = "smartrecruiters"; workable = "workable"
    bespoke = "bespoke"; google_jobs = "google_jobs"; scrape = "scrape"

class Company(BaseModel):
    name: str
    slug: str                         # stable id, e.g. "razorpay"
    source: CompanySource             # how to reach its board
    source_token: str | None          # greenhouse board token / lever site / workday tenant…
    careers_domains: list[str] = []    # for first-party apply-URL validation
    hire_locations: list[str] = []     # e.g. ["India","Remote"]
    tags: list[str] = []               # ["backend","fintech"] — used by packs
    active: bool = True

# --- jobs (extends existing Role; keep old fields backward-compatible) ---
class EmploymentType(str, Enum):
    full_time="full_time"; contract="contract"; intern="intern"; unknown="unknown"

class Role(BaseModel):
    company: str
    title: str
    url: str                          # PREFERRED: first-party apply URL
    location: str
    summary: str
    match_reason: str
    # V2 additions:
    source: CompanySource = CompanySource.google_jobs
    apply_urls: list[str] = []         # all known apply links, first-party first
    posted_at: str | None = None
    employment_type: EmploymentType = EmploymentType.unknown
    raw_description: str | None = None # full JD text (input to fit scoring)
    fit_score: float | None = None     # 0..1, set by ResumeFitScorer
    confidence: float = 1.0            # source-quality 0..1

# --- source adapter protocol (all adapters implement this) ---
class SourceAdapter(Protocol):
    name: str
    def supports(self, company: Company) -> bool: ...
    def fetch_open_roles(self, company: Company, criteria: "JobCriteria") -> list[Role]: ...

# --- criteria (extend) ---
class JobCriteria(BaseModel):
    role_keywords: list[str]
    seniority: Literal["junior","mid","senior","staff"]
    location: list[str]
    comp_min_lpa: int | None = None
    comp_max_lpa: int | None = None
    # V2 additions:
    employment_types: list[EmploymentType] = [EmploymentType.full_time]
    max_age_days: int | None = 45
    country: str = "in"

# --- people (extend; honesty fields) ---
class Person(BaseModel):
    name: str; title: str; company: str; profile_url: str
    source: Literal["linkedin","github","company_page","other"]
    why_relevant: str
    verified_current_employer: bool = False   # only True with evidence
    confidence: float = 0.0
```

`OutreachDraft`, `OutcomeLog`, `HuntResult`, `PastDraft` stay as-is (see `schemas.py`),
except retrieval now reads outcomes (Phase 4).

---

## 5. Agent roster, critic, and report format

### 5.1 Worker roles (spawn one sub-agent per task, scoped to its files)
- **Contracts agent** — owns Section 4 schemas + `SourceAdapter` protocol. Blocking.
- **Source-adapter agents** (parallel, one per adapter) — Greenhouse, Lever, Ashby,
  Workday, SmartRecruiters, Workable, Amazon(bespoke), GoogleJobsFallback.
- **Registry/curation agent** — builds the seed pack; resolves+**live-verifies** each
  company's `source`+`source_token`.
- **Resolver agent** — `SourceResolver`, tiered selection + fallback + dedupe + freshness.
- **Matching agent** — `ResumeFitScorer` + ranking.
- **Referrals agent** — honest referrals redesign.
- **Loop/eval agent** — outcome-driven self-RAG + judge recalibration.
- **Integration agent** — `run_hunt`, FastAPI, Next.js wiring, E2E.
- **Critic/QA agent** — independent gate (below). Never the same agent that built the task.

### 5.2 Critic agent (the gate that prevents half-assed merges)
For each `in_review` task, the critic independently:
1. Re-runs the DoD verification command(s) from a clean state; confirms the claimed output.
2. **Adversarial checks:** grep the diff for fabrication (hardcoded names/URLs/sample
   people leaking into prod paths), for `# TODO`/stubbed returns, for swallowed exceptions,
   for non-hermetic tests (real network in unit tests), for scope creep beyond the task,
   and for regressions (previously-green tests still green).
3. Confirms every external-fact claim in the worker's report is backed by captured output.
4. Returns `PASS` or `FAIL(reasons[])`. **One real failure = FAIL.**

### 5.3 Worker report format (returned to orchestrator)
```
TASK: <id>
BRANCH: <task branch>
CHANGED: <files + 1-line each>
DOD EVIDENCE:
  $ <verification command>
  <captured output proving the DoD>
RESIDUAL RISKS: <known gaps / what was NOT done>
FOLLOWUPS: <new tasks discovered, if any>
```

---

## 6. Task DAG with Definition of Done

Legend: **DoD** = acceptance criteria + the exact command whose output is the proof.
Tasks with no shared files run in parallel. `★` = on the critical path.

### Phase 0 — Contracts & scaffolding (blocking) ★
- **C1 — Schemas & adapter protocol.** Implement Section 4 in `schemas.py` +
  `sources/base.py`. Backward-compatible (existing `Role(...)` constructions still valid).
  - **DoD:** `.venv/bin/python -m pytest tests/test_schemas.py -q` green; `python -c
    "from job_hunt_agent.sources.base import SourceAdapter"` imports; existing suite no
    *more* red than the known JS-TEST 6.
  - **Critic:** no field removed from existing models; new fields have safe defaults.

### Phase 1 — Job-source layer (most tasks parallel after C1)
- **JS-TEST ★ — migrate `tests/test_job_search.py`** to the `google_jobs` path. Mock
  `_fetch_google_jobs` with a **recorded real** `google_jobs` payload fixture
  (`tests/fixtures/google_jobs_sample.json`, captured live once). Cover: mapping,
  seniority filter, dedupe by `job_id`, honest-empty, employment-type tagging.
  - **DoD:** `pytest tests/test_job_search.py -q` → **all green, 0 network calls**
    (assert via a patched `urlopen` that raises if hit).
- **SRC-GH — Greenhouse adapter.** `sources/greenhouse.py`. `GET
  https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` (no auth). Map →
  `Role` (first-party `absolute_url`, full `content`, `updated_at`→posted_at).
  - **DoD:** hermetic unit test against `tests/fixtures/adapters/greenhouse.json` (real
    capture) yields ≥1 `Role` with first-party apply URL + non-empty `raw_description`;
    empty/4xx → `[]` + log. Documented live smoke (not in CI).
- **SRC-LEVER — Lever adapter.** `GET https://api.lever.co/v0/postings/{site}?mode=json`
  (no auth). Map `text`→title, `applyUrl`/`hostedUrl`→url, `categories`→location/type,
  `descriptionPlain`→raw_description, `lists`→summary highlights. DoD mirrors SRC-GH.
- **SRC-ASHBY — Ashby adapter.** **Step 1: verify endpoint shape** (`posting-api/
  job-board/{board}`) live and capture a fixture. Then map → `Role`. DoD mirrors SRC-GH.
- **SRC-WD — Workday adapter.** **Step 1: verify** the `POST .../wday/cxs/{tenant}/{site}/
  jobs` pattern + per-tenant tokens against one real tenant; capture fixture. Map → `Role`.
  If verification fails, return a documented `NotSupported` and downgrade those companies
  to `google_jobs`. DoD mirrors SRC-GH **plus** a written verification note.
- **SRC-SR / SRC-WK — SmartRecruiters / Workable adapters.** Same verify-first pattern
  (`api.smartrecruiters.com/v1/companies/{c}/postings`, `apply.workable.com/api/v3/
  accounts/{name}/jobs`). DoD mirrors SRC-GH.
- **SRC-AMZN — Amazon bespoke adapter.** Verify `amazon.jobs` public JSON search endpoint;
  capture fixture; map → `Role`. DoD mirrors SRC-GH. (Template for future bespoke big-tech.)
- **SRC-GJ — google_jobs fallback adapter.** Wrap existing `_fetch_google_jobs` behind
  `SourceAdapter`; `supports()`=any; add company-name filter + `employment_type` from
  `detected_extensions.schedule_type`; flag aggregator/staffing `via` as low confidence.
  - **DoD:** unit test (fixture) tags a `$85/hour` contract as `contract` and a recruiter
    `via` with `confidence < 0.5`.
- **REG — Company registry + seed pack.** `sources/registry.py` + `config/company_packs/
  backend_india.yaml`. Curation agent assembles **~20 companies** and **live-verifies**
  each `source`+`source_token` resolves to ≥1 open posting. **Do not invent tokens** —
  each must be confirmed. Candidate companies to *verify* (not assume): Razorpay, Postman,
  Hasura, Zerodha, CRED, Atlassian, MongoDB, Twilio, HashiCorp, Confluent, Vercel, Sentry,
  GitLab, Grafana Labs, Browserstack, Freshworks, Groww, Swiggy, Meesho, PhonePe.
  - **DoD:** `.venv/bin/python scripts/verify_registry.py --pack backend_india --live
    --strict-live` reports **0 dead or unverified sources**; output (company → source →
    #open roles) captured as evidence. Any company that won't verify is dropped or marked
    `google_jobs`, never left broken.
- **RES ★ — SourceResolver.** Given `Company`, pick adapter (explicit `source` → platform
  → bespoke → google_jobs fallback). Aggregate across a pack, dedupe by (company,title) +
  apply-URL, drop postings older than `max_age_days`, filter `employment_types`.
  - **DoD:** integration test over the seed pack returns first-party roles with working
    apply URLs; deliberately-dead company → honest `[]`+log, no crash.
- **JS-CLEAN — remove dead dork code** from `job_search.py` (keep the 4 helpers imported by
  `referrals.py`). **DoD:** suite green; `referrals.py` imports unbroken.

**Phase-1 gate:** full suite green + a live pack run prints ≥10 first-party, full-time,
in-date backend roles across ≥5 distinct real companies, each with a working apply URL.

### Phase 2 — Matching
- **FIT — ResumeFitScorer.** `job_hunt_agent/matching.py`. Score resume↔`raw_description`
  (embeddings, e.g. Gemini text-embedding, or hybrid keyword+LLM), 0..1, with a rationale
  citing overlapping requirements. Rank roles; rewrite `match_reason` to quote a real JD
  requirement. **DoD:** calibration fixture — the user's backend resume scores a backend JD
  **>** an irrelevant JD by a documented margin; `scripts/validate_fit.py` enforces it.

### Phase 3 — Referrals (honesty redesign)
- **REF — honest `find_referrals`.** Return a `Person` **only** with evidence of current
  employment at the target company (company team/about page, or a profile explicitly
  stating current employer). Kill padding + placeholder titles. Set
  `verified_current_employer` + `confidence`. When none verify, return `[]` + an honest
  "no verified contacts; here's how to find one" note. **DoD:** golden test asserts **no**
  `confidence < 0.5` person and **no** "Profile result mentioning" titles ever reach output.

### Phase 4 — Outcome-driven loop + evals
- **LOOP — outcome-ranked self-RAG.** Retrieval in `mcp_client.py`/`draft.py` ranks past
  drafts by **logged reply outcome** (`replied`/`introduced` > neutral > `no_reply`),
  with judge score as tiebreaker. **DoD:** seeded-outcome test shows a high-reply exemplar
  retrieved over a high-judge/low-reply one; `scripts/compare_rounds.py` still gates ≥ +0.3.
- **JUDGE — recalibrate** `validate_judge.py` references for generic-backend outreach.
  **DoD:** judge gate passes on new good/bad references.

### Phase 5 — Integration, E2E, ops
- **INT — wire resolver into `run_hunt`**, FastAPI (`/api/hunt` accepts `pack`,
  `employment_types`), Next.js (pack selector, employment + source/confidence badges).
  **DoD:** `pytest` green; `uvicorn` boots; one live E2E hunt returns the bar below.
- **E2E gate (release):** a generic-backend/India hunt returns **≥3** first-party,
  full-time, in-date roles from **curated** companies (working apply URLs + fit scores) and
  **either** verified referrals **or** an honest omission; a Phoenix trace exists for the run.

---

## 7. Quality gates & anti-patterns (the "not half-assed" contract)

**Iron laws (any violation = task FAIL):**
1. **DoD or it didn't happen.** No task is `done` without its verification command output
   captured in the report and re-confirmed by the critic.
2. **Green at every boundary.** The suite is green when a task closes. No "I'll fix tests
   later." (JS-TEST exists precisely to clear the one known red set early.)
3. **Hermetic tests.** Unit tests make **zero** network calls; they run against fixtures
   captured from **real** responses. A test that hits the network is a FAIL.
4. **No fabrication.** No invented company, URL, person, title, requirement, or board token
   anywhere in a code path or fixture. Tokens/endpoints are **verified live** before use.
5. **Honest degradation.** Return fewer/empty + explain. Never pad to hit a count.
6. **Scope fences.** A worker edits only its task's files. Cross-cutting changes go back to
   the orchestrator as a new task.
7. **No swallowed errors.** Failures log and surface; no bare `except: pass` on data paths.

**Definition-of-Done template (every task uses it):**
```
DoD(<task>):
  - behavior: <observable outcome>
  - verify:   <exact command>
  - expect:   <what the output must show>
  - regression: existing green tests stay green
  - critic:   <the specific adversarial checks for this task>
```

---

## 8. Guardrails & ops

- **Branching.** Integration branch `v2-rebuild` off `main`. One branch per task off
  `v2-rebuild`; PRs land into `v2-rebuild`, **never `main`**. `main` redeploys Render on
  push, which **wipes the ephemeral SQLite outcomes DB and disturbs the submitted demo** —
  merge to `main` only when the E2E release gate passes and the user approves.
- **Secrets.** `.env` (`GOOGLE_API_KEY`, `SERPAPI_API_KEY`, `PHOENIX_*`). Production startup
  already rejects missing secrets / localhost CORS / mocks; keep that.
- **Cost.** Prefer free first-party ATS adapters; `google_jobs` is fallback/discovery only
  (SerpAPI credits). Cache adapter responses per (company, day). Batch embeddings.
- **Tracing.** Every new tool/adapter emits a Phoenix span; resolver records chosen source
  + confidence as span attributes.

---

## 9. First sprint (start here in the new chat)

1. **C1** (Contracts) — blocking; everyone waits on it.
2. In parallel after C1: **JS-TEST**, **SRC-GH**, **SRC-LEVER**, **SRC-GJ**, and **REG**
   (curation can research/verify tokens while adapters are built).
3. Then **RES** (resolver) → **Phase-1 gate**.
4. Proceed Phase 2 → 5 per the DAG.

Stop after each phase gate and report the captured evidence to the user before continuing.

---

## Appendix A — Verified API facts (trust these; re-verify the flagged ones)

- **SerpAPI `google_jobs`** — *verified live in this repo.* Params: `engine=google_jobs`,
  `q`, `location`, `gl`, `hl`. Per job: `title`, `company_name`, `location`, `via`,
  `description`, `job_highlights[]`, `apply_options[{title,link}]`,
  `detected_extensions{posted_at,schedule_type,work_from_home}`, `job_id`; paginate via
  `serpapi_pagination.next_page_token`. Returns an `error` string on no-match (treat as empty).
- **Greenhouse Job Board API** — *verified (docs).* `GET https://boards-api.greenhouse.io/
  v1/boards/{board_token}/jobs?content=true`, **no auth**. Fields: `id`, `title`,
  `location.name`, `absolute_url`, `content` (full, HTML-escaped), `departments`,
  `updated_at`.
- **Lever Postings API** — *verified (docs).* `GET https://api.lever.co/v0/postings/{site}
  ?mode=json`, **no auth**. Fields: `text`, `hostedUrl`, `applyUrl`,
  `categories{team,location,commitment,department}`, `description`, `descriptionPlain`,
  `lists[]`. (`createdAt` not documented — derive freshness cautiously.)
- **Live noise findings** — *verified live:* keyword discovery skews to staffing/contract;
  Google not syndicated to google_jobs; Amazon surfaces via aggregators with messy
  attribution. These justify the curated+first-party architecture.

**NEEDS VERIFICATION (each adapter's Step 1 — do not build on assumption):** Ashby
`posting-api/job-board/{board}`; Workday `wday/cxs/{tenant}/{site}/jobs` + tenant tokens;
SmartRecruiters `v1/companies/{c}/postings`; Workable `api/v3/accounts/{name}/jobs`;
Amazon `amazon.jobs` JSON search endpoint. Capture a real fixture as part of verification.

## Appendix B — Repro commands

```bash
# live google_jobs check (env-loaded)
.venv/bin/python - <<'PY'
from job_hunt_agent.tools.job_search import search_jobs
from job_hunt_agent.schemas import JobCriteria
import json
c = JobCriteria(role_keywords=["backend engineer","software engineer"],
                seniority="junior", location=["Remote-India","Bengaluru","Hyderabad"])
print(json.dumps([r.model_dump() for r in search_jobs(c)], indent=2))
PY

# suite (must be green at every task boundary, except the known JS-TEST set until migrated)
.venv/bin/python -m pytest -q
```
