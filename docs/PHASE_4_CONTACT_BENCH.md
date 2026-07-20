# Phase 4 — Source-backed contact bench

**Status:** Phase 4A through 4D2 are implemented.

## Outcome

Each pursued application can build a durable bench of up to five distinct,
appropriate people from a larger discovery pool. Five is the target, never a
padding requirement: if only three people have sufficient evidence, the
product must persist and display `3/5 source-backed` with structured reasons.

The contact search finds public professional-profile results and preserves the
exact source snippet used for each lead. It does not independently verify a
person's current employment. The 4D workspace prepares grounded drafts,
preserves owner-written message versions, and records explicit manual actions,
but it does **not** send a message.

## Product rules

- Discover up to 12 candidates across team peers, adjacent peers, team
  leaders, recruiters, and explicit owner-provided warm paths.
- Select at most five after evidence checks, deterministic scoring, identity
  deduplication, cooldown checks, and category diversity.
- Require a current-employer signal observed in a named public search result,
  including a bounded excerpt, HTTPS URL, observation time, and heuristic score
  at least `0.75` before a person can enter the bench.
- Preserve the exact evidence and score components used for this application.
- Treat provider failures as incomplete/retryable work, not proof that no more
  suitable people exist.
- Keep reads database-only. Provider work belongs in the durable worker and
  runs outside long database transactions.
- Never send automatically. Outreach remains a manual, per-person workflow.

## Checkpoints

### 4A — Durable foundation

- Persist versioned application contact plans, owner-scoped canonical public
  profile identities, and application-specific evidence snapshots.
- Enforce owner isolation, normalized-profile deduplication, evidence floors,
  unique pool and bench ranks, and honest partial coverage.
- Provide strict database-only contact-bench contracts and reads, including a
  trustworthy not-started state.
- Add pure discovery and selection logic that retains evidence and diagnostic
  completeness while keeping the legacy hunt wrapper compatible.

### 4B — Provider-backed discovery

- Add an idempotent `POST /api/applications/{id}/contact-searches` mutation.
- Queue an explicit `discover_contacts` background job and expose real stored
  progress for polling.
- Fetch public search results through an injected provider adapter, persist the
  10–12 candidate pool, then atomically publish the selected bench.
- Distinguish successful exhaustion, degraded partial results, retryable
  provider failure, configuration failure, and cancelled work.

The worker uses the injected SerpAPI adapter only outside database
transactions, bounds provider data before persistence, and publishes the pool,
five-person bench, evidence snapshots, plan result, and queue completion in one
transaction. Explicit mock mode is deterministic and never constructs the live
provider.

### 4C — Practical dossier experience

- Show `N/5 source-backed`, real progress, evidence links and capture dates,
  category/relevance labels, and structured shortfall reasons.
- Preserve the last good bench during retries and polling failures.
- Offer only profile/evidence review actions in this phase and state clearly
  that nothing has been sent.

The application dossier now starts searches only from an explicit owner action,
polls durable progress with bounded backoff, and retains the last completed
bench through queued refreshes, provider failures, and polling interruptions.
Each card exposes the public profile, preserved employer evidence, evidence
confidence, relevance category, and any lifecycle restriction without exposing
internal ranking components as candidate quality.

### 4D — Manual staged outreach

#### 4D1 — Durable safety boundary

- Pin one completed contact plan to one owner-scoped outreach sequence.
- Unlock up to five eligible, distinct leads together, preferring warm paths,
  engineering peers, likely team leaders, and recruiters when available.
- Persist immutable encrypted message revisions. Copying and manually marking
  one exact version sent are separate idempotent events.
- Persist a timezone-correct five-business-day follow-up date, allow at most
  one initial send and one follow-up per person, and reject premature follow-up
  or no-reply actions.
- Enforce 30-day person cooldowns and at most three cold employee contacts for
  one company in a rolling seven-day window before releasing text for copy,
  then recheck those limits when the owner records the exact version as sent.
- Pause the sequence after a useful reply; stop it after an introduction,
  referral, do-not-contact request, or explicit owner stop. Every later
  mutation rechecks posting/application state and stops instead of acting when
  the role is no longer active.
- Keep every read database-only. There is no model call, provider call,
  clipboard action, messaging integration, timer-driven transition, or send.

#### 4D2 — Practical composer and controls

- Add the persistent message editor, copy-and-mark-sent confirmation,
  follow-up due state, outcome controls, pause/resume, and stop actions to the
  application dossier.
- Keep Clipboard success separate from the server-side manual-send assertion.
- Reconcile ambiguous mutations with the same idempotency key and preserve the
  last known sequence through transient failures.
- Derive follow-up and no-reply eligibility on the server from the owner's
  business-day policy instead of duplicating cadence rules in the browser.

## Phase 4A definition of done

- A complete fixture with enough evidence selects five distinct people from a
  larger pool with useful category diversity.
- A three-person fixture returns exactly three and never invents two more.
- Every selected person has preserved employer evidence and a normalized HTTPS
  public profile URL.
- Cross-owner references, duplicate identities, duplicate ranks, and
  under-evidenced source-backed rows fail closed.
- An exhausted partial plan requires structured shortfall reasons.
- Contact-bench reads never invoke a provider or worker.
- Upgrade, schema check, downgrade, and re-upgrade all succeed.

## Phase 4B definition of done

- Starting a search is owner-scoped, same-origin, optimistic-concurrency
  protected, idempotent, and returns the stored plan for both new and replayed
  requests.
- The generic worker claims `discover_contacts`, keeps its lease alive, performs
  provider work without an open database transaction, and atomically publishes
  no more than 12 candidates and five source-backed leads.
- Cancellation, lease loss, posting closure, stale plan references, retries,
  configuration failures, and provider exhaustion produce explicit durable
  states without partial publication or private error leakage.
- A malformed provider row cannot discard good leads or trigger deterministic
  paid retries, and mock mode cannot call the live provider.
- The application contact read exposes real queued/running/completed/failed
  progress and structured shortfalls without making provider calls.

## Phase 4C definition of done

- A not-started dossier offers an explicit **Find up to 5 people** action;
  merely opening the page never starts provider work.
- Queued and running attempts poll without overlapping requests, back off after
  transient failures, and never clear the last completed bench.
- Complete, partial, failed, cancelled, restricted-contact, and closed-posting
  states have honest, actionable copy and do not imply that outreach happened.
- Every returned person shows rank, public role, relevance category, evidence
  confidence, profile and employer-evidence links, source, and checked time.
- Lost or ambiguous POST responses reconcile durable state and retain the same
  idempotency key, so retrying cannot duplicate a paid search.
- Desktop and mobile browser QA cover not-started, queued, `5/5`, `3/5`, failed
  refresh with a prior result, do-not-contact restrictions, and posting closure.

## Phase 4D1 definition of done

- Starting outreach is owner-scoped, same-origin, version checked, idempotent,
  and pins exactly one completed evidence-backed bench.
- Up to five eligible leads can be drafted in the first wave. Each person keeps
  a separate exact message and manual-send record; cooldown and company-volume
  checks still run when a send is recorded.
- Exact v1/v2 message bodies are encrypted at rest, survive reload, and the
  version manually marked sent remains auditable.
- Copy never implies send. Mark-sent requires a prior copy of the exact latest
  version and persists one dated five-business-day follow-up.
- Early follow-ups, duplicate initial/follow-up sends, recent same-person
  contact, and a fourth cold company contact in seven days fail closed.
- Useful replies pause; introductions, referrals, do-not-contact requests, and
  manual stop preserve history and stop remaining waves. A mutation attempted
  after posting closure stops the sequence rather than saving or sending.
- Migration upgrade, schema parity, downgrade/re-upgrade, contracts, repository
  transitions, router security, and the full backend suite pass.

## Phase 4D2 definition of done

- The dossier starts outreach only after a completed source-backed bench exists
  and clearly separates eligible leads from restricted or cooling-down history.
- Unsaved owner text survives refreshes and conflicts. Saving creates a new
  immutable version without changing the text or implying that it was sent.
- Copy writes the exact saved body to the Clipboard before recording a copy
  event. Mark-sent is a separate confirmation bound to that same version and a
  selected channel.
- Initial and follow-up controls use server-projected business-day deadlines;
  premature send and no-reply actions remain unavailable or fail with honest
  policy feedback.
- Useful reply, introduction, referral, decline, unreachable, no reply, and do
  not contact outcomes expose their sequence effects before they are recorded.
- Pause, resume, permanent stop, closed posting, stopped, and completed states
  remain useful and read-only where appropriate. Nothing is sent automatically.
- Retryable or lost mutation responses reconcile durable state before a retry,
  reuse one idempotency key for the same intent, and never optimistically claim
  that a save, copy, send, or outcome succeeded.
- Desktop and mobile browser QA cover start, eligible/restricted leads, save/copy/send,
  follow-up timing, outcomes, pause/resume/stop, refresh preservation, and
  terminal role states without console errors.
