"""Deterministic entry point for the job-hunt pipeline."""

from __future__ import annotations

import argparse
import logging
import os
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from uuid import uuid4

from .matching import ResumeFitScorer
from .schemas import HuntResult, JobCriteria, OutreachDraft, PastDraft, Person, Role
from .sources.registry import load_company_pack
from .sources.resolver import SourceResolver
from .tools.registry import build_pipeline_tools


DEFAULT_MAX_ROLES = 3
DEFAULT_MAX_REFERRALS_PER_ROLE = 5
DRAFT_OUTPUT_ATTRIBUTE_LIMIT = 2_000
LOGGER = logging.getLogger(__name__)


def run_hunt(
    resume_text: str,
    criteria: JobCriteria | dict[str, Any],
    *,
    run_id: str | None = None,
    max_roles: int = DEFAULT_MAX_ROLES,
    max_referrals_per_role: int = DEFAULT_MAX_REFERRALS_PER_ROLE,
    use_mocks: bool = False,
    use_self_rag: bool = True,
    enable_tracing: bool = False,
    pack: str | None = None,
) -> HuntResult:
    """Run job search, referral discovery, and message drafting.

    ``use_self_rag`` controls V8 self-retrieval inside ``draft_message``. Pass
    False for the V10 round-1 baseline so the demo can A/B compare against the
    same input.
    """
    criteria = JobCriteria.model_validate(criteria)
    run_id = run_id or uuid4().hex
    tools = build_pipeline_tools(use_mocks=use_mocks)
    tracer = _build_tracer(enable_tracing)
    exemplar_cache: dict[tuple[frozenset[str], int], list[PastDraft]] = {}

    with _maybe_span(
        tracer,
        "run_hunt",
        {
            "job_hunt.run_id": run_id,
            "job_hunt.criteria.keywords": ", ".join(criteria.role_keywords),
            "job_hunt.criteria.locations": ", ".join(criteria.location),
            "job_hunt.criteria.seniority": criteria.seniority,
            "job_hunt.max_roles": max_roles,
            "job_hunt.max_referrals_per_role": max_referrals_per_role,
            "job_hunt.use_mocks": use_mocks,
            "job_hunt.use_self_rag": use_self_rag,
            "job_hunt.pack": pack or "",
        },
    ) as run_span:
        with _maybe_span(tracer, "search_jobs"):
            if pack and not use_mocks:
                registry = load_company_pack(pack)
                discovered = SourceResolver().fetch_registry_roles(
                    registry,
                    criteria,
                    max_per_company=max_roles,
                    max_total=max(max_roles * 5, max_roles),
                    allow_fallback=False,
                    require_first_party=True,
                )
            else:
                discovered = [
                    Role.model_validate(role)
                    for role in tools.search_jobs(criteria)
                ]
            roles = ResumeFitScorer().rank_roles(
                resume_text,
                discovered,
            )[:max_roles]

        outreach: list[OutreachDraft] = []
        for role in roles:
            with _maybe_span(
                tracer,
                "find_referrals",
                {
                    "job_hunt.role.company": role.company,
                    "job_hunt.role.title": role.title,
                    "job_hunt.role.url": role.url,
                },
            ):
                people: list[Person] = []
                for candidate in tools.find_referrals(role):
                    person = Person.model_validate(candidate)
                    if (
                        not person.verified_current_employer
                        or person.confidence < 0.5
                    ):
                        LOGGER.warning(
                            "Omitting unverified referral candidate %r for %s.",
                            person.name,
                            role.company,
                        )
                        continue
                    people.append(person)
                    if len(people) >= max_referrals_per_role:
                        break

            for person in people:
                with _maybe_span(
                    tracer,
                    "draft_message",
                    {
                        "job_hunt.role.company": role.company,
                        "job_hunt.role.title": role.title,
                        "job_hunt.role.keywords": tuple(criteria.role_keywords),
                        "job_hunt.person.source": person.source,
                        "job_hunt.person.title": person.title,
                        "job_hunt.rag.use_self_rag": use_self_rag,
                    },
                ) as draft_span:
                    message = tools.draft_message(
                        role,
                        person,
                        resume_text,
                        keywords=tuple(criteria.role_keywords),
                        use_self_rag=use_self_rag,
                        exemplar_cache=exemplar_cache,
                    )
                    if _trace_draft_content_enabled():
                        draft_span.set_attribute(
                            "job_hunt.draft.output_text",
                            _trim_attribute(str(message).strip()),
                        )
                    evaluation = (
                        tools.score_draft(role, person, str(message).strip())
                        if tools.score_draft is not None
                        else None
                    )
                    if evaluation is not None:
                        # The composite must live on this draft_message span,
                        # not only the child eval span: query_past_drafts()
                        # reads it from here (fixtures/SEED_NOTES.md).
                        draft_span.set_attribute(
                            "job_hunt.eval.composite_score",
                            float(evaluation.composite),
                        )
                outreach.append(
                    OutreachDraft(
                        role=role,
                        person=person,
                        message=str(message).strip(),
                        eval_score=(
                            evaluation.composite if evaluation is not None else None
                        ),
                    )
                )

        run_span.set_attribute("job_hunt.roles.count", len(roles))
        run_span.set_attribute(
            "job_hunt.roles.fit_scores",
            tuple(float(role.fit_score or 0.0) for role in roles),
        )
        run_span.set_attribute("job_hunt.outreach.count", len(outreach))
        return HuntResult(run_id=run_id, roles=roles, outreach=outreach)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the job-hunt pipeline.")
    parser.add_argument("--resume", required=True, help="Path to a plain-text resume file.")
    parser.add_argument(
        "--keywords",
        required=True,
        help="Comma-separated role keywords, e.g. SCIM,IAM,identity.",
    )
    parser.add_argument(
        "--seniority",
        choices=["junior", "mid", "senior", "staff"],
        default="senior",
        help="Target seniority band.",
    )
    parser.add_argument(
        "--location",
        required=True,
        help="Comma-separated locations, e.g. Remote-India,Hyderabad.",
    )
    parser.add_argument("--comp-min-lpa", type=int, help="Optional minimum comp in LPA.")
    parser.add_argument("--comp-max-lpa", type=int, help="Optional maximum comp in LPA.")
    parser.add_argument("--max-roles", type=int, default=DEFAULT_MAX_ROLES)
    parser.add_argument("--max-referrals", type=int, default=DEFAULT_MAX_REFERRALS_PER_ROLE)
    parser.add_argument(
        "--pack",
        default="backend_india",
        help="Company pack used for first-party discovery. Pass an empty value for legacy search.",
    )
    parser.add_argument(
        "--employment-types",
        default="full_time",
        help="Comma-separated: full_time,contract,intern,unknown.",
    )
    parser.add_argument("--max-age-days", type=int, default=45)
    parser.add_argument(
        "--use-mocks",
        action="store_true",
        help="Use the all-mock tool pipeline for fast local smoke tests.",
    )
    parser.add_argument(
        "--no-self-rag",
        dest="use_self_rag",
        action="store_false",
        help="Disable V8 self-RAG retrieval. Use for the V10 round-1 baseline.",
    )
    parser.set_defaults(use_self_rag=True)
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Emit Phoenix/OpenTelemetry spans for this deterministic pipeline run.",
    )
    parser.add_argument(
        "--run-id",
        help="Optional stable run id. If omitted, one is generated and printed.",
    )
    return parser.parse_args()


def criteria_from_args(args: argparse.Namespace) -> JobCriteria:
    """Build JobCriteria from CLI arguments."""
    return JobCriteria(
        role_keywords=_split_csv(args.keywords),
        seniority=args.seniority,
        location=_split_csv(args.location),
        comp_min_lpa=args.comp_min_lpa,
        comp_max_lpa=args.comp_max_lpa,
        employment_types=_split_csv(getattr(args, "employment_types", "full_time")),
        max_age_days=getattr(args, "max_age_days", 45),
    )


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _trim_attribute(value: str, limit: int = DRAFT_OUTPUT_ATTRIBUTE_LIMIT) -> str:
    """Keep custom trace attributes comfortably below exporter/UI limits."""
    if len(value) <= limit:
        return value
    return value[:limit].rstrip()


def _trace_draft_content_enabled() -> bool:
    """Draft text is private by default; curated demo seeding can opt in."""

    return os.getenv("ENABLE_TRACE_DRAFT_CONTENT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def main() -> None:
    args = parse_args()
    resume_text = Path(args.resume).read_text(encoding="utf-8")
    result = run_hunt(
        resume_text=resume_text,
        criteria=criteria_from_args(args),
        run_id=args.run_id,
        max_roles=args.max_roles,
        max_referrals_per_role=args.max_referrals,
        use_mocks=args.use_mocks,
        use_self_rag=args.use_self_rag,
        enable_tracing=args.trace,
        pack=args.pack or None,
    )
    if args.trace:
        from .tracing import flush_phoenix_tracing

        flush_phoenix_tracing()
    print(f"run_id: {result.run_id}")
    print(result.model_dump_json(indent=2))


class _NoopSpan:
    def set_attribute(self, key: str, value: Any) -> None:
        return None


def _build_tracer(enable_tracing: bool) -> Any | None:
    if not enable_tracing:
        return None

    from opentelemetry import trace

    from .tracing import configure_phoenix_tracing

    configure_phoenix_tracing()
    return trace.get_tracer(__name__)


def _maybe_span(tracer: Any | None, name: str, attributes: dict[str, Any] | None = None) -> Any:
    if tracer is None:
        return nullcontext(_NoopSpan())

    span = tracer.start_as_current_span(
        name,
        record_exception=False,
        set_status_on_exception=False,
    )
    manager = span.__enter__()
    if attributes:
        for key, value in attributes.items():
            manager.set_attribute(key, value)
    return _SpanContext(span, manager)


class _SpanContext:
    def __init__(self, span_context: Any, span: Any) -> None:
        self._span_context = span_context
        self._span = span

    def __enter__(self) -> Any:
        return self._span

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool | None:
        return self._span_context.__exit__(exc_type, exc, traceback)


if __name__ == "__main__":
    main()
