"""Shared fail-closed production checks for provider-consuming processes."""

from __future__ import annotations

import os

from .config import env_bool, is_production, practical_mode_enabled
from .database import DatabaseConfigError, resolve_database_url


def production_runtime_errors(
    *,
    practical_mode: bool | None = None,
    use_mocks: bool | None = None,
    enable_tracing: bool | None = None,
) -> list[str]:
    """Return common production errors without opening a database connection."""

    if not is_production():
        return []

    practical = (
        practical_mode_enabled() if practical_mode is None else practical_mode
    )
    mocks = env_bool("USE_MOCKS", default=False) if use_mocks is None else use_mocks
    tracing = (
        env_bool("ENABLE_TRACING", default=True)
        if enable_tracing is None
        else enable_tracing
    )

    errors: list[str] = []
    if not practical:
        errors.append(
            "ENABLE_PRACTICAL_MODE must be true in production; "
            "the public legacy hunt is development-only"
        )

    for name in (
        "GOOGLE_API_KEY",
        "PHOENIX_API_KEY",
        "PHOENIX_COLLECTOR_ENDPOINT",
        "JOB_HUNT_DATA_KEYS",
    ):
        if not os.getenv(name, "").strip():
            errors.append(f"{name} is required when ENVIRONMENT=production")

    if not any(
        os.getenv(name, "").strip()
        for name in ("SERPAPI_API_KEY", "SERPAPI_KEY")
    ):
        errors.append(
            "SERPAPI_API_KEY or SERPAPI_KEY is required when ENVIRONMENT=production"
        )
    if mocks:
        errors.append("USE_MOCKS must be false when ENVIRONMENT=production")
    if not tracing:
        errors.append("ENABLE_TRACING must not be false when ENVIRONMENT=production")
    if not env_bool("GEMINI_PAID_SERVICE_ACK", default=False):
        errors.append(
            "GEMINI_PAID_SERVICE_ACK must be true: "
            "resumes must not use unpaid Gemini quota"
        )
    if env_bool("ENABLE_TRACE_DRAFT_CONTENT", default=False):
        errors.append("ENABLE_TRACE_DRAFT_CONTENT must be false in production")

    if practical:
        try:
            resolve_database_url(required=True, production=True)
        except DatabaseConfigError as exc:
            errors.append(str(exc))

    return errors


def validate_production_runtime(
    *,
    practical_mode: bool | None = None,
    use_mocks: bool | None = None,
    enable_tracing: bool | None = None,
) -> None:
    """Refuse unsafe provider work before any process claims or accepts work."""

    errors = production_runtime_errors(
        practical_mode=practical_mode,
        use_mocks=use_mocks,
        enable_tracing=enable_tracing,
    )
    if errors:
        raise RuntimeError("Invalid production runtime config: " + "; ".join(errors))


__all__ = ["production_runtime_errors", "validate_production_runtime"]
