# Phase 5 — Grounded Application Pack

**Status:** Phase 5A is implemented and covered by automated model, schema,
repository, route, migration, frontend build, and live desktop/mobile browser
checks. Phase 5B grounded artifacts and Phase 5C
exact submission tracking are not implemented.

## Outcome

Phase 5A adds a truthful fit-and-evidence review to each pursued application:

```text
pursued application
  -> select one immutable resume
  -> pin it with the pursued posting version in one application pack
  -> extract required/preferred statements as exact JD source spans
  -> review each statement against currently approved achievements
  -> save a complete immutable review revision
  -> explicitly mark that exact current revision reviewed
```

An unsupported requirement is a valid, visible outcome. It is not converted
into evidence and it does not authorize a fabricated claim. Phase 5A prepares
the grounding boundary only; it does not generate application materials or
submit an application.

## Delivered Phase 5A workflow

### Start one pinned pack

The dossier shows **Fit and evidence review** before **People**. The owner
selects one saved immutable resume; the current base resume is selected by
default when available. Creating the pack pins:

- the owner and application;
- the application's immutable `pursued_posting_version_id`;
- its job-posting ID; and
- the selected resume as `base_resume_version_id`.

There is exactly one pack per application. Phase 5A has no reset, delete,
repin, or switch-resume flow after creation.

The exact full description preserved with the pursued posting version is the
authoritative JD. If and only if that version has no persisted full
description, the owner must paste the exact JD while creating the pack. A
supplied JD is rejected when a persisted description exists, and the shorter
posting summary is never silently promoted to a full JD.

### Deterministic requirement extraction

Extraction version `requirements-v1` uses conservative deterministic heading,
bullet, and explicit requirement-signal rules. Bullets inherit importance only
inside a recognized requirements/qualifications section; marketing, benefits,
and unqualified prose are not promoted through an arbitrary fallback. It emits
at most 40 candidates in source order. Every candidate
contains:

- a stable revision-local ID and ordinal;
- importance: `required` or `preferred`;
- exact text from the pinned JD;
- exact start/end character offsets; and
- initial coverage `needs_review`.

The server validates that `job_description[source_start:source_end]` is exactly
the returned requirement text. Phase 5A has no `uncertain` importance.

Current approved achievement evidence is ranked by deterministic phrase/skill
overlap and shown as a suggestion. Suggestions are not treated as proof and do
not decide coverage for the owner.

### Review and confirm

For every extracted requirement, the owner chooses one outcome:

- `supported` — requires at least one exact currently approved evidence ID and
  version;
- `partial` — also requires at least one exact currently approved evidence ID
  and version; or
- `unsupported` — permits no evidence reference and keeps the gap visible.

The owner can exclude a false-positive candidate or reclassify it between
`required` and `preferred`; its quote can never be rewritten away from the
pinned source span. Saving sends the complete included requirement set, not a
partial patch. The
server revalidates source spans, evidence ownership, approval state, and exact
evidence version, then appends an encrypted immutable revision with a parent
pointer. It never edits an earlier revision.

Marking a revision reviewed is a separate explicit event. It requires the
current pack version, the exact current revision ID, every requirement resolved
out of `needs_review`, current evidence mappings, and a literal boolean
confirmation. A later edit creates another draft revision; the earlier review
event remains durable, and the newer revision can be reviewed separately.

The read projection returns:

- the current revision;
- only the latest reviewed revision and its event, when one exists;
- current approved evidence; and
- current blockers.

It does not return the full revision or review-event history.

### Dossier behavior

The UI shows the pinned resume label, exact JD source, extraction version,
current revision, latest reviewed revision/event, requirement coverage, mapped
evidence snapshots, gaps, and blocker explanations. It preserves unsaved
coverage/evidence choices during refreshes and stale-conflict recovery, and it
reconciles ambiguous mutations from the saved server projection. The existing
first-party posting link remains available in the dossier.

Closed postings keep the saved projection readable but disable new pack,
revision, and review-event mutations.

## Persistence contract

### `application_packs`

One owner-scoped aggregate per application:

- application, job-posting, pursued-posting-version, and selected-resume IDs;
- optimistic `version`; and
- created/updated timestamps.

Status is not stored in this row. The API derives `not_started`, `draft`, or
`reviewed` from the presence of the pack, its latest revision, and its latest
review event.

### `application_pack_revisions`

Append-only review snapshots:

- application/pack IDs, revision number, and optional parent revision ID;
- source: `extracted` or `edited`;
- encrypted private payload and encryption-key ID;
- owner-bound content hash; and
- creation timestamp.

The encrypted payload contains the description source, exact JD, extraction
version, ordered requirements, coverage decisions, and exact approved-evidence
snapshots. Raw JD/evidence text is not stored in mapped plaintext columns.

### `application_pack_events`

Append-only explicit review confirmations:

- application/pack and exact revision IDs;
- positive per-pack sequence number;
- event type `reviewed`;
- occurrence time; and
- idempotency-key hash.

Only one `reviewed` event is allowed for a given revision, while later revisions
may each receive their own reviewed event.

Downgrading the migration removes `application_pack.%` owner mutation receipts
before dropping these three tables.

## Implemented API

```text
GET  /api/applications/{application_id}/application-pack
POST /api/applications/{application_id}/application-packs
POST /api/applications/{application_id}/application-packs/{pack_id}/revisions
POST /api/applications/{application_id}/application-packs/{pack_id}/events
```

`GET /application-pack` is an authenticated, owner-scoped, private `no-store`,
database-only projection. It reports prerequisites before creation and the
current/latest-reviewed state afterward.

`POST /application-packs` accepts `base_resume_version_id` and conditionally
`owner_job_description`. It pins the pursued posting version server-side,
creates the only pack for that application, and appends the deterministic
extracted revision.

`POST /revisions` accepts the current revision as `parent_revision_id` and the
complete reviewed requirement set. It appends one immutable `edited` revision.

`POST /events` accepts `event_type: reviewed`, the exact current `revision_id`,
and `confirm_requirements_reviewed: true`. It appends the review event and
advances the pack version.

Every mutation is origin/CSRF protected, owner-scoped, `If-Match` fenced, and
idempotency-keyed. Accepted responses return the reconciled current projection.

## Safety invariants

- One application has at most one pack.
- A pack always uses the pursued posting version; no later JD is substituted.
- An owner JD paste is accepted only when the persisted full description is
  missing.
- Requirement importance is only `required` or `preferred`.
- Every requirement text/offset pair is an exact slice of the pinned JD.
- `supported` and `partial` require currently approved, exact-version evidence;
  `unsupported` cannot carry evidence.
- Pending, rejected, retired, missing, stale-version, or cross-owner evidence
  fails closed.
- Revisions and review events are append-only and exact-revision scoped.
- Editing after review returns the projection to `draft`; the latest prior
  reviewed revision remains identifiable without exposing full history.
- The selected resume cannot be deleted while referenced by a pack.
- Private JD/evidence snapshots are encrypted and excluded from receipt
  payloads and validation-error echoes.
- Reads make no model, provider, browser, contact-search, or background-job
  call. Phase 5A mutations are also provider/model-free.
- Closed postings remain readable and reject mutations.
- Nothing generates claims, fills a form, sends a message, or submits an
  application.

## Phase 5A definition of done

- [x] Migration and metadata parity cover all three tables, owner-scoped keys,
  composite foreign keys, immutable revision/event constraints, downgrade,
  re-upgrade, and mutation-receipt cleanup.
- [x] One pack is pinned to the exact application posting version and selected
  immutable resume; duplicate packs and cross-owner edges fail closed.
- [x] Deterministic extraction produces ordered `required`/`preferred` exact
  source spans and a versioned extraction result.
- [x] Exact owner JD paste is required only when the persisted full description
  is absent; persisted descriptions cannot be overridden and summaries are not
  promoted.
- [x] Only exact versions of approved evidence can be mapped; stale or retired
  mappings block review and remain visible as a blocker.
- [x] Full review saves append immutable revisions, and multiple exact revisions
  can receive independently sequenced reviewed events.
- [x] Same-key replay is idempotent, changed-request reuse conflicts, and stale
  pack/application versions are rejected.
- [x] Private JD/evidence markers are absent from mapped revision storage and
  mutation receipts, and invalid private request text is not echoed.
- [x] Authenticated routes use the stable `/application-pack`, `/revisions`, and
  `/events` paths with private database-only reads and strict mutation
  preconditions.
- [x] The dossier implementation provides the current/latest-reviewed review
  workspace, honest gaps, copy support, local-draft preservation, conflict
  reconciliation, and no generated artifacts.
- [x] Live browser QA proves start → map → save immutable revision → mark exact
  revision reviewed on desktop and mobile, with no console errors or lost
  unsaved choices.

## Planned Phase 5B — Grounded artifacts

Phase 5B will use one reviewed grounding revision to create a tailored resume,
exact base-versus-tailored diff, company-specific note, and concise answers to
exact owner-entered questions. Any generated or rewritten candidate claim must
cite approved evidence from that reviewed revision; company/role facts must
cite the pinned JD. Owner edits must create immutable artifact revisions rather
than overwrite reviewed material.

Provider disclosure and consent are required if a future implementation sends
private input to an external model. No read may trigger generation.

## Planned Phase 5C — Exact submission record

Phase 5C will add the narrow `ready_to_apply` and `applied` transitions and
atomically record the exact reviewed artifact/resume version, first-party
destination, owner-local applied date, next-action change, and immutable
activity event. It will record a manual submission; it will not submit for the
owner.

## Explicit Phase 5A exclusions

Phase 5A does **not** include:

- tailored resume, answer, note, claim generation, or an exact resume diff;
- a full revision/review-event history API or history browser;
- pack reset, deletion, repinning, or switching the selected resume;
- overriding a persisted JD with owner-supplied text;
- an `uncertain` requirement importance;
- model/provider processing or automatic resume-to-evidence parsing;
- PDF/DOCX rendering or export;
- application-form scraping, browser automation, form filling, or submission;
- live company research beyond the pinned JD;
- a synthetic ATS score or LLM truth verdict;
- application stage changes or an applied record;
- bulk pack creation; or
- using contact identities, outreach content, or replies as candidate evidence.
