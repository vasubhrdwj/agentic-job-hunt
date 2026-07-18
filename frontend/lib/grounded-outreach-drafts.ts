import type { ApplicationArtifactsResponse } from "./application-artifact-types";
import type { ContactCategory } from "./application-types";

export const LINKEDIN_FRIENDLY_DRAFT_LIMIT = 500;

export interface ApprovedOutreachGrounding {
  applicationId: string;
  artifactRevisionId: string;
  approvalEventId: string;
  roleTitle: string;
  companyName: string;
  evidence: {
    id: string;
    version: number;
    statement: string;
  };
}

export interface OutreachRecipientFacts {
  applicationContactId: string;
  publicName: string;
  currentTitle: string;
  category: ContactCategory;
}

export interface PreparedOutreachDrafts {
  initial: string;
  followUp: string;
  provenance: {
    source: "approved_application_materials";
    artifactRevisionId: string;
    approvalEventId: string;
    evidenceId: string;
    evidenceVersion: number;
  };
}

export function approvedOutreachGrounding({
  artifacts,
  applicationId,
  roleTitle,
  companyName,
}: {
  artifacts: ApplicationArtifactsResponse | null;
  applicationId: string;
  roleTitle: string;
  companyName: string;
}): ApprovedOutreachGrounding | null {
  if (
    !artifacts || artifacts.application_id !== applicationId ||
    artifacts.blockers.includes("grounding_evidence_changed")
  ) return null;
  const revision = artifacts.approved_revision;
  const approvalEvent = artifacts.approval_event;
  if (
    !revision || !approvalEvent || approvalEvent.event_type !== "approved" ||
    approvalEvent.artifact_revision_id !== revision.id
  ) {
    return null;
  }

  const compactRole = compact(roleTitle);
  const compactCompany = compact(companyName);
  const evidence = revision.selected_evidence.find((item) => {
    const statement = compact(item.statement);
    return statement.length > 0 && statement.length <= 180;
  });
  if (!compactRole || !compactCompany || !evidence) return null;

  return {
    applicationId,
    artifactRevisionId: revision.id,
    approvalEventId: approvalEvent.id,
    roleTitle: compactRole,
    companyName: compactCompany,
    evidence: {
      id: evidence.id,
      version: evidence.version,
      statement: compact(evidence.statement),
    },
  };
}

export function prepareGroundedOutreachDrafts(
  grounding: ApprovedOutreachGrounding | null,
  recipient: OutreachRecipientFacts,
): PreparedOutreachDrafts | null {
  if (!grounding) return null;
  const name = compact(recipient.publicName);
  if (!name) return null;
  const greetingName = name.split(" ")[0] ?? name;
  const title = compact(recipient.currentTitle);
  const initialBase = `Hi ${greetingName} — I’m applying for ${grounding.roleTitle} at ${grounding.companyName}. One relevant proof point: ${grounding.evidence.statement}`;
  const initial = fitDraft(initialBase, initialCta(recipient.category, title));
  const followUp = fitDraft(
    `Hi ${greetingName} — just following up on my note about ${grounding.roleTitle} at ${grounding.companyName}.`,
    followUpCta(recipient.category),
  );
  if (!initial || !followUp) return null;

  return {
    initial,
    followUp,
    provenance: {
      source: "approved_application_materials",
      artifactRevisionId: grounding.artifactRevisionId,
      approvalEventId: grounding.approvalEventId,
      evidenceId: grounding.evidence.id,
      evidenceVersion: grounding.evidence.version,
    },
  };
}

export function hydrateOutreachDraft({
  currentValue,
  dirty,
  savedBody,
  preparedBody,
}: {
  currentValue: string;
  dirty: boolean;
  savedBody: string | null;
  preparedBody: string;
}): string {
  if (dirty) return currentValue;
  if (savedBody !== null) return savedBody;
  return preparedBody;
}

export function outreachDraftIsDirty({
  value,
  savedBody,
  preparedBody,
}: {
  value: string;
  savedBody: string | null;
  preparedBody: string;
}): boolean {
  return value !== (savedBody ?? preparedBody);
}

function initialCta(category: ContactCategory, title: string): string {
  if (category === "recruiter") {
    return "Would you be open to sharing whether that background matches what the hiring team is prioritizing? Thanks.";
  }
  if (category === "team_leader") {
    return title
      ? `Given your perspective as ${title}, what problem does this hire most need to solve? Thanks.`
      : "What problem does this hire most need to solve? Thanks.";
  }
  if (category === "team_peer" || category === "adjacent_peer") {
    return title
      ? `Given your perspective as ${title}, what does the team value most in candidates? Thanks.`
      : "What does the team value most in candidates? Thanks.";
  }
  return "Would you be open to sharing what the team values most in candidates, or who would be best to ask? Thanks.";
}

function followUpCta(category: ContactCategory): string {
  if (category === "recruiter") {
    return "If the role is still active, I’d appreciate one pointer on fit or the right recruiting contact. No worries if you’re busy — thanks.";
  }
  if (category === "team_leader") {
    return "If you have a moment, I’d value one pointer on the problem this hire needs to solve. No worries if you’re busy — thanks.";
  }
  if (category === "team_peer" || category === "adjacent_peer") {
    return "If you have a moment, I’d value one pointer on what the team prioritizes. No worries if you’re busy — thanks.";
  }
  return "If you have a moment, I’d appreciate a pointer to the right person to ask. No worries if you’re busy — thanks.";
}

function fitDraft(base: string, cta: string): string {
  const full = `${base} ${cta}`;
  if (full.length <= LINKEDIN_FRIENDLY_DRAFT_LIMIT) return full;
  const compactCta = "Would you be open to one brief pointer? Thanks.";
  const compactDraft = `${base} ${compactCta}`;
  return compactDraft.length <= LINKEDIN_FRIENDLY_DRAFT_LIMIT ? compactDraft : "";
}

function compact(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}
