# Demo script — Job Hunt Signal (≤3:00)

Pre-record with Loom/QuickTime. Voiceover over screen capture, no talking
head. Subtitles auto-generated. Have these tabs open and warm before
recording: the Vercel app, Phoenix (`job-hunt-agent` project), and
`demo/round_comparison.png`.

> Pre-flight: hit the backend once so the instance is warm, and confirm the
> UptimeRobot monitor is green. Do NOT push to main on recording day.

## Beat 1 — Problem (0:00–0:25)

Screen: the input page, empty.

> "Job hunting outreach is a numbers game played badly: people blast the same
> template to strangers and get silence. Job Hunt Signal is an agent that
> finds real roles, real referral targets, and writes outreach that earns a
> reply — and it measurably gets better every time it runs, by reading its
> own traces."

## Beat 2 — Product (0:25–1:15)

Screen: paste resume, set keywords (SCIM, identity, IAM), hit Run hunt.
While the progress states tick ("searching jobs… finding referrals…
drafting…"), narrate:

> "Paste a resume and target criteria. The agent searches live job listings,
> finds three plausible referral targets per role from public profiles, and
> drafts a personalized message for each — never inventing profile URLs."

Screen: results render. Hover one draft card; point at the judge score badge.

> "Every draft is scored one-to-five by an LLM judge on personalization,
> specificity, and the quality of the ask. You edit, copy, send — then log
> what happened, building the outcome data the agent learns from."

## Beat 3 — The Phoenix trace (1:15–2:00)

Screen: switch to Phoenix, open the trace for the run you just did (search
by run_id from the UI).

> "Everything you just saw is traced in Arize Phoenix: the full span tree —
> job search, referral lookups, nine drafts. Open a draft span: here's the
> retrieval call where the agent queried its own past traces for the
> highest-scoring messages on these keywords, and the exemplar IDs it pulled.
> The judge's sub-scores land on the same span."

## Beat 4 — The loop closes (2:00–2:50)

Screen: `demo/round_comparison.png`, full screen.

> "Does self-retrieval actually help? Same resume, same criteria, judge held
> constant. Round one: retrieval off — the baseline. Round two: the agent
> pulls its three best past drafts as exemplars. Average judge score climbs
> from {AVG1} to {AVG2} — {GAP} points — because the agent imitates what
> already worked: a precise spec detail, the recipient's team by name, a
> fifteen-minute time-boxed ask. The agent literally learns to write better
> outreach from its own observability data."

## Close (2:50–3:00)

Screen: repo README.

> "Job Hunt Signal — Gemini for the agent, Phoenix for the memory. Live URL
> and code in the description."

---

Fill in {AVG1}/{AVG2}/{GAP} from `demo/round_comparison.md` before recording.
Vasu signs off on the final cut before upload (A9 checklist).
