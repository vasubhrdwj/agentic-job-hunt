import asyncio
import os
import urllib.error
import unittest
from unittest.mock import AsyncMock, patch

from job_hunt_agent import mcp_client
from job_hunt_agent.mcp_client import query_past_drafts


def _span(
    *,
    message: str,
    keywords: object = ("SCIM",),
    score: object = None,
    span_id: str = "span-1",
    trace_id: str = "trace-1",
    title: str = "Senior Engineer, Identity",
    company: str = "Okta",
) -> dict[str, object]:
    attributes: dict[str, object] = {
        "job_hunt.role.company": company,
        "job_hunt.role.title": title,
        "job_hunt.role.keywords": keywords,
        "job_hunt.draft.output_text": message,
    }
    if score is not None:
        attributes["job_hunt.eval.composite_score"] = score

    return {
        "name": "draft_message",
        "context": {"span_id": span_id, "trace_id": trace_id},
        "attributes": attributes,
    }


class QueryPastDraftsTest(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_transport_parses_and_sorts_past_drafts(self) -> None:
        spans = [
            _span(message="low score", score=3.0, span_id="span-low"),
            _span(message="no score", score=None, span_id="span-none"),
            _span(message="high score", score="4.8", span_id="span-high"),
            _span(message="unrelated", keywords=("frontend",), score=5.0),
            _span(message="", score=5.0),
        ]

        with patch.object(
            mcp_client,
            "_fetch_spans_mcp",
            new=AsyncMock(return_value=spans),
        ):
            drafts = await query_past_drafts(["scim"], top_k=3, transport="mcp")

        self.assertEqual(
            [draft.message for draft in drafts],
            ["high score", "low score", "no score"],
        )
        self.assertEqual(drafts[0].eval_score, 4.8)
        self.assertEqual(drafts[0].matched_keywords, ["scim"])
        self.assertEqual(drafts[0].span_id, "span-high")

    async def test_mcp_failure_falls_back_to_rest(self) -> None:
        rest_spans = [_span(message="rest draft", score=4.2)]

        with (
            patch.object(
                mcp_client,
                "_fetch_spans_mcp",
                new=AsyncMock(side_effect=RuntimeError("mcp down")),
            ) as mcp_fetch,
            patch.object(
                mcp_client,
                "_fetch_spans_rest",
                new=AsyncMock(return_value=rest_spans),
            ) as rest_fetch,
        ):
            drafts = await query_past_drafts(["SCIM"], transport="mcp")

        mcp_fetch.assert_awaited_once()
        rest_fetch.assert_awaited_once()
        self.assertEqual([draft.message for draft in drafts], ["rest draft"])

    async def test_rest_failure_returns_empty_list(self) -> None:
        with patch.object(
            mcp_client,
            "_fetch_spans_rest",
            new=AsyncMock(side_effect=RuntimeError("auth failed")),
        ):
            drafts = await query_past_drafts(["SCIM"], transport="rest")

        self.assertEqual(drafts, [])

    async def test_empty_corpus_returns_empty_list(self) -> None:
        with patch.object(
            mcp_client,
            "_fetch_spans_rest",
            new=AsyncMock(return_value=[_span(message="unrelated", keywords=("frontend",))]),
        ):
            drafts = await query_past_drafts(["nonexistent"], transport="rest")

        self.assertEqual(drafts, [])

    async def test_rest_project_lookup_retries_with_resolved_identifier(self) -> None:
        mcp_client._project_identifier_cache.clear()
        config = mcp_client._PhoenixConfig(
            base_url="https://app.phoenix.arize.com/s/example",
            api_key="fake-key",
            project="job-hunt-agent",
            limit=100,
            timeout_s=1.5,
            start_time="2026-06-01T00:00:00Z",
            max_pages=1,
        )
        calls: list[str] = []

        def fake_fetch_json(url: str, config: mcp_client._PhoenixConfig) -> dict[str, object]:
            calls.append(url)
            if url.endswith(
                "/v1/projects/job-hunt-agent/spans"
                "?limit=100&start_time=2026-06-01T00%3A00%3A00Z"
            ):
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            if url.endswith("/v1/projects?limit=100"):
                return {"data": [{"name": "job-hunt-agent", "id": "project-123"}]}
            if url.endswith(
                "/v1/projects/project-123/spans"
                "?limit=100&start_time=2026-06-01T00%3A00%3A00Z"
            ):
                return {"data": [_span(message="resolved project draft")]}
            raise AssertionError(f"unexpected URL: {url}")

        with patch.object(mcp_client, "_fetch_json", side_effect=fake_fetch_json):
            spans = await asyncio.to_thread(mcp_client._fetch_spans_rest_sync, config)

        self.assertEqual(
            spans[0]["attributes"]["job_hunt.draft.output_text"],
            "resolved project draft",
        )
        self.assertEqual(
            calls,
            [
                "https://app.phoenix.arize.com/s/example/v1/projects/job-hunt-agent/spans"
                "?limit=100&start_time=2026-06-01T00%3A00%3A00Z",
                "https://app.phoenix.arize.com/s/example/v1/projects?limit=100",
                "https://app.phoenix.arize.com/s/example/v1/projects/project-123/spans"
                "?limit=100&start_time=2026-06-01T00%3A00%3A00Z",
            ],
        )


class LivePhoenixTest(unittest.TestCase):
    def test_live_phoenix_query_smoke(self) -> None:
        if os.getenv("RUN_LIVE_PHOENIX") != "1":
            self.skipTest("set RUN_LIVE_PHOENIX=1 to run the live Phoenix test")
        if not os.getenv("PHOENIX_COLLECTOR_ENDPOINT"):
            self.skipTest("PHOENIX_COLLECTOR_ENDPOINT is not set")

        drafts = asyncio.run(query_past_drafts(["SCIM"], top_k=3, timeout_s=3.0))

        self.assertIsInstance(drafts, list)
        for draft in drafts:
            self.assertTrue(draft.message)


if __name__ == "__main__":
    unittest.main()


def test_resolve_timeout_prefers_explicit_then_env(monkeypatch) -> None:
    from job_hunt_agent.mcp_client import DEFAULT_QUERY_TIMEOUT_SECONDS, _resolve_timeout

    monkeypatch.setenv("PHOENIX_QUERY_TIMEOUT_S", "8")
    assert _resolve_timeout(2.0) == 2.0
    assert _resolve_timeout(None) == 8.0

    monkeypatch.setenv("PHOENIX_QUERY_TIMEOUT_S", "not-a-number")
    assert _resolve_timeout(None) == DEFAULT_QUERY_TIMEOUT_SECONDS

    monkeypatch.delenv("PHOENIX_QUERY_TIMEOUT_S")
    assert _resolve_timeout(None) == DEFAULT_QUERY_TIMEOUT_SECONDS
