"""SQLAlchemy models for the durable practical-product path."""

from .base import Base
from .application import ActionItem, Application, ApplicationActivityEvent
from .application_correction import ApplicationMilestoneCorrection
from .application_interview import (
    ApplicationInterviewRound,
    ApplicationInterviewRoundEvent,
)
from .application_outcome import ApplicationOutcome
from .application_artifact import ApplicationArtifactEvent, ApplicationArtifactRevision
from .application_pack import ApplicationPack, ApplicationPackEvent, ApplicationPackRevision
from .application_submission import ApplicationSubmission
from .contact import ApplicationContact, Contact, ContactPlan
from .foundation import (
    BackgroundJob,
    BackgroundJobEvent,
    HuntOutcome,
    HuntRun,
    Owner,
    OwnerSession,
    WorkerHeartbeat,
)
from .profile import (
    AchievementEvidence,
    CandidateProfile,
    CareerTrack,
    OwnerMutationReceipt,
    ResumeVersion,
    SavedSearch,
)
from .opportunity import (
    JobObservation,
    JobPosting,
    JobPostingAlias,
    JobPostingVersion,
    OpportunityDecisionEvent,
    OpportunityScan,
    OpportunityScanSource,
    OwnerOpportunity,
    SavedSearchMatch,
)
from .outreach import OutreachEvent, OutreachMessageVersion, OutreachReply, OutreachSequence
from .weekly_review import ApplicationActionReview, ApplicationMetricSnapshot

__all__ = [
    "Base",
    "ActionItem",
    "Application",
    "ApplicationActivityEvent",
    "ApplicationInterviewRound",
    "ApplicationInterviewRoundEvent",
    "ApplicationMilestoneCorrection",
    "ApplicationOutcome",
    "ApplicationArtifactEvent",
    "ApplicationArtifactRevision",
    "ApplicationPack",
    "ApplicationPackEvent",
    "ApplicationPackRevision",
    "ApplicationSubmission",
    "ApplicationActionReview",
    "ApplicationMetricSnapshot",
    "ApplicationContact",
    "BackgroundJob",
    "BackgroundJobEvent",
    "AchievementEvidence",
    "CandidateProfile",
    "CareerTrack",
    "Contact",
    "ContactPlan",
    "HuntOutcome",
    "HuntRun",
    "JobObservation",
    "JobPosting",
    "JobPostingAlias",
    "JobPostingVersion",
    "Owner",
    "OwnerMutationReceipt",
    "OwnerOpportunity",
    "OwnerSession",
    "OpportunityDecisionEvent",
    "OpportunityScan",
    "OpportunityScanSource",
    "OutreachEvent",
    "OutreachMessageVersion",
    "OutreachReply",
    "OutreachSequence",
    "ResumeVersion",
    "SavedSearch",
    "SavedSearchMatch",
    "WorkerHeartbeat",
]
