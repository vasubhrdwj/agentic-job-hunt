"""Bounded configuration for provider-backed contact-search plans."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


CONTACT_PLAN_OWNER_DAILY_LIMIT_ENV = "JOB_HUNT_CONTACT_PLAN_OWNER_DAILY_LIMIT"
CONTACT_PLAN_GLOBAL_DAILY_LIMIT_ENV = "JOB_HUNT_CONTACT_PLAN_GLOBAL_DAILY_LIMIT"
CONTACT_PLAN_GLOBAL_MONTHLY_LIMIT_ENV = "JOB_HUNT_CONTACT_PLAN_GLOBAL_MONTHLY_LIMIT"

DEFAULT_CONTACT_PLAN_OWNER_DAILY_LIMIT = 2
DEFAULT_CONTACT_PLAN_GLOBAL_DAILY_LIMIT = 5
DEFAULT_CONTACT_PLAN_GLOBAL_MONTHLY_LIMIT = 80

MAX_CONTACT_PLAN_OWNER_DAILY_LIMIT = 100
MAX_CONTACT_PLAN_GLOBAL_DAILY_LIMIT = 1_000
MAX_CONTACT_PLAN_GLOBAL_MONTHLY_LIMIT = 10_000


class ContactSearchBudgetConfigError(ValueError):
    """Contact-search budget configuration is invalid."""


@dataclass(frozen=True)
class ContactSearchBudget:
    """Maximum new contact plans allowed in UTC calendar windows."""

    owner_daily_limit: int = DEFAULT_CONTACT_PLAN_OWNER_DAILY_LIMIT
    global_daily_limit: int = DEFAULT_CONTACT_PLAN_GLOBAL_DAILY_LIMIT
    global_monthly_limit: int = DEFAULT_CONTACT_PLAN_GLOBAL_MONTHLY_LIMIT

    def __post_init__(self) -> None:
        _validate_limit(
            CONTACT_PLAN_OWNER_DAILY_LIMIT_ENV,
            self.owner_daily_limit,
            maximum=MAX_CONTACT_PLAN_OWNER_DAILY_LIMIT,
        )
        _validate_limit(
            CONTACT_PLAN_GLOBAL_DAILY_LIMIT_ENV,
            self.global_daily_limit,
            maximum=MAX_CONTACT_PLAN_GLOBAL_DAILY_LIMIT,
        )
        _validate_limit(
            CONTACT_PLAN_GLOBAL_MONTHLY_LIMIT_ENV,
            self.global_monthly_limit,
            maximum=MAX_CONTACT_PLAN_GLOBAL_MONTHLY_LIMIT,
        )


def contact_search_budget_from_env(
    environ: Mapping[str, str] | None = None,
) -> ContactSearchBudget:
    """Load strict limits while keeping conservative defaults."""

    source = os.environ if environ is None else environ
    return ContactSearchBudget(
        owner_daily_limit=_read_limit(
            source,
            CONTACT_PLAN_OWNER_DAILY_LIMIT_ENV,
            default=DEFAULT_CONTACT_PLAN_OWNER_DAILY_LIMIT,
            maximum=MAX_CONTACT_PLAN_OWNER_DAILY_LIMIT,
        ),
        global_daily_limit=_read_limit(
            source,
            CONTACT_PLAN_GLOBAL_DAILY_LIMIT_ENV,
            default=DEFAULT_CONTACT_PLAN_GLOBAL_DAILY_LIMIT,
            maximum=MAX_CONTACT_PLAN_GLOBAL_DAILY_LIMIT,
        ),
        global_monthly_limit=_read_limit(
            source,
            CONTACT_PLAN_GLOBAL_MONTHLY_LIMIT_ENV,
            default=DEFAULT_CONTACT_PLAN_GLOBAL_MONTHLY_LIMIT,
            maximum=MAX_CONTACT_PLAN_GLOBAL_MONTHLY_LIMIT,
        ),
    )


def _read_limit(
    environ: Mapping[str, str],
    name: str,
    *,
    default: int,
    maximum: int,
) -> int:
    raw = environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip()
    try:
        value = int(normalized)
    except (TypeError, ValueError) as exc:
        raise ContactSearchBudgetConfigError(
            f"{name} must be an integer between 1 and {maximum}"
        ) from exc
    _validate_limit(name, value, maximum=maximum)
    return value


def _validate_limit(name: str, value: int, *, maximum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise ContactSearchBudgetConfigError(
            f"{name} must be an integer between 1 and {maximum}"
        )


__all__ = [
    "CONTACT_PLAN_GLOBAL_DAILY_LIMIT_ENV",
    "CONTACT_PLAN_GLOBAL_MONTHLY_LIMIT_ENV",
    "CONTACT_PLAN_OWNER_DAILY_LIMIT_ENV",
    "DEFAULT_CONTACT_PLAN_GLOBAL_DAILY_LIMIT",
    "DEFAULT_CONTACT_PLAN_GLOBAL_MONTHLY_LIMIT",
    "DEFAULT_CONTACT_PLAN_OWNER_DAILY_LIMIT",
    "ContactSearchBudget",
    "ContactSearchBudgetConfigError",
    "contact_search_budget_from_env",
]
