# OpenAI Build Week demo script (target: 2:40)

Use a local `USE_MOCKS=1` workspace seeded with
`python3 scripts/seed_demo_workspace.py`. Record at 1080p with readable browser
zoom. Use the synthetic account and résumé only; hide generated credentials,
browser autofill, DevTools, and environment variables. Use voiceover and no
music.

## Pre-flight

- Warm every page and complete one mock scan before recording.
- Keep a second unpursued role ready so the pursuit flow is fresh.
- Confirm the résumé shown is `fixtures/sample_resume.txt`, not personal data.
- Confirm no real profile URL, email, API key, database URL, or session cookie
  is visible.
- Export at 1080p and keep the public YouTube upload below three minutes.

## 0:00–0:15 — problem and promise

**Screen:** Today, showing a varied list of ranked roles.

**Voiceover:**

> Job searching is not one task. It is finding fresh roles, judging fit,
> proving that fit, finding people, writing outreach, and remembering every
> follow-up. Job Hunt Signal turns one résumé into a ranked, evidence-backed
> pipeline and up to five warm paths into each role.

## 0:15–0:42 — résumé-first setup

**Screen:** Profile. Briefly show the upload control, then the already imported
synthetic profile, skills, and evidence.

**Voiceover:**

> Start with a real PDF, DOCX, or text résumé. The app safely extracts the
> title, experience, skills, and exact achievement bullets, then asks only for
> details a résumé cannot know. Private fields are encrypted per account, and
> the original binary file is not retained.

## 0:42–1:08 — search and automatic fit

**Screen:** Search → Scan roles → Today. Select Recommended and open the top
role's evidence panel.

**Voiceover:**

> A durable worker scans curated first-party career boards. Today deduplicates
> the result set, prevents one company from taking over the page, and ranks all
> roles before pagination. There is no mystery percentage: each recommendation
> shows eligibility, fit band, confidence, matching evidence, and missing
> inputs.

## 1:08–1:35 — application dossier

**Screen:** Pursue one role. Scroll through why-fit, requirement coverage, and
application materials.

**Voiceover:**

> Pursuing a role creates a dossier from the exact résumé and job-description
> versions. It prepares a why-fit story, maps requirements to evidence, drafts
> tailored material, and blocks unsupported claims. The user starts from a
> grounded answer instead of doing the same comparison from scratch.

## 1:35–1:58 — five-person referral plan

**Screen:** People bench with five mock contacts; open two different drafts and
show the evidence/shortfall treatment.

**Voiceover:**

> One referral lead is fragile, so the app finds up to five appropriate people
> and drafts separately for each. Every person keeps source evidence. If only
> three qualify, the product says three of five—it never invents the other two.
> Copying, sending, follow-ups, and outcomes stay explicit and rate-limited.

## 1:58–2:18 — closed loop

**Screen:** Today action center, application timeline, then Weekly Review.

**Voiceover:**

> Applications, outreach, interviews, corrections, and next actions form one
> durable timeline. Today surfaces overdue work, and Weekly Review shows the
> funnel without silently calling unanswered applications rejections.

## 2:18–2:38 — Codex and GPT-5.6

**Screen:** GitHub commit history, then the README Codex section and Build Week
comparison link.

**Voiceover:**

> Codex accelerated the Build Week rebuild across Postgres migrations, FastAPI
> repositories, generated contracts, Next.js workflows, deployment debugging,
> and regression tests. I made the product calls: fit-first ranking, five
> evidence-backed contacts, résumé-first onboarding, normal accounts, and no
> invented claims. In the GPT-5.6 pass, Codex also built the no-key demo seeder
> from the public API contracts. GPT-5.6 was a development collaborator, not a
> model receiving user résumés in production.

## 2:38–2:48 — close

**Screen:** Today → repository README → live URL.

**Voiceover:**

> Job Hunt Signal does the repetitive research and grounding. The job seeker
> keeps the decisions. The live app, MIT-licensed code, and free synthetic demo
> are linked in the submission.
