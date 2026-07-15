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

## Phase 6A2 — Daily application operations

### Phase 6A2a — Today application actions

The Today workspace now starts with application work that is overdue, due on
the owner's local date, or due within the next seven local calendar days. It
comes from the dedicated read-only `GET /api/today/application-actions`
projection and loads independently from the opportunity-review inbox.

- The server derives the local date from the persisted owner timezone. The
  browser does not decide whether an action is overdue.
- Every item names the exact open action, active application and stage, pinned
  posting version, and current posting lifecycle state.
- Results are ordered by due date, action creation time, and stable action ID.
- Each urgency bucket returns its complete count and up to the requested safe
  limit, so a large overdue backlog cannot hide actions due today.
- A posting that closes after pursuit does not hide its application action;
  the owner still needs to resolve that application deliberately.
- Closed applications, completed actions, and cancelled actions cannot enter
  the queue. Malformed active action graphs fail closed instead of silently
  disappearing.
- The UI links to the dossier rather than offering unsafe inline completion.
  Application tasks and opportunity discovery also keep independent loading,
  empty, retry, and stale-data states.

No migration was required. The existing open-action invariant and owner/due
index are the durable source for this projection.

### Phase 6A2b — Repeatable interview rounds

Implemented in migration `20260715_0013`.

An applied, screening, or interviewing application can now schedule one
confirmed interview appointment at a time. Each round has a stable identifier,
monotonic round number, exact immutable application-submission reference, and
append-only schedule, reschedule, completion, or cancellation events. A later
round gets a new identifier instead of overwriting the earlier history.

- Scheduling and every later round event atomically replace the prior task.
  The scheduled round owns one dated `prepare_interview` task, so Today always
  reflects the current appointment rather than a stale follow-up date.
- Scheduling does not claim that an interview happened. Completing the first
  round advances an applied or screening application to `interviewing` and
  links the coarse milestone to that exact round. Later completions stay in the
  same stage while preserving their own round history.
- Cancelling a round creates a next-decision task; it never fabricates a
  rejection or closes the application. Offers and closure cannot be recorded
  while an appointment is unresolved.
- The API accepts a timezone-free wall time plus an explicit IANA timezone.
  The server resolves the instant, rejects daylight-saving gaps or ambiguous
  wall times, and bounds preparation dates using the persisted owner timezone.
- Scheduling uses the current application version. Later events use the round
  version. Every mutation is idempotent and increments the application version
  because the current task changes.

The dossier exposes the scheduled appointment, accessible inline reschedule,
complete, and cancel flows, plus completed/cancelled history. It deliberately
does not store meeting URLs, interviewer identities, private notes, or feedback
in this checkpoint. Multiple simultaneous appointments also remain deferred;
truthfully prioritizing them requires a multi-action design rather than hiding
several commitments behind one task.

### Phase 6A2c — Append-only milestone date corrections

Implemented in migration `20260715_0014`.

Activity now lets the owner correct an inaccurate coarse recruiter-screen,
manually recorded interview, or offer date. A correction is a new immutable
record linked to the original milestone; the original date and every earlier
correction remain visible. The latest correction is the current effective date
used by later transition validation.

- The mutation requires the current application version, a retry-safe
  idempotency key, and explicit confirmation.
- The server enforces the exact safe window between the immutable submission,
  adjacent resolved milestones, completed interview rounds, any terminal
  outcome, and the owner's current local date.
- A correction changes only `application.version` and `updated_at`. It cannot
  reopen or advance the application, replace the current task, edit the exact
  submission, or alter an outcome.
- Corrections remain available for an earlier eligible milestone after the
  application closes, provided the corrected date does not cross the saved
  outcome.
- Repeating a correction appends another linked revision, including a valid
  return to the original date; it never mutates or deletes prior history.

Round-linked interview milestones are intentionally excluded. Their date is
grounded in an exact completed interview round, so changing only the coarse
timeline would create conflicting histories. Terminal outcome corrections,
milestone retractions, and stage changes likewise require their own atomic
workflows rather than a date-only edit.

### Phase 6A2d — Exact outreach replies

Implemented in migration `20260715_0015`.

Replies are now first-class immutable facts rather than person-level outcome
labels. The owner records a reply from the card for the exact initial or
follow-up message that elicited it. The server derives the recipient and
immutable message version from that selected `marked_sent` event, so the
browser cannot accidentally claim a different person or draft.

- Each reply records its exact manual send, immutable message version and
  kind, reply classification, owner-local received date, encrypted optional
  note, and server recording time.
- Reply dates cannot precede the selected send or fall after the owner's local
  current date. The exact-send confirmation must be the literal boolean true.
- Several real replies can point to the same sent attempt. Earlier replies are
  never overwritten when a later introduction, referral, decline, or request
  not to be contacted arrives.
- A reply may be logged after a no-reply resolution, sequence completion,
  posting closure, or later application progress. Recording that historical
  fact does not reopen the application or re-enable another message.
- For a live plan, an introduction, referral, or do-not-contact request stops
  remaining outreach. Other replies pause it for review. The replied-to person
  cannot silently re-enter the staged sending cadence.
- `No reply` and `Could not reach` remain separate non-reply resolutions. New
  response facts cannot enter through the older unattributed outcome path;
  legacy outcomes stay readable without fabricated backfills.

The dossier nests ordered reply history under the exact sent attempt, including
the sent version, channel, date, and expandable text. The same history remains
available for resolved and terminal recipients. This provenance is the minimum
safe input for later sequence analytics; it records association, not proof that
outreach caused an interview or offer.

### Phase 6B — Weekly review and trustworthy funnel

Implemented in migration `20260715_0016`.

The authenticated **Weekly review** workspace now combines one owner-local,
database-only projection with an explicit way to clear overdue application
work. Continue and Waiting both reschedule the exact current non-interview
action and append an immutable review record. They never infer a rejection or
change the application stage. Interview-owned tasks link back to their round
workflow instead of accepting an incompatible generic reschedule.

The funnel uses exact application submissions from the latest 84 owner-local
days. An application enters the primary denominator only after a fixed 14-day
observation horizon. Screen, interview, and offer conversions inside that
horizon form the reported rate; later conversions stay visible but are not
quietly mixed into it. Recent open applications, malformed historical graphs,
missing attribution, rates with no denominator, and every sample size remain
explicit.

Every new pursuit also captures one immutable reporting snapshot:

- the owner-selected acquisition source;
- the exact saved search and career-track names and versions when Job Hunt
  search is selected;
- the exact pursued posting version; and
- the current assessment state, with future-capable assessment bands.

A single unambiguous saved-search match can be selected automatically. Several
matches require an explicit choice. The migration does not reinterpret mutable
legacy searches or tracks, so older applications remain visibly unattributed
instead of receiving fabricated history.

Outreach reporting anchors each attempt to its exact initial manual send and
includes useful replies, introductions, or referrals attributed to either the
initial message or its exact follow-up. It reports observed results by verified
contact category and bench position. Contacts two through five use only roles
that actually reached that position while earlier positions had not already
succeeded. Same-owner-local-day ordering ambiguity and still-open attempts are
excluded and counted. These are observed rescue rates, never causal uplift.

### Phase 6C — Privacy and operational hardening

- Export/delete and retention controls.
- Recruiter-screen/interview preparation and evidence-backed story prompts.
- Legacy import/deprecation, runbooks, and migration gates.
- Backup/restore and restart drills.
- Cross-browser, mobile, accessibility, concurrency, source-failure, and
  deployment smoke gates.
