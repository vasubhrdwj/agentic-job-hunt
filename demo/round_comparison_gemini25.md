# Round comparison: baseline vs self-RAG

Generated 2026-06-10 17:46 UTC. Criteria keywords: SCIM, identity, IAM, OIDC. Drafter: gemini-2.5-flash (cross-model finding — production ships gemini-3.5-flash; weaker drafters start further from the exemplar ceiling, so the loop lifts them more). Judge: V9 composite (1-5) on gemini-2.5-flash, held constant.

| Round | Self-RAG | run_id | Avg score |
|---|---|---|--:|
| 1 | off | `7e080ce485ad445da2e0c3f95b79dbab` | **3.86** |
| 2 | on | `b05293ea193646bd8693eea8e6379871` | **4.67** |

**Gap: +0.81** (target: ≥ +0.7)

## Round 1 — baseline (no retrieval)

| # | Company | Person | Score |
|--:|---|---|--:|
| 1 | Komodo Health | Ben Engel-Streich | 3.75 |
| 2 | Komodo Health | Aishwarya Karpurapu | 3.75 |
| 3 | Komodo Health | Alex Soong | 4.00 |
| 4 | NextGen Identity Pty Ltd | Naseema Mohamed Jamal | 4.25 |
| 5 | NextGen Identity Pty Ltd | Keshav Bansal | 3.75 |
| 6 | NextGen Identity Pty Ltd | Akshay Dange | 4.00 |
| 7 | AHEAD | Grant Sewell | 4.00 |
| 8 | AHEAD | Brayden Park | 3.25 |
| 9 | AHEAD | Cory Carlson | 4.00 |

## Round 2 — with self-RAG exemplars

| # | Company | Person | Score |
|--:|---|---|--:|
| 1 | Komodo Health | Ben Engel-Streich | 4.25 |
| 2 | Komodo Health | Aishwarya Karpurapu | 4.75 |
| 3 | Komodo Health | Alex Soong | 4.75 |
| 4 | NextGen Identity Pty Ltd | Naseema Mohamed Jamal | 4.75 |
| 5 | NextGen Identity Pty Ltd | Keshav Bansal | 4.75 |
| 6 | NextGen Identity Pty Ltd | Akshay Dange | 4.50 |
| 7 | AHEAD | Grant Sewell | 4.75 |
| 8 | AHEAD | Brayden Park | 4.75 |
| 9 | AHEAD | Cory Carlson | 4.75 |

Round 2 drafts were written with the top-scoring past drafts for the same keywords retrieved from Phoenix traces as few-shot exemplars (`use_self_rag=True`). Same resume, same criteria, same judge.
