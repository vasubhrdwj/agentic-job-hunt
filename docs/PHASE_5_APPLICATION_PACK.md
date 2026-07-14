# Phase 5 — Grounded Application Pack

**Status:** Phases 5A–5C are implemented. Model, schema, repository, route,
migration, generated-contract, frontend, and full-suite checks cover the
provider-free workflow; live desktop/mobile acceptance has passed.

## Outcome

Phase 5 adds a truthful preparation and manual-application path to each pursued
role:

```text
pursued application
  -> select one immutable resume
  -> pin it with the pursued posting version in one application pack
  -> extract required/preferred statements as exact JD source spans
  -> review each statement against currently approved achievements
  -> save a complete immutable review revision
  -> explicitly mark that exact current revision reviewed
  -> create a deterministic tailored resume, company note, and exact answers
  -> inspect the exact diff and claim-level source provenance
  -> approve one immutable artifact revision and tailored resume version
  -> mark those exact materials ready to apply
  -> submit manually at a persisted verified first-party destination
  -> record the exact submission, applied date, and follow-up action
```

An unsupported requirement is a valid, visible outcome. It is not converted
into evidence and it does not authorize a fabricated claim. Questions that
cannot be answered from approved evidence stay `needs_owner_input` and block
approval. The system never opens, fills, or submits an employer form.

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

### `application_artifact_revisions` and `application_artifact_events`

Artifact revisions append encrypted complete material payloads, input/generator
versions, revision/parent edges, owner-bound hashes, and exact grounding IDs.
Events append explicit `approved` or `rejected` decisions; an approval also
names the immutable tailored resume created for that exact revision. Composite
owner/application/pack keys prevent cross-aggregate references.

### `application_submissions` and transition activity

There is at most one submission per owner/application. It stores only exact IDs
for the pack revision/review, artifact revision/approval, tailored resume,
canonical persisted destination, owner-local applied date, `manual` method, and
recorded timestamp. Application activity events preserve the previous and new
action IDs; the applied event also references the exact submission.

Downgrades remove the relevant mutation receipts before dropping each phase's
tables or restoring the earlier stage/action constraints.

## Implemented API

```text
GET  /api/applications/{application_id}/application-pack
POST /api/applications/{application_id}/application-packs
POST /api/applications/{application_id}/application-packs/{pack_id}/revisions
POST /api/applications/{application_id}/application-packs/{pack_id}/events
GET  /api/applications/{application_id}/application-artifacts
POST /api/applications/{application_id}/application-packs/{pack_id}/artifact-revisions
POST /api/applications/{application_id}/application-packs/{pack_id}/artifact-events
GET  /api/applications/{application_id}/submission
POST /api/applications/{application_id}/transitions
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

`POST /artifact-revisions` accepts selected approved evidence plus exact
owner-entered application questions. It deterministically appends a complete
encrypted material revision with an exact line diff and claim provenance.
`POST /artifact-events` explicitly approves or rejects that exact revision;
approval atomically creates or reuses its immutable tailored resume version.

`GET /submission` is a database-only projection of the saved first-party apply
destinations and any immutable manual-submission receipt. `POST /transitions`
accepts only `ready_to_apply` or `applied`, exact reviewed material IDs, an
explicit confirmation, and the next dated action. Recording `applied` also
requires the owner-local applied date and exact persisted destination URL.

Every mutation is origin/CSRF protected, owner-scoped, `If-Match` fenced, and
idempotency-keyed. Accepted responses return the reconciled saved projection.

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
- Artifact revisions and approve/reject events are append-only; approval names
  one exact revision and one immutable child resume.
- Generated claims must reproduce exact source spans from approved evidence,
  the pinned JD, or persisted posting fields. Missing support stays missing.
- Fit and material mutations are accepted only while `pursuing` and freeze after
  readiness.
- Application transitions are forward-only, exact-material fenced, and replace
  one open action with exactly one next action atomically.
- An `applied` application has exactly one immutable `manual` submission record,
  and earlier stages have none.
- A submission destination must be one of the persisted first-party apply URLs
  from the pursued posting version.
- The selected resume cannot be deleted while referenced by a pack.
- Private JD/evidence snapshots are encrypted and excluded from receipt
  payloads and validation-error echoes.
- Reads make no model, provider, browser, contact-search, or background-job
  call. Phase 5A mutations are also provider/model-free.
- Closed postings remain readable and reject mutations.
- Nothing fills a form, sends a message, or submits an application. Phase 5B
  generation occurs only after an explicit mutation and remains local and
  deterministic.

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

## Delivered Phase 5B — Grounded artifacts

One explicit request uses the current reviewed grounding revision to create a
deterministic local material set. The owner selects up to five approved evidence
statements and may add exact application questions with character limits and
per-question evidence. The server appends one encrypted immutable revision:

- a tailored resume that preserves the base resume and prepends only sourced
  relevant highlights;
- an exact reconstructable line diff between base and tailored text;
- a company/role note sourced from pinned posting fields and JD spans;
- concise answers whose candidate claims cite approved evidence; and
- claim-level source ranges for every generated evidence, JD, or posting fact.

An unanswered question remains `needs_owner_input` and blocks approval. Reads
never generate. Creating a later revision never overwrites an earlier one, and
rejecting an exact revision requires changed inputs before another approval.
Approving atomically creates or reuses an immutable non-base resume version
whose parent is the pack's pinned base resume.

Phase 5B makes no model or provider call. If a future generator sends private
input to an external service, it requires a separate disclosed consent and
data-boundary design.

## Delivered Phase 5C — Exact manual submission

The application state machine is forward-only:

```text
pursuing
  -> ready_to_apply   # reviewed exact materials; next action is submit
  -> applied          # owner asserts manual submission; next action is follow up
```

Readiness validates the current reviewed grounding event, approved artifact
event, immutable tailored resume, open posting, and at least one persisted
verified first-party destination. It completes the preparation action, creates
one dated submit action, and appends activity sequence 2. Grounding and material
mutations then become read-only; People and manual outreach remain available.

The applied transition requires a separate literal manual-submission
confirmation, one exact saved destination, the owner-local applied date, and a
follow-up due date. In one transaction it creates the immutable submission row,
completes the submit action, creates one dated follow-up action, advances the
application, and appends activity sequence 3 referencing that submission.
Closing a posting after readiness does not erase the saved pack or prevent the
owner from accurately recording an application submitted on or before closure.

The receipt names the exact 5A revision/review event, 5B revision/approval
event, tailored resume version, destination, and dates used. The system records
the owner's assertion; it does not claim to have submitted anything itself.

## Phase 5B/5C definition of done

- [x] Deterministic local generation produces immutable resume, note, and answer
  revisions without provider/model calls.
- [x] Every emitted candidate claim cites exact approved evidence, and every
  company/role fact cites a pinned posting field or exact JD span.
- [x] Exact line diffs reconstruct both base and tailored resume content.
- [x] Unanswered questions, unchanged resumes, stale grounding, rejected current
  revisions, and closed postings block approval visibly.
- [x] Approval creates or reuses one immutable non-base child resume and pins it
  to the exact artifact event.
- [x] Only `pursuing -> ready_to_apply -> applied` is accepted, with one open
  dated action and one immutable event at each stage.
- [x] The applied receipt records exact reviewed materials, a verified persisted
  destination, owner-local applied date, and manual method atomically.
- [x] Fit review and materials freeze after readiness while People/outreach stay
  usable throughout all three stages.
- [x] No read generates, no form is filled, and no application is submitted
  automatically.

## Explicit Phase 5 exclusions

Phase 5 does **not** include:

- a full revision/review-event history API or history browser;
- pack reset, deletion, repinning, or switching the selected resume;
- overriding a persisted JD with owner-supplied text;
- an `uncertain` requirement importance;
- model/provider processing, automatic resume-to-evidence parsing, or freeform
  AI rewriting;
- PDF/DOCX rendering or export;
- application-form scraping, browser automation, form filling, or submission;
- live company research beyond the pinned JD;
- a synthetic ATS score or LLM truth verdict;
- bulk pack creation; or
- using contact identities, outreach content, or replies as candidate evidence.
