# Phase 3 — Application Pipeline

**Status:** the Phase 3A pursuit foundation is implemented. It creates and
recovers a real application workflow without pretending that the later stage,
application-pack, or contact workflows already exist.

## Outcome

Pressing **Pursue** now turns a persisted opportunity into a durable piece of
work instead of another bookmark:

```text
open persisted opportunity
  -> choose an initial action date
  -> Pursue
  -> exactly one application in `pursuing`
  -> exactly one open, dated next action
  -> exactly one immutable `application_created` activity
  -> application list and dossier read the saved graph from Postgres
```

Pursue does not search the web, read a resume, discover contacts, draft text,
call a model, or enqueue provider work. Those capabilities are added only
after the durable application boundary is trustworthy.

## Atomic pursuit boundary

`POST /api/opportunities/{opportunity_id}/decision` accepts `action: pursue`
and an optional `initial_action_due_on`. One transaction creates:

1. one owner-scoped application pinned to the posting version reviewed at
   pursuit time;
2. one open `Review role and prepare application` action;
3. one immutable `application_created` activity that enters `pursuing` and
   points to that action; and
4. one append-only opportunity decision event that moves the opportunity to
   `pursued`.

The action defaults to tomorrow in the owner's timezone. An explicit date may
be local today through 365 days ahead. Closed postings cannot be pursued.

## Correctness rules

- An owner and opportunity can have at most one application.
- A current application can have at most one open next action.
- Pursuit is owner-scoped across the application, opportunity, posting,
  posting version, action, and activity records.
- `If-Match` fences the first creation against a stale opportunity version.
- Replaying the same idempotency key and request returns the original graph,
  even after the opportunity version changes.
- Reusing that key with a different request conflicts.
- A second Pursue with a different key returns the existing application graph
  instead of creating another application, action, or creation activity.
- The pursued posting version is immutable evidence of what the owner reviewed;
  later posting changes do not rewrite it.
- Pursued opportunities cannot be moved back to Watch, Dismiss, or Inbox through
  the opportunity-decision endpoint. Later withdrawal belongs to the
  application pipeline.

## Database-only reads and UI

The first application workspace exposes only persisted projections:

```text
GET /api/applications
GET /api/applications/{application_id}
GET /api/applications/{application_id}/activity
```

Every response is owner-authenticated, private/no-store, and explicitly reports
`data_source: database`. Opening the list or dossier performs no live source,
resume, contact, drafting, model, or worker call.

The **Applications** list makes pursued roles recoverable after leaving Today.
The application dossier keeps the current dated action above the fold and shows
the first-party posting link, posting state, pursued posting version, current
stage, and immutable creation activity. Mobile uses the same readable
single-column workflow; this checkpoint does not introduce a drag-only board.

## Deliberately deferred

This checkpoint does not claim any of the following:

- stage transitions beyond `pursuing`;
- completing, snoozing, replacing, or cancelling the next action;
- application notes, applied dates, resume-version selection, outcomes, or a
  unified action center;
- requirement assessment or a generated application pack;
- contact discovery, verification, ranking, outreach drafts, sending, or
  follow-ups; or
- automatic applications or external messages.

## Immediate next checkpoint: source-backed contact bench

The locked next product checkpoint is:

> For every pursued role, discover a larger candidate pool and retain up to
> five distinct, appropriate public-profile leads when their saved source
> evidence meets the configured threshold.

Five remains a coverage target, not permission to invent or pad results. A
shortfall must be shown honestly, such as `3 of 5 source-backed`, with structured
reasons. Up to five eligible leads can be prepared together, but every message
and send record remains separate and nothing is sent automatically. This
contact bench was **not implemented in Phase 3A**; Phase 4 now delivers it.

## Checkpoint gate

Phase 3A is ready to hand off when automated checks prove that:

1. the first Pursue creates one application, one open dated action, one
   creation activity, and one pursued decision in one transaction;
2. same-key retry, changed-request conflict, and different-key double Pursue
   follow the rules above;
3. stale first creation, closed posting pursuit, invalid dates, and cross-owner
   access fail safely;
4. application list/detail/activity reads survive reload and invoke no provider
   or background-work capability; and
5. the Applications list and dossier remain usable on desktop and mobile.
