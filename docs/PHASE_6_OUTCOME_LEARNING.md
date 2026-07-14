# Phase 6 — Outcome learning and weekly operations

Phase 6 turns a recorded application into a trustworthy operating history. It
starts with durable facts, then builds weekly guidance and metrics from those
facts. The product must never infer a rejection, invent a skipped stage, or
claim that outreach caused an interview.

## Phase 6A1 — Hiring progress and terminal outcomes

Implemented in migration `20260715_0012`.

### Practical workflow

After an application is recorded, its dossier can now record:

- a completed recruiter screen;
- a completed qualified interview milestone;
- an offer received; and
- an explicit final outcome: rejected, withdrawn, offer accepted, offer
  declined, no response, or posting closed.

Real-world skips are allowed. For example, an owner may record an interview or
offer directly from `applied` when no earlier milestone was observed. The app
does not fabricate the missing stage.

Every active transition completes the previous task and creates exactly one
new dated task. Closing an application cancels its current task and creates no
replacement. A pre-submission pursuit can also be closed explicitly as
withdrawn or posting closed.

### Durable evidence boundary

- Progress is appended to the immutable application activity stream with both
  the real-world milestone date and the server recording timestamp.
- A terminal result creates one immutable, owner-scoped
  `application_outcomes` record.
- Post-submission outcomes reference the exact immutable submission record.
- Offer acceptance and decline are valid only after an offer was recorded.
- Dates cannot move backward across recorded milestones or into the future.
- Mutations require the current application version and an idempotency key.
- Closed applications have an outcome and no open action; active applications
  have one open action and no outcome.

Contact discovery and new outreach stop after confirmed hiring progress. Saved
contacts, messages, application materials, and the exact submission receipt
remain readable.

### Deliberate limits

This checkpoint records the first coarse screening/interview/offer milestones.
It does not yet model repeated interview rounds, interview appointments,
private interview notes, or corrections. It also does not claim causal lift
from referrals. Those require additional immutable provenance before they can
be presented honestly.

## Next checkpoints

### Phase 6A2 — Daily application actions and interview rounds

- Put overdue, today, and next-seven-day application actions at the top of
  Today using a dedicated server projection.
- Add repeatable scheduled/completed interview rounds with stable identifiers.
- Add append-only corrections that supersede an incorrect milestone without
  rewriting history.
- Attribute an outreach response to the exact marked-sent event and message
  version before sequence analytics are enabled.

### Phase 6B — Weekly review and trustworthy funnel

- Review stale applications without auto-classifying them as rejected.
- Show mature-cohort counts, rates, missing data, censored open applications,
  and sample sizes.
- Report contacts two through five as observed rescue rates among applications
  still unsuccessful after the prior contact—not as causal uplift.
- Add immutable career-track, acquisition-source, and assessment snapshots
  before segmenting metrics by those dimensions.

### Phase 6C — Privacy and operational hardening

- Export/delete and retention controls.
- Backup/restore and restart drills.
- Cross-browser, mobile, accessibility, concurrency, source-failure, and
  deployment smoke gates.
