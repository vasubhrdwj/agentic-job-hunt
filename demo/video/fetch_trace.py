"""Fetch one real Phoenix trace and save a capture-safe subset."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from job_hunt_agent import mcp_client  # noqa: E402
from job_hunt_agent.tracing import DEFAULT_PROJECT_NAME  # noqa: E402


OUTPUT = ROOT / "demo" / "video" / "build" / "trace.json"
CANONICAL = json.loads((ROOT / "demo" / "canonical_run.json").read_text(encoding="utf-8"))

SAFE_ATTRIBUTES = {
    "job_hunt.run_id",
    "job_hunt.criteria.keywords",
    "job_hunt.criteria.locations",
    "job_hunt.criteria.seniority",
    "job_hunt.roles.count",
    "job_hunt.outreach.count",
    "job_hunt.role.company",
    "job_hunt.role.title",
    "job_hunt.role.keywords",
    "job_hunt.person.title",
    "job_hunt.person.source",
    "job_hunt.rag.use_self_rag",
    "job_hunt.eval.composite_score",
    "job_hunt.draft.output_text",
}


def context_value(span: dict[str, Any], key: str) -> str:
    context = span.get("context")
    if isinstance(context, dict):
        return str(context.get(key) or "")
    return str(span.get(key) or "")


def parent_id(span: dict[str, Any]) -> str:
    parent = span.get("parent_id") or span.get("parent_span_id")
    if parent:
        return str(parent)
    parent_context = span.get("parent_context")
    if isinstance(parent_context, dict):
        return str(parent_context.get("span_id") or "")
    return ""


def safe_span(span: dict[str, Any]) -> dict[str, Any]:
    attributes = mcp_client._span_attributes(span)
    safe_attributes = {
        key: value
        for key, value in attributes.items()
        if key in SAFE_ATTRIBUTES
    }
    output = str(safe_attributes.get("job_hunt.draft.output_text", ""))
    if output:
        safe_attributes["job_hunt.draft.output_text"] = output[:360]
    return {
        "name": str(span.get("name") or span.get("span_name") or "span"),
        "span_id": context_value(span, "span_id"),
        "trace_id": context_value(span, "trace_id"),
        "parent_id": parent_id(span),
        "start_time": span.get("start_time"),
        "end_time": span.get("end_time"),
        "attributes": safe_attributes,
    }


async def main() -> None:
    load_dotenv(ROOT / ".env")
    config = mcp_client._build_config(
        project=DEFAULT_PROJECT_NAME,
        min_limit=1000,
        timeout_s=20,
    )
    spans = await mcp_client._fetch_spans_rest(config)
    roots = [
        span
        for span in spans
        if mcp_client._span_attributes(span).get("job_hunt.run_id")
    ]
    if not roots:
        raise RuntimeError("No traced job-hunt runs were found in Phoenix")

    canonical_id = CANONICAL["run_id"]
    root = next(
        (
            span
            for span in roots
            if mcp_client._span_attributes(span).get("job_hunt.run_id") == canonical_id
        ),
        roots[0],
    )
    trace_id = context_value(root, "trace_id")
    trace_spans = [safe_span(span) for span in spans if context_value(span, "trace_id") == trace_id]
    trace_spans.sort(key=lambda span: str(span.get("start_time") or ""))

    payload = {
        "project": DEFAULT_PROJECT_NAME,
        "trace_id": trace_id,
        "run_id": mcp_client._span_attributes(root).get("job_hunt.run_id"),
        "spans": trace_spans,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved {len(trace_spans)} real Phoenix spans to {OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
