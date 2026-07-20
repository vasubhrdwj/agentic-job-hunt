"""Shared fail-closed production checks for provider-consuming processes."""

from __future__ import annotations

import os

from .config import env_bool, is_production, practical_mode_enabled
from .contact_search_budget import (
    ContactSearchBudgetConfigError,
    contact_search_budget_from_env,
)
from .database import DatabaseConfigError, resolve_database_url


def production_core_errors(
    *,
    practical_mode: bool | None = None,
    use_mocks: bool | None = None,
) -> list[str]:
    """Return errors that make the private web workspace unsafe to start."""

    if not is_production():
        return []

    practical = (
        practical_mode_enabled() if practical_mode is None else practical_mode
    )
    mocks = env_bool("USE_MOCKS", default=False) if use_mocks is None else use_mocks

    errors: list[str] = []
    if not practical:
        errors.append(
            "ENABLE_PRACTICAL_MODE must be true in production; "
            "the public legacy hunt is development-only"
        )

    if not os.getenv("JOB_HUNT_DATA_KEYS", "").strip():
        errors.append("JOB_HUNT_DATA_KEYS is required when ENVIRONMENT=production")
    if mocks:
        errors.append("USE_MOCKS must be false when ENVIRONMENT=production")
    if env_bool("ENABLE_TRACE_DRAFT_CONTENT", default=False):
        errors.append("ENABLE_TRACE_DRAFT_CONTENT must be false in production")

    try:
        contact_search_budget_from_env()
    except ContactSearchBudgetConfigError as exc:
        errors.append(str(exc))

    if practical:
        try:
            resolve_database_url(required=True, production=True)
        except DatabaseConfigError as exc:
            errors.append(str(exc))

    return errors


def production_provider_errors(
    *,
    enable_tracing: bool | None = None,
) -> list[str]:
    """Return errors that must block provider-consuming worker processes."""

    if not is_production():
        return []

    tracing = (
        env_bool("ENABLE_TRACING", default=True)
        if enable_tracing is None
        else enable_tracing
    )
    errors: list[str] = []
    for name in ("GOOGLE_API_KEY", "PHOENIX_API_KEY", "PHOENIX_COLLECTOR_ENDPOINT"):
        if not os.getenv(name, "").strip():
            errors.append(f"{name} is required for the production worker")
    if not any(
        os.getenv(name, "").strip()
        for name in ("SERPAPI_API_KEY", "SERPAPI_KEY")
    ):
        errors.append("SERPAPI_API_KEY or SERPAPI_KEY is required for the production worker")
    if not tracing:
        errors.append("ENABLE_TRACING must not be false for the production worker")
    if not env_bool("GEMINI_PAID_SERVICE_ACK", default=False):
        errors.append(
            "GEMINI_PAID_SERVICE_ACK must be true: "
            "resumes must not use unpaid Gemini quota"
        )
    return errors


def production_contact_provider_errors() -> list[str]:
    """Return only the configuration errors needed for contact discovery."""

    if not is_production():
        return []
    if not any(
        os.getenv(name, "").strip()
        for name in ("SERPAPI_API_KEY", "SERPAPI_KEY")
    ):
        return [
            "SERPAPI_API_KEY or SERPAPI_KEY is required for contact discovery"
        ]
    return []


def production_runtime_errors(
    *,
    practical_mode: bool | None = None,
    use_mocks: bool | None = None,
    enable_tracing: bool | None = None,
    require_providers: bool = True,
) -> list[str]:
    """Return the complete production-worker configuration errors."""

    errors = [
        *production_core_errors(
            practical_mode=practical_mode,
            use_mocks=use_mocks,
        ),
    ]
    if require_providers:
        errors.extend(production_provider_errors(enable_tracing=enable_tracing))
    return errors


def validate_production_runtime(
    *,
    practical_mode: bool | None = None,
    use_mocks: bool | None = None,
    enable_tracing: bool | None = None,
    require_providers: bool = True,
) -> None:
    """Refuse unsafe provider work before any process claims or accepts work."""

    errors = production_runtime_errors(
        practical_mode=practical_mode,
        use_mocks=use_mocks,
        enable_tracing=enable_tracing,
        require_providers=require_providers,
    )
    if errors:
        raise RuntimeError("Invalid production runtime config: " + "; ".join(errors))


def validate_contact_search_runtime(
    *,
    practical_mode: bool | None = None,
    use_mocks: bool | None = None,
) -> None:
    """Refuse unsafe contact work without requiring unrelated AI services."""

    errors = [
        *production_core_errors(
            practical_mode=practical_mode,
            use_mocks=use_mocks,
        ),
        *production_contact_provider_errors(),
    ]
    if errors:
        raise RuntimeError("Invalid contact-search runtime config: " + "; ".join(errors))


__all__ = [
    "production_core_errors",
    "production_contact_provider_errors",
    "production_provider_errors",
    "production_runtime_errors",
    "validate_production_runtime",
    "validate_contact_search_runtime",
]
