"""Hermetic contract tests for the live SerpAPI contact provider."""

from __future__ import annotations

import io
import json
from http.client import IncompleteRead
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest

from job_hunt_agent.contact_discovery import (
    ContactProviderConfigurationError,
    ContactProviderError,
    ContactSearchProvider,
    DiscoveryCategory,
    ProviderSearchPage,
)
from job_hunt_agent.contact_providers.serpapi import (
    MAX_QUERY_CHARS,
    SerpAPIContactProvider,
    SerpApiContactProvider,
)


@dataclass
class FakeResponse:
    body: bytes
    status: int = 200
    closed: bool = False

    def read(self, amount: int = -1) -> bytes:
        return self.body if amount < 0 else self.body[:amount]

    def close(self) -> None:
        self.closed = True


class RecordingOpener:
    def __init__(self, outcome: FakeResponse | BaseException) -> None:
        self.outcome = outcome
        self.calls: list[tuple[Request, float]] = []

    def __call__(self, request: Request, *, timeout: float) -> FakeResponse:
        self.calls.append((request, timeout))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _response(payload: object, *, status: int = 200) -> FakeResponse:
    return FakeResponse(json.dumps(payload).encode("utf-8"), status=status)


def _result(position: int) -> dict[str, object]:
    return {
        "position": position,
        "title": f"Person {position} - Engineer at Acme | LinkedIn",
        "link": f"https://www.linkedin.com/in/person-{position}",
        "snippet": f"Person {position} is an engineer at Acme.",
    }


def _search(
    opener: RecordingOpener,
    *,
    limit: int = 5,
) -> ProviderSearchPage:
    return SerpAPIContactProvider(
        "private-test-key",
        opener=opener,
        timeout=3.5,
    ).search(
        'site:linkedin.com/in "Acme" engineer',
        category=DiscoveryCategory.peer,
        limit=limit,
    )


def test_satisfies_provider_contract_and_maps_bounded_organic_results() -> None:
    payload = {
        "search_metadata": {"status": "Success"},
        "organic_results": [_result(3), _result(7), _result(9)],
        "serpapi_pagination": {
            "next": "https://serpapi.com/search.json?start=10",
        },
    }
    response = _response(payload)
    opener = RecordingOpener(response)
    provider = SerpAPIContactProvider(
        " private-test-key ",
        opener=opener,
        timeout=3.5,
    )

    assert isinstance(provider, ContactSearchProvider)
    page = provider.search(
        ' site:linkedin.com/in "Acme" engineer ',
        category=DiscoveryCategory.peer,
        limit=2,
    )

    results = tuple(page.results)
    assert len(results) == 2
    assert page.exhausted is False
    assert results[0].result_title == "Person 3 - Engineer at Acme | LinkedIn"
    assert results[0].result_url == "https://www.linkedin.com/in/person-3"
    assert results[0].result_excerpt == "Person 3 is an engineer at Acme."
    assert results[0].result_position == 3
    assert results[0].observed_at is not None
    assert results[0].observed_at.tzinfo is not None
    assert results[1].observed_at == results[0].observed_at
    assert results[0].confidence is None

    assert response.closed is True
    request, timeout = opener.calls[0]
    query = parse_qs(urlsplit(request.full_url).query)
    assert query == {
        "api_key": ["private-test-key"],
        "engine": ["google"],
        "hl": ["en"],
        "num": ["2"],
        "q": ['site:linkedin.com/in "Acme" engineer'],
    }
    assert timeout == 3.5
    assert request.get_header("Accept") == "application/json"


def test_uses_fallback_positions_and_optional_empty_snippet() -> None:
    item = _result(1)
    item.pop("position")
    item.pop("snippet")
    page = _search(RecordingOpener(_response({"organic_results": [item]})))

    result = tuple(page.results)[0]
    assert result.result_position == 1
    assert result.result_excerpt == ""


@pytest.mark.parametrize(
    ("organic_count", "limit", "pagination", "expected"),
    [
        (0, 5, None, True),
        (2, 5, None, True),
        (5, 5, None, False),
        (2, 5, {"next_link": "https://serpapi.com/next"}, False),
        (2, 5, {"next": ""}, True),
    ],
)
def test_reports_exhaustion_only_when_pagination_and_bound_prove_it(
    organic_count: int,
    limit: int,
    pagination: dict[str, object] | None,
    expected: bool,
) -> None:
    payload: dict[str, object] = {
        "search_metadata": {"status": "Success"},
        "organic_results": [_result(index) for index in range(1, organic_count + 1)],
    }
    if pagination is not None:
        payload["serpapi_pagination"] = pagination

    page = _search(RecordingOpener(_response(payload)), limit=limit)

    assert page.exhausted is expected


@pytest.mark.parametrize(
    "information",
    [
        {"total_results": 0},
        {"total_results": "0"},
        {"organic_results_state": "Fully empty"},
    ],
)
def test_accepts_provider_error_as_empty_only_with_success_and_zero_proof(
    information: dict[str, object],
) -> None:
    page = _search(
        RecordingOpener(
            _response(
                {
                    "error": "Google hasn't returned any results for this query.",
                    "search_metadata": {"status": "Success"},
                    "search_information": information,
                },
            ),
        ),
    )

    assert tuple(page.results) == ()
    assert page.exhausted is True


def test_valid_empty_response_without_error_is_successful() -> None:
    page = _search(RecordingOpener(_response({"organic_results": []})))

    assert tuple(page.results) == ()
    assert page.exhausted is False


def test_from_env_prefers_primary_key_and_supports_legacy_name() -> None:
    primary = SerpAPIContactProvider.from_env(
        environ={
            "SERPAPI_API_KEY": " primary-secret ",
            "SERPAPI_KEY": "legacy-secret",
        },
        opener=RecordingOpener(_response({})),
    )
    legacy = SerpAPIContactProvider.from_env(
        environ={"SERPAPI_KEY": " legacy-secret "},
        opener=RecordingOpener(_response({})),
    )

    assert "primary-secret" not in repr(primary)
    assert "legacy-secret" not in repr(legacy)
    assert SerpApiContactProvider is SerpAPIContactProvider


@pytest.mark.parametrize(
    "environment",
    [{}, {"SERPAPI_API_KEY": "  "}, {"SERPAPI_KEY": ""}],
)
def test_missing_environment_key_is_configuration_failure(
    environment: dict[str, str],
) -> None:
    with pytest.raises(ContactProviderConfigurationError) as raised:
        SerpAPIContactProvider.from_env(environ=environment)

    assert "API" not in str(raised.value) or "key" not in str(raised.value).casefold()


@pytest.mark.parametrize("status", [401, 403])
def test_http_authentication_failures_are_safe_configuration_errors(
    status: int,
) -> None:
    secret = "secret-key-must-not-leak"
    body_secret = "secret-provider-body"
    error = HTTPError(
        f"https://serpapi.com/search.json?api_key={secret}",
        status,
        "provider message contains secrets",
        hdrs=None,
        fp=io.BytesIO(json.dumps({"error": body_secret}).encode()),
    )
    provider = SerpAPIContactProvider(secret, opener=RecordingOpener(error))

    with pytest.raises(ContactProviderConfigurationError) as raised:
        provider.search("query", category=DiscoveryCategory.peer, limit=5)

    rendered = f"{raised.value!r} {raised.value}"
    assert secret not in rendered
    assert body_secret not in rendered
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    ("body", "expected_error"),
    [
        ({"error": "Too many searches per hour."}, ContactProviderError),
        (
            {"error": "Your account has run out of searches."},
            ContactProviderConfigurationError,
        ),
        (
            {"error": "Monthly search quota has been exhausted."},
            ContactProviderConfigurationError,
        ),
    ],
)
def test_429_distinguishes_retryable_throughput_from_exhausted_quota(
    body: dict[str, str],
    expected_error: type[ContactProviderError],
) -> None:
    error = HTTPError(
        "https://serpapi.com/search.json",
        429,
        "provider error",
        hdrs=None,
        fp=io.BytesIO(json.dumps(body).encode()),
    )

    with pytest.raises(expected_error):
        _search(RecordingOpener(error))


@pytest.mark.parametrize("status", [408, 425, 500, 502, 503])
def test_other_http_failures_are_retryable_provider_errors(status: int) -> None:
    error = HTTPError(
        "https://serpapi.com/search.json",
        status,
        "provider detail",
        hdrs=None,
        fp=io.BytesIO(b'{"error":"private body"}'),
    )

    with pytest.raises(ContactProviderError) as raised:
        _search(RecordingOpener(error))

    assert not isinstance(raised.value, ContactProviderConfigurationError)
    assert "private body" not in str(raised.value)


@pytest.mark.parametrize("status", [400, 404, 410, 422])
def test_nonretryable_http_client_failures_are_safe_configuration_errors(
    status: int,
) -> None:
    error = HTTPError(
        "https://serpapi.com/search.json",
        status,
        "private provider detail",
        hdrs=None,
        fp=io.BytesIO(b'{"error":"private body"}'),
    )

    with pytest.raises(ContactProviderConfigurationError) as raised:
        _search(RecordingOpener(error))

    assert "private" not in str(raised.value).casefold()


@pytest.mark.parametrize(
    "transport_error",
    [
        TimeoutError("private timeout detail"),
        URLError("private URL detail"),
        OSError("private IO detail"),
    ],
)
def test_transport_failures_become_safe_retryable_provider_errors(
    transport_error: BaseException,
) -> None:
    with pytest.raises(ContactProviderError) as raised:
        _search(RecordingOpener(transport_error))

    assert not isinstance(raised.value, ContactProviderConfigurationError)
    assert "private" not in str(raised.value).casefold()
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    ("payload", "configuration"),
    [
        ({"error": "Invalid API key supplied."}, True),
        ({"error": "Your account has run out of searches."}, True),
        (
            {
                "error": "Invalid API key supplied.",
                "search_metadata": {"status": "Success"},
                "search_information": {"total_results": 0},
            },
            True,
        ),
        (
            {
                "error": "Your account has run out of searches.",
                "search_metadata": {"status": "Success"},
                "search_information": {"organic_results_state": "Fully empty"},
            },
            True,
        ),
        ({"error": "Upstream provider rejected this query."}, False),
        (
            {
                "error": "zero results but not proven",
                "search_metadata": {"status": "Success"},
            },
            False,
        ),
        ({"search_metadata": {"status": "Error"}}, False),
    ],
)
def test_200_provider_errors_are_classified_without_exposing_messages(
    payload: dict[str, object],
    configuration: bool,
) -> None:
    expected = ContactProviderConfigurationError if configuration else ContactProviderError

    with pytest.raises(expected) as raised:
        _search(RecordingOpener(_response(payload)))

    assert "rejected this query" not in str(raised.value)
    assert "zero results" not in str(raised.value)


@pytest.mark.parametrize(
    "outcome",
    [
        _response({}),
        FakeResponse(b"not-json"),
        _response([]),
        _response({"organic_results": {}}),
        _response({"organic_results": ["not-an-object"]}),
        _response({"organic_results": [{"title": "Missing link"}]}),
        _response({"organic_results": [{"title": 123, "link": "https://example.com"}]}),
        _response({"serpapi_pagination": []}),
        _response({"serpapi_pagination": {"next": 42}}),
        _response({"search_metadata": "Success"}),
        _response({"error": {"message": "nested"}}),
        _response(
            {
                "search_metadata": {"status": "Success"},
                "search_information": {"total_results": 0},
                "organic_results": [_result(1)],
            },
        ),
    ],
)
def test_malformed_json_and_shapes_are_retryable_provider_errors(
    outcome: FakeResponse,
) -> None:
    with pytest.raises(ContactProviderError) as raised:
        _search(RecordingOpener(outcome))

    assert not isinstance(raised.value, ContactProviderConfigurationError)


def test_read_time_http_failure_is_sanitized_without_partial_body_leakage() -> None:
    class BrokenReadResponse(FakeResponse):
        def read(self, amount: int = -1) -> bytes:
            del amount
            raise IncompleteRead(b"PRIVATE_PARTIAL_PROVIDER_BODY")

    with pytest.raises(ContactProviderError) as raised:
        _search(RecordingOpener(BrokenReadResponse(b"")))

    assert not isinstance(raised.value, ContactProviderConfigurationError)
    assert "PRIVATE_PARTIAL_PROVIDER_BODY" not in repr(raised.value)


def test_deeply_nested_json_is_sanitized_as_a_provider_failure() -> None:
    body = ("[" * 2_000 + "0" + "]" * 2_000).encode()

    with pytest.raises(ContactProviderError):
        _search(RecordingOpener(FakeResponse(body)))


@pytest.mark.parametrize("status", ["", "Complete", "Processing", 42])
def test_unknown_search_status_never_proves_completion(status: object) -> None:
    with pytest.raises(ContactProviderError):
        _search(
            RecordingOpener(
                _response(
                    {
                        "search_metadata": {"status": status},
                        "organic_results": [],
                    }
                )
            )
        )


@pytest.mark.parametrize(
    "item",
    [
        {**_result(1), "title": "x" * 1_001},
        {**_result(1), "snippet": "x" * 1_001},
        {**_result(1), "link": "https://example.com/" + "x" * 2_100},
        {**_result(1), "position": 10_001},
    ],
)
def test_result_fields_are_bounded_before_crossing_the_provider_boundary(
    item: dict[str, object],
) -> None:
    with pytest.raises(ContactProviderError):
        _search(
            RecordingOpener(
                _response(
                    {
                        "search_metadata": {"status": "Success"},
                        "organic_results": [item],
                    }
                )
            )
        )


@pytest.mark.parametrize(
    ("query", "category", "limit"),
    [
        ("", DiscoveryCategory.peer, 5),
        ("x" * (MAX_QUERY_CHARS + 1), DiscoveryCategory.peer, 5),
        ("query", "peer", 5),
        ("query", DiscoveryCategory.peer, 0),
        ("query", DiscoveryCategory.peer, 101),
        ("query", DiscoveryCategory.peer, True),
    ],
)
def test_rejects_invalid_caller_inputs_before_transport(
    query: str,
    category: Any,
    limit: Any,
) -> None:
    opener = RecordingOpener(_response({}))
    provider = SerpAPIContactProvider("secret", opener=opener)

    with pytest.raises(ValueError):
        provider.search(query, category=category, limit=limit)

    assert opener.calls == []
