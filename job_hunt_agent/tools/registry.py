"""Tool selection for the agent.

The V3 integration path is intentionally boring: real tools are preferred as
soon as their modules exist, while missing later-stage tools fall back to mocks
so the agent loop stays runnable during parallel development.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from importlib.util import find_spec
from typing import Any

from ..evals import score_draft
from .job_search import search_jobs
from .mocks import (
    draft_message_mock,
    find_referrals_mock,
    score_draft_mock,
    search_jobs_mock,
)


ToolCallable = Callable[..., Any]


@dataclass(frozen=True)
class PipelineTools:
    """Named tool bundle for deterministic orchestration code.

    ``score_draft`` is the V9 judge, not an agent-facing tool; it defaults to
    None so hand-built bundles (tests) opt in explicitly.
    """

    search_jobs: ToolCallable
    find_referrals: ToolCallable
    draft_message: ToolCallable
    score_draft: ToolCallable | None = None


def build_toolset(*, use_mocks: bool = False) -> list[ToolCallable]:
    """Return the tool list the ADK agent should expose."""
    tools = build_pipeline_tools(use_mocks=use_mocks)
    return [
        tools.search_jobs,
        tools.find_referrals,
        tools.draft_message,
    ]


def build_pipeline_tools(*, use_mocks: bool = False) -> PipelineTools:
    """Return named tools for non-agent orchestration."""
    if use_mocks:
        return PipelineTools(
            search_jobs=search_jobs_mock,
            find_referrals=find_referrals_mock,
            draft_message=draft_message_mock,
            score_draft=score_draft_mock,
        )

    return PipelineTools(
        search_jobs=search_jobs,
        find_referrals=_optional_tool("referrals", "find_referrals", find_referrals_mock),
        draft_message=_optional_tool("draft", "draft_message", draft_message_mock),
        score_draft=score_draft,
    )


def _optional_tool(module_name: str, attribute_name: str, fallback: ToolCallable) -> ToolCallable:
    module_path = f"{__package__}.{module_name}"
    if find_spec(module_path) is None:
        return fallback

    module = import_module(module_path)
    return getattr(module, attribute_name)
