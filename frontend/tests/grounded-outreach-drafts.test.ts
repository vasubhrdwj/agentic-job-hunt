import assert from "node:assert/strict";
import test from "node:test";

import type { ApplicationArtifactsResponse } from "../lib/application-artifact-types";
import {
  approvedOutreachGrounding,
  hydrateOutreachDraft,
  LINKEDIN_FRIENDLY_DRAFT_LIMIT,
  outreachDraftIsDirty,
  prepareGroundedOutreachDrafts,
} from "../lib/grounded-outreach-drafts";

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

test("uses the exact latest approved artifact revision and records provenance", () => {
  const grounding = approvedOutreachGrounding({
    artifacts: artifacts(),
    applicationId: "application-1",
    roleTitle: "Backend Engineer",
    companyName: "StableCo",
  });
  const drafts = prepareGroundedOutreachDrafts(grounding, {
    applicationContactId: "contact-1",
    publicName: "Priya Shah",
    category: "team_peer",
  });
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

test("customizes conservative calls to action by recipient category", () => {
  const grounding = approvedOutreachGrounding({
    artifacts: artifacts(),
    applicationId: "application-1",
    roleTitle: "Backend Engineer",
    companyName: "StableCo",
  });
  const recruiter = prepareGroundedOutreachDrafts(grounding, {
    applicationContactId: "recruiter",
    publicName: "Asha Rao",
    category: "recruiter",
  });
  const leader = prepareGroundedOutreachDrafts(grounding, {
    applicationContactId: "leader",
    publicName: "Dev Mehta",
    category: "team_leader",
  });
  assert.ok(recruiter && leader);
  assert.match(recruiter.initial, /hiring team is prioritizing/);
  assert.match(leader.initial, /problem does this hire most need to solve/);
  assert.doesNotMatch(leader.initial, /Engineering Director/);
  assert.notEqual(recruiter.initial, leader.initial);
});

test("output is deterministic and LinkedIn-friendly", () => {
  const grounding = approvedOutreachGrounding({
    artifacts: artifacts(),
    applicationId: "application-1",
    roleTitle: "Backend Engineer",
    companyName: "StableCo",
  });
  const recipient = {
    applicationContactId: "peer",
    publicName: "Priya Shah",
    category: "team_peer" as const,
  };
  const first = prepareGroundedOutreachDrafts(grounding, recipient);
  const second = prepareGroundedOutreachDrafts(grounding, recipient);
  assert.deepEqual(first, second);
  assert.ok(first);
  assert.ok(first.initial.length <= LINKEDIN_FRIENDLY_DRAFT_LIMIT);
  assert.ok(first.followUp.length <= LINKEDIN_FRIENDLY_DRAFT_LIMIT);
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
  const tooLongRecipient = prepareGroundedOutreachDrafts(grounding, {
    applicationContactId: "contact-long",
    publicName: "x".repeat(LINKEDIN_FRIENDLY_DRAFT_LIMIT),
    category: "team_peer",
  });
  const normalRecipient = prepareGroundedOutreachDrafts(grounding, {
    applicationContactId: "contact-normal",
    publicName: "Priya Shah",
    category: "team_peer",
  });
  assert.equal(tooLongRecipient, null);
  assert.ok(normalRecipient);
});
