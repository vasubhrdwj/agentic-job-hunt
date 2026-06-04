import os
import unittest
from unittest.mock import patch

from job_hunt_agent.schemas import PastDraft, Person, Role
from job_hunt_agent.tools import draft
from job_hunt_agent.tools.draft import draft_message


def _past_draft(message: str, score: float, span_id: str = "span-x") -> PastDraft:
    return PastDraft(
        message=message,
        role_title="Senior Engineer, Identity",
        company="Northstar Identity",
        eval_score=score,
        matched_keywords=["SCIM"],
        span_id=span_id,
        trace_id="trace-x",
    )


class DraftMessageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.role = Role(
            company="Okta",
            title="Senior Software Engineer, Lifecycle Management",
            url="https://www.linkedin.com/jobs/view/senior-software-engineer-identity-at-okta-123",
            location="Remote-India",
            summary="Build SCIM provisioning and lifecycle automation for enterprise customers.",
            match_reason="The role centers on SCIM 2.0 provisioning at scale.",
        )
        self.person = Person(
            name="Anika Rao",
            title="Staff Engineer, Lifecycle Management",
            company="Okta",
            profile_url="https://www.linkedin.com/in/anika-rao",
            source="linkedin",
            why_relevant="Owns lifecycle management work close to the role's SCIM scope.",
        )
        self.resume_text = "Built SCIM 2.0 provisioning and identity automation services in Go."

    def test_draft_message_uses_gemini_output_when_clean(self) -> None:
        generated = (
            "Hi Anika, I saw Okta's Lifecycle Management role and your Staff "
            "Engineer work seems close to the SCIM provisioning scope. I recently "
            "built SCIM 2.0 provisioning services in Go, so the team looks relevant. "
            "Would you be open to a quick pointer on whether this is the right team?\n\n"
            "Thanks,"
        )

        with (
            patch.object(draft, "_get_google_api_key", return_value="fake-key"),
            patch.object(draft, "_generate", return_value=generated),
        ):
            message = draft_message(self.role, self.person, self.resume_text)

        self.assertEqual(message, generated)
        self.assertNotIn("[", message)
        self.assertNotIn("Subject:", message)

    def test_draft_message_falls_back_when_google_key_missing(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(draft, "_load_dotenv_if_available", return_value=None),
        ):
            message = draft_message(self.role, self.person, self.resume_text)

        self.assertIn("Hi Anika", message)
        self.assertIn("SCIM", message)
        self.assertNotIn("[", message)

    def test_draft_message_rejects_placeholders(self) -> None:
        with (
            patch.object(draft, "_get_google_api_key", return_value="fake-key"),
            patch.object(draft, "_generate", return_value="Hi [Name], please refer me."),
        ):
            message = draft_message(self.role, self.person, self.resume_text)

        self.assertIn("Hi Anika", message)
        self.assertNotIn("[Name]", message)

    def test_clean_removes_subject_and_markdown_fences(self) -> None:
        raw = "```text\nSubject: Referral request\n\nHi Anika, this is a useful draft.\n\nThanks,\n```"

        cleaned = draft._clean(raw)

        self.assertNotIn("Subject:", cleaned)
        self.assertNotIn("```", cleaned)
        self.assertIn("Hi Anika", cleaned)

    def test_build_user_prompt_contains_role_person_and_resume_signals(self) -> None:
        prompt = draft._build_user_prompt(self.role, self.person, self.resume_text)

        self.assertIn("Okta", prompt)
        self.assertIn("Anika Rao", prompt)
        self.assertIn("SCIM 2.0 provisioning", prompt)


class SelfRagDraftMessageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.role = Role(
            company="Northstar Identity",
            title="Senior Engineer, Lifecycle Automation",
            url="https://example.com/job",
            location="Remote-India",
            summary="Build SCIM provisioning and lifecycle automation.",
            match_reason="Centers on SCIM 2.0 work at scale.",
        )
        self.person = Person(
            name="Anika Rao",
            title="Staff Engineer, Lifecycle Management",
            company="Northstar Identity",
            profile_url="https://example.com/anika",
            source="linkedin",
            why_relevant="Owns the lifecycle management team that ships provisioning.",
        )
        self.resume_text = "Built SCIM 2.0 RFC 7644 PATCH flows in Go."
        self.captured_prompts: list[str] = []

    def _capture_generate(self, role, person, resume_text, *, exemplars, api_key):  # noqa: ARG002
        prompt = draft._build_user_prompt(role, person, resume_text, exemplars=exemplars)
        self.captured_prompts.append(prompt)
        return (
            "Hi Anika, I built SCIM 2.0 RFC 7644 PATCH flows and your Lifecycle "
            "Management team ships the matching surface. Would 15 min Tue or Wed "
            "work for a quick pointer on team fit?\n\nThanks,"
        )

    def test_use_self_rag_false_skips_retrieval(self) -> None:
        with (
            patch.object(draft, "_get_google_api_key", return_value="fake-key"),
            patch.object(draft, "_run_query_past_drafts") as fetch,
            patch.object(draft, "_generate", side_effect=self._capture_generate),
        ):
            draft_message(
                self.role,
                self.person,
                self.resume_text,
                keywords=("SCIM", "identity"),
                use_self_rag=False,
            )

        fetch.assert_not_called()
        self.assertEqual(len(self.captured_prompts), 1)
        self.assertNotIn("Past example", self.captured_prompts[0])

    def test_use_self_rag_true_threads_exemplars_into_prompt(self) -> None:
        exemplars = [
            _past_draft("EXEMPLAR ONE about SCIM 2.0 RFC 7644.", 4.9, "span-1"),
            _past_draft("EXEMPLAR TWO referencing Lifecycle Management team.", 4.7, "span-2"),
            _past_draft("EXEMPLAR THREE asking for 15 min Tue.", 4.6, "span-3"),
        ]

        with (
            patch.object(draft, "_get_google_api_key", return_value="fake-key"),
            patch.object(draft, "_run_query_past_drafts", return_value=exemplars) as fetch,
            patch.object(draft, "_generate", side_effect=self._capture_generate),
        ):
            draft_message(
                self.role,
                self.person,
                self.resume_text,
                keywords=("SCIM", "identity"),
                use_self_rag=True,
            )

        fetch.assert_called_once_with(["SCIM", "identity"], 3)
        prompt = self.captured_prompts[0]
        self.assertIn("Past example 1 (score 4.9)", prompt)
        self.assertIn("EXEMPLAR ONE", prompt)
        self.assertIn("EXEMPLAR TWO", prompt)
        self.assertIn("EXEMPLAR THREE", prompt)
        self.assertIn("high-scoring past examples", prompt)

    def test_retrieval_empty_falls_through_to_baseline(self) -> None:
        with (
            patch.object(draft, "_get_google_api_key", return_value="fake-key"),
            patch.object(draft, "_run_query_past_drafts", return_value=[]),
            patch.object(draft, "_generate", side_effect=self._capture_generate),
        ):
            draft_message(
                self.role,
                self.person,
                self.resume_text,
                keywords=("SCIM",),
                use_self_rag=True,
            )

        self.assertNotIn("Past example", self.captured_prompts[0])

    def test_retrieval_exception_falls_through_silently(self) -> None:
        with (
            patch.object(draft, "_get_google_api_key", return_value="fake-key"),
            patch.object(draft, "_run_query_past_drafts", side_effect=RuntimeError("mcp down")),
            patch.object(draft, "_generate", side_effect=self._capture_generate),
        ):
            message = draft_message(
                self.role,
                self.person,
                self.resume_text,
                keywords=("SCIM",),
                use_self_rag=True,
            )

        self.assertIn("Hi Anika", message)
        self.assertNotIn("Past example", self.captured_prompts[0])

    def test_low_score_exemplars_filtered_out(self) -> None:
        mid_band = [
            _past_draft("MID example A", 3.2, "span-a"),
            _past_draft("MID example B", 3.4, "span-b"),
        ]

        with (
            patch.object(draft, "_get_google_api_key", return_value="fake-key"),
            patch.object(draft, "_run_query_past_drafts", return_value=mid_band),
            patch.object(draft, "_generate", side_effect=self._capture_generate),
        ):
            draft_message(
                self.role,
                self.person,
                self.resume_text,
                keywords=("SCIM",),
                use_self_rag=True,
            )

        self.assertNotIn("Past example", self.captured_prompts[0])

    def test_exemplar_cache_avoids_repeat_queries(self) -> None:
        exemplars = [_past_draft("ONLY EXAMPLE", 4.8, "span-1")]
        cache: dict = {}

        with (
            patch.object(draft, "_get_google_api_key", return_value="fake-key"),
            patch.object(draft, "_run_query_past_drafts", return_value=exemplars) as fetch,
            patch.object(draft, "_generate", side_effect=self._capture_generate),
        ):
            draft_message(
                self.role, self.person, self.resume_text,
                keywords=("SCIM", "identity"),
                exemplar_cache=cache,
            )
            draft_message(
                self.role, self.person, self.resume_text,
                keywords=("identity", "SCIM"),   # different order, same set
                exemplar_cache=cache,
            )
            draft_message(
                self.role, self.person, self.resume_text,
                keywords=("scim", "IDENTITY"),   # different case, same set
                exemplar_cache=cache,
            )

        self.assertEqual(fetch.call_count, 1)
        for prompt in self.captured_prompts:
            self.assertIn("ONLY EXAMPLE", prompt)


if __name__ == "__main__":
    unittest.main()
