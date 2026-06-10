# Job Hunt Signal — Devpost submission

Paste-ready copy for the Devpost form. Update the bracketed links before
submitting.

**Hosted app:** https://agentic-job-hunt.vercel.app
**Demo video:** [ADD LINK AFTER UPLOAD]
**Repo:** https://github.com/vasubhrdwj/agentic-job-hunt (MIT)

## Inspiration

Job-hunt outreach is broken in a specific way: the messages that get replies
are personal, precise, and easy to say yes to — but nobody has the time to
write twenty-seven of them per week. Generic AI drafting makes the spam
problem worse, not better. We wanted an agent that doesn't just write
outreach, but *gets measurably better at it* by studying which of its own
past messages actually scored well — a self-improvement loop built on
observability data instead of vibes.

## What it does

Paste a resume and target criteria (keywords, seniority, location). The agent:

1. finds 3 matching open roles from live job listings,
2. surfaces 3 plausible referral targets per role from public profiles —
   with a hard rule against fabricating profile URLs,
3. drafts a concise, personalized referral request for each pair, and
4. scores every draft 1–5 with an LLM judge (personalization, specificity,
   ask quality, tone).

The Arize moment: before drafting, the agent queries its own Phoenix traces
for the highest-scoring past drafts on similar keywords and uses them as
few-shot exemplars. Round-over-round, the judge scores climb — we ship the
A/B chart (`demo/round_comparison.png`) proving it. Users log outcomes
(replied / no reply / introduced) against each draft, feeding the next
iteration of the corpus.

## How we built it

- **Agent + pipeline:** Python, Google ADK, Gemini 2.5 Flash for tool calls,
  drafting, and the LLM judge. A deterministic `run_hunt()` pipeline drives
  search → referrals → draft → eval.
- **Tools:** SerpAPI-backed job and people search (`site:linkedin.com`
  queries — no scraping), Gemini extraction into strict Pydantic contracts.
- **Observability & the loop:** every run is traced to Arize Phoenix Cloud
  via OpenInference/OTLP. Self-RAG retrieval reads past `draft_message`
  spans (REST, with MCP transport supported), filters by eval score ≥ 4,
  and threads exemplars into the drafting prompt. Judge sub-scores are
  written back onto the same spans — the traces *are* the training data.
- **App:** FastAPI backend on Render, Next.js + Tailwind frontend on Vercel,
  SQLite outcome log, seeded 18-message outreach corpus with a documented
  quality rubric.

## Challenges we ran into

- **Making the self-improvement visible.** Our first judge was too generous —
  it gave competent-but-generic drafts 4.5/5, leaving no headroom to measure
  improvement. We rebuilt the rubric around three verifiable behaviors
  (spec-level detail like an RFC number, the recipient's team by name, a
  time-boxed ask) and validated it against handwritten good/bad references
  before trusting any number it produced.
- **One silent timeout disabled the whole loop.** Phoenix queries are cached
  per run, so a single 1.5s timeout meant zero exemplars for all nine
  drafts. Found it in the traces — fixed with a configurable timeout.
- **Free-tier hosting honesty.** Render's free tier has no persistent disk
  and sleeps after 15 idle minutes. We kept SQLite ephemeral, documented the
  trade-off, and added a keep-alive monitor instead of pretending it's prod.

## Accomplishments we're proud of

- The eval-score chart: same inputs, judge held constant, retrieval toggled —
  and the average climbs. The loop demonstrably works.
- A judge calibrated against handwritten references before we believed it.
- No fabricated data anywhere: real listings, real public profiles, and a
  pipeline that says "I couldn't find anyone" rather than inventing a URL.

## What we learned

Observability isn't just for debugging — treated as a queryable corpus, traces
become the substrate for self-improvement. And LLM judges are only as good as
their calibration set: validate the judge before the pipeline, or the metric
is noise.

## What's next

Real outcome-based reranking (replies beat judge scores), multi-channel
outreach (email + LinkedIn + warm intro paths), persona modes, and PDF resume
parsing. The seeded corpus retires as real outcomes accumulate.

## Built with

`python` `google-adk` `gemini-2.5-flash` `arize-phoenix` `openinference`
`opentelemetry` `fastapi` `next.js` `tailwind` `serpapi` `render` `vercel`
