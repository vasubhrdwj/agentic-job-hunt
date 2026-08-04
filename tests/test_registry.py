from pathlib import Path

import pytest

from job_hunt_agent.schemas import Company, CompanySource
from job_hunt_agent.sources.registry import RegistryError, load_company_pack
from scripts import verify_registry


CURATED_PACK_COUNTS = {
    "backend_india": 35,
    "ai_ml": 35,
    "global_remote": 35,
    "fintech": 43,
}


def _write_pack(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "pack.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_backend_india_pack_loads_curated_active_companies():
    registry = load_company_pack("backend_india")

    assert registry.name == "backend_india"
    assert len(registry) == 35
    assert len(registry.active_companies) == 35
    assert len({company.slug for company in registry}) == 35
    assert all(company.source is not CompanySource.google_jobs for company in registry)
    assert registry.get("rubrik").source_token == "rubrik"
    assert registry.get("amazon").source_token == "amazon"
    assert registry.get("stable-money").source_token == "stable-money1"
    assert registry.get("redwood-software").source_token == "redwoodsoftware"
    assert registry.get("datadog").source_token == "datadog"
    assert "job-boards.eu.greenhouse.io" in registry.get("groww").careers_domains


def test_curated_pack_catalog_has_over_one_hundred_consistent_companies():
    identities: dict[str, tuple[CompanySource, str | None]] = {}
    entry_count = 0

    for pack, expected_count in CURATED_PACK_COUNTS.items():
        registry = load_company_pack(pack)

        assert registry.name == pack
        assert len(registry) == expected_count
        assert len(registry.active_companies) == expected_count
        assert all(
            company.source
            not in {CompanySource.google_jobs, CompanySource.scrape}
            for company in registry
        )
        entry_count += len(registry)
        for company in registry:
            identity = (company.source, company.source_token)
            previous = identities.setdefault(company.slug, identity)
            assert previous == identity

    assert entry_count == 148
    assert len(identities) == 114


def test_registry_get_and_select():
    registry = load_company_pack("backend_india")

    assert registry.get("razorpay").name == "Razorpay"
    assert registry.get("missing") is None
    fintech = registry.select(tags=["backend", "fintech"], locations=["India"])
    assert {company.slug for company in fintech} >= {
        "razorpay",
        "cred",
        "groww",
        "phonepe",
        "stripe",
        "zeta",
        "stable-money",
    }

    early_career = registry.select(
        tags=["backend", "early-career"],
        locations=["India"],
    )
    assert {company.slug for company in early_career} == {
        "redwood-software",
        "stable-money",
    }


def test_inactive_entry_is_loaded_but_excluded_and_uses_safe_defaults(tmp_path):
    path = _write_pack(
        tmp_path,
        """
name: test
companies:
  - name: Future Company
    slug: future-company
    source: google_jobs
    source_token: null
    active: false
""",
    )

    registry = load_company_pack(path)
    company = registry.get("future-company", include_inactive=True)

    assert company is not None
    assert company.careers_domains == []
    assert company.hire_locations == []
    assert company.tags == []
    assert registry.active_companies == ()
    assert registry.get("future-company") is None
    assert registry.select(active_only=False) == (company,)


@pytest.mark.parametrize(
    "body, message",
    [
        ("- not-a-mapping\n", "must contain a mapping"),
        ("name: test\ncompanies: nope\n", "companies list"),
        (
            """
name: test
companies:
  - name: Broken
    slug: broken
    source: greenhouse
    source_token: null
    careers_domains: [broken.example]
""",
            "requires source_token",
        ),
        (
            """
name: test
companies:
  - name: Broken
    slug: broken
    source: google_jobs
    source_token: invented
    careers_domains: [broken.example]
""",
            "null source_token",
        ),
        (
            """
name: test
companies:
  - name: Broken
    slug: Broken_Slug
    source: google_jobs
    source_token: null
    careers_domains: [broken.example]
""",
            "slug must contain",
        ),
        (
            """
name: test
companies:
  - name: Broken
    slug: broken
    source: google_jobs
    source_token: null
    careers_domains: [https://broken.example/jobs]
""",
            "lowercase hostname",
        ),
        (
            """
name: test
companies:
  - name: Broken
    slug: broken
    source: google_jobs
    source_token: null
    careers_domains: [broken.example]
    typo_field: true
""",
            "unknown fields",
        ),
    ],
)
def test_malformed_config_is_rejected(tmp_path, body, message):
    with pytest.raises(RegistryError, match=message):
        load_company_pack(_write_pack(tmp_path, body))


def test_duplicate_slugs_are_rejected(tmp_path):
    path = _write_pack(
        tmp_path,
        """
name: duplicate
companies:
  - name: One
    slug: same
    source: google_jobs
    source_token: null
    careers_domains: [one.example]
  - name: Two
    slug: same
    source: google_jobs
    source_token: null
    careers_domains: [two.example]
""",
    )

    with pytest.raises(RegistryError, match="duplicate company slug"):
        load_company_pack(path)


@pytest.mark.parametrize(
    "body",
    [
        "name: first\nname: second\ncompanies: []\n",
        """
name: duplicate
companies:
  - name: One
    slug: one
    slug: two
    source: google_jobs
    source_token: null
    careers_domains: [one.example]
""",
    ],
)
def test_duplicate_yaml_keys_are_rejected(tmp_path, body):
    with pytest.raises(RegistryError, match="duplicate YAML key"):
        load_company_pack(_write_pack(tmp_path, body))


def test_named_pack_rejects_path_traversal(tmp_path):
    with pytest.raises(RegistryError, match="invalid company pack name"):
        load_company_pack("../secret", pack_dir=tmp_path)


def test_verifier_default_mode_is_hermetic(monkeypatch, capsys):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("network check should require --live")

    monkeypatch.setattr(verify_registry, "check_company_live", fail_if_called)

    assert verify_registry.main(["--pack", "backend_india"]) == 0
    output = capsys.readouterr().out
    assert "configured=35 active=35 inactive=0" in output
    assert "invalid=0 live_checks=not_requested" in output


def test_google_jobs_does_not_match_an_empty_company_name(monkeypatch):
    company = Company(
        name="Acme",
        slug="acme",
        source=CompanySource.google_jobs,
        source_token=None,
        careers_domains=["acme.example"],
    )
    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")
    monkeypatch.setattr(
        verify_registry,
        "_request_json",
        lambda url, timeout: {"jobs_results": [{"company_name": ""}]},
    )

    result = verify_registry._check_google_jobs(company, timeout=1)

    assert result.status == "dead"
    assert result.count == 0


@pytest.mark.parametrize(
    "job",
    [
        {
            "absolute_url": "https://evil.example/jobs/1",
            "company_name": "Acme",
        },
        {
            "absolute_url": "https://jobs.acme.example/jobs/1",
            "company_name": "Other Company",
        },
    ],
)
def test_live_check_rejects_untrusted_url_or_wrong_company(monkeypatch, job):
    company = Company(
        name="Acme",
        slug="acme",
        source=CompanySource.greenhouse,
        source_token="acme",
        careers_domains=["acme.example"],
    )
    monkeypatch.setattr(
        verify_registry,
        "_request_json",
        lambda url, timeout: {"jobs": [job]},
    )

    result = verify_registry.check_company_live(company, timeout=1)

    assert result.status == "dead"
    assert result.count == 0


def test_live_check_accepts_subdomain_url_and_company_board_suffix(monkeypatch):
    company = Company(
        name="Acme",
        slug="acme",
        source=CompanySource.greenhouse,
        source_token="acme",
        careers_domains=["acme.example"],
    )
    monkeypatch.setattr(
        verify_registry,
        "_request_json",
        lambda url, timeout: {
            "jobs": [
                {
                    "absolute_url": "https://jobs.acme.example/jobs/1",
                    "company_name": "Acme Job Board",
                }
            ]
        },
    )

    result = verify_registry.check_company_live(company, timeout=1)

    assert result.status == "verified"
    assert result.count == 1


def test_lever_live_check_rejects_a_different_company_token(monkeypatch):
    company = Company(
        name="CRED",
        slug="cred",
        source=CompanySource.lever,
        source_token="cred",
        careers_domains=["jobs.lever.co"],
    )
    monkeypatch.setattr(
        verify_registry,
        "_request_json",
        lambda url, timeout: [
            {
                "hostedUrl": "https://jobs.lever.co/palantir/role-1",
                "applyUrl": "https://jobs.lever.co/palantir/role-1/apply",
            }
        ],
    )

    result = verify_registry.check_company_live(company, timeout=1)

    assert result.status == "dead"
    assert result.count == 0


def test_workable_live_check_uses_post_and_account_scoped_records(monkeypatch):
    company = Company(
        name="PeopleCert",
        slug="peoplecert",
        source=CompanySource.workable,
        source_token="peoplecert",
        careers_domains=["apply.workable.com"],
    )
    calls = []

    def request(url, *, timeout, data=None):
        calls.append((url, timeout, data))
        return {
            "results": [
                {
                    "shortcode": "ABC123",
                    "state": "published",
                    "isInternal": False,
                }
            ]
        }

    monkeypatch.setattr(verify_registry, "_request_json", request)

    result = verify_registry.check_company_live(company, timeout=1)

    assert result.status == "verified"
    assert calls[0][2] == {"query": ""}


def test_amazon_live_check_validates_first_party_india_paths(monkeypatch):
    company = Company(
        name="Amazon",
        slug="amazon",
        source=CompanySource.bespoke,
        source_token="amazon",
        careers_domains=["amazon.jobs"],
    )
    monkeypatch.setattr(
        verify_registry,
        "_request_json",
        lambda url, timeout: {
            "jobs": [
                {"job_path": "/en/jobs/123/software-development-engineer"},
                {"job_path": "https://evil.example/jobs/123"},
            ]
        },
    )

    result = verify_registry.check_company_live(company, timeout=1)

    assert result.status == "verified"
    assert result.count == 1


def test_verifier_live_mode_reports_verified_unverified_and_dead(
    monkeypatch,
    capsys,
):
    registry = load_company_pack("backend_india")
    results = iter(
        [
            verify_registry.LiveCheck("verified", 3, "ok"),
            verify_registry.LiveCheck("unverified", None, "skipped"),
            verify_registry.LiveCheck("dead", 0, "empty"),
        ]
    )
    monkeypatch.setattr(
        verify_registry,
        "load_company_pack",
        lambda pack: type(registry)(
            registry.companies[:3],
            name="small",
            description="",
        ),
    )
    monkeypatch.setattr(
        verify_registry,
        "check_company_live",
        lambda company, timeout: next(results),
    )

    assert verify_registry.main(["--pack", "ignored", "--live"]) == 1
    assert "SUMMARY verified=1 unverified=1 dead=1" in capsys.readouterr().out


def test_strict_live_fails_on_unverified_source(monkeypatch, capsys):
    registry = load_company_pack("backend_india")
    monkeypatch.setattr(
        verify_registry,
        "load_company_pack",
        lambda pack: type(registry)(
            registry.companies[:1],
            name="small",
            description="",
        ),
    )
    monkeypatch.setattr(
        verify_registry,
        "check_company_live",
        lambda company, timeout: verify_registry.LiveCheck(
            "unverified",
            None,
            "not checked",
        ),
    )

    assert (
        verify_registry.main(
            ["--pack", "ignored", "--live", "--strict-live"],
        )
        == 1
    )
    assert "SUMMARY verified=0 unverified=1 dead=0" in capsys.readouterr().out


def test_project_docs_use_the_strict_live_dod_command():
    command = (
        ".venv/bin/python scripts/verify_registry.py "
        "--pack backend_india --live --strict-live"
    )

    assert command in Path("README.md").read_text(encoding="utf-8")
    plan = Path("REBUILD_PLAN_V2.md").read_text(encoding="utf-8")
    assert command in " ".join(plan.split())
