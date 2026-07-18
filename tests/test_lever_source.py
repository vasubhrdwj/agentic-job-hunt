import copy
import json
import logging
from pathlib import Path
from urllib.error import HTTPError, URLError
from unittest.mock import Mock

import pytest

from job_hunt_agent.schemas import (
    Company,
    CompanySource,
    EmploymentType,
    JobCriteria,
)
from job_hunt_agent.sources import lever
from job_hunt_agent.sources.base import SourceAdapter
from job_hunt_agent.sources.lever import LeverAdapter, LeverSourceAdapter
from job_hunt_agent.sources.resolver import SourceResolver


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "adapters" / "lever.json"


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class _FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


class _FakeSpanContext:
    def __init__(self, span: _FakeSpan) -> None:
        self.span = span

    def __enter__(self) -> _FakeSpan:
        return self.span

    def __exit__(self, *args: object) -> None:
        return None


class _FakeTracer:
    def __init__(self) -> None:
        self.spans: list[tuple[str, _FakeSpan]] = []

    def start_as_current_span(self, name: str) -> _FakeSpanContext:
        span = _FakeSpan()
        self.spans.append((name, span))
        return _FakeSpanContext(span)


@pytest.fixture(autouse=True)
def _forbid_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("Lever unit tests must not make network calls")

    monkeypatch.setattr(lever, "urlopen", fail_if_called)


@pytest.fixture
def fixture_payload() -> list[dict[str, object]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def company() -> Company:
    return Company(
        name="Palantir",
        slug="palantir",
        source=CompanySource.lever,
        source_token="palantir",
        careers_domains=["jobs.lever.co"],
        hire_locations=["Palo Alto, CA"],
        tags=["software"],
    )


@pytest.fixture
def criteria() -> JobCriteria:
    return JobCriteria(
        role_keywords=["automation"],
        seniority="mid",
        location=["Palo Alto, CA"],
        employment_types=[EmploymentType.full_time],
        country="us",
    )


def test_maps_recorded_lever_posting_without_network(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: list[dict[str, object]],
    company: Company,
    criteria: JobCriteria,
) -> None:
    urlopen = Mock(
        return_value=_Response(json.dumps(fixture_payload).encode("utf-8")),
    )
    monkeypatch.setattr(lever, "urlopen", urlopen)

    roles = LeverAdapter().fetch_open_roles(company, criteria)

    assert len(roles) == 1
    role = roles[0]
    assert role.company == "Palantir"
    assert role.title == "Administrative Business Partner - Security"
    assert role.url == (
        "https://jobs.lever.co/palantir/"
        "0bbfd4f4-41ff-4ec6-b73f-5200efd5d4d3/apply"
    )
    assert role.apply_urls == [
        role.url,
        (
            "https://jobs.lever.co/palantir/"
            "0bbfd4f4-41ff-4ec6-b73f-5200efd5d4d3"
        ),
    ]
    assert role.location == "Palo Alto, CA"
    assert "Core Responsibilities:" in role.summary
    assert "What We Value:" in role.summary
    assert "Palantir builds the world’s leading software" in role.raw_description
    assert "Core Responsibilities:" in role.raw_description
    assert "What We Value:" in role.raw_description
    assert role.employment_type is EmploymentType.full_time
    assert role.source is CompanySource.lever
    assert role.company_slug == "palantir"
    assert role.source_job_id == "0bbfd4f4-41ff-4ec6-b73f-5200efd5d4d3"
    assert role.confidence == 1.0
    assert role.posted_at is None
    assert 'requested keyword "automation"' in role.match_reason

    request = urlopen.call_args.args[0]
    assert request.full_url == "https://api.lever.co/v0/postings/palantir?mode=json"
    assert request.get_header("Accept") == "application/json"
    assert urlopen.call_args.kwargs == {"timeout": 20}


def test_fetch_records_token_safe_source_span(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: list[dict[str, object]],
    company: Company,
    criteria: JobCriteria,
) -> None:
    tracer = _FakeTracer()
    secret_token = "sensitive-board-token"
    payload = copy.deepcopy(fixture_payload)
    payload[0]["applyUrl"] = str(payload[0]["applyUrl"]).replace(
        "/palantir/",
        f"/{secret_token}/",
    )
    payload[0]["hostedUrl"] = str(payload[0]["hostedUrl"]).replace(
        "/palantir/",
        f"/{secret_token}/",
    )
    monkeypatch.setattr(lever, "TRACER", tracer)
    monkeypatch.setattr(
        lever,
        "urlopen",
        Mock(
            return_value=_Response(json.dumps(payload).encode("utf-8")),
        ),
    )

    roles = LeverAdapter().fetch_open_roles(
        company.model_copy(update={"source_token": secret_token}),
        criteria,
    )

    assert len(roles) == 1
    assert len(tracer.spans) == 1
    span_name, span = tracer.spans[0]
    assert span_name == "job_source.lever.fetch_open_roles"
    assert span.attributes == {
        "job_source.name": "lever",
        "job_source.company_slug": "palantir",
        "job_source.source_token_configured": True,
        "job_source.role_count": 1,
    }
    assert secret_token not in repr(span.attributes)


def test_uses_hosted_url_when_apply_url_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: list[dict[str, object]],
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = copy.deepcopy(fixture_payload)
    del payload[0]["applyUrl"]
    monkeypatch.setattr(
        lever,
        "urlopen",
        Mock(return_value=_Response(json.dumps(payload).encode("utf-8"))),
    )

    role = LeverAdapter().fetch_open_roles(company, criteria)[0]

    assert role.url.endswith("0bbfd4f4-41ff-4ec6-b73f-5200efd5d4d3")
    assert role.apply_urls == [role.url]


def test_rejects_non_lever_urls_in_postings(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: list[dict[str, object]],
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = copy.deepcopy(fixture_payload)
    payload[0]["applyUrl"] = "https://example.com/not-first-party"
    payload[0]["hostedUrl"] = "http://jobs.lever.co/insecure"
    monkeypatch.setattr(
        lever,
        "urlopen",
        Mock(return_value=_Response(json.dumps(payload).encode("utf-8"))),
    )

    with caplog.at_level(logging.WARNING):
        roles = LeverAdapter().fetch_open_roles(company, criteria)

    assert roles == []
    assert "missing trusted applyUrl or hostedUrl" in caplog.text


def test_rejects_cross_tenant_lever_urls(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: list[dict[str, object]],
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = copy.deepcopy(fixture_payload)
    payload[0]["applyUrl"] = "https://jobs.lever.co/other-company/123/apply"
    payload[0]["hostedUrl"] = "https://jobs.lever.co/other-company/123"
    monkeypatch.setattr(
        lever,
        "urlopen",
        Mock(return_value=_Response(json.dumps(payload).encode("utf-8"))),
    )

    assert LeverAdapter().fetch_open_roles(company, criteria) == []


@pytest.mark.parametrize("segment", ["..", "%2e%2e", "%252e%252e"])
def test_rejects_lever_path_traversal(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: list[dict[str, object]],
    company: Company,
    criteria: JobCriteria,
    segment: str,
) -> None:
    payload = copy.deepcopy(fixture_payload)
    payload[0]["applyUrl"] = (
        f"https://jobs.lever.co/palantir/{segment}/other/123/apply"
    )
    payload[0]["hostedUrl"] = (
        f"https://jobs.lever.co/palantir/{segment}/other/123"
    )
    monkeypatch.setattr(
        lever,
        "urlopen",
        Mock(return_value=_Response(json.dumps(payload).encode("utf-8"))),
    )

    assert LeverAdapter().fetch_open_roles(company, criteria) == []


@pytest.mark.parametrize(
    ("commitment", "expected"),
    [
        ("Full-time", EmploymentType.full_time),
        ("Regular Full Time (Salary)", EmploymentType.full_time),
        ("Contractor", EmploymentType.contract),
        ("6 month fixed-term", EmploymentType.contract),
        ("Internship", EmploymentType.intern),
        ("Co-op Intern", EmploymentType.intern),
        ("Part-time", EmploymentType.unknown),
        ("", EmploymentType.unknown),
    ],
)
def test_normalizes_lever_commitment(
    commitment: str,
    expected: EmploymentType,
) -> None:
    assert lever._normalize_employment_type(commitment) is expected


def test_supports_only_lever_companies_with_tokens(company: Company) -> None:
    adapter = LeverAdapter()

    assert isinstance(adapter, SourceAdapter)
    assert LeverSourceAdapter is LeverAdapter
    assert adapter.name == "lever"
    assert adapter.supports(company)
    assert not adapter.supports(company.model_copy(update={"source_token": None}))
    assert not adapter.supports(
        company.model_copy(update={"source": CompanySource.greenhouse}),
    )


@pytest.mark.parametrize(
    "criteria_update",
    [
        {"role_keywords": ["backend"]},
        {"seniority": "staff"},
        {"location": ["Hyderabad"]},
        {"employment_types": [EmploymentType.contract]},
    ],
)
def test_filters_only_on_lever_source_evidence(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: list[dict[str, object]],
    company: Company,
    criteria: JobCriteria,
    criteria_update: dict[str, object],
) -> None:
    monkeypatch.setattr(
        lever,
        "urlopen",
        Mock(return_value=_Response(json.dumps(fixture_payload).encode("utf-8"))),
    )

    roles = LeverAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update=criteria_update),
    )

    assert roles == []


def test_keyword_matching_requires_token_or_phrase_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: list[dict[str, object]],
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = copy.deepcopy(fixture_payload)
    payload[0]["text"] = "Platform Operations Engineer"
    payload[0]["descriptionPlain"] = "Support ongoing platform operations."
    payload[0]["lists"] = []
    monkeypatch.setattr(
        lever,
        "urlopen",
        Mock(return_value=_Response(json.dumps(payload).encode("utf-8"))),
    )

    substring_roles = LeverAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"role_keywords": ["go"]}),
    )

    payload[0]["descriptionPlain"] = "Build production services in Go."
    monkeypatch.setattr(
        lever,
        "urlopen",
        Mock(return_value=_Response(json.dumps(payload).encode("utf-8"))),
    )
    token_roles = LeverAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"role_keywords": ["go"]}),
    )

    assert substring_roles == []
    assert len(token_roles) == 1


def test_lists_empty_uses_additional_plain_for_search_and_full_description(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
) -> None:
    stable_money = company.model_copy(
        update={
            "name": "Stable Money",
            "slug": "stable-money",
            "source_token": "stable-money1",
            "hire_locations": ["India", "Bengaluru"],
        },
    )
    payload = [
        {
            "id": "bfcb73b0-353c-4b78-b489-02db4ccaa637",
            "text": "Software Engineer I (Backend)",
            "applyUrl": (
                "https://jobs.lever.co/stable-money1/"
                "bfcb73b0-353c-4b78-b489-02db4ccaa637/apply"
            ),
            "hostedUrl": (
                "https://jobs.lever.co/stable-money1/"
                "bfcb73b0-353c-4b78-b489-02db4ccaa637"
            ),
            "categories": {
                "commitment": "Full-time",
                "location": "Bengaluru, Karnataka, India",
            },
            "descriptionPlain": "Stable Money builds simple fixed-income products.",
            "lists": [],
            "additionalPlain": (
                "What you'll do: Build reliable APIs in Python. "
                "Requirements: 1-2 years of backend engineering experience."
            ),
        },
    ]
    monkeypatch.setattr(
        lever,
        "urlopen",
        Mock(return_value=_Response(json.dumps(payload).encode("utf-8"))),
    )

    roles = LeverAdapter().fetch_open_roles(
        stable_money,
        criteria.model_copy(
            update={
                "role_keywords": ["Python"],
                "seniority": "junior",
                "location": ["India"],
            },
        ),
    )

    assert len(roles) == 1
    role = roles[0]
    assert role.summary == (
        "Stable Money builds simple fixed-income products. "
        "What you'll do: Build reliable APIs in Python."
    )
    assert role.raw_description == (
        "Stable Money builds simple fixed-income products.\n\n"
        "What you'll do: Build reliable APIs in Python. Requirements: 1-2 years "
        "of backend engineering experience."
    )
    assert 'requested keyword "Python"' in role.match_reason


@pytest.mark.parametrize(
    ("experience_requirement", "expected_count"),
    [
        ("1-2 years", 1),
        ("3+ years", 0),
    ],
)
def test_resolver_reads_experience_from_additional_plain_when_lists_are_empty(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    experience_requirement: str,
    expected_count: int,
) -> None:
    payload = [
        {
            "id": "backend-1",
            "text": "Backend Engineer",
            "applyUrl": "https://jobs.lever.co/palantir/backend-1/apply",
            "hostedUrl": "https://jobs.lever.co/palantir/backend-1",
            "categories": {
                "commitment": "Full-time",
                "location": "Bengaluru, Karnataka, India",
            },
            "descriptionPlain": "Build reliable services.",
            "lists": [],
            "additionalPlain": (
                f"Requirements: {experience_requirement} of backend experience."
            ),
        },
    ]
    monkeypatch.setattr(
        lever,
        "urlopen",
        Mock(return_value=_Response(json.dumps(payload).encode("utf-8"))),
    )
    india_company = company.model_copy(update={"hire_locations": ["India"]})
    junior_criteria = JobCriteria(
        role_keywords=["backend"],
        seniority="junior",
        location=["India"],
        employment_types=[EmploymentType.full_time],
        country="in",
    )

    roles = SourceResolver([LeverAdapter()]).fetch_company_roles(
        india_company,
        junior_criteria,
        use_cache=False,
        allow_fallback=False,
    )

    assert len(roles) == expected_count


def test_full_description_dedupes_structured_and_plain_repetitions(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = [
        {
            "id": "dedupe-1",
            "text": "Automation Engineer",
            "applyUrl": "https://jobs.lever.co/palantir/dedupe-1/apply",
            "hostedUrl": "https://jobs.lever.co/palantir/dedupe-1",
            "categories": {
                "commitment": "Full-time",
                "location": "Palo Alto, CA",
            },
            "descriptionPlain": "Build automation for production teams.",
            "lists": [
                {
                    "text": "Requirements",
                    "content": "<ul><li>Two years of Python experience.</li></ul>",
                },
            ],
            "additionalPlain": "Two years of Python experience.",
        },
    ]
    monkeypatch.setattr(
        lever,
        "urlopen",
        Mock(return_value=_Response(json.dumps(payload).encode("utf-8"))),
    )

    role = LeverAdapter().fetch_open_roles(company, criteria)[0]

    assert role.raw_description == (
        "Build automation for production teams.\n\n"
        "Requirements: Two years of Python experience."
    )
    assert role.raw_description.count("Two years of Python experience.") == 1


def test_junior_filter_rejects_engineering_manager(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: list[dict[str, object]],
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = copy.deepcopy(fixture_payload)
    payload[0]["text"] = "Engineering Manager, Backend"
    monkeypatch.setattr(
        lever,
        "urlopen",
        Mock(return_value=_Response(json.dumps(payload).encode("utf-8"))),
    )

    roles = LeverAdapter().fetch_open_roles(
        company,
        criteria.model_copy(
            update={
                "role_keywords": [],
                "seniority": "junior",
            },
        ),
    )

    assert roles == []


def test_resolver_rejects_junior_sde_iii_with_structured_experience_requirement(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
) -> None:
    meesho = company.model_copy(
        update={
            "name": "Meesho",
            "slug": "meesho",
            "source_token": "meesho",
            "hire_locations": ["India", "Bengaluru"],
        }
    )
    payload = [
        {
            "id": "sde-iii-data",
            "text": "Software Development Engineer III Data",
            "applyUrl": "https://jobs.lever.co/meesho/sde-iii-data/apply",
            "hostedUrl": "https://jobs.lever.co/meesho/sde-iii-data",
            "categories": {
                "commitment": "Full-time",
                "location": "Bengaluru, Karnataka, India",
            },
            "descriptionPlain": "Build reliable data products at scale.",
            "lists": [
                {
                    "text": "Requirements",
                    "content": "<ul><li>5 - 8 yrs of relevant experience.</li></ul>",
                }
            ],
        }
    ]
    monkeypatch.setattr(
        lever,
        "urlopen",
        Mock(return_value=_Response(json.dumps(payload).encode("utf-8"))),
    )
    criteria = JobCriteria(
        role_keywords=["software"],
        seniority="junior",
        location=["India"],
        employment_types=[EmploymentType.full_time],
        country="in",
    )

    roles = SourceResolver([LeverAdapter()]).fetch_company_roles(
        meesho,
        criteria,
        use_cache=False,
        allow_fallback=False,
    )

    assert roles == []


def test_location_tokens_are_not_combined_across_distinct_locations(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: list[dict[str, object]],
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = copy.deepcopy(fixture_payload)
    payload[0]["categories"] = {
        "commitment": "Full-time",
        "location": "New York, NY",
        "allLocations": [
            "New York, NY",
            "Bengaluru, Karnataka, India",
        ],
    }
    monkeypatch.setattr(
        lever,
        "urlopen",
        Mock(return_value=_Response(json.dumps(payload).encode("utf-8"))),
    )

    mixed_location_roles = LeverAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"location": ["New York, India"]}),
    )
    actual_location_roles = LeverAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"location": ["Bengaluru, India"]}),
    )

    assert mixed_location_roles == []
    assert len(actual_location_roles) == 1


def test_all_locations_remain_atomic_when_primary_location_is_absent(
    monkeypatch: pytest.MonkeyPatch,
    fixture_payload: list[dict[str, object]],
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = copy.deepcopy(fixture_payload)
    payload[0]["categories"] = {
        "commitment": "Full-time",
        "allLocations": [
            "New York, NY",
            "Bengaluru, Karnataka, India",
        ],
    }
    monkeypatch.setattr(
        lever,
        "urlopen",
        Mock(return_value=_Response(json.dumps(payload).encode("utf-8"))),
    )

    synthetic_location_roles = LeverAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"location": ["New York, India"]}),
    )
    actual_location_roles = LeverAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"location": ["Bengaluru, India"]}),
    )

    assert synthetic_location_roles == []
    assert len(actual_location_roles) == 1
    assert actual_location_roles[0].location == (
        "New York, NY; Bengaluru, Karnataka, India"
    )


def test_missing_token_returns_empty_and_logs(
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
) -> None:
    company = company.model_copy(update={"source_token": None})

    with caplog.at_level(logging.WARNING):
        roles = LeverAdapter().fetch_open_roles(company, criteria)

    assert roles == []
    assert "has no source token" in caplog.text


def test_empty_payload_returns_empty_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(lever, "urlopen", Mock(return_value=_Response(b"[]")))

    with caplog.at_level(logging.INFO):
        roles = LeverAdapter().fetch_open_roles(company, criteria)

    assert roles == []
    assert "no published roles" in caplog.text


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            HTTPError(
                "https://api.lever.co/v0/postings/missing",
                404,
                "Not Found",
                {},
                None,
            ),
            "failed with HTTP 404",
        ),
        (URLError("offline"), "failed:"),
    ],
)
def test_http_errors_return_empty_and_log(
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

    monkeypatch.setattr(lever, "urlopen", raise_error)

    with caplog.at_level(logging.WARNING):
        roles = LeverAdapter().fetch_open_roles(company, criteria)

    assert roles == []
    assert message in caplog.text


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"{not-json", "malformed JSON"),
        (b'{"error": "not a list"}', "expected a list"),
    ],
)
def test_malformed_payload_returns_empty_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
    body: bytes,
    message: str,
) -> None:
    monkeypatch.setattr(lever, "urlopen", Mock(return_value=_Response(body)))

    with caplog.at_level(logging.WARNING):
        roles = LeverAdapter().fetch_open_roles(company, criteria)

    assert roles == []
    assert message in caplog.text
