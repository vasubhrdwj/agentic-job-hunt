"""Hermetic tests for the Workday CXS source adapter."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError

import pytest

from job_hunt_agent.schemas import (
    Company,
    CompanySource,
    EmploymentType,
    JobCriteria,
)
from job_hunt_agent.sources import ashby as common
from job_hunt_agent.sources.base import SourceAdapter
from job_hunt_agent.sources.workday import WorkdayAdapter, WorkdaySourceAdapter
import job_hunt_agent.sources.workday as workday


FIXTURE = Path(__file__).parent / "fixtures" / "adapters" / "workday.json"
NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)


class _Response:
    def __init__(self, payload: object) -> None:
        self.body = json.dumps(payload).encode()

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def read(self) -> bytes:
        return self.body


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("Workday tests must not access the network")

    monkeypatch.setattr(common, "urlopen", fail)
    monkeypatch.setattr(common, "_utc_now", lambda: NOW)


@pytest.fixture
def fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def company() -> Company:
    return Company(
        name="BrowserStack",
        slug="browserstack",
        source=CompanySource.workday,
        source_token="browserstack:External",
        careers_domains=[
            "browserstack.com",
            "browserstack.wd3.myworkdayjobs.com",
        ],
        hire_locations=["India"],
        tags=["developer-tools"],
    )


@pytest.fixture
def criteria() -> JobCriteria:
    return JobCriteria(
        role_keywords=["strategic customers"],
        seniority="mid",
        location=["Mumbai India"],
        employment_types=[EmploymentType.full_time],
    )


def _install_router(
    monkeypatch: pytest.MonkeyPatch,
    fixture: dict[str, object],
) -> list[object]:
    calls: list[object] = []

    def fake(request: object, *, timeout: int) -> _Response:
        calls.append((request, timeout))
        if request.full_url.endswith("/jobs"):
            return _Response(fixture["list"])
        path = request.full_url.split("/External", 1)[1]
        return _Response(fixture["details"][path])

    monkeypatch.setattr(common, "urlopen", fake)
    return calls


def test_maps_real_fixture_and_request_shape(
    monkeypatch: pytest.MonkeyPatch,
    fixture: dict[str, object],
    company: Company,
    criteria: JobCriteria,
) -> None:
    calls = _install_router(monkeypatch, fixture)

    roles = WorkdayAdapter().fetch_open_roles(company, criteria)

    assert len(roles) == 1
    role = roles[0]
    assert role.title == "Account Manager - Strategic Sales"
    assert role.url.startswith(
        "https://browserstack.wd3.myworkdayjobs.com/External/job/",
    )
    assert role.location == "Mumbai Remote"
    assert role.posted_at == "2026-06-20"
    assert role.employment_type is EmploymentType.full_time
    assert role.source is CompanySource.workday
    assert role.raw_description and "strategic customers" in role.raw_description
    list_request, timeout = calls[0]
    assert list_request.get_method() == "POST"
    assert json.loads(list_request.data) == {
        "limit": 20,
        "offset": 0,
        "searchText": "strategic customers",
    }
    assert timeout == common.REQUEST_TIMEOUT_SECONDS
    assert "/wday/cxs/browserstack/External/job/" in calls[1][0].full_url


def test_protocol_support_requires_token_and_workday_domain(company: Company) -> None:
    adapter = WorkdayAdapter()
    assert isinstance(adapter, SourceAdapter)
    assert WorkdaySourceAdapter is WorkdayAdapter
    assert adapter.supports(company)
    assert not adapter.supports(company.model_copy(update={"source_token": "bad"}))
    assert not adapter.supports(company.model_copy(update={"careers_domains": []}))


@pytest.mark.parametrize(
    "update",
    [
        {"role_keywords": ["backend"]},
        {"seniority": "staff"},
        {"location": ["Bengaluru"]},
        {"employment_types": [EmploymentType.contract]},
        {"max_age_days": 0},
    ],
)
def test_criteria_filters(
    monkeypatch: pytest.MonkeyPatch,
    fixture: dict[str, object],
    company: Company,
    criteria: JobCriteria,
    update: dict[str, object],
) -> None:
    _install_router(monkeypatch, fixture)
    assert (
        WorkdayAdapter().fetch_open_roles(
            company,
            criteria.model_copy(update=update),
        )
        == []
    )


def test_rejects_detail_url_from_another_tenant_host(
    monkeypatch: pytest.MonkeyPatch,
    fixture: dict[str, object],
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
) -> None:
    detail = next(iter(fixture["details"].values()))
    detail["jobPostingInfo"]["externalUrl"] = (
        "https://other.wd3.myworkdayjobs.com/External/job/fake"
    )
    _install_router(monkeypatch, fixture)
    with caplog.at_level(logging.WARNING):
        roles = WorkdayAdapter().fetch_open_roles(company, criteria)
    assert roles == []
    assert "missing trusted externalUrl" in caplog.text


def test_rejects_unsafe_external_path_without_requesting_detail(
    monkeypatch: pytest.MonkeyPatch,
    fixture: dict[str, object],
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fixture["list"]["jobPostings"][0]["externalPath"] = "https://evil.example/job"
    calls = _install_router(monkeypatch, fixture)
    with caplog.at_level(logging.WARNING):
        roles = WorkdayAdapter().fetch_open_roles(company, criteria)
    assert roles == []
    assert len(calls) == 1
    assert "invalid externalPath" in caplog.text


def test_malformed_list_returns_empty_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        common,
        "urlopen",
        lambda *a, **k: _Response({"jobPostings": {}}),
    )
    with caplog.at_level(logging.WARNING):
        roles = WorkdayAdapter().fetch_open_roles(company, criteria)
    assert roles == []
    assert "malformed jobs payload" in caplog.text


def test_http_error_returns_empty_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise HTTPError("url", 404, "missing", {}, None)

    monkeypatch.setattr(common, "urlopen", fail)
    with caplog.at_level(logging.WARNING):
        roles = WorkdayAdapter().fetch_open_roles(company, criteria)
    assert roles == []
    assert "Workday request failed" in caplog.text
    assert "HTTP 404" in caplog.text


def test_trace_records_role_count(
    monkeypatch: pytest.MonkeyPatch,
    fixture: dict[str, object],
    company: Company,
    criteria: JobCriteria,
) -> None:
    class Span:
        def __init__(self) -> None:
            self.attributes: dict[str, object] = {}

        def set_attribute(self, key: str, value: object) -> None:
            self.attributes[key] = value

        def __enter__(self) -> "Span":
            return self

        def __exit__(self, *args: object) -> None:
            del args

    class Tracer:
        def __init__(self) -> None:
            self.name = ""
            self.span = Span()

        def start_as_current_span(self, name: str) -> Span:
            self.name = name
            return self.span

    tracer = Tracer()
    monkeypatch.setattr(workday, "TRACER", tracer)
    _install_router(monkeypatch, fixture)
    WorkdayAdapter().fetch_open_roles(company, criteria)
    assert tracer.name == "job_source.workday.fetch_open_roles"
    assert tracer.span.attributes == {
        "job_source.name": "workday",
        "job_source.company_slug": "browserstack",
        "job_source.source_token_configured": True,
        "job_source.role_count": 1,
    }
