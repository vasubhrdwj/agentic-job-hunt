#!/usr/bin/env python3
"""Validate a company pack and probe configured sources for the REG live gate."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from job_hunt_agent.schemas import Company, CompanySource
from job_hunt_agent.sources.registry import RegistryError, load_company_pack


DOD_COMMAND = (
    ".venv/bin/python scripts/verify_registry.py "
    "--pack backend_india --live --strict-live"
)
_IDENTITY_WORD = re.compile(r"[a-z0-9]+")
_GENERIC_IDENTITY_WORDS = frozenset(
    {
        "board",
        "career",
        "careers",
        "company",
        "corp",
        "corporation",
        "inc",
        "incorporated",
        "job",
        "jobs",
        "limited",
        "llc",
        "ltd",
        "private",
        "pvt",
        "software",
    }
)
_SMARTRECRUITERS_DETAIL_LIMIT = 5


@dataclass(frozen=True)
class LiveCheck:
    status: str
    count: int | None
    detail: str


def _request_json(
    url: str,
    *,
    timeout: float,
    data: dict[str, Any] | None = None,
) -> Any:
    encoded = json.dumps(data).encode() if data is not None else None
    request = Request(
        url,
        data=encoded,
        method="POST" if data is not None else "GET",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "job-hunt-registry-verifier/1.0",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def check_company_live(company: Company, *, timeout: float = 15.0) -> LiveCheck:
    """Probe one active company's configured source."""

    try:
        if company.source is CompanySource.greenhouse:
            payload = _request_json(
                "https://boards-api.greenhouse.io/v1/boards/"
                f"{company.source_token}/jobs",
                timeout=timeout,
            )
            return _validate_job_records(
                company,
                payload.get("jobs"),
                source="Greenhouse",
                url_fields=("absolute_url",),
                identity_fields=("company_name",),
            )

        if company.source is CompanySource.lever:
            payload = _request_json(
                f"https://api.lever.co/v0/postings/{company.source_token}?mode=json",
                timeout=timeout,
            )
            jobs = (
                [
                    job
                    for job in payload
                    if _lever_job_matches_token(company, job)
                ]
                if isinstance(payload, list)
                else payload
            )
            return _validate_job_records(
                company,
                jobs,
                source="Lever",
                url_fields=("hostedUrl", "applyUrl"),
            )

        if company.source is CompanySource.ashby:
            payload = _request_json(
                "https://api.ashbyhq.com/posting-api/job-board/"
                f"{company.source_token}",
                timeout=timeout,
            )
            return _validate_job_records(
                company,
                payload.get("jobs"),
                source="Ashby",
                url_fields=("jobUrl", "applyUrl"),
                identity_fields=("companyName", "organizationName"),
            )

        if company.source is CompanySource.smartrecruiters:
            return _check_smartrecruiters(company, timeout=timeout)

        if company.source is CompanySource.workable:
            payload = _request_json(
                "https://apply.workable.com/api/v3/accounts/"
                f"{company.source_token}/jobs",
                timeout=timeout,
                data={"query": ""},
            )
            jobs = payload.get("results", payload.get("jobs"))
            if not isinstance(jobs, list):
                return LiveCheck(
                    "dead",
                    None,
                    "Workable response did not contain a jobs list",
                )
            valid = sum(
                _workable_posting_matches_account(company, job)
                for job in jobs
            )
            if valid < 1:
                return LiveCheck(
                    "dead",
                    0,
                    "Workable returned no posting for the configured account",
                )
            return LiveCheck(
                "verified",
                valid,
                f"Workable validated {valid} posting(s) for the configured account",
            )

        if company.source is CompanySource.workday:
            return _check_workday(company, timeout=timeout)

        if company.source is CompanySource.bespoke and company.slug == "amazon":
            return _check_amazon(company, timeout=timeout)

        if company.source is CompanySource.google_jobs:
            return _check_google_jobs(company, timeout=timeout)

        return LiveCheck(
            "unverified",
            None,
            f"no generic live verifier for {company.source.value}",
        )
    except HTTPError as exc:
        return LiveCheck("dead", None, f"HTTP {exc.code}")
    except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return LiveCheck("dead", None, f"{type(exc).__name__}: {exc}")


def _check_smartrecruiters(company: Company, *, timeout: float) -> LiveCheck:
    payload = _request_json(
        "https://api.smartrecruiters.com/v1/companies/"
        f"{company.source_token}/postings?limit=100",
        timeout=timeout,
    )
    jobs = payload.get("content")
    total = payload.get("totalFound")
    if not isinstance(jobs, list) or not isinstance(total, int):
        return LiveCheck(
            "dead",
            None,
            "SmartRecruiters response lacked content or totalFound",
        )

    valid = 0
    rejected = 0
    for job in jobs[:_SMARTRECRUITERS_DETAIL_LIMIT]:
        if not isinstance(job, Mapping) or not _nested_company_matches(company, job):
            rejected += 1
            continue
        ref = job.get("ref")
        if not _smartrecruiters_ref_is_trusted(company, ref):
            rejected += 1
            continue
        detail = _request_json(str(ref), timeout=timeout)
        if _job_has_valid_evidence(
            company,
            detail,
            url_fields=("postingUrl", "applyUrl", "referralUrl"),
            identity_fields=(),
            nested_company=True,
        ):
            valid += 1
        else:
            rejected += 1

    if valid < 1:
        return LiveCheck(
            "dead",
            0,
            "SmartRecruiters returned no posting with trusted company/apply evidence",
        )
    return LiveCheck(
        "verified",
        valid,
        f"SmartRecruiters validated {valid} sampled posting(s) "
        f"from {total} open records; rejected={rejected}",
    )


def _check_workday(company: Company, *, timeout: float) -> LiveCheck:
    token = company.source_token or ""
    if ":" not in token:
        return LiveCheck("dead", None, "Workday token must be tenant:site")
    tenant, site = token.split(":", 1)
    host = next(
        (
            domain
            for domain in company.careers_domains
            if domain.endswith(".myworkdayjobs.com")
        ),
        None,
    )
    if host is None:
        return LiveCheck("dead", None, "Workday entry lacks a myworkdayjobs.com domain")
    payload = _request_json(
        f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
        timeout=timeout,
        data={"limit": 20, "offset": 0, "searchText": ""},
    )
    jobs = payload.get("jobPostings")
    if not isinstance(jobs, list):
        return LiveCheck("dead", None, "Workday response lacked jobPostings")

    valid = 0
    for job in jobs:
        if not isinstance(job, Mapping):
            continue
        path = job.get("externalPath")
        if not isinstance(path, str) or not path.startswith("/"):
            continue
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc:
            continue
        if _url_is_trusted(f"https://{host}{path}", company.careers_domains):
            valid += 1

    if valid < 1:
        return LiveCheck(
            "dead",
            0,
            "Workday returned no posting with a trusted careers path",
        )
    return LiveCheck(
        "verified",
        valid,
        f"Workday validated {valid} posting path(s)",
    )


def _check_google_jobs(company: Company, *, timeout: float) -> LiveCheck:
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        return LiveCheck(
            "unverified",
            None,
            "SERPAPI_API_KEY is not set; fallback was not queried",
        )
    query = urlencode(
        {
            "engine": "google_jobs",
            "q": f"{company.name} software engineer",
            "location": "India",
            "gl": "in",
            "hl": "en",
            "api_key": api_key,
        }
    )
    payload = _request_json(
        f"https://serpapi.com/search.json?{query}",
        timeout=timeout,
    )
    if payload.get("error"):
        return LiveCheck("dead", 0, str(payload["error"]))
    jobs = payload.get("jobs_results", [])
    if not isinstance(jobs, list):
        return LiveCheck("dead", None, "SerpAPI response lacked jobs_results")

    matching = 0
    for job in jobs:
        if not isinstance(job, Mapping):
            continue
        if not _company_identity_matches(company.name, job.get("company_name")):
            continue
        apply_options = job.get("apply_options")
        if not isinstance(apply_options, list):
            continue
        apply_urls = [
            option.get("link")
            for option in apply_options
            if isinstance(option, Mapping)
        ]
        if any(
            _url_is_trusted(url, company.careers_domains)
            for url in apply_urls
            if isinstance(url, str)
        ):
            matching += 1

    if matching < 1:
        return LiveCheck(
            "dead",
            0,
            "SerpAPI returned no job with matching company identity "
            "and a trusted apply URL",
        )
    return LiveCheck(
        "verified",
        matching,
        f"SerpAPI google_jobs validated {matching} posting(s)",
    )


def _check_amazon(company: Company, *, timeout: float) -> LiveCheck:
    query = urlencode(
        {
            "base_query": "software engineer",
            "normalized_country_code[]": "IND",
            "result_limit": 10,
            "offset": 0,
        },
        doseq=True,
    )
    payload = _request_json(
        f"https://www.amazon.jobs/en/search.json?{query}",
        timeout=timeout,
    )
    jobs = payload.get("jobs") if isinstance(payload, Mapping) else None
    if not isinstance(jobs, list):
        return LiveCheck("dead", None, "Amazon response lacked jobs")
    valid = 0
    for job in jobs:
        if not isinstance(job, Mapping):
            continue
        job_path = job.get("job_path")
        if not isinstance(job_path, str) or not job_path.startswith("/en/jobs/"):
            continue
        if _url_is_trusted(
            f"https://www.amazon.jobs{job_path}",
            company.careers_domains,
        ):
            valid += 1
    if valid < 1:
        return LiveCheck("dead", 0, "Amazon returned no trusted India posting")
    return LiveCheck(
        "verified",
        valid,
        f"Amazon validated {valid} sampled India posting(s)",
    )


def _validate_job_records(
    company: Company,
    jobs: Any,
    *,
    source: str,
    url_fields: tuple[str, ...],
    identity_fields: tuple[str, ...] = (),
) -> LiveCheck:
    if not isinstance(jobs, list):
        return LiveCheck("dead", None, f"{source} response did not contain a jobs list")

    valid = 0
    rejected = 0
    for job in jobs:
        if _job_has_valid_evidence(
            company,
            job,
            url_fields=url_fields,
            identity_fields=identity_fields,
        ):
            valid += 1
        else:
            rejected += 1

    if valid < 1:
        return LiveCheck(
            "dead",
            0,
            f"{source} returned no posting with trusted company/apply evidence",
        )
    return LiveCheck(
        "verified",
        valid,
        f"{source} validated {valid} open posting(s); rejected={rejected}",
    )


def _job_has_valid_evidence(
    company: Company,
    job: Any,
    *,
    url_fields: tuple[str, ...],
    identity_fields: tuple[str, ...],
    nested_company: bool = False,
) -> bool:
    if not isinstance(job, Mapping):
        return False

    urls = [
        job.get(field)
        for field in url_fields
        if isinstance(job.get(field), str) and str(job.get(field)).strip()
    ]
    if not urls or not all(
        _url_is_trusted(str(url), company.careers_domains) for url in urls
    ):
        return False

    if nested_company and not _nested_company_matches(company, job):
        return False

    identity_present = any(field in job for field in identity_fields)
    if identity_present:
        identities = [
            job.get(field)
            for field in identity_fields
            if isinstance(job.get(field), str)
        ]
        if not any(
            _company_identity_matches(company.name, identity)
            for identity in identities
        ):
            return False
    return True


def _nested_company_matches(company: Company, job: Mapping[str, Any]) -> bool:
    identity = job.get("company")
    if not isinstance(identity, Mapping):
        return False
    candidates = (identity.get("name"), identity.get("identifier"))
    return any(
        _company_identity_matches(company.name, candidate)
        for candidate in candidates
    )


def _smartrecruiters_ref_is_trusted(company: Company, value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    expected_path = f"/v1/companies/{company.source_token}/postings/"
    return (
        parsed.scheme == "https"
        and parsed.hostname == "api.smartrecruiters.com"
        and parsed.path.startswith(expected_path)
    )


def _lever_job_matches_token(company: Company, job: Any) -> bool:
    if not isinstance(job, Mapping) or not company.source_token:
        return False
    expected_prefix = f"/{company.source_token.casefold()}/"
    urls = (job.get("hostedUrl"), job.get("applyUrl"))
    matched = False
    for value in urls:
        if not isinstance(value, str):
            continue
        try:
            parsed = urlsplit(value)
        except ValueError:
            return False
        if parsed.scheme != "https" or parsed.hostname != "jobs.lever.co":
            return False
        if not parsed.path.casefold().startswith(expected_prefix):
            return False
        matched = True
    return matched


def _workable_posting_matches_account(company: Company, job: Any) -> bool:
    if not isinstance(job, Mapping) or not company.source_token:
        return False
    shortcode = job.get("shortcode")
    return (
        isinstance(shortcode, str)
        and bool(shortcode.strip())
        and job.get("isInternal") is not True
        and job.get("state") in (None, "published")
    )


def _url_is_trusted(value: str, domains: list[str]) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "\\" in value
        or any(character.isspace() for character in value)
        or port not in (None, 443)
    ):
        return False
    host = parsed.hostname.rstrip(".").casefold()
    return any(
        host == domain.casefold()
        or host.endswith(f".{domain.casefold()}")
        for domain in domains
    )


def _company_identity_matches(expected: str, observed: Any) -> bool:
    if not isinstance(observed, str) or not observed.strip():
        return False
    expected_words = _identity_words(expected)
    observed_words = _identity_words(observed)
    return bool(
        expected_words
        and observed_words
        and (
            expected_words.issubset(observed_words)
            or observed_words.issubset(expected_words)
        )
    )


def _identity_words(value: str) -> set[str]:
    return {
        word
        for word in _IDENTITY_WORD.findall(value.casefold())
        if word not in _GENERIC_IDENTITY_WORDS
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, epilog=f"REG DoD: {DOD_COMMAND}")
    parser.add_argument(
        "--pack",
        default="backend_india",
        help="Pack name or explicit .yaml path (default: backend_india)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Perform network checks. Required for the documented REG DoD.",
    )
    parser.add_argument(
        "--strict-live",
        action="store_true",
        help="With --live, fail when a source is unverified. Required for the REG DoD.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Per-request timeout for --live checks.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.strict_live and not args.live:
        print("ERROR: --strict-live requires --live", file=sys.stderr)
        return 2

    load_dotenv()
    try:
        registry = load_company_pack(args.pack)
    except RegistryError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1

    inactive = len(registry) - len(registry.active_companies)
    print(
        f"PACK {registry.name}: configured={len(registry)} "
        f"active={len(registry.active_companies)} inactive={inactive}"
    )

    if not args.live:
        for company in registry.companies:
            state = "active" if company.active else "inactive"
            token = company.source_token or "<null>"
            print(
                f"VALID {company.slug}: {state} source={company.source.value} "
                f"token={token}"
            )
        print(
            f"SUMMARY valid={len(registry)} invalid=0 "
            "live_checks=not_requested"
        )
        return 0

    totals = {"verified": 0, "unverified": 0, "dead": 0}
    for company in registry.active_companies:
        result = check_company_live(company, timeout=args.timeout)
        totals[result.status] += 1
        count = "n/a" if result.count is None else str(result.count)
        print(
            f"{result.status.upper()} {company.slug}: "
            f"source={company.source.value} open_roles={count} detail={result.detail}"
        )

    print(
        "SUMMARY "
        f"verified={totals['verified']} "
        f"unverified={totals['unverified']} "
        f"dead={totals['dead']}"
    )
    if totals["dead"] or (args.strict_live and totals["unverified"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
