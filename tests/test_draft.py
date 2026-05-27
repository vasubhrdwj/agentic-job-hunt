import os
import unittest
from unittest.mock import patch

from job_hunt_agent.schemas import Person, Role
from job_hunt_agent.tools import draft
from job_hunt_agent.tools.draft import draft_message


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


if __name__ == "__main__":
    unittest.main()
