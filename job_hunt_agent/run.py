"""Deterministic entry point for the job-hunt pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .schemas import HuntResult, JobCriteria, OutreachDraft, Person, Role
from .tools.registry import build_pipeline_tools


DEFAULT_MAX_ROLES = 3
DEFAULT_MAX_REFERRALS_PER_ROLE = 3


def run_hunt(
    resume_text: str,
    criteria: JobCriteria | dict[str, Any],
    *,
    max_roles: int = DEFAULT_MAX_ROLES,
    max_referrals_per_role: int = DEFAULT_MAX_REFERRALS_PER_ROLE,
    use_mocks: bool = False,
) -> HuntResult:
    """Run job search, referral discovery, and message drafting."""
    criteria = JobCriteria.model_validate(criteria)
    tools = build_pipeline_tools(use_mocks=use_mocks)

    roles = [
        Role.model_validate(role)
        for role in tools.search_jobs(criteria)
    ][:max_roles]

    outreach: list[OutreachDraft] = []
    for role in roles:
        people = [
            Person.model_validate(person)
            for person in tools.find_referrals(role)
        ][:max_referrals_per_role]

        for person in people:
            message = tools.draft_message(role, person, resume_text)
            outreach.append(
                OutreachDraft(
                    role=role,
                    person=person,
                    message=str(message).strip(),
                )
            )

    return HuntResult(roles=roles, outreach=outreach)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the job-hunt pipeline.")
    parser.add_argument("--resume", required=True, help="Path to a plain-text resume file.")
    parser.add_argument(
        "--keywords",
        required=True,
        help="Comma-separated role keywords, e.g. SCIM,IAM,identity.",
    )
    parser.add_argument(
        "--seniority",
        choices=["junior", "mid", "senior", "staff"],
        default="senior",
        help="Target seniority band.",
    )
    parser.add_argument(
        "--location",
        required=True,
        help="Comma-separated locations, e.g. Remote-India,Hyderabad.",
    )
    parser.add_argument("--comp-min-lpa", type=int, help="Optional minimum comp in LPA.")
    parser.add_argument("--comp-max-lpa", type=int, help="Optional maximum comp in LPA.")
    parser.add_argument("--max-roles", type=int, default=DEFAULT_MAX_ROLES)
    parser.add_argument("--max-referrals", type=int, default=DEFAULT_MAX_REFERRALS_PER_ROLE)
    parser.add_argument(
        "--use-mocks",
        action="store_true",
        help="Use the all-mock tool pipeline for fast local smoke tests.",
    )
    return parser.parse_args()


def criteria_from_args(args: argparse.Namespace) -> JobCriteria:
    """Build JobCriteria from CLI arguments."""
    return JobCriteria(
        role_keywords=_split_csv(args.keywords),
        seniority=args.seniority,
        location=_split_csv(args.location),
        comp_min_lpa=args.comp_min_lpa,
        comp_max_lpa=args.comp_max_lpa,
    )


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def main() -> None:
    args = parse_args()
    resume_text = Path(args.resume).read_text(encoding="utf-8")
    result = run_hunt(
        resume_text=resume_text,
        criteria=criteria_from_args(args),
        max_roles=args.max_roles,
        max_referrals_per_role=args.max_referrals,
        use_mocks=args.use_mocks,
    )
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
