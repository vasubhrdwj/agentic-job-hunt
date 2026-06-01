"""Upload synthetic past outreach drafts to Phoenix.

The seed data is append-only. Real uploads require ``--allow-duplicates`` so
the caller explicitly acknowledges that reruns create duplicate seed spans.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from job_hunt_agent.mcp_client import query_past_drafts  # noqa: E402
from job_hunt_agent.tracing import configure_phoenix_tracing  # noqa: E402
from opentelemetry import trace  # noqa: E402


DEFAULT_SEED_FILE = REPO_ROOT / "fixtures" / "seed_outreach.jsonl"
DEFAULT_PROJECT = "job-hunt-agent-dev"
VERIFY_CLUSTERS: dict[str, list[str]] = {
    "SCIM": ["SCIM"],
    "React": ["React"],
    "LLM": ["LLM"],
}
VERIFY_ATTEMPTS = 10
VERIFY_SLEEP_SECONDS = 2.0
FORCE_FLUSH_TIMEOUT_MILLIS = 10_000

OUTCOMES = Literal["replied", "no_reply", "introduced", "rejected", "pending"]
SCORE_BANDS = Literal["high", "mid", "low"]


class SeedDraft(BaseModel):
    """One synthetic draft to emit as a Phoenix ``draft_message`` span."""

    id: str
    role_company: str
    role_title: str
    role_keywords: list[str] = Field(min_length=1)
    person_name: str
    person_title: str
    message: str = Field(min_length=40)
    eval_score: float = Field(ge=1.0, le=5.0)
    outcome: OUTCOMES
    score_band: SCORE_BANDS
    pattern_tags: list[str]

    @model_validator(mode="after")
    def score_matches_band(self) -> "SeedDraft":
        if self.score_band == "high" and not 4.2 <= self.eval_score <= 5.0:
            raise ValueError("high seed scores must be between 4.2 and 5.0")
        if self.score_band == "mid" and not 2.8 <= self.eval_score <= 3.5:
            raise ValueError("mid seed scores must be between 2.8 and 3.5")
        if self.score_band == "low" and not 1.0 <= self.eval_score <= 2.0:
            raise ValueError("low seed scores must be between 1.0 and 2.0")
        return self


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed Phoenix with synthetic outreach drafts.")
    parser.add_argument("--seed-file", type=Path, default=DEFAULT_SEED_FILE)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Check seeded retrieval without uploading new seed spans.",
    )
    parser.add_argument(
        "--allow-duplicates",
        action="store_true",
        help="Required for real uploads because seed spans are append-only.",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.verify_only:
        return 0 if asyncio.run(verify_seed_retrieval(project=args.project)) else 1

    if args.dry_run:
        entries = load_seed_file(args.seed_file)
        print(f"Loaded {len(entries)} seed drafts from {args.seed_file}")
        for entry in entries:
            print(
                f"{entry.id}: {entry.score_band} {entry.eval_score:.1f} "
                f"{entry.role_keywords}"
            )
        if args.verify:
            print("dry-run: skipping Phoenix verification")
        return 0

    if not args.allow_duplicates:
        print(
            "Seed uploads are append-only. Re-running creates duplicate seed spans. "
            "Pass --allow-duplicates to upload.",
            file=sys.stderr,
        )
        return 2

    entries = load_seed_file(args.seed_file)
    print(f"Loaded {len(entries)} seed drafts from {args.seed_file}")

    if args.verbose:
        for entry in entries:
            print(
                f"{entry.id}: {entry.score_band} {entry.eval_score:.1f} "
                f"{entry.role_keywords}"
            )

    if not upload_seed_spans(entries, project=args.project):
        return 1

    if args.verify:
        return 0 if asyncio.run(verify_seed_retrieval(project=args.project)) else 1

    return 0


def load_seed_file(path: Path) -> list[SeedDraft]:
    entries: list[SeedDraft] = []
    with path.open(encoding="utf-8") as seed_file:
        for line_number, raw_line in enumerate(seed_file, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                entries.append(SeedDraft.model_validate_json(line))
            except ValidationError as exc:
                raise ValueError(f"invalid seed entry on line {line_number}: {exc}") from exc
    return entries


def upload_seed_spans(entries: list[SeedDraft], *, project: str) -> bool:
    provider = configure_phoenix_tracing(project_name=project)
    tracer = trace.get_tracer("job_hunt_agent.seed_phoenix")
    for entry in entries:
        emit_seed_span(entry, tracer)

    force_flush = getattr(provider, "force_flush", None)
    if force_flush is None:
        return True

    flushed = force_flush(timeout_millis=FORCE_FLUSH_TIMEOUT_MILLIS)
    if flushed is False:
        print("Phoenix force_flush timed out before all seed spans were exported.", file=sys.stderr)
        return False
    return True


def emit_seed_span(entry: SeedDraft, tracer: Any) -> None:
    with tracer.start_as_current_span("draft_message") as span:
        span.set_attribute("job_hunt.draft.output_text", entry.message)
        span.set_attribute("job_hunt.role.keywords", tuple(entry.role_keywords))
        span.set_attribute("job_hunt.role.title", entry.role_title)
        span.set_attribute("job_hunt.role.company", entry.role_company)
        span.set_attribute("job_hunt.eval.composite_score", float(entry.eval_score))
        span.set_attribute("job_hunt.seed.id", entry.id)
        span.set_attribute("job_hunt.seed.tag", "seed")
        span.set_attribute("job_hunt.seed.synthetic", True)
        span.set_attribute("job_hunt.seed.score_band", entry.score_band)
        span.set_attribute("job_hunt.outcome", entry.outcome)
        span.set_attribute("job_hunt.person.name", entry.person_name)
        span.set_attribute("job_hunt.person.title", entry.person_title)


async def verify_seed_retrieval(
    *,
    project: str,
    attempts: int = VERIFY_ATTEMPTS,
    sleep_seconds: float = VERIFY_SLEEP_SECONDS,
) -> bool:
    for attempt in range(1, attempts + 1):
        failures: list[str] = []
        for label, keywords in VERIFY_CLUSTERS.items():
            drafts = await query_past_drafts(keywords, top_k=3, project=project, timeout_s=3.0)
            if not drafts:
                failures.append(f"{label}: no drafts")
                continue
            top_score = drafts[0].eval_score or 0.0
            if top_score < 4.0:
                failures.append(f"{label}: top draft score below 4.0")

        if not failures:
            print(f"Verified seeded drafts in Phoenix project {project!r}.")
            return True

        if attempt < attempts:
            print(
                f"Verification attempt {attempt}/{attempts} pending: "
                f"{'; '.join(failures)}"
            )
            time.sleep(sleep_seconds)

    print(f"Verification failed for project {project!r}: {'; '.join(failures)}", file=sys.stderr)
    return False


def seed_entries_as_dicts(entries: list[SeedDraft]) -> list[dict[str, Any]]:
    """Small helper for ad hoc inspection and tests."""
    return [json.loads(entry.model_dump_json()) for entry in entries]


if __name__ == "__main__":
    raise SystemExit(main())
