"""V10: the eval-score-climbing comparison.

Runs the full pipeline twice against the same resume + criteria:

    round 1: use_self_rag=False  (baseline -- no access to past drafts)
    round 2: use_self_rag=True   (retrieves high-scoring exemplars from Phoenix)

Every draft is scored by the V9 judge inside run_hunt(). The script emits:

    demo/round_comparison.md    per-message scores + per-round averages
    demo/round_comparison.png   grouped bar chart of the same numbers
    demo/canonical_run.json     round-2 HuntResult (hosting-outage insurance)

Usage (live, writes Phoenix traces when --trace is set):

    PHOENIX_QUERY_LOOKBACK_HOURS=720 python scripts/compare_rounds.py --trace

Exits non-zero if the round-2 average does not beat round 1 by --min-gap
(default +0.7, per PLAN.md V10): that is a stop-and-tune-the-seed-data
signal, not a formatting problem.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_hunt_agent.run import run_hunt
from job_hunt_agent.schemas import HuntResult, JobCriteria


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MIN_GAP = 0.7


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A/B the self-RAG loop (V10).")
    parser.add_argument(
        "--resume",
        default=str(REPO_ROOT / "fixtures" / "sample_resume.txt"),
        help="Plain-text resume file both rounds share.",
    )
    parser.add_argument(
        "--criteria",
        default=str(REPO_ROOT / "fixtures" / "sample_criteria_scim.json"),
        help="JobCriteria JSON file both rounds share.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(REPO_ROOT / "demo"),
        help="Where round_comparison.md/.png and canonical_run.json land.",
    )
    parser.add_argument("--max-roles", type=int, default=3)
    parser.add_argument("--max-referrals", type=int, default=3)
    parser.add_argument(
        "--min-gap",
        type=float,
        default=DEFAULT_MIN_GAP,
        help="Required round2-minus-round1 average gap (PLAN target: 0.7).",
    )
    parser.add_argument(
        "--use-mocks",
        action="store_true",
        help="Offline smoke mode: mock tools + deterministic mock judge.",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Emit Phoenix spans for both rounds (use for the demo runs).",
    )
    parser.add_argument(
        "--no-canonical",
        action="store_true",
        help="Skip writing canonical_run.json.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    resume_text = Path(args.resume).read_text(encoding="utf-8")
    criteria = JobCriteria.model_validate_json(
        Path(args.criteria).read_text(encoding="utf-8")
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rounds: list[HuntResult] = []
    for label, use_self_rag in (("round 1 (no RAG)", False), ("round 2 (self-RAG)", True)):
        print(f"Running {label} ...", flush=True)
        result = run_hunt(
            resume_text=resume_text,
            criteria=criteria,
            max_roles=args.max_roles,
            max_referrals_per_role=args.max_referrals,
            use_mocks=args.use_mocks,
            use_self_rag=use_self_rag,
            enable_tracing=args.trace,
        )
        scored = [d.eval_score for d in result.outreach if d.eval_score is not None]
        print(
            f"  run_id={result.run_id}  drafts={len(result.outreach)}  "
            f"scored={len(scored)}  avg={_avg(scored):.2f}",
            flush=True,
        )
        rounds.append(result)

    round1, round2 = rounds
    avg1 = _avg([d.eval_score for d in round1.outreach if d.eval_score is not None])
    avg2 = _avg([d.eval_score for d in round2.outreach if d.eval_score is not None])
    gap = round(avg2 - avg1, 2)

    markdown_path = output_dir / "round_comparison.md"
    markdown_path.write_text(
        _render_markdown(round1, round2, avg1, avg2, gap, criteria),
        encoding="utf-8",
    )
    print(f"Wrote {markdown_path}")

    png_path = output_dir / "round_comparison.png"
    _render_chart(round1, round2, avg1, avg2, gap, png_path)
    print(f"Wrote {png_path}")

    if not args.no_canonical:
        canonical_path = output_dir / "canonical_run.json"
        canonical_path.write_text(
            round2.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        print(f"Wrote {canonical_path}")

    print(f"\nround 1 avg: {avg1:.2f}   round 2 avg: {avg2:.2f}   gap: {gap:+.2f}")
    if gap < args.min_gap:
        print(
            f"GAP BELOW TARGET (+{args.min_gap}): stop and tune the seed data / "
            "exemplar prompt before recording the demo (PLAN.md V10).",
        )
        return 1
    print(f"Gap meets the +{args.min_gap} target.")
    return 0


def _avg(scores: list[float]) -> float:
    return sum(scores) / len(scores) if scores else 0.0


def _score_cell(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "—"


def _round_table(result: HuntResult) -> list[str]:
    lines = [
        "| # | Company | Person | Score |",
        "|--:|---|---|--:|",
    ]
    for index, draft in enumerate(result.outreach, start=1):
        lines.append(
            f"| {index} | {draft.role.company} | {draft.person.name} | "
            f"{_score_cell(draft.eval_score)} |"
        )
    return lines


def _render_markdown(
    round1: HuntResult,
    round2: HuntResult,
    avg1: float,
    avg2: float,
    gap: float,
    criteria: JobCriteria,
) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Round comparison: baseline vs self-RAG",
        "",
        f"Generated {generated}. Criteria keywords: "
        f"{', '.join(criteria.role_keywords)}. Judge: V9 composite (1-5).",
        "",
        f"| Round | Self-RAG | run_id | Avg score |",
        f"|---|---|---|--:|",
        f"| 1 | off | `{round1.run_id}` | **{avg1:.2f}** |",
        f"| 2 | on | `{round2.run_id}` | **{avg2:.2f}** |",
        "",
        f"**Gap: {gap:+.2f}** (target: ≥ +{DEFAULT_MIN_GAP})",
        "",
        "## Round 1 — baseline (no retrieval)",
        "",
        *_round_table(round1),
        "",
        "## Round 2 — with self-RAG exemplars",
        "",
        *_round_table(round2),
        "",
        "Round 2 drafts were written with the top-scoring past drafts for the "
        "same keywords retrieved from Phoenix traces as few-shot exemplars "
        "(`use_self_rag=True`). Same resume, same criteria, same judge.",
        "",
    ]
    return "\n".join(lines)


def _render_chart(
    round1: HuntResult,
    round2: HuntResult,
    avg1: float,
    avg2: float,
    gap: float,
    path: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    scores1 = [d.eval_score or 0.0 for d in round1.outreach]
    scores2 = [d.eval_score or 0.0 for d in round2.outreach]
    count = max(len(scores1), len(scores2))
    scores1 += [0.0] * (count - len(scores1))
    scores2 += [0.0] * (count - len(scores2))
    positions = list(range(1, count + 1))
    width = 0.38

    fig, ax = plt.subplots(figsize=(10, 5), dpi=150)
    ax.bar(
        [p - width / 2 for p in positions], scores1, width,
        label=f"Round 1 — no RAG (avg {avg1:.2f})", color="#9aa5b1",
    )
    ax.bar(
        [p + width / 2 for p in positions], scores2, width,
        label=f"Round 2 — self-RAG (avg {avg2:.2f})", color="#2f6fed",
    )
    ax.axhline(avg1, color="#9aa5b1", linestyle="--", linewidth=1)
    ax.axhline(avg2, color="#2f6fed", linestyle="--", linewidth=1)
    ax.set_xlabel("Draft #")
    ax.set_ylabel("Judge composite score (1–5)")
    ax.set_ylim(0, 5.4)
    ax.set_xticks(positions)
    ax.set_title(
        f"Outreach quality climbs when the agent reads its own traces "
        f"(gap {gap:+.2f})"
    )
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())
