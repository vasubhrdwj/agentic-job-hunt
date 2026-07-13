"""Production-safe SerpAPI adapter for public contact-profile searches.

The adapter deliberately keeps transport details and provider payloads behind
the :class:`~job_hunt_agent.contact_discovery.ContactSearchProvider` boundary.
Callers receive structured public-search observations or one of the two safe
provider exceptions; credentials, response bodies, and provider error text are
never included in an exception or representation.
"""

from __future__ import annotations

import json
import math
import os
import re
from http.client import HTTPException
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from job_hunt_agent.contact_discovery import (
    ContactProviderConfigurationError,
    ContactProviderError,
    DiscoveryCategory,
    ProviderSearchPage,
    ProviderSearchResult,
)


SERPAPI_SEARCH_ENDPOINT = "https://serpapi.com/search.json"
DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_SEARCH_RESULTS = 100
MAX_QUERY_CHARS = 2_000
MAX_RESPONSE_BYTES = 2_000_000
MAX_RESULT_TEXT_CHARS = 1_000
MAX_RESULT_URL_CHARS = 2_048
MAX_RESULT_POSITION = 10_000

_ENV_API_KEY_NAMES = ("SERPAPI_API_KEY", "SERPAPI_KEY")
_MISSING = object()


class _Response(Protocol):
    status: int

    def read(self, amount: int = -1) -> bytes: ...

    def close(self) -> None: ...


_Opener = Callable[..., _Response]


@dataclass(frozen=True)
class _FetchedResponse:
    status: int
    body: bytes
    transport_failed: bool = False
    oversized: bool = False


class SerpAPIContactProvider:
    """Search public profile evidence through SerpAPI's Google engine."""

    name = "serpapi"

    def __init__(
        self,
        api_key: str,
        *,
        opener: _Opener | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        normalized_key = api_key.strip() if isinstance(api_key, str) else ""
        if not normalized_key:
            raise ContactProviderConfigurationError(
                "SerpAPI contact search is not configured.",
            )
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout))
            or float(timeout) <= 0
        ):
            raise ValueError("timeout must be a positive finite number")

        self._api_key = normalized_key
        self._opener = opener or urlopen
        self._timeout = float(timeout)

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        opener: _Opener | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> SerpAPIContactProvider:
        """Build the adapter from the supported environment variable names."""

        source = os.environ if environ is None else environ
        api_key = next(
            (
                value.strip()
                for name in _ENV_API_KEY_NAMES
                if isinstance((value := source.get(name)), str) and value.strip()
            ),
            "",
        )
        return cls(api_key, opener=opener, timeout=timeout)

    def __repr__(self) -> str:
        """Return a useful representation that never includes credentials."""

        return (
            f"{type(self).__name__}(name={self.name!r}, "
            f"timeout={self._timeout!r})"
        )

    def search(
        self,
        query: str,
        *,
        category: DiscoveryCategory,
        limit: int,
    ) -> ProviderSearchPage:
        """Return one bounded page of structured public-search observations."""

        normalized_query = query.strip() if isinstance(query, str) else ""
        if not normalized_query:
            raise ValueError("query must not be blank")
        if len(normalized_query) > MAX_QUERY_CHARS:
            raise ValueError(f"query must be at most {MAX_QUERY_CHARS} characters")
        if not isinstance(category, DiscoveryCategory):
            raise ValueError("category must be a DiscoveryCategory")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_SEARCH_RESULTS
        ):
            raise ValueError(f"limit must be between 1 and {MAX_SEARCH_RESULTS}")

        request = Request(
            _search_url(query=normalized_query, api_key=self._api_key, limit=limit),
            headers={
                "Accept": "application/json",
                "User-Agent": "job-hunt-agent/contact-discovery",
            },
        )
        fetched = _fetch_response(
            request,
            opener=self._opener,
            timeout=self._timeout,
        )
        _raise_for_transport_or_status(fetched)
        payload = _decode_payload(fetched.body)

        metadata = _optional_object(payload, "search_metadata")
        information = _optional_object(payload, "search_information")
        pagination = _optional_object(payload, "serpapi_pagination")
        proven_empty = _is_proven_empty(
            metadata=metadata,
            information=information,
        )

        provider_error = _optional_text(payload, "error")
        if provider_error:
            if _looks_like_authentication_error(provider_error):
                raise ContactProviderConfigurationError(
                    "SerpAPI contact search credentials were rejected.",
                )
            if _looks_like_exhausted_quota(provider_error):
                raise ContactProviderConfigurationError(
                    "SerpAPI contact search quota is exhausted.",
                )
            if not (
                proven_empty and _looks_like_empty_result_error(provider_error)
            ):
                raise ContactProviderError(
                    "SerpAPI contact search reported a provider error.",
                )

        organic_results = _organic_results(payload, proven_empty=proven_empty)
        if proven_empty and organic_results:
            raise ContactProviderError(
                "SerpAPI contact search returned an unusable response.",
            )
        _raise_for_search_status(metadata)
        search_succeeded = _search_succeeded(metadata)
        has_next = _has_next_page(pagination)
        observed_at = datetime.now(timezone.utc)
        mapped = tuple(
            _map_organic_result(item, fallback_position=index, observed_at=observed_at)
            for index, item in enumerate(organic_results[:limit], start=1)
        )

        # A next link is authoritative.  Even without one, receiving the full
        # requested bound cannot prove that Google had no further results.
        exhausted = (
            search_succeeded
            and not has_next
            and len(organic_results) < limit
        )
        return ProviderSearchPage(results=mapped, exhausted=exhausted)


# Keep both common acronym spellings import-compatible.
SerpApiContactProvider = SerpAPIContactProvider


def _search_url(*, query: str, api_key: str, limit: int) -> str:
    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "num": str(limit),
        "hl": "en",
    }
    return f"{SERPAPI_SEARCH_ENDPOINT}?{urlencode(params)}"


def _fetch_response(
    request: Request,
    *,
    opener: _Opener,
    timeout: float,
) -> _FetchedResponse:
    response: _Response | None = None
    http_error_status: int | None = None
    http_error_body = b""
    transport_failed = False
    oversized = False
    status = 200
    body = b""

    try:
        response = opener(request, timeout=timeout)
        status = _response_status(response)
        body, oversized = _read_limited(response)
    except HTTPError as exc:
        http_error_status = _coerce_http_status(exc.code)
        if http_error_status == 429:
            http_error_body, oversized = _read_error_body_limited(exc)
        else:
            try:
                exc.close()
            except OSError:
                pass
    except (TimeoutError, URLError, OSError, HTTPException):
        transport_failed = True
    finally:
        if response is not None:
            try:
                response.close()
            except (OSError, HTTPException):
                pass

    if transport_failed:
        return _FetchedResponse(status=0, body=b"", transport_failed=True)
    if http_error_status is not None:
        return _FetchedResponse(
            status=http_error_status,
            body=http_error_body,
            oversized=oversized,
        )
    return _FetchedResponse(status=status, body=body, oversized=oversized)


def _response_status(response: _Response) -> int:
    raw_status: object = getattr(response, "status", None)
    if raw_status is None:
        getcode = getattr(response, "getcode", None)
        raw_status = getcode() if callable(getcode) else 200
    return _coerce_http_status(raw_status)


def _coerce_http_status(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 599:
        return 500
    return value


def _read_limited(response: _Response) -> tuple[bytes, bool]:
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if not isinstance(body, bytes):
        return b"", True
    return body[:MAX_RESPONSE_BYTES], len(body) > MAX_RESPONSE_BYTES


def _read_error_body_limited(error: HTTPError) -> tuple[bytes, bool]:
    try:
        body = error.read(MAX_RESPONSE_BYTES + 1)
    except (TimeoutError, URLError, OSError, HTTPException):
        return b"", False
    finally:
        try:
            error.close()
        except (OSError, HTTPException):
            pass
    if not isinstance(body, bytes):
        return b"", True
    return body[:MAX_RESPONSE_BYTES], len(body) > MAX_RESPONSE_BYTES


def _raise_for_transport_or_status(fetched: _FetchedResponse) -> None:
    if fetched.transport_failed:
        raise ContactProviderError(
            "SerpAPI contact search is temporarily unavailable.",
        )
    if fetched.status in {401, 403}:
        raise ContactProviderConfigurationError(
            "SerpAPI contact search credentials were rejected.",
        )
    if fetched.status == 429 and _body_reports_exhausted_quota(fetched.body):
        raise ContactProviderConfigurationError(
            "SerpAPI contact search quota is exhausted.",
        )
    if fetched.oversized:
        raise ContactProviderError(
            "SerpAPI contact search is temporarily unavailable.",
        )
    if fetched.status in {408, 425, 429} or 500 <= fetched.status < 600:
        raise ContactProviderError(
            "SerpAPI contact search is temporarily unavailable.",
        )
    if not 200 <= fetched.status < 300:
        raise ContactProviderConfigurationError(
            "SerpAPI contact search request was rejected.",
        )


def _decode_payload(body: bytes) -> dict[str, Any]:
    payload: object = None
    malformed = False
    try:
        payload = json.loads(body.decode("utf-8"), parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        malformed = True
    if malformed or not isinstance(payload, dict):
        raise ContactProviderError(
            "SerpAPI contact search returned an unusable response.",
        )
    return payload


def _reject_json_constant(value: str) -> None:
    del value
    raise ValueError("non-standard JSON constant")


def _optional_object(payload: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    value = payload.get(key, _MISSING)
    if value is _MISSING:
        return None
    if not isinstance(value, dict):
        raise ContactProviderError(
            "SerpAPI contact search returned an unusable response.",
        )
    return value


def _optional_text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key, _MISSING)
    if value is _MISSING or value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ContactProviderError(
            "SerpAPI contact search returned an unusable response.",
        )
    return value.strip() or None


def _organic_results(
    payload: Mapping[str, Any],
    *,
    proven_empty: bool,
) -> list[dict[str, Any]]:
    value = payload.get("organic_results", _MISSING)
    if value is _MISSING:
        if proven_empty:
            return []
        raise ContactProviderError(
            "SerpAPI contact search returned an unusable response.",
        )
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ContactProviderError(
            "SerpAPI contact search returned an unusable response.",
        )
    return value


def _map_organic_result(
    item: Mapping[str, Any],
    *,
    fallback_position: int,
    observed_at: datetime,
) -> ProviderSearchResult:
    title = item.get("title")
    link = item.get("link")
    snippet = item.get("snippet", "")
    position = item.get("position", fallback_position)
    if (
        not isinstance(title, str)
        or not title.strip()
        or not isinstance(link, str)
        or not link.strip()
        or not isinstance(snippet, str)
        or len(title.strip()) > MAX_RESULT_TEXT_CHARS
        or len(snippet.strip()) > MAX_RESULT_TEXT_CHARS
        or len(link.strip()) > MAX_RESULT_URL_CHARS
        or isinstance(position, bool)
        or not isinstance(position, int)
        or not 1 <= position <= MAX_RESULT_POSITION
    ):
        raise ContactProviderError(
            "SerpAPI contact search returned an unusable response.",
        )
    return ProviderSearchResult(
        result_title=title.strip(),
        result_url=link.strip(),
        result_excerpt=snippet.strip(),
        result_position=position,
        observed_at=observed_at,
    )


def _has_next_page(pagination: Mapping[str, Any] | None) -> bool:
    if pagination is None:
        return False
    has_next = False
    for key in ("next", "next_link"):
        value = pagination.get(key, _MISSING)
        if value is _MISSING or value is None or value == "":
            continue
        if not isinstance(value, str):
            raise ContactProviderError(
                "SerpAPI contact search returned an unusable response.",
            )
        has_next = has_next or bool(value.strip())
    return has_next


def _is_proven_empty(
    *,
    metadata: Mapping[str, Any] | None,
    information: Mapping[str, Any] | None,
) -> bool:
    if metadata is None or information is None:
        return False
    status = metadata.get("status")
    if not isinstance(status, str) or status.strip().casefold() != "success":
        return False

    total_results = information.get("total_results", _MISSING)
    total_is_zero = (
        not isinstance(total_results, bool)
        and (
            total_results == 0
            or (isinstance(total_results, str) and total_results.strip() == "0")
        )
    )
    organic_state = information.get("organic_results_state", _MISSING)
    state_is_empty = (
        isinstance(organic_state, str)
        and organic_state.strip().casefold() == "fully empty"
    )
    return total_is_zero or state_is_empty


def _raise_for_search_status(metadata: Mapping[str, Any] | None) -> None:
    if metadata is None or "status" not in metadata:
        return
    status = metadata["status"]
    if not isinstance(status, str):
        raise ContactProviderError(
            "SerpAPI contact search returned an unusable response.",
        )
    if status.strip().casefold() != "success":
        raise ContactProviderError(
            "SerpAPI contact search reported a provider error.",
        )


def _search_succeeded(metadata: Mapping[str, Any] | None) -> bool:
    if metadata is None:
        return False
    status = metadata.get("status")
    return isinstance(status, str) and status.strip().casefold() == "success"


def _body_reports_exhausted_quota(body: bytes) -> bool:
    error_text = _error_text_from_body(body)
    return bool(error_text and _looks_like_exhausted_quota(error_text))


def _looks_like_exhausted_quota(message: str) -> bool:
    normalized = " ".join(message.casefold().split())
    quota_patterns = (
        r"\b(?:run|ran) out of searches\b",
        r"\bno searches (?:are )?left\b",
        r"\b(?:does not|doesn't) have enough searches(?: left)?\b",
        r"\bmonthly (?:search(?:es)?|quota|allowance|credits?)\b.{0,40}"
        r"\b(?:exhausted|depleted|exceeded|used up)\b",
        r"\b(?:exhausted|depleted|exceeded|used up)\b.{0,40}"
        r"\b(?:monthly|month|quota|allowance|search(?:es)?)\b",
        r"\bquota (?:is |has been )?(?:exhausted|depleted|exceeded)\b",
    )
    return any(re.search(pattern, normalized) for pattern in quota_patterns)


def _looks_like_empty_result_error(message: str) -> bool:
    normalized = " ".join(message.casefold().replace("’", "'").split())
    return any(
        marker in normalized
        for marker in (
            "hasn't returned any results",
            "has not returned any results",
            "did not match any documents",
            "no results for this query",
            "no results found",
        )
    )


def _error_text_from_body(body: bytes) -> str | None:
    if not body:
        return None
    decoded: object = None
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None
    value = decoded.get("error")
    return value if isinstance(value, str) else None


def _looks_like_authentication_error(message: str) -> bool:
    normalized = " ".join(re.sub(r"[_-]+", " ", message.casefold()).split())
    if any(
        marker in normalized
        for marker in (
            "unauthorized",
            "forbidden",
            "authentication failed",
            "permission denied",
            "not authorized",
        )
    ):
        return True
    return "api key" in normalized and any(
        marker in normalized
        for marker in (
            "invalid",
            "missing",
            "required",
            "rejected",
            "disabled",
            "not valid",
            "not authorized",
        )
    )


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_SEARCH_RESULTS",
    "SERPAPI_SEARCH_ENDPOINT",
    "SerpAPIContactProvider",
    "SerpApiContactProvider",
]
