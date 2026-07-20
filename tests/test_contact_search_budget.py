"""Strict configuration tests for shared contact-search provider budgets."""

from __future__ import annotations

import pytest

from job_hunt_agent.contact_search_budget import (
    CONTACT_PLAN_GLOBAL_DAILY_LIMIT_ENV,
    CONTACT_PLAN_GLOBAL_MONTHLY_LIMIT_ENV,
    CONTACT_PLAN_OWNER_DAILY_LIMIT_ENV,
    DEFAULT_CONTACT_PLAN_GLOBAL_DAILY_LIMIT,
    DEFAULT_CONTACT_PLAN_GLOBAL_MONTHLY_LIMIT,
    DEFAULT_CONTACT_PLAN_OWNER_DAILY_LIMIT,
    ContactSearchBudget,
    ContactSearchBudgetConfigError,
    contact_search_budget_from_env,
)
from job_hunt_agent.production_runtime import production_core_errors


def test_contact_search_budget_uses_conservative_defaults() -> None:
    budget = contact_search_budget_from_env({})

    assert budget == ContactSearchBudget(
        owner_daily_limit=DEFAULT_CONTACT_PLAN_OWNER_DAILY_LIMIT,
        global_daily_limit=DEFAULT_CONTACT_PLAN_GLOBAL_DAILY_LIMIT,
        global_monthly_limit=DEFAULT_CONTACT_PLAN_GLOBAL_MONTHLY_LIMIT,
    )
    assert budget == ContactSearchBudget(
        owner_daily_limit=2,
        global_daily_limit=5,
        global_monthly_limit=80,
    )


def test_contact_search_budget_accepts_bounded_overrides() -> None:
    budget = contact_search_budget_from_env(
        {
            CONTACT_PLAN_OWNER_DAILY_LIMIT_ENV: " 3 ",
            CONTACT_PLAN_GLOBAL_DAILY_LIMIT_ENV: "12",
            CONTACT_PLAN_GLOBAL_MONTHLY_LIMIT_ENV: "240",
        }
    )

    assert budget == ContactSearchBudget(
        owner_daily_limit=3,
        global_daily_limit=12,
        global_monthly_limit=240,
    )


@pytest.mark.parametrize(
    ("environment", "expected_name"),
    [
        ({CONTACT_PLAN_OWNER_DAILY_LIMIT_ENV: ""}, CONTACT_PLAN_OWNER_DAILY_LIMIT_ENV),
        ({CONTACT_PLAN_OWNER_DAILY_LIMIT_ENV: "0"}, CONTACT_PLAN_OWNER_DAILY_LIMIT_ENV),
        (
            {CONTACT_PLAN_GLOBAL_DAILY_LIMIT_ENV: "not-an-int"},
            CONTACT_PLAN_GLOBAL_DAILY_LIMIT_ENV,
        ),
        (
            {CONTACT_PLAN_GLOBAL_MONTHLY_LIMIT_ENV: "10001"},
            CONTACT_PLAN_GLOBAL_MONTHLY_LIMIT_ENV,
        ),
    ],
)
def test_contact_search_budget_rejects_invalid_limits(
    environment: dict[str, str],
    expected_name: str,
) -> None:
    with pytest.raises(ContactSearchBudgetConfigError, match=expected_name):
        contact_search_budget_from_env(environment)


def test_production_startup_rejects_invalid_contact_search_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(CONTACT_PLAN_OWNER_DAILY_LIMIT_ENV, "0")

    errors = production_core_errors(practical_mode=False, use_mocks=False)

    assert any(CONTACT_PLAN_OWNER_DAILY_LIMIT_ENV in error for error in errors)
