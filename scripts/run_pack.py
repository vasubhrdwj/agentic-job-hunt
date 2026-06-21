#!/usr/bin/env python3
"""Run a company pack through SourceResolver and enforce the Phase-1 live gate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from job_hunt_agent.schemas import EmploymentType, JobCriteria
from job_hunt_agent.sources.registry import load_company_pack
from job_hunt_agent.sources.resolver import SourceResolver, is_first_party_role


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", default="backend_india")
    parser.add_argument(
        "--keywords",
        default="backend engineer,software engineer,backend developer",
    )
    parser.add_argument("--seniority", default="junior")
    parser.add_argument(
        "--locations",
        default="India,Remote-India,Bengaluru,Hyderabad",
    )
    parser.add_argument("--max-age-days", type=int, default=45)
    parser.add_argument("--max-per-company", type=int, default=3)
    parser.add_argument(
        "--max-companies",
        type=int,
        default=None,
        help="Limit the live gate to a representative first-party subset.",
    )
    parser.add_argument(
        "--sources",
        default="greenhouse,lever",
        help="Comma-separated source types included in this supply gate.",
    )
    parser.add_argument("--min-roles", type=int, default=10)
    parser.add_argument("--min-companies", type=int, default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    registry = load_company_pack(args.pack)
    wanted_sources = set(_csv(args.sources))
    if wanted_sources:
        registry = type(registry)(
            (
                company
                for company in registry.active_companies
                if company.source.value in wanted_sources
            ),
            name=registry.name,
            description=registry.description,
        )
    if args.max_companies is not None:
        registry = type(registry)(
            registry.active_companies[: max(0, args.max_companies)],
            name=registry.name,
            description=registry.description,
        )
    criteria = JobCriteria(
        role_keywords=_csv(args.keywords),
        seniority=args.seniority,
        location=_csv(args.locations),
        employment_types=[EmploymentType.full_time],
        max_age_days=args.max_age_days,
    )
    roles = SourceResolver().fetch_registry_roles(
        registry,
        criteria,
        max_per_company=args.max_per_company,
        use_cache=False,
        allow_fallback=False,
    )
    companies = {company.slug: company for company in registry.active_companies}
    first_party = [
        role
        for role in roles
        if (company := next(
            (
                entry
                for entry in companies.values()
                if entry.name.casefold() == role.company.casefold()
            ),
            None,
        ))
        and is_first_party_role(role, company)
    ]
    distinct_companies = {role.company for role in first_party}
    for role in first_party:
        print(
            f"ROLE company={role.company!r} title={role.title!r} "
            f"source={role.source.value} posted_at={role.posted_at!r} url={role.url}"
        )
    print(
        f"SUMMARY roles={len(roles)} first_party={len(first_party)} "
        f"companies={len(distinct_companies)}"
    )
    return int(
        len(first_party) < args.min_roles
        or len(distinct_companies) < args.min_companies
    )


if __name__ == "__main__":
    raise SystemExit(main())
