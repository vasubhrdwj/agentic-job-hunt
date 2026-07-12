"""Small, side-effect-free runtime configuration helpers."""

from __future__ import annotations

import os


TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
FALSY_ENV_VALUES = {"0", "false", "no", "off"}


def env_bool(name: str, *, default: bool = False) -> bool:
    """Read a conventional boolean environment value."""

    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in TRUTHY_ENV_VALUES:
        return True
    if normalized in FALSY_ENV_VALUES:
        return False
    return default


def is_production() -> bool:
    return os.getenv("ENVIRONMENT", "").strip().lower() == "production"


def practical_mode_enabled() -> bool:
    """Select the Postgres product path without silently enabling it in dev."""

    return env_bool("ENABLE_PRACTICAL_MODE", default=is_production())
