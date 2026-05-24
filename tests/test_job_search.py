import os
import unittest
from urllib.parse import urlparse
from unittest.mock import MagicMock, patch

from job_hunt_agent.schemas import JobCriteria, Role
from job_hunt_agent.tools import job_search
from job_hunt_agent.tools.job_search import search_jobs


def _assert_linkedin_job_posting_url(test_case: unittest.TestCase, url: str) -> None:
    parsed = urlparse(url)
    test_case.assertEqual(parsed.scheme, "https")
    test_case.assertTrue(parsed.netloc.endswith("linkedin.com"))
    test_case.assertTrue(parsed.path.startswith("/jobs/view/"))


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
                {
                    "title": "Sde 1 jobs",
                    "link": "https://in.linkedin.com/jobs/sde-1-jobs",
                    "snippet": "Directory page, not a posting.",
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
        _assert_linkedin_job_posting_url(self, role.url)
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

    def test_build_queries_targets_linkedin_job_postings(self) -> None:
        criteria = JobCriteria(
            role_keywords=["SDE 1", "Backend Engineer"],
            seniority="junior",
            location=["Remote-India", "Bengaluru"],
        )

        queries = job_search._build_queries(criteria)

        self.assertEqual(
            queries,
            [
                'site:linkedin.com/jobs/view "SDE 1" "Remote India"',
                'site:linkedin.com/jobs/view "SDE 1" "Bengaluru"',
                'site:linkedin.com/jobs/view "Backend Engineer" "Remote India"',
                'site:linkedin.com/jobs/view "Backend Engineer" "Bengaluru"',
            ],
        )

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

    def test_search_jobs_filters_senior_roles_for_junior_criteria(self) -> None:
        criteria = JobCriteria(
            role_keywords=["Backend Engineer"],
            seniority="junior",
            location=["Remote-India"],
        )
        payload = {
            "organic_results": [
                {
                    "title": "Remote hiring Senior Backend Engineer in South Asia | LinkedIn",
                    "link": "https://www.linkedin.com/jobs/view/senior-backend-engineer-at-remote-3739159630",
                    "snippet": "Senior backend role.",
                },
                {
                    "title": "Tailored AI hiring Backend Engineer in Bengaluru, Karnataka, India | LinkedIn",
                    "link": "https://in.linkedin.com/jobs/view/backend-engineer-at-tailored-ai-4367155051",
                    "snippet": "Build backend APIs as a junior engineer.",
                },
            ]
        }

        with (
            patch.dict(os.environ, {"SERPAPI_API_KEY": "fake-key"}, clear=True),
            patch.object(job_search, "_fetch_serpapi_search", return_value=payload),
        ):
            roles = search_jobs(criteria)

        self.assertEqual(len(roles), 1)
        self.assertEqual(roles[0].company, "Tailored AI")
        self.assertEqual(roles[0].title, "Backend Engineer")
        self.assertIn("Title matches Backend Engineer", roles[0].match_reason)

    def test_search_jobs_requires_title_match_for_role_title_keywords(self) -> None:
        criteria = JobCriteria(
            role_keywords=["SDE 1", "Backend Engineer"],
            seniority="junior",
            location=["Remote-India"],
        )
        payload = {
            "organic_results": [
                {
                    "title": "Custom Software Engineer | LinkedIn",
                    "link": (
                        "https://in.linkedin.com/jobs/view/"
                        "custom-software-engineer-at-accenture-services-pvt-ltd-4410467462"
                    ),
                    "snippet": "Similar jobs include SDE 1 - Backend.",
                },
                {
                    "title": "Zexovo hiring SDE-1 (Backend / AWS Systems) | LinkedIn",
                    "link": (
                        "https://in.linkedin.com/jobs/view/"
                        "sde-1-backend-aws-systems-at-zexovo-4410232641"
                    ),
                    "snippet": "Hiring: SDE-1 — Backend / AWS Systems. Location: Remote (India).",
                },
            ]
        }

        with (
            patch.dict(os.environ, {"SERPAPI_API_KEY": "fake-key"}, clear=True),
            patch.object(job_search, "_fetch_serpapi_search", return_value=payload),
        ):
            roles = search_jobs(criteria)

        self.assertEqual(len(roles), 1)
        self.assertEqual(roles[0].company, "Zexovo")
        self.assertEqual(roles[0].title, "SDE-1 (Backend / AWS Systems)")

    def test_search_jobs_returns_empty_when_serpapi_request_fails(self) -> None:
        with (
            patch.dict(os.environ, {"SERPAPI_API_KEY": "fake-key"}, clear=True),
            patch.object(job_search, "_fetch_serpapi_search", return_value=None),
        ):
            self.assertEqual(search_jobs(self.criteria), [])

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

    def test_fetch_serpapi_malformed_json_returns_none(self) -> None:
        response = MagicMock()
        response.read.return_value = b"{not-json"

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
            _assert_linkedin_job_posting_url(self, role.url)


if __name__ == "__main__":
    unittest.main()
