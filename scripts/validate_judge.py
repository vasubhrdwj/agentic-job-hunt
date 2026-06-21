"""V9 judge calibration against handwritten reference messages.

Run this BEFORE trusting eval scores or recording the V10 comparison:

    python scripts/validate_judge.py

Requires GOOGLE_API_KEY (from .env or the environment). Pass criteria, per
PLAN.md V9: every "good" reference must score >= 4.0 composite and every
"bad" reference <= 2.0. If this fails, fix the judge prompt in
job_hunt_agent/evals.py -- do not proceed to V10 with a judge that cannot
tell obviously-good from obviously-bad apart.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_hunt_agent.evals import score_draft
from job_hunt_agent.schemas import Person, Role


REFERENCES_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "judge_references.jsonl"
GOOD_MIN = 4.0
BAD_MAX = 2.0

ROLE = Role(
    company="Postman",
    title="Software Engineer, Authorization",
    url="https://job-boards.greenhouse.io/postman/jobs/7776298003",
    location="Bengaluru",
    summary=(
        "Build reliable backend authorization services and APIs for a "
        "developer platform."
    ),
    match_reason="Listing names backend authorization, APIs, and platform reliability.",
)
PERSON = Person(
    name="Anika Rao",
    title="Staff Software Engineer, Platform",
    company="Postman",
    profile_url="https://example.invalid/calibration/anika-rao",
    source="linkedin",
    why_relevant="Calibration contact adjacent to the backend platform role.",
    verified_current_employer=True,
    confidence=1.0,
)


def main() -> int:
    entries = [
        json.loads(line)
        for line in REFERENCES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    failures: list[str] = []

    print(f"{'id':<24} {'band':<5} {'pers':>4} {'spec':>4} {'ask':>4} {'tone':>4} "
          f"{'comp':>5} {'sec':>5}  verdict")
    print("-" * 88)

    for entry in entries:
        started = time.perf_counter()
        result = score_draft(ROLE, PERSON, entry["message"])
        elapsed = time.perf_counter() - started

        if result is None:
            failures.append(f"{entry['id']}: judge returned None (auth/network?)")
            print(f"{entry['id']:<24} {entry['band']:<5} {'--':>4} {'--':>4} {'--':>4} "
                  f"{'--':>4} {'--':>5} {elapsed:>5.1f}  ERROR")
            continue

        if entry["band"] == "good":
            passed = result.composite >= GOOD_MIN
            bar = f">= {GOOD_MIN}"
        else:
            passed = result.composite <= BAD_MAX
            bar = f"<= {BAD_MAX}"
        verdict = "PASS" if passed else f"FAIL (need {bar})"
        if not passed:
            failures.append(f"{entry['id']}: composite {result.composite} not {bar}")
        if elapsed > 2.0:
            print(f"  note: {entry['id']} took {elapsed:.1f}s (> 2s latency target)")

        print(f"{entry['id']:<24} {entry['band']:<5} {result.personalization:>4} "
              f"{result.specificity:>4} {result.ask:>4} {result.tone:>4} "
              f"{result.composite:>5.2f} {elapsed:>5.1f}  {verdict}")

    print("-" * 88)
    if failures:
        print(f"JUDGE NOT CALIBRATED — {len(failures)} failure(s):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Judge calibrated: all good references >= 4.0, all bad references <= 2.0.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
