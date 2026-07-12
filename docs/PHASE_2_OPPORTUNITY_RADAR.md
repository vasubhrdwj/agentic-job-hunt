# Phase 2 — Durable Opportunity Radar

**Status:** Phase 2A contract and identity work started from commit `4cd8d49`.

## Outcome

Phase 2 turns disposable hunt results into a durable, deduplicated review
inbox. Repeated scans of the same saved search should show what is new or
changed instead of making the owner review the same posting again.

The Today page reads only persisted data. Opening Today never searches the
web, calls a model provider, discovers contacts, or drafts outreach.

## First useful flow

```text
saved search
  -> Scan roles (search only; no contacts or drafting)
  -> durable scan status and source warnings
  -> stable posting identity + immutable posting version
  -> one owner opportunity even when several searches find it
  -> Today: new / watching / dismissed
  -> open the first-party posting and review provenance
```

The current `Run full hunt` path remains available during migration. A full
hunt launched from an unchanged saved-search prefill may also contribute its
observed roles to the same durable posting store, but it is not the long-term
scan architecture.

## Locked truth rules

- A posting identity prefers `(source, company_slug, source_job_id)`.
- A trusted canonical job URL is the fallback. Company + title is never an
  identity key.
- A changed title, description, location, or source fact creates an immutable
  posting version; it does not create a second opportunity.
- Opportunities are unique by `(owner_id, job_posting_id)` and retain every
  saved-search provenance that matched them.
- Current adapters fetch criteria-filtered results. They must report
  `criteria_filtered` scope and `unknown` or `partial` completeness.
- A failed, partial, unknown-completeness, or criteria-filtered fetch can never
  close a posting.
- Closure requires either explicit authoritative closure or two consecutive
  successful, authoritative, complete-board omissions. Reappearance reopens
  the same posting.
- Source errors store safe codes and warnings, never raw response bodies,
  credentials, or exception text.
- Scan creation and finalization are idempotent and safe under worker retry.

## Incremental delivery

### 2A1 — Identity and fetch truth

- Add optional stable identity fields to `Role` without breaking legacy
  constructors or stored hunt results.
- Populate native IDs in first-party adapters and deterministic IDs in mocks.
- Add a backward-compatible source fetch result with explicit scope,
  completeness, timestamps, warning codes, and observed counts.
- Keep existing list-returning resolver methods as compatibility projections.

### 2A2 — Durable postings and opportunities

- Add scan run/source run, posting, posting version, alias, observation,
  saved-search match, opportunity, and decision-event records.
- Pin the saved-search version and criteria snapshot accepted for each scan.
- Upsert posting/version/observation and owner opportunity in one finalization
  boundary without duplicating results on retry.

### 2A3 — Search-only worker and API

- `POST /api/saved-searches/{id}/scans` creates or replays one scan.
- `GET /api/scans/{id}` reports real persisted progress and degradation.
- The worker fetches search results without referral discovery, drafting,
  self-RAG, resume transmission, or model calls.
- Automatic cadence dispatch stays disabled until manual scans and retry
  finalization pass the release gate.

### 2A4 — Today review inbox

- `GET /api/today` returns persisted summary counts, last scan health, and
  deduplicated opportunities.
- `GET /api/opportunities/{id}` shows current facts, immutable history, source
  provenance, unknowns, and matching saved searches.
- Initial durable decisions are `watch`, `dismiss`, and `restore_to_inbox`,
  with version fencing and an append-only event. There is no fake `Pursue`:
  that label appears only when it atomically creates the minimal application
  and next action required by the application phase.
- Saved searches keep the current full-hunt action as a clearly separate
  compatibility choice while `Scan roles` becomes the radar action.

## Release gate

Phase 2A is complete when:

1. scanning the same unchanged fixture twice creates one opportunity and one
   posting version;
2. a changed description creates one new posting version and visibly marks the
   existing opportunity changed;
3. two saved searches finding the same stable posting create one owner
   opportunity with both provenances;
4. source failure and incomplete inventory never close or hide a posting;
5. two complete authoritative omissions close it, and reappearance reopens it;
6. concurrent scan creation/finalization is idempotent on real PostgreSQL;
7. Today makes zero live source or model calls and retains last good data while
   a later scan is degraded;
8. watch, dismiss, and undo survive refresh and reject stale edits;
9. first-party apply URLs and all unknown facts remain explicit; and
10. the existing full hunt, five-contact output, profile, and saved-search flows
    continue to pass their release gates.
