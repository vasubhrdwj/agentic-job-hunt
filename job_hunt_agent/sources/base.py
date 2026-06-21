"""Shared protocol implemented by every job-source adapter."""

from typing import Protocol, runtime_checkable

from job_hunt_agent.schemas import Company, JobCriteria, Role


@runtime_checkable
class SourceAdapter(Protocol):
    """Structural contract for fetching roles from a company job source."""

    name: str

    def supports(self, company: Company) -> bool:
        """Return whether this adapter can fetch roles for ``company``."""
        ...

    def fetch_open_roles(
        self,
        company: Company,
        criteria: JobCriteria,
    ) -> list[Role]:
        """Fetch open roles matching ``criteria`` without fabricating results."""
        ...
