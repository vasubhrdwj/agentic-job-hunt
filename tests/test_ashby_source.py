"""Hermetic tests for the Ashby source adapter."""

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
from job_hunt_agent.sources import ashby
from job_hunt_agent.sources.ashby import AshbyAdapter, AshbySourceAdapter
from job_hunt_agent.sources.base import SourceAdapter


FIXTURE = Path(__file__).parent / "fixtures" / "adapters" / "ashby.json"
NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def read(self) -> bytes:
        return self.body


class _Span:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


class _SpanContext:
    def __init__(self, span: _Span) -> None:
        self.span = span

    def __enter__(self) -> _Span:
        return self.span

    def __exit__(self, *args: object) -> None:
        del args


class _Tracer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, _Span]] = []

    def start_as_current_span(self, name: str) -> _SpanContext:
        span = _Span()
        self.calls.append((name, span))
        return _SpanContext(span)


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("Ashby tests must not access the network")

    monkeypatch.setattr(ashby, "urlopen", fail)
    monkeypatch.setattr(ashby, "_utc_now", lambda: NOW)


@pytest.fixture
def company() -> Company:
    return Company(
        name="Ashby",
        slug="ashby",
        source=CompanySource.ashby,
        source_token="Ashby",
        careers_domains=["ashbyhq.com", "jobs.ashbyhq.com"],
        hire_locations=["Europe"],
        tags=["developer-tools"],
    )


@pytest.fixture
def criteria() -> JobCriteria:
    return JobCriteria(
        role_keywords=["design system"],
        seniority="mid",
        location=["Portugal"],
        employment_types=[EmploymentType.full_time],
    )


def _payload() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _install(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> list[object]:
    calls: list[object] = []

    def fake(request: object, *, timeout: int) -> _Response:
        calls.append((request, timeout))
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr(ashby, "urlopen", fake)
    return calls


def test_maps_real_fixture_and_request_shape(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
) -> None:
    calls = _install(monkeypatch, _payload())

    roles = AshbyAdapter().fetch_open_roles(company, criteria)

    assert len(roles) == 1
    role = roles[0]
    assert role.title == "Design Engineer, EU"
    assert role.url.endswith("/application")
    assert role.apply_urls[1].endswith("188cc71b-a625-4022-94dc-7c43fa1a8b06")
    assert role.location == "Portugal"
    assert role.posted_at == "2026-06-12T20:19:00.233+00:00"
    assert role.employment_type is EmploymentType.full_time
    assert role.source is CompanySource.ashby
    assert role.raw_description and "design system" in role.raw_description
    request, timeout = calls[0]
    assert request.full_url == (
        "https://api.ashbyhq.com/posting-api/job-board/Ashby"
    )
    assert request.get_method() == "GET"
    assert timeout == ashby.REQUEST_TIMEOUT_SECONDS


def test_protocol_support_and_trace(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
) -> None:
    tracer = _Tracer()
    monkeypatch.setattr(ashby, "TRACER", tracer)
    _install(monkeypatch, _payload())
    adapter = AshbyAdapter()

    roles = adapter.fetch_open_roles(company, criteria)

    assert isinstance(adapter, SourceAdapter)
    assert AshbySourceAdapter is AshbyAdapter
    assert adapter.supports(company)
    assert not adapter.supports(company.model_copy(update={"source_token": None}))
    assert tracer.calls[0][0] == "job_source.ashby.fetch_open_roles"
    assert tracer.calls[0][1].attributes == {
        "job_source.name": "ashby",
        "job_source.company_slug": "ashby",
        "job_source.source_token_configured": True,
        "job_source.role_count": len(roles),
    }


@pytest.mark.parametrize(
    "update",
    [
        {"role_keywords": ["backend"]},
        {"seniority": "staff"},
        {"location": ["India"]},
        {"employment_types": [EmploymentType.contract]},
        {"max_age_days": 1},
    ],
)
def test_criteria_filters(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
    update: dict[str, object],
) -> None:
    _install(monkeypatch, _payload())
    assert (
        AshbyAdapter().fetch_open_roles(
            company,
            criteria.model_copy(update=update),
        )
        == []
    )


def test_does_not_merge_distinct_locations(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = _payload()
    job = payload["jobs"][0]
    job["location"] = "Remote - US"
    job["secondaryLocations"] = [{"location": "Bengaluru, India"}]
    _install(monkeypatch, payload)

    roles = AshbyAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"location": ["Remote India"]}),
    )

    assert roles == []


def test_rejects_wrong_board_url(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = _payload()
    payload["jobs"][0]["applyUrl"] = "https://jobs.ashbyhq.com/Other/id/application"
    payload["jobs"][0]["jobUrl"] = "https://evil.example/job"
    _install(monkeypatch, payload)

    with caplog.at_level(logging.WARNING):
        roles = AshbyAdapter().fetch_open_roles(company, criteria)

    assert roles == []
    assert "missing trusted" in caplog.text


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"{bad", "malformed JSON"),
        (b'{"jobs": "wrong"}', "jobs is not a list"),
    ],
)
def test_malformed_response_is_honest_empty(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
    body: bytes,
    message: str,
) -> None:
    monkeypatch.setattr(ashby, "urlopen", lambda *a, **k: _Response(body))
    with caplog.at_level(logging.WARNING):
        roles = AshbyAdapter().fetch_open_roles(company, criteria)
    assert roles == []
    assert message in caplog.text


def test_http_error_is_honest_empty(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise HTTPError("url", 404, "missing", {}, None)

    monkeypatch.setattr(ashby, "urlopen", fail)
    with caplog.at_level(logging.WARNING):
        roles = AshbyAdapter().fetch_open_roles(company, criteria)
    assert roles == []
    assert "HTTP 404" in caplog.text
