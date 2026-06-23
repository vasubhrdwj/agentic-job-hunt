"""Hermetic tests for the Greenhouse source adapter."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import pytest

from job_hunt_agent.schemas import (
    Company,
    CompanySource,
    EmploymentType,
    JobCriteria,
)
from job_hunt_agent.sources import greenhouse
from job_hunt_agent.sources.base import SourceAdapter
from job_hunt_agent.sources.greenhouse import GreenhouseAdapter


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "adapters" / "greenhouse.json"
FIXED_NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("Greenhouse unit tests must not access the network")

    monkeypatch.setattr(greenhouse, "urlopen", fail_if_called)
    monkeypatch.setattr(greenhouse, "_utc_now", lambda: FIXED_NOW)


@pytest.fixture
def company() -> Company:
    return Company(
        name="MongoDB",
        slug="mongodb",
        source=CompanySource.greenhouse,
        source_token="mongodb",
        careers_domains=["mongodb.com"],
        hire_locations=["India"],
        tags=["backend"],
    )


@pytest.fixture
def criteria() -> JobCriteria:
    return JobCriteria(
        role_keywords=["distributed systems"],
        seniority="staff",
        location=["Bengaluru"],
        max_age_days=None,
    )


def _fixture_bytes() -> bytes:
    return FIXTURE_PATH.read_bytes()


def _install_response(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
) -> dict[str, object]:
    captured: dict[str, object] = {}

    def fake_urlopen(request: object, *, timeout: int) -> BytesIO:
        captured["request"] = request
        captured["timeout"] = timeout
        return BytesIO(body)

    monkeypatch.setattr(greenhouse, "urlopen", fake_urlopen)
    return captured


def test_adapter_satisfies_protocol_and_supports_only_greenhouse(
    company: Company,
) -> None:
    adapter = GreenhouseAdapter()

    assert isinstance(adapter, SourceAdapter)
    assert adapter.name == "greenhouse"
    assert adapter.supports(company)
    assert not adapter.supports(company.model_copy(update={"source_token": None}))
    assert not adapter.supports(
        company.model_copy(update={"source": CompanySource.google_jobs}),
    )


def test_maps_real_fixture_to_exact_role_contract(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
) -> None:
    captured = _install_response(monkeypatch, _fixture_bytes())

    roles = GreenhouseAdapter().fetch_open_roles(company, criteria)

    assert len(roles) == 1
    role = roles[0]
    assert role.company == "MongoDB"
    assert role.title == "Senior Staff Engineer"
    assert role.url == "https://www.mongodb.com/careers/job/?gh_jid=7704173"
    assert role.apply_urls == [role.url]
    assert role.location == "Bengaluru"
    assert role.source is CompanySource.greenhouse
    assert role.posted_at == "2026-03-11T07:17:17-04:00"
    assert role.source_updated_at == "2026-06-18T15:34:16-04:00"
    assert role.employment_type is EmploymentType.full_time
    assert role.confidence == 1.0
    assert role.raw_description
    assert "distributed systems" in role.raw_description
    assert "<p>" not in role.raw_description
    assert "&amp;" not in role.raw_description
    assert "<" not in role.summary
    assert '"distributed systems"' in role.match_reason
    assert "Bengaluru" in role.match_reason

    request = captured["request"]
    assert request.full_url == (
        "https://boards-api.greenhouse.io/v1/boards/mongodb/jobs?content=true"
    )
    assert request.get_header("Accept") == "application/json"
    assert captured["timeout"] == greenhouse.DEFAULT_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    "criteria_update",
    [
        {"role_keywords": ["frontend"]},
        {"seniority": "junior"},
        {"location": ["Hyderabad"]},
        {"employment_types": [EmploymentType.contract]},
    ],
)
def test_filters_only_on_source_evidence(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
    criteria_update: dict[str, object],
) -> None:
    _install_response(monkeypatch, _fixture_bytes())

    roles = GreenhouseAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update=criteria_update),
    )

    assert roles == []


def test_filters_roles_older_than_max_age_using_first_published(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = json.loads(_fixture_bytes())
    # 2026-05-06 19:59:59 -04:00 is one second older than the 45-day UTC cutoff.
    payload["jobs"][0]["first_published"] = "2026-05-06T19:59:59-04:00"
    payload["jobs"][0]["updated_at"] = "2026-06-20T00:00:00Z"
    _install_response(monkeypatch, json.dumps(payload).encode())

    roles = GreenhouseAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"max_age_days": 45}),
    )

    assert roles == []


def test_accepts_role_exactly_on_max_age_boundary_across_timezones(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = json.loads(_fixture_bytes())
    # This is exactly 2026-05-07 00:00:00 UTC, 45 days before FIXED_NOW.
    payload["jobs"][0]["first_published"] = "2026-05-06T20:00:00-04:00"
    _install_response(monkeypatch, json.dumps(payload).encode())

    roles = GreenhouseAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"max_age_days": 45}),
    )

    assert len(roles) == 1


def test_max_age_none_disables_freshness_filter(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = json.loads(_fixture_bytes())
    payload["jobs"][0]["first_published"] = "2020-01-01T00:00:00Z"
    _install_response(monkeypatch, json.dumps(payload).encode())

    roles = GreenhouseAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"max_age_days": None}),
    )

    assert len(roles) == 1


def test_invalid_first_published_cannot_bypass_max_age_filter(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = json.loads(_fixture_bytes())
    payload["jobs"][0]["first_published"] = "not-a-timestamp"
    _install_response(monkeypatch, json.dumps(payload).encode())

    with caplog.at_level(logging.WARNING, logger=greenhouse.__name__):
        roles = GreenhouseAdapter().fetch_open_roles(
            company,
            criteria.model_copy(update={"max_age_days": 45}),
        )

    assert roles == []
    assert "invalid first_published" in caplog.text


def test_keyword_matching_requires_token_or_phrase_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = json.loads(_fixture_bytes())
    payload["jobs"][0]["content"] = (
        "&lt;p&gt;MongoDB supports ongoing database development.&lt;/p&gt;"
    )
    _install_response(monkeypatch, json.dumps(payload).encode())

    false_positive_roles = GreenhouseAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"role_keywords": ["go"]}),
    )
    assert false_positive_roles == []

    _install_response(monkeypatch, _fixture_bytes())
    standalone_token_roles = GreenhouseAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"role_keywords": ["go"]}),
    )
    assert len(standalone_token_roles) == 1


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer II",
        "Backend Engineer 2",
        "SDE III",
        "Backend Developer L2",
        "Mid-Level Software Engineer",
        "Engineering Manager, Backend",
    ],
)
def test_junior_filter_rejects_numeric_and_equivalent_nonjunior_levels(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
    title: str,
) -> None:
    payload = json.loads(_fixture_bytes())
    payload["jobs"][0]["title"] = title
    _install_response(monkeypatch, json.dumps(payload).encode())

    roles = GreenhouseAdapter().fetch_open_roles(
        company,
        criteria.model_copy(
            update={
                "role_keywords": [],
                "seniority": "junior",
            },
        ),
    )

    assert roles == []


def test_junior_filter_still_accepts_explicit_level_one(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = json.loads(_fixture_bytes())
    payload["jobs"][0]["title"] = "Software Engineer I"
    _install_response(monkeypatch, json.dumps(payload).encode())

    roles = GreenhouseAdapter().fetch_open_roles(
        company,
        criteria.model_copy(
            update={
                "role_keywords": [],
                "seniority": "junior",
            },
        ),
    )

    assert len(roles) == 1


def test_location_tokens_are_not_combined_across_independent_locations(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = json.loads(_fixture_bytes())
    payload["jobs"][0]["location"] = {"name": "Remote - US"}
    payload["jobs"][0]["offices"] = [
        {
            "name": "Bengaluru",
            "location": "Bengaluru, Karnataka, India",
        },
    ]
    body = json.dumps(payload).encode()
    _install_response(monkeypatch, body)

    mixed_tokens = GreenhouseAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"location": ["Remote-India"]}),
    )
    assert mixed_tokens == []

    _install_response(monkeypatch, body)
    actual_location = GreenhouseAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"location": ["Remote-US"]}),
    )
    assert len(actual_location) == 1

    payload["jobs"][0]["location"] = {"name": "Remote-US + Bengaluru, India"}
    payload["jobs"][0]["offices"] = []
    _install_response(monkeypatch, json.dumps(payload).encode())
    combined_label = GreenhouseAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"location": ["Remote-India"]}),
    )
    assert combined_label == []


def test_accepts_greenhouse_host_without_custom_domain(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = json.loads(_fixture_bytes())
    payload["jobs"][0]["absolute_url"] = (
        "https://boards.greenhouse.io/mongodb/jobs/7704173"
    )
    _install_response(monkeypatch, json.dumps(payload).encode())

    roles = GreenhouseAdapter().fetch_open_roles(
        company.model_copy(update={"careers_domains": []}),
        criteria,
    )

    assert len(roles) == 1
    assert roles[0].url.startswith("https://boards.greenhouse.io/")


def test_rejects_untrusted_apply_url_with_log(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = json.loads(_fixture_bytes())
    payload["jobs"][0]["absolute_url"] = "https://jobs.example.net/7704173"
    _install_response(monkeypatch, json.dumps(payload).encode())

    with caplog.at_level(logging.WARNING, logger=greenhouse.__name__):
        roles = GreenhouseAdapter().fetch_open_roles(company, criteria)

    assert roles == []
    assert "not a trusted first-party URL" in caplog.text


def test_rejects_cross_tenant_greenhouse_url(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = json.loads(_fixture_bytes())
    payload["jobs"][0]["absolute_url"] = (
        "https://job-boards.greenhouse.io/other-company/jobs/7704173"
    )
    _install_response(monkeypatch, json.dumps(payload).encode())

    assert GreenhouseAdapter().fetch_open_roles(
        company.model_copy(
            update={"careers_domains": ["job-boards.greenhouse.io"]},
        ),
        criteria,
    ) == []


@pytest.mark.parametrize("segment", ["..", "%2e%2e", "%252e%252e"])
def test_rejects_greenhouse_path_traversal(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
    segment: str,
) -> None:
    payload = json.loads(_fixture_bytes())
    payload["jobs"][0]["absolute_url"] = (
        f"https://job-boards.greenhouse.io/mongodb/{segment}/other/jobs/7704173"
    )
    _install_response(monkeypatch, json.dumps(payload).encode())

    assert GreenhouseAdapter().fetch_open_roles(company, criteria) == []


def test_na_location_uses_matching_office_for_display(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = json.loads(_fixture_bytes())
    payload["jobs"][0]["location"] = {"name": "N/A"}
    payload["jobs"][0]["offices"] = [
        {
            "name": "Bengaluru",
            "location": "Bengaluru, Karnataka, India",
        }
    ]
    _install_response(monkeypatch, json.dumps(payload).encode())

    roles = GreenhouseAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"location": ["Bengaluru"]}),
    )

    assert roles[0].location == "Bengaluru, Karnataka, India"


def test_missing_employment_evidence_remains_unknown(
    monkeypatch: pytest.MonkeyPatch,
    company: Company,
    criteria: JobCriteria,
) -> None:
    payload = json.loads(_fixture_bytes())
    payload["jobs"][0]["metadata"] = []
    payload["jobs"][0]["title"] = "Software Engineer"
    payload["jobs"][0]["content"] = "<p>Build distributed systems.</p>"
    _install_response(monkeypatch, json.dumps(payload).encode())

    roles = GreenhouseAdapter().fetch_open_roles(
        company,
        criteria.model_copy(update={"seniority": "mid"}),
    )

    assert len(roles) == 1
    assert roles[0].employment_type is EmploymentType.unknown


def test_empty_payload_returns_empty_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    company: Company,
    criteria: JobCriteria,
) -> None:
    _install_response(monkeypatch, b'{"jobs": [], "meta": {"total": 0}}')

    with caplog.at_level(logging.INFO, logger=greenhouse.__name__):
        roles = GreenhouseAdapter().fetch_open_roles(company, criteria)

    assert roles == []
    assert "returned no open roles" in caplog.text


def test_http_error_returns_empty_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    company: Company,
    criteria: JobCriteria,
) -> None:
    def raise_http_error(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise HTTPError(
            url="https://boards-api.greenhouse.io/",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(greenhouse, "urlopen", raise_http_error)

    with caplog.at_level(logging.WARNING, logger=greenhouse.__name__):
        roles = GreenhouseAdapter().fetch_open_roles(company, criteria)

    assert roles == []
    assert "HTTP 404" in caplog.text


@pytest.mark.parametrize(
    ("body", "expected_log"),
    [
        (b"{not-json", "malformed JSON"),
        (b"[]", "expected an object"),
        (b'{"jobs": {}}', "jobs is not a list"),
        (b'{"jobs": [null]}', "expected an object"),
    ],
)
def test_malformed_payload_returns_empty_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    company: Company,
    criteria: JobCriteria,
    body: bytes,
    expected_log: str,
) -> None:
    _install_response(monkeypatch, body)

    with caplog.at_level(logging.WARNING, logger=greenhouse.__name__):
        roles = GreenhouseAdapter().fetch_open_roles(company, criteria)

    assert roles == []
    assert expected_log in caplog.text


def test_missing_token_returns_empty_without_network(
    caplog: pytest.LogCaptureFixture,
    company: Company,
    criteria: JobCriteria,
) -> None:
    with caplog.at_level(logging.WARNING, logger=greenhouse.__name__):
        roles = GreenhouseAdapter().fetch_open_roles(
            company.model_copy(update={"source_token": None}),
            criteria,
        )

    assert roles == []
    assert "not configured" in caplog.text
