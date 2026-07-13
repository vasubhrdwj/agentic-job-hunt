"""Evidence, completeness, and diversity tests for pure contact discovery."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from job_hunt_agent.contact_discovery import (
    BenchCoverageStatus,
    ContactCategory,
    ContactProviderConfigurationError,
    DiscoveryCategory,
    DiscoveryOutcome,
    ProviderSearchResult,
    discover_contacts,
    normalize_profile_url,
    select_contact_bench,
)
from job_hunt_agent.schemas import Role


OBSERVED_AT = datetime(2026, 7, 14, 8, 30, tzinfo=timezone.utc)


def _role() -> Role:
    return Role(
        company="Twilio",
        title="Engineer, Identity & Access",
        url="https://jobs.example.test/twilio/identity-engineer",
        location="Remote - India",
        summary="Build identity systems using SCIM, SAML, OAuth, and OIDC.",
        match_reason="Identity engineering experience overlaps the role.",
    )


def _result(
    name: str,
    title: str,
    *,
    slug: str,
    company: str = "Twilio",
    confidence: float | None = None,
    position: int | None = None,
    observed_at: datetime | None = None,
    url: str | None = None,
    excerpt: str | None = None,
) -> ProviderSearchResult:
    return ProviderSearchResult(
        result_title=f"{name} - {title} - {company} | LinkedIn",
        result_url=url or f"https://www.linkedin.com/in/{slug}",
        result_excerpt=excerpt or f"{title} at {company} working on identity systems.",
        result_position=position,
        observed_at=observed_at,
        confidence=confidence,
    )


class FakeProvider:
    name = "fake-search"

    def __init__(
        self,
        responses: dict[DiscoveryCategory, list[ProviderSearchResult]] | None = None,
        *,
        failures: dict[DiscoveryCategory, Exception] | None = None,
    ) -> None:
        self.responses = responses or {}
        self.failures = failures or {}
        self.calls: list[tuple[str, DiscoveryCategory, int]] = []

    def search(
        self,
        query: str,
        *,
        category: DiscoveryCategory,
        limit: int,
    ) -> list[ProviderSearchResult]:
        self.calls.append((query, category, limit))
        failure = self.failures.get(category)
        if failure is not None:
            raise failure
        return self.responses.get(category, [])


def _full_provider() -> FakeProvider:
    peers = [
        _result(
            f"Peer Person{index}",
            "Staff Software Engineer" if index == 0 else "Senior Software Engineer",
            slug=f"peer-{index}",
            position=index + 1,
        )
        for index in range(6)
    ]
    return FakeProvider(
        {
            DiscoveryCategory.peer: peers,
            DiscoveryCategory.leader: [
                _result(
                    "Leader Person",
                    "Engineering Manager",
                    slug="leader-person",
                ),
                _result(
                    "Director Person",
                    "Engineering Director",
                    slug="director-person",
                ),
            ],
            DiscoveryCategory.recruiter: [
                _result(
                    "Recruiter Person",
                    "Technical Recruiter",
                    slug="recruiter-person",
                ),
                _result(
                    "Sourcer Person",
                    "Talent Sourcer",
                    slug="sourcer-person",
                ),
            ],
        }
    )


def test_discovery_searches_every_category_and_does_not_stop_at_five() -> None:
    provider = _full_provider()

    result = discover_contacts(_role(), provider=provider, observed_at=OBSERVED_AT)

    assert [call[1] for call in provider.calls] == [
        DiscoveryCategory.peer,
        DiscoveryCategory.leader,
        DiscoveryCategory.recruiter,
    ]
    assert all(call[2] == 12 for call in provider.calls)
    assert len(result.candidates) == 10
    assert result.diagnostics.queries_succeeded == 3
    assert result.diagnostics.exhausted is True


def test_discovery_caps_pool_at_twelve_after_all_category_queries() -> None:
    provider = _full_provider()
    provider.responses[DiscoveryCategory.peer].extend(
        _result(
            f"Extra Person{index}",
            "Backend Software Engineer",
            slug=f"extra-{index}",
        )
        for index in range(6)
    )

    result = discover_contacts(_role(), provider=provider, observed_at=OBSERVED_AT)

    assert len(provider.calls) == 3
    assert len(result.candidates) == 12
    assert len({candidate.normalized_profile_url for candidate in result.candidates}) == 12
    assert result.diagnostics.candidate_limit_reached is True
    assert result.diagnostics.outcome is DiscoveryOutcome.candidate_limit_reached
    assert result.diagnostics.exhausted is False


def test_profile_identity_is_strictly_normalized_and_duplicate_evidence_is_retained() -> None:
    second_observation = OBSERVED_AT + timedelta(minutes=5)
    duplicate_peer = _result(
        "Priya Rao",
        "Staff Software Engineer",
        slug="unused",
        url="https://in.linkedin.com/in/Priya-Rao/?trk=public_profile",
        excerpt="Staff Software Engineer at Twilio working on identity platform.",
        position=7,
        observed_at=OBSERVED_AT,
    )
    duplicate_leader_lane = _result(
        "Priya Rao",
        "Staff Software Engineer",
        slug="unused",
        url="https://www.linkedin.com/in/priya-rao#about",
        excerpt="Staff Software Engineer at Twilio building access systems.",
        position=2,
        observed_at=second_observation,
    )
    provider = FakeProvider(
        {
            DiscoveryCategory.peer: [duplicate_peer],
            DiscoveryCategory.leader: [duplicate_leader_lane],
        }
    )

    result = discover_contacts(_role(), provider=provider, observed_at=OBSERVED_AT)

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.profile_url == "https://www.linkedin.com/in/priya-rao"
    assert candidate.normalized_profile_url == candidate.profile_url
    assert len(candidate.evidence) == 2
    assert candidate.evidence[0].result_excerpt == duplicate_peer.result_excerpt
    assert candidate.evidence[0].result_url == duplicate_peer.result_url
    assert candidate.evidence[0].result_position == 7
    assert candidate.evidence[0].observed_at == OBSERVED_AT
    assert candidate.evidence[1].observed_at == second_observation
    assert candidate.evidence[1].result_url == "https://www.linkedin.com/in/priya-rao"
    assert {item.query_category for item in candidate.evidence} == {
        DiscoveryCategory.peer,
        DiscoveryCategory.leader,
    }


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://IN.linkedin.com/in/Priya-Rao/?trk=search#about",
            ("https://www.linkedin.com/in/priya-rao", "linkedin"),
        ),
        (
            "https://github.com/Octo-Cat?tab=repositories",
            ("https://github.com/octo-cat", "github"),
        ),
        ("http://www.linkedin.com/in/priya", None),
        ("https://linkedin.example.com/in/priya", None),
        ("https://evil.example\\@linkedin.com/in/priya", None),
        ("https://user@linkedin.com/in/priya", None),
        ("https://www.linkedin.com/company/twilio", None),
        ("https://github.com/octo-cat/project", None),
        ("https://github.com/topics", None),
        ("https://github.com/bad--name", None),
        ("https://www.linkedin.com/in/%2e%2e", None),
    ],
)
def test_normalize_profile_url_accepts_only_person_profiles(
    url: str,
    expected: tuple[str, str] | None,
) -> None:
    assert normalize_profile_url(url) == expected


def test_former_and_conflicting_current_employer_evidence_is_rejected() -> None:
    provider = FakeProvider(
        {
            DiscoveryCategory.peer: [
                _result(
                    "Former Person",
                    "Staff Software Engineer",
                    slug="former-person",
                    excerpt="Staff Software Engineer, formerly at Twilio.",
                ),
                ProviderSearchResult(
                    result_title=(
                        "Other Employer - AI Engineer @ E.ON Next | LinkedIn"
                    ),
                    result_url="https://www.linkedin.com/in/other-employer",
                    result_excerpt=(
                        "AI Engineer at E.ON Next. Current work references Twilio identity."
                    ),
                ),
                _result(
                    "Current Person",
                    "Senior Security Engineer",
                    slug="current-person",
                ),
            ]
        }
    )

    result = discover_contacts(_role(), provider=provider, observed_at=OBSERVED_AT)

    assert [candidate.public_name for candidate in result.candidates] == ["Current Person"]
    reasons = {rejected.reason_code for rejected in result.rejected_results}
    assert "former_target_employer" in reasons
    assert "conflicting_current_employer" in reasons


def test_a_conflicting_duplicate_disqualifies_the_identity_fail_closed() -> None:
    current = _result(
        "Priya Rao",
        "Staff Software Engineer",
        slug="priya-rao",
    )
    former = _result(
        "Priya Rao",
        "Staff Software Engineer",
        slug="priya-rao",
        excerpt="Staff Software Engineer, formerly at Twilio.",
    )
    provider = FakeProvider(
        {
            DiscoveryCategory.peer: [current],
            DiscoveryCategory.leader: [former],
        }
    )

    result = discover_contacts(_role(), provider=provider, observed_at=OBSERVED_AT)

    assert result.candidates == ()
    assert any(
        rejected.reason_code == "conflicting_duplicate_evidence"
        for rejected in result.rejected_results
    )


def test_selection_is_deterministic_and_category_diverse() -> None:
    discovery = discover_contacts(
        _role(),
        provider=_full_provider(),
        observed_at=OBSERVED_AT,
    )

    selected = select_contact_bench(discovery)
    reversed_selected = select_contact_bench(reversed(discovery.candidates))

    assert selected.coverage_status is BenchCoverageStatus.met
    assert selected.verified_count == 5
    assert selected.shortfall_reasons == ()
    assert [item.normalized_profile_url for item in selected.selected] == [
        item.normalized_profile_url for item in reversed_selected.selected
    ]
    categories = [item.category for item in selected.selected]
    assert categories.count(ContactCategory.team_peer) >= 2
    assert ContactCategory.team_leader in categories
    assert ContactCategory.recruiter in categories


def test_selection_reports_honest_three_of_five_after_exhaustion() -> None:
    provider = FakeProvider(
        {
            DiscoveryCategory.peer: [
                _result("Peer Person", "Staff Software Engineer", slug="peer-person")
            ],
            DiscoveryCategory.leader: [
                _result("Leader Person", "Engineering Manager", slug="leader-person")
            ],
            DiscoveryCategory.recruiter: [
                _result(
                    "Recruiter Person",
                    "Technical Recruiter",
                    slug="recruiter-person",
                )
            ],
        }
    )
    discovery = discover_contacts(_role(), provider=provider, observed_at=OBSERVED_AT)

    selection = select_contact_bench(discovery)

    assert selection.coverage_status is BenchCoverageStatus.partial
    assert selection.coverage_label == "3/5 verified"
    assert selection.verified_count == 3
    assert len(selection.selected) == 3
    assert selection.exhausted is True
    assert {reason.code for reason in selection.shortfall_reasons} == {
        "verified_contacts_shortfall",
        "search_exhausted",
    }
    assert all(reason.detail.strip() for reason in selection.shortfall_reasons)


def test_provider_failure_is_retryable_and_never_masquerades_as_exhaustion() -> None:
    provider = FakeProvider(
        {
            DiscoveryCategory.peer: [
                _result("Peer Person", "Staff Software Engineer", slug="peer-person")
            ],
            DiscoveryCategory.leader: [
                _result("Leader Person", "Engineering Manager", slug="leader-person")
            ],
        },
        failures={DiscoveryCategory.recruiter: TimeoutError("private provider body")},
    )

    discovery = discover_contacts(_role(), provider=provider, observed_at=OBSERVED_AT)
    selection = select_contact_bench(discovery)

    assert discovery.diagnostics.outcome is DiscoveryOutcome.partial_provider_failure
    assert discovery.diagnostics.provider_failed is True
    assert discovery.diagnostics.retryable is True
    assert discovery.diagnostics.exhausted is False
    assert discovery.diagnostics.provider_failures[0].error_type == "TimeoutError"
    assert "private provider body" not in repr(discovery.diagnostics)
    assert {reason.code for reason in selection.shortfall_reasons} == {
        "verified_contacts_shortfall",
        "provider_failure",
    }
    assert "search_exhausted" not in {
        reason.code for reason in selection.shortfall_reasons
    }


def test_configuration_failure_is_non_retryable_and_structured() -> None:
    failure = ContactProviderConfigurationError("missing private credential")
    provider = FakeProvider(
        failures={category: failure for category in DiscoveryCategory}
    )

    discovery = discover_contacts(_role(), provider=provider, observed_at=OBSERVED_AT)

    assert discovery.candidates == ()
    assert discovery.diagnostics.outcome is DiscoveryOutcome.configuration_failure
    assert discovery.diagnostics.queries_failed == 3
    assert discovery.diagnostics.retryable is False
    assert discovery.diagnostics.exhausted is False
    assert "missing private credential" not in repr(discovery.diagnostics)


def test_all_malformed_results_make_the_lane_retryable_not_exhausted() -> None:
    provider = FakeProvider(
        {
            DiscoveryCategory.peer: ["not a result"],  # type: ignore[list-item]
        }
    )

    discovery = discover_contacts(_role(), provider=provider, observed_at=OBSERVED_AT)

    assert discovery.diagnostics.outcome is DiscoveryOutcome.partial_provider_failure
    assert discovery.diagnostics.queries_succeeded == 2
    assert discovery.diagnostics.queries_failed == 1
    assert discovery.diagnostics.results_observed == 1
    assert discovery.diagnostics.retryable is True
    assert discovery.diagnostics.exhausted is False
    assert discovery.diagnostics.provider_failures[0].code == (
        "provider_malformed_response"
    )
    assert discovery.diagnostics.provider_failures[0].error_type == (
        "MalformedProviderResponse"
    )


@pytest.mark.parametrize("response", ["raw response", {"organic_results": []}])
def test_malformed_result_container_is_a_safe_provider_failure(response: object) -> None:
    class MalformedContainerProvider:
        name = "malformed-provider"

        def search(self, *args: object, **kwargs: object) -> object:
            return response

    discovery = discover_contacts(
        _role(),
        provider=MalformedContainerProvider(),  # type: ignore[arg-type]
        observed_at=OBSERVED_AT,
    )

    assert discovery.candidates == ()
    assert discovery.diagnostics.outcome is DiscoveryOutcome.provider_failure
    assert discovery.diagnostics.queries_succeeded == 0
    assert discovery.diagnostics.queries_failed == 3
    assert discovery.diagnostics.retryable is True
    assert discovery.diagnostics.exhausted is False
    assert {
        failure.code for failure in discovery.diagnostics.provider_failures
    } == {"provider_malformed_response"}


def test_selection_enforces_confidence_floor_without_padding() -> None:
    provider = FakeProvider(
        {
            DiscoveryCategory.peer: [
                _result(
                    "Strong Person",
                    "Staff Software Engineer",
                    slug="strong-person",
                    confidence=0.75,
                ),
                _result(
                    "Weak Person",
                    "Senior Software Engineer",
                    slug="weak-person",
                    confidence=0.7499,
                ),
            ]
        }
    )
    discovery = discover_contacts(_role(), provider=provider, observed_at=OBSERVED_AT)

    selection = select_contact_bench(discovery)

    assert [contact.public_name for contact in selection.selected] == ["Strong Person"]
    assert selection.verified_count == 1
    assert selection.eligible_count == 1


def test_github_profile_can_be_verified_with_evidence_and_selected() -> None:
    provider = FakeProvider(
        {
            DiscoveryCategory.peer: [
                ProviderSearchResult(
                    result_title="Neha Gupta - GitHub",
                    result_url="https://github.com/Neha-Gupta?tab=repositories",
                    result_excerpt=(
                        "Senior Software Engineer at Twilio working on identity infrastructure."
                    ),
                    result_position=4,
                )
            ]
        }
    )

    discovery = discover_contacts(_role(), provider=provider, observed_at=OBSERVED_AT)
    selection = select_contact_bench(discovery)

    assert len(discovery.candidates) == 1
    assert discovery.candidates[0].profile_source == "github"
    assert discovery.candidates[0].profile_url == "https://github.com/neha-gupta"
    assert discovery.candidates[0].confidence == 0.8
    assert selection.verified_count == 1


def test_public_configuration_bounds_fail_closed() -> None:
    provider = FakeProvider()

    with pytest.raises(ValueError, match="candidate_limit"):
        discover_contacts(_role(), provider=provider, candidate_limit=13)
    with pytest.raises(ValueError, match="timezone-aware"):
        discover_contacts(
            _role(),
            provider=provider,
            observed_at=datetime(2026, 7, 14, 8, 30),
        )
    with pytest.raises(ValueError, match="target_count"):
        select_contact_bench((), target_count=6)
    with pytest.raises(ValueError, match="confidence_floor"):
        select_contact_bench((), confidence_floor=0.74)
