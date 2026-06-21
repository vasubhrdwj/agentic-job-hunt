"""Hermetic tests for the Google Jobs fallback source adapter."""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from unittest.mock import Mock

import pytest

from job_hunt_agent.schemas import (
    Company,
    CompanySource,
    EmploymentType,
    JobCriteria,
)
from job_hunt_agent.sources import google_jobs
from job_hunt_agent.sources.base import SourceAdapter
from job_hunt_agent.sources.google_jobs import (
    GoogleJobsAdapter,
    GoogleJobsSourceAdapter,
)
from job_hunt_agent.tools import job_search


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "google_jobs_sample.json"


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("Google Jobs adapter tests must not access the network")

    monkeypatch.setattr(job_search, "urlopen", fail_if_called)
    monkeypatch.setattr(job_search, "_load_dotenv_if_available", lambda: None)


@pytest.fixture
def fixture_payload() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def company() -> Company:
    return Company(
        name="MongoDB",
        slug="mongodb",
        source=CompanySource.google_jobs,
        source_token=None,
        careers_domains=["mongodb.com"],
        hire_locations=["India", "Remote"],
        tags=["backend"],
    )


@pytest.fixture
def criteria() -> JobCriteria:
    return JobCriteria(
        role_keywords=["Backend Engineer"],
        seniority="junior",
        location=["Remote-India"],
        employment_types=[EmploymentType.full_time],
    )


def _item_for(payload: dict[str, object], company_name: str) -> dict[str, object]:
    jobs = payload["jobs_results"]
    assert isinstance(jobs, list)
    return copy.deepcopy(
        next(item for item in jobs if item["company_name"] == company_name),
    )


def _install_payload(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> Mock:
    monkeypatch.setattr(job_search, "_get_serpapi_api_key", lambda: "fake-key")
    fetch = Mock(return_value=payload)
    monkeypatch.setattr(job_search, "_fetch_google_jobs", fetch)
    return fetch


def test_supports_any_company_and_satisfies_protocol(company: Company) -> None:
    adapter = GoogleJobsAdapter()

    assert isinstance(adapter, SourceAdapter)
    assert GoogleJobsSourceAdapter is GoogleJobsAdapter
    assert adapter.name == "google_jobs"
    assert adapter.supports(company)
    assert adapter.supports(
        company.model_copy(
            update={
                "source": CompanySource.greenhouse,
                "source_token": "mongodb",
            },
        ),
    )


def test_prefers_first_party_apply_url_and_maps_complete_contract(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: dict[str, object],
    company: Company,
    criteria: JobCriteria,
) -> None:
    item = _item_for(fixture_payload, "MongoDB")
    item["via"] = "MongoDB Careers"
    first_party = "https://www.mongodb.com/careers/jobs/123/backend-engineer"
    item["apply_options"].append(
        {"title": "MongoDB Careers", "link": first_party},
    )
    fetch = _install_payload(monkeypatch, {"jobs_results": [item]})

    roles = GoogleJobsAdapter().fetch_open_roles(company, criteria)

    assert len(roles) == 1
    role = roles[0]
    assert role.company == "MongoDB"
    assert role.title == "REMOTE (INDIA): Backend Engineer - SaaS platform"
    assert role.url == first_party
    assert role.apply_urls == [
        first_party,
        item["apply_options"][0]["link"],
        item["apply_options"][1]["link"],
    ]
    assert role.source is CompanySource.google_jobs
    assert role.posted_at == "1 day ago"
    assert role.employment_type is EmploymentType.full_time
    assert role.raw_description == item["description"]
    assert role.confidence == 0.8
    assert role.location == "Remote"

    assert fetch.call_count == 1
    assert fetch.call_args.kwargs == {
        "query": 'Backend Engineer remote "MongoDB"',
        "location": "India",
        "api_key": "fake-key",
    }


def test_filters_results_to_the_requested_company_without_substring_matches(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: dict[str, object],
    company: Company,
    criteria: JobCriteria,
) -> None:
    mongodb = _item_for(fixture_payload, "MongoDB")
    mongodb["company_name"] = "MongoDB India Ltd"
    mercor = _item_for(fixture_payload, "Mercor")
    mongo_consulting = copy.deepcopy(mongodb)
    mongo_consulting["company_name"] = "MongoDB Consulting"
    mongo_consulting["job_id"] = "different-job"
    _install_payload(
        monkeypatch,
        {"jobs_results": [mercor, mongo_consulting, mongodb]},
    )

    roles = GoogleJobsAdapter().fetch_open_roles(company, criteria)

    assert len(roles) == 1
    assert roles[0].company == "MongoDB"


@pytest.mark.parametrize(
    ("target", "observed"),
    [
        ("Insight", "Insight Global"),
        ("Acme", "Acme International"),
    ],
)
def test_company_filter_rejects_longer_unrelated_names(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: dict[str, object],
    criteria: JobCriteria,
    target: str,
    observed: str,
) -> None:
    item = _item_for(fixture_payload, "MongoDB")
    item["company_name"] = observed
    company = Company(
        name=target,
        slug=target.casefold(),
        source=CompanySource.google_jobs,
        source_token=None,
        careers_domains=[f"{target.casefold()}.example"],
    )
    _install_payload(monkeypatch, {"jobs_results": [item]})

    assert GoogleJobsAdapter().fetch_open_roles(company, criteria) == []


def test_hourly_contract_and_aggregator_via_are_low_confidence(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: dict[str, object],
) -> None:
    item = _item_for(fixture_payload, "Mercor")
    company = Company(
        name="Mercor",
        slug="mercor",
        source=CompanySource.google_jobs,
        source_token=None,
        careers_domains=["mercor.com"],
    )
    criteria = JobCriteria(
        role_keywords=["Backend Engineer"],
        seniority="mid",
        location=["Remote-India"],
        employment_types=[EmploymentType.contract],
    )
    _install_payload(monkeypatch, {"jobs_results": [item]})

    roles = GoogleJobsAdapter().fetch_open_roles(company, criteria)

    assert len(roles) == 1
    assert roles[0].employment_type is EmploymentType.contract
    assert roles[0].posted_at == "2 days ago"
    assert roles[0].confidence < 0.5
    assert roles[0].confidence == 0.35


def test_recruiter_or_aggregator_via_lowers_full_time_confidence(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: dict[str, object],
) -> None:
    item = _item_for(fixture_payload, "YMinds.AI")
    item["via"] = "Insight Global Staffing"
    company = Company(
        name="YMinds.AI",
        slug="yminds-ai",
        source=CompanySource.google_jobs,
        source_token=None,
        careers_domains=["yminds.ai"],
    )
    criteria = JobCriteria(
        role_keywords=["Backend Engineer"],
        seniority="mid",
        location=["Bengaluru"],
    )
    _install_payload(monkeypatch, {"jobs_results": [item]})

    role = GoogleJobsAdapter().fetch_open_roles(company, criteria)[0]

    assert role.employment_type is EmploymentType.full_time
    assert role.confidence < 0.5


def test_known_employment_type_mismatch_is_filtered(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: dict[str, object],
) -> None:
    item = _item_for(fixture_payload, "Mercor")
    company = Company(
        name="Mercor",
        slug="mercor",
        source=CompanySource.google_jobs,
        source_token=None,
    )
    criteria = JobCriteria(
        role_keywords=["Backend Engineer"],
        seniority="mid",
        location=["Remote-India"],
        employment_types=[EmploymentType.full_time],
    )
    _install_payload(monkeypatch, {"jobs_results": [item]})

    assert GoogleJobsAdapter().fetch_open_roles(company, criteria) == []


def test_dedupes_repeated_job_ids_across_queries(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: dict[str, object],
    company: Company,
    criteria: JobCriteria,
) -> None:
    item = _item_for(fixture_payload, "MongoDB")
    duplicate = copy.deepcopy(item)
    duplicate["title"] = "Python Backend Engineer"
    duplicate["apply_options"] = [item["apply_options"][1]]
    fetch = _install_payload(
        monkeypatch,
        {"jobs_results": [item, duplicate]},
    )
    criteria = criteria.model_copy(
        update={"location": ["Remote-India", "Bengaluru"]},
    )

    roles = GoogleJobsAdapter().fetch_open_roles(company, criteria)

    assert len(roles) == 1
    assert fetch.call_count == 2


def test_missing_credentials_returns_empty_without_fetching(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(job_search, "_get_serpapi_api_key", lambda: None)
    fetch = Mock(side_effect=AssertionError("fetch must not run without credentials"))
    monkeypatch.setattr(job_search, "_fetch_google_jobs", fetch)

    with caplog.at_level(logging.WARNING, logger=google_jobs.__name__):
        roles = GoogleJobsAdapter().fetch_open_roles(company, criteria)

    assert roles == []
    assert "missing" in caplog.text
    fetch.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"jobs_results": []},
        {"jobs_results": "not-a-list"},
        {"jobs_results": [None, "bad-item", {"company_name": "MongoDB"}]},
    ],
)
def test_empty_failed_or_malformed_payloads_return_honest_empty(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    company: Company,
    criteria: JobCriteria,
) -> None:
    _install_payload(monkeypatch, payload)

    assert GoogleJobsAdapter().fetch_open_roles(company, criteria) == []


def test_invalid_apply_urls_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: dict[str, object],
    company: Company,
    criteria: JobCriteria,
) -> None:
    item = _item_for(fixture_payload, "MongoDB")
    item["apply_options"] = [
        {"title": "bad", "link": "javascript:alert(1)"},
        {"title": "insecure", "link": "http://mongodb.com/jobs/1"},
    ]
    _install_payload(monkeypatch, {"jobs_results": [item]})

    assert GoogleJobsAdapter().fetch_open_roles(company, criteria) == []


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test\\@mongodb.com/jobs/1",
        "https://user@mongodb.com/jobs/1",
        "https://mongodb.com:bad/jobs/1",
        "https://mongodb.com:444/jobs/1",
        "https://mongodb.com/jobs/\n1",
    ],
)
def test_deceptive_first_party_urls_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: dict[str, object],
    company: Company,
    criteria: JobCriteria,
    url: str,
) -> None:
    item = _item_for(fixture_payload, "MongoDB")
    item["apply_options"] = [{"title": "MongoDB Careers", "link": url}]
    _install_payload(monkeypatch, {"jobs_results": [item]})

    assert GoogleJobsAdapter().fetch_open_roles(company, criteria) == []


def test_fetch_exception_returns_honest_empty(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(job_search, "_get_serpapi_api_key", lambda: "fake-key")
    monkeypatch.setattr(
        job_search,
        "_fetch_google_jobs",
        Mock(side_effect=RuntimeError("transport exploded")),
    )

    with caplog.at_level(logging.WARNING, logger=google_jobs.__name__):
        roles = GoogleJobsAdapter().fetch_open_roles(company, criteria)

    assert roles == []
    assert "transport exploded" in caplog.text


def test_trace_records_source_quality_counts(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: dict[str, object],
    company: Company,
    criteria: JobCriteria,
) -> None:
    item = _item_for(fixture_payload, "MongoDB")
    _install_payload(monkeypatch, {"jobs_results": [item]})
    recorded: dict[str, object] = {}

    class Span:
        def set_attribute(self, key: str, value: object) -> None:
            recorded[key] = value

    class SpanContext:
        def __enter__(self) -> Span:
            return Span()

        def __exit__(self, *args: object) -> None:
            del args

    class Tracer:
        def start_as_current_span(self, name: str) -> SpanContext:
            recorded["span_name"] = name
            return SpanContext()

    monkeypatch.setattr(google_jobs, "TRACER", Tracer())

    roles = GoogleJobsAdapter().fetch_open_roles(company, criteria)

    assert len(roles) == 1
    assert recorded == {
        "span_name": "job_source.google_jobs.fetch_open_roles",
        "job_source.name": "google_jobs",
        "job_source.company_slug": "mongodb",
        "job_source.query_count": 1,
        "job_source.role_count": 1,
        "job_source.first_party_role_count": 0,
        "job_source.low_confidence_role_count": 1,
    }
