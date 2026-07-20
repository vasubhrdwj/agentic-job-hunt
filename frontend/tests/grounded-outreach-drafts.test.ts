import assert from "node:assert/strict";
import test from "node:test";

import type { ApplicationArtifactsResponse } from "../lib/application-artifact-types";
import {
  approvedProfileOutreachGrounding,
  approvedOutreachGrounding,
  hydrateOutreachDraft,
  LINKEDIN_FRIENDLY_DRAFT_LIMIT,
  outreachDraftIsDirty,
  prepareGroundedOutreachDrafts,
  type OutreachRecipientFacts,
} from "../lib/grounded-outreach-drafts";
import type { AchievementEvidence } from "../lib/workspace-types";

function artifacts(status: "approved" | "draft" = "approved"): ApplicationArtifactsResponse {
  const revision = {
    id: "artifact-revision-1",
    selected_evidence: [{
      id: "evidence-1",
      version: 2,
      statement: "Owned an AWS Lambda event pipeline in production.",
    }],
  };
  const event = {
    id: "approval-1",
    event_type: "approved" as const,
    artifact_revision_id: revision.id,
  };
  return {
    application_id: "application-1",
    status,
    current_revision: revision,
    approved_revision: revision,
    current_event: event,
    approval_event: event,
    blockers: [],
  } as unknown as ApplicationArtifactsResponse;
}

function recipient(
  overrides: Partial<OutreachRecipientFacts> = {},
): OutreachRecipientFacts {
  return {
    applicationContactId: "contact-1",
    publicName: "Priya Shah",
    category: "team_peer",
    currentTitle: "Staff Engineer",
    currentCompany: "StableCo",
    whyRelevant: "The saved result suggests work close to this hiring team.",
    employerEvidence: {
      excerpt: "Public profile result lists a Staff Engineer role at StableCo.",
      source: "linkedin",
    },
    ...overrides,
  };
}

function profileEvidence(
  overrides: Partial<AchievementEvidence> = {},
): AchievementEvidence {
  return {
    id: "profile-evidence-1",
    statement: "Owned an AWS Lambda event pipeline in production.",
    source_resume_version_id: "resume-1",
    source_excerpt: "Owned the xAPI event pipeline end to end on AWS Lambda.",
    skills: ["AWS", "Backend"],
    origin: "resume_suggestion",
    approval_state: "approved",
    approved_at: "2026-07-14T09:00:00Z",
    rejected_at: null,
    retired_at: null,
    version: 2,
    created_at: "2026-07-14T08:00:00Z",
    updated_at: "2026-07-14T09:00:00Z",
    ...overrides,
  };
}

test("uses the exact latest approved artifact revision and records provenance", () => {
  const grounding = approvedOutreachGrounding({
    artifacts: artifacts(),
    applicationId: "application-1",
    roleTitle: "Backend Engineer",
    companyName: "StableCo",
  });
  const drafts = prepareGroundedOutreachDrafts(grounding, recipient({
    applicationContactId: "contact-1",
    publicName: "Priya Shah",
    category: "team_peer",
  }));
  assert.ok(drafts);
  assert.match(drafts.initial, /Backend Engineer at StableCo/);
  assert.match(drafts.initial, /Owned an AWS Lambda event pipeline in production/);
  assert.deepEqual(drafts.provenance, {
    source: "approved_application_materials",
    artifactRevisionId: "artifact-revision-1",
    approvalEventId: "approval-1",
    evidenceId: "evidence-1",
    evidenceVersion: 2,
  });
});

test("an immutable approved revision remains usable while a newer draft is current", () => {
  const withNewerDraft = artifacts("draft");
  withNewerDraft.current_revision = {
    ...withNewerDraft.approved_revision!,
    id: "artifact-revision-2",
    revision_number: 2,
    selected_evidence: [],
  };
  withNewerDraft.current_event = null;

  const grounding = approvedOutreachGrounding({
    artifacts: withNewerDraft,
    applicationId: "application-1",
    roleTitle: "Backend Engineer",
    companyName: "StableCo",
  });

  assert.equal(grounding?.artifactRevisionId, "artifact-revision-1");
  assert.equal(grounding?.evidence.id, "evidence-1");
});

test("changed grounding blocks even an otherwise valid approved revision", () => {
  const changed = artifacts("draft");
  changed.blockers = ["grounding_evidence_changed"];

  assert.equal(approvedOutreachGrounding({
    artifacts: changed,
    applicationId: "application-1",
    roleTitle: "Backend Engineer",
    companyName: "StableCo",
  }), null);
});

test("a draft without an exact approved revision and event cannot ground outreach", () => {
  const unapproved = artifacts("draft");
  unapproved.approved_revision = null;
  unapproved.approval_event = null;

  assert.equal(approvedOutreachGrounding({
    artifacts: unapproved,
    applicationId: "application-1",
    roleTitle: "Backend Engineer",
    companyName: "StableCo",
  }), null);
});

test("customizes polite, explicit referral asks by recipient category", () => {
  const grounding = approvedOutreachGrounding({
    artifacts: artifacts(),
    applicationId: "application-1",
    roleTitle: "Backend Engineer",
    companyName: "StableCo",
  });
  const recruiter = prepareGroundedOutreachDrafts(grounding, recipient({
    applicationContactId: "recruiter",
    publicName: "Asha Rao",
    category: "recruiter",
    currentTitle: "Technical Recruiter",
  }));
  const leader = prepareGroundedOutreachDrafts(grounding, recipient({
    applicationContactId: "leader",
    publicName: "Dev Mehta",
    category: "team_leader",
    currentTitle: "Engineering Director",
  }));
  assert.ok(recruiter && leader);
  assert.match(recruiter.initial, /referring or forwarding my application/);
  assert.match(leader.initial, /comfortable referring me/);
  assert.match(leader.initial, /search result.*described you as Engineering Director at StableCo/);
  assert.notEqual(recruiter.initial, leader.initial);
});

test("output is deterministic and LinkedIn-friendly", () => {
  const grounding = approvedOutreachGrounding({
    artifacts: artifacts(),
    applicationId: "application-1",
    roleTitle: "Backend Engineer",
    companyName: "StableCo",
  });
  const recipientFacts = recipient({
    applicationContactId: "peer",
    publicName: "Priya Shah",
    category: "team_peer",
  });
  const first = prepareGroundedOutreachDrafts(grounding, recipientFacts);
  const second = prepareGroundedOutreachDrafts(grounding, recipientFacts);
  assert.deepEqual(first, second);
  assert.ok(first);
  assert.ok(first.initial.length <= LINKEDIN_FRIENDLY_DRAFT_LIMIT);
  assert.ok(first.followUp.length <= LINKEDIN_FRIENDLY_DRAFT_LIMIT);
});

test("uses current approved profile evidence before application materials are approved", () => {
  const grounding = approvedProfileOutreachGrounding({
    evidence: [
      profileEvidence({
        id: "unrelated-newer",
        statement: "Designed an unrelated visual identity system.",
        skills: ["Design"],
        approved_at: "2026-07-15T09:00:00Z",
      }),
      profileEvidence(),
      profileEvidence({
        id: "pending",
        statement: "Invented pending statement.",
        approval_state: "pending",
        approved_at: null,
      }),
    ],
    applicationId: "application-1",
    roleTitle: "Backend Engineer",
    companyName: "StableCo",
  });
  const drafts = prepareGroundedOutreachDrafts(
    grounding,
    recipient(),
  );

  assert.ok(drafts);
  assert.match(drafts.initial, /Owned an AWS Lambda event pipeline in production/);
  assert.match(drafts.initial, /comfortable referring me/);
  assert.deepEqual(drafts.provenance, {
    source: "approved_profile_evidence",
    evidenceId: "profile-evidence-1",
    evidenceVersion: 2,
  });
});

test("does not call unrelated approved profile evidence relevant to the role", () => {
  const grounding = approvedProfileOutreachGrounding({
    evidence: [profileEvidence({
      statement: "Designed a visual identity system for a consumer brand.",
      skills: ["Design", "Typography"],
    })],
    applicationId: "application-1",
    roleTitle: "Backend Engineer",
    companyName: "StableCo",
  });

  assert.equal(grounding, null);
});

test("keeps messages natural, source-qualified, and distinct by saved title", () => {
  const grounding = approvedProfileOutreachGrounding({
    evidence: [profileEvidence()],
    applicationId: "application-1",
    roleTitle: "Backend Engineer",
    companyName: "StableCo",
  });
  const peer = prepareGroundedOutreachDrafts(grounding, recipient({
    publicName: "Alex Kim",
    currentTitle: "Platform Engineer",
    employerEvidence: {
      excerpt: "A very long saved snippet that must stay in provenance, not the message.",
      source: "linkedin",
    },
  }));
  const leader = prepareGroundedOutreachDrafts(grounding, recipient({
    publicName: "Alex Kim",
    currentTitle: "Engineering Manager",
    employerEvidence: {
      excerpt: "Different saved evidence for the leadership role.",
      source: "github",
    },
  }));

  assert.ok(peer && leader);
  assert.match(peer.initial, /search result.*described you as Platform Engineer at StableCo/);
  assert.doesNotMatch(peer.initial, /long saved snippet|linkedin/i);
  assert.notEqual(peer.initial, leader.initial);
  assert.notEqual(peer.followUp, leader.followUp);
});

test("long recipient titles and snippets use a shorter truthful draft instead of going blank", () => {
  const grounding = approvedProfileOutreachGrounding({
    evidence: [profileEvidence()],
    applicationId: "application-1",
    roleTitle: "Backend Engineer",
    companyName: "StableCo",
  });
  const drafts = prepareGroundedOutreachDrafts(grounding, recipient({
    currentTitle: "Principal ".repeat(35).trim(),
    employerEvidence: {
      excerpt: "evidence ".repeat(120).trim(),
      source: "public_web",
    },
  }));

  assert.ok(drafts);
  assert.ok(drafts.initial.length <= LINKEDIN_FRIENDLY_DRAFT_LIMIT);
  assert.ok(drafts.followUp.length <= LINKEDIN_FRIENDLY_DRAFT_LIMIT);
  assert.doesNotMatch(drafts.initial, /evidence evidence/);
  assert.match(drafts.initial, /referring me|referring or forwarding/);
});

test("long approved profile evidence is rejected without dropping a trailing qualifier", () => {
  const longStatement = `${"Built a production-like backend prototype ".repeat(8)}but it was never deployed to users.`;
  const grounding = approvedProfileOutreachGrounding({
    evidence: [profileEvidence({ statement: longStatement })],
    applicationId: "application-1",
    roleTitle: "Backend Engineer",
    companyName: "StableCo",
  });
  assert.equal(grounding, null);
});

test("five people with the same first name and title still receive distinct drafts", () => {
  const grounding = approvedProfileOutreachGrounding({
    evidence: [profileEvidence()],
    applicationId: "application-1",
    roleTitle: "Backend Engineer",
    companyName: "StableCo",
  });
  const names = ["Alex Kim", "Alex Rao", "Alex Shah", "Alex Singh", "Alex Mehta"];
  const messages = names.map((publicName, index) => {
    const drafts = prepareGroundedOutreachDrafts(grounding, recipient({
      applicationContactId: `contact-${index + 1}`,
      publicName,
      currentTitle: "Platform Engineer",
    }));
    assert.ok(drafts);
    return drafts.initial;
  });

  assert.equal(new Set(messages).size, 5);
});

test("saved versions and dirty edits win at hydration", () => {
  assert.equal(hydrateOutreachDraft({
    currentValue: "My unsaved rewrite",
    dirty: true,
    savedBody: "Saved body",
    preparedBody: "Prepared body",
  }), "My unsaved rewrite");
  assert.equal(hydrateOutreachDraft({
    currentValue: "Prepared body",
    dirty: false,
    savedBody: "Saved body",
    preparedBody: "New prepared body",
  }), "Saved body");
  assert.equal(hydrateOutreachDraft({
    currentValue: "",
    dirty: false,
    savedBody: null,
    preparedBody: "Prepared body",
  }), "Prepared body");
  assert.equal(outreachDraftIsDirty({
    value: "",
    savedBody: null,
    preparedBody: "Prepared body",
  }), true, "clearing an automatic draft is a user edit and must stay blank");
});

test("fails closed when approved evidence is unavailable or too long", () => {
  const missing = artifacts();
  missing.approved_revision!.selected_evidence = [];
  assert.equal(approvedOutreachGrounding({
    artifacts: missing,
    applicationId: "application-1",
    roleTitle: "Backend Engineer",
    companyName: "StableCo",
  }), null);

  const tooLong = artifacts();
  tooLong.approved_revision!.selected_evidence[0]!.statement = "x".repeat(181);
  assert.equal(approvedOutreachGrounding({
    artifacts: tooLong,
    applicationId: "application-1",
    roleTitle: "Backend Engineer",
    companyName: "StableCo",
  }), null);
});

test("fails closed for one recipient without claiming other recipients were prepared", () => {
  const grounding = approvedOutreachGrounding({
    artifacts: artifacts(),
    applicationId: "application-1",
    roleTitle: "Backend Engineer",
    companyName: "StableCo",
  });
  const tooLongRecipient = prepareGroundedOutreachDrafts(grounding, recipient({
    applicationContactId: "contact-long",
    publicName: "x".repeat(LINKEDIN_FRIENDLY_DRAFT_LIMIT),
    category: "team_peer",
  }));
  const normalRecipient = prepareGroundedOutreachDrafts(grounding, recipient({
    applicationContactId: "contact-normal",
    publicName: "Priya Shah",
    category: "team_peer",
  }));
  assert.equal(tooLongRecipient, null);
  assert.ok(normalRecipient);
});
