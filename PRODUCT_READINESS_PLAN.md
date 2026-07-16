# Product Readiness Plan

## Objective

Turn Job Hunt Signal into a dependable product that helps a candidate:

1. find fresh, trustworthy jobs worth reviewing;
2. understand which roles they can credibly win;
3. distinguish merely possible roles from genuinely better career moves;
4. take the right next action without losing track of applications; and
5. find an evidence-backed referral bench without inventing relationships.

Readiness is determined by measured user outcomes and live production behavior,
not feature count or test count.

## Release discipline

A phase is not complete until all of the following are true:

- its automated tests pass;
- its production configuration is checked in;
- the deployed web and required worker capabilities report healthy;
- a real hosted acceptance path completes from the public Vercel origin;
- failures are visible and actionable to the user; and
- the phase's measurable exit gates are recorded.

Code-complete without a deployed canary is incomplete.

## Current truth

The repository already has a strong persistence, privacy, deduplication,
application-tracking, outreach-safety, and failure-recovery foundation.
However, the practical search loop is not yet product-ready:

- production has a healthy web/database process but no active worker;
- practical scans cover one curated 20-company backend/India pack;
- practical scans do not assess roles against the pinned resume or priorities;
- Today is ordered by surfacing time rather than opportunity value;
- unknown posting dates and employment types can silently remove useful roles;
- compensation preferences are stored but not applied;
- automatic scheduled scans are not connected;
- onboarding requires substantial manual entry; and
- the single owner access key is not a multi-user account system.

## Product metrics

- **Precision@10:** top-ten roles labelled worth opening or considering divided
  by ten.
- **Coverage recall:** relevant roles ingested divided by relevant roles found
  during a manual audit of the same supported company boards.
- **Valid-link rate:** surfaced roles whose apply URL is a live, correct
  first-party destination.
- **Scan SLA:** time from a scan request or scheduled slot to a terminal,
  persisted result.
- **Activation:** profile, resume, career target, first saved search, and first
  successful scan completed.
- **Five-contact coverage:** pursued roles for which five unique people pass the
  public-evidence and current-employer verification floor.
- **Useful-week retention:** activated users who complete useful job-search work
  in at least three of four pilot weeks.

## Phase 0 — Restore the real production loop

**Effort:** 3–5 engineering days.

### Work

- Start with a scan-only worker embedded in the existing free web service. This
  processes user-triggered scans while the service is awake without requiring a
  second paid service.
- Preserve a clean capability boundary so the same worker can move to a
  continuously running background service when private-beta load requires it.
- Make worker capabilities explicit so first-party scans do not require Gemini,
  Phoenix, or SerpAPI credentials.
- Advertise only capabilities the live worker can actually execute.
- Reject or disable scans when no fresh scan-capable worker exists.
- Add actionable worker-unavailable and stale-queue messaging.
- Fix unknown-date and unknown-employment-type filtering.
- Reject meaningless blank onboarding profiles.
- Run a real saved-search → scan → Today → Pursue production canary.

### Exit gates

- Vercel login and all private reads succeed.
- `/web-ready` and the scan capability both report healthy.
- A real 20-company scan reaches a terminal state within ten minutes.
- At least one trusted first-party role reaches Today when matching supply exists.
- Pursue creates exactly one application and one dated next action.
- No scan can remain silently queued because no compatible worker exists.
- A failed source is visible and does not erase last-known-good roles.

### Free-tier operating constraint

The free Render web service may sleep after inactivity. Interactive scans are
supported because the user's request wakes the web and embedded scan worker
together. Automatic unattended schedules require a wake mechanism such as a
public-repository GitHub Actions schedule, or a future always-on worker.
Cold-start delay is acceptable for the personal/private prototype but is not a
strong-beta architecture.

## Phase 1 — Make job supply dependable and broad enough

**Effort:** 2–3 person-weeks.

### Work

- Separate broad ingestion from hard filtering.
- Apply explicit show/hide policies for unknown date, employment type, and pay.
- Apply compensation preferences only to verified compensation.
- Let the owner maintain company targets without editing repository YAML.
- Add additional first-party ATS coverage and carefully controlled broad
  discovery that resolves to verified employer apply URLs.
- Connect daily, weekday, and weekly schedules.
- Add retries with backoff, source-health history, queue alerts, and canaries.
- Build a labelled benchmark of at least 150 real postings across at least
  three representative searches.

### Exit gates

- Coverage recall is at least 90% across the audited supported-company sample.
- Valid first-party apply-link rate is at least 98%.
- Duplicate opportunity rate is below 1%.
- Unknown metadata never silently becomes a hard exclusion.
- At least 95% of scheduled scans start within five minutes.
- A 20-company scan completes within ten minutes at P95.
- Every ranking change is evaluated against the same versioned benchmark.

## Phase 2 — Add explainable opportunity assessment

**Effort:** 3–4 person-weeks.

### Work

Create versioned assessments bound to the exact resume, career target, approved
evidence, and posting version:

1. **Eligibility:** hard requirements, authorization, location, level, and
   explicitly unknown requirements.
2. **Career value:** compensation, scope, learning, company quality, and
   flexibility using the candidate's priorities.
3. **Evidence confidence:** what the resume and approved achievements actually
   support, with gaps kept as gaps.
4. **Action priority:** an explainable decision that combines the other
   assessments without hiding uncertainty.

Use these assessments to rank Today and assign reach, core, and hedge lanes.

### Exit gates

- Precision@10 is at least 70% for the private beta.
- False hard-exclusion rate is below 5% on the benchmark.
- Every assessed role exposes evidence, gaps, and unknowns.
- The same versioned inputs produce deterministic results.
- No unsupported salary, qualification, or career-value claim is shown as fact.
- Today is ranked by action priority instead of discovery time.

## Phase 3 — Make the daily workflow fast

**Effort:** 2–3 person-weeks.

### Work

- Replace the long first-run form with guided resume import and progressive
  onboarding.
- Start the first useful scan within the onboarding flow.
- Make saved searches, Today review, pursuit, application preparation, and
  outreach feel like one coherent workflow.
- Improve the five-person bench with same-team evidence, warm-path inputs, and
  visible reserves.
- Produce editable, evidence-grounded application materials with ATS-ready
  document export.
- Add notifications and a concise weekly execution loop.

### Exit gates

- At least 80% of testers activate without operator help.
- Median onboarding time is at most ten minutes; P90 is at most twenty.
- The first useful scan begins within fifteen minutes of signup.
- Median daily opportunity review takes at most fifteen minutes.
- Every active application has exactly one dated next action.
- At least 70% of pursued roles produce five verified contacts.
- At least 90% produce three verified contacts.
- Every contact shortfall is explicit; no bench is padded.

## Phase 4 — Private beta

**Engineering effort:** about two person-weeks.

**Elapsed pilot:** three to four weeks with 5–15 invited users.

### Exit gates

- At least five users activate and record at least 200 real role decisions.
- Phase 1–3 metrics hold for two consecutive weeks.
- At least 60% of activated users are useful-week retained.
- Median reported usefulness is at least 4/5.
- Unexpected API error rate is below 2%.
- No data loss, cross-owner exposure, automatic application, or automatic send.
- No unresolved severity-1 or severity-2 defect.

**Cumulative private-beta effort:** approximately 10–14 person-weeks.

## Strong beta

**Target:** 50–150 users.

**Additional effort:** 18–28 person-weeks.

Add multi-user authentication and recovery, administration, quota and cost
controls, at least 100 maintained company boards per declared segment,
segment-aware ranking, automated Chromium/Firefox/WebKit acceptance,
operational dashboards, and a formal security/privacy review.

### Exit gates

- Precision@10 reaches at least 80% in every supported segment with enough labels.
- Coverage recall remains at least 90% on audited first-party boards.
- Scheduled-scan success is at least 98%.
- Activation without assistance is at least 85%.
- Four-week retained usage is at least 65%.
- Provider cost per active user stays below a declared sustainable target.

**Cumulative strong-beta effort:** approximately 28–42 person-weeks.

## Broadly usable product

**Target:** 500–5,000 users across explicitly supported roles and regions.

**Additional effort:** 35–60 person-weeks.

Add a maintainable source platform covering hundreds of company boards,
multiple role/region segments, semantic skill and experience reasoning,
self-service accounts and billing, scalable queues and caching, disaster
recovery, provider failover, notifications, integrations, support tooling, and
formal accessibility/security/privacy release processes.

### Exit gates

- Supported-market coverage recall is at least 85%.
- Precision@10 remains at least 80% per major segment.
- Apply-link validity is at least 99%.
- Scheduled-scan SLA attainment is at least 99%.
- API uptime is at least 99.9%.
- Export, deletion, recovery, and incident drills pass.
- Sensitive operations pass independent security review.

**Cumulative broadly-usable effort:** approximately 65–100 person-weeks.

## Known risks

- ATS structures and job-source APIs change without notice.
- LinkedIn-style scraping adds policy, account, and reliability risk.
- Recommendation quality requires real user labels; engineering cannot replace
  pilot time.
- Five verified contacts will not exist for every role.
- Provider, database, worker, and observability costs require explicit budgets.
- Multi-user authentication materially expands the security boundary.
- Hiring outcomes arrive slowly, so early releases must optimize verified
  relevance and execution quality without claiming causal hiring improvement.
