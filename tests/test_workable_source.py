"""Hermetic tests for the Workable source adapter."""

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
from job_hunt_agent.sources.workable import WorkableAdapter, WorkableSourceAdapter
import job_hunt_agent.sources.workable as workable


FIXTURE = Path(__file__).parent / "fixtures" / "adapters" / "workable.json"
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
        raise AssertionError("Workable tests must not access the network")

    monkeypatch.setattr(common, "urlopen", fail)
    monkeypatch.setattr(common, "_utc_now", lambda: NOW)


@pytest.fixture
def fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def company() -> Company:
    return Company(
        name="PeopleCert",
        slug="peoplecert",
        source=CompanySource.workable,
        source_token="peoplecert",
        careers_domains=["peoplecert.org", "apply.workable.com"],
        hire_locations=["Greece"],
        tags=["edtech"],
    )


@pytest.fixture
def criteria() -> JobCriteria:
    return JobCriteria(
        role_keywords=["HTML"],
        seniority="mid",
        location=["Athens Greece"],
        employment_types=[EmploymentType.full_time],
    )


def _install_router(
    monkeypatch: pytest.MonkeyPatch,
    fixture: dict[str, object],
) -> list[object]:
    calls: list[object] = []

    def fake(request: object, *, timeout: int) -> _Response:
        calls.append((request, timeout))
        if "/api/v3/" in request.full_url:
            return _Response(fixture["list"])
        shortcode = request.full_url.rsplit("/", 1)[-1]
        return _Response(fixture["details"][shortcode])

    monkeypatch.setattr(common, "urlopen", fake)
    return calls


def test_maps_real_fixture_and_uses_post(
    monkeypatch: pytest.MonkeyPatch,
    fixture: dict[str, object],
    company: Company,
    criteria: JobCriteria,
) -> None:
    calls = _install_router(monkeypatch, fixture)

    roles = WorkableAdapter().fetch_open_roles(company, criteria)

    assert len(roles) == 1
    role = roles[0]
    assert role.title == "HTML CSS Developer"
    assert role.url == "https://apply.workable.com/peoplecert/j/AD5788CFCA/"
    assert role.location == "Athens, Attica, Greece"
    assert role.posted_at == "2026-06-18T00:00:00.000Z"
    assert role.employment_type is EmploymentType.full_time
    assert role.source is CompanySource.workable
    assert role.raw_description and "Figma" in role.raw_description
    list_request, timeout = calls[0]
    assert list_request.get_method() == "POST"
    assert json.loads(list_request.data) == {"query": "HTML"}
    assert timeout == common.REQUEST_TIMEOUT_SECONDS
    assert calls[1][0].full_url.endswith("/jobs/AD5788CFCA")


def test_protocol_support_and_alias(company: Company) -> None:
    adapter = WorkableAdapter()
    assert isinstance(adapter, SourceAdapter)
    assert WorkableSourceAdapter is WorkableAdapter
    assert adapter.supports(company)
    assert not adapter.supports(company.model_copy(update={"source_token": None}))


@pytest.mark.parametrize(
    "update",
    [
        {"role_keywords": ["Python"]},
        {"seniority": "staff"},
        {"location": ["India"]},
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
    assert (
        WorkableAdapter().fetch_open_roles(
            company,
            criteria.model_copy(update=update),
        )
        == []
    )


def test_paginates_with_opaque_token(
    monkeypatch: pytest.MonkeyPatch,
    fixture: dict[str, object],
    company: Company,
    criteria: JobCriteria,
) -> None:
    first = dict(fixture["list"])
    first["results"] = []
    first["nextPage"] = "opaque-token"
    bodies: list[dict[str, object]] = []

    def fake(request: object, *, timeout: int) -> _Response:
        del timeout
        if "/api/v3/" in request.full_url:
            body = json.loads(request.data)
            bodies.append(body)
            return _Response(first if len(bodies) == 1 else fixture["list"])
        return _Response(fixture["details"]["AD5788CFCA"])

    monkeypatch.setattr(common, "urlopen", fake)
    roles = WorkableAdapter().fetch_open_roles(company, criteria)
    assert len(roles) == 1
    assert bodies == [
        {"query": "HTML"},
        {"query": "HTML", "token": "opaque-token"},
    ]


def test_internal_or_unpublished_jobs_are_omitted(
    monkeypatch: pytest.MonkeyPatch,
    fixture: dict[str, object],
    company: Company,
    criteria: JobCriteria,
) -> None:
    fixture["details"]["AD5788CFCA"]["isInternal"] = True
    _install_router(monkeypatch, fixture)
    assert WorkableAdapter().fetch_open_roles(company, criteria) == []


def test_malformed_list_returns_empty_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(common, "urlopen", lambda *a, **k: _Response({"results": {}}))
    with caplog.at_level(logging.WARNING):
        roles = WorkableAdapter().fetch_open_roles(company, criteria)
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
        roles = WorkableAdapter().fetch_open_roles(company, criteria)
    assert roles == []
    assert "Workable request failed" in caplog.text
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
    monkeypatch.setattr(workable, "TRACER", tracer)
    _install_router(monkeypatch, fixture)
    WorkableAdapter().fetch_open_roles(company, criteria)
    assert tracer.name == "job_source.workable.fetch_open_roles"
    assert tracer.span.attributes == {
        "job_source.name": "workable",
        "job_source.company_slug": "peoplecert",
        "job_source.source_token_configured": True,
        "job_source.role_count": 1,
    }
