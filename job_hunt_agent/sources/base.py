"""Shared protocol implemented by every job-source adapter."""

from typing import Protocol, runtime_checkable
from urllib.parse import unquote

from job_hunt_agent.schemas import Company, JobCriteria, Role


def safe_url_path_parts(path: str) -> tuple[str, ...] | None:
    """Return decoded path segments, rejecting browser-normalized traversal."""
    parts: list[str] = []
    for raw_part in path.split("/"):
        if not raw_part:
            continue
        decoded = raw_part
        for _ in range(2):
            decoded = unquote(decoded)
        if (
            decoded in {".", ".."}
            or "/" in decoded
            or "\\" in decoded
            or any(ord(character) < 32 for character in decoded)
        ):
            return None
        parts.append(decoded)
    return tuple(parts)


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
