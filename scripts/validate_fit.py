#!/usr/bin/env python3
"""Calibrate deterministic resume-fit scoring against captured job fixtures."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from job_hunt_agent.matching import ResumeFitScorer
from job_hunt_agent.schemas import Role


MIN_MARGIN = 0.10


def main() -> int:
    resume = (ROOT / "fixtures/sample_resume.txt").read_text(encoding="utf-8")
    google_jobs = json.loads(
        (ROOT / "tests/fixtures/google_jobs_sample.json").read_text(encoding="utf-8")
    )
    backend_job = next(
        item for item in google_jobs["jobs_results"] if item["company_name"] == "MongoDB"
    )
    lever_jobs = json.loads(
        (ROOT / "tests/fixtures/adapters/lever.json").read_text(encoding="utf-8")
    )
    irrelevant_job = lever_jobs[0]
    roles = [
        Role(
            company=backend_job["company_name"],
            title=backend_job["title"],
            url=backend_job["apply_options"][0]["link"],
            location=backend_job["location"],
            summary=backend_job["description"][:300],
            match_reason="Unscored.",
            raw_description=backend_job["description"],
        ),
        Role(
            company="Palantir",
            title=irrelevant_job["text"],
            url=irrelevant_job["applyUrl"],
            location=irrelevant_job["categories"]["location"],
            summary=irrelevant_job["descriptionPlain"][:300],
            match_reason="Unscored.",
            raw_description=irrelevant_job["descriptionPlain"],
        ),
    ]
    ranked = ResumeFitScorer().rank_roles(resume, roles)
    scores = {role.company: role.fit_score or 0.0 for role in ranked}
    margin = scores["MongoDB"] - scores["Palantir"]
    print(
        f"backend={scores['MongoDB']:.4f} "
        f"irrelevant={scores['Palantir']:.4f} margin={margin:.4f}"
    )
    return int(margin < MIN_MARGIN)


if __name__ == "__main__":
    raise SystemExit(main())
