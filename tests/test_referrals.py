import os
import unittest
import warnings
from urllib.parse import urlparse
from unittest.mock import patch

from google.adk.tools import FunctionTool
from job_hunt_agent.schemas import Person, Role
from job_hunt_agent.tools import referrals
from job_hunt_agent.tools.referrals import find_referrals


def _assert_profile_url(test_case: unittest.TestCase, url: str) -> None:
    parsed = urlparse(url)
    test_case.assertEqual(parsed.scheme, "https")
    test_case.assertTrue(
        parsed.netloc.endswith("linkedin.com") or parsed.netloc.endswith("github.com")
    )


class ReferralsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.role = Role(
            company="Twilio",
            title="Engineer, Identity & Access",
            url="https://www.linkedin.com/jobs/view/engineer-identity-access-at-twilio-4385523447",
            location="Remote-India",
            summary="Build identity and access systems using SCIM, SAML, and OIDC.",
            match_reason="Snippet matches SCIM and identity access.",
        )

    def test_find_referrals_returns_empty_when_env_missing(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(referrals, "_load_dotenv_if_available", return_value=None),
        ):
            self.assertEqual(find_referrals(self.role), [])

    def test_find_referrals_parses_and_ranks_linkedin_profiles(self) -> None:
        payload = {
            "organic_results": [
                {
                    "title": "Priya Rao - Staff Software Engineer - Twilio | LinkedIn",
                    "link": "https://www.linkedin.com/in/priya-rao-identity/",
                    "snippet": "Staff Software Engineer at Twilio working on identity platform and access systems.",
                },
                {
                    "title": "Aman Shah - Engineering Manager - Twilio | LinkedIn",
                    "link": "https://in.linkedin.com/in/aman-shah/",
                    "snippet": "Engineering Manager at Twilio. Teams include Identity and Access.",
                },
                {
                    "title": "Mira Iyer - Senior Security Engineer - Twilio | LinkedIn",
                    "link": "https://www.linkedin.com/in/mira-iyer-security/",
                    "snippet": "Senior Security Engineer at Twilio with OAuth, SAML, and SCIM experience.",
                },
                {
                    "title": "Twilio Careers | LinkedIn",
                    "link": "https://www.linkedin.com/company/twilio/jobs/",
                    "snippet": "Jobs at Twilio.",
                },
            ]
        }

        with (
            patch.dict(os.environ, {"SERPAPI_API_KEY": "fake-key"}, clear=True),
            patch.object(referrals, "_fetch_serpapi_search", return_value=payload),
        ):
            people = find_referrals(self.role)

        self.assertEqual(len(people), 3)
        self.assertTrue(all(isinstance(person, Person) for person in people))
        self.assertEqual([person.source for person in people], ["linkedin", "linkedin", "linkedin"])
        self.assertTrue(all(person.company == "Twilio" for person in people))
        self.assertTrue(all("Twilio" in person.why_relevant or "role" in person.why_relevant for person in people))
        for person in people:
            _assert_profile_url(self, person.profile_url)

    def test_find_referrals_accepts_adk_style_dict_input(self) -> None:
        payload = {
            "organic_results": [
                {
                    "title": "Priya Rao - Staff Software Engineer - Twilio | LinkedIn",
                    "link": "https://www.linkedin.com/in/priya-rao-identity/",
                    "snippet": "Staff Software Engineer at Twilio working on identity platform.",
                },
            ]
        }

        with (
            patch.dict(os.environ, {"SERPAPI_API_KEY": "fake-key"}, clear=True),
            patch.object(referrals, "_fetch_serpapi_search", return_value=payload),
        ):
            people = find_referrals(self.role.model_dump())

        self.assertEqual(len(people), 1)
        self.assertEqual(people[0].name, "Priya Rao")

    def test_find_referrals_can_use_github_profiles_when_company_is_visible(self) -> None:
        payload = {
            "organic_results": [
                {
                    "title": "Neha Gupta - GitHub",
                    "link": "https://github.com/neha-gupta",
                    "snippet": "Senior Software Engineer at Twilio. Works on identity infrastructure.",
                },
            ]
        }

        with (
            patch.dict(os.environ, {"SERPAPI_API_KEY": "fake-key"}, clear=True),
            patch.object(referrals, "_fetch_serpapi_search", return_value=payload),
        ):
            people = find_referrals(self.role)

        self.assertEqual(len(people), 1)
        self.assertEqual(people[0].source, "github")
        self.assertEqual(people[0].title, "Senior Software Engineer")
        _assert_profile_url(self, people[0].profile_url)

    def test_find_referrals_uses_low_confidence_padding_without_fabricating_urls(self) -> None:
        payload = {
            "organic_results": [
                {
                    "title": "Priya Rao - Staff Software Engineer - Twilio | LinkedIn",
                    "link": "https://www.linkedin.com/in/priya-rao-identity/",
                    "snippet": "Staff Software Engineer at Twilio working on identity platform.",
                },
                {
                    "title": "Aman Shah | LinkedIn",
                    "link": "https://www.linkedin.com/in/aman-shah/",
                    "snippet": "Search result for Twilio identity engineering.",
                },
            ]
        }

        with (
            patch.dict(os.environ, {"SERPAPI_API_KEY": "fake-key"}, clear=True),
            patch.object(referrals, "_fetch_serpapi_search", return_value=payload),
        ):
            people = find_referrals(self.role)

        self.assertEqual(len(people), 2)
        self.assertEqual(people[1].source, "other")
        self.assertEqual(people[1].profile_url, "https://www.linkedin.com/in/aman-shah")

    def test_find_referrals_marks_other_current_company_as_low_confidence(self) -> None:
        payload = {
            "organic_results": [
                {
                    "title": "Rishil Patel - AI Engineer @ E.ON Next | LinkedIn",
                    "link": "https://www.linkedin.com/in/rishilppatel/",
                    "snippet": "Profile result mentioning Twilio identity access systems.",
                },
            ]
        }

        with (
            patch.dict(os.environ, {"SERPAPI_API_KEY": "fake-key"}, clear=True),
            patch.object(referrals, "_fetch_serpapi_search", return_value=payload),
        ):
            people = find_referrals(self.role)

        self.assertEqual(len(people), 1)
        self.assertEqual(people[0].source, "other")
        self.assertEqual(people[0].title, "AI Engineer")

    def test_find_referrals_does_not_treat_domain_keyword_as_title(self) -> None:
        payload = {
            "organic_results": [
                {
                    "title": "Scott Bonnell | LinkedIn",
                    "link": "https://www.linkedin.com/in/scottbonnellnyc/",
                    "snippet": "Scott Bonnell · CRO Prove Identity at Twilio.",
                },
            ]
        }

        with (
            patch.dict(os.environ, {"SERPAPI_API_KEY": "fake-key"}, clear=True),
            patch.object(referrals, "_fetch_serpapi_search", return_value=payload),
        ):
            people = find_referrals(self.role)

        self.assertEqual(len(people), 1)
        self.assertEqual(people[0].source, "other")
        self.assertEqual(people[0].title, "Profile result mentioning Twilio")

    def test_find_referrals_compacts_snippet_sentence_titles(self) -> None:
        payload = {
            "organic_results": [
                {
                    "title": "Liat Dremer | LinkedIn",
                    "link": "https://www.linkedin.com/in/liatdremer/",
                    "snippet": (
                        "Liat Dremer · A Back-End Engineer, highly experienced "
                        "in distributed systems at Twilio."
                    ),
                },
            ]
        }

        with (
            patch.dict(os.environ, {"SERPAPI_API_KEY": "fake-key"}, clear=True),
            patch.object(referrals, "_fetch_serpapi_search", return_value=payload),
        ):
            people = find_referrals(self.role)

        self.assertEqual(len(people), 1)
        self.assertEqual(people[0].title, "Back-End Engineer")

    def test_find_referrals_removes_name_prefix_from_snippet_title(self) -> None:
        payload = {
            "organic_results": [
                {
                    "title": "Jason Naylor | LinkedIn",
                    "link": "https://www.linkedin.com/in/jrnaylor/",
                    "snippet": (
                        "Jason Naylor · Senior Manager - IAM Engineering at "
                        "Duck Creek Technologies."
                    ),
                },
            ]
        }
        role = Role(
            company="Duck Creek Technologies",
            title="Senior Associate IAM Engineer",
            url="https://www.linkedin.com/jobs/view/senior-associate-iam-engineer-at-duck-creek-1",
            location="Remote-India",
            summary="Understanding of SCIM protocol, SAML, and OIDC.",
            match_reason="Snippet matches SCIM in context.",
        )

        with (
            patch.dict(os.environ, {"SERPAPI_API_KEY": "fake-key"}, clear=True),
            patch.object(referrals, "_fetch_serpapi_search", return_value=payload),
        ):
            people = find_referrals(role)

        self.assertEqual(len(people), 1)
        self.assertEqual(people[0].title, "Senior Manager - IAM Engineering")

    def test_find_referrals_returns_empty_for_no_usable_results(self) -> None:
        payload = {
            "organic_results": [
                {
                    "title": "Twilio Careers | LinkedIn",
                    "link": "https://www.linkedin.com/company/twilio/jobs/",
                    "snippet": "Jobs at Twilio.",
                },
                {
                    "title": "GitHub Topics",
                    "link": "https://github.com/topics/identity",
                    "snippet": "Topic page, not a person.",
                },
            ]
        }

        with (
            patch.dict(os.environ, {"SERPAPI_API_KEY": "fake-key"}, clear=True),
            patch.object(referrals, "_fetch_serpapi_search", return_value=payload),
        ):
            self.assertEqual(find_referrals(self.role), [])

    def test_build_queries_targets_linkedin_and_github_profiles(self) -> None:
        queries = referrals._build_queries(self.role)

        self.assertIn('site:linkedin.com/in "Twilio" "identity access"', queries)
        self.assertIn('site:linkedin.com/in "Twilio" "engineering manager"', queries)
        self.assertTrue(any(query.startswith('site:github.com "Twilio"') for query in queries))

    def test_adk_can_build_function_declaration_for_find_referrals(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            declaration = FunctionTool(find_referrals)._get_declaration()

        self.assertEqual(declaration.name, "find_referrals")
        self.assertIn("role", declaration.parameters_json_schema["properties"])

    def test_live_serpapi_referral_shape(self) -> None:
        if os.getenv("RUN_LIVE_SERPAPI") != "1":
            self.skipTest("set RUN_LIVE_SERPAPI=1 to run the live SerpAPI test")
        referrals._load_dotenv_if_available()
        api_key = referrals._get_serpapi_api_key()
        if not api_key:
            self.skipTest("SERPAPI_API_KEY is not set")

        people = find_referrals(self.role)

        self.assertLessEqual(len(people), 3)
        for person in people:
            self.assertIsInstance(person, Person)
            self.assertTrue(person.name)
            self.assertTrue(person.title)
            self.assertEqual(person.company, self.role.company)
            _assert_profile_url(self, person.profile_url)


if __name__ == "__main__":
    unittest.main()
