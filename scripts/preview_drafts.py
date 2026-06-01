"""Quick eyeball check for draft_message. Run: python scripts/preview_drafts.py"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

from job_hunt_agent.schemas import Person, Role
from job_hunt_agent.tools.draft import draft_message


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    resume_text = (REPO_ROOT / "fixtures" / "sample_resume.txt").read_text(encoding="utf-8")

    samples = [
        (
            Role(
                company="Okta",
                title="Senior Software Engineer, Lifecycle Management",
                url="https://example.com/jobs/okta-lifecycle",
                location="Remote-India",
                summary=(
                    "Build provisioning and lifecycle workflows for enterprise "
                    "identity customers. The role touches SCIM integrations, "
                    "directory sync, and large-scale account automation."
                ),
                match_reason=(
                    "Centers on SCIM 2.0 provisioning at scale and accepts "
                    "Remote-India, which lines up with the candidate's recent "
                    "SCIM server work."
                ),
            ),
            Person(
                name="Anika Rao",
                title="Staff Engineer, Lifecycle Management",
                company="Okta",
                profile_url="https://www.linkedin.com/in/example-anika",
                source="linkedin",
                why_relevant=(
                    "Owns the lifecycle management team that ships SCIM "
                    "provisioning features."
                ),
            ),
        ),
        (
            Role(
                company="Saviynt",
                title="Backend Engineer, Identity Governance",
                url="https://example.com/jobs/saviynt-iga",
                location="Hyderabad",
                summary=(
                    "Work on identity governance APIs and access workflows "
                    "used by enterprise security teams. Emphasizes backend "
                    "reliability and IAM domain knowledge."
                ),
                match_reason=(
                    "IAM-adjacent backend work with audit-log expectations "
                    "similar to the candidate's ClickHouse pipeline."
                ),
            ),
            Person(
                name="Karan Shah",
                title="Principal Engineer, Identity Governance",
                company="Saviynt",
                profile_url="https://www.linkedin.com/in/example-karan",
                source="linkedin",
                why_relevant=(
                    "Owns identity governance architecture and would know "
                    "whether a SCIM/OIDC background fits the team."
                ),
            ),
        ),
        (
            Role(
                company="JumpCloud",
                title="Software Engineer, Directory Platform",
                url="https://example.com/jobs/jumpcloud-directory",
                location="Remote",
                summary=(
                    "Build directory platform services for device, user, and "
                    "access management. Owns identity data flows used across "
                    "customer integrations."
                ),
                match_reason=(
                    "Directory + OIDC scope aligns with the candidate's "
                    "Keycloak-to-OIDC migration experience."
                ),
            ),
            Person(
                name="Priya Das",
                title="Staff Engineer, Directory Platform",
                company="JumpCloud",
                profile_url="https://github.com/example-priyad",
                source="github",
                why_relevant=(
                    "Works directly on directory platform services, the team "
                    "this role would join."
                ),
            ),
        ),
    ]

    for index, (role, person) in enumerate(samples, start=1):
        print(f"\n=== Sample {index}: {person.name} at {role.company} ===")
        print(draft_message(role, person, resume_text))


if __name__ == "__main__":
    main()
