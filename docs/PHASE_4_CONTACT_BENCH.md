# Phase 4 — Verified contact bench

**Status:** Phase 4A is implemented. Provider-backed execution, dossier
controls, and manual staged outreach remain the explicit 4B–4D checkpoints.

## Outcome

Each pursued application can build a durable bench of up to five distinct,
appropriate people from a larger discovery pool. Five is the target, never a
padding requirement: if only three people have sufficient evidence, the
product must persist and display `3/5 verified` with structured reasons.

This phase finds and verifies public professional profiles. It does **not**
draft, send, or imply that outreach has happened.

## Product rules

- Discover up to 12 candidates across team peers, adjacent peers, team
  leaders, recruiters, and explicit owner-provided warm paths.
- Select at most five after evidence checks, deterministic scoring, identity
  deduplication, cooldown checks, and category diversity.
- Require current-employer evidence observed from a named public source,
  including a bounded excerpt, HTTPS URL, observation time, and confidence of
  at least `0.75` before a person can enter the bench.
- Preserve the exact evidence and score components used for this application.
- Treat provider failures as incomplete/retryable work, not proof that no more
  suitable people exist.
- Keep reads database-only. Provider work belongs in the durable worker and
  runs outside long database transactions.
- Never send automatically. Later outreach remains a manual, staged workflow.

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

### 4C — Practical dossier experience

- Show `N/5 verified`, real progress, evidence links and checked dates,
  category/relevance labels, and structured shortfall reasons.
- Preserve the last good bench during retries and polling failures.
- Offer only profile/evidence review actions in this phase and state clearly
  that nothing has been sent.

### 4D — Manual staged outreach

- Persist exact message versions and manual copy/mark-sent events.
- Unlock only the allowed first wave; keep later contacts as reserves.
- Allow at most one follow-up and stop or pause later waves after a useful
  reply, introduction, referral, or terminal application outcome.

## Phase 4A definition of done

- A complete fixture with enough evidence selects five distinct people from a
  larger pool with useful category diversity.
- A three-person fixture returns exactly three and never invents two more.
- Every selected person has preserved employer evidence and a normalized HTTPS
  public profile URL.
- Cross-owner references, duplicate identities, duplicate ranks, and
  under-evidenced “verified” rows fail closed.
- An exhausted partial plan requires structured shortfall reasons.
- Contact-bench reads never invoke a provider or worker.
- Upgrade, schema check, downgrade, and re-upgrade all succeed.
