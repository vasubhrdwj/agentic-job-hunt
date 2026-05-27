import unittest
import warnings

from google.adk.tools import FunctionTool
from job_hunt_agent.agent import build_agent
from job_hunt_agent.schemas import JobCriteria, Person, Role
from job_hunt_agent.tools.mocks import (
    draft_message_mock,
    find_referrals_mock,
    search_jobs_mock,
)


class MockToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.criteria = JobCriteria(
            role_keywords=["SCIM", "identity", "IAM"],
            seniority="senior",
            location=["Hyderabad", "Remote-India"],
            comp_min_lpa=25,
            comp_max_lpa=40,
        )

    def test_search_jobs_mock_returns_three_valid_roles(self) -> None:
        roles = search_jobs_mock(self.criteria)

        self.assertEqual(len(roles), 3)
        validated_roles = [Role.model_validate(role) for role in roles]
        self.assertEqual(validated_roles[0].company, "Okta")
        self.assertTrue(all(role.url for role in validated_roles))
        self.assertTrue(all("identity" in role.match_reason.lower() for role in validated_roles))

    def test_search_jobs_mock_accepts_adk_style_dict_input(self) -> None:
        roles = search_jobs_mock(self.criteria.model_dump())

        self.assertEqual(len(roles), 3)
        self.assertTrue(all(Role.model_validate(role).company for role in roles))

    def test_find_referrals_mock_returns_three_valid_people_per_role(self) -> None:
        role = Role.model_validate(search_jobs_mock(self.criteria)[0])
        people = find_referrals_mock(role)

        self.assertEqual(len(people), 3)
        validated_people = [Person.model_validate(person) for person in people]
        self.assertTrue(all(person.company == role.company for person in validated_people))
        self.assertTrue(all(person.profile_url for person in validated_people))

    def test_find_referrals_mock_accepts_adk_style_dict_input(self) -> None:
        role = search_jobs_mock(self.criteria)[1]
        people = find_referrals_mock(role)

        self.assertEqual(len(people), 3)
        self.assertTrue(all(Person.model_validate(person).name for person in people))

    def test_find_referrals_mock_returns_fallback_people_for_real_search_companies(self) -> None:
        role = Role(
            company="Twilio",
            title="Engineer, Identity & Access",
            url="https://www.linkedin.com/jobs/view/engineer-identity-access-at-twilio-4385523447",
            location="Remote-India",
            summary="Build identity and access systems.",
            match_reason="Snippet matches SCIM.",
        )

        people = find_referrals_mock(role)

        self.assertEqual(len(people), 3)
        validated_people = [Person.model_validate(person) for person in people]
        self.assertTrue(all(person.company == "Twilio" for person in validated_people))
        self.assertTrue(
            all(person.profile_url.startswith("https://example.com/mock/") for person in validated_people)
        )

    def test_draft_message_mock_is_specific_and_has_no_placeholders(self) -> None:
        role = Role.model_validate(search_jobs_mock(self.criteria)[0])
        person = Person.model_validate(find_referrals_mock(role)[0])

        message = draft_message_mock(
            role,
            person,
            resume_text="Built SCIM provisioning and identity automation services.",
        )

        self.assertIn(person.name.split()[0], message)
        self.assertIn(role.company, message)
        self.assertIn("SCIM", message)
        self.assertIn("My background lines up through my recent SCIM and identity automation work.", message)
        self.assertNotIn("around matches", message.lower())
        self.assertNotIn("especially the parts of the role around", message.lower())
        self.assertNotIn("[", message)
        self.assertNotIn("]", message)

    def test_build_agent_can_attach_mock_tools(self) -> None:
        agent = build_agent(use_mocks=True)

        self.assertEqual(len(agent.tools), 3)
        self.assertEqual(
            [tool.__name__ for tool in agent.tools],
            ["search_jobs_mock", "find_referrals_mock", "draft_message_mock"],
        )

    def test_build_agent_uses_real_search_with_mock_followups_by_default(self) -> None:
        agent = build_agent()

        self.assertEqual(len(agent.tools), 3)
        self.assertEqual(
            [tool.__name__ for tool in agent.tools],
            ["search_jobs", "find_referrals", "draft_message"],
        )

    def test_adk_can_build_function_declarations_for_mocks(self) -> None:
        mock_tools = [search_jobs_mock, find_referrals_mock, draft_message_mock]

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            declarations = [FunctionTool(tool)._get_declaration() for tool in mock_tools]

        self.assertEqual(
            [declaration.name for declaration in declarations],
            ["search_jobs_mock", "find_referrals_mock", "draft_message_mock"],
        )
        self.assertIn("criteria", declarations[0].parameters_json_schema["properties"])


if __name__ == "__main__":
    unittest.main()
