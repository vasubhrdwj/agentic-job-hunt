from __future__ import annotations

from dataclasses import dataclass, field

from job_hunt_agent.schemas import (
    Company,
    CompanySource,
    EmploymentType,
    JobCriteria,
    Role,
)
from job_hunt_agent.sources.base import FetchCompleteness, FetchScope
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
    company_slug: str | None = None,
    source_job_id: str | None = None,
) -> Role:
    return Role(
        company=company,
        title=title,
        url=url,
        location="India",
        summary="Build backend services.",
        match_reason="The posting asks for backend services.",
        source=source,
        company_slug=company_slug,
        source_job_id=source_job_id,
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
    criteria_calls: list[JobCriteria] = field(default_factory=list)

    def supports(self, company: Company) -> bool:
        return self.supported_source is None or company.source is self.supported_source

    def fetch_open_roles(self, company: Company, criteria: JobCriteria) -> list[Role]:
        self.calls += 1
        self.criteria_calls.append(criteria.model_copy(deep=True))
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


def test_result_api_reports_filtered_partial_fetch_and_preserves_cache_metadata():
    primary = FakeAdapter(
        name="greenhouse",
        roles=[
            _role(
                company_slug="acme",
                source_job_id="job-1",
            )
        ],
        supported_source=CompanySource.greenhouse,
    )
    resolver = SourceResolver([primary], fallback=FakeAdapter(name="google_jobs"))

    first = resolver.fetch_company_roles_result(_company(), _criteria())
    first.roles[0].title = "Caller mutation"
    second = resolver.fetch_company_roles_result(_company(), _criteria())

    assert first.scope is FetchScope.criteria_filtered
    assert first.completeness is FetchCompleteness.partial
    assert not first.authoritative_for_closure
    assert first.observed_count == first.returned_count == 1
    assert not first.cache_hit
    assert second.cache_hit
    assert second.fetch_id == first.fetch_id
    assert second.roles[0].title == "Backend Engineer"
    assert primary.calls == 1


def test_empty_or_failed_primary_uses_google_jobs_fallback():
    primary = FakeAdapter(
        name="greenhouse",
        raises=RuntimeError("board down"),
        supported_source=CompanySource.greenhouse,
    )
    fallback_role = _role(source=CompanySource.google_jobs)
    fallback = FakeAdapter(name="google_jobs", roles=[fallback_role])
    resolver = SourceResolver([primary, fallback], fallback=fallback)

    result = resolver.fetch_company_roles_result(
        _company(),
        _criteria(),
        use_cache=False,
    )

    assert result.roles == [fallback_role]
    assert result.used_fallback
    assert result.source is CompanySource.google_jobs
    assert result.warning_codes == ["source_fetch_failed"]
    assert "board down" not in result.model_dump_json()
    assert primary.calls == 1
    assert fallback.calls == 1


def test_fallback_failure_adds_only_safe_stable_warning_codes():
    primary = FakeAdapter(
        name="greenhouse",
        raises=RuntimeError("PRIVATE PRIMARY FAILURE"),
        supported_source=CompanySource.greenhouse,
    )
    fallback = FakeAdapter(
        name="google_jobs",
        raises=RuntimeError("PRIVATE FALLBACK FAILURE"),
    )
    result = SourceResolver([primary, fallback], fallback=fallback).fetch_company_roles_result(
        _company(),
        _criteria(),
        use_cache=False,
    )

    assert result.roles == []
    assert result.warning_codes == [
        "source_fetch_failed",
        "fallback_source_fetch_failed",
    ]
    assert "PRIVATE" not in result.model_dump_json()


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


def test_cache_separates_fallback_enabled_and_disabled_results():
    primary = FakeAdapter(
        name="greenhouse",
        supported_source=CompanySource.greenhouse,
    )
    fallback_role = _role(source=CompanySource.google_jobs)
    fallback = FakeAdapter(name="google_jobs", roles=[fallback_role])
    resolver = SourceResolver([primary, fallback], fallback=fallback)

    without_fallback = resolver.fetch_company_roles_result(
        _company(),
        _criteria(),
        allow_fallback=False,
    )
    with_fallback = resolver.fetch_company_roles_result(
        _company(),
        _criteria(),
        allow_fallback=True,
    )

    assert without_fallback.roles == []
    assert not without_fallback.used_fallback
    assert with_fallback.roles == [fallback_role]
    assert with_fallback.used_fallback
    assert primary.calls == 2
    assert fallback.calls == 1


def test_backend_search_expands_source_vocabulary_and_accepts_alias_titles():
    requested = _criteria(role_keywords=["Backend Engineer"])
    primary = FakeAdapter(
        name="greenhouse",
        supported_source=CompanySource.greenhouse,
        roles=[
            _role(
                company="Stable Money",
                title="Software Engineer I (Backend)",
                url="https://acme.example/jobs/software-engineer-backend",
            ),
            _role(
                company="Redwood Software",
                title="Site Reliability Engineer",
                url="https://acme.example/jobs/site-reliability-engineer",
            ),
            _role(
                title="Software Engineering Intern",
                url="https://acme.example/jobs/software-engineering-intern",
            ),
        ],
    )
    resolver = SourceResolver([primary], fallback=FakeAdapter(name="google_jobs"))

    roles = resolver.fetch_company_roles(
        _company(),
        requested,
        use_cache=False,
        allow_fallback=False,
    )

    assert [role.title for role in roles] == [
        "Software Engineer I (Backend)",
        "Site Reliability Engineer",
        "Software Engineering Intern",
    ]
    assert requested.role_keywords == ["Backend Engineer"]
    assert primary.criteria_calls[0].role_keywords == [
        "Backend Engineer",
        "Site Reliability Engineer",
        "SDE",
        "Backend Developer",
        "Infrastructure Engineer",
        "Associate Software Engineer",
        "Software Engineer",
        "Software Development Engineer",
        "Platform Engineer",
    ]


def test_explicit_application_engineer_search_is_not_rewritten_or_blocked():
    requested = _criteria(role_keywords=["Application Engineer"])
    primary = FakeAdapter(
        name="greenhouse",
        supported_source=CompanySource.greenhouse,
        roles=[
            _role(
                company="Twilio",
                title="Associate Application Engineer",
                url="https://acme.example/jobs/application-engineer",
            )
        ],
    )
    resolver = SourceResolver([primary], fallback=FakeAdapter(name="google_jobs"))

    roles = resolver.fetch_company_roles(
        _company(),
        requested,
        use_cache=False,
        allow_fallback=False,
    )

    assert [role.title for role in roles] == ["Associate Application Engineer"]
    assert primary.criteria_calls[0].role_keywords == ["Application Engineer"]


def test_backend_search_rejects_incidental_application_engineer_body_matches():
    primary = FakeAdapter(
        name="greenhouse",
        supported_source=CompanySource.greenhouse,
        roles=[
            _role(
                company="Twilio",
                title="Associate Application Engineer",
                url="https://acme.example/jobs/application-engineer",
            ),
            _role(
                company="Amazon",
                title="Software Application Engineer",
                url="https://acme.example/jobs/software-application-engineer",
            ),
        ],
    )
    resolver = SourceResolver([primary], fallback=FakeAdapter(name="google_jobs"))

    roles = resolver.fetch_company_roles(
        _company(),
        _criteria(role_keywords=["Software Engineer", "Backend Engineer"]),
        use_cache=False,
        allow_fallback=False,
    )

    assert roles == []


def test_custom_keyword_search_is_not_broadened_into_backend_role_family():
    requested = _criteria(
        role_keywords=["SCIM", "identity", "IAM", "software security"],
    )
    primary = FakeAdapter(
        name="greenhouse",
        supported_source=CompanySource.greenhouse,
        roles=[_role(title="SCIM Integration Specialist")],
    )
    resolver = SourceResolver([primary], fallback=FakeAdapter(name="google_jobs"))

    roles = resolver.fetch_company_roles(
        _company(),
        requested,
        use_cache=False,
        allow_fallback=False,
    )

    assert [role.title for role in roles] == ["SCIM Integration Specialist"]
    assert primary.criteria_calls[0].role_keywords == [
        "SCIM",
        "identity",
        "IAM",
        "software security",
    ]
    assert requested.role_keywords == [
        "SCIM",
        "identity",
        "IAM",
        "software security",
    ]


def test_backend_title_gate_rejects_recruiting_roles_matched_from_description():
    primary = FakeAdapter(
        name="bespoke",
        supported_source=CompanySource.bespoke,
        roles=[
            _role(
                company="Amazon",
                title=(
                    "Full Lifecycle Recruiter-II, "
                    "Amazon University Talent Acquisition"
                ),
                url="https://amazon.example/jobs/recruiter-ii",
                source=CompanySource.bespoke,
            ),
            _role(
                company="Amazon",
                title="Full Lifecycle Recruiter, Stores TA",
                url="https://amazon.example/jobs/stores-recruiter",
                source=CompanySource.bespoke,
            ),
            _role(
                company="Amazon",
                title="Software Engineering Recruiter",
                url="https://amazon.example/jobs/engineering-recruiter",
                source=CompanySource.bespoke,
            ),
        ],
    )
    resolver = SourceResolver([primary], fallback=FakeAdapter(name="google_jobs"))

    roles = resolver.fetch_company_roles(
        _company(source=CompanySource.bespoke),
        _criteria(role_keywords=["Software Engineer", "Backend Engineer"]),
        use_cache=False,
        allow_fallback=False,
    )

    assert roles == []


def test_filters_known_stale_and_wrong_type_but_preserves_unknown_dates():
    primary = FakeAdapter(
        name="greenhouse",
        supported_source=CompanySource.greenhouse,
        roles=[
            _role(title="Backend Engineer Fresh", posted_at="2 days ago"),
            _role(title="Backend Engineer Stale", posted_at="90 days ago"),
            _role(
                title="Backend Engineer Contract",
                employment_type=EmploymentType.contract,
            ),
            _role(
                title="Backend Engineer Undated",
                url="https://acme.example/jobs/undated",
                posted_at=None,
            ),
            _role(
                title="Backend Engineer Unparseable date",
                url="https://acme.example/jobs/unparseable-date",
                posted_at="not-a-date",
            ),
        ],
    )
    resolver = SourceResolver([primary], fallback=FakeAdapter(name="google_jobs"))

    roles = resolver.fetch_company_roles(
        _company(),
        _criteria(max_age_days=45),
        use_cache=False,
    )

    assert [role.title for role in roles] == [
        "Backend Engineer Fresh",
        "Backend Engineer Undated",
        "Backend Engineer Unparseable date",
    ]


def test_full_time_filter_preserves_unknown_employment_type():
    primary = FakeAdapter(
        name="greenhouse",
        supported_source=CompanySource.greenhouse,
        roles=[
            _role(
                title="Backend Engineer Unknown type",
                employment_type=EmploymentType.unknown,
            )
        ],
    )
    resolver = SourceResolver([primary], fallback=FakeAdapter(name="google_jobs"))

    roles = resolver.fetch_company_roles(
        _company(),
        _criteria(),
        use_cache=False,
    )

    assert len(roles) == 1
    assert roles[0].employment_type is EmploymentType.unknown


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


def test_junior_filter_rejects_meesho_sde_iii_and_structured_range_requirement():
    primary = FakeAdapter(
        name="lever",
        supported_source=CompanySource.lever,
        roles=[
            _role(
                company="Meesho",
                title="Software Development Engineer III Data",
                url="https://jobs.lever.co/meesho/sde-iii-data",
                source=CompanySource.lever,
            ).model_copy(
                update={
                    # Lever keeps structured list content in the normalized
                    # summary while descriptionPlain may contain only an intro.
                    "summary": "Requirements: 5 - 8 yrs of relevant experience.",
                    "raw_description": "Build reliable data products at scale.",
                }
            ),
        ],
    )
    company = _company(
        name="Meesho",
        slug="meesho",
        source=CompanySource.lever,
    ).model_copy(update={"careers_domains": ["jobs.lever.co"]})
    resolver = SourceResolver([primary], fallback=FakeAdapter(name="google_jobs"))

    junior_roles = resolver.fetch_company_roles(
        company,
        _criteria(),
        use_cache=False,
        allow_fallback=False,
    )
    mid_roles = resolver.fetch_company_roles(
        company,
        _criteria(seniority="mid"),
        use_cache=False,
        allow_fallback=False,
    )

    assert junior_roles == []
    assert [role.title for role in mid_roles] == [
        "Software Development Engineer III Data"
    ]


def test_junior_filter_uses_structured_summary_experience_when_description_is_sparse():
    primary = FakeAdapter(
        name="lever",
        supported_source=CompanySource.lever,
        roles=[
            _role(
                title="Software Engineer Data",
                source=CompanySource.lever,
            ).model_copy(
                update={
                    "summary": "Requirements: 5 - 8 yrs of relevant experience.",
                    "raw_description": "Build reliable data products at scale.",
                }
            )
        ],
    )
    resolver = SourceResolver([primary], fallback=FakeAdapter(name="google_jobs"))

    roles = resolver.fetch_company_roles(
        _company(source=CompanySource.lever),
        _criteria(),
        use_cache=False,
        allow_fallback=False,
    )

    assert roles == []


def test_junior_filter_rejects_only_explicitly_ineligible_experience():
    descriptions = {
        "range": "Requirements: 5–8 years of backend experience.",
        "minimum": "Minimum of three years of professional experience.",
        "more-than": "Requires more than 2 yrs of production experience.",
        "plus": "You should have 3+ years building distributed systems.",
        "unknown": "Build reliable backend services.",
        "missing": None,
        "junior-range": "Requires 1-3 years of backend experience.",
        "junior-plus": "Requires 2+ years of Python experience.",
        "upper-bound": "This opening is for candidates with up to 5 years of experience.",
        "optional": "Five years of Kafka experience preferred.",
        "company-age": "The product launched 5 years ago and now serves India.",
    }
    roles = [
        _role(
            title=f"Backend Engineer {label}",
            url=f"https://acme.example/jobs/{label}",
        ).model_copy(update={"raw_description": description})
        for label, description in descriptions.items()
    ]
    primary = FakeAdapter(
        name="greenhouse",
        supported_source=CompanySource.greenhouse,
        roles=roles,
    )
    resolver = SourceResolver([primary], fallback=FakeAdapter(name="google_jobs"))

    matched = resolver.fetch_company_roles(
        _company(),
        _criteria(),
        use_cache=False,
        allow_fallback=False,
    )

    assert [role.title for role in matched] == [
        "Backend Engineer unknown",
        "Backend Engineer missing",
        "Backend Engineer junior-range",
        "Backend Engineer junior-plus",
        "Backend Engineer upper-bound",
        "Backend Engineer optional",
        "Backend Engineer company-age",
    ]


def test_registry_aggregation_preserves_distinct_requisitions_and_dedupes_urls():
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

    assert len(roles) == 3
    assert [role.url for role in roles] == [
        "https://acme.example/jobs/1",
        "https://acme.example/jobs/2",
        "https://beta.example/jobs/1",
    ]


def test_dedupes_same_native_posting_even_when_url_and_title_change():
    original = _role(
        company_slug="acme",
        source_job_id="native-1",
    )
    changed = _role(
        title="Backend Engineer II",
        url="https://acme.example/jobs/native-1-new-url",
        company_slug="acme",
        source_job_id="native-1",
    )
    primary = FakeAdapter(
        name="greenhouse",
        roles=[original, changed],
        supported_source=CompanySource.greenhouse,
    )
    resolver = SourceResolver([primary], fallback=FakeAdapter(name="google_jobs"))

    roles = resolver.fetch_company_roles(
        _company(),
        _criteria(),
        use_cache=False,
    )

    assert roles == [original]


def test_url_fallback_dedupes_host_case_trailing_slash_and_tracking_query():
    tracked = _role(
        url=(
            "https://ACME.example/jobs/1/"
            "?utm_source=google&utm_medium=organic"
        ),
    )
    canonical = _role(url="https://acme.example/jobs/1")
    primary = FakeAdapter(
        name="greenhouse",
        roles=[tracked, canonical],
        supported_source=CompanySource.greenhouse,
    )

    roles = SourceResolver(
        [primary],
        fallback=FakeAdapter(name="google_jobs"),
    ).fetch_company_roles(_company(), _criteria(), use_cache=False)

    assert roles == [tracked]


def test_registry_can_require_company_careers_domain():
    acme = _company()
    primary = FakeAdapter(
        name="greenhouse",
        supported_source=CompanySource.greenhouse,
        roles=[
            _role(title="Backend Engineer Trusted"),
            _role(
                title="Backend Engineer Aggregator",
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

    assert [role.title for role in roles] == ["Backend Engineer Trusted"]


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
