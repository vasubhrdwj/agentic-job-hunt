import argparse
import json
import unittest
from unittest.mock import patch

from job_hunt_agent.run import criteria_from_args, run_hunt
from job_hunt_agent.schemas import HuntResult, JobCriteria, OutreachDraft, Person, Role
from job_hunt_agent.tools.registry import PipelineTools


class RunHuntTest(unittest.TestCase):
    def setUp(self) -> None:
        self.criteria = JobCriteria(
            role_keywords=["SCIM", "identity"],
            seniority="senior",
            location=["Remote-India"],
        )
        self.role = Role(
            company="Okta",
            title="Senior Software Engineer, Identity",
            url="https://www.linkedin.com/jobs/view/senior-software-engineer-identity-at-okta-123",
            location="Remote-India",
            summary="Build SCIM provisioning and lifecycle automation.",
            match_reason="Snippet matches SCIM and identity lifecycle work.",
        )
        self.person = Person(
            name="Priya Rao",
            title="Staff Engineer, Identity",
            company="Okta",
            profile_url="https://www.linkedin.com/in/priya-rao-identity",
            source="linkedin",
            why_relevant="Works near the identity systems described in the role.",
        )

    def test_run_hunt_returns_structured_result(self) -> None:
        tools = PipelineTools(
            search_jobs=lambda criteria: [self.role],
            find_referrals=lambda role: [self.person],
            draft_message=lambda role, person, resume_text: (
                f"Hi Priya, your {person.title} work at {role.company} lines up with my SCIM work."
            ),
        )

        with patch("job_hunt_agent.run.build_pipeline_tools", return_value=tools):
            result = run_hunt(
                resume_text="Built SCIM provisioning systems.",
                criteria=self.criteria,
            )

        self.assertIsInstance(result, HuntResult)
        self.assertEqual(len(result.roles), 1)
        self.assertEqual(len(result.outreach), 1)
        self.assertIsInstance(result.outreach[0], OutreachDraft)
        self.assertIn("SCIM", result.outreach[0].message)
        self.assertTrue(result.run_id)
        self.assertTrue(result.outreach[0].draft_id)

    def test_run_hunt_preserves_explicit_run_id(self) -> None:
        tools = PipelineTools(
            search_jobs=lambda criteria: [self.role],
            find_referrals=lambda role: [self.person],
            draft_message=lambda role, person, resume_text: "message",
        )

        with patch("job_hunt_agent.run.build_pipeline_tools", return_value=tools):
            result = run_hunt(
                resume_text="resume",
                criteria=self.criteria,
                run_id="fixed-run-id-123",
            )

        self.assertEqual(result.run_id, "fixed-run-id-123")

    def test_run_hunt_limits_roles_and_referrals(self) -> None:
        roles = [
            self.role.model_copy(update={"company": f"Company {index}"})
            for index in range(5)
        ]
        people = [
            self.person.model_copy(update={"name": f"Person {index}"})
            for index in range(4)
        ]
        tools = PipelineTools(
            search_jobs=lambda criteria: roles,
            find_referrals=lambda role: people,
            draft_message=lambda role, person, resume_text: "message",
        )

        with patch("job_hunt_agent.run.build_pipeline_tools", return_value=tools):
            result = run_hunt(
                resume_text="Built identity systems.",
                criteria=self.criteria,
                max_roles=2,
                max_referrals_per_role=2,
            )

        self.assertEqual(len(result.roles), 2)
        self.assertEqual(len(result.outreach), 4)

    def test_run_hunt_with_mocks_produces_three_by_three_outreach(self) -> None:
        result = run_hunt(
            resume_text="Built SCIM provisioning and identity automation services.",
            criteria=self.criteria,
            use_mocks=True,
        )

        self.assertEqual(len(result.roles), 3)
        self.assertEqual(len(result.outreach), 9)
        self.assertTrue(all(draft.message for draft in result.outreach))

    def test_run_hunt_can_emit_trace_spans_without_resume_attributes(self) -> None:
        spans: list[tuple[str, dict[str, object]]] = []

        class FakeSpan:
            def __init__(self, name: str) -> None:
                self.name = name
                self.attributes: dict[str, object] = {}

            def set_attribute(self, key: str, value: object) -> None:
                self.attributes[key] = value

        class FakeSpanContext:
            def __init__(self, name: str) -> None:
                self.span = FakeSpan(name)

            def __enter__(self) -> FakeSpan:
                return self.span

            def __exit__(self, exc_type, exc, traceback) -> None:
                spans.append((self.span.name, self.span.attributes))

        class FakeTracer:
            def start_as_current_span(self, name: str) -> FakeSpanContext:
                return FakeSpanContext(name)

        with patch("job_hunt_agent.run._build_tracer", return_value=FakeTracer()):
            result = run_hunt(
                resume_text="PRIVATE RESUME TEXT BUILT SCIM",
                criteria=self.criteria,
                run_id="trace-run-id",
                use_mocks=True,
                max_roles=1,
                max_referrals_per_role=1,
                enable_tracing=True,
            )

        self.assertEqual(len(result.outreach), 1)
        flattened_attributes = {
            value
            for _, attributes in spans
            for value in attributes.values()
            if isinstance(value, str)
        }
        self.assertNotIn("PRIVATE RESUME TEXT BUILT SCIM", flattened_attributes)
        self.assertIn("run_hunt", [name for name, _ in spans])
        run_span_attrs = next(attrs for name, attrs in spans if name == "run_hunt")
        self.assertEqual(run_span_attrs["job_hunt.run_id"], "trace-run-id")

    def test_criteria_from_args_parses_cli_values(self) -> None:
        args = argparse.Namespace(
            keywords="SCIM, IAM, identity",
            seniority="staff",
            location="Remote-India, Hyderabad",
            comp_min_lpa=30,
            comp_max_lpa=60,
        )

        criteria = criteria_from_args(args)

        self.assertEqual(criteria.role_keywords, ["SCIM", "IAM", "identity"])
        self.assertEqual(criteria.location, ["Remote-India", "Hyderabad"])
        self.assertEqual(criteria.seniority, "staff")
        self.assertEqual(criteria.comp_min_lpa, 30)

    def test_hunt_result_json_round_trip(self) -> None:
        result = run_hunt(
            resume_text="Built SCIM provisioning and identity automation services.",
            criteria=self.criteria,
            use_mocks=True,
        )

        payload = json.loads(result.model_dump_json())

        self.assertIn("roles", payload)
        self.assertIn("outreach", payload)
        self.assertEqual(len(payload["outreach"]), 9)


if __name__ == "__main__":
    unittest.main()
