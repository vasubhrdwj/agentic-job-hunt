"""Regression checks for dialect-safe Alembic batch operations."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest


MIGRATION_MODULES = (
    "migrations.versions.20260714_0011_application_submission",
    "migrations.versions.20260715_0012_application_progress",
    "migrations.versions.20260715_0013_interview_rounds",
    "migrations.versions.20260715_0014_application_milestone_corrections",
)


@pytest.mark.parametrize("module_name", MIGRATION_MODULES)
def test_batch_recreate_is_limited_to_sqlite(
    module_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = importlib.import_module(module_name)

    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")),
    )
    assert migration._batch_recreate_mode() == "always"

    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
    )
    assert migration._batch_recreate_mode() == "auto"
