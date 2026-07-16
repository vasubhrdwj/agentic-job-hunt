# Phase 6C privacy and data controls

The authenticated `/privacy` workspace is the owner-facing control surface for
portable export, bounded legacy-run retention, and transactional deletion. All
privacy responses use `Cache-Control: no-store`, require the opaque owner
session, and mutations also enforce the configured browser origin.

## Portable export

`GET /api/privacy/export` returns schema-versioned, deterministic JSON scoped
to the authenticated owner. Tables and rows use stable ordering and the
manifest includes a count for every included table.

- Encrypted profile, resume, evidence, opportunity-note, application-pack,
  artifact, outreach, legacy-run, and interview-preparation payloads are
  decrypted with their authenticated record binding.
- Stored ciphertext, encryption-key IDs, session records, token/access hashes,
  mutation or idempotency fingerprints, queue lease data, internal job errors,
  and secret-looking JSON fields are never exported.
- Missing, cleared, or undecryptable private values are omitted instead of
  leaking ciphertext. The manifest reports the table, field, reason, and count.
- The backend and Next.js proxy both enforce a 32 MiB limit. Oversized exports
  fail explicitly; they are never truncated.

## Retention

`GET/PATCH /api/privacy/retention` reports and updates a versioned 1–30 day
policy for encrypted **legacy hunt runs only**. `If-Match` prevents lost
updates. New legacy runs receive the current owner's policy at creation.
Scheduled cleanup applies it to existing runs, and shortening the period
immediately deletes newly eligible run graphs in the same transaction. The
response reports the exact number purged. A longer setting never extends an
already shorter per-run expiry.

Profiles, resumes, evidence, saved searches, opportunities, applications,
interviews, contacts, and outreach are not silently aged out. They remain until
the owner explicitly deletes the workspace.

## Transactional workspace deletion

`GET /api/privacy/deletion-preview` returns exact owner-row counts, active
session count, and the required phrase `DELETE WORKSPACE <owner-id>`.
`DELETE /api/privacy/workspace` requires that exact phrase, an allowed origin,
the current owner session, and an `Idempotency-Key`.

The database deletes the owner row and its full cascade in one transaction,
including every owner session and owner background job. Other owners and
system rows are not deleted; a worker heartbeat pointing at a deleted owner job
is retained with its job reference cleared. Only after the transaction
succeeds does the API return a minimal receipt and expire the browser cookie.

For safe retries, the database retains a payload-free deletion receipt with a
random deletion ID, time, request fingerprint, idempotency-key hash, and a
domain-separated HMAC of the owner ID. It has no foreign key to the deleted
owner and contains no raw owner ID or private payload. Configure the stable
`JOB_HUNT_PRIVACY_RECEIPT_SECRET` when owner credentials may rotate; otherwise
the app falls back to the configured high-entropy owner-token digest.

The `20260715_0018` downgrade refuses to discard any retention setting or
deletion receipt. An operator must deliberately preserve and clear that state
before downgrading.

## Provider-side limits

Local deletion cannot retract provider-side logs. The UI and API disclose the
following policies as verified on 2026-07-15; operators must re-check the linked
official pages when provider terms change.

- [Gemini API Additional Terms](https://ai.google.dev/gemini-api/terms): paid
  prompts and responses are not used to improve products.
- [Abuse monitoring](https://ai.google.dev/gemini-api/docs/usage-policies):
  mandatory policy-enforcement logs may include prompts, context, and output
  for up to 55 days.
- [Logs policy](https://ai.google.dev/gemini-api/docs/logs-policy): optional
  private project logs have selectable 7, 14, 28, or 55-day retention;
  dataset/feedback sharing changes the applicable data use.
- [Zero Data Retention](https://ai.google.dev/gemini-api/docs/zdr): ZDR requires
  approval and sanitizes identifiable content before abuse logging; Search or
  Maps grounding has separate storage terms, currently including 30 days.
