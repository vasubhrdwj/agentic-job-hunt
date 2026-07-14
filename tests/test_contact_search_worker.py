"""Hermetic execution tests for durable contact-search publication."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

import job_hunt_agent.contact_search_worker as contact_worker
from job_hunt_agent.contact_discovery import (
    ContactProviderConfigurationError,
    DiscoveryCategory,
    ProviderSearchResult,
)
from job_hunt_agent.database import Database
from job_hunt_agent.job_queue import cancel_job, claim_next_job
from job_hunt_agent.models import (
    Application,
    ApplicationContact,
    BackgroundJob,
    Base,
    Contact,
    ContactPlan,
    JobPosting,
    JobPostingVersion,
    Owner,
    OwnerOpportunity,
    WorkerHeartbeat,
)


NOW = datetime(2026, 7, 14, 8, 30, tzinfo=timezone.utc)
PROCESS_NOW = NOW + timedelta(minutes=1)
OWNER_ID = "owner-a"
APPLICATION_ID = "application-a"
PLAN_ID = "contact-plan-a"
JOB_ID = "contact-job-a"
WORKER_ID = "contact-worker"
LEASE_TOKEN = "contact-lease-token"


@dataclass(frozen=True)
class Claim:
    job_id: str = JOB_ID
    run_id: str = PLAN_ID
    lease_token: str = LEASE_TOKEN


class FakeProvider:
    name = "fake-public-search"

    def __init__(
        self,
        responses: dict[DiscoveryCategory, list[ProviderSearchResult]] | None = None,
        *,
        failures: dict[DiscoveryCategory, Exception] | None = None,
        on_first_call: Callable[[], None] | None = None,
    ) -> None:
        self.responses = responses or {}
        self.failures = failures or {}
        self.on_first_call = on_first_call
        self.calls: list[tuple[DiscoveryCategory, int]] = []

    def search(
        self,
        _query: str,
        *,
        category: DiscoveryCategory,
        limit: int,
    ) -> list[ProviderSearchResult]:
        self.calls.append((category, limit))
        if len(self.calls) == 1 and self.on_first_call is not None:
            self.on_first_call()
        failure = self.failures.get(category)
        if failure is not None:
            raise failure
        return self.responses.get(category, [])


@pytest.fixture
def contact_worker_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Database]:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'contact-worker.db'}")
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        _seed_application(session)
        _seed_contact_plan(session)
    monkeypatch.setattr(contact_worker, "_utcnow", lambda: PROCESS_NOW)
    try:
        yield database
    finally:
        database.dispose()


def _seed_application(session) -> None:
    session.add_all(
        [
            Owner(id=OWNER_ID, display_name="Owner A", timezone="Asia/Kolkata"),
            Owner(id="owner-b", display_name="Owner B", timezone="UTC"),
        ]
    )
    session.flush()
    session.add(
        JobPosting(
            id="posting-a",
            owner_id=OWNER_ID,
            identity_kind="native",
            identity_key="source:greenhouse:twilio:123",
            identity_key_hash="1" * 64,
            source="greenhouse",
            company_slug="twilio",
            source_job_id="123",
            canonical_url="https://boards.greenhouse.io/twilio/jobs/123",
            lifecycle_state="open",
            consecutive_complete_omissions=0,
            first_confirmed_at=NOW,
            last_confirmed_at=NOW,
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.flush()
    session.add(
        JobPostingVersion(
            id="posting-version-a",
            owner_id=OWNER_ID,
            job_posting_id="posting-a",
            version_number=1,
            content_hash="2" * 64,
            source="greenhouse",
            source_job_id="123",
            company_name="Twilio",
            title="Staff Software Engineer",
            canonical_url="https://boards.greenhouse.io/twilio/jobs/123",
            apply_urls=["https://boards.greenhouse.io/twilio/jobs/123"],
            location="Remote India",
            summary="Build reliable identity systems.",
            description="Design and operate reliable identity systems.",
            employment_type="full_time",
            source_facts={},
            source_confidence=1.0,
            observed_at=NOW,
            created_at=NOW,
        )
    )
    session.flush()
    session.add(
        OwnerOpportunity(
            id="opportunity-a",
            owner_id=OWNER_ID,
            job_posting_id="posting-a",
            decision="pursued",
            reviewed_posting_version_id="posting-version-a",
            decision_updated_at=NOW,
            first_surfaced_at=NOW,
            last_surfaced_at=NOW,
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.flush()
    session.add(
        Application(
            id=APPLICATION_ID,
            owner_id=OWNER_ID,
            owner_opportunity_id="opportunity-a",
            job_posting_id="posting-a",
            pursued_posting_version_id="posting-version-a",
            stage="pursuing",
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def _seed_contact_plan(session) -> None:
    session.add(
        BackgroundJob(
            id=JOB_ID,
            kind=contact_worker.CONTACT_SEARCH_JOB_KIND,
            owner_id=OWNER_ID,
            dedupe_scope=f"owner:{OWNER_ID}",
            subject_type="contact_plan",
            subject_id=PLAN_ID,
            payload={
                "contact_plan_id": PLAN_ID,
                "candidate_limit": 12,
                "target_count": 5,
            },
            dedupe_key=f"contacts:{PLAN_ID}",
            status="queued",
            priority=75,
            attempt_count=0,
            max_attempts=3,
            run_after=NOW,
            stage="queued",
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.flush()
    session.add(
        ContactPlan(
            id=PLAN_ID,
            owner_id=OWNER_ID,
            application_id=APPLICATION_ID,
            plan_number=1,
            status="queued",
            target_count=5,
            candidate_limit=12,
            confidence_floor=0.75,
            policy_version="contact-bench-v1",
            scoring_version="contact-score-v1",
            background_job_id=JOB_ID,
            discovered_count=0,
            verified_count=0,
            selected_count=0,
            coverage_status="pending",
            exhausted=False,
            retryable=False,
            shortfall_reasons=[],
            error_code=None,
            version=1,
            started_at=None,
            finalized_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def _claim(database: Database) -> Claim:
    with database.session() as session:
        job = claim_next_job(
            session,
            worker_id=WORKER_ID,
            lease_token=LEASE_TOKEN,
            lease_seconds=3_600,
            kinds={contact_worker.CONTACT_SEARCH_JOB_KIND},
            now=PROCESS_NOW,
        )
        assert job is not None and job.id == JOB_ID
    return Claim()


def _result(name: str, title: str, slug: str) -> ProviderSearchResult:
    return ProviderSearchResult(
        result_title=f"{name} - {title} - Twilio | LinkedIn",
        result_url=f"https://www.linkedin.com/in/{slug}",
        result_excerpt=f"{title} at Twilio working on identity systems.",
        observed_at=PROCESS_NOW,
        confidence=0.9,
    )


def _full_responses() -> dict[DiscoveryCategory, list[ProviderSearchResult]]:
    return {
        DiscoveryCategory.peer: [
            _result("Peer Alpha", "Staff Software Engineer", "peer-alpha"),
            _result("Peer Beta", "Senior Software Engineer", "peer-beta"),
            _result("Peer Gamma", "Backend Software Engineer", "peer-gamma"),
        ],
        DiscoveryCategory.leader: [
            _result("Leader Alpha", "Engineering Manager", "leader-alpha"),
            _result("Leader Beta", "Engineering Director", "leader-beta"),
        ],
        DiscoveryCategory.recruiter: [
            _result("Recruiter Alpha", "Technical Recruiter", "recruiter-alpha"),
        ],
    }


def _partial_responses() -> dict[DiscoveryCategory, list[ProviderSearchResult]]:
    return {
        DiscoveryCategory.peer: [
            _result("Peer Alpha", "Staff Software Engineer", "peer-alpha"),
        ],
        DiscoveryCategory.leader: [
            _result("Leader Alpha", "Engineering Manager", "leader-alpha"),
        ],
        DiscoveryCategory.recruiter: [
            _result("Recruiter Alpha", "Technical Recruiter", "recruiter-alpha"),
        ],
    }


def _count(session, model: type) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _make_claimable_now(database: Database) -> None:
    with database.session() as session:
        job = session.get(BackgroundJob, JOB_ID)
        assert job is not None
        job.run_after = PROCESS_NOW - timedelta(minutes=1)


class _FixedWorkerDateTime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[no-untyped-def]
        return PROCESS_NOW if tz is not None else PROCESS_NOW.replace(tzinfo=None)


def test_contact_worker_atomically_publishes_five_reserves_from_a_larger_pool(
    contact_worker_db: Database,
) -> None:
    claim = _claim(contact_worker_db)

    def observe_committed_running_state() -> None:
        # This second session can observe the running plan because the provider
        # is called only after the worker's start transaction has committed.
        with contact_worker_db.session() as session:
            plan = session.get(ContactPlan, PLAN_ID)
            assert plan is not None and plan.status == "running"

    provider = FakeProvider(
        _full_responses(),
        on_first_call=observe_committed_running_state,
    )
    contact_worker.process_claimed_contact_search(
        claim,
        database=contact_worker_db,
        worker_id=WORKER_ID,
        provider=provider,
    )

    assert provider.calls == [
        (DiscoveryCategory.peer, 12),
        (DiscoveryCategory.leader, 12),
        (DiscoveryCategory.recruiter, 12),
    ]
    with contact_worker_db.session() as session:
        plan = session.get(ContactPlan, PLAN_ID)
        job = session.get(BackgroundJob, JOB_ID)
        assert plan is not None and job is not None
        assert plan.status == "completed"
        assert plan.coverage_status == "met"
        assert (plan.discovered_count, plan.verified_count, plan.selected_count) == (
            6,
            6,
            5,
        )
        assert plan.shortfall_reasons == []
        assert job.status == "succeeded"
        rows = list(
            session.scalars(
                select(ApplicationContact).order_by(ApplicationContact.pool_rank)
            )
        )
        assert len(rows) == 6
        selected = sorted(
            (row for row in rows if row.bench_rank is not None),
            key=lambda row: row.bench_rank or 0,
        )
        assert [row.pool_rank for row in rows] == list(range(1, len(rows) + 1))
        assert [row.bench_rank for row in selected] == [1, 2, 3, 4, 5]
        assert {row.bench_state for row in selected} == {"reserve"}
        assert all(row.unlocked_at is None for row in selected)
        assert {row.bench_state for row in rows if row.bench_rank is None} == {
            "overflow"
        }

    # A duplicate delivery cannot reacquire a succeeded lease and publishes no
    # extra rows or contacts.
    contact_worker.process_claimed_contact_search(
        claim,
        database=contact_worker_db,
        worker_id=WORKER_ID,
        provider=provider,
    )
    with contact_worker_db.session() as session:
        assert _count(session, Contact) == 6
        assert _count(session, ApplicationContact) == 6


@pytest.mark.parametrize("stage", ["ready_to_apply", "applied"])
def test_contact_worker_start_and_publish_accept_later_active_stages(
    contact_worker_db: Database,
    stage: str,
) -> None:
    with contact_worker_db.session() as session:
        application = session.get(Application, APPLICATION_ID)
        assert application is not None
        application.stage = stage

    claim = _claim(contact_worker_db)
    provider = FakeProvider(_full_responses())
    contact_worker.process_claimed_contact_search(
        claim,
        database=contact_worker_db,
        worker_id=WORKER_ID,
        provider=provider,
    )

    assert provider.calls == [
        (DiscoveryCategory.peer, 12),
        (DiscoveryCategory.leader, 12),
        (DiscoveryCategory.recruiter, 12),
    ]
    with contact_worker_db.session() as session:
        plan = session.get(ContactPlan, PLAN_ID)
        job = session.get(BackgroundJob, JOB_ID)
        assert plan is not None and job is not None
        assert plan.status == "completed"
        assert plan.coverage_status == "met"
        assert plan.selected_count == 5
        assert job.status == "succeeded"
        assert _count(session, ApplicationContact) == 6


def test_oversized_provider_row_is_skipped_without_retrying_or_losing_good_leads(
    contact_worker_db: Database,
) -> None:
    claim = _claim(contact_worker_db)
    responses = _full_responses()
    responses[DiscoveryCategory.peer].insert(
        0,
        _result(
            f"{'A' * 250} {'B' * 250}",
            "Staff Software Engineer",
            "oversized-person",
        ),
    )
    provider = FakeProvider(responses)

    contact_worker.process_claimed_contact_search(
        claim,
        database=contact_worker_db,
        worker_id=WORKER_ID,
        provider=provider,
    )

    assert len(provider.calls) == 3
    with contact_worker_db.session() as session:
        plan = session.get(ContactPlan, PLAN_ID)
        job = session.get(BackgroundJob, JOB_ID)
        assert plan is not None and job is not None
        assert plan.status == "completed"
        assert plan.selected_count == 5
        assert job.status == "succeeded"
        assert _count(session, Contact) == 6


def test_contact_worker_truthfully_completes_three_of_five_with_structured_reasons(
    contact_worker_db: Database,
) -> None:
    claim = _claim(contact_worker_db)
    contact_worker.process_claimed_contact_search(
        claim,
        database=contact_worker_db,
        worker_id=WORKER_ID,
        provider=FakeProvider(_partial_responses()),
    )

    with contact_worker_db.session() as session:
        plan = session.get(ContactPlan, PLAN_ID)
        assert plan is not None
        assert plan.status == "completed"
        assert plan.coverage_status == "partial"
        assert (plan.discovered_count, plan.verified_count, plan.selected_count) == (
            3,
            3,
            3,
        )
        assert plan.exhausted is True
        assert plan.retryable is False
        assert plan.shortfall_reasons == [
            {
                "code": "verified_contacts_shortfall",
                "count": 2,
                "detail": (
                    "Only 3 of 5 distinct contacts met the 0.75 evidence floor."
                ),
            },
            {
                "code": "search_exhausted",
                "count": 2,
                "detail": (
                    "The configured discovery budget completed without enough evidence."
                ),
            },
        ]
        assert _count(session, ApplicationContact) == 3


def test_existing_do_not_contact_is_preserved_and_excluded_from_selection(
    contact_worker_db: Database,
) -> None:
    profile_url = "https://www.linkedin.com/in/peer-alpha"
    identity_key = f"profile_url:{profile_url}"
    with contact_worker_db.session() as session:
        session.add(
            Contact(
                id="existing-contact",
                owner_id=OWNER_ID,
                identity_key=identity_key,
                identity_key_hash=hashlib.sha256(identity_key.encode()).hexdigest(),
                profile_url=profile_url,
                normalized_profile_url=profile_url,
                profile_source="linkedin",
                public_name="Peer Alpha",
                lifecycle="do_not_contact",
                do_not_contact_at=NOW,
                version=4,
                created_at=NOW,
                updated_at=NOW,
            )
        )

    claim = _claim(contact_worker_db)
    responses = _full_responses()
    responses[DiscoveryCategory.peer] = responses[DiscoveryCategory.peer][:2]
    contact_worker.process_claimed_contact_search(
        claim,
        database=contact_worker_db,
        worker_id=WORKER_ID,
        provider=FakeProvider(responses),
    )

    with contact_worker_db.session() as session:
        contact = session.get(Contact, "existing-contact")
        plan = session.get(ContactPlan, PLAN_ID)
        excluded = session.scalar(
            select(ApplicationContact).where(
                ApplicationContact.contact_id == "existing-contact"
            )
        )
        assert contact is not None and plan is not None and excluded is not None
        assert contact.lifecycle == "do_not_contact"
        assert contact.do_not_contact_at is not None
        assert excluded.bench_rank is None
        assert excluded.bench_state == "excluded"
        assert excluded.exclusion_reason == "contact_do_not_contact"
        assert plan.discovered_count == plan.verified_count == 5
        assert plan.selected_count == 4
        assert plan.coverage_status == "partial"
        assert {
            reason["code"] for reason in plan.shortfall_reasons
        } == {
            "contact_lifecycle_excluded",
            "search_exhausted",
            "verified_contacts_shortfall",
        }


@pytest.mark.parametrize(
    ("failure", "expected_job", "expected_plan", "retryable", "error_code"),
    [
        (TimeoutError("private timeout body"), "queued", "queued", True, None),
        (
            ContactProviderConfigurationError("private missing credential"),
            "dead_letter",
            "failed",
            False,
            "provider_configuration_failure",
        ),
    ],
)
def test_total_provider_failure_retries_or_fails_without_publishing(
    contact_worker_db: Database,
    failure: Exception,
    expected_job: str,
    expected_plan: str,
    retryable: bool,
    error_code: str | None,
) -> None:
    claim = _claim(contact_worker_db)
    provider = FakeProvider(
        failures={category: failure for category in DiscoveryCategory}
    )
    contact_worker.process_claimed_contact_search(
        claim,
        database=contact_worker_db,
        worker_id=WORKER_ID,
        provider=provider,
    )

    with contact_worker_db.session() as session:
        plan = session.get(ContactPlan, PLAN_ID)
        job = session.get(BackgroundJob, JOB_ID)
        assert plan is not None and job is not None
        assert job.status == expected_job
        assert plan.status == expected_plan
        assert plan.retryable is retryable
        assert plan.error_code == error_code
        assert plan.discovered_count == plan.selected_count == 0
        assert plan.shortfall_reasons == []
        assert _count(session, Contact) == 0
        assert _count(session, ApplicationContact) == 0
        assert "private" not in str(job.__dict__)


@pytest.mark.parametrize(
    "stage", ["pursuing", "ready_to_apply", "applied"]
)
def test_cancel_requested_during_provider_work_publishes_nothing(
    contact_worker_db: Database,
    stage: str,
) -> None:
    with contact_worker_db.session() as session:
        application = session.get(Application, APPLICATION_ID)
        assert application is not None
        application.stage = stage
    claim = _claim(contact_worker_db)

    def request_cancel() -> None:
        with contact_worker_db.session() as session:
            cancelled = cancel_job(
                session,
                JOB_ID,
                actor="owner:owner-a",
                reason="owner_cancelled",
                now=PROCESS_NOW,
            )
            assert cancelled is not None and cancelled.cancel_requested_at is not None

    contact_worker.process_claimed_contact_search(
        claim,
        database=contact_worker_db,
        worker_id=WORKER_ID,
        provider=FakeProvider(_full_responses(), on_first_call=request_cancel),
    )

    with contact_worker_db.session() as session:
        plan = session.get(ContactPlan, PLAN_ID)
        job = session.get(BackgroundJob, JOB_ID)
        assert plan is not None and job is not None
        assert plan.status == "cancelled"
        assert plan.coverage_status == "pending"
        assert job.status == "cancelled"
        assert _count(session, ApplicationContact) == 0
        assert _count(session, Contact) == 0


@pytest.mark.parametrize(
    "stage", ["pursuing", "ready_to_apply", "applied"]
)
def test_posting_closed_during_provider_work_cancels_before_publication(
    contact_worker_db: Database,
    stage: str,
) -> None:
    with contact_worker_db.session() as session:
        application = session.get(Application, APPLICATION_ID)
        assert application is not None
        application.stage = stage
    claim = _claim(contact_worker_db)

    def close_posting() -> None:
        with contact_worker_db.session() as session:
            posting = session.get(JobPosting, "posting-a")
            assert posting is not None
            posting.lifecycle_state = "closed"
            posting.closure_reason = "explicit"
            posting.closed_at = PROCESS_NOW
            posting.version += 1

    contact_worker.process_claimed_contact_search(
        claim,
        database=contact_worker_db,
        worker_id=WORKER_ID,
        provider=FakeProvider(_full_responses(), on_first_call=close_posting),
    )

    with contact_worker_db.session() as session:
        plan = session.get(ContactPlan, PLAN_ID)
        job = session.get(BackgroundJob, JOB_ID)
        assert plan is not None and job is not None
        assert plan.status == "cancelled"
        assert job.status == "cancelled"
        assert _count(session, ApplicationContact) == 0


def test_lease_loss_has_no_publication_and_terminal_reconciliation_is_safe(
    contact_worker_db: Database,
) -> None:
    claim = _claim(contact_worker_db)

    def lose_lease() -> None:
        with contact_worker_db.session() as session:
            job = session.get(BackgroundJob, JOB_ID)
            assert job is not None
            job.lease_token = "replacement-lease"

    contact_worker.process_claimed_contact_search(
        claim,
        database=contact_worker_db,
        worker_id=WORKER_ID,
        provider=FakeProvider(_full_responses(), on_first_call=lose_lease),
    )

    with contact_worker_db.session() as session:
        plan = session.get(ContactPlan, PLAN_ID)
        job = session.get(BackgroundJob, JOB_ID)
        assert plan is not None and job is not None
        assert plan.status == "running"
        assert _count(session, ApplicationContact) == 0
        job.status = "dead_letter"
        job.stage = "dead_letter"
        job.last_error = "lease_expired"
        job.dead_lettered_at = PROCESS_NOW
        job.lease_owner = None
        job.lease_token = None
        job.lease_expires_at = None
        job.heartbeat_at = None

    with contact_worker_db.session() as session:
        assert contact_worker.reconcile_terminal_contact_plans(
            session,
            now=PROCESS_NOW,
        ) == 1
        assert contact_worker.reconcile_terminal_contact_plans(
            session,
            now=PROCESS_NOW,
        ) == 0

    with contact_worker_db.session() as session:
        plan = session.get(ContactPlan, PLAN_ID)
        assert plan is not None
        assert plan.status == "failed"
        assert plan.error_code == "lease_expired"
        assert plan.retryable is True
        assert plan.finalized_at is not None
        assert _count(session, Contact) == 0
        assert _count(session, ApplicationContact) == 0


def test_cross_owner_job_reference_fails_without_mutating_the_other_owner_plan(
    contact_worker_db: Database,
) -> None:
    with contact_worker_db.session() as session:
        job = session.get(BackgroundJob, JOB_ID)
        assert job is not None
        job.owner_id = "owner-b"
        job.dedupe_scope = "owner:owner-b"

    claim = _claim(contact_worker_db)
    provider = FakeProvider(_full_responses())
    contact_worker.process_claimed_contact_search(
        claim,
        database=contact_worker_db,
        worker_id=WORKER_ID,
        provider=provider,
    )

    assert provider.calls == []
    with contact_worker_db.session() as session:
        plan = session.get(ContactPlan, PLAN_ID)
        job = session.get(BackgroundJob, JOB_ID)
        assert plan is not None and job is not None
        assert plan.owner_id == OWNER_ID
        assert plan.status == "queued"
        assert plan.version == 1
        assert job.owner_id == "owner-b"
        assert job.status == "dead_letter"
        assert job.last_error == "invalid_contact_search_reference"
        assert _count(session, Contact) == 0
        assert _count(session, ApplicationContact) == 0


@pytest.mark.parametrize("company", ["C" * 201, "   "])
def test_unsupported_posting_company_fails_before_provider_spend(
    contact_worker_db: Database,
    company: str,
) -> None:
    with contact_worker_db.session() as session:
        version = session.get(JobPostingVersion, "posting-version-a")
        assert version is not None
        version.company_name = company

    claim = _claim(contact_worker_db)
    provider = FakeProvider(_full_responses())
    contact_worker.process_claimed_contact_search(
        claim,
        database=contact_worker_db,
        worker_id=WORKER_ID,
        provider=provider,
    )

    assert provider.calls == []
    with contact_worker_db.session() as session:
        plan = session.get(ContactPlan, PLAN_ID)
        job = session.get(BackgroundJob, JOB_ID)
        assert plan is not None and job is not None
        assert plan.status == "failed"
        assert plan.retryable is False
        assert plan.error_code == "invalid_contact_search_reference"
        assert job.status == "dead_letter"
        assert _count(session, Contact) == 0


def test_padded_posting_company_is_normalized_before_contact_publication(
    contact_worker_db: Database,
) -> None:
    with contact_worker_db.session() as session:
        version = session.get(JobPostingVersion, "posting-version-a")
        assert version is not None
        version.company_name = f"{' ' * 100}Twilio{' ' * 100}"

    claim = _claim(contact_worker_db)
    provider = FakeProvider(_full_responses())
    contact_worker.process_claimed_contact_search(
        claim,
        database=contact_worker_db,
        worker_id=WORKER_ID,
        provider=provider,
    )

    assert len(provider.calls) == 3
    with contact_worker_db.session() as session:
        plan = session.get(ContactPlan, PLAN_ID)
        companies = set(session.scalars(select(ApplicationContact.current_company)))
        assert plan is not None and plan.status == "completed"
        assert companies == {"Twilio"}


def test_main_practical_worker_dispatches_contact_jobs_to_contact_execution(
    contact_worker_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker
    from job_hunt_agent import job_queue

    _make_claimable_now(contact_worker_db)
    monkeypatch.setattr(worker, "datetime", _FixedWorkerDateTime)
    monkeypatch.setattr(job_queue, "utcnow", lambda: PROCESS_NOW)
    provider = FakeProvider(_full_responses())
    monkeypatch.setattr(
        worker.SerpAPIContactProvider,
        "from_env",
        classmethod(lambda _cls: provider),
    )

    def forbidden_hunt(**_kwargs: object) -> None:
        raise AssertionError("a contact job must not fall through to legacy hunt")

    monkeypatch.setattr(worker, "run_hunt", forbidden_hunt)
    result = worker._run_practical_worker_once(
        contact_worker_db,
        worker_id="main-contact-worker",
        lease_seconds=60,
        retry_delay_seconds=0,
        use_mocks=False,
        enable_tracing=False,
    )

    assert result == worker.WorkerResult(
        claimed=True,
        run_id=PLAN_ID,
        status="succeeded",
        stage="succeeded",
    )
    assert len(provider.calls) == 3
    with contact_worker_db.session() as session:
        plan = session.get(ContactPlan, PLAN_ID)
        heartbeat = session.get(WorkerHeartbeat, "main-contact-worker")
        assert plan is not None and plan.status == "completed"
        assert plan.selected_count == 5
        assert heartbeat is not None
        assert set(heartbeat.supported_kinds) == {
            "discover_contacts",
            "legacy_hunt",
            "scan_saved_search",
        }
        assert heartbeat.current_job_id is None


def test_main_practical_worker_mock_mode_is_network_free_and_builds_five_contacts(
    contact_worker_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import job_queue, worker

    _make_claimable_now(contact_worker_db)
    monkeypatch.setattr(worker, "datetime", _FixedWorkerDateTime)
    monkeypatch.setattr(job_queue, "utcnow", lambda: PROCESS_NOW)

    def forbidden_live_provider(_cls: type) -> None:
        raise AssertionError("mock mode must never construct a live provider")

    monkeypatch.setattr(
        worker.SerpAPIContactProvider,
        "from_env",
        classmethod(forbidden_live_provider),
    )
    result = worker._run_practical_worker_once(
        contact_worker_db,
        worker_id="mock-contact-worker",
        lease_seconds=60,
        retry_delay_seconds=0,
        use_mocks=True,
        enable_tracing=False,
    )

    assert result.status == "succeeded"
    with contact_worker_db.session() as session:
        plan = session.get(ContactPlan, PLAN_ID)
        rows = list(
            session.scalars(
                select(ApplicationContact).order_by(ApplicationContact.bench_rank)
            )
        )
        assert plan is not None and plan.status == "completed"
        assert plan.selected_count == 5
        assert len(rows) == 5
        assert {row.discovery_provider for row in rows} == {"mock_public_search"}
        assert [row.bench_rank for row in rows] == [1, 2, 3, 4, 5]


def test_main_practical_worker_fails_unconfigured_contact_provider_safely(
    contact_worker_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_hunt_agent import worker
    from job_hunt_agent import job_queue

    _make_claimable_now(contact_worker_db)
    monkeypatch.setattr(worker, "datetime", _FixedWorkerDateTime)
    monkeypatch.setattr(job_queue, "utcnow", lambda: PROCESS_NOW)

    def missing_provider(_cls: type) -> None:
        raise ContactProviderConfigurationError("PRIVATE_SERPAPI_KEY")

    monkeypatch.setattr(
        worker.SerpAPIContactProvider,
        "from_env",
        classmethod(missing_provider),
    )
    result = worker._run_practical_worker_once(
        contact_worker_db,
        worker_id="unconfigured-contact-worker",
        lease_seconds=60,
        retry_delay_seconds=0,
        use_mocks=False,
        enable_tracing=False,
    )

    assert result.claimed is True
    assert result.run_id == PLAN_ID
    with contact_worker_db.session() as session:
        plan = session.get(ContactPlan, PLAN_ID)
        job = session.get(BackgroundJob, JOB_ID)
        assert plan is not None and job is not None
        assert plan.status == "failed"
        assert plan.error_code == "provider_configuration_failure"
        assert plan.retryable is False
        assert job.status == "dead_letter"
        assert "PRIVATE_SERPAPI_KEY" not in str(job.last_error)
