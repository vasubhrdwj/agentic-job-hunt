# OpenAI Build Week — paste-ready submission

This file contains the final project copy plus the few values that must come
from Vasu's Devpost, YouTube, and Codex accounts. Replace every `REQUIRED`
marker before submitting.

## Submission fields

**Project name:** Job Hunt Signal

**Tagline:** Turn one résumé into a ranked, evidence-backed job pipeline and a five-person referral plan.

**Category:** Apps for Your Life

**Submitter type:** `REQUIRED — INDIVIDUAL OR TEAM OF INDIVIDUALS`

**Country of residence:** `REQUIRED — CONFIRM BEFORE SUBMISSION`

**Live project:** https://agentic-job-hunt.vercel.app

**Repository:** https://github.com/vasubhrdwj/agentic-job-hunt

**Devpost draft:** https://devpost.com/software/job-hunt-signal

**License:** MIT

**Public YouTube demo (<3 minutes):** `REQUIRED — ADD PUBLIC YOUTUBE URL`

**Primary `/feedback` Codex Session ID:** `REQUIRED — RUN /feedback IN THE MAIN BUILD TASK`

## Testing instructions

The hosted app is free to test and supports normal email/password signup:

1. Open https://agentic-job-hunt.vercel.app and create an account. Passwords
   must contain at least 12 characters.
2. Open Profile and upload a PDF, DOCX, or TXT résumé no larger than 3 MiB.
   The app keeps normalized encrypted résumé text, not the original binary.
3. Confirm the extracted title, experience, skills, and achievement evidence;
   add only the few preferences that cannot be learned from a résumé.
4. Create a career target and saved search, choose Scan roles, then open Today
   when the durable scan completes.
5. Pursue a recommended role to inspect its fit evidence, grounded application
   materials, source-backed People bench, separate outreach drafts, and next
   actions.

Judges who prefer a deterministic local walkthrough can follow the clean-clone
Docker instructions in the root README and run
`python3 scripts/seed_demo_workspace.py`. That mode needs no paid API key.

## Inspiration

Job searching is rarely one hard task. It is dozens of repeated small tasks:
finding fresh roles, checking eligibility, comparing a job description with a
résumé, deciding where to spend time, rewriting evidence, finding people who
might respond, drafting messages, and remembering every follow-up. A
spreadsheet stores rows but does not do that work; a generic chatbot forgets
the state and can invent details.

We wanted a private job-search workspace that carries verified context from the
résumé all the way to the application and outreach decision. It should reduce
effort without taking away the user's control or pretending uncertainty is
confidence.

## What it does

Job Hunt Signal turns an uploaded résumé into a durable, owner-scoped job
pipeline:

- It safely parses PDF, DOCX, and TXT résumés, extracts high-confidence profile
  fields and skills, and converts exact résumé bullets into grounded evidence.
- It scans curated first-party company and ATS sources, normalizes and
  deduplicates openings, rejects stale or ineligible roles, and ranks the whole
  Today inbox by explainable fit before pagination.
- Each role explains matching evidence, uncertainty, and gaps. Pursuing one
  creates a dossier with a why-fit story, requirement coverage, tailored résumé
  changes, application answers, and interview preparation grounded in the
  exact résumé and posting versions.
- It researches up to five appropriate referral leads, preserves source
  evidence and honest shortfalls, and prepares a distinct message for each
  person instead of one generic template.
- It tracks manual sends, follow-ups, application milestones, interviews,
  corrections, outcomes, overdue work, and weekly funnel learning.
- Normal signup gives each person a separate encrypted workspace with export
  and deletion controls.

The product never auto-submits an employer form or auto-sends outreach. It
never fills an evidence gap with an invented claim or person.

## How we built it

The product uses a Next.js 16/React 19 frontend, a FastAPI backend, PostgreSQL
16, Alembic migrations, encrypted owner-scoped repositories, and a durable
worker queue. Search adapters read public first-party career sources. Matching,
evidence grounding, practical application materials, and default outreach
drafts are deterministic and provider-free. SerpAPI can optionally power live
public-profile discovery; the older agent experiment uses Google ADK/Gemini
and Phoenix, but neither is required for the practical free workflow.

We built the extension in small vertical slices so a single product decision
was reflected in the database constraint, repository transaction, API schema,
generated TypeScript contract, interface, and regression coverage together.

## How Codex and GPT-5.6 accelerated the work

Codex was the implementation collaborator throughout the Build Week extension.
It helped translate direct product feedback into small commits across the
FastAPI/Postgres backend and Next.js frontend, inspect failures at their real
boundary, and keep the API contract and tests synchronized with each change.

Concrete areas include:

- owner-scoped models, Alembic migrations, transactional repositories, and
  generated OpenAPI/TypeScript contracts;
- the automatic fit engine and persisted Today ordering;
- the five-person source-backed contact bench and per-person outreach drafts;
- evidence-pinned application materials and interview story starters;
- normal multi-user authentication, encrypted privacy controls, and safe
  deployment across Vercel, Render, and Neon Postgres; and
- the bounded PDF/DOCX/TXT parser, atomic résumé import, and upload-first UI.

During the GPT-5.6 submission pass, a GPT-5.6 Codex agent read the public API
contracts and implemented the dependency-free demo workspace seeder and its
sample-data guide. GPT-5.6 was used through Codex as a development model; it is
not a production inference dependency and does not receive users' résumés.

Vasu made the key product decisions: serve real job seekers rather than ship a
one-run demo; rank roles by fit; find five appropriate people rather than one;
make résumé upload the default; replace a shared passkey with normal accounts;
prefer free first-party sources; minimize repetitive approvals; never invent
evidence; and leave sending and applying to the user. Codex explored and
implemented solutions under those constraints.

## What was new during Build Week

The repository existed before the event as a narrower agent demo. Commit
`31043a9` is the final pre-submission-period baseline. The Build Week extension
starts at `d1b64b1`; the dated work is visible at:

https://github.com/vasubhrdwj/agentic-job-hunt/compare/31043a9...main

That extension added the durable opportunity radar, fit-ranked Today inbox,
application dossiers, five-contact outreach waves, application materials,
interview preparation, outcome learning, multi-user accounts, privacy
controls, free-tier scanning, and secure upload-first résumé onboarding.

## Challenges we ran into

**Useful automation without fabricated confidence.** A fit percentage looked
simple but hid missing job-description and candidate facts. We replaced it
with categorical, explainable assessment: eligibility, fit band, confidence,
supporting evidence, and explicit missing inputs.

**A referral feature that is not spam.** Finding one person was too fragile;
returning arbitrary names was worse. We built a diverse bench of up to five,
pinned every result to public evidence, drafted separately for each person,
added cooldowns and volume limits, and kept sending manual.

**Turning a demo into a real account-based product.** The original shared
owner-key model blocked normal use. We migrated to email/password accounts and
owner-scoped data without losing the existing workspace, then hardened
sessions, CORS, upload body limits, migrations, exports, and deletion.

**Free-tier deployment boundaries.** Render sleep, worker availability,
database migrations, and Vercel proxy failures can look like product failures.
We separated web readiness from provider capability, added durable retries and
an embedded free-tier scan worker, and made degraded capabilities visible.

## Accomplishments we are proud of

- The app now evaluates and orders opportunities before asking the user to do
  work, instead of recreating a spreadsheet with extra approval buttons.
- One résumé upload creates reusable profile context and grounded evidence
  while preserving encryption, account isolation, and immutable history.
- Every pursued role connects the same evidence to fit, application materials,
  five possible referral paths, outreach, interview preparation, and outcomes.
- Missing evidence remains a visible product state. A shorter honest result is
  considered better than a polished hallucination.
- The repository includes a free, deterministic, synthetic judge walkthrough
  and a public hosted instance.

## What we learned

The best job-search automation is not automatic submission. It is reducing the
number of decisions the user has to reconstruct while keeping high-stakes
external actions explicit. Evidence provenance is also a user-experience
feature: showing why a role fits and which résumé line supports a story makes
the recommendation easier to trust and faster to use.

Codex was most valuable when product feedback crossed layers. A request such as
“rank roles by fit” is not one UI sort; it changes persistence, pagination,
cache identity, API semantics, edge cases, and tests. Keeping that context in
one development task materially shortened the feedback-to-working-product
loop.

## What's next

- More first-party company sources and user-selectable source packs.
- Optional provider choices for public contact research while preserving the
  evidence and budget fences.
- Outcome-calibrated recommendation weights once a user has enough data,
  without pretending small samples are causal.
- A simpler guided Today flow that makes the next best action even more
  obvious on mobile.

## Built with

`Codex` `GPT-5.6` `Python` `FastAPI` `PostgreSQL` `SQLAlchemy` `Alembic`
`Next.js` `React` `TypeScript` `Docker` `Vercel` `Render` `Neon`
`Google ADK` `Gemini` `Arize Phoenix` `SerpAPI`

## Final submission checklist

- [x] Vasu's Devpost account is registered for OpenAI Build Week.
- [x] Editable Devpost project draft created as `job-hunt-signal`.
- [x] GitHub repository visibility confirmed as public.
- [ ] Choose Individual or Team of Individuals, confirm country of residence,
  and ensure every named contributor has agreed to the submission.
- [ ] Record the current product using
  [`demo/OPENAI_BUILD_WEEK_VIDEO.md`](OPENAI_BUILD_WEEK_VIDEO.md).
- [ ] Upload the video publicly to YouTube and paste its URL above.
- [ ] Run `/feedback` in the primary Codex build task and paste the Session ID
  above and into Devpost.
- [ ] Keep the hosted app free and available through the judging period.
- [ ] Replace every `REQUIRED` marker, preview the entry, then submit before
  July 21, 2026 at 5:00 PM Pacific Time.
