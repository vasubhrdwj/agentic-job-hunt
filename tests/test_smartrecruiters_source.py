"""Hermetic tests for the SmartRecruiters source adapter."""

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
from job_hunt_agent.sources.smartrecruiters import (
    SmartRecruitersAdapter,
    SmartRecruitersSourceAdapter,
)
import job_hunt_agent.sources.smartrecruiters as smartrecruiters


FIXTURE = Path(__file__).parent / "fixtures" / "adapters" / "smartrecruiters.json"
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
        raise AssertionError("SmartRecruiters tests must not access the network")

    monkeypatch.setattr(common, "urlopen", fail)
    monkeypatch.setattr(common, "_utc_now", lambda: NOW)


@pytest.fixture
def fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def company() -> Company:
    return Company(
        name="Freshworks",
        slug="freshworks",
        source=CompanySource.smartrecruiters,
        source_token="Freshworks",
        careers_domains=["freshworks.com", "jobs.smartrecruiters.com"],
        hire_locations=["India"],
        tags=["saas"],
    )


@pytest.fixture
def criteria() -> JobCriteria:
    return JobCriteria(
        role_keywords=["Kubernetes"],
        seniority="senior",
        location=["Chennai India"],
        employment_types=[EmploymentType.full_time],
    )


def _install_router(
    monkeypatch: pytest.MonkeyPatch,
    fixture: dict[str, object],
) -> list[object]:
    calls: list[object] = []

    def fake(request: object, *, timeout: int) -> _Response:
        calls.append((request, timeout))
        if "/postings?" in request.full_url:
            return _Response(fixture["list"])
        posting_id = request.full_url.rsplit("/", 1)[-1]
        return _Response(fixture["details"][posting_id])

    monkeypatch.setattr(common, "urlopen", fake)
    return calls


def test_maps_real_list_and_detail_fixture(
    monkeypatch: pytest.MonkeyPatch,
    fixture: dict[str, object],
    company: Company,
    criteria: JobCriteria,
) -> None:
    calls = _install_router(monkeypatch, fixture)

    roles = SmartRecruitersAdapter().fetch_open_roles(company, criteria)

    assert len(roles) == 1
    role = roles[0]
    assert role.title == "Senior Software Engineer - Site Reliability"
    assert role.url.startswith("https://jobs.smartrecruiters.com/Freshworks/")
    assert len(role.apply_urls) == 2
    assert role.location == "Chennai, , India"
    assert role.posted_at == "2026-06-18T02:37:14.914Z"
    assert role.employment_type is EmploymentType.full_time
    assert role.source is CompanySource.smartrecruiters
    assert role.raw_description and "Kubernetes" in role.raw_description
    list_request, timeout = calls[0]
    assert "limit=100" in list_request.full_url
    assert "q=Kubernetes" in list_request.full_url
    assert timeout == common.REQUEST_TIMEOUT_SECONDS
    assert calls[1][0].full_url.endswith("/postings/744000132768109")


def test_protocol_support_and_alias(company: Company) -> None:
    adapter = SmartRecruitersAdapter()
    assert isinstance(adapter, SourceAdapter)
    assert SmartRecruitersSourceAdapter is SmartRecruitersAdapter
    assert adapter.name == "smartrecruiters"
    assert adapter.supports(company)
    assert not adapter.supports(company.model_copy(update={"source_token": None}))


@pytest.mark.parametrize(
    "update",
    [
        {"role_keywords": ["frontend"]},
        {"seniority": "junior"},
        {"location": ["Bengaluru"]},
        {"employment_types": [EmploymentType.contract]},
        {"max_age_days": 1},
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
    roles = SmartRecruitersAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update=update),
    )
    assert roles == []


def test_rejects_untrusted_detail_urls(
    monkeypatch: pytest.MonkeyPatch,
    fixture: dict[str, object],
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fixture["details"]["744000132768109"]["postingUrl"] = "https://evil.example/job"
    fixture["details"]["744000132768109"]["applyUrl"] = (
        "https://jobs.smartrecruiters.com/AnotherCompany/job"
    )
    _install_router(monkeypatch, fixture)
    with caplog.at_level(logging.WARNING):
        roles = SmartRecruitersAdapter().fetch_open_roles(company, criteria)
    assert roles == []
    assert "missing trusted" in caplog.text


def test_rejects_company_path_traversal(
    monkeypatch: pytest.MonkeyPatch,
    fixture: dict[str, object],
    company: Company,
    criteria: JobCriteria,
) -> None:
    fixture["details"]["744000132768109"]["postingUrl"] = (
        "https://jobs.smartrecruiters.com/Freshworks/%2e%2e/AnotherCompany/job"
    )
    fixture["details"]["744000132768109"]["applyUrl"] = (
        "https://jobs.smartrecruiters.com/Freshworks/%2e%2e/AnotherCompany/apply"
    )
    _install_router(monkeypatch, fixture)

    assert SmartRecruitersAdapter().fetch_open_roles(company, criteria) == []


def test_malformed_list_returns_empty_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(common, "urlopen", lambda *a, **k: _Response({"content": {}}))
    with caplog.at_level(logging.WARNING):
        roles = SmartRecruitersAdapter().fetch_open_roles(company, criteria)
    assert roles == []
    assert "malformed postings payload" in caplog.text


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
        roles = SmartRecruitersAdapter().fetch_open_roles(company, criteria)
    assert roles == []
    assert "SmartRecruiters request failed" in caplog.text
    assert "HTTP 404" in caplog.text


def test_trace_records_safe_attributes(
    monkeypatch: pytest.MonkeyPatch,
    fixture: dict[str, object],
    company: Company,
    criteria: JobCriteria,
) -> None:
    class Span:
        attributes: dict[str, object] = {}

        def set_attribute(self, key: str, value: object) -> None:
            self.attributes[key] = value

        def __enter__(self) -> "Span":
            return self

        def __exit__(self, *args: object) -> None:
            del args

    class Tracer:
        name = ""
        span = Span()

        def start_as_current_span(self, name: str) -> Span:
            self.name = name
            return self.span

    tracer = Tracer()
    monkeypatch.setattr(smartrecruiters, "TRACER", tracer)
    _install_router(monkeypatch, fixture)
    SmartRecruitersAdapter().fetch_open_roles(company, criteria)
    assert tracer.name == "job_source.smartrecruiters.fetch_open_roles"
    assert tracer.span.attributes == {
        "job_source.name": "smartrecruiters",
        "job_source.company_slug": "freshworks",
        "job_source.source_token_configured": True,
        "job_source.role_count": 1,
    }
