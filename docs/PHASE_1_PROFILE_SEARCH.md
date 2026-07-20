# Phase 1 — Remember Me and My Searches

**Status:** Phase 1A delivered and verified on SQLite, real PostgreSQL, the
Docker runtime, and the signed-in browser flow.

## Outcome

Phase 1 removes the most repetitive part of the current workflow. The owner
stores a base resume and job preferences once, then launches a deliberate hunt
from a saved search without repasting or reconstructing criteria.

At Phase 1 delivery, saving or editing a profile/search made zero provider calls
and scheduled execution was intentionally deferred. Phase 2 now connects due
saved searches to the durable scan worker while the hosted service is awake;
editing profile/search data itself still makes no provider call.

## First usable flow

```text
sign in
  -> complete About you
  -> save encrypted base resume
  -> create a career track
  -> save one JobCriteria-compatible search
  -> open Run now
  -> review the exact resume + criteria prefill
  -> consent to provider processing
  -> launch the existing durable hunt
```

This was the first migration flow. New legacy hunts are now retired; retained
runs remain readable in the Legacy hunt archive.

## Product boundaries

- One candidate profile per owner.
- Multiple immutable resume versions; exactly one may be the base resume.
- Multiple career tracks, such as `Backend / India` and `Platform / Remote`.
- A saved search selects one career track, one explicitly pinned resume version
  (the current base is resolved and pinned when saved), one currently supported
  seniority, a company pack, and lossless `JobCriteria`.
- Achievement evidence is manual and pending until explicitly approved. No
  resume parsing or generated claims in this slice.
- `Run now` is a projection, not a provider action. The owner still reviews the
  provider disclosure and explicitly launches the hunt.
- Manual cadence remains available. Daily/weekdays/weekly schedules now create
  durable scans while the background service is awake, with an explicit free-
  hosting sleep caveat and an on-demand **Scan roles** fallback.

## Non-technical UI

### Home

- Keep retained legacy runs readable without starting new legacy work.
- When a base resume exists, prefill it instead of showing a blank textarea.
- Show a compact saved-search selector above the hunt form.
- Selecting a search fills the form; it never starts work automatically.
- Add clear links to `Profile` and `Saved searches`.

### Profile

Use four plain-language sections:

1. **About you** — current title, home location, work authorization, preferred
   work modes, notice period, and a short career direction.
2. **Base resume** — current encrypted resume, version metadata, and `Make base`
   for another immutable version.
3. **Career targets** — named role families, allowed seniorities, and target
   locations. Stored preference weights remain advanced metadata and are
   explicitly not presented as part of current ranking.
4. **Achievement evidence** — manual truthful claims with an explicit pending,
   approved, rejected, or retired review state.

The page autosaves nothing silently. Save buttons show success, stale-version
conflicts, and validation errors next to the affected section.

### Saved searches

- List name, career track, chosen resume, key criteria, cadence, and last/next
  timestamps.
- `Run now` opens the existing hunt form with the exact stored values.
- Editing/deleting is version-fenced and requires confirmation where dependent
  data would be affected.
- A search blocked by a missing/deleted resume or inactive track explains the
  blocker instead of producing a partial request.

## Security and correctness

- Every read and write is owner-scoped; cross-owner IDs return 404.
- Every mutation requires the owner cookie and an allowed Origin.
- Resume text, career-thesis free text, evidence statements, and source excerpts
  are encrypted with `DataKeyring`.
- Resume lists return metadata rather than content. Evidence review responses
  include decrypted statement text only on owner-authenticated, `no-store`
  routes because the owner must be able to review the claim being approved.
- Detail and hunt-input responses are `no-store` and owner-only.
- Mutable records use integer versions and `If-Match`/ETag semantics.
- Create retries are idempotent without storing request bodies or private
  response payloads in an idempotency table.
- Compensation ranges reject `comp_min_lpa > comp_max_lpa`.
- IANA timezones and cadence-specific schedule fields are validated server-side.
- Career tracks referenced by saved searches cannot disappear through a silent
  cascade.

## Release gate

Phase 1A is complete when an authenticated owner can:

1. save a profile and encrypted base resume;
2. reload the browser and recover both;
3. create two career tracks and two owner-isolated saved searches;
4. open a saved search as one exact, legacy-compatible hunt prefill;
5. launch it and receive the existing five-contact-per-role result;
6. resolve a stale edit without overwriting newer data;
7. prove private markers are absent from raw mapped storage and logs;
8. restart the web process without losing any accepted data.

The release gate passed. The live mock-backed walkthrough recovered accepted
profile data after rebuilding/restarting the web container, created and edited
a saved search, launched its exact prefill, and returned three roles with five
distinct contacts per role. Browser QA also found and fixed missing legacy IANA
timezone aliases in the slim container by adding the pinned Python timezone
database and a Docker build assertion.
