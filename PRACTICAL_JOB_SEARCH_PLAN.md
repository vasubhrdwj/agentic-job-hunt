# Practical Job Search Control Room — Implementable Plan

**Status:** Phase 0 complete; Phase 1A reusable profile/search workflow complete; Slice 2 opportunity radar is next
**Primary user:** one private owner using the product for a real job search
**North-star metric:** qualified interviews for genuinely better roles per hour of user effort
**First complete release:** durable opportunity radar + application pipeline + five-person contact bench + manual, staged outreach

## 1. Product outcome

This project should become a personal **Career Upgrade Engine**, not another job-board search box and not a mass auto-applier.

The system should answer four questions every day:

1. Which newly found roles are genuinely worth considering?
2. Which of those roles can I credibly win, and where are the gaps?
3. What is the highest-value next action for every active application?
4. Who are the best verified people who could help, and who should I contact next?

The operating loop is:

```text
career profile
  -> scheduled broad scans
  -> persist + deduplicate + verify
  -> daily decision inbox
  -> pursue / watch / dismiss
  -> application dossier
  -> five-person contact bench
  -> apply + staged outreach
  -> follow-ups + interviews + outcomes
  -> improve ranking and outreach from real results
```

### Product principles

- **Ingest broadly, rank narrowly.** Missing salary, date, or employment metadata lowers confidence; it does not silently hide an otherwise strong first-party role.
- **“Better” is separate from “possible.”** A role can be a strong resume fit and still be a poor career move.
- **Evidence over confidence theatre.** Every recommendation exposes supporting facts, gaps, unknowns, and source freshness.
- **Enrich only after intent.** Do not spend contact-search or generation budget until the user presses `Pursue`.
- **One next action per active application.** A tracker without a dated next action becomes a graveyard.
- **Manual external action.** The product helps prepare applications and outreach; the owner remains the sender and applicant.
- **No invented facts.** Resume claims, compensation, contact relationships, and employer status must be sourced or marked unknown.

### Explicit non-goals for the first release

- Automatic mass applications
- Automatic LinkedIn or email sending
- Public signup or multi-tenant SaaS
- A generic AI chat screen
- Unsupported compensation estimates presented as facts
- One opaque “fit percentage” that hides trade-offs
- More visual polish before the opportunity supply, persistence, and workflow are reliable

## 2. What exists and what changes

| Area | Reuse | Current limitation | Planned change |
| --- | --- | --- | --- |
| Job discovery | Eight first-party/ATS source strategies, company registry, URL checks | A run returns a small disposable result set | Scheduled scans persist every observation and create a deduplicated inbox |
| Role matching | Resume fit scorer and job-description evidence | Fit and opportunity quality are conflated | Separate eligibility, career value, evidence confidence, and action priority |
| Contacts | Current-employer verification, public-profile discovery, and five contacts per returned role | Evidence is compressed and drafts are still generated eagerly | Discover 10–12, retain at least five verified and diverse contacts, then sequence outreach |
| Drafting | Drafter, evaluator, and existing outcome loop | Edits live only in React state; learning may use generated rather than sent text | Persist every message version and learn only from the exact version marked sent |
| Queue | Postgres leases, heartbeats, retries, cancellation, generic job kinds, and practical `legacy_hunt` dispatch | Only the hunt job has a full domain handler | Add handlers for scans, assessments, contacts, and application packs |
| Storage | Migrated Postgres owner/session/job/hunt/result/outcome storage with encrypted private JSON | Hunt results remain run-shaped and cannot support a daily inbox or pipeline | Add normalized profile, search, opportunity, application, and contact records |
| API | Owner-session FastAPI with owner-scoped hunt/run/outcome resources | Product resources beyond a hunt run do not exist yet | Add profile, search, opportunity, application, contact, outreach, and action APIs |
| Frontend | Authenticated Next.js shell, same-origin proxy, durable deep links, and useful form/card/clipboard patterns | Still run-centric, with no persistent profile or application state | A Today inbox, dossier, pipeline, search settings, and contact workflow |
| Observability | Phoenix/OpenTelemetry and live registry verification | Health says only that the HTTP process responds | Readiness includes database, worker heartbeat, scheduler lag, and source-run health |

The existing `run_hunt` path remains available temporarily for demo compatibility. It is not the architecture for the practical product.

## 3. Locked five-person contact decision

Yes, finding five appropriate people raises the probability that at least one responds. Under the simplifying assumption that each person independently replies with probability `p`:

```text
P(at least one reply) = 1 - (1 - p)^5
```

Examples:

- 10% per-person reply probability -> about 41% chance of at least one reply
- 15% -> about 56%
- 20% -> about 67%

Real responses are correlated, so this is not a promise and it is not five times the effectiveness. Five near-identical messages sent together can also look spammy, reach the same internal thread, and reduce goodwill.

The product rule is therefore:

> **Find a bench of at least five verified, appropriate contacts for every pursued role; contact them progressively, not all at once.**

Five is a **coverage target**, not permission to lower the evidence bar. If only three people can be verified after exhausting the search budget, the UI must say `3 of 5 verified` and explain why. It must never invent or pad the list.

### 3.1 Contact bench composition

Discover up to 10–12 candidates, select the top five, and retain additional verified candidates behind `Show more`.

Target a diverse bench:

1. A warm connection or evidence-backed introduction path, when one exists
2. A likely same-team peer
3. A second same-team or closely adjacent technical peer
4. A relevant hiring manager or team leader
5. A recruiter or sourcer for the relevant function

If there is no verified warm path, use another strong team-relevant person. Never infer alumni, friendship, community membership, or a relationship from a name or demographic signal.

Each contact type receives a different ask:

- Warm path: introduction or referral
- Team peer: team context and fit check; referral only if comfortable
- Manager: concise fit signal and a team/hiring question
- Recruiter: interest, availability, and eligibility
- Alumni/community path: advice or an introduction, only when the connection is evidenced

### 3.2 Eligibility and ranking

A candidate is eligible for the verified bench only when all are true:

- Stable public profile URL
- Current-employer evidence
- Evidence excerpt or structured fact, evidence URL, and verification timestamp
- Verification confidence at or above the configured floor; start at `0.75`
- Concrete relevance to the specific role
- No evidence that the person is a former employee
- No duplicate normalized profile URL
- No active do-not-contact or cross-role/company cooldown

Use an explainable score:

| Component | Weight |
| --- | ---: |
| Relationship strength with explicit evidence | 25 |
| Team proximity | 25 |
| Current-employer verification | 20 |
| Ability to help for this role | 15 |
| Evidence freshness and completeness | 10 |
| Profile/source quality | 5 |
| Same-persona or same-team redundancy penalty | up to -15 |

Every relationship and team-proximity field is labelled `verified`, `inferred`, or `unknown`. The UI shows a short ranking explanation rather than just a number.

### 3.3 Outreach sequencing

Default policy:

- **Day 0:** unlock the strongest warm or team-relevant contact.
- A recruiter may be unlocked in parallel only because the purpose and wording are different.
- **After three business days without a useful response:** unlock the next contact from a different category or team cluster.
- Continue progressively; keep contacts four and five as reserves until earlier attempts need escalation.
- Allow no more than three cold employee contacts at one company in a rolling seven-day window.
- Allow one follow-up per person after five business days.
- Do not contact the same person again within 30 days unless they replied and invited it.

Nothing sends automatically. `Copy and mark sent` persists the exact edited message, timestamp, channel, and next follow-up.

Pause remaining outreach for user review after any reply. Stop it automatically after:

- A referral or introduction
- A substantive recruiter or hiring-manager conversation
- The role closes
- The application reaches interview, rejection, withdrawal, or offer
- A do-not-contact request
- The owner manually stops the sequence

### 3.4 Contact states

```text
discovered -> verified -> reserve / ready -> drafted -> sent
sent -> follow_up_due -> followed_up -> no_reply
sent / followed_up -> replied -> introduced / referred / declined
any state -> paused / stopped
```

Server-side rules, not hidden buttons, enforce cooldowns, idempotency, maximum waves, and stop conditions.

## 4. Practical deployment and architecture

### 4.1 Deployment decision

The practical product targets one private, always-on workspace:

- **Postgres 16** is the durable system of record.
- **FastAPI web process** serves owner-scoped APIs.
- **Worker process** claims durable background jobs with leases, heartbeats, and retries.
- **Scheduler tick inside the worker** enqueues due saved searches idempotently.
- **Next.js frontend** calls the API through a same-origin `/api` proxy.
- **No Redis or Celery initially.** Postgres is sufficient for this single-owner workload.

Local development uses Docker Compose with a persistent Postgres volume. Hosted use requires managed Postgres plus both web and worker services. The current free Render blueprint with ephemeral `/tmp` SQLite is a demo path and cannot pass the release gate.

### 4.2 Owner access

Do not build public accounts. Build one secure owner workspace:

- Store `owner_id` on every personal record from day one.
- `POST /api/session` accepts the private owner token, compares it to `JOB_HUNT_OWNER_TOKEN_HASH`, and returns a random opaque session in an `HttpOnly`, `Secure`, `SameSite=Strict` cookie.
- Store only the session-token hash, owner, creation time, and expiry.
- Require an origin/CSRF check for mutations.
- Permit a development bypass only when bound to loopback and `ENVIRONMENT != production`; reject it at production startup.
- Reuse the current `DataKeyring` to encrypt persistent resume text, generated application material, and other sensitive free text.

This fixes the current problem where a run is inaccessible in a new tab or browser session while avoiding a premature multi-user auth system.

### 4.3 Background job kinds

Generalize the current durable worker rather than running the entire hunt eagerly:

```text
legacy_hunt
scan_saved_search
scan_company
finalize_scan
assess_opportunity
discover_contacts
generate_application_pack
```

Each job has a deterministic dedupe key, subject IDs, status, attempt count, `run_after`, lease owner/token/expiry, heartbeat, stage, and sanitized error.

Examples:

```text
scan:{saved_search_id}:{scheduled_slot}
scan-company:{scan_id}:{company_slug}
assess:{opportunity_id}:{resume_version}:{job_version}:{algorithm_version}
contacts:{application_id}:{contact_plan_version}
pack:{application_id}:{resume_version}:{job_version}
```

### 4.4 Scheduled scan flow

1. Scheduler atomically claims due saved searches.
2. Create one `scan_run` for the search and scheduled time slot.
3. Enqueue one `scan_company` job per company/source partition.
4. Fetch broadly and record source success, completeness, warnings, and observations.
5. Upsert canonical postings and immutable posting versions.
6. Finalizer evaluates hard exclusions, preferences, and unknown-field policies over persisted data.
7. Create search matches and one owner-level opportunity per canonical posting.
8. Assess only new or changed opportunities.
9. Advance `next_scan_at` in the owner’s timezone.
10. The Today page reads only persisted results; it never waits on live providers.

## 5. Domain and persistence design

Use synchronous SQLAlchemy 2, psycopg 3, and Alembic. Keep routes thin: routers validate and authorize, services hold business rules, and repositories own SQL.

### 5.1 Core records

| Entity | Essential fields and constraints |
| --- | --- |
| `owners` | `id`, display name, timezone, created/updated timestamps |
| `owner_sessions` | hashed opaque token, `owner_id`, expiry, revoked timestamp |
| `candidate_profiles` | career thesis, location, work authorization, preferences, onboarding state, version |
| `career_tracks` | name, role families, seniority range, target locations, priorities, active flag |
| `resume_versions` | encrypted content, content hash, source, parent ID, base flag, created timestamp |
| `achievement_evidence` | approved statement, source resume/excerpt, skills, approval state, version |
| `saved_searches` | track, structured criteria, schedule, timezone, active flag, last/next scan, version |
| `scan_runs` | saved search, scheduled slot, status, counts, started/completed timestamps |
| `scan_source_runs` | scan, company/source, status, complete-inventory flag, warnings, observed count |
| `job_postings` | stable identity, canonical URL, company/source IDs, lifecycle, first/last seen |
| `job_posting_versions` | content hash, title, full JD, location/comp/type/date evidence, observed timestamp |
| `job_aliases` | alias URL/source key -> canonical posting |
| `scan_observations` | scan source run, posting, source facts, observation timestamp |
| `saved_search_matches` | search, posting, filter result, reasons, discovered timestamp |
| `opportunities` | unique `(owner_id, job_posting_id)`, lane, decision, decision reason, version |
| `match_assessments` | opportunity, profile/resume/JD/algorithm versions, scores, evidence, gaps |
| `applications` | opportunity, stage, outcome, applied date, resume version, current next action, version |
| `action_items` | application/contact, kind, title, due time, state, completed/snoozed timestamps |
| `activity_events` | immutable owner/application event timeline with typed metadata |
| `contacts` | normalized profile URL, public identity, source, employer evidence, verified timestamp |
| `application_contacts` | application/contact, category, rank, score components, proximity, state, wave |
| `contact_plans` | application, target `5`, candidate budget, cooldown policy, status, stop reason |
| `outreach_attempts` | contact link, exact message, channel, initial/follow-up, sequence, draft/sent times, state |
| `outreach_events` | attempt, event type, notes, event time, idempotency key |
| `pursuit_packs` | application, input versions, encrypted artifacts, claims provenance, approval state |
| `background_jobs` | kind, dedupe key, payload IDs, status, retries, scheduling and lease fields |
| `worker_heartbeats` | worker ID, supported kinds, last heartbeat, current job, build version |

All mutable resources have an integer `version` for optimistic concurrency. Mutations accept an idempotency key. Events are append-only.

### 5.2 Stable job identity

Extend `Role` and every source adapter with `company_slug` and `source_job_id`.

Preferred identity:

```text
source:{source}:{company_slug}:{source_job_id}
```

Fallback only when the source exposes no stable ID:

```text
url:{company_slug}:{sha256(canonical_url_without_tracking_parameters)}
```

Native IDs should come from:

- Greenhouse: posting `id`
- Lever: posting `id`
- Ashby: posting `id`
- Workday: `jobReqId` or stable requisition ID
- SmartRecruiters: posting `id`
- Workable: shortcode/stable posting ID
- Amazon: `id_icims` or stable posting ID
- Google Jobs: `job_id`

Never use company + title as durable identity. A title, URL, or JD change creates a new `job_posting_version`, not a duplicate opportunity. Different requisition IDs remain distinct.

Close a posting only when:

- The authoritative source explicitly reports closure; or
- Two consecutive successful, authoritative, complete-inventory scans omit it.

A timeout, adapter error, partial inventory, or one missing observation never closes a posting. A reappearing source ID reopens the same posting and emits an event.

### 5.3 Search criteria cleanup

Replace ambiguous/unimplemented criteria with:

- `seniority_levels: list[...]` instead of one mandatory band
- `minimum_comp_lpa`: a real hard exclusion only when compensation is verified
- `target_comp_lpa`: a career-value preference
- `salary_unknown_policy: show | hide`, default `show`
- `employment_type_unknown_policy: show | hide`, default `show`
- `posted_date_unknown_policy: show | hide`, default `show`
- Separate `hard_exclusions` from `ranking_preferences`

Continue accepting legacy `seniority`, `comp_min_lpa`, and `comp_max_lpa` during migration. Translate `comp_min_lpa` to `minimum_comp_lpa`; deprecate `comp_max_lpa` rather than pretending it is implemented.

### 5.4 Explainable assessment

Do not collapse the result into one percentage. Persist four assessments:

1. **Eligibility** — required skills/experience the candidate can evidence; band `strong`, `plausible`, or `weak`.
2. **Career value** — compensation, scope, learning, company quality, flexibility, and the owner’s weighted priorities; band `high`, `medium`, or `low`.
3. **Evidence confidence** — verified/inferred/unknown coverage and source freshness.
4. **Action priority** — whether the owner should act now, based on the first three plus freshness and closing/change signals.

Each assessment stores algorithm/prompt version, component scores, evidence IDs, gaps, unknowns, and a human-readable explanation. Changing the resume, job description, or algorithm creates a new version.

## 6. API contract

Use one standard error shape with `code`, safe `message`, `retryable`, optional field errors, and request ID. Mutable resources use `If-Match`/version checks; mutation endpoints accept idempotency keys.

### Owner and profile

```text
POST       /api/session
DELETE     /api/session
GET/PUT    /api/me/profile
GET/POST   /api/me/resume-versions
GET/POST   /api/me/evidence
PATCH      /api/me/evidence/{id}
GET/POST   /api/career-tracks
PATCH      /api/career-tracks/{id}
```

### Searches, scans, and inbox

```text
GET/POST   /api/saved-searches
GET/PATCH/DELETE /api/saved-searches/{id}
POST       /api/saved-searches/{id}/scans
GET        /api/scans/{id}
GET        /api/today
GET        /api/opportunities/{id}
POST       /api/opportunities/{id}/decision
POST       /api/opportunities/{id}/assessment
```

`POST /decision` supports `pursue`, `watch`, `dismiss`, and an evidence-friendly reason. `pursue` atomically creates or returns the existing application, first activity event, and initial action.

### Applications and actions

```text
GET/POST   /api/applications
GET/PATCH  /api/applications/{id}
GET        /api/applications/{id}/activity
POST       /api/applications/{id}/notes
GET/POST   /api/action-items
PATCH      /api/action-items/{id}
POST       /api/applications/{id}/pursuit-pack
```

### Contacts and outreach

```text
POST       /api/applications/{id}/contact-searches
GET        /api/contact-searches/{id}
GET        /api/applications/{id}/contacts
PATCH      /api/application-contacts/{id}
POST       /api/application-contacts/{id}/outreach/draft
PATCH      /api/outreach/{id}
POST       /api/outreach/{id}/mark-sent
POST       /api/outreach/{id}/events
POST       /api/applications/{id}/outreach/stop
```

The contact-search response includes `target_count`, `verified_count`, `coverage_status: met | partial`, `exhausted`, and structured shortfall reasons.

### Health

```text
GET /health       # HTTP liveness only
GET /ready        # DB connectivity, migrations current, recent worker heartbeat
GET /api/health   # owner-visible scan success, scheduler lag, queue/dead-letter summary
```

Keep `/api/hunt` and `/api/runs/*` behind `LEGACY_HUNT_API=1`, add deprecation headers, and remove them only after the new UI and optional import path are proven.

## 7. Frontend information architecture

```text
/
  -> /onboarding until the minimum profile exists
  -> /today afterwards

/onboarding
/today
/searches
/searches/new
/searches/[searchId]
/jobs/[jobId]
/applications
/applications/[applicationId]
/actions
/settings/profile
/settings/resumes
/settings/evidence
/scans/[scanId]
/privacy
```

Keep `/runs/[runId]` temporarily as a legacy diagnostic page.

### 7.1 Today

The default screen shows:

- New roles needing a decision
- Due/overdue next actions
- Changed or closing roles
- Active applications with latest activity
- Last successful scan, next scheduled scan, and degraded-source warnings

An opportunity card shows:

- Company, title, location, posting freshness, and first-party source status
- Target track and `reach/core/hedge` lane
- Eligibility band with two strongest evidence matches and important gaps
- Career-value band and contributing priorities
- Verified/inferred/unknown status for compensation, date, location, and employment type
- Why acting now is recommended
- `Pursue`, `Watch`, `Dismiss`, `Hide company`, and `Open` actions

Filters live in URL search parameters. Dismiss asks for a reason, updates optimistically, and offers Undo.

### 7.2 Job review and application dossier

The pre-pursuit job page contains source facts, preserved JD, career value, eligibility, gaps, unknowns, provenance, and first-seen/change history. Expensive contact and pack work has not run yet.

After `Pursue`, the application dossier has:

- **Overview:** stage, posting state, important dates, career value, next action
- **Fit & evidence:** requirement-to-approved-achievement mapping and gaps
- **Application pack:** tailored resume version, diff, answers, and claim provenance
- **People:** five-person coverage, rankings, evidence, waves, messages, and outcomes
- **Activity:** immutable decisions, notes, sends, replies, application changes, and interviews

Application stages:

```text
pursuing -> ready_to_apply -> applied -> screening -> interviewing -> offer -> closed
```

Closed requires an outcome such as rejected, withdrawn, declined, accepted, or posting closed. Every nonterminal application must show one current next action and due date.

### 7.3 Contact bench UI

Replace the current stack of immediately editable drafts with a coverage view:

```text
Contacts: 5/5 verified

1. Anika — likely team peer        Ready now
2. Rahul — relevant recruiter      Ready now; different ask
3. Meera — adjacent team           Unlocks Monday
4. Devika — hiring manager         Reserve
5. Nikhil — likely team peer       Reserve
```

Each card shows category, why this person, evidence and checked date, team-proximity status, rank explanation, outreach state, next allowed action, and collision/cooldown warnings.

Actions include `Verify`, `Draft`, `Copy and mark sent`, `Snooze`, `Log reply`, `Skip`, and `Stop outreach`. Draft edits autosave and visibly report `Saving`, `Saved`, or `Failed`; failed saves preserve the local text.

### 7.4 Frontend foundation

- Generate transport types from FastAPI OpenAPI with `openapi-typescript`; keep UI view models separate.
- Add Zod validation for critical responses.
- Add TanStack Query for caching, bounded GET retry, polling, invalidation, and mutation state.
- Add React Hook Form + Zod for long persisted forms.
- Add Vitest, Testing Library, and MSW for component/contract tests.
- Add Playwright and axe for end-to-end and accessibility gates.
- Preserve the current form-label, clipboard, source-badge, and privacy patterns where useful.
- Replace elapsed-time progress guesses with real server stage/status.
- Retain last good data with a stale warning during temporary polling/network failures.

Target WCAG 2.2 AA. All pipeline transitions need keyboard-accessible menus; drag-and-drop cannot be the only control. Mobile uses grouped application lists rather than a horizontally scrolling Kanban board.

## 8. Vertical implementation slices

Each slice must leave the repository deployable and keep legacy hunt tests passing until the legacy route is removed.

### Slice 0 — Durable foundation and contracts

**Goal:** the product can safely persist personal workflow data and run background work after restarts.

Backend tasks:

- [x] Add SQLAlchemy, psycopg, and Alembic dependencies.
- [x] Add `database.py`, Alembic config, initial schema migration, and transaction boundaries for the foundation models.
- [x] Add owner/session authentication and owner-scoping tests.
- [x] Encrypt persistent resume, result, and outcome fields using the existing keyring.
- [x] Add generic `background_jobs` and `worker_heartbeats` while preserving lease/retry semantics.
- [x] Dispatch `legacy_hunt` through the generic worker.
- [x] Add scheduler tick and deterministic owner-scoped job dedupe.
- [x] Add capability-aware `/ready` and owner-visible operational health.
- [x] Add Docker Compose for Postgres, web, worker, and frontend development.

Frontend tasks:

- [x] Add the authenticated app shell and bounded same-origin API proxy.
- [x] Generate and consume API types and fail CI on schema drift.
- [ ] Add query/form/test foundations and standard problem handling.
- [ ] Handle loading, empty, offline, 401, 403, 409, 422, 429, and 500 states.

Definition of done:

- [x] Restarting the database client/worker loses no data or queued work.
- [x] Two concurrent workers claim different jobs in the mandatory Postgres gate.
- [x] Two scheduler ticks create one job for the same scheduled slot.
- [x] Another owner/session cannot read or mutate any practical hunt resource.
- [x] Sensitive hunt plaintext is absent from durable mapped fields and failure logs; private draft tracing remains disabled.
- [x] `/ready` fails when migrations are behind, the worker heartbeat is stale,
  or active work has no fresh compatible worker.

Current checkpoint: steps 0A–0F are implemented and hermetically verified.
Practical mode now uses Postgres exclusively for hunt requests, jobs, encrypted
results, and encrypted outcomes. SQLite remains only behind the explicit
`ENABLE_PRACTICAL_MODE=0` development compatibility path. The first durable
profile/search forms now include standard problem handling and explicit
loading, empty, validation, conflict, and retry states; broader query/form/test
framework adoption can proceed incrementally with later product surfaces.

### Slice 1 — Profile, evidence, and saved searches

**Goal:** the user describes what a better job means once, and the product remembers it.

Backend tasks:

- [x] Add profile, career-track, resume-version, achievement-evidence, and saved-search models/services/routes.
- [ ] Parse a resume into suggested evidence, but require explicit approval before reuse.
- [x] Support multiple target tracks and seniority levels.
- [ ] Separate hard exclusions, ranking preferences, and include-but-flag-unknown policies.
- [x] Validate compensation semantics.
- [ ] Migrate legacy criteria into saved searches.
- [x] Calculate timezone-correct `next_scan_at` values.

Frontend tasks:

- [x] Build resumable onboarding: resume, career target, evidence approval, first saved search.
- [x] Build profile/evidence editors and saved-search CRUD.
- [ ] Make hard versus soft versus unknown policy visible for every relevant filter.

Definition of done:

- Refreshing any onboarding step loses no accepted data.
- The owner can create two distinct career tracks and scheduled searches.
- Contradictory compensation values fail inline and server-side.
- No generated achievement is usable until approved.
- Search schedule and next run are visible and timezone-correct.

Phase 1A checkpoint: the practical remembered-workflow core is shipped and
provider-free. Resume suggestion parsing, legacy-criteria import, and the
hard/soft/unknown filter redesign remain explicit follow-up work; no UI claims
those semantics exist yet. The persisted profile/search foundation is ready for
Slice 2 opportunity ingestion and deduplication.

### Slice 2 — Opportunity radar and Today inbox

**Goal:** scans create a high-signal, deduplicated daily decision queue.

Backend tasks:

- [ ] Add stable native IDs to `Role` and every adapter.
- [ ] Return source fetch metadata including success and complete-inventory status.
- [ ] Separate broad ingestion from hard filtering in the source resolver.
- [ ] Add posting/version/alias/observation persistence and lifecycle rules.
- [ ] Add scan orchestration, finalization, match creation, and owner-level opportunity dedupe.
- [ ] Add versioned four-part assessment and `/api/today` projection.
- [ ] Store unknown metadata with confidence rather than dropping it by default.

Frontend tasks:

- [ ] Build Today summary, opportunity cards, filters, scan status, and degraded-source state.
- [ ] Build job-review page with preserved JD, assessments, evidence, gaps, and provenance.
- [ ] Add `Pursue`, `Watch`, `Dismiss`, hide-company, reason capture, and Undo.

Definition of done:

- Repeating an unchanged scan creates zero new opportunities.
- The same posting found by two searches appears once in Today with both provenances.
- A changed title/JD updates the existing posting and emits a change event.
- A source failure never closes a posting.
- Two complete authoritative omissions close it; a later reappearance reopens it.
- Unknown salary/date/type is visibly unknown and follows the chosen policy.
- At least 95% of surfaced apply links pass the first-party URL check in the live QA pack.
- The Today page makes no provider calls and keeps last good data through a refresh failure.

### Slice 3 — Application pipeline and next actions

**Goal:** every pursued role becomes a controlled process rather than a forgotten bookmark.

Backend tasks:

- [ ] Make `Pursue` transactionally create one application, event, and initial next action.
- [ ] Add application stages, required transition fields, notes, immutable events, and action items.
- [ ] Cancel or replace irrelevant actions on stage changes.
- [ ] Require applied date and resume version at `applied`; require outcome at `closed`.

Frontend tasks:

- [ ] Build application dossier shell, pipeline list/board, and activity timeline.
- [ ] Build the unified action center and Today action integration.
- [ ] Add accessible stage menus and required transition dialogs.
- [ ] Build a grouped, non-horizontal mobile pipeline.

Definition of done:

- Repeated/double `Pursue` creates exactly one application.
- Every nonterminal application has one visible current next action and due date.
- Applying records date and exact resume version.
- Closing records an outcome and cancels irrelevant actions.
- Completing or snoozing an action updates Today, pipeline, and dossier.
- All stage transitions work with keyboard only.

### Slice 4 — Five-person contact bench and staged outreach

**Goal:** each pursued role gets credible referral coverage without mass messaging.

Backend tasks:

- [ ] Split referral work into `discover_contacts(candidate_limit=12)` and `select_contact_bench(target_count=5)`.
- [ ] Preserve employer evidence excerpt/URL/time, team relevance, category, relationship status, and score components.
- [ ] Normalize profile URLs and enforce owner/company/person cooldowns.
- [ ] Search separately for team peers, leaders, recruiters, and owner-provided warm paths.
- [ ] Persist contact plan, application contacts, exact message versions, sends, and events.
- [ ] Enforce wave unlocks, one follow-up, stop conditions, and mutation idempotency server-side.
- [ ] Migrate legacy `draft_id` and outcome rows into attempts/events when importing old data.
- [ ] Update learning retrieval to use the exact persisted message marked sent.

Frontend tasks:

- [ ] Build contact-search progress and honest `N/5 verified` coverage.
- [ ] Build ranked evidence-rich contact cards and wave/cooldown states.
- [ ] Build persistent message composer, copy-and-mark-sent, follow-up, outcome, and stop controls.
- [ ] Show partial coverage and structured exhaustion reasons without placeholders.

Definition of done:

- A fixture with enough evidence produces five distinct verified candidates from a larger pool.
- A fixture with only three valid candidates shows `3/5` and never pads.
- The selected five are category-diverse unless the evidence pool makes that impossible.
- Only the allowed first wave is active; contacts four and five remain reserves.
- An edited sent message survives reload and is the text used for outcome learning.
- Mark-sent is idempotent and creates one dated follow-up.
- A useful reply pauses later waves; a referral/introduction stops them.
- Role closure and terminal application states cancel pending outreach actions.
- Nothing sends automatically.

### Slice 5 — Grounded application pack

**Goal:** reduce the time from `Pursue` to a truthful, high-quality application.

Backend tasks:

- [ ] Extract required/preferred JD requirements with source spans.
- [ ] Map requirements only to approved achievement evidence.
- [ ] Generate a tailored resume variant, concise application answers, and a company-specific note.
- [ ] Store input versions, generated artifact versions, claim provenance, errors, and approvals.
- [ ] Reject or flag any generated claim without approved evidence.

Frontend tasks:

- [ ] Build requirement-to-evidence table and unsupported-gap view.
- [ ] Show exact base-versus-tailored resume diff.
- [ ] Let the owner edit, approve, reject, copy, and select the version actually used.
- [ ] Keep the first-party apply link and a short application checklist visible.

Definition of done:

- Every nontrivial factual claim links to approved evidence or is blocked.
- Regeneration never silently overwrites an approved version.
- The user can move from Pursue to a ready, reviewed pack in under ten minutes in the pilot test.
- The applied transition records the exact pack/resume version used.

### Slice 6 — Outcome learning, weekly review, and hardening

**Goal:** learn what produces interviews and make the workflow trustworthy over weeks.

- [ ] Add weekly funnel and next-action review.
- [ ] Measure results by career track, source, assessment band, contact type, and sequence position.
- [ ] Show the incremental value of contacts two through five.
- [ ] Add recruiter-screen/interview preparation actions and evidence-backed story prompts.
- [ ] Add privacy export/delete and retention controls.
- [ ] Complete legacy import/deprecation, backup/restore drill, deployment smoke tests, and runbooks.
- [ ] Add cross-browser, mobile, accessibility, concurrency, restart, source-failure, and migration gates.

Definition of done:

- Funnel metrics derive from immutable events and exact sent/application versions.
- Weekly review identifies stale applications and requires a next decision.
- Backup restore reproduces profiles, searches, opportunities, applications, contacts, messages, and actions.
- Production survives web/worker restart and a temporary source outage without duplicating or losing work.

## 9. Repository change map

### Modify

- `requirements.txt`: SQLAlchemy, Alembic, psycopg, and test dependencies
- `job_hunt_agent/schemas.py`: stable job identity, evidence-rich contacts, new transport contracts
- `job_hunt_agent/sources/base.py`: structured fetch result and completeness metadata
- `job_hunt_agent/sources/*.py`: native IDs and broad evidence preservation
- `job_hunt_agent/sources/resolver.py`: ingestion/filter split and stable dedupe
- `job_hunt_agent/tools/referrals.py`: configurable 10–12 candidate discovery and five-person selection
- `job_hunt_agent/run.py`: legacy wrapper only; remove eager behavior from the new flow
- `job_hunt_agent/persistence.py`: transitional legacy facade; do not grow it with new domain logic
- `job_hunt_agent/worker.py`: generic dispatch, scheduler tick, readiness heartbeat
- `job_hunt_agent/api.py`: app composition and legacy router mounting
- `frontend/app/*`: new information architecture and legacy redirect/diagnostics
- `frontend/components/*`: split run-centric cards into opportunity, application, contact, and action components
- `frontend/lib/api.ts`: session, typed errors, idempotency, version conflicts, abort/retry behavior
- `frontend/lib/types.ts`: replace handwritten transport copies with generated types
- `render.yaml`: durable database plus deployed web and worker topology, or replace with the chosen equivalent
- `.env.example`, `README.md`, `frontend/README.md`: practical runtime and privacy documentation

### Add

```text
alembic.ini
migrations/
docker-compose.yml
job_hunt_agent/database.py
job_hunt_agent/models/
job_hunt_agent/repositories/
job_hunt_agent/services/
job_hunt_agent/routers/
job_hunt_agent/job_queue.py
job_hunt_agent/auth.py
job_hunt_agent/scheduler.py
scripts/import_legacy_sqlite.py
frontend/features/
frontend/components/ui/
frontend/components/jobs/
frontend/components/applications/
frontend/components/contacts/
frontend/components/actions/
frontend/tests/
frontend/e2e/
```

## 10. Test and QA strategy

### Backend unit and integration gates

- Job identity: same native ID survives title/URL changes; different requisition IDs remain distinct; tracking URLs dedupe.
- Scans: identical repeats add no new opportunity; concurrent scheduler ticks create one scan.
- Lifecycle: source failure does not close; two successful complete omissions do; reappearance reopens.
- Queue: concurrent workers do not double-claim; retries are idempotent; expired leases recover.
- Criteria: hard, soft, and unknown policies behave distinctly; legacy criteria translate truthfully.
- Assessment: outputs include component evidence, gaps, unknowns, and version lineage.
- Applications: pursue/stage/action transitions are transactional and idempotent.
- Contacts: five verified and diverse people when available; honest partial result otherwise; no duplicates or former employees.
- Outreach: cooldowns, maximum waves, one follow-up, exact sent-text persistence, and stop-after-success.
- Privacy: owner isolation, encryption, no sensitive logs/traces, session expiry, export, and delete.

Use hermetic source fixtures in CI. Keep live registry/source verification as an explicit scheduled or pre-release suite so normal tests are deterministic.

### Frontend gates

- Component/contract tests for every loading, empty, partial, stale, conflict, and error state.
- E2E at 320px, 390px, 768px, 1024px, and 1440px.
- Chromium, WebKit, and Firefox for the core path.
- axe and keyboard-only checks for onboarding, Today, dossier, contact composer, and application transitions.
- No horizontal document overflow, no console errors, and no lost local draft after an autosave failure.

### Required end-to-end path

```text
complete onboarding
  -> create scheduled search
  -> run scan
  -> see deduplicated Today inbox
  -> pursue one role
  -> receive exactly one application + next action
  -> discover five verified contacts
  -> edit and mark first message sent
  -> enforce cooldown
  -> unlock next eligible contact
  -> log introduction
  -> stop remaining outreach
  -> apply with recorded resume version
  -> advance application and record outcome
```

Also run the same path with only three verifiable contacts; it must complete successfully with an honest shortfall.

## 11. Delivery order and release gates

Keep changes reviewable in this order:

1. **PR 1 — Database/auth/queue foundation:** migrations, owner session, generic jobs, worker readiness, Compose.
2. **PR 2 — Profile and saved searches:** persistent onboarding, evidence approval, criteria semantics.
3. **PR 3 — Scan ingestion and identity:** adapters, versions, observations, lifecycle, scheduler.
4. **PR 4 — Today and decisions:** assessments, projection endpoint, inbox/job pages, pursue/watch/dismiss.
5. **PR 5 — Applications and actions:** pipeline, next-action invariant, activity timeline.
6. **PR 6 — Contact backend:** evidence-rich discovery, five-person selection, waves, outreach persistence.
7. **PR 7 — Contact frontend:** bench, composer, sends, follow-ups, outcomes, stop behavior.
8. **PR 8 — Application pack:** evidence mapping, resume diff, grounded answers, approvals.
9. **PR 9 — Learning and production gate:** weekly review, funnel metrics, import, backups, full E2E.

Release gates:

- **R1 — Daily radar:** slices 0–3. Useful for finding, deciding, and tracking roles every day.
- **R2 — Referral workflow:** slice 4. Useful for systematic, non-spammy warm outreach with at least five verified candidates when available.
- **R3 — Application quality:** slices 5–6. Useful for faster grounded applications, interview preparation, and outcome-based improvement.

Do not begin the application-pack generator before R1 data identity, decisions, and application states are stable. Otherwise generated assets will attach to disposable run blobs again.

## 12. Pilot success metrics

Measure these over the first 30 days:

### Supply and relevance

- At least 70% of the top ten surfaced roles are worth opening or considering.
- Zero duplicate Today cards for the same requisition.
- At least 95% of surfaced apply links are valid first-party destinations.
- Unknown metadata is visible; no unsupported salary or date is presented as verified.

### User effort and execution

- Daily inbox review takes under 15 minutes.
- At least 80% of high-priority roles receive a decision within 24 hours.
- Every active application has a dated next action.
- Pursue-to-reviewed-application-pack time is under 10 minutes once R3 ships.

### Contacts and outcomes

- Every pursued role has five verified contacts or an evidence-backed shortfall.
- Track percentage of roles with any reply, useful reply, introduction/referral, and recruiter screen.
- Track response and negative/opt-out rates by contact category and sequence position.
- Track average contacts used before success and the incremental value of contacts two through five.
- Track application -> screen -> interview -> offer conversion by source and assessment band.

### Trust

- Zero fabricated resume claims, relationships, employer statuses, apply links, or compensation facts.
- Zero automatic external sends or applications.
- Zero loss of accepted user data across normal restarts and deploys.

## 13. First implementation checkpoint

The first meaningful checkpoint is not a redesigned homepage. It is this backend-level invariant:

> Running the same saved search twice creates no duplicate opportunity; a pursued role creates exactly one application with a next action; and contact discovery returns five verified people when five exist while allowing no more than the configured outreach wave to be active.

Once that passes integration tests, build the Today and contact UI on top of it. That sequence turns the current impressive demo into a product the owner can trust throughout a real job search.
