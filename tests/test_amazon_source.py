"""Hermetic tests for the Amazon Jobs bespoke source adapter."""

from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock

import pytest

from job_hunt_agent.schemas import (
    Company,
    CompanySource,
    EmploymentType,
    JobCriteria,
)
from job_hunt_agent.sources import amazon
from job_hunt_agent.sources.amazon import AmazonAdapter, AmazonSourceAdapter
from job_hunt_agent.sources.base import SourceAdapter


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "adapters" / "amazon.json"
FIXED_NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        del args
        return None

    def read(self) -> bytes:
        return self.body


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("Amazon adapter unit tests must not access the network")

    monkeypatch.setattr(amazon, "urlopen", fail_if_called)
    monkeypatch.setattr(amazon, "_utc_now", lambda: FIXED_NOW)


@pytest.fixture
def fixture_payload() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def company() -> Company:
    return Company(
        name="Amazon",
        slug="amazon",
        source=CompanySource.bespoke,
        source_token="amazon",
        careers_domains=["amazon.jobs"],
        hire_locations=["India", "Bengaluru"],
        tags=["backend", "ecommerce", "cloud"],
    )


@pytest.fixture
def criteria() -> JobCriteria:
    return JobCriteria(
        role_keywords=["distributed systems"],
        seniority="junior",
        location=["Bengaluru"],
        employment_types=[EmploymentType.full_time],
        max_age_days=45,
        country="in",
    )


def _fixture_bytes() -> bytes:
    return FIXTURE_PATH.read_bytes()


def _install_response(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
) -> Mock:
    fake = Mock(return_value=_Response(body))
    monkeypatch.setattr(amazon, "urlopen", fake)
    return fake


def test_adapter_satisfies_protocol_and_supports_only_configured_amazon(
    company: Company,
) -> None:
    adapter = AmazonAdapter()

    assert isinstance(adapter, SourceAdapter)
    assert AmazonSourceAdapter is AmazonAdapter
    assert adapter.name == "bespoke"
    assert adapter.supports(company)
    assert adapter.supports(
        company.model_copy(update={"source_token": "amazon.jobs"}),
    )
    assert not adapter.supports(company.model_copy(update={"source_token": None}))
    assert not adapter.supports(company.model_copy(update={"slug": "other"}))
    assert not adapter.supports(
        company.model_copy(update={"source": CompanySource.google_jobs}),
    )


def test_maps_recorded_fixture_to_exact_role_contract(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
) -> None:
    urlopen = _install_response(monkeypatch, _fixture_bytes())

    roles = AmazonAdapter().fetch_open_roles(company, criteria)

    assert len(roles) == 1
    role = roles[0]
    assert role.company == "Amazon"
    assert role.title == "Software Development Engineer"
    assert role.url == "https://account.amazon.jobs/jobs/10454374/apply"
    assert role.apply_urls == [
        role.url,
        "https://www.amazon.jobs/en/jobs/10454374/software-development-engineer",
    ]
    assert role.location == "Bengaluru, Karnataka, IND"
    assert role.source is CompanySource.bespoke
    assert role.company_slug == "amazon"
    assert role.source_job_id == "10454374"
    assert role.posted_at == "2026-06-19"
    assert role.employment_type is EmploymentType.full_time
    assert role.confidence == 1.0
    assert role.raw_description
    assert "distributed systems" in role.raw_description
    assert "<br" not in role.raw_description
    assert "<br" not in role.summary
    assert '"distributed systems"' in role.match_reason
    assert "ADCI - Karnataka" in role.match_reason
    assert "Bengaluru" in role.match_reason

    request = urlopen.call_args.args[0]
    query = parse_qs(urlparse(request.full_url).query)
    assert urlparse(request.full_url)._replace(query="").geturl() == (
        amazon.AMAZON_SEARCH_ENDPOINT
    )
    assert query == {
        "base_query": ["distributed systems"],
        "offset": ["0"],
        "result_limit": [str(amazon.DEFAULT_RESULT_LIMIT)],
        "normalized_country_code[]": ["IND"],
    }
    assert request.get_header("Accept") == "application/json"
    assert urlopen.call_args.kwargs == {"timeout": amazon.DEFAULT_TIMEOUT_SECONDS}


def test_emits_source_trace_attributes(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
) -> None:
    _install_response(monkeypatch, _fixture_bytes())
    attributes: dict[str, object] = {}

    class _Span:
        def set_attribute(self, key: str, value: object) -> None:
            attributes[key] = value

    class _SpanContext:
        def __enter__(self) -> _Span:
            return _Span()

        def __exit__(self, *args: object) -> None:
            del args
            return None

    class _Tracer:
        def start_as_current_span(self, name: str) -> _SpanContext:
            attributes["span.name"] = name
            return _SpanContext()

    monkeypatch.setattr(amazon, "TRACER", _Tracer())

    roles = AmazonAdapter().fetch_open_roles(company, criteria)

    assert len(roles) == 1
    assert attributes == {
        "span.name": "job_source.amazon.fetch_open_roles",
        "job_source.name": "bespoke",
        "job_source.company_slug": "amazon",
        "job_source.role_count": 1,
    }


@pytest.mark.parametrize(
    "criteria_update",
    [
        {"role_keywords": ["frontend"]},
        {"location": ["Hyderabad"]},
        {"employment_types": [EmploymentType.contract]},
        {"max_age_days": 1},
    ],
)
def test_filters_only_on_source_evidence(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
    criteria_update: dict[str, object],
) -> None:
    _install_response(monkeypatch, _fixture_bytes())

    roles = AmazonAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update=criteria_update),
    )

    assert roles == []


def test_keyword_matching_requires_phrase_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: dict[str, object],
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = copy.deepcopy(fixture_payload)
    job = payload["jobs"][0]
    job["description"] = "Amazon supports ongoing database development."
    body = json.dumps(payload).encode()
    _install_response(monkeypatch, body)

    false_positive = AmazonAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"role_keywords": ["go"]}),
    )

    _install_response(monkeypatch, body)
    job["description"] = "Build services in Go for distributed systems."
    _install_response(monkeypatch, json.dumps(payload).encode())
    exact_token = AmazonAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"role_keywords": ["go"]}),
    )

    assert false_positive == []
    assert len(exact_token) == 1


@pytest.mark.parametrize(
    "title",
    [
        "Senior Software Development Engineer",
        "Software Development Engineer II",
        "SDE 2",
        "Staff Backend Engineer",
        "Software Development Manager",
    ],
)
def test_junior_filter_rejects_advanced_and_nonjunior_titles(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: dict[str, object],
    company: Company,
    criteria: JobCriteria,
    title: str,
) -> None:
    payload = copy.deepcopy(fixture_payload)
    payload["jobs"][0]["title"] = title
    _install_response(monkeypatch, json.dumps(payload).encode())

    roles = AmazonAdapter().fetch_open_roles(company, criteria)

    assert roles == []


def test_location_tokens_are_not_combined_across_distinct_locations(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: dict[str, object],
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = copy.deepcopy(fixture_payload)
    job = payload["jobs"][0]
    job["location"] = ""
    job["normalized_location"] = ""
    job["city"] = ""
    job["state"] = ""
    job["country_code"] = ""
    job["locations"] = [
        json.dumps(
            {
                "location": "US, Virtual",
                "normalizedCountryName": "United States",
                "type": "VIRTUAL",
            },
        ),
        json.dumps(
            {
                "location": "IN, KA, Bengaluru",
                "normalizedCountryName": "India",
                "type": "ONSITE",
            },
        ),
    ]
    body = json.dumps(payload).encode()

    _install_response(monkeypatch, body)
    mixed = AmazonAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"location": ["Remote-India"]}),
    )
    _install_response(monkeypatch, body)
    remote_us = AmazonAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"location": ["Remote-US"]}),
    )
    _install_response(monkeypatch, body)
    bengaluru = AmazonAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"location": ["Bengaluru-India"]}),
    )

    assert mixed == []
    assert len(remote_us) == 1
    assert len(bengaluru) == 1


@pytest.mark.parametrize(
    ("schedule", "is_intern", "expected"),
    [
        ("full-time", None, EmploymentType.full_time),
        ("regular full time", None, EmploymentType.full_time),
        ("contractor", None, EmploymentType.contract),
        ("seasonal", None, EmploymentType.contract),
        ("full-time", True, EmploymentType.intern),
        ("internship", None, EmploymentType.intern),
        ("part-time", None, EmploymentType.unknown),
    ],
)
def test_normalizes_employment_type(
    schedule: str,
    is_intern: bool | None,
    expected: EmploymentType,
) -> None:
    assert (
        amazon._employment_type(
            {
                "job_schedule_type": schedule,
                "is_intern": is_intern,
            },
        )
        is expected
    )


def test_fixed_term_title_overrides_full_time_schedule() -> None:
    assert (
        amazon._employment_type(
            {
                "title": "Software Development Engineer - FTC",
                "job_schedule_type": "full-time",
            },
        )
        is EmploymentType.contract
    )


def test_falls_back_to_public_job_page_when_apply_url_is_untrusted(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: dict[str, object],
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = copy.deepcopy(fixture_payload)
    payload["jobs"][0]["url_next_step"] = "https://jobs.example.com/fake"
    _install_response(monkeypatch, json.dumps(payload).encode())

    role = AmazonAdapter().fetch_open_roles(company, criteria)[0]

    assert role.url == (
        "https://www.amazon.jobs/en/jobs/10454374/software-development-engineer"
    )
    assert role.apply_urls == [role.url]


def test_rejects_job_without_any_trusted_first_party_url(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: dict[str, object],
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = copy.deepcopy(fixture_payload)
    payload["jobs"][0]["url_next_step"] = "https://jobs.example.com/fake"
    payload["jobs"][0]["job_path"] = "//jobs.example.com/fake"
    _install_response(monkeypatch, json.dumps(payload).encode())

    with caplog.at_level(logging.WARNING, logger=amazon.__name__):
        roles = AmazonAdapter().fetch_open_roles(company, criteria)

    assert roles == []
    assert "missing trusted first-party apply URL" in caplog.text


@pytest.mark.parametrize("posted_date", [None, "recently"])
def test_unverifiable_posted_date_is_preserved_as_unknown_freshness(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: dict[str, object],
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
    posted_date: str | None,
) -> None:
    payload = copy.deepcopy(fixture_payload)
    payload["jobs"][0]["posted_date"] = posted_date
    _install_response(monkeypatch, json.dumps(payload).encode())

    with caplog.at_level(logging.WARNING, logger=amazon.__name__):
        roles = AmazonAdapter().fetch_open_roles(company, criteria)

    assert len(roles) == 1
    assert roles[0].posted_at is None
    assert "unknown freshness" in caplog.text
    assert "posted_date is missing or invalid" in caplog.text


def test_accepts_role_exactly_on_max_age_date_boundary(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: dict[str, object],
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = copy.deepcopy(fixture_payload)
    payload["jobs"][0]["posted_date"] = "May 07, 2026"
    _install_response(monkeypatch, json.dumps(payload).encode())

    roles = AmazonAdapter().fetch_open_roles(company, criteria)

    assert len(roles) == 1
    assert roles[0].posted_at == "2026-05-07"


def test_max_age_none_allows_role_without_parseable_date(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: dict[str, object],
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = copy.deepcopy(fixture_payload)
    payload["jobs"][0]["posted_date"] = "recently"
    _install_response(monkeypatch, json.dumps(payload).encode())

    roles = AmazonAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"max_age_days": None}),
    )

    assert len(roles) == 1
    assert roles[0].posted_at is None


def test_deduplicates_same_source_job_across_queries(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
) -> None:
    urlopen = _install_response(monkeypatch, _fixture_bytes())

    roles = AmazonAdapter().fetch_open_roles(
        company,
        criteria.model_copy(
            update={
                "role_keywords": ["distributed systems", "distributed"],
            },
        ),
    )

    assert len(roles) == 1
    assert urlopen.call_count == 2


def test_empty_payload_returns_empty_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _install_response(monkeypatch, b'{"hits": 0, "jobs": [], "error": null}')

    with caplog.at_level(logging.INFO, logger=amazon.__name__):
        roles = AmazonAdapter().fetch_open_roles(company, criteria)

    assert roles == []
    assert "returned no published roles" in caplog.text


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            HTTPError(
                amazon.AMAZON_SEARCH_ENDPOINT,
                503,
                "Unavailable",
                {},
                None,
            ),
            "HTTP 503",
        ),
        (URLError("offline"), "request failed"),
        (TimeoutError("slow"), "request failed"),
    ],
)
def test_network_errors_return_empty_and_log(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    message: str,
) -> None:
    def raise_error(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise error

    monkeypatch.setattr(amazon, "urlopen", raise_error)

    with caplog.at_level(logging.WARNING, logger=amazon.__name__):
        roles = AmazonAdapter().fetch_open_roles(company, criteria)

    assert roles == []
    assert message in caplog.text


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"{not-json", "malformed JSON"),
        (b"[]", "expected an object"),
        (b'{"jobs": {}}', "jobs is not a list"),
        (b'{"error": "temporarily unavailable", "jobs": []}', "returned an error"),
        (b'{"jobs": [null]}', "expected an object"),
    ],
)
def test_malformed_or_error_payload_returns_empty_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
    body: bytes,
    message: str,
) -> None:
    _install_response(monkeypatch, body)

    with caplog.at_level(logging.WARNING, logger=amazon.__name__):
        roles = AmazonAdapter().fetch_open_roles(company, criteria)

    assert roles == []
    assert message in caplog.text


def test_unconfigured_company_returns_empty_without_network(
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=amazon.__name__):
        roles = AmazonAdapter().fetch_open_roles(
            company.model_copy(update={"source_token": None}),
            criteria,
        )

    assert roles == []
    assert "not configured" in caplog.text
