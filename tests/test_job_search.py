import os
import unittest
from unittest.mock import MagicMock, patch

from job_hunt_agent.schemas import JobCriteria, Role
from job_hunt_agent.tools import job_search
from job_hunt_agent.tools.job_search import search_jobs


class JobSearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.criteria = JobCriteria(
            role_keywords=["SCIM", "identity"],
            seniority="senior",
            location=["Remote-India"],
        )

    def test_search_jobs_returns_empty_when_env_missing(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(job_search, "_load_dotenv_if_available", return_value=None),
        ):
            self.assertEqual(search_jobs(self.criteria), [])

    def test_search_jobs_parses_serpapi_organic_results(self) -> None:
        payload = {
            "organic_results": [
                {
                    "title": "Okta hiring Senior Software Engineer, Identity in India | LinkedIn",
                    "link": "https://www.linkedin.com/jobs/view/senior-software-engineer-identity-at-okta-123",
                    "snippet": "Work on SCIM provisioning and identity lifecycle automation for enterprise customers.",
                },
                {
                    "title": "Okta hiring Senior Software Engineer, Identity in India | LinkedIn",
                    "link": "https://www.linkedin.com/jobs/view/senior-software-engineer-identity-at-okta-duplicate-999",
                    "snippet": "Duplicate result.",
                },
                {
                    "title": "LinkedIn job search page",
                    "link": "https://www.linkedin.com/jobs/search/?keywords=scim",
                    "snippet": "Search page, not a posting.",
                },
            ]
        }

        with (
            patch.dict(
                os.environ,
                {
                    "SERPAPI_API_KEY": "fake-key",
                },
                clear=True,
            ),
            patch.object(job_search, "_fetch_serpapi_search", return_value=payload),
        ):
            roles = search_jobs(self.criteria)

        self.assertEqual(len(roles), 1)
        role = roles[0]
        self.assertIsInstance(role, Role)
        self.assertEqual(role.company, "Okta")
        self.assertEqual(role.title, "Senior Software Engineer, Identity")
        self.assertIn("linkedin.com/jobs/view", role.url)
        self.assertIn("SCIM", role.match_reason)

    def test_search_jobs_accepts_adk_style_dict_input(self) -> None:
        payload = {
            "organic_results": [
                {
                    "title": "Saviynt hiring Backend Engineer, Identity Governance in Hyderabad | LinkedIn",
                    "link": "https://www.linkedin.com/jobs/view/backend-engineer-at-saviynt-456",
                    "snippet": "Build IAM workflows and identity governance APIs.",
                },
            ]
        }

        with (
            patch.dict(
                os.environ,
                {
                    "SERPAPI_API_KEY": "fake-key",
                },
                clear=True,
            ),
            patch.object(job_search, "_fetch_serpapi_search", return_value=payload),
        ):
            roles = search_jobs(self.criteria.model_dump())

        self.assertEqual(len(roles), 1)
        self.assertEqual(roles[0].company, "Saviynt")

    def test_search_jobs_uses_linkedin_url_company_when_title_has_location(self) -> None:
        payload = {
            "organic_results": [
                {
                    "title": "Senior Associate IAM Engineer - Remote | LinkedIn",
                    "link": (
                        "https://in.linkedin.com/jobs/view/"
                        "senior-associate-iam-engineer-remote-at-duck-creek-technologies-4395060510"
                    ),
                    "snippet": "Understanding of SCIM protocol, SAML, and OIDC/OAUTH.",
                },
            ]
        }

        with (
            patch.dict(os.environ, {"SERPAPI_API_KEY": "fake-key"}, clear=True),
            patch.object(job_search, "_fetch_serpapi_search", return_value=payload),
        ):
            roles = search_jobs(self.criteria)

        self.assertEqual(len(roles), 1)
        self.assertEqual(roles[0].company, "Duck Creek Technologies")
        self.assertEqual(roles[0].title, "Senior Associate IAM Engineer")

    def test_fetch_serpapi_error_payload_returns_none(self) -> None:
        response = MagicMock()
        response.read.return_value = b'{"error": "Invalid API key"}'

        with patch.object(job_search, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = response

            payload = job_search._fetch_serpapi_search(
                query='site:linkedin.com/jobs "SCIM" "Remote India"',
                api_key="fake-key",
                num_results=1,
            )

        self.assertIsNone(payload)

    def test_live_serpapi_query_shape(self) -> None:
        if os.getenv("RUN_LIVE_SERPAPI") != "1":
            self.skipTest("set RUN_LIVE_SERPAPI=1 to run the live SerpAPI test")
        job_search._load_dotenv_if_available()
        api_key = job_search._get_serpapi_api_key()
        if not api_key:
            self.skipTest("SERPAPI_API_KEY is not set")

        payload = job_search._fetch_serpapi_search(
            query='site:linkedin.com/jobs "SCIM" "Remote India"',
            api_key=api_key,
            num_results=1,
        )
        self.assertIsNotNone(
            payload,
            "SerpAPI request failed; check whether SERPAPI_API_KEY is valid and has quota.",
        )

        roles = search_jobs(self.criteria)

        self.assertLessEqual(len(roles), 5)
        for role in roles:
            self.assertIsInstance(role, Role)
            self.assertTrue(role.company)
            self.assertTrue(role.title)
            self.assertTrue(role.url.startswith("http"))


if __name__ == "__main__":
    unittest.main()
