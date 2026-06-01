# V7 Seed Outreach Notes

All entries in `seed_outreach.jsonl` are synthetic. Names, companies, roles,
messages, outcomes, and scores are fabricated for demo data; do not treat them
as real hiring contacts or real company history.

## Trace Contract

`scripts/seed_phoenix.py` writes one flat `draft_message` span per seed entry.
Each span must carry the attributes that `mcp_client.query_past_drafts()` reads:

- `job_hunt.draft.output_text`
- `job_hunt.role.keywords`
- `job_hunt.role.title`
- `job_hunt.role.company`
- `job_hunt.eval.composite_score`

Seed-only attributes:

- `job_hunt.seed.id`
- `job_hunt.seed.tag = "seed"`
- `job_hunt.seed.synthetic = true`
- `job_hunt.seed.score_band`
- `job_hunt.outcome`

## Rubric

High-score drafts include all three signals:

- Concrete technical detail, such as `SCIM 2.0 RFC 7644`, `Next.js App Router`,
  or `RAG retrieval`.
- Named team reference, such as `Lifecycle Management team`.
- Specific next step, such as `15 min Tue or Wed?`.

Mid-score drafts include exactly one signal. Low-score drafts include none.

## Distribution

| Cluster | High | Mid | Low | Total |
| --- | ---: | ---: | ---: | ---: |
| SCIM / identity / IAM | 3 | 3 | 2 | 8 |
| React / frontend / Next.js | 2 | 2 | 1 | 5 |
| LLM / RAG / agents | 1 | 1 | 3 | 5 |
| Total | 6 | 6 | 6 | 18 |

## Daily Dev Workflow

`PHOENIX_QUERY_LOOKBACK_HOURS` defaults to 24 hours. For V8/V10 development,
re-seed the dev project at the start of the day:

```bash
python scripts/seed_phoenix.py --project job-hunt-agent-dev --allow-duplicates --verify
```

For the final demo project, seed only after local validation and human review:

```bash
python scripts/seed_phoenix.py --project job-hunt-agent --allow-duplicates --verify
```

## V9 Dependency

The eval implementation must write `job_hunt.eval.composite_score` onto the same
`draft_message` span as `job_hunt.draft.output_text`. If V9 only emits a child
eval span, V6 retrieval will return real-run drafts with `eval_score=None`.
