# Job Hunt Signal QA and product-fit report

Date: 2026-07-10  
Branch: `v2-rebuild`  
Mode: report-only; no application source files changed

## Verdict

Job Hunt Signal is a strong local prototype with unusually careful source verification, privacy boundaries, queue semantics, and honest empty-result behavior. It already proves the complete workflow from resume input to ranked first-party roles, verified referral leads, editable outreach, and outcome logging.

It is not yet a dependable daily job-search product.

- Local implementation quality: **8.5/10**
- Usefulness for a real personal job search today: **6/10**
- Checked-in production readiness: **4/10**

The main blockers are not code cleanliness. They are product supply and deployment: default live filters returned only 2 roles from 1 company, and the Render blueprint deploys no worker to process queued hunts.

## What the product currently does

1. Accepts a plain-text resume and search criteria: keywords, a single seniority band, locations, employment types, and compensation bounds.
2. Searches a curated `backend_india` pack of 20 companies through first-party Greenhouse, Lever, Workday, SmartRecruiters, Amazon, and other adapters.
3. Rejects stale, unknown-employment, wrong-location, weak-source, duplicate, and non-first-party results rather than padding output.
4. Scores and ranks roles against resume/JD token overlap and quotes a matching job-description requirement.
5. Searches public LinkedIn/GitHub result snippets for up to three relevant employees per role.
6. Requires visible current-employer evidence and at least 0.5 referral confidence before producing outreach.
7. Drafts short messages from a bounded role-relevant resume excerpt, optionally using successful past drafts as examples.
8. Scores drafts on personalization, specificity, ask, and tone.
9. Queues encrypted requests for a separate worker with leases, retries, cancellation, dead-letter state, and idempotent submission.
10. Protects private runs with a one-time browser-session capability, supports deletion, and retains outcomes as an append-only log.

## Evidence collected

### Automated checks

- Python: **404 passed, 2 skipped, 16 subtests passed**
- Frontend ESLint: passed
- Next.js production build and TypeScript: passed
- Browser console: 0 warnings, 0 errors

The first production-build attempt failed because the sandbox blocked an internal port. The same build passed outside that restriction; this was not a product defect.

### Browser flows

Passed:

- Empty-resume validation
- Provider-consent validation
- Hunt submission and queued state
- Worker completion
- Review of 3 mock roles and 9 mock referral drafts
- Draft editing and clipboard copy
- Outcome save and persistence after reload
- Queued-run cancellation
- Completed-run deletion
- Denial of private-run access in a fresh tab
- Privacy disclosure page

Observed twice:

- A transient poll failure replaced the run page with a terminal `Failed to fetch` screen. Reload recovered the successful/queued run.

Mobile visual verification was attempted, but the Codex in-app browser did not apply its viewport override. Responsive Tailwind breakpoints are present in source, but a real 390×844 visual pass remains required.

### Live source verification

Strict registry check:

- Configured: 20
- Verified: 20
- Unverified: 0
- Dead: 0

Default UI supply check (`junior`, India/Remote-India/Bengaluru/Hyderabad, full-time, 45 days):

- 2 qualifying first-party roles
- 1 company
- Failed target: 10 roles across 5 companies

Senior supply check with otherwise identical filters:

- 8 qualifying first-party roles
- 3 companies
- Failed target: 10 roles across 5 companies

## Findings

### Critical: checked-in production deployment has no worker

`POST /api/hunt` now queues work for a separate worker, but `render.yaml` defines only a web service and the Docker command launches only Uvicorn. Unless a worker is provisioned outside this repository, hosted runs remain queued forever. The same deployment uses `/tmp/outcomes.db`, so restarts and deploys erase queue state, results, and outcomes.

User impact: the local demo works, but the public deployment cannot reliably complete or retain a real hunt.

### High: compensation inputs do not affect search results

The UI collects `comp_min_lpa` and `comp_max_lpa`, and the API accepts them, but the resolver and source adapters never apply either field. The browser accepted a minimum of 40 LPA and maximum of 20 LPA, queued the run, and returned results.

User impact: users may reject or trust roles based on a filter that currently does nothing.

### High: live result supply is below the product's own usefulness gate

All 20 sources are healthy, but the default search returned only 2 qualifying roles from 1 company. The senior variant returned 8 roles from 3 companies. The current strict combination of title keywords, one seniority band, locations, full-time evidence, and 45-day freshness is too narrow for dependable daily use.

User impact: a user can complete the whole flow and still receive too little opportunity coverage.

### Medium: one temporary network error permanently stops polling

The review page converts any polling error into a terminal error screen and does not retry or offer `Reconnect`. This reproduced twice during new-hunt navigation. Reload recovered the run because the backend had continued successfully.

User impact: a harmless Wi-Fi or backend blip during a 60–120 second hunt looks like a failed hunt.

### Medium: edited outreach is not persisted

Draft textareas are editable and copy correctly, but edits live only in React component state. Reloading or revisiting the review restores the generated version.

User impact: a carefully personalized message can be lost before it is sent or logged.

### Medium: outcome history is not human-identifiable

The outcome form identifies each draft while entering an outcome, but the `Previously logged` list later shows only outcome, notes, an eight-character draft ID, and timestamp. It omits the person, company, and role.

User impact: after several contacts, the history cannot answer “who replied?” without manually matching opaque IDs.

### Medium: it is a hunt generator, not yet a job-search workspace

There are no saved searches, scheduled refreshes, new-role alerts, cross-run deduplication, bookmarks, application status, interview/offer tracking, follow-up reminders, or CSV export. Outcomes cover referral messages only.

User impact: the product helps generate one batch, but does not manage the multi-week search that follows.

### Low: privacy copy is stale

The privacy page says encrypted requests are removed after “synchronous processing,” while the product now uses an asynchronous queue and worker.

### Low: the default seniority conflicts with the included example

The frontend defaults to `junior`, while the included sample resume describes a senior engineer with 3.5 years of experience and explicitly seeks senior roles. This makes first-run results look weaker than the product can produce.

## What is especially good

- The 20-company registry is genuinely live and verified.
- First-party apply URLs are preferred; weak aggregator fallback is intentionally disabled for curated hunts.
- The product refuses to fabricate roles or people.
- Referral candidates require current-employer evidence.
- Resume-fit explanations quote JD evidence instead of presenting an unexplained score.
- Requests are encrypted while queued and plaintext resume data is cleared after completion.
- Private access tokens stay out of URLs.
- Cancellation, deletion, idempotency, retry bounds, and worker leases are already implemented.
- Outcome-based draft retrieval is a meaningful learning loop, not a cosmetic AI feature.
- The UI is clear, calm, accessible, and usable on desktop.

## Recommended roadmap, in order

1. **Make production capable of completing hunts.** Deploy a worker and durable shared database; add readiness checks that fail if the worker is absent.
2. **Increase opportunity supply without lowering evidence quality.** Allow multiple seniority bands, broaden title synonyms, add more verified company packs, and expose freshness/location strictness as controls.
3. **Turn it into a recurring search.** Save search profiles, refresh on a schedule, detect only new roles, dedupe across runs, and send an opt-in digest.
4. **Make filters truthful.** Implement compensation extraction/filtering where evidence exists; otherwise remove or label the fields as unsupported. Validate min ≤ max.
5. **Make polling resilient.** Retry transient failures with backoff, keep the last known state, and offer an explicit reconnect action.
6. **Build a job pipeline.** Track saved, applied, referral requested, replied, introduced, interview, rejected, offer, and follow-up dates by role/contact.
7. **Persist user edits.** Save customized outreach per draft and show a clear unsaved/saved state.
8. **Improve referral evidence.** Show the exact employer evidence and verification date, let users shortlist contacts, and track contact-specific follow-ups.
9. **Improve matching.** Combine the deterministic evidence scorer with skills/experience/level requirements and explain missing qualifications, not just overlap.
10. **Complete production QA.** Test a deployed real-provider hunt, worker recovery, persistence across redeploys, and true mobile viewports.

## Bottom line

Keep this project. Its strongest idea is not “AI writes referral messages”; many products can do that. Its defensible value is the evidence chain:

`verified first-party role → resume/JD evidence → verified current employee → editable outreach → measured outcome`

That chain is already real. The next leap is to make it continuous, durable, and broad enough that it reliably finds new opportunities every day.
