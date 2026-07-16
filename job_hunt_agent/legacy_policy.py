"""Fail-closed policy for the deprecated hunt/run compatibility API."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from enum import Enum


LEGACY_HUNT_API_MODE_ENV = "LEGACY_HUNT_API_MODE"
LEGACY_HUNT_API_SUNSET_ENV = "LEGACY_HUNT_API_SUNSET"
LEGACY_HUNT_DEPRECATION_URL_ENV = "LEGACY_HUNT_DEPRECATION_URL"
DEFAULT_LEGACY_HUNT_API_SUNSET = "Tue, 31 Dec 2030 23:59:59 GMT"
DEFAULT_LEGACY_HUNT_DEPRECATION_URL = (
    "https://github.com/vasubhrdwj/agentic-job-hunt/blob/"
    "main/docs/runbooks/legacy-hunt-deprecation.md"
)


class LegacyHuntApiMode(str, Enum):
    enabled = "enabled"
    read_only = "read_only"
    disabled = "disabled"


def legacy_hunt_api_mode(*, production: bool) -> LegacyHuntApiMode:
    """Return the explicit compatibility mode and reject typos at startup."""

    default = (
        LegacyHuntApiMode.read_only.value
        if production
        else LegacyHuntApiMode.enabled.value
    )
    raw = os.getenv(LEGACY_HUNT_API_MODE_ENV, default).strip().lower()
    try:
        return LegacyHuntApiMode(raw)
    except ValueError as exc:
        choices = ", ".join(mode.value for mode in LegacyHuntApiMode)
        raise RuntimeError(
            f"{LEGACY_HUNT_API_MODE_ENV} must be one of: {choices}"
        ) from exc


def legacy_deprecation_headers(
    mode: LegacyHuntApiMode,
    *,
    now: datetime | None = None,
    production: bool | None = None,
) -> dict[str, str]:
    """Headers attached to every compatibility response, including errors."""

    sunset = os.getenv(
        LEGACY_HUNT_API_SUNSET_ENV,
        DEFAULT_LEGACY_HUNT_API_SUNSET,
    ).strip()
    deprecation_url = os.getenv(
        LEGACY_HUNT_DEPRECATION_URL_ENV,
        DEFAULT_LEGACY_HUNT_DEPRECATION_URL,
    ).strip()
    try:
        sunset_at = parsedate_to_datetime(sunset)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"{LEGACY_HUNT_API_SUNSET_ENV} must use RFC 7231 IMF-fixdate"
        ) from exc
    if (
        sunset_at.tzinfo is None
        or format_datetime(sunset_at.astimezone(timezone.utc), usegmt=True) != sunset
    ):
        raise RuntimeError(
            f"{LEGACY_HUNT_API_SUNSET_ENV} must use RFC 7231 IMF-fixdate"
        )
    current = now or datetime.now(timezone.utc)
    if mode is not LegacyHuntApiMode.disabled and sunset_at <= current:
        raise RuntimeError(
            f"{LEGACY_HUNT_API_SUNSET_ENV} must be in the future unless legacy mode is disabled"
        )
    is_production = (
        os.getenv("ENVIRONMENT", "").strip().lower() == "production"
        if production is None
        else production
    )
    allowed_schemes = ("https://",) if is_production else ("https://", "http://")
    if not deprecation_url.startswith(allowed_schemes):
        raise RuntimeError(
            f"{LEGACY_HUNT_DEPRECATION_URL_ENV} must be an absolute "
            + ("HTTPS URL in production" if is_production else "HTTP(S) URL")
        )
    return {
        "Deprecation": "true",
        "Sunset": sunset,
        "Link": f'<{deprecation_url}>; rel="deprecation"; type="text/markdown"',
        "X-Legacy-Hunt-Mode": mode.value,
    }


def is_legacy_hunt_path(path: str) -> bool:
    return path == "/api/hunt" or path.startswith("/api/runs/")


def legacy_request_problem(
    mode: LegacyHuntApiMode,
    *,
    method: str,
    path: str,
) -> dict[str, object] | None:
    """Return a stable 410 problem when policy blocks this request."""

    normalized_method = method.upper()
    if mode is LegacyHuntApiMode.enabled:
        return None
    path_parts = path.split("/")
    exact_run_path = (
        len(path_parts) == 4
        and path_parts[:3] == ["", "api", "runs"]
        and bool(path_parts[3])
    )
    if (
        mode is LegacyHuntApiMode.read_only
        and exact_run_path
        and normalized_method in {"GET", "HEAD", "DELETE"}
    ):
        # DELETE remains available as the privacy escape hatch.
        return None
    code = (
        "legacy_read_only"
        if mode is LegacyHuntApiMode.read_only
        else "legacy_disabled"
    )
    message = (
        "The legacy hunt API is read-only. Existing runs remain readable and "
        "deletable; use the practical job-search workspace for new work."
        if mode is LegacyHuntApiMode.read_only
        else "The legacy hunt API is disabled. Use the practical job-search workspace."
    )
    return {
        "code": code,
        "message": message,
        "retryable": False,
    }


__all__ = [
    "DEFAULT_LEGACY_HUNT_API_SUNSET",
    "LEGACY_HUNT_API_MODE_ENV",
    "LegacyHuntApiMode",
    "is_legacy_hunt_path",
    "legacy_deprecation_headers",
    "legacy_hunt_api_mode",
    "legacy_request_problem",
]
