import copy
import json
import os
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import MagicMock, call, patch

from job_hunt_agent.schemas import EmploymentType, JobCriteria, Role
from job_hunt_agent.tools import job_search
from job_hunt_agent.tools.job_search import search_jobs


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "google_jobs_sample.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class JobSearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = _load_fixture()

    def setUp(self) -> None:
        self.criteria = JobCriteria(
            role_keywords=["Backend Engineer"],
            seniority="junior",
            location=["Remote-India"],
        )
        self.urlopen_guard = patch.object(
            job_search,
            "urlopen",
            side_effect=AssertionError("unit tests must not make network calls"),
        )
        self.urlopen_mock = self.urlopen_guard.start()
        self.addCleanup(self.urlopen_guard.stop)

    def tearDown(self) -> None:
        self.urlopen_mock.assert_not_called()

    def _payload_for(self, *companies: str) -> dict:
        wanted = set(companies)
        payload = copy.deepcopy(self.fixture)
        payload["jobs_results"] = [
            item for item in payload["jobs_results"] if item["company_name"] in wanted
        ]
        return payload

    def _search_with_payload(
        self,
        payload: dict | None,
        *,
        criteria: JobCriteria | dict | None = None,
    ) -> tuple[list[Role], object]:
        with (
            patch.dict(os.environ, {"SERPAPI_API_KEY": "fake-key"}, clear=True),
            patch.object(job_search, "_load_dotenv_if_available", return_value=None),
            patch.object(job_search, "_fetch_google_jobs", return_value=payload) as fetch,
        ):
            roles = search_jobs(criteria or self.criteria)
        return roles, fetch

    def test_search_jobs_returns_empty_when_env_missing(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(job_search, "_load_dotenv_if_available", return_value=None),
            patch.object(job_search, "_fetch_google_jobs") as fetch,
        ):
            self.assertEqual(search_jobs(self.criteria), [])

        fetch.assert_not_called()

    def test_search_jobs_maps_google_jobs_result(self) -> None:
        criteria = JobCriteria(
            role_keywords=["Backend Engineer"],
            seniority="mid",
            location=["Bengaluru"],
        )
        roles, _ = self._search_with_payload(
            self._payload_for("YMinds.AI"),
            criteria=criteria,
        )

        self.assertEqual(len(roles), 1)
        role = roles[0]
        self.assertIsInstance(role, Role)
        self.assertEqual(role.company, "YMinds.AI")
        self.assertEqual(
            role.title,
            "Backend Engineer 4+Years | Microservices| Python| NoSQL | Django",
        )
        self.assertEqual(role.location, "Bengaluru, Karnataka")
        self.assertEqual(
            role.url,
            (
                "https://in.linkedin.com/jobs/view/"
                "backend-engineer-4%2Byears-microservices-python-nosql-django-"
                "at-yminds-ai-4430922897"
                "?utm_campaign=google_jobs_apply&utm_source=google_jobs_apply"
                "&utm_medium=organic"
            ),
        )
        self.assertIn("scalable, reliable, and high-performance backend systems", role.summary)
        self.assertIn('Job description mentions "Backend Engineer"', role.match_reason)
        self.assertIn("Posted 2 days ago.", role.match_reason)
        self.assertIs(role.employment_type, EmploymentType.full_time)

    def test_search_jobs_tags_contract_and_intern_schedule_types(self) -> None:
        criteria = JobCriteria(
            role_keywords=["Backend Engineer"],
            seniority="mid",
            location=["Bengaluru"],
        )
        cases = (
            ("Contractor", EmploymentType.contract),
            ("Internship", EmploymentType.intern),
        )

        for schedule_type, expected in cases:
            with self.subTest(schedule_type=schedule_type):
                payload = self._payload_for("YMinds.AI")
                payload["jobs_results"][0]["detected_extensions"][
                    "schedule_type"
                ] = schedule_type
                roles, _ = self._search_with_payload(payload, criteria=criteria)

                self.assertEqual(len(roles), 1)
                self.assertIs(roles[0].employment_type, expected)

    def test_search_jobs_tags_hourly_contract_even_when_schedule_says_full_time(self) -> None:
        criteria = JobCriteria(
            role_keywords=["Backend Engineer"],
            seniority="mid",
            location=["Remote-India"],
        )
        payload = self._payload_for("Mercor")
        payload["jobs_results"][0]["description"] = (
            "Compensation: $85/hour. Pay is tied to each accepted task. "
            "The engagement has no fixed task limit."
        )

        roles, _ = self._search_with_payload(payload, criteria=criteria)

        self.assertEqual(len(roles), 1)
        self.assertIs(roles[0].employment_type, EmploymentType.contract)

    def test_search_jobs_accepts_adk_style_dict_input(self) -> None:
        roles, _ = self._search_with_payload(
            self._payload_for("MongoDB"),
            criteria=self.criteria.model_dump(mode="json"),
        )

        self.assertEqual(len(roles), 1)
        self.assertEqual(roles[0].company, "MongoDB")
        self.assertEqual(roles[0].location, "Remote")

    def test_build_google_jobs_requests_and_search_call_shape(self) -> None:
        criteria = JobCriteria(
            role_keywords=["Backend Engineer", "SDE 1"],
            seniority="junior",
            location=["Remote-India", "Bengaluru"],
        )

        self.assertEqual(
            job_search._build_google_jobs_requests(criteria),
            [
                ("Backend Engineer remote", "India"),
                ("Backend Engineer", "Bengaluru, India"),
                ("SDE 1 remote", "India"),
                ("SDE 1", "Bengaluru, India"),
            ],
        )

        roles, fetch = self._search_with_payload({"jobs_results": []}, criteria=criteria)

        self.assertEqual(roles, [])
        self.assertEqual(
            fetch.call_args_list,
            [
                call(
                    query="Backend Engineer remote",
                    location="India",
                    api_key="fake-key",
                ),
                call(
                    query="Backend Engineer",
                    location="Bengaluru, India",
                    api_key="fake-key",
                ),
                call(query="SDE 1 remote", location="India", api_key="fake-key"),
                call(
                    query="SDE 1",
                    location="Bengaluru, India",
                    api_key="fake-key",
                ),
            ],
        )

    def test_search_jobs_filters_senior_roles_for_junior_criteria(self) -> None:
        roles, _ = self._search_with_payload(
            self._payload_for("eBrevia", "MongoDB"),
        )

        self.assertEqual([role.company for role in roles], ["MongoDB"])
        self.assertEqual(roles[0].title, "REMOTE (INDIA): Backend Engineer - SaaS platform")

    def test_search_jobs_dedupes_by_job_id(self) -> None:
        original = copy.deepcopy(self._payload_for("MongoDB")["jobs_results"][0])
        same_job_different_metadata = copy.deepcopy(original)
        same_job_different_metadata["title"] = "Python Backend Engineer"
        same_job_different_metadata["company_name"] = "MongoDB India"
        same_job_different_metadata["apply_options"] = [
            original["apply_options"][1],
        ]
        self.assertNotEqual(
            original["apply_options"][0]["link"],
            same_job_different_metadata["apply_options"][0]["link"],
        )

        with (
            patch.dict(os.environ, {"SERPAPI_API_KEY": "fake-key"}, clear=True),
            patch.object(job_search, "_load_dotenv_if_available", return_value=None),
            patch.object(
                job_search,
                "_fetch_google_jobs",
                return_value={
                    "jobs_results": [original, same_job_different_metadata],
                },
            ) as fetch,
        ):
            roles = search_jobs(self.criteria)

        self.assertEqual(len(roles), 1)
        self.assertEqual(roles[0].company, "MongoDB")
        fetch.assert_called_once()

    def test_fetch_google_jobs_builds_expected_serpapi_url(self) -> None:
        response = MagicMock()
        response.read.return_value = b'{"jobs_results": []}'

        with patch.object(job_search, "urlopen") as fake_urlopen:
            fake_urlopen.return_value.__enter__.return_value = response

            payload = job_search._fetch_google_jobs(
                query="Backend Engineer remote",
                location="India",
                api_key="fake-key",
            )

        self.assertEqual(payload, {"jobs_results": []})
        request = fake_urlopen.call_args.args[0]
        params = parse_qs(urlparse(request.full_url).query)
        self.assertEqual(
            params,
            {
                "engine": ["google_jobs"],
                "q": ["Backend Engineer remote"],
                "api_key": ["fake-key"],
                "hl": ["en"],
                "gl": ["in"],
                "location": ["India"],
            },
        )
        self.assertEqual(fake_urlopen.call_args.kwargs, {"timeout": 20})

    def test_fetch_google_jobs_error_payload_returns_none(self) -> None:
        response = MagicMock()
        response.read.return_value = b'{"error": "Google hasn\\u0027t returned any results."}'

        with patch.object(job_search, "urlopen") as fake_urlopen:
            fake_urlopen.return_value.__enter__.return_value = response

            payload = job_search._fetch_google_jobs(
                query="Backend Engineer remote",
                location="India",
                api_key="fake-key",
            )

        self.assertIsNone(payload)
        fake_urlopen.assert_called_once()

    def test_fetch_google_jobs_rejects_non_object_json_and_invalid_utf8(self) -> None:
        for body in (b"[]", b'"text"', b"null", b"\xff"):
            with self.subTest(body=body):
                response = MagicMock()
                response.read.return_value = body
                with patch.object(job_search, "urlopen") as fake_urlopen:
                    fake_urlopen.return_value.__enter__.return_value = response
                    payload = job_search._fetch_google_jobs(
                        query="Backend Engineer remote",
                        location="India",
                        api_key="fake-key",
                    )
                self.assertIsNone(payload)

    def test_search_jobs_returns_empty_for_no_results_or_fetch_failure(self) -> None:
        empty_roles, _ = self._search_with_payload({"jobs_results": []})
        failed_roles, _ = self._search_with_payload(None)

        self.assertEqual(empty_roles, [])
        self.assertEqual(failed_roles, [])

    def test_search_jobs_skips_malformed_and_unusable_results(self) -> None:
        payload = {
            "jobs_results": [
                None,
                "not-a-dict",
                {"title": "Backend Engineer", "company_name": ""},
                {
                    "title": "Backend Engineer",
                    "company_name": "Missing Apply Link",
                    "location": "India",
                },
                {
                    "title": "Product Manager",
                    "company_name": "Not Engineering",
                    "apply_options": [{"link": "https://example.invalid/job"}],
                },
            ]
        }

        roles, _ = self._search_with_payload(payload)

        self.assertEqual(roles, [])

    def test_search_jobs_preserves_remote_and_physical_locations(self) -> None:
        criteria = JobCriteria(
            role_keywords=["Backend Engineer"],
            seniority="mid",
            location=["Remote-India", "Bengaluru"],
        )
        roles, _ = self._search_with_payload(
            self._payload_for("MongoDB", "YMinds.AI"),
            criteria=criteria,
        )

        by_company = {role.company: role for role in roles}
        self.assertEqual(by_company["MongoDB"].location, "Remote")
        self.assertEqual(by_company["YMinds.AI"].location, "Bengaluru, Karnataka")


if __name__ == "__main__":
    unittest.main()
