from __future__ import annotations

from dataclasses import dataclass, field

from job_hunt_agent.schemas import (
    Company,
    CompanySource,
    EmploymentType,
    JobCriteria,
    Role,
)
from job_hunt_agent.sources.registry import CompanyRegistry
from job_hunt_agent.sources.resolver import SourceResolver, is_first_party_role


def _company(
    *,
    name: str = "Acme",
    slug: str = "acme",
    source: CompanySource = CompanySource.greenhouse,
) -> Company:
    return Company(
        name=name,
        slug=slug,
        source=source,
        source_token=slug if source is not CompanySource.google_jobs else None,
        careers_domains=[f"{slug}.example"],
        tags=["backend"],
    )


def _criteria(**updates) -> JobCriteria:
    base = JobCriteria(
        role_keywords=["backend engineer"],
        seniority="junior",
        location=["India"],
    )
    return base.model_copy(update=updates)


def _role(
    *,
    company: str = "Acme",
    title: str = "Backend Engineer",
    url: str = "https://acme.example/jobs/1",
    posted_at: str | None = "1 day ago",
    employment_type: EmploymentType = EmploymentType.full_time,
    source: CompanySource = CompanySource.greenhouse,
) -> Role:
    return Role(
        company=company,
        title=title,
        url=url,
        location="India",
        summary="Build backend services.",
        match_reason="The posting asks for backend services.",
        source=source,
        apply_urls=[url],
        posted_at=posted_at,
        employment_type=employment_type,
        raw_description="Build backend services.",
    )


@dataclass
class FakeAdapter:
    name: str
    roles: list[Role] = field(default_factory=list)
    roles_by_slug: dict[str, list[Role]] = field(default_factory=dict)
    supported_source: CompanySource | None = None
    raises: Exception | None = None
    calls: int = 0

    def supports(self, company: Company) -> bool:
        return self.supported_source is None or company.source is self.supported_source

    def fetch_open_roles(self, company: Company, criteria: JobCriteria) -> list[Role]:
        del criteria
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        roles = self.roles_by_slug.get(company.slug, self.roles)
        return [role.model_copy(deep=True) for role in roles]


def test_uses_explicit_adapter_and_daily_cache_returns_copies():
    primary = FakeAdapter(
        name="greenhouse",
        roles=[_role()],
        supported_source=CompanySource.greenhouse,
    )
    fallback = FakeAdapter(name="google_jobs")
    resolver = SourceResolver([primary, fallback], fallback=fallback)

    first = resolver.fetch_company_roles(_company(), _criteria())
    first[0].title = "Mutated by caller"
    second = resolver.fetch_company_roles(_company(), _criteria())

    assert primary.calls == 1
    assert fallback.calls == 0
    assert second[0].title == "Backend Engineer"


def test_empty_or_failed_primary_uses_google_jobs_fallback():
    primary = FakeAdapter(
        name="greenhouse",
        raises=RuntimeError("board down"),
        supported_source=CompanySource.greenhouse,
    )
    fallback_role = _role(source=CompanySource.google_jobs)
    fallback = FakeAdapter(name="google_jobs", roles=[fallback_role])
    resolver = SourceResolver([primary, fallback], fallback=fallback)

    roles = resolver.fetch_company_roles(_company(), _criteria(), use_cache=False)

    assert roles == [fallback_role]
    assert primary.calls == 1
    assert fallback.calls == 1


def test_fallback_can_be_disabled_for_first_party_supply_checks():
    primary = FakeAdapter(
        name="greenhouse",
        supported_source=CompanySource.greenhouse,
    )
    fallback = FakeAdapter(
        name="google_jobs",
        roles=[_role(source=CompanySource.google_jobs)],
    )
    resolver = SourceResolver([primary, fallback], fallback=fallback)

    roles = resolver.fetch_company_roles(
        _company(),
        _criteria(),
        use_cache=False,
        allow_fallback=False,
    )

    assert roles == []
    assert primary.calls == 1
    assert fallback.calls == 0


def test_filters_stale_wrong_type_and_unverifiable_dates():
    primary = FakeAdapter(
        name="greenhouse",
        supported_source=CompanySource.greenhouse,
        roles=[
            _role(title="Fresh", posted_at="2 days ago"),
            _role(title="Stale", posted_at="90 days ago"),
            _role(title="Contract", employment_type=EmploymentType.contract),
            _role(
                title="Undated",
                url="https://acme.example/jobs/undated",
                posted_at=None,
            ),
        ],
    )
    resolver = SourceResolver([primary], fallback=FakeAdapter(name="google_jobs"))

    roles = resolver.fetch_company_roles(
        _company(),
        _criteria(max_age_days=45),
        use_cache=False,
    )

    assert [role.title for role in roles] == ["Fresh"]


def test_full_time_filter_rejects_unknown_employment_type():
    primary = FakeAdapter(
        name="greenhouse",
        supported_source=CompanySource.greenhouse,
        roles=[
            _role(
                title="Unknown type",
                employment_type=EmploymentType.unknown,
            )
        ],
    )
    resolver = SourceResolver([primary], fallback=FakeAdapter(name="google_jobs"))

    assert resolver.fetch_company_roles(
        _company(),
        _criteria(),
        use_cache=False,
    ) == []


def test_junior_backend_intent_rejects_frontend_mobile_and_high_experience():
    primary = FakeAdapter(
        name="greenhouse",
        supported_source=CompanySource.greenhouse,
        roles=[
            _role(title="Frontend Software Engineer"),
            _role(
                title="Software Engineer",
                url="https://acme.example/jobs/senior-hidden",
            ).model_copy(
                update={
                    "raw_description": (
                        "A long introduction. " * 500
                        + "Requires 5+ years of backend experience."
                    )
                }
            ),
            _role(
                title="Backend Engineer",
                url="https://acme.example/jobs/intermediate",
            ).model_copy(
                update={"raw_description": "An Intermediate Backend Engineer role."}
            ),
            _role(
                title="Backend Engineer",
                url="https://acme.example/jobs/junior",
            ).model_copy(
                update={"raw_description": "Requires 1 year of Python experience."}
            ),
        ],
    )
    resolver = SourceResolver([primary], fallback=FakeAdapter(name="google_jobs"))

    roles = resolver.fetch_company_roles(
        _company(),
        _criteria(),
        use_cache=False,
    )

    assert [role.title for role in roles] == ["Backend Engineer"]


def test_registry_aggregation_dedupes_by_company_title_and_apply_url():
    acme = _company()
    beta = _company(name="Beta", slug="beta")
    duplicate_title = _role(url="https://acme.example/jobs/2")
    shared_url = _role(
        company="Beta",
        title="Platform Engineer",
        url="https://acme.example/jobs/1",
    )
    beta_role = _role(
        company="Beta",
        url="https://beta.example/jobs/1",
    )
    primary = FakeAdapter(
        name="greenhouse",
        supported_source=CompanySource.greenhouse,
        roles_by_slug={
            "acme": [_role(), duplicate_title],
            "beta": [beta_role, shared_url],
        },
    )
    resolver = SourceResolver([primary], fallback=FakeAdapter(name="google_jobs"))

    roles = resolver.fetch_registry_roles(
        CompanyRegistry([acme, beta]),
        _criteria(),
        use_cache=False,
    )

    assert len(roles) == 2
    assert {(role.company, role.title) for role in roles} == {
        ("Acme", "Backend Engineer"),
        ("Beta", "Backend Engineer"),
    }


def test_registry_can_require_company_careers_domain():
    acme = _company()
    primary = FakeAdapter(
        name="greenhouse",
        supported_source=CompanySource.greenhouse,
        roles=[
            _role(title="Trusted"),
            _role(
                title="Aggregator",
                url="https://jobs.example.net/acme/aggregator",
            ),
        ],
    )
    resolver = SourceResolver([primary], fallback=FakeAdapter(name="google_jobs"))

    roles = resolver.fetch_registry_roles(
        CompanyRegistry([acme]),
        _criteria(),
        use_cache=False,
        require_first_party=True,
    )

    assert [role.title for role in roles] == ["Trusted"]


def test_first_party_url_requires_safe_https_domain():
    company = _company()

    assert is_first_party_role(_role(), company)
    assert not is_first_party_role(
        _role(url="https://evil.example\\@acme.example/jobs/1"),
        company,
    )
    assert not is_first_party_role(
        _role(url="https://user@acme.example/jobs/1"),
        company,
    )
    assert not is_first_party_role(
        _role(url="https://acme.example/jobs/../other/1"),
        company,
    )
    assert not is_first_party_role(
        _role(url="https://acme.example/jobs/%2e%2e/other/1"),
        company,
    )


def test_first_party_url_requires_shared_ats_tenant_match():
    cases = [
        (
            _company(
                name="MongoDB",
                slug="mongodb",
                source=CompanySource.greenhouse,
            ).model_copy(
                update={"careers_domains": ["job-boards.greenhouse.io"]},
            ),
            "https://job-boards.greenhouse.io/other/jobs/123",
        ),
        (
            _company(
                name="Palantir",
                slug="palantir",
                source=CompanySource.lever,
            ).model_copy(update={"careers_domains": ["jobs.lever.co"]}),
            "https://jobs.lever.co/other/123/apply",
        ),
        (
            _company(
                name="Ashby",
                slug="ashby",
                source=CompanySource.ashby,
            ).model_copy(update={"careers_domains": ["jobs.ashbyhq.com"]}),
            "https://jobs.ashbyhq.com/other/123",
        ),
        (
            _company(
                name="Freshworks",
                slug="freshworks",
                source=CompanySource.smartrecruiters,
            ).model_copy(
                update={"careers_domains": ["jobs.smartrecruiters.com"]},
            ),
            "https://jobs.smartrecruiters.com/other/123",
        ),
    ]

    for company, url in cases:
        assert not is_first_party_role(
            _role(
                company=company.name,
                url=url,
                source=company.source,
            ),
            company,
        )
