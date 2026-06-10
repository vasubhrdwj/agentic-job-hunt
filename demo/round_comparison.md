# Round comparison: baseline vs self-RAG

Generated 2026-06-10 18:52 UTC. Criteria keywords: SCIM, identity, IAM, OIDC. Drafter: gemini-3.5-flash (production config). Judge: V9 composite (1-5) on gemini-2.5-flash, held constant.

| Round | Self-RAG | run_id | Avg score |
|---|---|---|--:|
| 1 | off | `c941c0aebd224118b3c5c38568a0835c` | **4.25** |
| 2 | on | `eb4581e66e95435eba23292b6736f8cd` | **4.64** |

**Gap: +0.39** (gate: ≥ +0.3 for the gemini-3.5-flash drafter; clean-corpus runs cluster at +0.36..+0.42. The same loop lifts the weaker gemini-2.5-flash drafter by +0.81 — see [round_comparison_gemini25.md](round_comparison_gemini25.md).)

## Round 1 — baseline (no retrieval)

| # | Company | Person | Score |
|--:|---|---|--:|
| 1 | Ovation Law Firm | Pavan Kumar S | 4.50 |
| 2 | Ovation Law Firm | Tej Bahadur Singh | 4.25 |
| 3 | Ovation Law Firm | Andrei Ivanov | 4.50 |
| 4 | Komodo Health | Ben Engel-Streich | 4.25 |
| 5 | Komodo Health | Aishwarya Karpurapu | 4.00 |
| 6 | Komodo Health | Alex Soong | 4.00 |
| 7 | NextGen Identity Pty Ltd | Naseema Mohamed Jamal | 4.25 |
| 8 | NextGen Identity Pty Ltd | Keshav Bansal | 4.25 |
| 9 | NextGen Identity Pty Ltd | Akshay Dange | 4.25 |

## Round 2 — with self-RAG exemplars

| # | Company | Person | Score |
|--:|---|---|--:|
| 1 | Ovation Law Firm | Pavan Kumar S | 4.75 |
| 2 | Ovation Law Firm | Tej Bahadur Singh | 4.50 |
| 3 | Ovation Law Firm | Andrei Ivanov | 4.50 |
| 4 | Komodo Health | Ben Engel-Streich | 4.75 |
| 5 | Komodo Health | Aishwarya Karpurapu | 4.75 |
| 6 | Komodo Health | Alex Soong | 4.50 |
| 7 | NextGen Identity Pty Ltd | Naseema Mohamed Jamal | 4.50 |
| 8 | NextGen Identity Pty Ltd | Keshav Bansal | 4.75 |
| 9 | NextGen Identity Pty Ltd | Akshay Dange | 4.75 |

Round 2 drafts were written with the top-scoring past drafts for the same keywords retrieved from Phoenix traces as few-shot exemplars (`use_self_rag=True`). Same resume, same criteria, same judge.
