"""Deterministic, network-free public-contact fixtures for explicit mock mode."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from ..contact_discovery import (
    DiscoveryCategory,
    ProviderSearchPage,
    ProviderSearchResult,
)


class MockContactSearchProvider:
    """Return bounded synthetic evidence only when ``USE_MOCKS`` is explicit."""

    name = "mock_public_search"

    def search(
        self,
        query: str,
        *,
        category: DiscoveryCategory,
        limit: int,
    ) -> ProviderSearchPage:
        phrases = re.findall(r'"([^"\\]{1,300})"', query)
        company = phrases[0].strip() if phrases else "Example Company"
        role_title = phrases[1].strip() if len(phrases) > 1 else "Software Engineer"
        fixtures = {
            DiscoveryCategory.peer: (
                ("Asha Mehta", role_title),
                ("Rohan Iyer", role_title),
                ("Neha Kapoor", role_title),
            ),
            DiscoveryCategory.leader: (("Maya Srinivas", "Engineering Manager"),),
            DiscoveryCategory.recruiter: (("Kabir Malhotra", "Technical Recruiter"),),
        }[category]
        observed_at = datetime.now(timezone.utc)
        results = []
        for position, (name, title) in enumerate(fixtures[:limit], start=1):
            identity = hashlib.sha256(
                f"{company}:{category.value}:{name}".encode("utf-8")
            ).hexdigest()[:16]
            results.append(
                ProviderSearchResult(
                    result_title=f"{name} - {title} - {company} | LinkedIn",
                    result_url=f"https://www.linkedin.com/in/mock-{identity}",
                    result_excerpt=(
                        f"{title} at {company} working on the hiring team's systems."
                    ),
                    result_position=position,
                    observed_at=observed_at,
                    confidence=0.9,
                )
            )
        return ProviderSearchPage(results=tuple(results), exhausted=True)


__all__ = ["MockContactSearchProvider"]
