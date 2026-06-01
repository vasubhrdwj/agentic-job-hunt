import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from job_hunt_agent.schemas import PastDraft
from scripts import seed_phoenix


SEED_FILE = Path(__file__).resolve().parents[1] / "fixtures" / "seed_outreach.jsonl"

# These regexes intentionally mirror fixtures/SEED_NOTES.md. Update both when
# adding a new seed pattern; brittle tests keep the demo rubric explicit.
SPEC_SIGNAL = re.compile(
    r"\b("
    r"RFC\s?\d{3,5}|"
    r"SCIM\s?2\.0|"
    r"OIDC|"
    r"Next\.js App Router|"
    r"React Server Components|"
    r"RAG retrieval|"
    r"embedding rerankers|"
    r"eval rubric"
    r")\b",
    re.IGNORECASE,
)
NAMED_TEAM_SIGNAL = re.compile(
    r"\b("
    r"Lifecycle Management|"
    r"Identity Platform|"
    r"Integrations|"
    r"Frontend Platform|"
    r"Growth Engineering|"
    r"Applied AI"
    r") team\b",
    re.IGNORECASE,
)
TIME_BOUND_SIGNAL = re.compile(
    r"\b(15 min|30 min|Mon|Tue|Wed|Thu|Fri|next week|tomorrow)\b",
    re.IGNORECASE,
)


def _entries() -> list[seed_phoenix.SeedDraft]:
    return seed_phoenix.load_seed_file(SEED_FILE)


def _cluster(entry: seed_phoenix.SeedDraft) -> str:
    keywords = set(entry.role_keywords)
    if "SCIM" in keywords:
        return "SCIM"
    if "React" in keywords:
        return "React"
    if "LLM" in keywords:
        return "LLM"
    raise AssertionError(f"unknown cluster for {entry.id}: {entry.role_keywords}")


def test_seed_jsonl_loads_with_unique_ids() -> None:
    entries = _entries()

    assert len(entries) == 18
    assert len({entry.id for entry in entries}) == len(entries)
    assert all(entry.pattern_tags == sorted(set(entry.pattern_tags)) for entry in entries)


def test_seed_score_distribution_and_cluster_split() -> None:
    counts: dict[tuple[str, str], int] = {}
    for entry in _entries():
        counts[(_cluster(entry), entry.score_band)] = counts.get(
            (_cluster(entry), entry.score_band),
            0,
        ) + 1

    assert counts == {
        ("SCIM", "high"): 3,
        ("SCIM", "mid"): 3,
        ("SCIM", "low"): 2,
        ("React", "high"): 2,
        ("React", "mid"): 2,
        ("React", "low"): 1,
        ("LLM", "high"): 1,
        ("LLM", "mid"): 1,
        ("LLM", "low"): 3,
    }


def test_seed_pattern_tags_match_message_text() -> None:
    signal_checks = {
        "cites_spec": SPEC_SIGNAL,
        "names_team": NAMED_TEAM_SIGNAL,
        "specific_ask": TIME_BOUND_SIGNAL,
    }
    expected_by_band = {"high": 3, "mid": 1, "low": 0}

    for entry in _entries():
        hits = {
            tag
            for tag, pattern in signal_checks.items()
            if pattern.search(entry.message)
        }
        assert hits == set(entry.pattern_tags), entry.id
        assert len(hits) == expected_by_band[entry.score_band], entry.id


def test_seed_model_rejects_score_band_mismatch() -> None:
    raw = _entries()[0].model_dump()
    raw["score_band"] = "low"

    try:
        seed_phoenix.SeedDraft.model_validate(raw)
    except ValueError as exc:
        assert "low seed scores" in str(exc)
    else:
        raise AssertionError("SeedDraft accepted a score outside its declared band")


class RecordingSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}

    def __enter__(self) -> "RecordingSpan":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


class RecordingTracer:
    def __init__(self) -> None:
        self.spans: list[tuple[str, RecordingSpan]] = []

    def start_as_current_span(self, name: str) -> RecordingSpan:
        span = RecordingSpan()
        self.spans.append((name, span))
        return span


def test_emit_seed_span_writes_v6_contract_attributes() -> None:
    entry = _entries()[0]
    tracer = RecordingTracer()

    seed_phoenix.emit_seed_span(entry, tracer)

    name, span = tracer.spans[0]
    assert name == "draft_message"
    assert span.attributes["job_hunt.draft.output_text"] == entry.message
    assert span.attributes["job_hunt.role.keywords"] == tuple(entry.role_keywords)
    assert span.attributes["job_hunt.role.title"] == entry.role_title
    assert span.attributes["job_hunt.role.company"] == entry.role_company
    assert span.attributes["job_hunt.eval.composite_score"] == entry.eval_score
    assert span.attributes["job_hunt.seed.id"] == entry.id
    assert span.attributes["job_hunt.seed.tag"] == "seed"
    assert span.attributes["job_hunt.seed.synthetic"] is True
    assert span.attributes["job_hunt.seed.score_band"] == entry.score_band
    assert span.attributes["job_hunt.outcome"] == entry.outcome


def test_dry_run_skips_upload_and_verify() -> None:
    with (
        patch.object(seed_phoenix, "configure_phoenix_tracing") as configure,
        patch.object(seed_phoenix, "query_past_drafts", new=AsyncMock()) as query,
    ):
        result = seed_phoenix.main(["--seed-file", str(SEED_FILE), "--dry-run", "--verify"])

    assert result == 0
    configure.assert_not_called()
    query.assert_not_called()


def test_verify_only_skips_upload() -> None:
    with (
        patch.object(seed_phoenix, "upload_seed_spans") as upload,
        patch.object(seed_phoenix, "verify_seed_retrieval", new=AsyncMock(return_value=True)) as verify,
    ):
        result = seed_phoenix.main(["--project", "job-hunt-agent-dev", "--verify-only"])

    assert result == 0
    upload.assert_not_called()
    verify.assert_awaited_once_with(project="job-hunt-agent-dev")


def test_real_upload_requires_allow_duplicates() -> None:
    with patch.object(seed_phoenix, "configure_phoenix_tracing") as configure:
        result = seed_phoenix.main(["--seed-file", str(SEED_FILE)])

    assert result == 2
    configure.assert_not_called()


def test_upload_seed_spans_flushes_with_timeout() -> None:
    entries = _entries()[:2]
    provider = Mock()
    provider.force_flush.return_value = True
    tracer = RecordingTracer()

    with (
        patch.object(seed_phoenix, "configure_phoenix_tracing", return_value=provider),
        patch.object(seed_phoenix.trace, "get_tracer", return_value=tracer),
    ):
        result = seed_phoenix.upload_seed_spans(entries, project="job-hunt-agent-dev")

    assert result is True
    assert [name for name, _ in tracer.spans] == ["draft_message", "draft_message"]
    provider.force_flush.assert_called_once_with(timeout_millis=10_000)


def test_verify_seed_retrieval_retries_until_indexed() -> None:
    calls: list[str] = []

    async def fake_query(keywords, top_k, project, timeout_s):  # noqa: ANN001
        calls.append(keywords[0])
        if len(calls) <= 3:
            return []
        if len(calls) <= 6:
            return [
                PastDraft(
                    message="mid seed message",
                    role_title="Role",
                    company="Company",
                    eval_score=3.2,
                    matched_keywords=[keywords[0]],
                    span_id="span-mid",
                    trace_id="trace",
                )
            ]
        return [
            PastDraft(
                message="seed message",
                role_title="Role",
                company="Company",
                eval_score=4.5,
                matched_keywords=[keywords[0]],
                span_id="span",
                trace_id="trace",
            )
        ]

    with (
        patch.object(seed_phoenix, "query_past_drafts", side_effect=fake_query),
        patch.object(seed_phoenix.time, "sleep", return_value=None),
    ):
        ok = asyncio.run(
            seed_phoenix.verify_seed_retrieval(
                project="job-hunt-agent-dev",
                attempts=3,
                sleep_seconds=0,
            )
        )

    assert ok is True
    assert calls == [
        "SCIM",
        "React",
        "LLM",
        "SCIM",
        "React",
        "LLM",
        "SCIM",
        "React",
        "LLM",
    ]
